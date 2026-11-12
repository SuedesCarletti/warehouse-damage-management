"""
sheets_permissions_mixin.py
────────────────────────────────────────────────────────────────
Responsabilidade: Carregamento e resolução de permissões, matrix por
empresa, telas customizadas e bulk-load via batch API.

Métodos públicos:
  load_permissions()
  get_user_permissions(user)
  get_permissions_matrix(empresa, tipo)
  get_permissions_combos(empresa)
  save_permissions_matrix(empresa, permissoes, apenas_inicializar)
  criar_combo_permissao(empresa, departamento, cargo)
  remover_combo_permissao(empresa, departamento, cargo)
  get_telas_modulo(empresa)
  get_telas_customizadas(empresa)
  salvar_tela_customizada(empresa, modulo, tela, descricao)
  remover_tela_customizada(empresa, modulo, tela)
  ensure_permissao(empresa, modulo, permissao, nivel_minimo)
  load_all_data_batch()

Métodos internos:
  _load_fallback_permissions()
  _load_permissions_matrix_cached(empresa)
  _fetch_permissions_matrix(empresa)
  _limpar_permissoes_tela(empresa, modulo, tela)
  _get_or_create_worksheet(nome, headers)
  _parse_batch_result(resultado_raw, abas)
  _batch_populate_permissions(empresas_data, permissoes_data)
  _batch_populate_users(usuarios_data)
  _batch_populate_telas(telas_data)
  _batch_populate_matrix(permissoes_data)
"""

import logging
import traceback
import time
import random
from typing import List, Tuple, Optional, Dict

from gspread.exceptions import APIError

logger = logging.getLogger(__name__)

# Importado aqui para uso em TELAS_PADRAO_POR_MODULO dentro do mixin
# (a constante global fica no núcleo; a referência abaixo é lazy via self)


def _deep_merge_perms(base: dict, overlay: dict) -> None:
    """Merge overlay into base in-place. overlay wins for each specified screen."""
    for modulo, telas in overlay.items():
        if not isinstance(telas, dict):
            continue
        if modulo not in base:
            base[modulo] = {}
        for tela, nivel_acesso in telas.items():
            if nivel_acesso:  # Only set if truthy (not empty string / False)
                base[modulo][tela] = nivel_acesso
            elif tela in base[modulo]:
                del base[modulo][tela]  # Explicit removal (nivel_acesso = '' or False)


