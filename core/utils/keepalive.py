"""
core/utils/keepalive.py
=======================
Scheduler de Keep-Alive para evitar cold start após inatividade.

PROBLEMA RESOLVIDO:
  Após período sem uso (~10-30 min), os caches de usuários, permissões e
  dados WMS expiram. A primeira requisição pós-inatividade precisa refazer
  todas as chamadas ao Google Sheets + Sankhya, causando ~60s de espera.

SOLUÇÃO:
  Background thread que, a cada INTERVAL_MINUTES:
  1. Faz um GET interno ao /health (mantém processo vivo no Railway)
  2. Força refresh do cache de usuários do Google Sheets
  3. Força refresh das permissões
  4. Faz warm-up do Sankhya (renova token se próximo do vencimento)

USO (em app.py, dentro de create_app(), após todos os serviços iniciados):
    from core.utils.keepalive import start_keepalive
    start_keepalive(app, sheets_service, sankhya_service)
"""

import threading
import time
import logging
import os

logger = logging.getLogger(__name__)

# CONFIGURAÇÕES

# Intervalo entre refreshes (em segundos). 8 minutos garante que os
# caches (TTL de ~5-10min) nunca expirem durante uso normal.
INTERVAL_SECONDS = 8 * 60  # 8 minutos

# Aguarda este tempo após o startup antes do 1º ciclo.
# Evita competição com a inicialização dos serviços.
INITIAL_DELAY_SECONDS = 90  # 1,5 minutos após startup

# Flag global para evitar múltiplas instâncias
_keepalive_started = False
_keepalive_lock = threading.Lock()


# FUNÇÃO PRINCIPAL DO SCHEDULER

def _keepalive_worker(app, sheets_service, sankhya_service):
    """
    Loop do worker de keep-alive.
    Roda em daemon thread — encerra automaticamente com a aplicação.
    """
    logger.info("[KeepAlive] Worker iniciado. "
                f"Primeiro ciclo em {INITIAL_DELAY_SECONDS}s.")
    
    # Aguarda antes do primeiro ciclo para não competir com startup
    time.sleep(INITIAL_DELAY_SECONDS)
    
    while True:
        try:
            _run_keepalive_cycle(app, sheets_service, sankhya_service)
        except Exception as e:
            logger.error(f"[KeepAlive] Erro no ciclo (não-fatal): {e}")
        
        logger.debug(f"[KeepAlive] Próximo ciclo em {INTERVAL_SECONDS}s.")
        time.sleep(INTERVAL_SECONDS)


