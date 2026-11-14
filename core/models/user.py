"""
core/models/user.py - VERSÃO ATUALIZADA
Modelo de dados para Usuário — com suporte à hierarquia de 3 níveis admin
Campo requer_alteracao_senha para forçar troca de senha no próximo login
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, List

# CONSTANTES DE HIERARQUIA

# Peso numérico de cada nível (quanto maior, mais poder)
NIVEL_HIERARQUIA: Dict[str, int] = {
    'admin_global':  100,
    'admin_empresa':  80,
    'admin_depto':    60,
    #'gestor':         40,
    'transportadora':         20,
    'usuario':        20,
    # Backward-compat: 'admin' antigo = admin_empresa por enquanto
    'admin':          80,
}

# Níveis que são "administradores" em qualquer sentido
NIVEIS_ADMIN = {'admin_global', 'admin_empresa', 'admin_depto', 'admin'}

# Mapeamento legado → novo (usado na migração gradual)
NIVEL_LEGADO_MAP = {
    'admin': 'admin_empresa',   # admin antigo vira admin_empresa
}


@dataclass
class User:
    """Modelo de Usuário com hierarquia administrativa"""

    nome: str
    email: str
    departamento: str
    nivel: str = 'usuario'
    ativo: str = 'sim'

    # Campos expandidos
    senha: Optional[str] = None
    empresa: Optional[str] = None
    cargo: Optional[str] = None

    # ─── Força alteração de senha no próximo login ───────────────────
    requer_alteracao_senha: str = 'nao'  # 'sim' ou 'nao'
    
    # ─── Escopo administrativo ─────────────────────────────────────────────
    # Formato:
    #   admin_global  → "*"
    #   admin_empresa → "Matriz"
    #   admin_depto   → "Matriz:TI"
    #   gestor/usuário→ None ou ""
    admin_escopo: Optional[str] = None

    # Metadados
    data_cadastro: Optional[str] = None
    ultimo_login: Optional[str] = None

    # Permissões dinâmicas (carregadas do Google Sheets)
    permissions: Dict = field(default_factory=dict)

    # ================================================================== #
    # VALIDAÇÕES
    # ================================================================== #

    def __post_init__(self):
        """Validações e normalizações após inicialização"""
        # Validar email
        if not self.email or '@' not in self.email:
            raise ValueError(f"Email inválido: {self.email}")

        # Normalizar email
        self.email = self.email.lower().strip()

        # Validar nível
        if self.nivel not in NIVEL_HIERARQUIA:
            raise ValueError(f"Nível inválido: {self.nivel}. Valores aceitos: {list(NIVEL_HIERARQUIA.keys())}")

        # Normalizar ativo
        if self.ativo not in ['sim', 'nao']:
            raise ValueError(f"Campo 'ativo' deve ser 'sim' ou 'nao', recebido: {self.ativo}")

  # Normalizar requer_alteracao_senha
        if self.requer_alteracao_senha not in ['sim', 'nao']:
            self.requer_alteracao_senha = 'nao'

        # Garantir permissions
        if not self.permissions:
            self.permissions = {'modules': [], 'screens': {}}

    @property
    def is_active(self) -> bool:
        """Usuário está ativo?"""
        return self.ativo == 'sim'

    @property
    def display_name(self) -> str:
        """Nome para exibição com fallback para email"""
        return self.nome or self.email.split('@')[0]

    @property
    def nivel_display(self) -> str:
        """Nome legível do nível"""
        labels = {
            'admin_global': 'Admin Global',
            'admin_empresa': 'Admin Empresa',
            'admin_depto': 'Admin Departamento',
            'admin': 'Admin Empresa',
            'gestor': 'Gestor',
            'usuario': 'Usuário',
        }
        return labels.get(self.nivel, self.nivel.replace('_', ' ').title())

  # Propriedade para verificar se precisa alterar senha
    @property
    def precisa_alterar_senha(self) -> bool:
        """Verifica se usuário deve ser forçado a alterar senha no próximo login"""
        return self.requer_alteracao_senha == 'sim'

    # ================================================================== #
    # PROPRIEDADES HIERÁRQUICAS — NÍVEL
    # ================================================================== #

    @property
    def is_admin_global(self) -> bool:
        """Nível 1: controle total do sistema"""
        return self.nivel == 'admin_global'

    @property
    def is_admin_empresa(self) -> bool:
        """Nível 2: controle da própria empresa"""
        return self.nivel in ('admin_empresa', 'admin')

    @property
    def is_admin_depto(self) -> bool:
        """Nível 3: controle do próprio departamento"""
        return self.nivel == 'admin_depto'

    @property
    def is_any_admin(self) -> bool:
        """Qualquer nível administrativo"""
        return self.nivel in NIVEIS_ADMIN

    @property
    def is_gestor(self) -> bool:
        """Gestor ou qualquer admin (visão ampla)"""
        return self.nivel in ('gestor', 'admin_global', 'admin_empresa', 'admin_depto', 'admin')

    @property
    def is_admin(self) -> bool:
        """Backward-compat: mantém a propriedade antiga apontando p/ qualquer admin"""
        return self.is_any_admin

    @property
    def nivel_peso(self) -> int:
        """Peso numérico do nível para comparações hierárquicas"""
        return NIVEL_HIERARQUIA.get(self.nivel, 0)

    def can_manage_nivel(self, target_nivel: str) -> bool:
        """
        Verifica se este usuário pode criar/editar um usuário com target_nivel.
        Regra: só pode gerenciar níveis ABAIXO do próprio.
        """
        meu_peso = self.nivel_peso
        target_peso = NIVEL_HIERARQUIA.get(target_nivel, 0)
        return meu_peso > target_peso

    def to_safe_dict(self) -> dict:
        """
        Retorna apenas os campos necessários para edição no frontend.
        Exclui senha, permissões e outros dados sensíveis/pesados.
        """
        return {
            'email':        self.email,
            'nome':         self.nome,
            'departamento': self.departamento or '',
            'cargo':        getattr(self, 'cargo', '') or '',
            'nivel':        self.nivel,
            'empresa':      self.empresa or '',
            'is_active':    getattr(self, 'is_active', self.ativo == 'sim'),
        }

    # ================================================================== #
    # PROPRIEDADES HIERÁRQUICAS — ESCOPO
    # ================================================================== #

    @property
    def escopo_empresa(self) -> Optional[str]:
        """
        Retorna a empresa do escopo administrativo.
        Exemplos:
          "*"         → None (admin_global)
          "Matriz"    → "Matriz"
          "Matriz:TI" → "Matriz"
        """
        if not self.admin_escopo or self.admin_escopo == '*':
            return None
        return self.admin_escopo.split(':')[0]

    @property
    def escopo_departamentos(self) -> List[str]:
        """
        Retorna lista de departamentos do escopo administrativo.
        Exemplos:
          "*"         → []
          "Matriz"    → []
          "Matriz:TI" → ["TI"]
          "Matriz:TI,RH,Vendas" → ["TI", "RH", "Vendas"]
        """
        if not self.admin_escopo or self.admin_escopo == '*':
            return []
        parts = self.admin_escopo.split(':')
        if len(parts) < 2:
            return []
        # TI,RH,Vendas → ["TI", "RH", "Vendas"]
        return [d.strip() for d in parts[1].split(',') if d.strip()]

    def pode_ver_empresa(self, nome_empresa: str) -> bool:
        """Admin pode ver dados de uma empresa específica?"""
        if self.is_admin_global:
            return True
        if self.is_admin_empresa or self.is_admin_depto:
            return self.escopo_empresa == nome_empresa
        return False

    def pode_ver_departamento(self, nome_empresa: str, nome_departamento: str) -> bool:
        """Admin pode ver dados de um departamento específico?"""
        if not self.pode_ver_empresa(nome_empresa):
            return False
        if self.is_admin_global or self.is_admin_empresa:
            return True  # vê todos os deptos da empresa
        # admin_depto: nome_departamento deve estar na lista
        return nome_departamento in self.escopo_departamentos
    
    def pode_ver_usuario(self, target_user: 'User') -> bool:
        """
        Verifica se este admin pode visualizar/gerenciar um usuário específico.
        Admin depto: o departamento do target deve ser um dos seus deptos.
        """
        if self.is_admin_global:
            return True
        if self.is_admin_empresa:
            return target_user.empresa == self.escopo_empresa
        if self.is_admin_depto:
            return (
                target_user.empresa == self.escopo_empresa
                and target_user.departamento in self.escopo_departamentos
            )
        return False
    
    # ================================================================== #
    # PERMISSÕES DE MÓDULO (existentes)
    # ================================================================== #

    def has_access_to(self, module: str) -> bool:
        return module in self.permissions.get('modules', [])

    def can_view_screen(self, module: str, screen: str) -> bool:
        module_perms = self.permissions.get('screens', {}).get(module, {})
        return module_perms.get('*', False) or module_perms.get(screen, False)

    def can_access_ticket(self, ticket_user_email: str) -> bool:
        if self.can_view_screen('helpdesk', 'list_tickets_all'):
            return True
        return self.email.lower() == ticket_user_email.lower()

    # ================================================================== #
    # SERIALIZAÇÃO
    # ================================================================== #

    def to_dict(self) -> dict:
        return {
            'nome': self.nome,
            'email': self.email,
            'departamento': self.departamento,
            'nivel': self.nivel,
            'ativo': self.ativo,
            'senha': self.senha,
            'empresa': self.empresa,
            'cargo': self.cargo,
            'admin_escopo': self.admin_escopo or '',
            'requer_alteracao_senha': self.requer_alteracao_senha,
            'data_cadastro': self.data_cadastro,
            'ultimo_login': self.ultimo_login,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'User':
        return cls(
            nome=data.get('nome', ''),
            email=data.get('email', ''),
            departamento=data.get('departamento', ''),
            nivel=(data.get('nivel') or 'usuario').strip(),
            ativo=data.get('ativo', 'sim'),
            senha=data.get('senha'),
            empresa=data.get('empresa'),
            cargo=data.get('cargo'),
            admin_escopo=data.get('admin_escopo') or None,
            requer_alteracao_senha=data.get('requer_alteracao_senha', 'nao'),
            data_cadastro=data.get('data_cadastro'),
            ultimo_login=data.get('ultimo_login'),
        )

    def __repr__(self):
        escopo = f" [{self.admin_escopo}]" if self.admin_escopo else ""
        senha_flag = " " if self.precisa_alterar_senha else ""
        return f"<User {self.email} ({self.nivel}{escopo}){senha_flag}>"