class SheetsPermissionsMixin:
    """Permissões, matrix de perfis, telas customizadas e batch load."""

    # LOAD DE PERMISSÕES

    def load_permissions(self):
        """
        Carrega as permissões com cache de 15 minutos.
        VERSÃO CORRIGIDA - Lê campos corretos e monta estrutura correta.

        ⚠️ CORREÇÃO CRÍTICA: Adicionado retry/backoff para leituras de Sheets (mitigar 429).
        """

        # Verificar cache
        if self._permissions_cache and self._cache_timestamp:
            elapsed = time.time() - self._cache_timestamp
            if elapsed < self._cache_ttl:
                logger.info(f"Usando cache de permissões ({int(self._cache_ttl - elapsed)}s restantes)")
                return

        if not self.connected:
            logger.warning(" Carregando permissões em modo fallback (sem conexão)")
            self._load_fallback_permissions()
            return

        def fetch_sheet_with_retry(sheet_name, max_retries=5, base_delay=1):
            ws = self.sheet.worksheet(sheet_name)
            for tentativa in range(max_retries):
                try:
                    if tentativa == 0 and self._cache_timestamp is None:
                        time.sleep(random.uniform(0.1, 0.8))
                    return ws.get_all_records()
                except APIError as e:
                    if '429' in str(e) or 'Quota exceeded' in str(e):
                        if tentativa < max_retries - 1:
                            delay = base_delay * (2 ** tentativa)
                            logger.warning(
                                f"⚠️  [Quota] Tentativa {tentativa + 1}/{max_retries} falhou ao ler '{sheet_name}'. "
                                f"Aguardando {delay:.2f}s..."
                            )
                            time.sleep(delay)
                            continue
                        else:
                            raise
                    else:
                        raise
            raise Exception(f"Falha ao ler '{sheet_name}' após {max_retries} tentativas.")

        try:
            logger.info("↺ Carregando permissões do Google Sheets...")

            # PASSO 1: Carregar empresas
            empresas_data = fetch_sheet_with_retry("Empresas")
            self.company_permissions = {
                row['empresa']: [m.strip() for m in row.get('modulos_permitidos', '').split(',') if m.strip()]
                for row in empresas_data
            }
            logger.info(f"   {len(self.company_permissions)} empresa(s) carregada(s)")
            for empresa, modulos in self.company_permissions.items():
                logger.info(f"      • {empresa}: {modulos}")

            # PASSO 2: Carregar permissões de níveis
            permissoes_data = fetch_sheet_with_retry("Permissoes")
            self.role_permissions = {}

            for row in permissoes_data:
                nivel    = row.get('nivel', '').strip().lower()
                modulo   = row.get('modulo', '').strip()
                tela     = row.get('tela', '').strip()
                permitido = row.get('permitido', '').strip().lower() == 'sim'

                if not nivel or not modulo or not tela:
                    continue
                if not permitido:
                    continue

                if nivel not in self.role_permissions:
                    self.role_permissions[nivel] = {}
                if modulo not in self.role_permissions[nivel]:
                    self.role_permissions[nivel][modulo] = {}

                self.role_permissions[nivel][modulo][tela] = True

            logger.info(f"   {len(self.role_permissions)} nível(is) carregado(s)")
            for nivel, modulos in self.role_permissions.items():
                telas_count = sum(len(telas) for telas in modulos.values())
                logger.info(f"      • {nivel}: {list(modulos.keys())} ({telas_count} telas)")

            # Atualizar cache
            self._permissions_cache = {
                'company': self.company_permissions.copy(),
                'role': self.role_permissions.copy()
            }
            self._cache_timestamp = time.time()

            logger.info(f"Permissões carregadas e cacheadas por {self._cache_ttl}s")

        except Exception as e:
            logger.error(f"Erro ao carregar permissões do Google Sheets: {e}")
            logger.error(traceback.format_exc())
            self._load_fallback_permissions()

    def _load_fallback_permissions(self):
        """Carrega permissões padrão quando Google Sheets não disponível."""
        logger.info("Carregando permissões de fallback")

        self.company_permissions = {
            'Matriz': ['helpdesk', 'wms'],
            'Empresax': ['wms'],
            'Thomas': ['helpdesk']
        }

        self.role_permissions = {
            'admin': {
                'helpdesk': {'*': True},
                'wms': {'*': True}
            },
            'gestor': {
                'helpdesk': {
                    'dashboard': True,
                    'list_tickets_all': True,
                    'new_ticket': True,
                    'view_ticket': True
                },
                'wms': {
                    'dashboard': True
                }
            },
            'usuario': {
                'helpdesk': {
                    'dashboard': True,
                    'list_tickets_own': True,
                    'new_ticket': True,
                    'view_ticket': True
                },
                'wms': {
                    'avarias_dashboard': True
                }
            }
        }

        logger.info("Permissões de fallback carregadas")

    # RESOLUÇÃO DE PERMISSÕES POR USUÁRIO

    def get_user_permissions(self, user) -> dict:
        """Resolve permissões efetivas de um usuário com base em empresa + perfil."""
        try:
            logger.info(f"[Permissões] Resolvendo para {user.email} "
                        f"(nivel={user.nivel}, cargo={user.cargo}, depto={user.departamento})")

            # ── Admin levels têm acesso total ao seu escopo ───────────────────
            if user.nivel in ('admin_global', 'admin_empresa', 'admin_depto', 'admin'):
                empresa_modulos = self.company_permissions.get(user.empresa, [])
                telas_admin = {'*': True}
                return {
                    'modules': empresa_modulos,
                    'screens': {m: telas_admin.copy() for m in empresa_modulos}
                }

            # ── Módulos da empresa ─────────────────────────────────────────────
            empresa_modulos = self.company_permissions.get(user.empresa, [])
            if not empresa_modulos:
                # FALLBACK TRANSPORTADORA: lê direto do role_permissions
                if user.nivel == 'transportadora':
                    trans_perms = self.role_permissions.get('transportadora', {})
                    if trans_perms:
                        modules = list(trans_perms.keys())
                        logger.info(f"[Permissões] Transportadora: fallback direto → {modules}")
                        return {'modules': modules, 'screens': trans_perms}
                logger.warning(f"[Permissões] Empresa '{user.empresa}' sem módulos")
                return {'modules': [], 'screens': {}}

            # ── Carrega matrix de permissões da empresa ───────────────────────
            matrix = self._load_permissions_matrix_cached(user.empresa)

            # Build merged permissions via cascade (lowest priority first)
            merged = {}

            # 1. Nível base
            nivel_key = user.nivel
            nivel_perms = matrix.get('nivel', {}).get(nivel_key, {})
            if not nivel_perms and nivel_key in self.role_permissions:
                nivel_perms = self.role_permissions[nivel_key]
            _deep_merge_perms(merged, nivel_perms)

            # 2. Departamento overlay
            if user.departamento:
                depto_perms = matrix.get('departamento', {}).get(user.departamento, {})
                _deep_merge_perms(merged, depto_perms)

            # 3. Cargo overlay
            if user.cargo:
                cargo_perms = matrix.get('cargo', {}).get(user.cargo, {})
                _deep_merge_perms(merged, cargo_perms)

            # 4. Combo overlay (departamento::cargo)
            combo_key = f"{user.departamento}::{user.cargo}" if user.departamento and user.cargo else None
            if combo_key and combo_key in matrix.get('combo', {}):
                combo_perms = matrix['combo'][combo_key]
                _deep_merge_perms(merged, combo_perms)

            # 5. Exceção de usuário (highest priority - only specified screens)
            if user.email in matrix.get('usuario', {}):
                excecao_perms = matrix['usuario'][user.email]
                _deep_merge_perms(merged, excecao_perms)
                logger.info(f"[Permissões] Exceção de usuário aplicada como override sobre herança: {user.email}")

            screens_resolvidas = merged if merged else None

            if screens_resolvidas is None:
                logger.warning(f"[Permissões] Nenhuma regra encontrada para {user.email}")
                return {'modules': [], 'screens': {}}

            # ── Intersecta com módulos permitidos da empresa ──────────────────
            user_modules = []
            user_screens = {}

            for modulo in empresa_modulos:
                if modulo in screens_resolvidas:
                    telas = screens_resolvidas[modulo]
                    if telas:
                        user_modules.append(modulo)
                        user_screens[modulo] = telas
                        logger.info(f"[Permissões] {modulo}: {list(telas.keys())}")
                else:
                    logger.info(f"[Permissões] {modulo}: sem telas permitidas")

            logger.info(f"[Permissões] RESULTADO: {user_modules}")
            return {'modules': user_modules, 'screens': user_screens}

        except Exception as e:
            logger.error(f"[Permissões] Erro ao resolver para {user.email}: {e}")
            logger.error(traceback.format_exc())
            return {'modules': [], 'screens': {}}

    def get_user_permissions_detail(self, user) -> dict:
        """
        Retorna o detalhamento das permissões efetivas de um usuário, mostrando
        cada camada da cascata separadamente.

        Retorna:
        {
            'base': {},      # from nivel only
            'herdado': {},   # after nivel + depto + cargo + combo merge
            'excecoes': {},  # raw user exceptions
            'efetivo': {},   # final merged result (what the user actually gets)
        }
        """
        try:
            matrix = self._load_permissions_matrix_cached(user.empresa)

            # 1. Base: apenas nível
            base = {}
            nivel_key = user.nivel
            nivel_perms = matrix.get('nivel', {}).get(nivel_key, {})
            if not nivel_perms and nivel_key in self.role_permissions:
                nivel_perms = self.role_permissions[nivel_key]
            _deep_merge_perms(base, nivel_perms)

            # 2. Herdado: nivel + depto + cargo + combo
            herdado = {}
            _deep_merge_perms(herdado, base)

            if user.departamento:
                depto_perms = matrix.get('departamento', {}).get(user.departamento, {})
                _deep_merge_perms(herdado, depto_perms)

            if user.cargo:
                cargo_perms = matrix.get('cargo', {}).get(user.cargo, {})
                _deep_merge_perms(herdado, cargo_perms)

            combo_key = f"{user.departamento}::{user.cargo}" if user.departamento and user.cargo else None
            if combo_key and combo_key in matrix.get('combo', {}):
                combo_perms = matrix['combo'][combo_key]
                _deep_merge_perms(herdado, combo_perms)

            # 3. Exceções brutas do usuário
            excecoes = matrix.get('usuario', {}).get(user.email, {})

            # 4. Efetivo: herdado + exceções
            efetivo = {}
            _deep_merge_perms(efetivo, herdado)
            _deep_merge_perms(efetivo, excecoes)

            return {
                'base': base,
                'herdado': herdado,
                'excecoes': excecoes,
                'efetivo': efetivo,
            }
        except Exception as e:
            logger.error(f"[Permissões] get_user_permissions_detail({user.email}): {e}")
            logger.error(traceback.format_exc())
            return {'base': {}, 'herdado': {}, 'excecoes': {}, 'efetivo': {}}

    # MATRIX DE PERMISSÕES POR EMPRESA

    def _load_permissions_matrix_cached(self, empresa: str) -> dict:
        """
        Carrega a matrix de permissões da empresa com cache (5 min).
        Estrutura retornada:
        {
          'nivel':        {'gestor':   {'helpdesk': {'dashboard': True}}},
          'departamento': {'TI':       {'wms':      {'avarias_dashboard': True}}},
          'cargo':        {'Analista': {'helpdesk': {'list_tickets_all': True}}},
          'combo':        {'TI::Analista': {'wms':  {'parametrizacao': True}}}
        }
        """
        cache_key = f'perm_matrix_{empresa}'

        if not hasattr(self, '_matrix_cache'):
            self._matrix_cache = {}

        cached = self._matrix_cache.get(cache_key)
        if cached and (time.time() - cached['ts']) < 300:
            return cached['data']

        data = self._fetch_permissions_matrix(empresa)
        self._matrix_cache[cache_key] = {'data': data, 'ts': time.time()}
        return data

    def _fetch_permissions_matrix(self, empresa: str) -> dict:
        """Lê a aba Permissoes e monta a matrix para a empresa."""
        matrix = {'nivel': {}, 'departamento': {}, 'cargo': {}, 'combo': {}, 'usuario': {}}

        try:
            rows = self._get_sheet_data('Permissoes')
            for row in rows:
                if row.get('empresa', '').strip().lower() != empresa.lower():
                    continue

                tipo     = row.get('tipo', 'nivel').strip().lower()
                perfil   = row.get('perfil', '').strip()
                modulo   = row.get('modulo', '').strip()
                tela     = row.get('tela', '').strip()
                perm_str = row.get('permitido', 'nao').strip().lower()
                permitido = perm_str in ('sim', 'yes', 'true', '1')

                if not all([tipo, perfil, modulo, tela]):
                    continue
                if not permitido:
                    continue
                if tipo not in matrix:
                    continue

                if perfil not in matrix[tipo]:
                    matrix[tipo][perfil] = {}
                if modulo not in matrix[tipo][perfil]:
                    matrix[tipo][perfil][modulo] = {}

                nivel_acesso = row.get('nivel_acesso', '').strip().lower() or 'editar'
                if nivel_acesso not in ('visualizar', 'editar', 'admin'):
                    nivel_acesso = 'editar'
                matrix[tipo][perfil][modulo][tela] = nivel_acesso

        except Exception as e:
            logger.error(f"_fetch_permissions_matrix({empresa}): {e}")

        return matrix

    def get_permissions_matrix(self, empresa: str, tipo: str) -> dict:
        """
        Retorna a sub-matrix de um tipo específico para a UI.
        Ex: tipo='cargo' → {'Analista': {'helpdesk': {'dashboard': True}}}
        """
        full = self._load_permissions_matrix_cached(empresa)
        return full.get(tipo, {})

    def get_permissions_combos(self, empresa: str) -> list:
        """
        Retorna lista de combos configurados para a UI.
        Ex: [{'id': 'TI::Analista', 'departamento': 'TI', 'cargo': 'Analista', 'matrix': {...}}]
        """
        full = self._load_permissions_matrix_cached(empresa)
        combos_matrix = full.get('combo', {})

        combo_keys_all = set(combos_matrix.keys())
        try:
            rows = self._get_sheet_data('Permissoes')
            for row in rows:
                if row.get('empresa', '').strip().lower() != empresa.lower():
                    continue
                if row.get('tipo', '').strip().lower() == 'combo':
                    perfil = row.get('perfil', '').strip()
                    if '::' in perfil and not perfil.startswith('_'):
                        combo_keys_all.add(perfil)
        except Exception as e:
            logger.warning(f"[Permissoes] get_permissions_combos fallback: {e}")

        result = []
        for combo_key in sorted(combo_keys_all):
            parts = combo_key.split('::')
            if len(parts) != 2:
                continue
            departamento, cargo = parts[0].strip(), parts[1].strip()
            if not departamento or not cargo:
                continue
            result.append({
                'id': combo_key,
                'departamento': departamento,
                'cargo': cargo,
                'matrix': {combo_key: combos_matrix.get(combo_key, {})}
            })

        return result

    # CRUD DE COMBOS

    def criar_combo_permissao(self, empresa: str, departamento: str, cargo: str) -> tuple:
        """Cria entrada inicial (vazia) para um combo cargo+departamento."""
        try:
            combo_key = f"{departamento}::{cargo}"
            full = self._load_permissions_matrix_cached(empresa)

            if combo_key in full.get('combo', {}):
                return False, f"Combinação '{combo_key}' já existe"

            sheet = self._get_worksheet('Permissoes')
            if sheet is None:
                return False, "Aba 'Permissoes' não encontrada"

            sheet.append_row([empresa, 'combo', combo_key, '_init', '_init', 'nao'])

            self.invalidate_permissions_cache()
            self.invalidate_users_cache()
            if hasattr(self, '_matrix_cache'):
                self._matrix_cache.pop(f'perm_matrix_{empresa}', None)

            return True, f"Combinação '{cargo}' + '{departamento}' criada"
        except Exception as e:
            return False, str(e)

    def remover_combo_permissao(self, empresa: str, departamento: str, cargo: str) -> tuple:
        """Remove todas as permissões de um combo cargo+departamento."""
        try:
            combo_key = f"{departamento}::{cargo}"
            sheet = self._get_worksheet('Permissoes')
            if sheet is None:
                return False, "Aba 'Permissoes' não encontrada"

            all_rows = sheet.get_all_records()
            to_delete = []
            for i, row in enumerate(all_rows, start=2):
                if (row.get('empresa', '').strip().lower() == empresa.lower() and
                        row.get('tipo', '').strip() == 'combo' and
                        row.get('perfil', '').strip() == combo_key):
                    to_delete.append(i)

            for i in reversed(to_delete):
                sheet.delete_rows(i)

            self.invalidate_permissions_cache()
            self.invalidate_users_cache()
            if hasattr(self, '_matrix_cache'):
                self._matrix_cache.pop(f'perm_matrix_{empresa}', None)

            return True, f"Combinação '{combo_key}' removida ({len(to_delete)} linhas)"
        except Exception as e:
            return False, str(e)

    def get_user_excecoes(self, empresa: str, email: str) -> dict:
        """Retorna as exceções de permissão configuradas para um usuário específico."""
        full = self._load_permissions_matrix_cached(empresa)
        return full.get('usuario', {}).get(email, {})

    def save_user_excecao(self, empresa: str, email: str, permissoes: dict) -> tuple:
        """
        Salva exceções de permissão para um usuário específico.
        Substitui todas as exceções anteriores deste usuário.
        permissoes = {'helpdesk': {'dashboard': 'editar', ...}, ...}
        """
        try:
            sheet = self._get_worksheet('Permissoes')
            if sheet is None:
                return False, "Aba 'Permissoes' não encontrada"

            all_rows = sheet.get_all_records()
            to_delete = []
            for i, row in enumerate(all_rows, start=2):
                if (row.get('empresa', '').strip().lower() == empresa.lower() and
                        row.get('tipo', '').strip() == 'usuario' and
                        row.get('perfil', '').strip() == email):
                    to_delete.append(i)
            for i in reversed(to_delete):
                sheet.delete_rows(i)

            novas_linhas = []
            for modulo, telas in permissoes.items():
                if not isinstance(telas, dict):
                    continue
                for tela, valor in telas.items():
                    if valor:
                        nivel_acesso = valor if isinstance(valor, str) and valor in ('visualizar', 'editar', 'admin') else 'editar'
                        novas_linhas.append([empresa, 'usuario', email, modulo, tela, 'sim', nivel_acesso])

            if novas_linhas:
                sheet.append_rows(novas_linhas)

            self.invalidate_permissions_cache()
            self.invalidate_users_cache()
            if hasattr(self, '_matrix_cache'):
                self._matrix_cache.pop(f'perm_matrix_{empresa}', None)

            logger.info(f"[Exceções] {email}: {len(novas_linhas)} exceções salvas para {empresa}")
            return True, f"{len(novas_linhas)} exceção(ões) salva(s) para {email}"

        except Exception as e:
            logger.error(f"save_user_excecao: {e}")
            return False, str(e)

    def get_telas_pendentes(self, empresa: str) -> dict:
        """
        Retorna telas de TELAS_PADRAO_POR_MODULO que não têm nenhuma
        configuração de nível na empresa (precisam ser configuradas).
        Retorna: {'helpdesk': ['tela1', 'tela2'], 'wms': [...]}
        """
        try:
            from core.services.google_sheets_service import TELAS_PADRAO_POR_MODULO
            modulos = self.company_permissions.get(empresa, [])
            matrix = self._load_permissions_matrix_cached(empresa)
            nivel_matrix = matrix.get('nivel', {})

            telas_configuradas = {}
            for nivel_data in nivel_matrix.values():
                for modulo, telas in nivel_data.items():
                    if modulo not in telas_configuradas:
                        telas_configuradas[modulo] = set()
                    telas_configuradas[modulo].update(telas.keys())

            pendentes = {}
            for modulo in modulos:
                padrao = TELAS_PADRAO_POR_MODULO.get(modulo, [])
                configuradas = telas_configuradas.get(modulo, set())
                nao_configuradas = [t for t in padrao if t not in configuradas]
                if nao_configuradas:
                    pendentes[modulo] = nao_configuradas

            return pendentes
        except Exception as e:
            logger.error(f"get_telas_pendentes: {e}")
            return {}

    def save_permissions_matrix(self, empresa: str, permissoes: dict,
                                apenas_inicializar: bool = False) -> tuple:
        """
        Persiste a matrix de permissões na aba 'Permissoes'.

        permissoes = {
          'nivel':        {'gestor':   {'helpdesk': {'dashboard': True, ...}}},
          'departamento': {'TI':       {'wms':      {'avarias_dashboard': True}}},
          'cargo':        {'Analista': ...},
          'combo':        {'TI::Analista': ...}
        }
        """
        try:
            sheet = self._get_worksheet('Permissoes')
            if sheet is None:
                return False, "Aba 'Permissoes' não encontrada"

            if not apenas_inicializar:
                all_rows = sheet.get_all_records()
                rows_to_delete = []
                for i, row in enumerate(all_rows, start=2):
                    if row.get('empresa', '').strip().lower() == empresa.lower():
                        rows_to_delete.append(i)
                for i in reversed(rows_to_delete):
                    sheet.delete_rows(i)

            novas_linhas = []
            for tipo, perfis in permissoes.items():
                if not isinstance(perfis, dict):
                    continue
                for perfil, modulos in perfis.items():
                    if not isinstance(modulos, dict):
                        continue
                    for modulo, telas in modulos.items():
                        if not isinstance(telas, dict):
                            continue
                        for tela, valor in telas.items():
                            if valor:
                                nivel_acesso = valor if isinstance(valor, str) and valor in ('visualizar', 'editar', 'admin') else 'editar'
                                novas_linhas.append([empresa, tipo, perfil, modulo, tela, 'sim', nivel_acesso])

            if novas_linhas:
                sheet.append_rows(novas_linhas)

            self.invalidate_permissions_cache()
            self.invalidate_users_cache()
            cache_key = f'perm_matrix_{empresa}'
            if hasattr(self, '_matrix_cache') and cache_key in self._matrix_cache:
                del self._matrix_cache[cache_key]

            logger.info(f"[Sheets] Salvas {len(novas_linhas)} permissões para {empresa}")
            return True, f"{len(novas_linhas)} permissão(ões) salva(s)"

        except Exception as e:
            logger.error(f"save_permissions_matrix: {e}")
            return False, str(e)


    # TELAS POR MÓDULO

    def get_telas_modulo(self, empresa: str) -> dict:
        """
        Retorna o dicionário completo de telas por módulo APENAS do Google Sheets.
        NÃO USA MAIS TELAS_PADRAO_POR_MODULO.

        Formato retornado:
          {
            'helpdesk': ['dashboard', 'new_ticket', 'relatorio_custom', ...],
            'wms':      ['dashboard', 'avarias_dashboard', 'nova_tela', ...],
          }
        """
        cache_key = f'telas_{empresa}'

        cached = self._telas_cache.get(cache_key)
        if cached and (time.time() - cached['ts']) < self._TELAS_TTL:
            return cached['data']

        resultado = {}

        try:
            rows = self._get_sheet_data('TelasModulos')
            if not rows:
                logger.warning(f"[TelasCustom] Aba 'TelasModulos' vazia para {empresa}")

            for row in rows:
                emp = row.get('empresa', '').strip()
                if emp.lower() not in (empresa.lower(), '*', 'global'):
                    continue
                if row.get('ativo', 'sim').strip().lower() not in ('sim', 'yes', '1', 'true'):
                    continue

                modulo = row.get('modulo', '').strip().lower()
                tela   = row.get('tela', '').strip()

                if not modulo or not tela:
                    continue

                if modulo not in resultado:
                    resultado[modulo] = []

                if tela not in resultado[modulo]:
                    resultado[modulo].append(tela)
                    logger.debug(f"[TelasCustom] +{modulo}/{tela} ({empresa})")

        except Exception as e:
            logger.error(f"[TelasCustom] ERRO ao carregar TelasModulos: {e}")
            resultado = {}

        self._telas_cache[cache_key] = {'data': resultado, 'ts': time.time()}

        logger.info(f"[TelasCustom] {empresa}: {len(resultado)} módulos carregados do Sheets")
        return resultado

    def get_telas_customizadas(self, empresa: str) -> list:
        """
        Retorna apenas as telas customizadas (não as padrão) para a UI de gerenciamento.

        Formato retornado:
          [
            {'empresa': 'Matriz', 'modulo': 'wms', 'tela': 'nova_tela',
             'descricao': 'Descrição', 'ativo': 'sim', '_row': 5},
            ...
          ]
        """
        try:
            rows = self._get_sheet_data('TelasModulos')
            result = []
            for i, row in enumerate(rows, start=2):
                emp = row.get('empresa', '').strip()
                if emp.lower() not in (empresa.lower(), '*', 'global'):
                    continue
                modulo = row.get('modulo', '').strip().lower()
                tela   = row.get('tela', '').strip()
                if not modulo or not tela:
                    continue
                result.append({
                    'empresa':   emp,
                    'modulo':    modulo,
                    'tela':      tela,
                    'descricao': row.get('descricao', '').strip(),
                    'ativo':     row.get('ativo', 'sim').strip().lower(),
                    '_row':      i,
                })
            return result
        except Exception as e:
            logger.warning(f"[TelasCustom] get_telas_customizadas falhou: {e}")
            return []

    def salvar_tela_customizada(self, empresa: str, modulo: str, tela: str,
                                descricao: str = '') -> tuple:
        """Adiciona uma tela customizada à aba 'TelasModulos'."""
        from core.services.google_sheets_service import TELAS_PADRAO_POR_MODULO

        try:
            modulo = modulo.strip().lower()
            tela   = tela.strip().lower().replace(' ', '_')

            if not modulo or not tela:
                return False, 'Módulo e tela são obrigatórios'

            telas_padrao = TELAS_PADRAO_POR_MODULO.get(modulo, [])
            if tela in telas_padrao:
                return False, f'"{tela}" já é uma tela padrão do módulo {modulo}'

            existentes = self.get_telas_customizadas(empresa)
            for ex in existentes:
                if ex['modulo'] == modulo and ex['tela'] == tela:
                    return False, f'Tela "{tela}" já existe no módulo {modulo}'

            ws = self._get_or_create_worksheet(
                'TelasModulos',
                headers=['empresa', 'modulo', 'tela', 'descricao', 'ativo']
            )
            if ws is None:
                return False, "Não foi possível acessar a aba 'TelasModulos'"

            ws.append_row([empresa, modulo, tela, descricao, 'sim'])
            self._telas_cache.pop(f'telas_{empresa}', None)

            logger.info(f"[TelasCustom] +{empresa}/{modulo}/{tela}")
            return True, f'Tela "{tela}" adicionada ao módulo {modulo}'

        except Exception as e:
            logger.error(f"[TelasCustom] salvar_tela_customizada: {e}")
            return False, str(e)

    def remover_tela_customizada(self, empresa: str, modulo: str, tela: str) -> tuple:
        """
        Remove uma tela customizada da aba 'TelasModulos'.
        Também remove todas as permissões que referenciam essa tela.
        """
        from core.services.google_sheets_service import TELAS_PADRAO_POR_MODULO

        try:
            modulo = modulo.strip().lower()
            tela   = tela.strip()

            if tela in TELAS_PADRAO_POR_MODULO.get(modulo, []):
                return False, 'Telas padrão não podem ser removidas aqui'

            ws = self._get_worksheet('TelasModulos')
            if ws is None:
                return False, "Aba 'TelasModulos' não encontrada"

            all_rows = ws.get_all_records()
            to_delete = []
            for i, row in enumerate(all_rows, start=2):
                emp_row = row.get('empresa', '').strip().lower()
                if (emp_row in (empresa.lower(), '*', 'global')
                        and row.get('modulo', '').strip().lower() == modulo
                        and row.get('tela', '').strip() == tela):
                    to_delete.append(i)

            if not to_delete:
                return False, 'Tela não encontrada'

            for i in reversed(to_delete):
                ws.delete_rows(i)

            self._limpar_permissoes_tela(empresa, modulo, tela)
            self._telas_cache.pop(f'telas_{empresa}', None)
            self.invalidate_permissions_cache()
            if hasattr(self, '_matrix_cache'):
                self._matrix_cache.pop(f'perm_matrix_{empresa}', None)

            logger.info(f"[TelasCustom] -{empresa}/{modulo}/{tela}")
            return True, f'Tela "{tela}" removida do módulo {modulo}'

        except Exception as e:
            logger.error(f"[TelasCustom] remover_tela_customizada: {e}")
            return False, str(e)

    def _limpar_permissoes_tela(self, empresa: str, modulo: str, tela: str):
        """Remove permissões da aba 'Permissoes' que referenciam uma tela específica."""
        try:
            ws = self._get_worksheet('Permissoes')
            if ws is None:
                return
            all_rows = ws.get_all_records()
            to_delete = []
            for i, row in enumerate(all_rows, start=2):
                if (row.get('empresa', '').strip().lower() == empresa.lower()
                        and row.get('modulo', '').strip().lower() == modulo
                        and row.get('tela', '').strip() == tela):
                    to_delete.append(i)

            for i in reversed(to_delete):
                ws.delete_rows(i)

            if to_delete:
                logger.info(f"[TelasCustom] Removidas {len(to_delete)} permissões de {modulo}/{tela}")
        except Exception as e:
            logger.warning(f"[TelasCustom] _limpar_permissoes_tela falhou: {e}")

    def _get_or_create_worksheet(self, nome: str, headers: list):
        """
        Retorna o worksheet pelo nome, criando-o (com headers) caso não exista.
        Helper usado por salvar_tela_customizada().
        """
        try:
            try:
                return self.sheet.worksheet(nome)
            except Exception:
                ws = self.sheet.add_worksheet(title=nome, rows=1000, cols=len(headers))
                ws.append_row(headers)
                logger.info(f"[Sheets] Aba '{nome}' criada automaticamente")
                return ws
        except Exception as e:
            logger.error(f"_get_or_create_worksheet({nome}): {e}")
            return None

    # PERMISSÕES AUTOMÁTICAS

    def ensure_permissao(self, empresa: str, modulo: str, permissao: str,
                         nivel_minimo: str = 'usuario') -> bool:
        """
        Garante que uma permissão padrão exista na aba 'Permissoes'.
        Não duplica se já existir.
        Retorna True se inseriu, False se já existia.
        """
        try:
            sheet = self._get_worksheet('Permissoes')
            if sheet is None:
                self.logger.warning("Aba 'Permissoes' não encontrada — criando...")
                spreadsheet = self._get_spreadsheet()
                if spreadsheet:
                    spreadsheet.add_worksheet(title='Permissoes', rows=500, cols=10)
                    sheet = self._get_worksheet('Permissoes')
                    if sheet:
                        sheet.append_row(['empresa', 'modulo', 'permissao', 'nivel_minimo'])

            if sheet is None:
                return False

            rows = sheet.get_all_records()
            for row in rows:
                if (row.get('empresa', '').strip().lower() == empresa.lower() and
                        row.get('modulo', '').strip() == modulo and
                        row.get('permissao', '').strip() == permissao):
                    return False  # Já existe

            sheet.append_row([empresa, modulo, permissao, nivel_minimo])
            self.logger.info(f"Permissão auto-criada: {empresa}/{modulo}/{permissao}")
            return True
        except Exception as e:
            self.logger.error(f"ensure_permissao: {e}")
            return False

    # BATCH LOAD (1 chamada de API para todas as abas)
    
    def load_all_data_batch(self) -> bool:
        """
        Carrega todas as abas auxiliares em UMA ÚNICA chamada à API do Google Sheets.
    
        Substitui as chamadas individuais durante inicialização e keepalive:
          - load_permissions()   → lê Empresas + Permissoes
          - get_all_users()      → lê Usuários
          - get_telas_modulo()   → lê TelasModulos
          - get_departamentos()  → lê Departamentos + Cargos
    
        ⚡ ANTI-STAMPEDE: lock garante que apenas 1 thread faz a chamada.
           Threads concorrentes aguardam e reutilizam o cache populado.
    
        Returns:
            bool: True se o batch foi bem-sucedido, False se caiu em fallback.
        """
        # ── Fast path sem lock (cache quente) ─────────────────────────────
        # 30s de margem: se expira em < 30s, já renova agora de forma controlada
        if (self._permissions_cache and self._cache_timestamp and
                (time.time() - self._cache_timestamp) < (self._cache_ttl - 30)):
            logger.debug("[Batch] Cache válido, pulando (fast path)")
            return True
    
        # ── Lock anti-stampede ─────────────────────────────────────────────
        with self._batch_lock:
    
            # Double-check pós-lock: outra thread pode ter populado o cache
            # enquanto esta aguardava na fila do lock.
            if (self._permissions_cache and self._cache_timestamp and
                    (time.time() - self._cache_timestamp) < (self._cache_ttl - 30)):
                logger.debug("[Batch] Cache populado por outra thread, reutilizando")
                return True
    
            # ── Apenas 1 thread chega aqui por vez ────────────────────────
            if not self.connected:
                logger.warning("[Batch] Sheets não conectado, usando fallback")
                self._load_fallback_permissions()
                return False
    
            ABAS = ['Empresas', 'Permissoes', 'Usuários', 'TelasModulos', 'Departamentos', 'Cargos']
    
            try:
                logger.info(f"[Batch] Carregando {len(ABAS)} abas em 1 chamada à API...")
                t0 = time.time()
    
                ranges = [f"'{aba}'!A:Z" for aba in ABAS]
                resultado_raw = self.sheet.values_batch_get(ranges)
    
                elapsed = time.time() - t0
                logger.info(f"[Batch] API respondeu em {elapsed:.2f}s")
    
                dados = self._parse_batch_result(resultado_raw, ABAS)
    
                # 1. Empresas + Permissoes → _permissions_cache
                self._batch_populate_permissions(
                    dados.get('Empresas', []),
                    dados.get('Permissoes', [])
                )
    
                # 2. Usuários → _users_cache
                self._batch_populate_users(dados.get('Usuários', []))
    
                # 3. TelasModulos → _telas_cache
                self._batch_populate_telas(dados.get('TelasModulos', []))
    
                # 4. Permissoes → _matrix_cache (reutiliza dados já carregados)
                self._batch_populate_matrix(dados.get('Permissoes', []))
    
                # 5. Departamentos + Cargos → _generic_cache
                now = time.time()
                self._generic_cache['Departamentos'] = (dados.get('Departamentos', []), now)
                self._generic_cache['Cargos']        = (dados.get('Cargos', []), now)
    
                logger.info("[Batch] Todos os caches populados com sucesso")
                return True
    
            except APIError as e:
                if '429' in str(e) or 'Quota exceeded' in str(e):
                    logger.error("[Batch] 429 na chamada batch — caches anteriores mantidos")
                else:
                    logger.error(f"[Batch] APIError: {e}")
                # Não sobrescreve caches existentes em caso de 429
                return False
    
            except Exception as e:
                logger.error(f"[Batch] Erro inesperado: {e}")
                logger.error(traceback.format_exc())
                # Só usa fallback se não há cache existente
                if not self._permissions_cache:
                    self._load_fallback_permissions()
                return False

    # ── HELPERS DO BATCH ──────────────────────────────────────────────────────

    def _parse_batch_result(self, resultado_raw: dict, abas: list) -> dict:
        """
        Converte o retorno bruto de values_batch_get em {nome_aba: [dict, ...]}

        A API retorna valueRanges com listas de listas. A primeira linha
        é o cabeçalho. Replica o comportamento do get_all_records().
        """
        dados = {}
        value_ranges = resultado_raw.get('valueRanges', [])

        for i, aba in enumerate(abas):
            try:
                if i >= len(value_ranges):
                    logger.warning(f"[Batch] Aba '{aba}' não retornada pela API")
                    dados[aba] = []
                    continue

                rows = value_ranges[i].get('values', [])

                if not rows:
                    logger.warning(f"[Batch] Aba '{aba}' está vazia")
                    dados[aba] = []
                    continue

                headers = rows[0]
                registros = []

                for row in rows[1:]:
                    padded = row + [''] * (len(headers) - len(row))
                    registros.append(dict(zip(headers, padded)))

                dados[aba] = registros
                logger.debug(f"[Batch] '{aba}': {len(registros)} registros")

            except Exception as e:
                logger.error(f"[Batch] Erro ao parsear aba '{aba}': {e}")
                dados[aba] = []

        return dados

    def _batch_populate_permissions(self, empresas_data: list, permissoes_data: list):
        """Popula _permissions_cache a partir dos dados batch."""
        try:
            self.company_permissions = {
                row['empresa']: [
                    m.strip()
                    for m in row.get('modulos_permitidos', '').split(',')
                    if m.strip()
                ]
                for row in empresas_data
                if row.get('empresa')
            }

            self.role_permissions = {}
            for row in permissoes_data:
                nivel    = row.get('nivel', '').strip().lower()
                modulo   = row.get('modulo', '').strip()
                tela     = row.get('tela', '').strip()
                permitido = row.get('permitido', '').strip().lower() == 'sim'

                if not all([nivel, modulo, tela]) or not permitido:
                    continue

                self.role_permissions.setdefault(nivel, {})
                self.role_permissions[nivel].setdefault(modulo, {})
                self.role_permissions[nivel][modulo][tela] = True

            self._permissions_cache = {
                'company': self.company_permissions.copy(),
                'role': self.role_permissions.copy()
            }
            self._cache_timestamp = time.time()

            logger.info(
                f"⚡ [Batch] Permissões: {len(self.company_permissions)} empresas, "
                f"{len(self.role_permissions)} níveis"
            )
        except Exception as e:
            logger.error(f"[Batch] _batch_populate_permissions: {e}")
            self._load_fallback_permissions()

    def _batch_populate_users(self, usuarios_data: list):
        """Popula _users_cache a partir dos dados batch."""
        try:
            from core.models.user import User

            valid_users = []
            for i, row in enumerate(usuarios_data, start=2):
                try:
                    if row.get('email') and row.get('nome'):
                        user = User.from_dict(row)
                        user.permissions = self.get_user_permissions(user)
                        valid_users.append(user)
                except (ValueError, Exception) as e:
                    logger.warning(f"[Batch] Ignorando usuário linha {i}: {e}")

            self._users_cache = valid_users
            self._users_cache_timestamp = time.time()

            logger.info(f"[Batch] Usuários: {len(valid_users)} carregados")
        except Exception as e:
            logger.error(f"[Batch] _batch_populate_users: {e}")

    def _batch_populate_telas(self, telas_data: list):
        """
        Popula _telas_cache para TODAS as empresas encontradas nos dados.
        O cache de telas é por empresa (chave: 'telas_{empresa}').
        """
        try:
            por_empresa: dict = {}

            for row in telas_data:
                empresa_row = row.get('empresa', '').strip()
                modulo      = row.get('modulo', '').strip().lower()
                tela        = row.get('tela', '').strip()
                ativo       = row.get('ativo', 'sim').strip().lower()

                if not all([empresa_row, modulo, tela]):
                    continue
                if ativo not in ('sim', 'yes', '1', 'true'):
                    continue

                empresas_alvo = (
                    list(self.company_permissions.keys())
                    if empresa_row in ('*', 'global')
                    else [empresa_row]
                )

                for emp in empresas_alvo:
                    por_empresa.setdefault(emp, {})
                    por_empresa[emp].setdefault(modulo, [])
                    if tela not in por_empresa[emp][modulo]:
                        por_empresa[emp][modulo].append(tela)

            now = time.time()
            for emp, telas_por_modulo in por_empresa.items():
                cache_key = f'telas_{emp}'
                self._telas_cache[cache_key] = {
                    'data': telas_por_modulo,
                    'ts': now
                }

            logger.info(f"[Batch] Telas: {len(por_empresa)} empresa(s) populadas")
        except Exception as e:
            logger.error(f"[Batch] _batch_populate_telas: {e}")

    def _batch_populate_matrix(self, permissoes_data: list):
        """
        Popula _matrix_cache para todas as empresas.
        Reutiliza os dados de Permissoes já carregados pelo batch — zero chamadas extras.
        """
        try:
            if not hasattr(self, '_matrix_cache'):
                self._matrix_cache = {}

            por_empresa: dict = {}

            for row in permissoes_data:
                empresa_row = row.get('empresa', '').strip()
                if not empresa_row:
                    continue

                tipo     = row.get('tipo', 'nivel').strip().lower()
                perfil   = row.get('perfil', '').strip()
                modulo   = row.get('modulo', '').strip()
                tela     = row.get('tela', '').strip()
                permitido = row.get('permitido', 'nao').strip().lower() in ('sim', 'yes', 'true', '1')

                if not all([tipo, perfil, modulo, tela]) or not permitido:
                    continue
                if tipo not in ('nivel', 'departamento', 'cargo', 'combo', 'usuario'):
                    continue

                por_empresa.setdefault(empresa_row, {
                    'nivel': {}, 'departamento': {}, 'cargo': {}, 'combo': {}, 'usuario': {}
                })

                matrix = por_empresa[empresa_row]
                matrix[tipo].setdefault(perfil, {})
                matrix[tipo][perfil].setdefault(modulo, {})
                nivel_acesso = row.get('nivel_acesso', '').strip().lower() or 'editar'
                if nivel_acesso not in ('visualizar', 'editar', 'admin'):
                    nivel_acesso = 'editar'
                matrix[tipo][perfil][modulo][tela] = nivel_acesso

            now = time.time()
            for emp, matrix in por_empresa.items():
                self._matrix_cache[f'perm_matrix_{emp}'] = {
                    'data': matrix,
                    'ts': now
                }

            logger.info(f"[Batch] Matrix: {len(por_empresa)} empresa(s) populadas")
        except Exception as e:
            logger.error(f"[Batch] _batch_populate_matrix: {e}")
