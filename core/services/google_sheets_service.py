"""
google_sheets_service.py — NÚCLEO
────────────────────────────────────────────────────────────────
Responsabilidade: Infraestrutura pura de conexão, estrutura de abas
e operações de leitura/escrita na API do Google Sheets.

Sem lógica de negócio. Toda lógica específica vive nos mixins:
  SheetsPermissionsMixin  → permissões, matrix, telas, batch load
  SheetsUsersMixin        → usuários, departamentos, cargos
  SheetsHelpdeskMixin     → categorias e tickets
  SheetsWmsMixin          → cache WMS (Avarias e Itens_Avarias)

Interface pública inalterada: todos os imports externos continuam
funcionando sem qualquer modificação.
"""

import json
import base64
import logging
import traceback
import time
import random as _random
from typing import Dict, List, Tuple, Optional, Any
from datetime import datetime

import gspread
from gspread.exceptions import APIError

from core.services.sheets_circuit_breaker import (
    SheetsCircuitBreaker,
    ProtectedSpreadsheet,
)
from core.services.sheets_permissions_mixin import SheetsPermissionsMixin
from core.services.sheets_users_mixin import SheetsUsersMixin
from core.services.sheets_helpdesk_mixin import SheetsHelpdeskMixin
from core.services.sheets_wms_mixin import SheetsWmsMixin

logger = logging.getLogger(__name__)

# Constante global usada pelos mixins via import lazy
TELAS_PADRAO_POR_MODULO = {
    'helpdesk': [
        'dashboard', 'new_ticket', 'view_ticket',
        'list_tickets_own', 'list_tickets_all', 'admin_panel',
    ],
    'wms': [
        'dash_operacional', 'dash_financeiro', 'dash_qualidade', 'dash_cobranca', 'dash_produtividade',
        'avarias_dashboard', 'registrar_avaria', 'ver_avarias', 'relatorio_avarias',
        'consultar_estoque',
        'param_geral', 'param_email', 'param_status', 'param_alertas', 'param_indenizacao', 'param_coleta',
        'portal_asn', 'fila_asn', 'portal_transportadora',
    ],
    'portal': [
        'dashboard', 'ver_portal', 'submeter_solicitacao',
    ],
}


