"""
sheets_wms_mixin.py
────────────────────────────────────────────────────────────────
Responsabilidade: Cache de leitura para as abas WMS (Avarias e Itens_Avarias).

Métodos públicos:
  get_wms_avarias_cached()
  get_wms_itens_cached()
  invalidate_wms_cache()
  invalidate_all_caches()

v2 — INVALIDAÇÃO CROSS-WORKER
  Problema anterior: invalidate_wms_cache() só limpava a memória do worker
  que fez a escrita. Os demais workers do Gunicorn (Railway) continuavam
  servindo dados stale por até N minutos (TTL completo).

  Solução: arquivo de sinal em /tmp/wms_cache_bust.ts
  - Ao invalidar: escreve timestamp atual no arquivo
  - Ao ler do cache: compara cache_timestamp vs arquivo
    → se arquivo for mais recente → força reload, independente do TTL
  - Todos os workers no Railway compartilham /tmp (mesmo container)
  - Custo: 1 file.stat() por request de leitura (~0.01ms, desprezível)
"""

import logging
import time
import pathlib

logger = logging.getLogger(__name__)

# Arquivo de sinal compartilhado entre workers (mesmo container Railway)
_BUST_FILE = pathlib.Path('/tmp/wms_cache_bust.ts')


def _read_bust_ts() -> float:
    """Lê o timestamp do último bust cross-worker. Retorna 0.0 se não existir."""
    try:
        return float(_BUST_FILE.read_text())
    except Exception:
        return 0.0


def _write_bust_ts():
    """Escreve timestamp atual no arquivo de sinal cross-worker (escrita atômica)."""
    try:
        tmp = _BUST_FILE.with_suffix('.tmp')
        tmp.write_text(str(time.time()))
        tmp.replace(_BUST_FILE)  # rename atômico — evita leitura parcial em workers concorrentes
    except Exception as e:
        # Nunca deixa falha de I/O afetar o fluxo principal
        logger.warning(f"Não foi possível escrever sinal de bust cross-worker: {e}")