def _run_keepalive_cycle(app, sheets_service, sankhya_service):
    """
    Executa um ciclo completo de keep-alive.

    1. Refresh batch (permissões + usuários) — só se cache próximo de expirar
    2. Warmup WMS (avarias + itens)          — só se cache próximo de expirar
    3. Warm-up Sankhya                        — só se token próximo de expirar
    """
    import time
    import logging
    logger = logging.getLogger(__name__)

    start = time.time()
    logger.info("[KeepAlive] Iniciando ciclo de refresh...")

    # 0. Reconexão automática ao Google Sheets
    #    Se a conexão caiu (ex.: falha transitória do Google no boot),
    #    connect() nunca era chamado de novo sozinho — o worker ficava
    #    preso em modo fallback (só 3 usuários fictícios) até um restart
    #    manual. Tenta reconectar aqui, a cada ciclo, sem impacto para
    #    quem está usando o sistema (roda em background thread).
    try:
        if sheets_service and not sheets_service.connected:
            logger.warning("[KeepAlive] Sheets desconectado — tentando reconectar...")
            ok = sheets_service.connect()
            if ok:
                logger.info("[KeepAlive] Reconexão ao Google Sheets bem-sucedida")
            else:
                logger.warning("[KeepAlive] Reconexão falhou — nova tentativa no próximo ciclo")
    except Exception as e:
        logger.warning(f"[KeepAlive] Erro ao tentar reconectar Sheets (não-fatal): {e}")

    # 1. Refresh permissões + usuários (batch — 1 única chamada API)
    #    Só chama se algum cache estiver a menos de 90s de expirar.
    #     NÃO chamar invalidate_permissions_cache() antes!
    #       load_all_data_batch() verifica internamente o TTL.
    try:
        if sheets_service and sheets_service.connected:
            now = time.time()

            users_age = now - (sheets_service._users_cache_timestamp or 0)
            perms_age = now - (sheets_service._cache_timestamp or 0)
            ttl       = sheets_service._users_cache_ttl   # 900s

            # Apenas renova se algum cache estiver a < 90s de expirar
            if users_age > (ttl - 90) or perms_age > (ttl - 90):
                ok = sheets_service.load_all_data_batch()
                if ok:
                    logger.info("[KeepAlive] Batch refresh concluído (1 chamada API)")
                else:
                    logger.warning("[KeepAlive] Batch falhou — caches anteriores mantidos")
            else:
                remaining = int(ttl - max(users_age, perms_age))
                logger.info(f"[KeepAlive] Caches permissões/usuários válidos por ~{remaining}s")

    except Exception as e:
        logger.warning(f"[KeepAlive] Erro batch refresh (não-fatal): {e}")

    # 2. Warmup do cache WMS (avarias + itens)
    #    NOVO: evita o burst de 429 no retorno pós-idle.
    #    Lógica: se o cache WMS estiver a < 90s de expirar (ou já expirado),
    #    faz o refresh agora, de forma controlada, sem concorrência de usuários.
    try:
        if sheets_service and sheets_service.connected:
            now = time.time()
            wms_ttl = sheets_service._wms_avarias_cache_ttl  # 300s

            avarias_age = now - (sheets_service._wms_avarias_cache_timestamp or 0)
            itens_age   = now - (sheets_service._wms_itens_cache_timestamp or 0)

            if avarias_age > (wms_ttl - 90):
                # Acessa via get_wms_avarias_cached() — respeita o lock anti-stampede
                data = sheets_service.get_wms_avarias_cached()
                logger.info(f"[KeepAlive] WMS Avarias aquecido: {len(data)} registros")
            else:
                remaining = int(wms_ttl - avarias_age)
                logger.info(f"[KeepAlive] Cache WMS Avarias válido por ~{remaining}s")

            if itens_age > (wms_ttl - 90):
                data = sheets_service.get_wms_itens_cached()
                logger.info(f"[KeepAlive] WMS Itens aquecido: {len(data)} registros")
            else:
                remaining = int(wms_ttl - itens_age)
                logger.info(f"[KeepAlive] Cache WMS Itens válido por ~{remaining}s")

    except Exception as e:
        logger.warning(f"[KeepAlive] Erro warmup WMS (não-fatal): {e}")

    # 3. Warm-up Sankhya (sem alteração)
    try:
        if sankhya_service and getattr(sankhya_service, 'enabled', False):
            from core.services.sankhya.service import SankhyaService

            token_age = None
            if SankhyaService._shared_token_timestamp:
                token_age = time.time() - SankhyaService._shared_token_timestamp

            if token_age is None or token_age > (10 * 60):
                SankhyaService._warmed_up = False
                ok = SankhyaService.warm_up()
                if ok:
                    logger.info("[KeepAlive] Token Sankhya renovado")
                else:
                    logger.warning("[KeepAlive] Warm-up Sankhya falhou (não-fatal)")
            else:
                remaining = int((15 * 60 - token_age) / 60)
                logger.info(f"[KeepAlive] Token Sankhya OK (~{remaining}min restantes)")
    except Exception as e:
        logger.warning(f"[KeepAlive] Erro warm-up Sankhya (não-fatal): {e}")

    elapsed = time.time() - start
    logger.info(f"[KeepAlive] Ciclo concluído em {elapsed:.1f}s")

# FUNÇÃO DE INICIALIZAÇÃO — Chamar no app.py

def start_keepalive(app, sheets_service, sankhya_service=None):
    """
    Inicia o scheduler de keep-alive em background thread.
    
    Deve ser chamado em app.py dentro de create_app(), após todos os
    serviços estarem inicializados.
    
    Args:
        app: Instância Flask (usado para contexto de app se necessário)
        sheets_service: GoogleSheetsService já inicializado
        sankhya_service: SankhyaService já inicializado (opcional)
    
    Exemplo:
        from core.utils.keepalive import start_keepalive
        start_keepalive(app, sheets_service, sankhya_service)
    """
    global _keepalive_started
    
    with _keepalive_lock:
        if _keepalive_started:
            logger.warning("[KeepAlive] Já iniciado, ignorando nova chamada.")
            return
        
        # Não inicia em modo de testes ou desenvolvimento com reloader ativo
        # O Werkzeug reloader cria 2 processos — evitamos duplicar o scheduler
        is_reloader_child = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
        is_debug = app.config.get('DEBUG', False)
        
        if is_debug and not is_reloader_child:
            logger.info("[KeepAlive] Modo DEBUG detectado, aguardando processo filho do reloader...")
            # Em debug, aguarda o processo filho (o que realmente serve requests)
            # O scheduler será iniciado nele
            return
        
        thread = threading.Thread(
            target=_keepalive_worker,
            args=(app, sheets_service, sankhya_service),
            name="KeepAliveScheduler",
            daemon=True  # Encerra junto com a aplicação
        )
        thread.start()
        _keepalive_started = True
        
        logger.info(
            f"⚡ [KeepAlive] Scheduler iniciado! "
            f"Intervalo: {INTERVAL_SECONDS // 60}min | "
            f"Delay inicial: {INITIAL_DELAY_SECONDS}s"
        )
