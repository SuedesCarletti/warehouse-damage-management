from dataclasses import dataclass
from typing import Optional

@dataclass
class Status:
    """Modelo de Status de Avaria"""
    status: str
    cor: str = "#6c757d"  # Cor padrão (cinza)
    ativo: str = "sim"
    ordem: int = 0
    descricao: str = ""
    status_type: str = "geral" # ◡› NOVO CAMPO: estoque, indenizacao, geral
    
    @classmethod
    def from_dict(cls, data: dict) -> 'Status':
        """Cria objeto Status a partir de dicionário"""
        return cls(
            status=data.get('status', ''),
            cor=data.get('cor', '#6c757d'),
            ativo=data.get('ativo', 'sim'),
            ordem=int(data.get('ordem', 0)),
            descricao=data.get('descricao', ''),
            status_type=data.get('status_type', 'geral') # ◡›
        )
    
    def to_dict(self) -> dict:
        """Converte Status para dicionário"""
        return {
            'status': self.status,
            'cor': self.cor,
            'ativo': self.ativo,
            'ordem': self.ordem,
            'descricao': self.descricao,
            'status_type': self.status_type # ◡›
        }
    
    def __repr__(self):
        return f"Status(status='{self.status}', type='{self.status_type}', cor='{self.cor}', ordem={self.ordem})"
