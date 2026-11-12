"""
core/models/company.py
Modelo de dados para Empresa/Companhia
"""

from dataclasses import dataclass, field
from typing import List

@dataclass
class Company:
    """
    Modelo de Empresa/Companhia
    
    Representa uma empresa no sistema com seus módulos permitidos.
    Usado para controle de acesso baseado na empresa do usuário.
    """
    
    # Identificação
    empresa: str  # Nome da empresa (ex: "Matriz", "Filial A")
    
    # Módulos permitidos para esta empresa
    modulos_permitidos: List[str] = field(default_factory=list)
    
    # Metadados opcionais
    ativo: str = 'sim'
    
    def __post_init__(self):
        """Validações após inicialização"""
        self._validate()
        self._normalize()
    
    def _validate(self):
        """Valida dados da empresa"""
        # Validar nome
        if not self.empresa or len(self.empresa) < 2:
            raise ValueError("Nome da empresa deve ter pelo menos 2 caracteres")
        
        # Validar status
        if self.ativo not in ['sim', 'nao']:
            raise ValueError("Status ativo deve ser 'sim' ou 'nao'")
        
        # Validar módulos
        valid_modules = ['helpdesk', 'wms']
        for modulo in self.modulos_permitidos:
            if modulo not in valid_modules:
                raise ValueError(f"Módulo '{modulo}' não é válido. Módulos válidos: {valid_modules}")
    
    def _normalize(self):
        """Normaliza dados"""
        # Garantir que modulos_permitidos seja sempre lista
        if isinstance(self.modulos_permitidos, str):
            # Se vier como string (ex: "helpdesk,wms"), converter para lista
            self.modulos_permitidos = [m.strip() for m in self.modulos_permitidos.split(',') if m.strip()]
    
    @property
    def is_active(self) -> bool:
        """Verifica se empresa está ativa"""
        return self.ativo == 'sim'
    
    @property
    def has_helpdesk(self) -> bool:
        """Verifica se empresa tem acesso ao HelpDesk"""
        return 'helpdesk' in self.modulos_permitidos
    
    @property
    def has_wms(self) -> bool:
        """Verifica se empresa tem acesso ao WMS"""
        return 'wms' in self.modulos_permitidos
    
    def has_module(self, module: str) -> bool:
        """
        Verifica se empresa tem acesso a um módulo específico
        
        Args:
            module: Nome do módulo (ex: 'helpdesk', 'wms')
            
        Returns:
            bool: True se empresa tem acesso ao módulo
        """
        return module in self.modulos_permitidos
    
    def add_module(self, module: str) -> bool:
        """
        Adiciona módulo aos permitidos
        
        Args:
            module: Nome do módulo
            
        Returns:
            bool: True se adicionado, False se já existia
        """
        if module not in self.modulos_permitidos:
            self.modulos_permitidos.append(module)
            return True
        return False
    
    def remove_module(self, module: str) -> bool:
        """
        Remove módulo dos permitidos
        
        Args:
            module: Nome do módulo
            
        Returns:
            bool: True se removido, False se não existia
        """
        if module in self.modulos_permitidos:
            self.modulos_permitidos.remove(module)
            return True
        return False
    
    def to_dict(self) -> dict:
        """Converte para dicionário (formato Google Sheets)"""
        return {
            'empresa': self.empresa,
            'modulos_permitidos': ','.join(self.modulos_permitidos),  # String separada por vírgulas
            'ativo': self.ativo
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'Company':
        """
        Cria empresa a partir de dicionário (linha do Google Sheets)
        
        Args:
            data: Dicionário com dados da empresa
            
        Returns:
            Company: Instância da empresa
        """
        modulos_str = data.get('modulos_permitidos', '')
        modulos_list = [m.strip() for m in modulos_str.split(',') if m.strip()]
        
        return cls(
            empresa=data.get('empresa', ''),
            modulos_permitidos=modulos_list,
            ativo=data.get('ativo', 'sim')
        )
    
    def __repr__(self):
        return f"<Company {self.empresa} ({', '.join(self.modulos_permitidos)})>"


# EXEMPLOS DE USO

if __name__ == "__main__":
    # Exemplo 1: Criar empresa com múltiplos módulos
    matriz = Company(
        empresa="Matriz",
        modulos_permitidos=['helpdesk', 'wms']
    )
    print(matriz)  # <Company Matriz (helpdesk, wms)>
    print(f"Tem HelpDesk? {matriz.has_helpdesk}")  # True
    print(f"Tem WMS? {matriz.has_wms}")  # True
    
    # Exemplo 2: Criar empresa com apenas um módulo
    filial_a = Company(
        empresa="Filial A",
        modulos_permitidos=['helpdesk']
    )
    print(filial_a)  # <Company Filial A (helpdesk)>
    print(f"Tem HelpDesk? {filial_a.has_helpdesk}")  # True
    print(f"Tem WMS? {filial_a.has_wms}")  # False
    
    # Exemplo 3: Adicionar módulo
    filial_a.add_module('wms')
    print(filial_a)  # <Company Filial A (helpdesk, wms)>
    
    # Exemplo 4: Converter para dict (para Google Sheets)
    print(filial_a.to_dict())
    # {'empresa': 'Filial A', 'modulos_permitidos': 'helpdesk,wms', 'ativo': 'sim'}
    
    # Exemplo 5: Criar a partir de dict (linha do Google Sheets)
    data_from_sheets = {
        'empresa': 'Thomas',
        'modulos_permitidos': 'helpdesk',
        'ativo': 'sim'
    }
    thomas = Company.from_dict(data_from_sheets)
    print(thomas)  # <Company Thomas (helpdesk)>
