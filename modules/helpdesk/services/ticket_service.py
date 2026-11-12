"""
services/ticket_service.py
Serviço para operações com tickets/chamados
"""

import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime

# CORRIGIDO: Importação do caminho absoluto a partir da raiz do projeto
from modules.helpdesk.models import Ticket

logger = logging.getLogger(__name__)

class TicketService:
    
    def __init__(self, db_manager, sheets_service, email_service, config):
        self.db = db_manager
        self.sheets = sheets_service
        self.email = email_service
        self.config = config
    
    def create_ticket(self, ticket_data: dict) -> Tuple[bool, str, Optional[Ticket]]:
        try:
            ticket_number = Ticket.generate_ticket_number()
            
            category = self.sheets.get_category_by_name(ticket_data['category'])
            third_party_email = category.email_terceiro if category else None
            
            ticket = Ticket(
                ticket_number=ticket_number,
                user_email=ticket_data['user_email'],
                user_name=ticket_data['user_name'],
                department=ticket_data['department'],
                category=ticket_data['category'],
                subject=ticket_data['subject'],
                description=ticket_data['description'],
                priority=ticket_data['priority'],
                created_by=ticket_data['created_by'],
                third_party_email=third_party_email
            )
            
            ticket_id = self.db.create_ticket(ticket)
            ticket.id = ticket_id
            
            self.sheets.add_ticket(ticket)

            self.db.add_status_log(
                ticket_id=ticket_id,
                new_status='Aberto',
                changed_by=ticket_data['created_by'],
                change_reason='Chamado criado'
            )
            
            if third_party_email and self.email.enabled:
                self.email.send_ticket_notification(
                    ticket, third_party_email, self.config.BASE_URL
                )
            
            logger.info(f"Ticket {ticket_number} criado")
            return True, ticket_number, ticket
            
        except Exception as e:
            logger.error(f"Erro ao criar ticket: {e}")
            return False, str(e), None
    
    def get_ticket(self, ticket_number: str) -> Optional[Ticket]:
        """Busca ticket por número"""
        return self.db.get_ticket_by_number(ticket_number)
    
    def get_tickets(self, filters: dict = None) -> List[Ticket]:
        """Lista tickets com filtros opcionais"""
        return self.db.get_tickets(filters)
    
    def update_status(self, ticket_number: str, new_status: str, 
                     changed_by: str, reason: str = "") -> Tuple[bool, str]:
        """
        Atualiza status de um ticket
        
        Args:
            ticket_number: Número do ticket
            new_status: Novo status
            changed_by: Email de quem fez a mudança
            reason: Motivo da mudança
            
        Returns:
            Tuple[bool, str]: (sucesso, mensagem)
        """
        try:
            ticket = self.get_ticket(ticket_number)
            if not ticket:
                return False, "Ticket não encontrado"
            
            old_status = ticket.status
            
            # Atualizar status
            success = self.db.update_ticket_status(
                ticket_number, new_status, reason
            )
            
            if success:
                # Adicionar log
                self.db.add_status_log(
                    ticket_id=ticket.id,
                    old_status=old_status,
                    new_status=new_status,
                    changed_by=changed_by,
                    change_reason=reason
                )
                
                logger.info(f"Status {ticket_number}: {old_status} → {new_status}")
                return True, "Status atualizado com sucesso"
            
            return False, "Erro ao atualizar status"
            
        except Exception as e:
            logger.error(f"Erro ao atualizar status: {e}")
            return False, str(e)
    
    def get_status_logs(self, ticket_number: str) -> List[dict]:
        """Retorna histórico de status de um ticket"""
        ticket = self.get_ticket(ticket_number)
        if not ticket:
            return []
        return self.db.get_status_logs(ticket.id)
    
    def get_statistics(self, user_email: str = None) -> dict:
        """
        Retorna estatísticas de tickets
        
        Args:
            user_email: Se fornecido, filtra por usuário
            
        Returns:
            dict: Estatísticas
        """
        filters = {'user_email': user_email} if user_email else None
        tickets = self.get_tickets(filters)
        
        return {
            'total': len(tickets),
            'abertos': len([t for t in tickets if t.status == 'Aberto']),
            'em_andamento': len([t for t in tickets if t.status == 'Em Andamento']),
            'resolvidos': len([t for t in tickets if t.status in ['Resolvido', 'Fechado']])
        }
    
    def mark_email_opened(self, ticket_number: str) -> bool:
        """Marca email do ticket como aberto"""
        return self.db.mark_email_opened(ticket_number)
    
    def resolve_by_token(self, token: str, resolved_by: str = "Terceiro") -> Tuple[bool, Optional[Ticket]]:
        """
        Resolve ticket usando token de resolução
        
        Args:
            token: Token único de resolução
            resolved_by: Quem resolveu
            
        Returns:
            Tuple[bool, Optional[Ticket]]: (sucesso, ticket)
        """
        try:
            ticket = self.db.get_ticket_by_token(token)
            if not ticket:
                return False, None
            
            if ticket.is_closed:
                return False, ticket  # Já resolvido
            
            # Atualizar para resolvido
            self.update_status(
                ticket.ticket_number,
                'Resolvido',
                resolved_by,
                'Resolvido via email'
            )
            
            return True, ticket
            
        except Exception as e:
            logger.error(f"Erro ao resolver ticket: {e}")
            return False, None