class SheetsWmsMixin:
    """Cache WMS com mutex anti-stampede, circuit breaker e invalidação cross-worker."""

    # HELPERS INTERNOS

    def _is_cache_busted(self, cache_timestamp: float) -> bool:
        """
        Verifica se outro worker invalidou o cache DEPOIS deste worker cachear.

        Retorna True se o arquivo de sinal for mais recente que cache_timestamp,
        sinalizando que os dados em memória estão stale por escrita de outro worker.
        """
        if not cache_timestamp:
            return False
        bust_ts = _read_bust_ts()
        return bust_ts > cache_timestamp

    # LEITURA COM CACHE

    def get_wms_avarias_cached(self) -> list:
        """
        ⚡ Cache com mutex anti-stampede e invalidação cross-worker.

        Ordem de verificação:
          1. Cache existe + TTL válido + não houve bust cross-worker → retorna cache (fast path)
          2. Cache expirado por TTL → reload normal
          3. Cache válido por TTL mas bust cross-worker detectado → reload forçado
             (garante que registros inseridos/alterados em outro worker apareçam)
        """
        # Fast path sem lock: cache quente E não houve escrita em outro worker
        if (self._wms_avarias_cache is not None
                and self._wms_avarias_cache_timestamp
                and (time.time() - self._wms_avarias_cache_timestamp) < self._wms_avarias_cache_ttl
                and not self._is_cache_busted(self._wms_avarias_cache_timestamp)):
            logger.debug("Cache hit: Avarias WMS")
            return self._wms_avarias_cache

        # Cache frio ou bust detectado: apenas uma thread busca, as demais esperam
        with self._wms_avarias_lock:
            # Double-check pós-lock (outra thread pode ter atualizado enquanto esperávamos)
            if (self._wms_avarias_cache is not None
                    and self._wms_avarias_cache_timestamp
                    and (time.time() - self._wms_avarias_cache_timestamp) < self._wms_avarias_cache_ttl
                    and not self._is_cache_busted(self._wms_avarias_cache_timestamp)):
                logger.debug("Cache hit pós-lock: Avarias WMS")
                return self._wms_avarias_cache

            motivo = "TTL expirado" if (
                not self._wms_avarias_cache_timestamp
                or (time.time() - self._wms_avarias_cache_timestamp) >= self._wms_avarias_cache_ttl
            ) else "bust cross-worker detectado"
            logger.info(f"Recarregando Avarias WMS ({motivo})...")

            data = self.get_all_records('Avarias')

            # Não sobrescreve cache com lista vazia (pode ser falha de API/circuit breaker)
            if data:
                self._wms_avarias_cache = data
                self._wms_avarias_cache_timestamp = time.time()
                logger.info(f"Cache WMS Avarias atualizado: {len(data)} registros")
            elif self._wms_avarias_cache is not None:
                # Mantém cache anterior mesmo expirado — melhor dado antigo que dado vazio
                logger.warning(
                    f"⚠️ get_all_records('Avarias') retornou vazio. "
                    f"Mantendo cache anterior ({len(self._wms_avarias_cache)} registros). "
                    f"Breaker: {self._breaker.state.value}"
                )
            else:
                # Primeira carga e já retornou vazio
                self._wms_avarias_cache = data
                self._wms_avarias_cache_timestamp = time.time()
                logger.warning("Cache WMS Avarias inicializado vazio")

            return self._wms_avarias_cache or []

    def get_wms_itens_cached(self) -> list:
        """
        ⚡ Cache com mutex anti-stampede e invalidação cross-worker.
        Mesma lógica de get_wms_avarias_cached() para Itens_Avarias.
        """
        # Fast path: cache quente E sem bust cross-worker
        if (self._wms_itens_cache is not None
                and self._wms_itens_cache_timestamp
                and (time.time() - self._wms_itens_cache_timestamp) < self._wms_itens_cache_ttl
                and not self._is_cache_busted(self._wms_itens_cache_timestamp)):
            logger.debug("Cache hit: Itens_Avarias WMS")
            return self._wms_itens_cache

        with self._wms_itens_lock:
            # Double-check pós-lock
            if (self._wms_itens_cache is not None
                    and self._wms_itens_cache_timestamp
                    and (time.time() - self._wms_itens_cache_timestamp) < self._wms_itens_cache_ttl
                    and not self._is_cache_busted(self._wms_itens_cache_timestamp)):
                logger.debug("Cache hit pós-lock: Itens WMS")
                return self._wms_itens_cache

            motivo = "TTL expirado" if (
                not self._wms_itens_cache_timestamp
                or (time.time() - self._wms_itens_cache_timestamp) >= self._wms_itens_cache_ttl
            ) else "bust cross-worker detectado"
            logger.info(f"Recarregando Itens_Avarias WMS ({motivo})...")

            data = self.get_all_records('Itens_Avarias')

            if data:
                self._wms_itens_cache = data
                self._wms_itens_cache_timestamp = time.time()
                logger.info(f"Cache WMS Itens atualizado: {len(data)} registros")
            elif self._wms_itens_cache is not None:
                logger.warning(
                    f"⚠️ get_all_records('Itens_Avarias') retornou vazio. "
                    f"Mantendo cache anterior ({len(self._wms_itens_cache)} registros)."
                )
            else:
                self._wms_itens_cache = data
                self._wms_itens_cache_timestamp = time.time()
                logger.warning("Cache WMS Itens inicializado vazio")

            return self._wms_itens_cache or []

    def get_wms_validacoes_cached(self) -> list:
        """
        ⚡ Cache com mutex anti-stampede para WMS_ValidacoesEstoque.
        """
        if getattr(self, '_wms_validacoes_cache', None) is not None \
           and getattr(self, '_wms_validacoes_cache_timestamp', None) \
           and (time.time() - self._wms_validacoes_cache_timestamp) < self._wms_itens_cache_ttl \
           and not self._is_cache_busted(self._wms_validacoes_cache_timestamp):
            return self._wms_validacoes_cache

        if not hasattr(self, '_wms_validacoes_lock'):
            import threading
            self._wms_validacoes_lock = threading.Lock()

        with self._wms_validacoes_lock:
            if getattr(self, '_wms_validacoes_cache', None) is not None \
               and getattr(self, '_wms_validacoes_cache_timestamp', None) \
               and (time.time() - self._wms_validacoes_cache_timestamp) < self._wms_itens_cache_ttl \
               and not self._is_cache_busted(self._wms_validacoes_cache_timestamp):
                return self._wms_validacoes_cache

            logger.info("Recarregando WMS_ValidacoesEstoque WMS...")
            data = self.get_all_records('WMS_ValidacoesEstoque')
            if data:
                self._wms_validacoes_cache = data
                self._wms_validacoes_cache_timestamp = time.time()
            elif getattr(self, '_wms_validacoes_cache', None) is not None:
                pass
            else:
                self._wms_validacoes_cache = data
                self._wms_validacoes_cache_timestamp = time.time()

            return self._wms_validacoes_cache or []

    # INVALIDAÇÃO

    def invalidate_wms_cache(self):
        """
        Invalida cache WMS após escrita.

        v2: Além de limpar a memória deste worker, escreve um sinal no filesystem
        (/tmp/wms_cache_bust.ts) para que os demais workers do Gunicorn detectem
        o bust na próxima leitura e recarreguem automaticamente.

        Efeito: registro inserido ou alterado aparece corretamente em TODOS os
        workers em até 1 request (sem esperar o TTL expirar).

        IMPORTANTE: chamar após qualquer operação de escrita em Avarias ou Itens_Avarias.
        """
        # 1. Limpar cache local deste worker
        self._wms_avarias_cache = None
        self._wms_avarias_cache_timestamp = None
        self._wms_itens_cache = None
        self._wms_itens_cache_timestamp = None
        self._wms_validacoes_cache = None
        self._wms_validacoes_cache_timestamp = None

        # 2. Sinalizar para os outros workers via arquivo compartilhado
        _write_bust_ts()

        logger.info("Cache WMS invalidado (sinal cross-worker emitido)")

    def invalidate_all_caches(self):
        """Invalida todos os caches: usuários, permissões e WMS."""
        self.invalidate_users_cache()
        self.invalidate_permissions_cache()
        self.invalidate_wms_cache()
