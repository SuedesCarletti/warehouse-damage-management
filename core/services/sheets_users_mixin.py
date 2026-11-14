"""
sheets_users_mixin.py
────────────────────────────────────────────────────────────────
Responsabilidade: CRUD de usuários, departamentos e cargos.

Métodos públicos:
  get_all_users()               invalidate_users_cache()
  invalidate_permissions_cache()
  get_user_by_email()           add_user()
  update_user()                 delete_user()
  get_departamentos()           add_departamento()
  rename_departamento()         delete_departamento()
  get_cargos()                  add_cargo()
  delete_cargo()

Métodos internos:
  _get_fallback_users()
  _get_users_worksheet()
"""

import logging
import traceback
import time
from typing import List, Tuple, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class SheetsUsersMixin:
    """CRUD completo de usuários, departamentos e cargos."""

    # CACHE

    def invalidate_users_cache(self):
        """
        Invalida o cache de usuários.
        Use após adicionar/atualizar/remover usuários.
        """
        self._users_cache = None
        self._users_cache_timestamp = None
        logger.info("↺ Cache de usuários invalidado")

    def invalidate_permissions_cache(self):
        """Invalida o cache de permissões (permissions_cache + matrix_cache)."""
        self._permissions_cache = None
        self._cache_timestamp = None
        if hasattr(self, '_matrix_cache'):
            self._matrix_cache.clear()
        logger.info("↺ Cache de permissões invalidado")

    # USUÁRIOS - LEITURA

    def get_all_users(self) -> list:
        """
        Retorna todos os usuários com CACHE de 15 minutos.
        Evita rate limiting da API do Google Sheets.
        """
        # VERIFICAR CACHE PRIMEIRO
        if self._users_cache and self._users_cache_timestamp:
            elapsed = time.time() - self._users_cache_timestamp
            if elapsed < self._users_cache_ttl:
                logger.debug(f"Usando cache de usuários ({int(self._users_cache_ttl - elapsed)}s restantes)")
                self._users_source = 'cache'
                return self._users_cache

        # Se não conectado, usar fallback
        if not self.connected:
            logger.info("Usando usuários de fallback (sem conexão)")
            self._users_source = 'fallback'
            return self._get_fallback_users()

        # Buscar do Google Sheets
        try:
            logger.info("↺ Carregando usuários do Google Sheets...")
            users_ws = self.sheet.worksheet("Usuários")
            data = users_ws.get_all_records()

            if not data:
                logger.warning("Nenhum dado retornado (Planilha vazia ou CircuitBreaker aberto).")
                raise Exception("Não foi possível carregar usuários (lista vazia).")

            from core.models.user import User

            valid_users = []
            for i, row in enumerate(data, start=2):
                try:
                    if row.get('email') and row.get('nome'):
                        user = User.from_dict(row)
                        user.permissions = self.get_user_permissions(user)
                        valid_users.append(user)
                    else:
                        logger.warning(f"Ignorando linha {i} na aba 'Usuários': email ou nome ausentes.")
                except ValueError as e:
                    logger.warning(f"Ignorando dados de usuário inválidos na linha {i}: {e}")

            # ATUALIZAR CACHE
            self._users_cache = valid_users
            self._users_cache_timestamp = time.time()
            self._users_source = 'sheets'

            logger.info(f"{len(valid_users)} usuário(s) carregado(s) e cacheado(s) por {self._users_cache_ttl}s")
            return valid_users

        except Exception as e:
            logger.error(f"Erro ao buscar usuários: {e}")
            # Se temos cache antigo, usar mesmo expirado
            if self._users_cache:
                logger.warning("Usando cache expirado de usuários devido ao erro")
                self._users_source = 'cache'
                return self._users_cache
            self._users_source = 'fallback'
            return self._get_fallback_users()

    def get_user_by_email(self, email: str):
        """Busca usuário por email."""
        users = self.get_all_users()
        for user in users:
            if user.email.lower() == email.lower():
                return user
        return None

    # USUÁRIOS - ESCRITA

    def add_user(self, user) -> Tuple[bool, str]:
        """Adiciona novo usuário ao Google Sheets."""
        if not self.connected:
            return False, "Google Sheets não conectado"

        try:
            users_ws = self.sheet.worksheet("Usuários")

            # Verificar duplicação
            existing = self.get_user_by_email(user.email)
            if existing:
                return False, "Email já cadastrado"

            # Ordem alinhada com _ensure_users_sheet:
            # nome | email | departamento | nivel | ativo | senha | empresa |
            # cargo | admin_escopo | requer_alteracao_senha | data_cadastro | ultimo_login
            nova_linha = [
                user.nome,
                user.email,
                user.departamento,
                user.nivel,
                user.ativo,
                user.senha or "",
                user.empresa or "",
                getattr(user, 'cargo', '') or "",
                getattr(user, 'admin_escopo', '') or "",
                getattr(user, 'requer_alteracao_senha', 'nao'),
                user.data_cadastro or datetime.now().strftime("%Y-%m-%d"),
                user.ultimo_login or "",
            ]

            users_ws.append_row(nova_linha)
            self.invalidate_users_cache()

            logger.info(f"Usuário {user.email} adicionado ao Google Sheets")
            return True, "Usuário adicionado com sucesso"

        except Exception as e:
            logger.error(f"Erro ao adicionar usuário: {e}")
            logger.error(traceback.format_exc())
            return False, str(e)

    def update_user(self, email: str, **updates) -> Tuple[bool, str]:
        """Atualiza dados de um usuário."""
        if not self.connected:
            return False, "Google Sheets não conectado"

        try:
            users_ws = self._get_users_worksheet()

            all_data = users_ws.get_all_values()
            headers = all_data[0]
            email_col = headers.index('email') + 1

            user_row = None
            for idx, row in enumerate(all_data[1:], start=2):
                if row[email_col - 1].lower() == email.lower():
                    user_row = idx
                    break

            if not user_row:
                return False, "Usuário não encontrado"

            for field, value in updates.items():
                if field in headers:
                    col_idx = headers.index(field) + 1
                    users_ws.update_cell(user_row, col_idx, str(value))

            self.invalidate_users_cache()
            self.invalidate_permissions_cache()

            logger.info(f"Usuário {email} atualizado no Google Sheets")
            return True, "Usuário atualizado com sucesso"

        except Exception as e:
            logger.error(f"Erro ao atualizar usuário: {e}")
            logger.error(traceback.format_exc())
            return False, str(e)

    def delete_user(self, email: str) -> Tuple[bool, str]:
        """Desativa usuário (soft delete)."""
        return self.update_user(email, ativo="nao")

    # DEPARTAMENTOS

    def get_departamentos(self, empresa: str) -> list:
        """Busca departamentos da aba 'Departamentos' filtrado por empresa."""
        try:
            rows = self._get_sheet_data_cached('Departamentos', ttl=900)  # 15 min
            return [
                row.get('nome', '').strip()
                for row in rows
                if row.get('empresa', '').strip().lower() == empresa.lower()
                and row.get('nome', '').strip()
            ]
        except Exception as e:
            self.logger.warning(f"get_departamentos({empresa}): {e}")
            return []

    def add_departamento(self, empresa: str, nome: str, descricao: str = '') -> tuple:
        """Adiciona departamento na aba 'Departamentos'."""
        try:
            sheet = self._get_worksheet('Departamentos')
            if sheet is None:
                return False, "Aba 'Departamentos' não encontrada no Google Sheets"

            existentes = self.get_departamentos(empresa)
            if nome in existentes:
                return False, f"Departamento '{nome}' já existe"

            sheet.append_row([empresa, nome, descricao])
            self._invalidate_cache('departamentos')
            self.logger.info(f"Departamento criado: {empresa}/{nome}")
            return True, f"Departamento '{nome}' criado com sucesso"
        except Exception as e:
            self.logger.error(f"add_departamento: {e}")
            return False, str(e)

    def rename_departamento(self, empresa: str, nome_original: str, novo_nome: str) -> tuple:
        """Renomeia departamento e atualiza usuários vinculados."""
        try:
            sheet = self._get_worksheet('Departamentos')
            if sheet is None:
                return False, "Aba 'Departamentos' não encontrada"

            # Atualiza na aba Departamentos
            rows = sheet.get_all_records()
            for i, row in enumerate(rows, start=2):
                if (row.get('empresa', '').strip().lower() == empresa.lower() and
                        row.get('nome', '').strip() == nome_original):
                    sheet.update_cell(i, 2, novo_nome)
                    break

            # Atualiza usuários que tinham o departamento antigo
            sheet_users = self._get_worksheet('Usuários')
            if sheet_users:
                user_rows = sheet_users.get_all_records()
                for i, row in enumerate(user_rows, start=2):
                    if (row.get('empresa', '').strip().lower() == empresa.lower() and
                            row.get('departamento', '').strip() == nome_original):
                        col_idx = self._get_column_index(sheet_users, 'departamento')
                        if col_idx:
                            sheet_users.update_cell(i, col_idx, novo_nome)

            self._invalidate_cache('departamentos')
            self.invalidate_users_cache()
            return True, f"Departamento renomeado de '{nome_original}' para '{novo_nome}'"
        except Exception as e:
            self.logger.error(f"rename_departamento: {e}")
            return False, str(e)

    def delete_departamento(self, empresa: str, nome: str) -> tuple:
        """Remove departamento da aba (usuários permanecem com o departamento)."""
        try:
            sheet = self._get_worksheet('Departamentos')
            if sheet is None:
                return False, "Aba 'Departamentos' não encontrada"

            rows = sheet.get_all_records()
            for i, row in enumerate(rows, start=2):
                if (row.get('empresa', '').strip().lower() == empresa.lower() and
                        row.get('nome', '').strip() == nome):
                    sheet.delete_rows(i)
                    self._invalidate_cache('departamentos')
                    return True, f"Departamento '{nome}' removido"

            return False, f"Departamento '{nome}' não encontrado"
        except Exception as e:
            self.logger.error(f"delete_departamento: {e}")
            return False, str(e)

    # CARGOS

    def get_cargos(self, empresa: str) -> list:
        """Busca cargos da aba 'Cargos' filtrado por empresa."""
        try:
            sheet = self._get_worksheet('Cargos')
            if sheet is None:
                return []
            rows = sheet.get_all_records()
            return sorted([
                row.get('cargo', '').strip()
                for row in rows
                if row.get('empresa', '').strip().lower() == empresa.lower()
                and row.get('cargo', '').strip()
            ])
        except Exception as e:
            logging.getLogger(__name__).warning(f"get_cargos({empresa}): {e}")
            return []

    def add_cargo(self, empresa: str, cargo: str) -> tuple:
        """Adiciona cargo na aba 'Cargos'."""
        try:
            sheet = self._get_worksheet('Cargos')
            if sheet is None:
                return False, "Aba 'Cargos' não encontrada no Google Sheets"

            existentes = self.get_cargos(empresa)
            if cargo in existentes:
                return False, f"Cargo '{cargo}' já existe"

            sheet.append_row([empresa, cargo])
            self._invalidate_cache('cargos')
            return True, f"Cargo '{cargo}' adicionado"
        except Exception as e:
            self.logger.error(f"add_cargo: {e}")
            return False, str(e)

    def delete_cargo(self, empresa: str, cargo: str) -> tuple:
        """Remove cargo da aba 'Cargos'."""
        try:
            sheet = self._get_worksheet('Cargos')
            if sheet is None:
                return False, "Aba 'Cargos' não encontrada"

            rows = sheet.get_all_records()
            for i, row in enumerate(rows, start=2):
                if (row.get('empresa', '').strip().lower() == empresa.lower() and
                        row.get('cargo', '').strip() == cargo):
                    sheet.delete_rows(i)
                    self._invalidate_cache('cargos')
                    return True, f"Cargo '{cargo}' removido"

            return False, f"Cargo '{cargo}' não encontrado"
        except Exception as e:
            self.logger.error(f"delete_cargo: {e}")
            return False, str(e)

    # FALLBACK

    def _get_fallback_users(self) -> list:
        """Usuários padrão quando Google Sheets não disponível."""
        from core.models.user import User

        logger.info("Usando usuários padrão (fallback)")

        # Nota: Senhas são placeholders, o auth_service tem backdoor para dev
        fallback_users = [
            User(nome="Admin Matriz", email="admin@matriz.com", departamento="TI",
                 nivel="admin", ativo="sim", senha="REDACTED_SECRET", empresa="Matriz"),
            User(nome="Gestor HelpDesk", email="gestor@cliente7.example.com", departamento="Suporte",
                 nivel="admin_depto", ativo="sim", senha="REDACTED_SECRET", empresa="EmpresaY"),
            User(nome="Operador WMS", email="pessoa1@example.com", departamento="Logística",
                 nivel="usuario", ativo="sim", senha="REDACTED_SECRET", empresa="Empresax")
        ]

        for user in fallback_users:
            user.permissions = self.get_user_permissions(user)

        return fallback_users

    # HELPERS INTERNOS

    def _get_users_worksheet(self):
        """Retorna worksheet de Usuários com cache da referência."""
        if self._users_ws is not None:
            return self._users_ws
        try:
            self._users_ws = self.sheet.worksheet("Usuários")
            return self._users_ws
        except Exception as e:
            logger.error(f"Erro ao obter worksheet Usuários: {e}")
            self._users_ws = None  # garante retry na próxima chamada
            raise
