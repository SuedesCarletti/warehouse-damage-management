"""
core/services/sheets_circuit_breaker.py

Circuit Breaker universal para o Google Sheets API.

Inspirado no padrão Netflix Hystrix — protege TODAS as chamadas à API
de forma transparente, sem exigir mudanças em config_service, wms_service
ou qualquer outro serviço que use o Sheets.

Como funciona:
  1. Após conectar, google_sheets_service envolve self.sheet com ProtectedSpreadsheet
  2. Qualquer chamada .worksheet() em qualquer parte do código retorna ProtectedWorksheet
  3. ProtectedWorksheet intercepta get_all_records(), append_row(), update_cell(), etc.
  4. Cada chamada verifica o circuito antes de executar e notifica sucesso/falha

Estados do circuito:
  CLOSED    → Normal. Chamadas passam para a API.
  OPEN      → N falhas consecutivas. Chamadas bloqueadas, retorna fallback imediato.
  HALF_OPEN → Após reset_timeout, permite 1 chamada de teste.
              Sucesso → CLOSED. Falha → OPEN novamente.

v2 — DOIS BREAKERS INDEPENDENTES:
  breaker_read  → governa leituras (get_all_records, get_all_values, etc.)
  breaker_write → governa escritas (append_row, update, etc.)

  Motivação: a API do Google tem cotas separadas para leitura e escrita
  (300/min cada, por projeto). Um burst de leituras que causa 429 NÃO deve
  bloquear escritas, pois a cota de escrita ainda está disponível.
  Antes disso, um 429 em leitura de alertas bloqueava o operador de salvar
  qualquer dado — comportamento incorreto e desnecessário.

Cobertura automática (zero mudanças nesses arquivos):
  ✅ config_service._read_all()          → ws.get_all_values()
  ✅ config_service._ensure_sheet()      → self.sheets.sheet.worksheet()
  ✅ wms_service.get_all_avarias()       → ws.get_all_records()
  ✅ google_sheets_service.get_all_records()
  ✅ rename_departamento(), delete_cargo(), etc.
  ✅ Qualquer chamada futura de qualquer serviço
"""

import time
import logging
import threading
from enum import Enum
from typing import Optional, Any

logger = logging.getLogger(__name__)


# CIRCUIT BREAKER — Estado e lógica de abertura/fechamento

class CircuitState(Enum):
    CLOSED    = 'CLOSED'
    OPEN      = 'OPEN'
    HALF_OPEN = 'HALF_OPEN'


