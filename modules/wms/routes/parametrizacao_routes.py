"""
modules/wms/routes/parametrizacao_routes.py
Rotas do Painel de Parametrizações WMS — Módulo 4 do Roadmap.

Blueprints : wms_param
Prefix     : /wms/parametrizacao  (registrado em app.py)
Permissão  : screen 'parametrizacao_pendentes' dentro do módulo 'wms'

Abas disponíveis (Geral → Dashboard → Indenização → Cobrança → Alertas → Pendentes → Automações → Templates):
  /                  → Aba padrão → redireciona para /geral
  /manage            → Compatibilidade legada → redireciona para /geral
  /geral             → Tab 1: Configurações Gerais (Email SMTP, Sistema, ASN)
  /dashboard         → Tab 2: Configurações do Dashboard (cards, alertas, paginação)
  /indenizacao       → Tab 3: Regras de Indenização por transportadora/tipo
  /cobranca          → Tab 4: Funil de Cobrança (escalação automática)
  /alertas           → Tab 5: Alertas visuais do Dashboard (badge por prazo de data)
  /pendentes         → Tab 6: Regras de Exclusão de Pendentes (existente)
  /automacoes        → Tab 7: Automações de Status
  /templates         → Tab 8: Templates de E-mail (WYSIWYG Quill.js)

APIs (AJAX):
  /api/rules         → CRUD regras de pendentes (existente)
  /api/alertas       → CRUD alertas
  /api/indenizacao   → CRUD regras de indenização
  /api/cobranca      → CRUD etapas de cobrança
  /api/config        → GET/PUT configs gerais
  /api/options       → Opções para dropdowns
  /api/templates     → CRUD templates de e-mail
  /api/email/testar  → Testa conexão SMTP em tempo real
"""

import logging
import time as _time
import threading

from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for

from utils.decorators import module_required, screen_required, set_theme
from ..services.parametrizacao_service import ParametrizacaoService
from ..services.config_service import WmsConfigService
from ..services.status_service import StatusService
from ..services.automacao_status_service import AutomacaoStatusService

logger = logging.getLogger(__name__)

# Blueprint + globals
param_bp = Blueprint('wms_param', __name__)

param_service: ParametrizacaoService = None
config_service: WmsConfigService = None
_status_service: StatusService = None
_automacao_service: AutomacaoStatusService = None
_email_service = None   # EmailService injetado pelo app.py via init_parametrizacao_routes
_resumo_cache: dict = {}          # { empresa: {'ts': float, 'payload': dict} }
_RESUMO_CACHE_TTL  = 5 * 60       # 5 min — mesmo intervalo do polling frontend

_alertas_config_cache: dict = {}  # { empresa: {'ts': float, 'data': list} }
_ALERTAS_CONFIG_TTL   = 15 * 60   # 15 min — alertas raramente mudam
_alertas_config_lock  = threading.Lock()

_detalhes_cache: dict = {}        # { empresa: {'ts': float, 'payload': dict} }
_DETALHES_CACHE_TTL   = 5 * 60    # 5 min — mesmo TTL do resumo


_lista_alertas_cache: dict = {}     # { (empresa, user_email): {'ts': float, 'data': list} }
_LISTA_ALERTAS_TTL = 5 * 60         # 3 min — UI de gestão, não precisa ser realtime


def _invalidar_caches_empresa(empresa: str):
    """Remove todas as entradas de caches relacionadas à empresa (chaves simples e compostas)."""
    for cache in (_alertas_config_cache, _resumo_cache, _detalhes_cache, _lista_alertas_cache):
        cache.pop(empresa, None)
        for k in list(cache.keys()):
            if isinstance(k, tuple) and k[0] == empresa:
                cache.pop(k, None)

# SCHEDULER DE ALERTAS — background thread (anti-stampede 429)
# Objetivo: o endpoint /api/alertas/resumo NUNCA lê o Sheets
# diretamente durante uma requisição de usuário. Um background
# thread recalcula o cache a cada 4 min por empresa ativa.
# Isso elimina o stampede que causava o erro 429 em cascata.

_scheduler_started   = False
_scheduler_lock      = threading.Lock()
_empresas_ativas: set = set()           # empresas que já fizeram polling
_calc_locks: dict     = {}              # { empresa: threading.Lock() } anti-stampede
_calc_locks_meta      = threading.Lock()


def _get_calc_lock(empresa: str) -> threading.Lock:
    """Retorna (criando se necessário) o lock anti-stampede por empresa."""
    with _calc_locks_meta:
        if empresa not in _calc_locks:
            _calc_locks[empresa] = threading.Lock()
        return _calc_locks[empresa]


def _calcular_resumo_empresa(empresa: str, app_ref, alertas_override=None) -> dict:
    """
    Calcula o payload de resumo de alertas para uma empresa.
    Chamado pelo scheduler (background) e pelo endpoint (cache frio).
    Sempre retorna dict válido — nunca propaga exceção para o caller.

    Usa o breaker de LEITURA exclusivo dos alertas quando disponível,
    garantindo que falha aqui nunca afete escritas ou navegação.

    alertas_override: se fornecido, usa essa lista em vez de _get_alertas_config_cached.
    """
    try:
        alertas_config = alertas_override if alertas_override is not None else _get_alertas_config_cached(empresa)
        if not alertas_config:
            return {'success': True, 'total': 0, 'grupos': []}

        # ── Verifica breaker de leitura antes de chamar o Sheets ─────────
        # Se o breaker de leitura estiver OPEN, retorna cache anterior
        # sem propagar o erro — operador não é impactado.
        try:
            sheets_svc = getattr(app_ref, 'sheets_service', None)
            if sheets_svc:
                read_breaker = getattr(sheets_svc, '_read_breaker', None)
                if read_breaker and not read_breaker.call_allowed():
                    logger.debug(
                        f"[AlertasScheduler] _read_breaker OPEN para '{empresa}', "
                        f"servindo cache anterior sem chamar Sheets."
                    )
                    cached = _resumo_cache.get(empresa)
                    return cached['payload'] if cached else {'success': True, 'total': 0, 'grupos': []}
        except Exception:
            pass  # não deixa verificação de breaker derrubar o cálculo

        # ── Obtém wms_service e lê avarias do cache existente ────────────
        wms_svc = getattr(app_ref, 'wms_service', None)
        if not wms_svc:
            return {'success': True, 'total': 0, 'grupos': []}

        # get_wms_avarias_cached() usa cache interno de 60s — não chama Sheets
        # se o cache ainda estiver quente. Ótimo para o scheduler.
        avarias_raw = wms_svc.sheets.get_wms_avarias_cached()

        from modules.wms.models.avaria import Avaria
        avarias = [
            Avaria.from_dict(r) for r in avarias_raw
            if r.get('cod_empresa') == empresa or r.get('empresa') == empresa
        ]
        avarias = [a for a in avarias if a is not None]
        contadores = {
            a['nome']: {
                'cor':           a.get('cor_badge', 'warning'),
                'count':         0,
                'campo_data':    a.get('campo_data', 'data_registro'),
                'tela_destino':  a.get('tela_destino', 'consultar'),
                'campo_parar':   a.get('campo_parar',   ''),   # ADICIONAR
                'status_parar':  a.get('status_parar',  ''),
                'logica_parada': a.get('logica_parada', 'OU'), # ADICIONAR
                '_status_ativos_set': set(),
            }
            for a in alertas_config
        }
        ids_disparados = set()

        for avaria in avarias:
            av_dict = avaria.to_dict() if hasattr(avaria, 'to_dict') else avaria
            disparados = config_service.avaliar_alertas_avaria(
                av_dict, empresa, alertas_ativos=alertas_config
            )
            for d in disparados:
                nome = d['nome']
                if nome in contadores:
                    contadores[nome]['count'] += 1
                    # Coletar status_indenizacao único desta avaria que está disparando o alerta
                    status_ind = str(av_dict.get('status_indenizacao', '') or '').strip()
                    if status_ind:
                        contadores[nome]['_status_ativos_set'].add(status_ind)
                ids_disparados.add(av_dict.get('id'))

        _ordem = {'danger': 0, 'warning': 1, 'info': 2, 'success': 3, 'secondary': 4}
        grupos = sorted(
            [
                {
                    'nome':          n,
                    'cor':           i['cor'],
                    'count':         i['count'],
                    'campo_data':    i['campo_data'],
                    'tela_destino':  i['tela_destino'],
                    'campo_parar':   i.get('campo_parar',   ''),   # ADICIONAR
                    'status_parar':  i.get('status_parar',  ''),
                    'logica_parada': i.get('logica_parada', 'OU'), # ADICIONAR
                    'status_ativos': ','.join(sorted(i['_status_ativos_set'])),
                }
                for n, i in contadores.items() if i['count'] > 0
            ],
            key=lambda g: _ordem.get(g['cor'], 9)
        )
        return {'success': True, 'total': len(ids_disparados), 'grupos': grupos}

    except Exception as e:
        logger.error(f"[AlertasScheduler] _calcular_resumo_empresa('{empresa}'): {e}")
        # Em caso de erro, retorna cache anterior se disponível, senão zeros
        cached = _resumo_cache.get(empresa)
        return cached['payload'] if cached else {'success': True, 'total': 0, 'grupos': []}

