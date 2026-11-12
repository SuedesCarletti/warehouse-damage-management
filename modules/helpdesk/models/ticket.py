"""
models/ticket.py
Modelo de dados para Ticket/Chamado
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import uuid

@dataclass
class Ticket:
    """Modelo de Ticket/Chamado"""
    
    # Identificação
    ticket_number: str
    
    # Usuário
    user_email: str
    user_name: str
    department: str
    
    # Detalhes do chamado
    category: str
    subject: str
    description: str
    priority: str
    
    # Status
    status: str = 'Aberto'
    
    # Metadados
    created_by: str = None
    assigned_to: Optional[str] = None
    third_party_email: Optional[str] = None
    resolution_token: Optional[str] = None
    
    # Timestamps
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    closed_at: Optional[str] = None
    
    # Flags
    email_opened: bool = False
    
    # ID interno (banco)
    id: Optional[int] = None
    
    def __post_init__(self):
        """Inicialização e defaults"""
        if not self.created_by:
            self.created_by = self.user_email
        
        if not self.resolution_token:
            self.resolution_token = str(uuid.uuid4())
        
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        
        if not self.updated_at:
            self.updated_at = self.created_at
        
        self._validate()
    
    def _validate(self):
        """Valida dados do ticket"""
        # Validar emails
        if not self.user_email or '@' not in self.user_email:
            raise ValueError("Email do usuário inválido")
        
        # Validar assunto
        if not self.subject or len(self.subject) < 5:
            raise ValueError("Assunto deve ter pelo menos 5 caracteres")
        
        # Validar descrição
        if not self.description or len(self.description) < 10:
            raise ValueError("Descrição deve ter pelo menos 10 caracteres")
        
        # Validar prioridade
        valid_priorities = ['Baixa', 'Média', 'Alta', 'Crítica']
        if self.priority not in valid_priorities:
            raise ValueError(f"Prioridade deve ser uma de: {valid_priorities}")
        
        # Validar status
        valid_statuses = ['Aberto', 'Em Andamento', 'Resolvido', 'Fechado']
        if self.status not in valid_statuses:
            raise ValueError(f"Status deve ser um de: {valid_statuses}")
    
    @staticmethod
    def generate_ticket_number() -> str:
        """Gera número único de ticket"""
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        random_suffix = str(uuid.uuid4())[:4].upper()
        return f"TI{timestamp}{random_suffix}"
    
    @property
    def is_open(self) -> bool:
        """Verifica se ticket está aberto"""
        return self.status in ['Aberto', 'Em Andamento']
    
    @property
    def is_closed(self) -> bool:
        """Verifica se ticket está fechado"""
        return self.status in ['Resolvido', 'Fechado']
    
    @property
    def has_third_party(self) -> bool:
        """Verifica se tem terceiro envolvido"""
        return bool(self.third_party_email and '@' in self.third_party_email)
    
    @property
    def priority_level(self) -> int:
        """Retorna nível numérico da prioridade (para ordenação)"""
        levels = {'Baixa': 1, 'Média': 2, 'Alta': 3, 'Crítica': 4}
        return levels.get(self.priority, 1)
    
    @property
    def status_badge_class(self) -> str:
        """Retorna classe CSS para badge de status"""
        classes = {
            'Aberto': 'status-aberto',
            'Em Andamento': 'status-andamento',
            'Resolvido': 'status-resolvido',
            'Fechado': 'status-resolvido'
        }
        return classes.get(self.status, 'status-aberto')
    
    @property
    def priority_class(self) -> str:
        """Retorna classe CSS para prioridade"""
        classes = {
            'Baixa': 'priority-baixa',
            'Média': 'priority-media',
            'Alta': 'priority-alta',
            'Crítica': 'priority-alta'
        }
        return classes.get(self.priority, 'priority-media')
    
    def can_be_updated(self) -> bool:
        """Verifica se ticket pode ser atualizado"""
        return self.is_open
    
    def can_be_closed(self) -> bool:
        """Verifica se ticket pode ser fechado"""
        return self.status != 'Fechado'
    
    def to_dict(self) -> dict:
        """Converte para dicionário"""
        return {
            'id': self.id,
            'ticket_number': self.ticket_number,
            'user_email': self.user_email,
            'user_name': self.user_name,
            'department': self.department,
            'category': self.category,
            'subject': self.subject,
            'description': self.description,
            'priority': self.priority,
            'status': self.status,
            'created_by': self.created_by,
            'assigned_to': self.assigned_to,
            'third_party_email': self.third_party_email,
            'resolution_token': self.resolution_token,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'closed_at': self.closed_at,
            'email_opened': self.email_opened
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'Ticket':
        """Cria ticket a partir de dicionário"""
        return cls(
            id=data.get('id'),
            ticket_number=data.get('ticket_number', ''),
            user_email=data.get('user_email', ''),
            user_name=data.get('user_name', ''),
            department=data.get('department', ''),
            category=data.get('category', ''),
            subject=data.get('subject', ''),
            description=data.get('description', ''),
            priority=data.get('priority', 'Média'),
            status=data.get('status', 'Aberto'),
            created_by=data.get('created_by'),
            assigned_to=data.get('assigned_to'),
            third_party_email=data.get('third_party_email'),
            resolution_token=data.get('resolution_token'),
            created_at=data.get('created_at'),
            updated_at=data.get('updated_at'),
            closed_at=data.get('closed_at'),
            email_opened=bool(data.get('email_opened', False))
        )
    
    def __repr__(self):
        return f"<Ticket {self.ticket_number} ({self.status})>"