class GoogleSheetsService(
    SheetsPermissionsMixin,
    SheetsUsersMixin,
    SheetsHelpdeskMixin,
    SheetsWmsMixin,
):
    """
    Serviço principal do Google Sheets.

    Herda toda a lógica de negócio dos mixins via herança múltipla Python.
    Este arquivo contém apenas infraestrutura pura.
    """

    def __init__(self, credentials_b64: str = None, spreadsheet_id: str = None):
        self.gc = None
        self.logger = logging.getLogger(__name__)
        self.sheet = None
        self.connected = False
        self.credentials_b64 = credentials_b64
        self.spreadsheet_id = spreadsheet_id

        # ── Caches de permissões ──────────────────────────────────────────────
        self._permissions_cache = None
        self._cache_timestamp = None
        self._cache_ttl = 900          # permissões: 15 min

        self.company_permissions = {}
        self.role_permissions = {}

        # ── Cache de usuários ─────────────────────────────────────────────────
        self._users_cache = None
        self._users_cache_timestamp = None
        self._users_cache_ttl = 900    # usuários: 15 min
        self._users_source = None      # 'sheets' | 'cache' | 'fallback'

        # ── Cache WMS ─────────────────────────────────────────────────────────
        self._wms_avarias_cache = None
        self._wms_avarias_cache_timestamp = None
        self._wms_avarias_cache_ttl = 90 + _random.uniform(-10, 10)

        self._wms_itens_cache = None
        self._wms_itens_cache_timestamp = None
        self._wms_itens_cache_ttl   = 90 + _random.uniform(-10, 10)

        # ── Cache de telas customizadas ───────────────────────────────────────
        self._telas_cache = {}
        self._TELAS_TTL = 600          # 10 minutos

        # ── Outros caches ────────────────────────────────────────────────────
        self._users_ws = None          # referência cached do worksheet Usuários

        self._generic_cache: Dict[str, tuple] = {}  # {sheet_name: (data, ts)}
        self._generic_cache_ttl = 600  # 10 min para abas auxiliares

        # ── Mutexes anti-stampede WMS ─────────────────────────────────────────
        self._wms_avarias_lock = __import__('threading').Lock()
        self._wms_itens_lock = __import__('threading').Lock()
        self._batch_lock = __import__('threading').Lock()

        # ── Circuit Breakers (instanciados ANTES do connect()) ────────────────
        # v2 — dois breakers independentes: leitura e escrita têm cotas separadas
        # na API do Google (300/min cada). Um burst de leitura não bloqueia escrita.
        self._read_breaker = SheetsCircuitBreaker(
            fail_max=5,        # mais tolerante — burst transitório não abre o circuito
            reset_timeout=65,  # 65s > 60s de janela da API → garante cota renovada
            name='GoogleSheets-Read'
        )
        self._write_breaker = SheetsCircuitBreaker(
            fail_max=3,        # mais rígido — escrita com falha é problema real
            reset_timeout=30,  # recupera em 30s — usuário não espera mais que isso
            name='GoogleSheets-Write'
        )
        # Compat. legada: self._breaker continua funcionando onde ainda é referenciado
        self._breaker = self._read_breaker

        if credentials_b64 and spreadsheet_id:
            self.connect()

    # CONEXÃO

    def connect(self) -> bool:
        """
        Conecta ao Google Sheets com retry exponencial para erro 429 (quota exceeded).

        Implementa:
        - 3 tentativas máximas
        - Backoff exponencial (2s, 4s, 8s)
        - Tratamento específico para erro 429

        Returns:
            bool: True se conectou com sucesso, False caso contrário.
        """
        max_retries = 3
        base_delay = 2

        for tentativa in range(max_retries):
            try:
                from google.oauth2.service_account import Credentials

                if not self.credentials_b64 or not self.spreadsheet_id:
                    logger.warning(" Credenciais Google Sheets não fornecidas")
                    return False

                if tentativa == 0:
                    logger.info("Conectando ao Google Sheets...")
                else:
                    logger.info(f"Tentativa {tentativa + 1}/{max_retries} de conexão...")

                creds_dict = json.loads(base64.b64decode(self.credentials_b64).decode('utf-8'))
                scopes = [
                    'https://www.googleapis.com/auth/spreadsheets',
                    'https://www.googleapis.com/auth/drive'
                ]

                creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
                self.gc = gspread.authorize(creds)

                self.sheet = self.gc.open_by_key(self.spreadsheet_id)

                # Envolve com ProtectedSpreadsheet — a partir daqui,
                # QUALQUER .worksheet() retorna ProtectedWorksheet com
                # circuit breaker + retry automático.
                self.sheet = ProtectedSpreadsheet(
                    self.sheet,
                    self._read_breaker,
                    self._write_breaker,
                )

                self._ensure_structure()
                self.connected = True
                logger.info("Google Sheets conectado com PERMISSÕES DE ESCRITA")

                # Warm-up (pré-carrega worksheet WMS)
                try:
                    if self.connected and self.sheet:
                        warm_start = time.time()
                        ws = self.sheet.worksheet('Avarias')
                        _ = ws.row_count
                        warm_time = time.time() - warm_start
                        logger.info(f"Google Sheets warm-up OK em {warm_time:.1f}s")
                except Exception as e:
                    logger.warning(f"Sheets warm-up falhou (não-fatal): {e}")

                self.load_permissions()
                return True

            except APIError as e:
                if '429' in str(e) or 'Quota exceeded' in str(e):
                    if tentativa < max_retries - 1:
                        delay = base_delay * (2 ** tentativa)
                        logger.warning(
                            f"⚠️  Quota excedida (429), aguardando {delay}s antes da próxima tentativa..."
                        )
                        time.sleep(delay)
                        continue
                    else:
                        logger.error(
                            f"❌ Quota excedida após {max_retries} tentativas. "
                            f"Aguarde 60 segundos e tente novamente."
                        )
                        self.connected = False
                        return False
                else:
                    logger.error(f"APIError ao conectar Google Sheets: {e}")
                    logger.error(traceback.format_exc())
                    self.connected = False
                    return False

            except Exception as e:
                logger.error(f"Erro ao conectar Google Sheets: {e}")
                logger.error(traceback.format_exc())
                self.connected = False
                return False

        logger.error("Falha ao conectar após todas as tentativas")
        self.connected = False
        return False

    # ESTRUTURA DE ABAS

    def _ensure_structure(self):
        """Garante que as abas necessárias existem."""
        try:
            self._ensure_users_sheet()
            self._ensure_categories_sheet()
            self._ensure_tickets_sheet()
            self._ensure_empresas_sheet()
            self._ensure_permissoes_sheet()
            self._ensure_itens_avarias_sheet()
        except Exception as e:
            logger.error(f"Erro ao criar estrutura: {e}")

    def _ensure_itens_avarias_sheet(self):
        """Cria aba Itens_Avarias se não existir e garante headers completos."""
        headers = [
            'avaria_id', 'sequencia', 'codprod', 'descrprod', 'qtdneg',
            'qtd_avariada', 'codlocaldest', 'codlocalorig', 'localizacao',
            'valor_item', 'codgrupoprod', 'codemp',
            'id_etiqueta', 'prefixo_estoque', 'etiqueta_anterior', 'status_etiqueta',
            'observacao_produto', 'link_fotos_drive', 'data_identificacao',
            'data_triagem', 'operador_identificacao', 'operador_triagem'
        ]
        try:
            ws = self.sheet.worksheet("Itens_Avarias")
            logger.info("Aba 'Itens_Avarias' existe")
            self._ensure_headers_complete(ws, headers)
        except Exception as e:
            if "No worksheet found" in str(e) or "worksheet" in str(e).lower():
                try:
                    logger.info("■├ Criando aba 'Itens_Avarias'...")
                    ws = self.sheet.add_worksheet(
                        title="Itens_Avarias",
                        rows=10000,
                        cols=len(headers)
                    )
                    ws.update('A1', [headers])
                    logger.info("Aba 'Itens_Avarias' criada")
                except Exception as create_e:
                    logger.error(f"Erro ao criar aba: {create_e}")
            else:
                logger.error(f"Erro ao verificar aba: {e}")

    def _ensure_users_sheet(self):
        """Cria aba Usuários se não existir, com todos os headers."""
        try:
            users_ws = self.sheet.worksheet("Usuários")
            logger.info("Aba 'Usuários' existe")
            self._ensure_headers_complete(users_ws, [
                "nome", "email", "departamento", "nivel", "ativo",
                "senha", "empresa", "cargo", "admin_escopo",
                "requer_alteracao_senha",
                "data_cadastro", "ultimo_login"
            ])
        except Exception:
            logger.info("Criando aba 'Usuários'...")
            users_ws = self.sheet.add_worksheet(title="Usuários", rows=1000, cols=15)
            headers = [
                "nome", "email", "departamento", "nivel", "ativo",
                "senha", "empresa", "cargo", "admin_escopo",
                "requer_alteracao_senha",
                "data_cadastro", "ultimo_login"
            ]
            users_ws.update('A1', [headers])
            logger.info("Aba 'Usuários' criada")

    def _ensure_categories_sheet(self):
        """Cria aba Categorias se não existir."""
        try:
            self.sheet.worksheet("Categorias")
            logger.info("Aba 'Categorias' existe")
        except Exception:
            logger.info("Criando aba 'Categorias'...")
            cats_ws = self.sheet.add_worksheet(title="Categorias", rows=1000, cols=10)
            headers = ["categoria", "descricao", "email_terceiro", "requer_gestor", "sla_horas", "ativo", "cor"]
            cats_ws.update('A1:G1', [headers])
            logger.info("Aba 'Categorias' criada")

    def _ensure_tickets_sheet(self):
        """Cria aba Tickets se não existir."""
        try:
            self.sheet.worksheet("Tickets")
            logger.info("Aba 'Tickets' existe")
        except Exception:
            logger.info("Criando aba 'Tickets'...")
            tickets_ws = self.sheet.add_worksheet(title="Tickets", rows=10000, cols=15)
            headers = [
                "ticket_number", "user_email", "user_name", "department",
                "category", "subject", "description", "priority", "status",
                "third_party_email", "created_at", "updated_at", "closed_at",
                "email_opened", "created_by"
            ]
            tickets_ws.update('A1:O1', [headers])
            logger.info("Aba 'Tickets' criada")

    def _ensure_empresas_sheet(self):
        """Cria aba Empresas se não existir."""
        try:
            self.sheet.worksheet("Empresas")
            logger.info("Aba 'Empresas' existe")
        except Exception:
            logger.info("Criando aba 'Empresas'...")
            ws = self.sheet.add_worksheet(title="Empresas", rows=100, cols=2)
            ws.update('A1:B1', [["empresa", "modulos_permitidos"]])
            ws.update('A2:B4', [
                ["Matriz", "helpdesk,wms"],
                ["Empresax", "wms"],
                ["Thomas", "helpdesk"]
            ])
            logger.info("Aba 'Empresas' criada")

    def _ensure_permissoes_sheet(self):
        """Cria aba Permissoes se não existir."""
        try:
            self.sheet.worksheet("Permissoes")
            logger.info("Aba 'Permissoes' existe")
        except Exception:
            logger.info("Criando aba 'Permissoes'...")
            ws = self.sheet.add_worksheet(title="Permissoes", rows=500, cols=4)
            ws.update('A1:D1', [["nivel", "modulo", "tela", "permitido"]])
            example_data = [
                ['admin', 'helpdesk', '*', 'sim'],
                ['admin', 'wms', '*', 'sim'],
                ['gestor', 'helpdesk', 'dashboard', 'sim'],
                ['gestor', 'helpdesk', 'list_tickets_all', 'sim'],
                ['gestor', 'helpdesk', 'new_ticket', 'sim'],
                ['gestor', 'helpdesk', 'view_ticket', 'sim'],
                ['gestor', 'wms', 'dashboard', 'sim'],
                ['usuario', 'helpdesk', 'dashboard', 'sim'],
                ['usuario', 'helpdesk', 'list_tickets_own', 'sim'],
                ['usuario', 'helpdesk', 'new_ticket', 'sim'],
                ['usuario', 'helpdesk', 'view_ticket', 'sim'],
                ['usuario', 'wms', 'avarias_dashboard', 'sim']
            ]
            ws.update('A2:D13', example_data)
            logger.info("Aba 'Permissoes' criada")

    # EMPRESAS

    def add_empresa(self, nome_empresa: str, modulos: list) -> tuple:
        """Cria uma nova empresa na aba 'Empresas'."""
        if not self.connected:
            return False, "Google Sheets não conectado"
        try:
            ws = self.sheet.worksheet("Empresas")
            modulos_str = ",".join(m.strip() for m in modulos if m.strip())
            ws.append_row([nome_empresa, modulos_str])
            self.company_permissions[nome_empresa] = [m.strip() for m in modulos if m.strip()]
            logger.info(f"Empresa '{nome_empresa}' adicionada com módulos: {modulos}")
            return True, f"Empresa '{nome_empresa}' criada com sucesso"
        except Exception as e:
            logger.error(f"Erro ao adicionar empresa: {e}")
            return False, str(e)

    def update_empresa_modulos(self, nome_empresa: str, modulos: list) -> tuple:
        """Atualiza os módulos de uma empresa existente na aba 'Empresas'."""
        if not self.connected:
            return False, "Google Sheets não conectado"
        try:
            ws = self.sheet.worksheet("Empresas")
            records = ws.get_all_records()
            headers = ws.row_values(1)

            idx_empresa = headers.index('empresa') + 1
            idx_modulos = headers.index('modulos_permitidos') + 1

            for i, row in enumerate(records, start=2):
                if row.get('empresa', '').strip() == nome_empresa:
                    modulos_str = ",".join(m.strip() for m in modulos if m.strip())
                    ws.update_cell(i, idx_modulos, modulos_str)
                    self.company_permissions[nome_empresa] = [m.strip() for m in modulos if m.strip()]
                    logger.info(f"Módulos de '{nome_empresa}' atualizados: {modulos}")
                    return True, f"Empresa '{nome_empresa}' atualizada"

            return False, f"Empresa '{nome_empresa}' não encontrada na planilha"
        except Exception as e:
            logger.error(f"Erro ao atualizar empresa: {e}")
            return False, str(e)

    # OPERAÇÕES DE LEITURA E ESCRITA

    def get_all_records(self, sheet_name: str) -> List[Dict]:
        """
        Retorna todos os registros de uma aba como lista de dicionários.

        ⚡ Circuit Breaker: se o Sheets estiver com 3+ falhas consecutivas,
        o circuito abre e retorna [] imediatamente sem chamar a API.
        O circuito tenta se recuperar automaticamente após 60s.

        Retry com backoff exponencial para erros 429.
        """
        if not self.connected:
            logger.warning(f"Sheets desconectado, falha ao buscar '{sheet_name}'")
            return []

        if not self._breaker.call_allowed():
            logger.warning(
                f"🔴 [CircuitBreaker] ABERTO — '{sheet_name}' bloqueado, "
                f"retornando [] sem chamar a API. Status: {self._breaker.status()['seconds_until_half_open']}s até próxima tentativa"
            )
            return []

        max_retries = 3
        base_delay = 2

        for tentativa in range(max_retries):
            try:
                ws = self.sheet.worksheet(sheet_name)
                data = ws.get_all_records()
                self._breaker.record_success()
                return data

            except APIError as e:
                is_quota = '429' in str(e) or 'Quota exceeded' in str(e)

                if is_quota:
                    self._breaker.record_failure()

                    if tentativa < max_retries - 1:
                        delay = base_delay * (2 ** tentativa)
                        logger.warning(
                            f"⚠️ [Quota] Tentativa {tentativa + 1}/{max_retries} "
                            f"em '{sheet_name}'. Aguardando {delay}s... "
                            f"Breaker: {self._breaker.state.value}"
                        )
                        time.sleep(delay)
                        continue
                    else:
                        logger.error(
                            f"❌ Quota excedida após {max_retries} tentativas "
                            f"em '{sheet_name}'. Breaker: {self._breaker.state.value}"
                        )
                        return []
                else:
                    raise

            except Exception as e:
                logger.error(f"Erro ao buscar '{sheet_name}': {e}")
                return []

        return []
    def append_row(self, sheet_name: str, row_data: List[Any], value_input_option: str = 'RAW') -> Tuple[bool, str]:
        """Adiciona uma nova linha a uma aba específica (usado pelo WMS)."""
        if not self.connected:
            return False, "Google Sheets não conectado"

        try:
            ws = self.sheet.worksheet(sheet_name)
            self._write_with_retry(ws.append_row, row_data, value_input_option=value_input_option)
            logger.debug(f"Linha adicionada à aba '{sheet_name}' with option {value_input_option}")
            return True, "Linha adicionada com sucesso"
        except Exception as e:
            logger.error(f"Erro ao adicionar linha à aba {sheet_name}: {e}")
            logger.error(traceback.format_exc())
            return False, str(e)

    def batch_append_rows(self, sheet_name: str, rows_data: list) -> Tuple[bool, str]:
        """
        ⚡ Adiciona múltiplas linhas em UMA ÚNICA chamada API.
        ANTES: N itens = N chamadas (N × 2-3s). DEPOIS: 1 chamada (2-3s total).
        """
        if not self.connected:
            return False, "Google Sheets não conectado"

        if not rows_data:
            return True, "Nenhuma linha para adicionar"

        try:
            ws = self.sheet.worksheet(sheet_name)
            ws.append_rows(rows_data, value_input_option='RAW')
            logger.info(f"{len(rows_data)} linhas adicionadas à '{sheet_name}' em batch")
            return True, f"{len(rows_data)} linhas adicionadas"

        except Exception as e:
            logger.error(f"Erro batch_append_rows '{sheet_name}': {e}")
            return False, str(e)

    def batch_update_cells(self, sheet_name: str, updates: list) -> tuple:
        """
        ⚡ Atualiza múltiplas células em UMA ÚNICA chamada API.
        ANTES: N campos = N chamadas. DEPOIS: 1 chamada batch.

        Args:
            sheet_name: Nome da aba.
            updates: Lista de dicts {'range': 'A2', 'values': [[valor]]}.

        Returns:
            Tuple[bool, str]: (sucesso, mensagem).
        """
        if not self.connected:
            return False, "Google Sheets não conectado"

        if not updates:
            return True, "Nenhuma atualização para aplicar"

        try:
            ws = self.sheet.worksheet(sheet_name)
            ws.batch_update(updates, value_input_option='RAW')
            logger.info(f"{len(updates)} células atualizadas em batch na aba '{sheet_name}'")
            return True, f"{len(updates)} campos atualizados"

        except Exception as e:
            logger.error(f"Erro no batch_update_cells para '{sheet_name}': {e}")
            logger.error(traceback.format_exc())
            return False, str(e)

    def _write_with_retry(self, write_fn, *args, max_retries=4, base_delay=2, **kwargs):
        """
        Executa uma função de escrita com retry exponencial para 429.
        Integrado ao Circuit Breaker: escritas contribuem para abertura do circuito.
        """
        if not self._breaker.call_allowed():
            fn_name = getattr(write_fn, '__name__', str(write_fn))
            raise Exception(
                f"[CircuitBreaker] ABERTO — escrita '{fn_name}' bloqueada. "
                f"Tente novamente em {self._breaker.status().get('seconds_until_half_open', '?')}s"
            )

        for tentativa in range(max_retries):
            try:
                result = write_fn(*args, **kwargs)
                self._breaker.record_success()
                return result

            except APIError as e:
                is_quota = '429' in str(e) or 'Quota exceeded' in str(e)

                if is_quota:
                    self._breaker.record_failure()

                    if tentativa < max_retries - 1:
                        delay = base_delay * (2 ** tentativa)
                        logger.warning(
                            f"⚠️ [Write 429] Tentativa {tentativa + 1}/{max_retries}. "
                            f"Aguardando {delay}s... Breaker: {self._breaker.state.value}"
                        )
                        time.sleep(delay)
                    else:
                        raise
                else:
                    raise

        raise Exception(f"Escrita falhou após {max_retries} tentativas.")

    # MONITORAMENTO

    def test_connection(self) -> dict:
        """Testa conexão e permissões de escrita."""
        result = {
            'connected': self.connected,
            'credentials_available': bool(self.credentials_b64),
            'spreadsheet_id_available': bool(self.spreadsheet_id),
            'message': 'Conectado' if self.connected else 'Desconectado',
            'write_permissions': False
        }

        if self.connected:
            try:
                users_ws = self.sheet.worksheet("Usuários")
                test_cell = 'K1'
                users_ws.update(test_cell, [['TEST']])
                users_ws.update(test_cell, [['']])
                result['write_permissions'] = True
                result['message'] = 'Conectado com permissões de escrita'
                logger.info("Permissões de ESCRITA confirmadas")
            except Exception as e:
                logger.error(f"SEM permissões de escrita: {e}")
                result['message'] = 'Conectado mas sem permissão de escrita'

        return result

    def get_breaker_status(self) -> dict:
        """
        Retorna status do circuit breaker para monitoramento e health checks.

        Exemplo de uso em rota Flask:
            @app.route('/health/sheets')
            def health_sheets():
                return jsonify(sheets_service.get_breaker_status())
        """
        return self._breaker.status()

    # HELPERS INTERNOS

    def _get_worksheet(self, sheet_name: str):
        """Retorna Worksheet por nome ou None se não encontrada/sem conexão."""
        if not self.connected or self.sheet is None:
            self.logger.warning(f"_get_worksheet('{sheet_name}'): sem conexão")
            return None
        try:
            return self.sheet.worksheet(sheet_name)
        except Exception as e:
            self.logger.warning(f"_get_worksheet('{sheet_name}'): {e}")
            return None

    def _get_sheet_data(self, sheet_name: str) -> list:
        """Retorna get_all_records() de uma aba ou [] se indisponível."""
        ws = self._get_worksheet(sheet_name)
        if ws is None:
            return []
        try:
            return ws.get_all_records()
        except Exception as e:
            self.logger.warning(f"_get_sheet_data('{sheet_name}'): {e}")
            return []

    def _get_sheet_data_cached(self, sheet_name: str, ttl: int = None) -> list:
        """Versão com cache do _get_sheet_data. Use para abas que mudam raramente."""
        ttl = ttl or self._generic_cache_ttl
        now = time.time()

        if sheet_name in self._generic_cache:
            data, ts = self._generic_cache[sheet_name]
            if (now - ts) < ttl:
                logger.debug(f"Cache hit genérico: '{sheet_name}'")
                return data

        data = self._get_sheet_data(sheet_name)
        self._generic_cache[sheet_name] = (data, now)
        logger.info(f"Cache genérico atualizado: '{sheet_name}' ({len(data)} registros)")
        return data

    def _invalidate_cache(self, cache_type: str):
        """
        Invalida cache por tipo.
        Aceita: 'departamentos', 'cargos', 'usuarios', 'permissoes', 'wms', 'all'.
        """
        if cache_type in ('departamentos', 'cargos', 'usuarios', 'all'):
            self.invalidate_users_cache()
        if cache_type in ('permissoes', 'all'):
            self.invalidate_permissions_cache()
        if cache_type in ('wms', 'all'):
            if hasattr(self, 'invalidate_wms_cache'):
                self.invalidate_wms_cache()
        if hasattr(self, '_matrix_cache'):
            self._matrix_cache.clear()
        self.logger.debug(f"Cache invalidado: tipo='{cache_type}'")

    def _col_to_letter(self, col: int) -> str:
        """Converte índice de coluna (1-based) para notação A1 (Ex: 1→A, 27→AA)."""
        letter = ''
        while col > 0:
            col, remainder = divmod(col - 1, 26)
            letter = chr(65 + remainder) + letter
        return letter

    def _ensure_headers_complete(self, ws, required_headers: List[str]) -> bool:
        """Verifica e adiciona headers ausentes em um worksheet."""
        if not self.connected:
            return False

        try:
            current_headers = ws.row_values(1)
            current_headers_clean = [h.strip() for h in current_headers if h.strip()]
            missing_headers = [h for h in required_headers if h not in current_headers_clean]

            if missing_headers:
                logger.warning(f"Aba '{ws.title}' incompleta. Faltando colunas: {missing_headers}")

                col_start_index = len(current_headers_clean) + 1
                col_end_index = col_start_index + len(missing_headers) - 1
                range_to_update = (
                    f'{self._col_to_letter(col_start_index)}1:'
                    f'{self._col_to_letter(col_end_index)}1'
                )

                ws.update(range_to_update, [missing_headers], value_input_option='RAW')
                logger.info(f"{len(missing_headers)} headers adicionados na linha 1: {missing_headers}")
                return True

            return False

        except Exception as e:
            logger.error(f"Erro ao garantir cabeçalhos para {ws.title}: {e}")
            return False