def _background_alertas_worker(app):
    """
    Loop do background thread de pré-cálculo de alertas.

    Aguarda 120s após startup, depois recalcula a cada 4 minutos.
    4 min < TTL de 5 min → cache SEMPRE quente em regime normal.

    ⚡ NOVO: também pre-aquece _detalhes_cache para evitar o primeiro
    acesso pós-login lendo WMS_Param_Alertas diretamente.
    """
    logger.info("[AlertasScheduler] Worker iniciado. Primeiro ciclo em 120s.")
    _time.sleep(120)

    INTERVAL = 4 * 60  # 4 minutos

    while True:
        empresas = set(_empresas_ativas)  # snapshot thread-safe
        if empresas and config_service:
            logger.debug(
                f"[AlertasScheduler] Recalculando {len(empresas)} empresa(s): {empresas}"
            )
            for empresa in empresas:
                try:
                    # ── Resumo (já existia) ────────────────────────────────
                    payload_resumo = _calcular_resumo_empresa(empresa, app)
                    _resumo_cache[empresa] = {'ts': _time.time(), 'payload': payload_resumo}

                    # ── Detalhes (NOVO) ────────────────────────────────────
                    # Pre-aquece _detalhes_cache para que o primeiro acesso
                    # pós-login encontre cache quente e não leia a API.
                    try:
                        payload_detalhes = _calcular_detalhes_empresa(empresa, app)
                        _detalhes_cache[empresa] = {'ts': _time.time(), 'payload': payload_detalhes}
                        logger.debug(
                            f"[AlertasScheduler] '{empresa}': "
                            f"{payload_resumo.get('total', 0)} alertas resumo | "
                            f"{payload_detalhes.get('total', 0)} alertas detalhes"
                        )
                    except Exception as e_det:
                        logger.warning(f"[AlertasScheduler] Detalhes '{empresa}': {e_det}")
                        # Não propaga — resumo já foi calculado com sucesso

                except Exception as e:
                    logger.error(f"[AlertasScheduler] Empresa '{empresa}': {e}")

                _time.sleep(3)  # espaçamento entre empresas — evita burst

        _time.sleep(INTERVAL)

def _calcular_detalhes_empresa(empresa: str, app_ref) -> dict:
    """
    Calcula o payload de detalhes de alertas para uma empresa.
    Chamado pelo scheduler (background) — mantém _detalhes_cache sempre quente.
    Mesma lógica do endpoint api_detalhes_alertas(), mas sem dependência de request.
    """
    try:
        alertas_config = _get_alertas_config_cached(empresa)
        if not alertas_config:
            return {'success': True, 'total': 0, 'alertas': []}

        wms_svc = getattr(app_ref, 'wms_service', None)
        if not wms_svc:
            return {'success': True, 'total': 0, 'alertas': []}

        avarias_raw = wms_svc.sheets.get_wms_avarias_cached()

        from modules.wms.models.avaria import Avaria
        from datetime import datetime

        avarias = [
            Avaria.from_dict(r) for r in avarias_raw
            if r.get('cod_empresa') == empresa or r.get('empresa') == empresa
        ]
        avarias = [a for a in avarias if a is not None]

        alertas_disparados = []
        for avaria in avarias:
            av_dict = avaria.to_dict() if hasattr(avaria, 'to_dict') else avaria
            disparados = config_service.avaliar_alertas_avaria(
                av_dict, empresa, alertas_ativos=alertas_config
            )
            for d in disparados:
                campo_data = d.get('campo_data', 'data_registro')
                data_str = av_dict.get(campo_data, '')
                dias_atraso = 0
                if data_str:
                    try:
                        from datetime import datetime as _dt
                        data_obj = _dt.strptime(data_str[:10], '%Y-%m-%d')
                        dias_atraso = (datetime.now() - data_obj).days
                    except Exception:
                        pass

                alertas_disparados.append({
                    'avaria_id':   av_dict.get('id', ''),
                    'nota':        av_dict.get('nota_fiscal', av_dict.get('nota', '')),
                    'nome_alerta': d.get('nome', ''),
                    'cor':         d.get('cor_badge', 'warning'),
                    'dias_atraso': dias_atraso,
                    'tela_destino': d.get('tela_destino', 'consultar'),
                })

        return {
            'success': True,
            'total':   len(alertas_disparados),
            'alertas': alertas_disparados[:50]  # limita a 50 para o dropdown
        }

    except Exception as e:
        logger.error(f"[AlertasScheduler] _calcular_detalhes_empresa('{empresa}'): {e}")
        cached = _detalhes_cache.get(empresa)
        return cached['payload'] if cached else {'success': True, 'total': 0, 'alertas': []}
      
def init_alertas_scheduler(app):
    """
    Inicia o background thread de pré-cálculo de alertas.
    Chamado UMA VEZ no app.py após init_parametrizacao_routes().
    Flag global evita múltiplas instâncias (hot reload, gunicorn workers).
    """
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            logger.warning("[AlertasScheduler] Já iniciado, ignorando duplicata.")
            return
        _scheduler_started = True

    t = threading.Thread(
        target=_background_alertas_worker,
        args=(app,),
        daemon=True,
        name='wms-alertas-scheduler'
    )
    t.start()
    logger.info("[AlertasScheduler] Background thread iniciada (daemon).")

