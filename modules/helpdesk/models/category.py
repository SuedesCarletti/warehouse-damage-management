"""
models/category.py
Modelo de dados para Categoria
"""

from dataclasses import dataclass
from typing import Optional

@dataclass
class Category:
    """Modelo de Categoria de Chamado"""
    
    categoria: str
    descricao: str
    email_terceiro: str = ''
    requer_gestor: str = 'nao'
    sla_horas: str = '24'
    ativo: str = 'sim'
    cor: str = '#3498db'
    
    def __post_init__(self):
        """Validações após inicialização"""
        self._validate()
    
    def _validate(self):
        """Valida dados da categoria"""
        # Validar nome
        if not self.categoria or len(self.categoria) < 3:
            raise ValueError("Nome da categoria deve ter pelo menos 3 caracteres")
        
        # Validar descrição
        if not self.descricao:
            raise ValueError("Descrição é obrigatória")
        
        # Validar email terceiro (se fornecido)
        if self.email_terceiro and '@' not in self.email_terceiro:
            raise ValueError("Email de terceiro inválido")
        
        # Validar requer_gestor
        if self.requer_gestor not in ['sim', 'nao']:
            raise ValueError("requer_gestor deve ser 'sim' ou 'nao'")
        
        # Validar SLA
        try:
            sla = int(self.sla_horas)
            if sla < 1:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("SLA deve ser um número positivo")
        
        # Validar status
        if self.ativo not in ['sim', 'nao']:
            raise ValueError("Status ativo deve ser 'sim' ou 'nao'")
        
        # Validar cor
        if not self.cor.startswith('#') or len(self.cor) != 7:
            raise ValueError("Cor deve estar no formato #RRGGBB")
    
    @property
    def is_active(self) -> bool:
        """Verifica se categoria está ativa"""
        return self.ativo == 'sim'
    
    @property
    def has_third_party(self) -> bool:
        """Verifica se tem email de terceiro"""
        return bool(self.email_terceiro and '@' in self.email_terceiro)
    
    @property
    def requires_manager(self) -> bool:
        """Verifica se requer gestor"""
        return self.requer_gestor == 'sim'
    
    @property
    def sla_hours_int(self) -> int:
        """Retorna SLA como inteiro"""
        return int(self.sla_horas)
    
    def can_be_used_by(self, user_level: str) -> bool:
        """Verifica se categoria pode ser usada por determinado nível de usuário"""
        if not self.requires_manager:
            return True
        return user_level in ['gestor', 'admin']
    
    def to_dict(self) -> dict:
        """Converte para dicionário"""
        return {
            'categoria': self.categoria,
            'descricao': self.descricao,
            'email_terceiro': self.email_terceiro,
            'requer_gestor': self.requer_gestor,
            'sla_horas': self.sla_horas,
            'ativo': self.ativo,
            'cor': self.cor
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'Category':
        """Cria categoria a partir de dicionário"""
        return cls(
            categoria=data.get('categoria', ''),
            descricao=data.get('descricao', ''),
            email_terceiro=data.get('email_terceiro', ''),
            requer_gestor=data.get('requer_gestor', 'nao'),
            sla_horas=str(data.get('sla_horas', '24')),
            ativo=data.get('ativo', 'sim'),
            cor=data.get('cor', '#3498db')
        )
    
    def __repr__(self):
        return f"<Category {self.categoria} (SLA: {self.sla_horas}h)>"
