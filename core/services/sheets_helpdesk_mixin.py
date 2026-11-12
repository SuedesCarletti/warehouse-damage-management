"""
sheets_helpdesk_mixin.py
────────────────────────────────────────────────────────────────
Responsabilidade: CRUD de categorias e tickets do HelpDesk.

Métodos públicos:
  get_all_categories()          get_category_by_name()
  add_category()                update_category()
  delete_category()             add_ticket()
  update_ticket_in_sheets()

Fallbacks internos:
  _get_fallback_categories()
  _get_fallback_companies()
  _get_fallback_roles()
"""

import logging
import traceback
from typing import List, Tuple, Optional, Dict

logger = logging.getLogger(__name__)


class SheetsHelpdeskMixin:
    """CRUD de categorias e tickets do módulo HelpDesk."""

    # CATEGORIAS

    def get_all_categories(self) -> list:
        """Retorna todas as categorias do Google Sheets."""
        from modules.helpdesk.models import Category

        if not self.connected:
            return self._get_fallback_categories()

        try:
            cats_ws = self.sheet.worksheet("Categorias")
            data = cats_ws.get_all_records()
            return [Category.from_dict(row) for row in data]
        except Exception as e:
            logger.error(f"Erro ao buscar categorias: {e}")
            return self._get_fallback_categories()

    def get_category_by_name(self, name: str):
        """Busca categoria por nome."""
        categories = self.get_all_categories()
        for cat in categories:
            if cat.categoria.lower() == name.lower():
                return cat
        return None

    def add_category(self, category) -> Tuple[bool, str]:
        """Adiciona nova categoria ao Google Sheets."""
        if not self.connected:
            return False, "Google Sheets não conectado"

        try:
            cats_ws = self.sheet.worksheet("Categorias")

            existing = self.get_category_by_name(category.categoria)
            if existing:
                return False, "Categoria já existe"

            nova_linha = [
                category.categoria,
                category.descricao,
                category.email_terceiro,
                category.requer_gestor,
                category.sla_horas,
                category.ativo,
                category.cor
            ]

            cats_ws.append_row(nova_linha)
            logger.info(f"Categoria '{category.categoria}' adicionada ao Google Sheets")
            return True, "Categoria adicionada com sucesso"

        except Exception as e:
            logger.error(f"Erro ao adicionar categoria: {e}")
            logger.error(traceback.format_exc())
            return False, str(e)

    def update_category(self, categoria: str, **updates) -> Tuple[bool, str]:
        """Atualiza dados de uma categoria."""
        if not self.connected:
            return False, "Google Sheets não conectado"

        try:
            cats_ws = self.sheet.worksheet("Categorias")

            all_data = cats_ws.get_all_values()
            headers = all_data[0]
            cat_col = headers.index('categoria') + 1

            cat_row = None
            for idx, row in enumerate(all_data[1:], start=2):
                if row[cat_col - 1].lower() == categoria.lower():
                    cat_row = idx
                    break

            if not cat_row:
                return False, "Categoria não encontrada"

            for field, value in updates.items():
                if field in headers:
                    col_idx = headers.index(field) + 1
                    cats_ws.update_cell(cat_row, col_idx, str(value))

            logger.info(f"Categoria '{categoria}' atualizada no Google Sheets")
            return True, "Categoria atualizada com sucesso"

        except Exception as e:
            logger.error(f"Erro ao atualizar categoria: {e}")
            logger.error(traceback.format_exc())
            return False, str(e)

    def delete_category(self, categoria: str) -> Tuple[bool, str]:
        """Desativa categoria (soft delete)."""
        return self.update_category(categoria, ativo="nao")

    # TICKETS

    def add_ticket(self, ticket) -> Tuple[bool, str]:
        """Adiciona novo ticket ao Google Sheets."""
        if not self.connected:
            logger.warning("Google Sheets não conectado - ticket não será registrado na planilha")
            return False, "Google Sheets não conectado"

        try:
            tickets_ws = self.sheet.worksheet("Tickets")

            nova_linha = [
                ticket.ticket_number,
                ticket.user_email,
                ticket.user_name,
                ticket.department,
                ticket.category,
                ticket.subject,
                ticket.description,
                ticket.priority,
                ticket.status,
                ticket.third_party_email or "",
                ticket.created_at,
                ticket.updated_at,
                ticket.closed_at or "",
                "sim" if ticket.email_opened else "nao",
                ticket.created_by
            ]

            tickets_ws.append_row(nova_linha)
            logger.info(f"Ticket {ticket.ticket_number} adicionado ao Google Sheets")
            return True, "Ticket registrado no Google Sheets"

        except Exception as e:
            logger.error(f"Erro ao adicionar ticket ao Google Sheets: {e}")
            logger.error(traceback.format_exc())
            return False, str(e)

    def update_ticket_in_sheets(self, ticket_number: str, **updates) -> Tuple[bool, str]:
        """Atualiza dados de um ticket no Google Sheets."""
        if not self.connected:
            return False, "Google Sheets não conectado"

        try:
            tickets_ws = self.sheet.worksheet("Tickets")

            all_data = tickets_ws.get_all_values()
            headers = all_data[0]
            ticket_col = headers.index('ticket_number') + 1

            ticket_row = None
            for idx, row in enumerate(all_data[1:], start=2):
                if row[ticket_col - 1] == ticket_number:
                    ticket_row = idx
                    break

            if not ticket_row:
                return False, "Ticket não encontrado no Google Sheets"

            for field, value in updates.items():
                if field in headers:
                    col_idx = headers.index(field) + 1
                    tickets_ws.update_cell(ticket_row, col_idx, str(value))

            logger.info(f"Ticket {ticket_number} atualizado no Google Sheets")
            return True, "Ticket atualizado no Google Sheets"

        except Exception as e:
            logger.error(f"Erro ao atualizar ticket no Google Sheets: {e}")
            logger.error(traceback.format_exc())
            return False, str(e)

    # FALLBACKS

    def _get_fallback_categories(self) -> list:
        """Categorias padrão quando Google Sheets não disponível."""
        from modules.helpdesk.models import Category

        logger.info("Usando categorias padrão (fallback)")
        return [
            Category("Problema no Computador", "Hardware, software, periféricos"),
            Category("Rede e Internet", "Conectividade e problemas de rede"),
            Category("Servidor", "Problemas de infraestrutura",
                     "servidor@terceiro.com", "sim", "4", "sim", "#e74c3c"),
            Category("Suporte Geral", "Outras questões de TI")
        ]

    def _get_fallback_companies(self) -> Dict[str, List[str]]:
        """Empresas padrão quando Google Sheets não disponível."""
        return {
            'Matriz': ['helpdesk', 'wms'],
            'Empresax': ['wms'],
            'Thomas': ['helpdesk']
        }

    def _get_fallback_roles(self) -> dict:
        """Permissões de níveis padrão quando Google Sheets não disponível."""
        return {
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