def _get_alertas_config_cached(empresa: str) -> list:
    """
    Retorna alertas ativos com cache de 15 min.
    ANTI-STAMPEDE: lock garante que apenas 1 thread lê WMS_Param_Alertas
    quando o cache expira. Demais threads aguardam e reutilizam o resultado.

    ANTES: sem lock → múltiplas threads simultâneas no primeiro login
           disparavam get_all_values() ao mesmo tempo → 429 em cascata.
    """
    agora = _time.time()

    # Fast path sem lock (cache quente)
    cached = _alertas_config_cache.get(empresa)
    if cached and (agora - cached['ts']) < _ALERTAS_CONFIG_TTL:
        return cached['data']

    # Cache frio: apenas 1 thread lê, as demais aguardam
    with _alertas_config_lock:
        # Double-check pós-lock
        cached = _alertas_config_cache.get(empresa)
        if cached and (_time.time() - cached['ts']) < _ALERTAS_CONFIG_TTL:
            return cached['data']

        # Somos a única thread lendo agora
        data = config_service.get_alertas_ativos(empresa) if config_service else []
        _alertas_config_cache[empresa] = {'ts': _time.time(), 'data': data}
        logger.debug(f"[AlertasConfig] Cache atualizado para '{empresa}': {len(data)} alertas")
        return data

def init_parametrizacao_routes(sheets_service, email_service=None):
    global param_service, config_service, _status_service, _automacao_service, _email_service

    param_service      = ParametrizacaoService(sheets_service)
    config_service     = WmsConfigService(sheets_service)
    _status_service    = StatusService(sheets_service)
    _automacao_service = AutomacaoStatusService(sheets_service)
    _email_service     = email_service

    logger.info("Rotas de parametrização (v2) inicializadas")
    logger.info("AutomacaoStatusService inicializado nas rotas de parametrização")

# ROTAS RAIZ — redirect para aba padrão

@param_bp.route('/')
@param_bp.route('')
@module_required('wms')
def parametrizacao_index():
    """
    FIX: Redireciona raiz do blueprint para a aba Geral (primeira aba — Opção C).
    Resolve o erro 404 ao clicar em 'Parametrização' no menu.
    """
    return redirect(url_for('wms_param.tab_geral'))


@param_bp.route('/manage')
@module_required('wms')
def manage_redirect():
    """Mantém compatibilidade com links antigos para /manage."""
    return redirect(url_for('wms_param.tab_geral'))


# TAB 1 — ALERTAS DO DASHBOARD

@param_bp.route('/alertas')
@module_required('wms')
@screen_required('wms', 'parametrizacao_pendentes')
@set_theme('wms')
def tab_alertas():
    """
    Configura alertas visuais no dashboard baseados em prazo de datas.

    Exemplo de alerta:
      - Campo: 'data_registro'  | Prazo: 15 dias | Cor: danger  | Msg: 'Pendente há 15+ dias'
      - Campo: 'prazo_pagamento'| Prazo: 5 dias  | Cor: warning | Msg: 'Pagamento vence em breve'

    Os alertas são avaliados no dashboard de avarias e exibem badges coloridos
    nas linhas da tabela.
    """
    empresa = session.get('user_empresa', '')
    logger.info(f"Tab Alertas acessada por {session.get('user_email')}")

    alertas = config_service.get_alertas(empresa) if config_service else []

    campos_data = [
        # Campos de data do modelo Avaria (verificados em avaria.py)
        ('data_registro',      'Data de Registro',       'atraso'),
        ('data_protocolo_mkt', 'Data Protocolo MKT',     'atraso'),
        ('prazo_pagamento',    'Prazo de Pagamento',      'vencimento'),   # data futura
        ('data_pagamento',     'Data de Pagamento',       'atraso'),
        ('data_entrega',       'Data de Entrega',         'vencimento'),   # data futura
        ('data_coleta',        'Data de Coleta',          'atraso'),
        ('data_finalizacao',   'Data de Finalização',     'atraso'),
        # data_triagem vem de ItemAvaria — pode estar vazia em avarias não triadas
        ('data_triagem',       'Data de Triagem (item)',  'atraso'),
    ]
    
    # Status reais do sistema para o campo "status_parar"
    # Organizados por status_type para facilitar a seleção na UI
    status_estoque = [
        'Em Trânsito', 'Aguard. Chegar CD', 'Aguardando Triagem', 'Em Triagem',
        'Em Processo de Indenização', 'Divergência de Estoque', 'Disponível',
        'Avaria Produto', 'Avaria Embalagem', 'Não Funciona', 'Defeito de Fábrica',
        'Em Processo Jurídico', 'Aguardando Armazenagem', 'Entregue Ag. Triagem',
        'Pendente', 'Não será devolvido', 'Refaturado', 'Recusado CD', 'Finalizada',
    ]
    status_indenizacao = [
        'Abrir PI', 'Não Indenizável', 'Aguard. Pagamento',
        'Pagto Ok - Total', 'Pagto Ok - Parcial',
        'Processo Judicial', 'Solicitar Reversa', 'Cliente ficou com produto',
        'Entregue Ag. Triagem',
    ]
    # Lista unificada, sem duplicatas, para o select do modal
    todos_status = list(dict.fromkeys(status_estoque + status_indenizacao))
    
    cores_badge = ['danger', 'warning', 'info', 'success', 'secondary']
    
    return render_template(
        'wms/parametrizacao/tab_alertas.html',
        alertas=alertas,
        campos_data=campos_data,
        todos_status=todos_status,
        status_estoque=status_estoque,
        status_indenizacao=status_indenizacao,
        cores_badge=cores_badge,
        active_tab='alertas',
    )

# TAB 2 — REGRAS DE INDENIZAÇÃO

@param_bp.route('/indenizacao')
@module_required('wms')
@screen_required('wms', 'parametrizacao_pendentes')
@set_theme('wms')
def tab_indenizacao():
    """
    Define regras automáticas de indenização por combinação de
    transportadora + tipo de avaria.

    Campos da regra:
      - transportadora     (ou '*' para qualquer)
      - tipo_avaria        (ou '*' para qualquer)
      - percentual_reemb   (% aplicado sobre valor_nf)
      - valor_max          (teto da indenização)
      - prazo_dias         (prazo para pagamento após avaria)
      - ativo              (sim/não)
    """
    empresa = session.get('user_empresa', '')
    logger.info(f"Tab Indenização acessada por {session.get('user_email')}")

    regras_indenizacao = config_service.get_regras_indenizacao(empresa) if config_service else []

    # Transportadoras únicas nas avarias (para o select)
    transportadoras = param_service.get_unique_transportadoras() if param_service else []

    status_indenizacao = []
    if _status_service:
        status_indenizacao = [s.status for s in _status_service.get_all_status(status_type='indenizacao')]

    return render_template(
        'wms/parametrizacao/tab_indenizacao.html',
        regras=regras_indenizacao,
        transportadoras=transportadoras,
        status_indenizacao=status_indenizacao,
        active_tab='indenizacao',
    )


# TAB 3 — FUNIL DE COBRANÇA

@param_bp.route('/cobranca')
@module_required('wms')
@screen_required('wms', 'parametrizacao_pendentes')
@set_theme('wms')
def tab_cobranca():
    """
    Configura o funil de cobrança de indenizações.

    Cada etapa do funil define:
      - nome_etapa      (ex: '1ª Cobrança', 'Escalação Gestão')
      - dias_apos_avaria (quantos dias após a avaria dispara a etapa)
      - acao            (email | notificacao | ambos)
      - destinatario    (transportadora | gestor | ambos)
      - template_email  (nome do template a usar)
      - ativo           (sim/não)

    O sistema avalia diariamente as avarias e dispara as etapas conforme
    as datas. A ordem das etapas é definida pela coluna 'ordem'.
    """
    empresa = session.get('user_empresa', '')
    logger.info(f"Tab Cobrança acessada por {session.get('user_email')}")

    etapas = config_service.get_etapas_cobranca(empresa) if config_service else []

    acoes_disponiveis = [
        ('email',        'Enviar Email'),
        ('notificacao',  'Notificação Interna'),
        ('ambos',        'Email + Notificação'),
    ]
    destinatarios_disponiveis = [
        ('transportadora', 'Transportadora'),
        ('gestor',         'Gestor WMS'),
        ('ambos',          'Transportadora + Gestor'),
    ]

    return render_template(
        'wms/parametrizacao/tab_cobranca.html',
        etapas=etapas,
        acoes=acoes_disponiveis,
        destinatarios=destinatarios_disponiveis,
        active_tab='cobranca',
    )