class SheetsCircuitBreaker:
    """
    Circuit breaker thread-safe.
    Usado internamente por ProtectedWorksheet — não precisa ser chamado diretamente.
    """

    def __init__(self, fail_max: int = 3, reset_timeout: int = 60, name: str = 'GoogleSheets'):
        self.fail_max = fail_max
        self.reset_timeout = reset_timeout
        self.name = name

        self._state = CircuitState.CLOSED
        self._fail_count = 0
        self._last_failure_time: Optional[float] = None
        self._lock = threading.Lock()

        # Estatísticas para monitoramento
        self._total_calls = 0
        self._total_failures = 0
        self._total_blocked = 0

        # Throttle de log quando OPEN — evita flood de warnings
        self._last_blocked_log: float = 0.0
        self._log_throttle_secs: int = 30  # loga no máximo 1x a cada 30s quando OPEN

    @property
    def state(self) -> CircuitState:
        """Retorna estado atual, fazendo OPEN → HALF_OPEN automaticamente após timeout."""
        with self._lock:
            if (self._state == CircuitState.OPEN and
                    self._last_failure_time and
                    time.time() - self._last_failure_time >= self.reset_timeout):
                self._state = CircuitState.HALF_OPEN
                logger.info(f"[CircuitBreaker:{self.name}] HALF_OPEN — testando recuperação")
            return self._state

    def call_allowed(self) -> bool:
        """True se CLOSED ou HALF_OPEN. False se OPEN."""
        self._total_calls += 1
        allowed = self.state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)
        if not allowed:
            self._total_blocked += 1
        return allowed

    def record_success(self):
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._fail_count = 0
                logger.info(f"[CircuitBreaker:{self.name}] FECHADO — serviço recuperado")
            elif self._fail_count > 0:
                self._fail_count = max(0, self._fail_count - 1)

    def record_failure(self):
        with self._lock:
            self._fail_count += 1
            self._total_failures += 1
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                logger.warning(
                    f"🔴 [CircuitBreaker:{self.name}] ABERTO "
                    f"— teste falhou, aguardando {self.reset_timeout}s"
                )
            elif self._state == CircuitState.CLOSED and self._fail_count >= self.fail_max:
                self._state = CircuitState.OPEN
                logger.error(
                    f"🔴 [CircuitBreaker:{self.name}] ABERTO "
                    f"após {self._fail_count} falhas consecutivas. "
                    f"Servindo cache/fallback por {self.reset_timeout}s."
                )
            else:
                logger.warning(
                    f"⚠️ [CircuitBreaker:{self.name}] Falha {self._fail_count}/{self.fail_max}"
                )

    def status(self) -> dict:
        current = self.state
        secs_open = None
        secs_until = None
        if self._last_failure_time and current == CircuitState.OPEN:
            secs_open = int(time.time() - self._last_failure_time)
            secs_until = max(0, self.reset_timeout - secs_open)
        return {
            'name': self.name,
            'state': current.value,
            'fail_count': self._fail_count,
            'fail_max': self.fail_max,
            'reset_timeout': self.reset_timeout,
            'seconds_open': secs_open,
            'seconds_until_half_open': secs_until,
            'stats': {
                'total_calls': self._total_calls,
                'total_failures': self._total_failures,
                'total_blocked': self._total_blocked,
            }
        }

    def should_log_blocked(self) -> bool:
        """
        Retorna True apenas se passaram >= _log_throttle_secs desde o último log
        de bloqueio. Evita flood de warnings quando o circuito está OPEN e
        múltiplos workers ou requests concorrentes chegam simultaneamente.
        """
        now = time.time()
        if now - self._last_blocked_log >= self._log_throttle_secs:
            self._last_blocked_log = now
            return True
        return False

    def reset(self):
        """Reset manual para CLOSED. Útil em testes ou painel admin."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._fail_count = 0
            self._last_failure_time = None
        logger.info(f"[CircuitBreaker:{self.name}] Reset manual — CLOSED")


# EXCEPTION

class CircuitBreakerOpen(Exception):
    """Raised quando circuito está OPEN e a chamada foi bloqueada."""
    pass


# PROTECTED WORKSHEET — Proxy transparente sobre gspread.Worksheet

class ProtectedWorksheet:
    """
    Proxy sobre gspread.Worksheet que intercepta todas as chamadas à API.

    Código existente não muda nada:
        ws = self.sheet.worksheet('Avarias')    # retorna ProtectedWorksheet
        data = ws.get_all_records()             # interceptado automaticamente
        ws.append_row([...])                    # interceptado automaticamente

    v2: dois breakers independentes
        breaker_read  → leituras (get_all_records, get_all_values, etc.)
        breaker_write → escritas (append_row, update, etc.)

    Leituras: breaker_read + retry com backoff exponencial para 429.
    Escritas: breaker_write apenas (sem retry — evita duplicatas).
    """

    # Métodos de LEITURA — retry + backoff + breaker_read
    _READ_METHODS = frozenset({
        'get_all_records', 'get_all_values', 'get',
        'row_values', 'col_values', 'cell', 'range',
        'find', 'findall',
    })

    # Métodos de ESCRITA — breaker_write sem retry automático
    _WRITE_METHODS = frozenset({
        'append_row', 'append_rows', 'update_cell', 'update',
        'batch_update', 'delete_rows', 'delete_row',
        'insert_row', 'insert_rows', 'clear', 'resize',
    })

    def __init__(
        self,
        worksheet,
        breaker_read: SheetsCircuitBreaker,
        breaker_write: SheetsCircuitBreaker,
        max_retries: int = 3,
        base_delay: float = 2.0,
    ):
        # Usa object.__setattr__ para evitar recursão no __getattr__
        object.__setattr__(self, '_ws',           worksheet)
        object.__setattr__(self, '_breaker',      breaker_read)   # compat. legada
        object.__setattr__(self, '_breaker_read', breaker_read)
        object.__setattr__(self, '_breaker_write',breaker_write)
        object.__setattr__(self, '_max_retries',  max_retries)
        object.__setattr__(self, '_base_delay',   base_delay)

    def __getattr__(self, name: str) -> Any:
        """
        Intercepta qualquer acesso a atributo/método do worksheet.
        Métodos conhecidos recebem proteção; demais passam diretamente.
        """
        ws          = object.__getattribute__(self, '_ws')
        br_read     = object.__getattribute__(self, '_breaker_read')
        br_write    = object.__getattribute__(self, '_breaker_write')
        attr        = getattr(ws, name)

        if not callable(attr):
            return attr  # propriedades passam direto

        if name in ProtectedWorksheet._READ_METHODS:
            return object.__getattribute__(self, '_wrap_read')(attr, name, br_read)

        if name in ProtectedWorksheet._WRITE_METHODS:
            return object.__getattribute__(self, '_wrap_write')(attr, name, br_write)

        return attr  # métodos desconhecidos passam direto

    def _wrap_read(self, fn, method_name: str, breaker: SheetsCircuitBreaker):
        """Retorna wrapper com retry + backoff + circuit breaker para leituras."""
        max_retries = object.__getattribute__(self, '_max_retries')
        base_delay  = object.__getattribute__(self, '_base_delay')
        ws          = object.__getattribute__(self, '_ws')
        sheet_title = getattr(ws, 'title', '?')

        def wrapper(*args, **kwargs):
            # ── 1. Verifica circuito de LEITURA ──────────────────────────
            if not breaker.call_allowed():
                if breaker.should_log_blocked():
                    status = breaker.status()
                    logger.warning(
                        f"🔴 [CircuitBreaker] ABERTO — "
                        f"leitura '{sheet_title}.{method_name}' bloqueada. "
                        f"Tentativa automática em {status.get('seconds_until_half_open', '?')}s. "
                        f"(Bloqueios adicionais suprimidos por {breaker._log_throttle_secs}s)"
                    )
                return [] if method_name in ('get_all_records', 'get_all_values', 'get') else None

            # ── 2. Tenta com retry para 429 ───────────────────────────────
            from gspread.exceptions import APIError
            for tentativa in range(max_retries):
                try:
                    result = fn(*args, **kwargs)
                    breaker.record_success()
                    return result

                except APIError as e:
                    is_quota = '429' in str(e) or 'Quota exceeded' in str(e)
                    if is_quota:
                        breaker.record_failure()
                        if tentativa < max_retries - 1:
                            delay = base_delay * (2 ** tentativa)
                            logger.warning(
                                f"⚠️ [429] '{sheet_title}.{method_name}' "
                                f"tentativa {tentativa + 1}/{max_retries}. "
                                f"Aguardando {delay:.1f}s | "
                                f"Breaker: {breaker.state.value}"
                            )
                            time.sleep(delay)
                        else:
                            logger.error(
                                f"❌ [429] '{sheet_title}.{method_name}' "
                                f"falhou após {max_retries} tentativas. "
                                f"Breaker: {breaker.state.value}"
                            )
                            return [] if method_name in (
                                'get_all_records', 'get_all_values', 'get'
                            ) else None
                    else:
                        breaker.record_failure()
                        raise

                except Exception as e:
                    logger.error(
                        f"❌ Erro inesperado em '{sheet_title}.{method_name}': {e}"
                    )
                    return [] if method_name in (
                        'get_all_records', 'get_all_values', 'get'
                    ) else None

            return []

        return wrapper

    def _wrap_write(self, fn, method_name: str, breaker: SheetsCircuitBreaker):
        """Retorna wrapper com circuit breaker para escritas (breaker_write independente)."""
        max_retries = object.__getattribute__(self, '_max_retries')
        base_delay  = object.__getattribute__(self, '_base_delay')
        ws          = object.__getattribute__(self, '_ws')
        sheet_title = getattr(ws, 'title', '?')

        def wrapper(*args, **kwargs):
            # ── 1. Verifica circuito de ESCRITA ──────────────────────────
            if not breaker.call_allowed():
                status = breaker.status()
                raise CircuitBreakerOpen(
                    f"[CircuitBreaker] ABERTO — escrita '{sheet_title}.{method_name}' "
                    f"bloqueada. Tente em {status.get('seconds_until_half_open', '?')}s"
                )

            # ── 2. Retry apenas para 429 (evita duplicatas em escrita) ────
            from gspread.exceptions import APIError
            for tentativa in range(max_retries):
                try:
                    result = fn(*args, **kwargs)
                    breaker.record_success()
                    return result

                except APIError as e:
                    is_quota = '429' in str(e) or 'Quota exceeded' in str(e)
                    if is_quota and tentativa < max_retries - 1:
                        breaker.record_failure()
                        delay = base_delay * (2 ** tentativa)
                        logger.warning(
                            f"⚠️ [Write 429] '{sheet_title}.{method_name}' "
                            f"tentativa {tentativa + 1}/{max_retries}. "
                            f"Aguardando {delay:.1f}s"
                        )
                        time.sleep(delay)
                    else:
                        if is_quota:
                            breaker.record_failure()
                        raise

                except Exception:
                    raise

            raise Exception(
                f"Escrita '{sheet_title}.{method_name}' "
                f"falhou após {max_retries} tentativas."
            )

        return wrapper

    # ── Propriedades delegadas explicitamente ────────────────────────────

    @property
    def title(self):
        return object.__getattribute__(self, '_ws').title

    @property
    def row_count(self):
        return object.__getattribute__(self, '_ws').row_count

    @property
    def col_count(self):
        return object.__getattribute__(self, '_ws').col_count

    def __repr__(self):
        ws      = object.__getattribute__(self, '_ws')
        br_read = object.__getattribute__(self, '_breaker_read')
        br_write= object.__getattribute__(self, '_breaker_write')
        return (
            f"<ProtectedWorksheet '{getattr(ws, 'title', '?')}' "
            f"read={br_read.state.value} write={br_write.state.value}>"
        )


# PROTECTED SPREADSHEET — Proxy sobre gspread.Spreadsheet
# Intercepta .worksheet() para retornar ProtectedWorksheet automaticamente

class ProtectedSpreadsheet:
    """
    Proxy sobre gspread.Spreadsheet.

    Intercepta APENAS o método .worksheet() para retornar ProtectedWorksheet.
    Todos os outros métodos (add_worksheet, values_batch_get, etc.) passam direto.

    v2 — recebe dois breakers:
        breaker_read  → repassado ao ProtectedWorksheet para leituras
        breaker_write → repassado ao ProtectedWorksheet para escritas

    ── Mudança necessária no google_sheets_service.connect(): ────────────

        # ANTES:
        self.sheet = ProtectedSpreadsheet(self.sheet, self._breaker)

        # DEPOIS:
        self.sheet = ProtectedSpreadsheet(self.sheet, self._read_breaker, self._write_breaker)
    """

    def __init__(
        self,
        spreadsheet,
        breaker_read: SheetsCircuitBreaker,
        breaker_write: SheetsCircuitBreaker,
    ):
        object.__setattr__(self, '_sp',           spreadsheet)
        object.__setattr__(self, '_breaker',      breaker_read)   # compat. legada
        object.__setattr__(self, '_breaker_read', breaker_read)
        object.__setattr__(self, '_breaker_write',breaker_write)
        object.__setattr__(self, '_ws_cache',     {})

    def worksheet(self, title: str) -> ProtectedWorksheet:
        """
        Retorna ProtectedWorksheet em vez de gspread.Worksheet raw.

        Protege o sp.worksheet() com o breaker de LEITURA (essa chamada
        faz fetch de metadados na API).
        Cache de worksheets evita chamadas repetidas à API.
        """
        sp       = object.__getattribute__(self, '_sp')
        br_read  = object.__getattribute__(self, '_breaker_read')
        br_write = object.__getattribute__(self, '_breaker_write')
        cache    = object.__getattribute__(self, '_ws_cache')

        # Cache hit: sem chamada à API
        if title in cache:
            return ProtectedWorksheet(cache[title], br_read, br_write)

        # Verifica breaker de leitura antes de chamar a API
        if not br_read.call_allowed():
            raise CircuitBreakerOpen(
                f"[CircuitBreaker] ABERTO — worksheet('{title}') bloqueado. "
                f"Tente em {br_read.status().get('seconds_until_half_open', '?')}s"
            )

        try:
            ws = sp.worksheet(title)
            cache[title] = ws
            return ProtectedWorksheet(ws, br_read, br_write)
        except Exception as _exc:
            # WorksheetNotFound é erro de configuração (aba inexistente),
            # NÃO é falha de cota ou conectividade — não deve penalizar o breaker.
            try:
                from gspread.exceptions import WorksheetNotFound as _WNF
                if not isinstance(_exc, _WNF):
                    br_read.record_failure()
            except ImportError:
                br_read.record_failure()
            raise

    def __getattr__(self, name: str) -> Any:
        """Todos os outros atributos/métodos passam direto para o spreadsheet real."""
        sp = object.__getattribute__(self, '_sp')
        return getattr(sp, name)

    def __repr__(self):
        sp       = object.__getattribute__(self, '_sp')
        br_read  = object.__getattribute__(self, '_breaker_read')
        br_write = object.__getattribute__(self, '_breaker_write')
        return (
            f"<ProtectedSpreadsheet '{getattr(sp, 'title', '?')}' "
            f"read={br_read.state.value} write={br_write.state.value}>"
        )

    def should_log_blocked(self) -> bool:
        """Compat. legada — delega ao breaker de leitura."""
        br_read = object.__getattribute__(self, '_breaker_read')
        return br_read.should_log_blocked()