# TAB 4 — REGRAS DE PENDENTES (existente, agora como aba)

@param_bp.route('/pendentes')
@module_required('wms')
@screen_required('wms', 'parametrizacao_pendentes')
@set_theme('wms')
def tab_pendentes():
    empresa = session.get('user_empresa', '')
    rules = param_service.get_all_rules(include_inactive=True, empresa=empresa) if param_service else []
    locals_list = param_service.get_unique_locals() if param_service else []
    etiqueta_options = ['pendente', 'ativa', 'baixada', 'recusada']

    status_indenizacao_list = []
    status_estoque_list = []
    if _status_service:
        status_indenizacao_list = _status_service.get_all_status(status_type='indenizacao')
        status_estoque_list = _status_service.get_all_status(status_type='estoque')

    return render_template(
        'wms/parametrizacao/tab_pendentes.html',
        rules=rules,
        locals_list=locals_list,
        etiqueta_options=etiqueta_options,
        status_indenizacao_list=status_indenizacao_list,
        status_estoque_list=status_estoque_list,
        active_tab='pendentes',
    )


# TAB 1 — CONFIGURAÇÕES GERAIS (Opção C: primeira aba)

@param_bp.route('/geral')
@module_required('wms')
@screen_required('wms', 'parametrizacao_pendentes')
@set_theme('wms')
def tab_geral():
    """
    Configurações gerais da empresa no WMS — Email SMTP, Sistema, ASN.
    Seção de impressão removida (URL hardcoded no script, não editável pela UI).
    Armazenadas como chave-valor em WMS_Config.
    """
    empresa = session.get('user_empresa', '')
    logger.info(f"Tab Geral acessada por {session.get('user_email')}")

    configs = config_service.get_all_configs(empresa) if config_service else {}

    secoes = [
        {
            'id':     'email',
            'titulo': 'Email — Configurações SMTP',
            'icone':  'fas fa-envelope',
            'campos': [
                ('email_smtp_host',    'Servidor SMTP (ex: mail.suaempresa.com)', 'text', ''),
                ('email_smtp_port',    'Porta SMTP', 'select:587,465,25', '587'),
                ('email_smtp_usuario', 'Usuário SMTP (conta de serviço)', 'text', ''),
                ('email_smtp_senha',   'Senha SMTP', 'password', ''),
                ('email_smtp_tls',     'Usar STARTTLS?', 'select:sim,nao', 'sim'),
                ('email_smtp_ssl',     'Usar SSL direto (porta 465)?', 'select:nao,sim', 'nao'),
                ('email_remetente_wms', 'Remetente padrão WMS (notificações automáticas)', 'email', ''),
                ('email_assinatura',   'Assinatura dos emails', 'textarea', 'Equipe WMS'),
                ('email_gestores',     'Emails dos gestores WMS (separe por vírgula)', 'text', ''),
            ],
        },
        {
            'id':     'sistema',
            'titulo': 'Sistema',
            'icone':  'fas fa-cog',
            'campos': [
                ('sistema_timezone', 'Fuso horário', 'select:America/Sao_Paulo,America/Manaus,America/Fortaleza', 'America/Sao_Paulo'),
                ('sistema_formato_data', 'Formato de data', 'select:dd/mm/yyyy,yyyy-mm-dd', 'dd/mm/yyyy'),
            ],
        },
        {
            'id':     'asn',
            'titulo': 'Portal ASN',
            'icone':  'fas fa-truck',
            'campos': [
                ('asn_requer_aprovacao', 'ASN requer aprovação antes de criar avaria?', 'select:sim,nao', 'nao'),
                ('asn_email_notificacao', 'Notificar por email ao criar ASN?', 'select:sim,nao', 'sim'),
                ('asn_prazo_antecedencia_horas', 'Alertar operador com antecedência (horas)', 'number', '2'),
            ],
        },
        {
            'id':     'etiqueta_inicial',
            'titulo': 'Etiqueta Inicial — Destino Intermediário (Validação de Estoque)',
            'icone':  'fas fa-tag',
            'campos': [
                (
                    'etiqueta_inicial_codlocal_padrao',
                    'Local padrão de destino (codlocal) — aplica para todos os produtos '
                    'quando nenhuma exceção for configurada ou aplicável. '
                    'Ex: 696 (Aguardando Armazenagem). Deixe vazio para desativar.',
                    'text',
                    '696',
                ),
                (
                    'indenizacao_locais_contabilizar',
                    'Localizações que contabilizam para indenização (separe por vírgula) — '
                    'Ex: Disponível,Aguardando Armazenagem. '
                    'Somente os itens nessas localizações terão seus valores somados como base. '
                    'Se vazio, usa o valor da NFD como base (comportamento anterior).',
                    'text',
                    '',
                ),
                (
                    'etiqueta_inicial_excecoes_json',
                    'Exceções por status (JSON) — redireciona para local diferente conforme status da avaria. '
                    'Formato: {"campo:valor": "codlocal"}. '
                    'Campos: status_indenizacao ou status_estoque. '
                    'Ex: {"status_indenizacao:Indenização": "497"} → produtos com esse status vão para 497. '
                    'Primeira exceção que bater vence. Deixe vazio se não precisar.',
                    'textarea',
                    '',
                ),
            ],
        },
        {
            'id':     'asn_recusado_centena',
            'titulo': 'ASN — Roteamento de Itens Recusados por Centena',
            'icone':  'fas fa-route',
            'campos': [
                (
                    'asn_gerar_etiqueta_recusado',
                    'Gerar etiqueta inicial (e lote) para itens recusados no portal ASN? '
                    'Se marcado como "nao", os saldos recusados ficarão pendentes (sem etiqueta) por padrão.',
                    'select:sim,nao',
                    'nao',
                ),
                (
                    'asn_recusado_roteamento_centena',
                    'Ativar roteamento por centena para itens recusados no recebimento? '
                    'Quando ativo, itens recusados são direcionados para o local determinado '
                    'pela centena do local de origem mais o sufixo. '
                    'Ex: origem 401 (centena 4) + sufixo 97 → destino 497. '
                    'Origem 201 (centena 2) → destino 297.',
                    'select:nao,sim',
                    'nao',
                ),
                (
                    'asn_recusado_sufixo_centena',
                    'Sufixo do local de destino para itens recusados (padrão: 97). '
                    'Destino final = centena(codlocalorig) * 100 + sufixo. '
                    'Ex: sufixo 97 com origem 401 → destino 497.',
                    'number',
                    '97',
                ),
            ],
        },
    ]

    return render_template(
        'wms/parametrizacao/tab_geral.html',
        configs=configs,
        secoes=secoes,
        empresa=empresa,
        active_tab='geral',
    )


# TAB 2 — CONFIGURAÇÕES DO DASHBOARD

@param_bp.route('/dashboard')
@module_required('wms')
@screen_required('wms', 'parametrizacao_pendentes')
@set_theme('wms')
def tab_dashboard():
    """
    Configurações visuais e comportamentais do Dashboard WMS.
    Controla cards exibidos, filtros padrão, paginação e indicadores.

    Separado de 'Geral' por ser focado exclusivamente na experiência
    do operador no painel principal.
    """
    empresa = session.get('user_empresa', '')
    logger.info(f"Tab Dashboard acessada por {session.get('user_email')}")

    configs = config_service.get_all_configs(empresa) if config_service else {}

    # Carrega os status disponíveis para os seletores visuais
    status_indenizacao_list_full = []
    status_indenizacao_opts = []
    if _status_service:
        status_indenizacao_list_full = sorted(
            _status_service.get_all_status(status_type='indenizacao'),
            key=lambda s: s.status
        )
        status_indenizacao_opts = [s.status for s in status_indenizacao_list_full]

    status_estoque_opts = []
    if _status_service:
        status_estoque_opts = sorted([s.status for s in _status_service.get_all_status(status_type='estoque')])

    indenizacao_options_str = ','.join(status_indenizacao_opts)
    estoque_options_str = ','.join(status_estoque_opts)

    # Monta etapas atuais do funil (dinâmicas: até 15 etapas)
    funil_etapas_atual = []
    for i in range(1, 16):
        label  = configs.get(f'dash_funil_etapa{i}_label',  '').strip()
        status = configs.get(f'dash_funil_etapa{i}_status', '').strip()
        if label:
            funil_etapas_atual.append({'label': label, 'status': status})
    if not funil_etapas_atual:
        funil_etapas_atual = [
            {'label': 'Registradas',    'status': ''},
            {'label': 'Aprovadas',      'status': 'Indenização Aprovada,Aprovado'},
            {'label': 'Em Negociação',  'status': 'Em Negociação'},
            {'label': 'Pendente Pgto',  'status': 'Pendente de Pagamento,Atrasado'},
            {'label': 'Pago',           'status': 'Pago,Pago Total,Pago Parcial,Quitado'},
        ]

    secoes = [
        {
            'id':     'dashboard_visual',
            'titulo': 'Exibição e Paginação',
            'icone':  'fas fa-table',
            'campos': [
                ('dashboard_itens_por_pagina',   'Itens por página na tabela', 'number', '50'),
                ('dashboard_ordenacao_padrao',    'Ordenação padrão da tabela',
                 'select:data_registro_desc,data_registro_asc,transportadora_asc,valor_nf_desc', 'data_registro_desc'),
                ('dashboard_colunas_visiveis',   'Colunas visíveis (separe por vírgula)', 'text',
                 'id_avaria,nota,transportadora,status,data_registro,valor_nf'),
            ],
        },
        {
            'id':     'dashboard_alertas',
            'titulo': 'Limites de Alerta nos Cards',
            'icone':  'fas fa-exclamation-triangle',
            'campos': [
                ('dashboard_dias_alerta_pendente',   'Dias para alertar avaria sem movimentação', 'number', '15'),
                ('dashboard_dias_alerta_pagamento',  'Dias para alertar pagamento vencendo',       'number', '30'),
                ('dashboard_dias_alerta_triagem',    'Dias para alertar avaria sem triagem',       'number', '7'),
            ],
        },
        {
            'id':     'dashboard_cards',
            'titulo': 'Cards do Dashboard',
            'icone':  'fas fa-th-large',
            'campos': [
                ('dashboard_exibir_card_pendentes',  'Exibir card "Avarias Pendentes"?',   'select:sim,nao', 'sim'),
                ('dashboard_exibir_card_triagem',    'Exibir card "Aguardando Triagem"?',  'select:sim,nao', 'sim'),
                ('dashboard_exibir_card_pagamento',  'Exibir card "Aguardando Pagamento"?','select:sim,nao', 'sim'),
                ('dashboard_exibir_card_asn',        'Exibir card "ASNs Agendados"?',      'select:sim,nao', 'sim'),
            ],
        },
        # ── NOVO: Configurações dos painéis analíticos ───────────────────────────
        {
            'id':     'dash_analitico_indenizacao',
            'titulo': 'Painéis Analíticos — Status de Indenização',
            'icone':  'fas fa-chart-pie',
            'campos': [
                (
                    'dash_status_indenizaveis',
                    'Status que contam como indenizáveis',
                    f'multiselect:{indenizacao_options_str}',
                    'Indenização Aprovada,Pago,Pago Parcial,Em Negociação,Pendente',
                ),
                (
                    'dash_status_pago',
                    'Status que representam pagamento realizado (inclui parcial)',
                    f'multiselect:{indenizacao_options_str}',
                    'Pago,Pago Total,Pago Parcial,Quitado',
                ),
                (
                    'dash_status_pendente',
                    'Status que representam pendência de pagamento',
                    f'multiselect:{indenizacao_options_str}',
                    'Pendente de Pagamento,Em Negociação,Aprovado,Indenização Aprovada',
                ),
            ],
        },
        {
            'id':     'dash_analitico_campos_data',
            'titulo': 'Painéis Analíticos — Campo de Data Padrão',
            'icone':  'fas fa-calendar-alt',
            'campos': [
                (
                    'dash_financeiro_campo_data',
                    'Campo de data padrão — Painel Financeiro',
                    'select:data_registro,data_pagamento,prazo_pagamento,data_finalizacao',
                    'data_registro',
                ),
                (
                    'dash_operacional_campo_data',
                    'Campo de data padrão — Painel Operacional',
                    'select:data_registro,data_finalizacao',
                    'data_registro',
                ),
                (
                    'dash_qualidade_campo_data',
                    'Campo de data padrão — Painel Qualidade',
                    'select:data_registro,data_finalizacao',
                    'data_registro',
                ),
                (
                    'dash_cobranca_campo_data',
                    'Campo de data padrão — Painel Cobrança',
                    'select:prazo_pagamento,data_registro,data_pagamento',
                    'prazo_pagamento',
                ),
                (
                    'dash_produtividade_campo_data',
                    'Campo de data padrão — Painel Produtividade',
                    'select:data_registro,data_finalizacao',
                    'data_registro',
                ),
            ],
        },
        {
            'id':     'dash_analitico_operacional',
            'titulo': 'Painéis Analíticos — Status Operacional',
            'icone':  'fas fa-boxes',
            'campos': [
                ('dash_op_status_pendente',   'Status = Pendente',               f'multiselect:{estoque_options_str}', 'Pendente,Aguardando Recebimento'),
                ('dash_op_status_nao_chegou', 'Status = Não chegou/Em trânsito', f'multiselect:{estoque_options_str}', 'Em Trânsito,Aguardando Chegada'),
                ('dash_op_status_triagem',    'Status = Em Triagem',             f'multiselect:{estoque_options_str}', 'Em Triagem,Aguardando Triagem'),
                ('dash_op_status_etiquetado', 'Status = Etiquetado',             f'multiselect:{estoque_options_str}', 'Etiquetado,Aguardando Destino'),
                ('dash_op_status_finalizado', 'Status = Finalizado',             f'multiselect:{estoque_options_str}', 'Finalizado,Baixado,Finalizada'),
            ],
        },
        {
            'id':     'dash_evolucao_estoque',
            'titulo': 'Gráfico de Evolução de Estoque (Sankhya)',
            'icone':  'fas fa-chart-bar',
            'campos': [
                (
                    'dash_evolucao_codlocais',
                    'Locais (CODLOCAL) considerados no gráfico — separe por vírgula. '
                    'Deixe vazio para incluir todos os locais. '
                    'Ex: 401,402,501 — apenas esses locais serão somados no saldo e valor.',
                    'text',
                    '',
                ),
            ],
        },
    ]

    return render_template(
        'wms/parametrizacao/tab_dashboard.html',
        configs=configs,
        secoes=secoes,
        empresa=empresa,
        active_tab='dashboard',
        funil_etapas_atual=funil_etapas_atual,
        status_indenizacao_list_full=status_indenizacao_list_full,
    )


# APIS — ALERTAS

@param_bp.route('/api/alertas', methods=['GET'])
@module_required('wms')
def api_list_alertas():
    empresa = session.get('user_empresa', '')
    user_email = session.get('user_email', '')
    try:
        agora = _time.time()
        cache_key = (empresa, user_email)
        cached = _lista_alertas_cache.get(cache_key)
        if cached and (agora - cached['ts']) < _LISTA_ALERTAS_TTL:
            return jsonify({'success': True, 'alertas': cached['data']})

        alertas = config_service.get_alertas(empresa)
        alertas = [a for a in alertas if a.get('criado_por', '') == user_email]
        _lista_alertas_cache[cache_key] = {'ts': agora, 'data': alertas}
        return jsonify({'success': True, 'alertas': alertas})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@param_bp.route('/api/alertas', methods=['POST'])
@module_required('wms')
def api_create_alerta():
    empresa = session.get('user_empresa', '')
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Corpo vazio'}), 400
        success, msg = config_service.add_alerta(empresa, data, session.get('user_email', 'sistema'))
        if success:
            _invalidar_caches_empresa(empresa)

        return jsonify({'success': success, ('message' if success else 'error'): msg}), (200 if success else 400)
    except Exception as e:
        logger.error(f"api_create_alerta: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@param_bp.route('/api/alertas/<alerta_id>', methods=['PUT'])
@module_required('wms')
def api_update_alerta(alerta_id):
    empresa = session.get('user_empresa', '')
    try:
        data = request.get_json()
        success, msg = config_service.update_alerta(empresa, alerta_id, data)
        if success:
            _invalidar_caches_empresa(empresa)

        return jsonify({'success': success, ('message' if success else 'error'): msg}), (200 if success else 400)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@param_bp.route('/api/alertas/<alerta_id>', methods=['DELETE'])
@module_required('wms')
def api_delete_alerta(alerta_id):
    empresa = session.get('user_empresa', '')
    try:
        success, msg = config_service.delete_alerta(empresa, alerta_id)
        if success:
            _invalidar_caches_empresa(empresa)

        return jsonify({'success': success, ('message' if success else 'error'): msg}), (200 if success else 400)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# APIS — INDENIZAÇÃO

@param_bp.route('/api/indenizacao', methods=['GET'])
@module_required('wms')
def api_list_indenizacao():
    empresa = session.get('user_empresa', '')
    try:
        regras = config_service.get_regras_indenizacao(empresa)
        return jsonify({'success': True, 'regras': regras})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@param_bp.route('/api/indenizacao', methods=['POST'])
@module_required('wms')
def api_create_indenizacao():
    empresa = session.get('user_empresa', '')
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Corpo vazio'}), 400
        success, msg = config_service.add_regra_indenizacao(empresa, data, session.get('user_email', 'sistema'))
        return jsonify({'success': success, ('message' if success else 'error'): msg}), (200 if success else 400)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@param_bp.route('/api/indenizacao/<regra_id>', methods=['PUT'])
@module_required('wms')
def api_update_indenizacao(regra_id):
    empresa = session.get('user_empresa', '')
    try:
        data = request.get_json()
        success, msg = config_service.update_regra_indenizacao(empresa, regra_id, data)
        return jsonify({'success': success, ('message' if success else 'error'): msg}), (200 if success else 400)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@param_bp.route('/api/indenizacao/<regra_id>', methods=['DELETE'])
@module_required('wms')
def api_delete_indenizacao(regra_id):
    empresa = session.get('user_empresa', '')
    try:
        success, msg = config_service.delete_regra_indenizacao(empresa, regra_id)
        return jsonify({'success': success, ('message' if success else 'error'): msg}), (200 if success else 400)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# APIS — COBRANÇA

@param_bp.route('/api/cobranca', methods=['GET'])
@module_required('wms')
def api_list_cobranca():
    empresa = session.get('user_empresa', '')
    try:
        etapas = config_service.get_etapas_cobranca(empresa)
        return jsonify({'success': True, 'etapas': etapas})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@param_bp.route('/api/cobranca', methods=['POST'])
@module_required('wms')
def api_create_cobranca():
    empresa = session.get('user_empresa', '')
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Corpo vazio'}), 400
        success, msg = config_service.add_etapa_cobranca(empresa, data, session.get('user_email', 'sistema'))
        return jsonify({'success': success, ('message' if success else 'error'): msg}), (200 if success else 400)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@param_bp.route('/api/cobranca/<etapa_id>', methods=['PUT'])
@module_required('wms')
def api_update_cobranca(etapa_id):
    empresa = session.get('user_empresa', '')
    try:
        data = request.get_json()
        success, msg = config_service.update_etapa_cobranca(empresa, etapa_id, data)
        return jsonify({'success': success, ('message' if success else 'error'): msg}), (200 if success else 400)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@param_bp.route('/api/cobranca/<etapa_id>', methods=['DELETE'])
@module_required('wms')
def api_delete_cobranca(etapa_id):
    empresa = session.get('user_empresa', '')
    try:
        success, msg = config_service.delete_etapa_cobranca(empresa, etapa_id)
        return jsonify({'success': success, ('message' if success else 'error'): msg}), (200 if success else 400)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# APIS — CONFIGURAÇÕES GERAIS

@param_bp.route('/api/config', methods=['GET'])
@module_required('wms')
def api_get_config():
    empresa = session.get('user_empresa', '')
    try:
        configs = config_service.get_all_configs(empresa)
        return jsonify({'success': True, 'configs': configs})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# SUBSTITUIR a função api_save_config inteira:

@param_bp.route('/api/config', methods=['POST'])
@module_required('wms')
def api_save_config():
    """Salva múltiplos campos de config em UMA operação batch."""
    empresa = session.get('user_empresa', '')
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Corpo vazio'}), 400

        salvos, n_erros, erros = config_service.batch_set_configs(
            empresa, {k: str(v) for k, v in data.items()},
            session.get('user_email', 'sistema')
        )

        if n_erros and salvos == 0:
            return jsonify({'success': False, 'error': '; '.join(erros)}), 500

        msg = f'{salvos} configuração(ões) salva(s)'
        if n_erros:
            msg += f' ({n_erros} com erro: {"; ".join(erros)})'

        # ── Recarrega EmailService se campos SMTP foram alterados ──────────
        if _email_service and any(k.startswith('email_smtp') for k in data.keys()):
            try:
                smtp_cfg = config_service.get_smtp_config(empresa)
                _email_service.update_smtp_config(smtp_cfg)
                logger.info("EmailService reconfigurado após salvar parametrizações")
            except Exception as e_email:
                logger.warning(f"Não foi possível reconfigurar EmailService: {e_email}")

        return jsonify({'success': True, 'message': msg})
    except Exception as e:
        logger.error(f"api_save_config: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
      
# APIS — REGRAS DE PENDENTES (mantidas para compatibilidade)

@param_bp.route('/api/rules', methods=['GET'])
@module_required('wms')
def api_list_rules():
    try:
        inc = request.args.get('include_inactive', 'false').lower() == 'true'
        empresa = session.get('user_empresa', '')
        rules = param_service.get_all_rules(include_inactive=inc, empresa=empresa)
        return jsonify({'success': True, 'rules': [r.to_dict() for r in rules], 'total': len(rules)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@param_bp.route('/api/rules', methods=['POST'])
@module_required('wms')
def api_create_rule():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Corpo da requisição vazio'}), 400
        # Garantir que a empresa da sessão seja sempre usada (não vem do frontend)
        data['empresa'] = session.get('user_empresa', '')
        success, msg = param_service.add_rule(data, session.get('user_email', 'sistema'))
        return jsonify({'success': success, ('message' if success else 'error'): msg}), (200 if success else 400)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@param_bp.route('/api/rules/<rule_id>', methods=['PUT'])
@module_required('wms')
def api_update_rule(rule_id):
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Corpo da requisição vazio'}), 400
        success, msg = param_service.update_rule(rule_id, data)
        return jsonify({'success': success, ('message' if success else 'error'): msg}), (200 if success else 400)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@param_bp.route('/api/rules/<rule_id>/toggle', methods=['POST'])
@module_required('wms')
def api_toggle_rule(rule_id):
    try:
        data = request.get_json()
        if data is None:
            return jsonify({'success': False, 'error': 'Corpo da requisição vazio'}), 400
        success, msg = param_service.set_active(rule_id, bool(data.get('ativo', False)))
        return jsonify({'success': success, ('message' if success else 'error'): msg}), (200 if success else 400)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@param_bp.route('/api/rules/<rule_id>', methods=['DELETE'])
@module_required('wms')
def api_delete_rule(rule_id):
    try:
        success, msg = param_service.delete_rule(rule_id)
        return jsonify({'success': success, ('message' if success else 'error'): msg}), (200 if success else 400)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@param_bp.route('/api/options', methods=['GET'])
@module_required('wms')
def api_options():
    try:
        locals_list = param_service.get_unique_locals() if param_service else []
        etiqueta_options = ['pendente', 'ativa', 'baixada', 'recusada']
        ind_list, est_list = [], []
        if _status_service:
            ind_list = [s.status for s in _status_service.get_all_status(status_type='indenizacao')]
            est_list = [s.status for s in _status_service.get_all_status(status_type='estoque')]
        return jsonify({
            'success': True,
            'locals': locals_list,
            'etiquetas': etiqueta_options,
            'status_indenizacao': ind_list,
            'status_estoque': est_list,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# TAB — AUTOMAÇÕES DE STATUS

@param_bp.route('/automacoes')
@module_required('wms')
@screen_required('wms', 'parametrizacao_pendentes')
@set_theme('wms')
def tab_automacoes():
    empresa = session.get('user_empresa', '')
    logger.info(f"Tab Automações acessada por {session.get('user_email')}")

    automacoes = _automacao_service.get_all(empresa=empresa) if _automacao_service else []
    triggers   = _automacao_service.get_triggers_info() if _automacao_service else []

    status_estoque_list     = []
    status_indenizacao_list = []
    status_all = []
    if _status_service:
        status_estoque_list     = _status_service.get_all_status(status_type='estoque')
        status_indenizacao_list = _status_service.get_all_status(status_type='indenizacao')
        status_all = status_estoque_list + status_indenizacao_list

    return render_template(
        'wms/parametrizacao/tab_automacoes.html',
        automacoes=automacoes,
        triggers=triggers,
        status_list=status_all,
        status_estoque=status_estoque_list,
        status_indenizacao=status_indenizacao_list,
        active_tab='automacoes',
    )


# APIS — AUTOMAÇÕES DE STATUS

@param_bp.route('/api/automacoes', methods=['GET'])
@module_required('wms')
def api_list_automacoes():
    empresa = session.get('user_empresa', '')
    try:
        items = _automacao_service.get_all(empresa=empresa) if _automacao_service else []
        return jsonify({'success': True, 'automacoes': items, 'total': len(items)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# CORRIGIDO: fora da função GET (indentação no nível raiz do módulo)
@param_bp.route('/api/automacoes', methods=['POST'])
@module_required('wms')
def api_create_automacao():
    empresa = session.get('user_empresa', '')
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Corpo vazio'}), 400
        success, msg = _automacao_service.criar(empresa, data, session.get('user_email', 'sistema'))
        return jsonify({'success': success, ('message' if success else 'error'): msg}), (200 if success else 400)
    except Exception as e:
        logger.error(f"api_create_automacao: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@param_bp.route('/api/automacoes/<auto_id>', methods=['PUT'])
@module_required('wms')
def api_update_automacao(auto_id):
    try:
        data = request.get_json()
        success, msg = _automacao_service.atualizar(auto_id, data)
        return jsonify({'success': success, ('message' if success else 'error'): msg}), (200 if success else 400)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@param_bp.route('/api/automacoes/<auto_id>', methods=['DELETE'])
@module_required('wms')
def api_delete_automacao(auto_id):
    try:
        success, msg = _automacao_service.excluir(auto_id)
        return jsonify({'success': success, ('message' if success else 'error'): msg}), (200 if success else 404)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@param_bp.route('/api/automacoes/<auto_id>/toggle', methods=['POST'])
@module_required('wms')
def api_toggle_automacao(auto_id):
    try:
        success, msg, novo_valor = _automacao_service.toggle_ativo(auto_id)
        return jsonify({'success': success, 'novo_valor': novo_valor,
                        ('message' if success else 'error'): msg})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@param_bp.route('/api/alertas/resumo', methods=['GET'])
@module_required('wms')
def api_resumo_alertas():
    """
    Retorna resumo de alertas agrupados por tipo.

    ARQUITETURA v2 (anti-burst 429):
    ─ Em regime normal (scheduler ativo após 120s):
        100% serve do cache in-memory (~0ms). O background thread
        mantém o cache sempre quente. Zero chamadas ao Sheets.

    ─ Cache frio (primeiros 120s após startup):
        Lock anti-stampede garante que somente 1 das N requisições
        simultâneas calcula; as demais aguardam e servem do mesmo
        resultado. Sem burst para o Sheets.

    ─ Falha no Sheets (429 ou outro erro):
        Retorna cache anterior ou zeros. Nunca 500 para o usuário.
        O breaker de leitura pode estar OPEN, mas isso NÃO afeta
        navegação nem escritas (breakers separados por tipo).
    """
    empresa    = session.get('user_empresa', '')
    user_email = session.get('user_email', '')
    cache_key  = (empresa, user_email)

    # Registra empresa como ativa para o scheduler pré-aquecer nos próximos ciclos
    _empresas_ativas.add(empresa)

    # ── Cache quente: resposta instantânea (~0ms) ──────────────────────
    agora  = _time.time()
    cached = _resumo_cache.get(cache_key)
    if cached and (agora - cached['ts']) < _RESUMO_CACHE_TTL:
        return jsonify(cached['payload'])

    if not config_service:
        return jsonify({'success': True, 'total': 0, 'grupos': []})

    # ── Cache frio: anti-stampede ──────────────────────────────────────
    from flask import current_app
    app_ref = current_app._get_current_object()

    lock = _get_calc_lock(cache_key)
    with lock:
        cached = _resumo_cache.get(cache_key)
        if cached and (_time.time() - cached['ts']) < _RESUMO_CACHE_TTL:
            return jsonify(cached['payload'])

        # Filtra alertas pelo criador antes de calcular o resumo
        alertas_todos = _get_alertas_config_cached(empresa)
        alertas_usuario = [a for a in alertas_todos if a.get('criado_por', '') == user_email]

        payload = _calcular_resumo_empresa(empresa, app_ref, alertas_override=alertas_usuario)
        _resumo_cache[cache_key] = {'ts': _time.time(), 'payload': payload}
        return jsonify(payload)



# NOTA: A inicialização do scheduler é feita no app.py logo após:
#   init_parametrizacao_routes(sheets_service, email_service=email_service)

@param_bp.route('/api/alertas/detalhes', methods=['GET'])
@module_required('wms')
def api_detalhes_alertas():
    """
    Retorna lista detalhada das avarias com alertas disparados.
    Usado pelo dropdown do sino no navbar.
    Retorna: avaria_id, nota, nome_alerta, cor, dias_atraso
    """
    empresa    = session.get('user_empresa', '')
    user_email = session.get('user_email', '')
    cache_key  = (empresa, user_email)
    try:
        if not config_service:
            return jsonify({'success': True, 'total': 0, 'alertas': []})

        # ── Cache hit ─────────────────────────────────────────────────────
        agora = _time.time()
        cached = _detalhes_cache.get(cache_key)
        if cached and (agora - cached['ts']) < _DETALHES_CACHE_TTL:
            return jsonify(cached['payload'])

        # ── Cache miss ────────────────────────────────────────────────────
        alertas_config_todos = _get_alertas_config_cached(empresa)
        alertas_config = [a for a in alertas_config_todos if a.get('criado_por', '') == user_email]
        if not alertas_config:
            payload = {'success': True, 'total': 0, 'alertas': []}
            _detalhes_cache[cache_key] = {'ts': agora, 'payload': payload}
            return jsonify(payload)

        from flask import current_app
        from datetime import datetime
        wms_svc = getattr(current_app, 'wms_service', None)
        if not wms_svc:
            return jsonify({'success': True, 'total': 0, 'alertas': []})

        # USA CACHE de avarias (60s TTL) — NÃO chama get_all_avarias() direto
        from modules.wms.models.avaria import Avaria
        avarias_raw = wms_svc.sheets.get_wms_avarias_cached()
        avarias = [
            Avaria.from_dict(r) for r in avarias_raw
            if r.get('cod_empresa') == empresa or r.get('empresa') == empresa
        ]
        avarias = [a for a in avarias if a is not None]

        resultado = []
        ids_vistos = set()

        for avaria in avarias:
            av_dict = avaria.to_dict() if hasattr(avaria, 'to_dict') else avaria
            disparados = config_service.avaliar_alertas_avaria(av_dict, empresa)

            for d in disparados:
                avaria_id = av_dict.get('id', '')
                nota      = av_dict.get('nota', av_dict.get('numnota', ''))
                nome      = d.get('nome', '')
                cor       = d.get('cor_badge', 'warning')

                campo_data = d.get('campo', 'data_registro')
                dias = 0
                data_str = av_dict.get(campo_data, '')
                if data_str:
                    try:
                        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%Y-%m-%d %H:%M:%S'):
                            try:
                                dt = datetime.strptime(str(data_str)[:10], fmt[:10])
                                dias = (datetime.now() - dt).days
                                break
                            except ValueError:
                                continue
                    except Exception:
                        dias = 0

                chave = f"{avaria_id}:{nome}"
                if chave not in ids_vistos:
                    ids_vistos.add(chave)
                resultado.append({
                    'avaria_id': avaria_id,   # ← garante que este campo está presente
                    'nota':      nota,
                    'nome':      nome,
                    'cor':       cor,
                    'dias':      dias,
                })

        resultado.sort(key=lambda x: x['dias'], reverse=True)

        payload = {'success': True, 'total': len(resultado), 'alertas': resultado}
        _detalhes_cache[cache_key] = {'ts': agora, 'payload': payload}
        return jsonify(payload)

    except Exception as e:
        logger.error(f"api_detalhes_alertas: {e}")
        return jsonify({'success': False, 'total': 0, 'alertas': [], 'error': str(e)}), 500

# TAB — TEMPLATES DE E-MAIL

@param_bp.route('/templates')
@module_required('wms')
@screen_required('wms', 'parametrizacao_pendentes')
@set_theme('wms')
def tab_templates():
    """
    Gerencia templates de e-mail reutilizáveis.
    Usados pelo funil de cobrança e outras notificações WMS.
    Suporte a variáveis dinâmicas {{chave}} substituídas no momento do envio.
    Editor visual Quill.js + modo HTML direto + preview em iframe.
    Armazenados em WMS_EmailTemplates no Google Sheets.
    """
    empresa = session.get('user_empresa', '')
    logger.info(f"Tab Templates acessada por {session.get('user_email')}")

    templates = config_service.get_templates(empresa) if config_service else []

    return render_template(
        'wms/parametrizacao/tab_templates.html',
        templates=templates,
        empresa=empresa,
        active_tab='templates',
    )


# APIS — TEMPLATES DE E-MAIL

@param_bp.route('/api/templates', methods=['GET'])
@module_required('wms')
def api_list_templates():
    empresa = session.get('user_empresa', '')
    try:
        templates = config_service.get_templates(empresa) if config_service else []
        return jsonify({'success': True, 'templates': templates})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@param_bp.route('/api/templates', methods=['POST'])
@module_required('wms')
def api_create_template():
    empresa = session.get('user_empresa', '')
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Corpo vazio'}), 400
        success, msg = config_service.add_template(empresa, data, session.get('user_email', 'sistema'))
        return jsonify({'success': success, ('message' if success else 'error'): msg}), (200 if success else 400)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@param_bp.route('/api/templates/<template_id>', methods=['PUT'])
@module_required('wms')
def api_update_template(template_id):
    empresa = session.get('user_empresa', '')
    try:
        data = request.get_json() or {}
        data['_user'] = session.get('user_email', 'sistema')
        success, msg = config_service.update_template(empresa, template_id, data)
        return jsonify({'success': success, ('message' if success else 'error'): msg}), (200 if success else 400)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@param_bp.route('/api/templates/<template_id>', methods=['DELETE'])
@module_required('wms')
def api_delete_template(template_id):
    empresa = session.get('user_empresa', '')
    try:
        success, msg = config_service.delete_template(empresa, template_id)
        return jsonify({'success': success, ('message' if success else 'error'): msg}), (200 if success else 400)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# API — TESTE DE CONEXÃO SMTP

@param_bp.route('/api/email/testar', methods=['POST'])
@module_required('wms')
def api_testar_email():
    """
    Testa conexão SMTP com os parâmetros enviados no corpo da requisição.
    Usado pelo botão "Testar Conexão SMTP" na Tab Geral.
    Não envia e-mail — apenas faz handshake SMTP e retorna resultado.

    Body JSON esperado:
        email_smtp_host, email_smtp_port, email_smtp_usuario,
        email_smtp_senha, email_smtp_tls, email_smtp_ssl
    """
    try:
        data = request.get_json() or {}

        if not _email_service:
            return jsonify({'success': False, 'error': 'EmailService não disponível'}), 500

        ok, msg = _email_service.test_connection(
            host    = data.get('email_smtp_host') or None,
            port    = int(data['email_smtp_port']) if data.get('email_smtp_port') else None,
            usuario = data.get('email_smtp_usuario') or None,
            senha   = data.get('email_smtp_senha') or None,
            use_tls = data.get('email_smtp_tls', 'sim') == 'sim',
            use_ssl = data.get('email_smtp_ssl', 'nao') == 'sim',
        )
        return jsonify({'success': ok, ('message' if ok else 'error'): msg})

    except Exception as e:
        logger.error(f"api_testar_email: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
