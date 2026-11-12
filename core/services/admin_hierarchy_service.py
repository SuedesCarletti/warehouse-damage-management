"""
core/services/admin_hierarchy_service.py
Lógica de negócio para a hierarquia de 3 níveis de administração.
"""

import logging
from typing import List, Optional, Tuple, Dict

from core.models.user import User, NIVEL_HIERARQUIA

logger = logging.getLogger(__name__)

# CONSTANTES

# Hierarquia real: admin_global → admin_empresa → admin_depto → usuario
# NOTA: 'gestor' foi removido como nível criável. Usuários existentes com
# nivel='gestor' continuam funcionando (backward-compat via NIVEL_HIERARQUIA),
NIVEIS_CRIÁVEIS: Dict[str, List[str]] = {
    'admin_global':  ['admin_global', 'admin_empresa', 'admin_depto', 'usuario', 'transportadora'],
    'admin_empresa': ['admin_depto', 'usuario', 'transportadora'],
    'admin':         ['admin_depto', 'usuario', 'transportadora'],   # backward-compat
    'admin_depto':   ['usuario'],
}

# Labels legíveis para exibição
NIVEL_LABELS: Dict[str, str] = {
    'admin_global':  'Admin Global',
    'admin_empresa': 'Admin Empresa',
    'admin_depto':   'Admin Departamento',
    'usuario':       'Usuário',
    'transportadora': 'Transportadora',
    'gestor':        'Gestor (legado)',
    'admin':         'Admin Empresa',
}

class AdminHierarchyService:
    """Serviço central para operações administrativas hierárquicas."""

    def __init__(self, sheets_service):
        self.sheets = sheets_service

    def can_create_nivel(self, admin: User, target_nivel: str) -> bool:
        """Verifica se o admin pode criar um usuário com target_nivel."""
        permitidos = NIVEIS_CRIÁVEIS.get(admin.nivel, [])
        return target_nivel in permitidos

    def can_manage_user(self, admin: User, target: User) -> bool:
        """Verifica se o admin pode gerenciar (editar/desativar) o target."""
        if admin.email.lower() == target.email.lower():
            return False
        # Permite gerenciar se o nível for inferior na hierarquia ou se for o mesmo nível operacional (usuário/transportadora)
        if admin.nivel_peso <= target.nivel_peso and target.nivel not in ('usuario', 'transportadora'):
            return False
        return admin.pode_ver_usuario(target)

    def validate_escopo_for_new_user(
        self,
        admin: User,
        empresa: str,
        departamento: str
    ) -> Tuple[bool, str]:
        """Valida se o admin pode criar um usuário na empresa/departamento informados."""
        if admin.is_admin_global:
            return True, 'ok'
    
        if admin.is_admin_empresa:
            minha_empresa = admin.escopo_empresa or admin.empresa
            if empresa and empresa != minha_empresa:
                return False, f"Você só pode criar usuários na empresa '{minha_empresa}'"
            return True, 'ok'
    
        if admin.is_admin_depto:
            minha_empresa = admin.escopo_empresa or admin.empresa
            meus_deptos   = admin.escopo_departamentos or ([admin.departamento] if admin.departamento else [])
    
            if empresa and empresa != minha_empresa:
                return False, f"Você só pode criar usuários na empresa '{minha_empresa}'"
            if departamento and departamento not in meus_deptos:
                lista = ', '.join(meus_deptos) if meus_deptos else '—'
                return False, f"Você só pode criar usuários nos departamentos: {lista}"
            return True, 'ok'
    
        return False, 'Sem permissão para criar usuários'

    # ================================================================== #
    # CONSULTAS
    # ================================================================== #

    def get_manageable_users(self, admin: User) -> List[User]:
        """Retorna usuários que o admin pode gerenciar, filtrados pelo escopo."""
        todos = self.sheets.get_all_users() or []
        return [u for u in todos if admin.pode_ver_usuario(u) and u.email != admin.email and self.can_manage_user(admin, u)]

    def get_manageable_empresas(self, admin: User) -> List[str]:
        """Retorna empresas que o admin pode gerenciar."""
        todas = list(self.sheets.company_permissions.keys())

        if admin.is_admin_global:
            return sorted(todas)

        escopo = admin.escopo_empresa
        if escopo and escopo in todas:
            return [escopo]

        return [admin.empresa] if admin.empresa else []

    def get_manageable_departamentos(self, admin: User, empresa: str) -> List[str]:
        """Retorna departamentos que o admin pode gerenciar dentro de uma empresa."""
        if not admin.pode_ver_empresa(empresa):
            return []
    
        deptos_aba = self.get_manageable_departamentos_raw(empresa)
        if deptos_aba:
            if admin.is_admin_depto:
                meus_deptos = admin.escopo_departamentos
                return [d for d in deptos_aba if d in meus_deptos]
            return deptos_aba
    
        # Fallback: extrai de usuários
        usuarios = self.sheets.get_all_users()
        deptos = sorted(set(
            u.departamento for u in usuarios
            if u.empresa == empresa and u.departamento
        ))
    
        if admin.is_admin_depto:
            meus_deptos = admin.escopo_departamentos
            return [d for d in deptos if d in meus_deptos]
    
        return deptos

    def get_manageable_departamentos_raw(self, empresa: str) -> list:
        """Retorna departamentos diretamente da aba Departamentos (sem filtro de usuários)."""
        try:
            deptos = self.sheets.get_departamentos(empresa)
            if deptos:
                return deptos
        except Exception:
            pass
        # Fallback: extrai de usuários existentes
        try:
            usuarios = self.sheets.get_all_users()
            return sorted(set(
                u.departamento for u in usuarios
                if u.empresa == empresa and u.departamento
            ))
        except Exception:
            return []

    def get_modulos_empresa(self, empresa: str) -> List[str]:
        """Retorna os módulos permitidos para uma empresa."""
        return self.sheets.company_permissions.get(empresa, [])

    def get_all_niveis_criáveis(self, admin: User) -> List[Dict]:
        """Retorna lista de níveis que o admin pode atribuir ao criar usuários."""
        permitidos = NIVEIS_CRIÁVEIS.get(admin.nivel, [])
        return [{'value': n, 'label': NIVEL_LABELS.get(n, n)} for n in permitidos]

    def get_cargos_empresa(self, empresa: str) -> list:
        """Retorna lista de cargos cadastrados para uma empresa."""
        try:
            return self.sheets.get_cargos(empresa) or []
        except Exception as e:
            logger.warning(f"[AdminHierarchy] get_cargos_empresa({empresa}): {e}")
            return []

    def get_usuarios_por_cargo(self, empresa: str) -> Dict[str, List[User]]:
        """
        Retorna usuários agrupados por cargo para uma empresa.
        Usuários sem cargo ficam sob a chave ''.
        """
        try:
            usuarios = self.sheets.get_all_users() or []
            by_cargo: Dict[str, List[User]] = {}
            for u in usuarios:
                if u.empresa != empresa:
                    continue
                chave = u.cargo or ''
                if chave not in by_cargo:
                    by_cargo[chave] = []
                by_cargo[chave].append(u)
            return by_cargo
        except Exception as e:
            logger.error(f"[AdminHierarchy] get_usuarios_por_cargo: {e}")
            return {}

    def get_usuarios_por_departamento(self, empresa: str) -> dict:
        """
        Retorna usuários agrupados por departamento para uma empresa.
        Usuários sem departamento ficam sob a chave ''.
    
        Espelha get_usuarios_por_cargo() — mesmo padrão, mesmo filtro.
        """
        try:
            usuarios = self.sheets.get_all_users() or []
            by_depto: dict = {}
            for u in usuarios:
                if u.empresa != empresa:
                    continue
                chave = u.departamento or ''
                if chave not in by_depto:
                    by_depto[chave] = []
                by_depto[chave].append(u)
            return by_depto
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(
                f"[AdminHierarchy] get_usuarios_por_departamento: {e}"
            )
            return {}

    def get_dashboard_stats(self, admin: User) -> dict:
        """Retorna estatísticas para o painel do admin, filtradas pelo escopo."""
        users    = self.get_manageable_users(admin)
        empresas = self.get_manageable_empresas(admin)

        ativos   = sum(1 for u in users if u.is_active)
        inativos = sum(1 for u in users if not u.is_active)

        by_nivel = {}
        for u in users:
            by_nivel[u.nivel] = by_nivel.get(u.nivel, 0) + 1

        return {
            'total_users':    len(users),
            'ativos':         ativos,
            'inativos':       inativos,
            'total_empresas': len(empresas),
            'by_nivel':       by_nivel,
            'empresas':       empresas,
        }

    # ================================================================== #
    # OPERAÇÕES — CRIAR / EDITAR / DESATIVAR USUÁRIOS
    # ================================================================== #

    def criar_usuario(self, admin: User, dados: dict) -> Tuple[bool, str]:
        """Cria um novo usuário respeitando a hierarquia."""
        target_nivel = dados.get('nivel', 'usuario')
        empresa      = dados.get('empresa', '')
        departamento = dados.get('departamento', '')
    
        if not self.can_create_nivel(admin, target_nivel):
            return False, f"Você não tem permissão para criar usuários com nível '{target_nivel}'"
    
        ok, motivo = self.validate_escopo_for_new_user(admin, empresa, departamento)
        if not ok:
            return False, motivo
    
        # Admin empresa: força sua própria empresa
        if admin.is_admin_empresa and not admin.is_admin_global:
            dados['empresa'] = admin.escopo_empresa or admin.empresa
    
        # Admin depto: força empresa e valida que o departamento informado é um dos seus
        if admin.is_admin_depto:
            meus_deptos = admin.escopo_departamentos or ([admin.departamento] if admin.departamento else [])
            dados['empresa'] = admin.escopo_empresa or admin.empresa
            # Se admin tem só 1 depto, força; se tiver vários, o front deve informar qual
            if len(meus_deptos) == 1:
                dados['departamento'] = meus_deptos[0]
            elif not dados.get('departamento') or dados['departamento'] not in meus_deptos:
                return False, f"Informe o departamento. Permitidos: {', '.join(meus_deptos)}"
    
        try:
            novo_usuario = User(
                nome         = dados.get('nome', ''),
                email        = dados.get('email', ''),
                departamento = dados.get('departamento', ''),
                nivel        = target_nivel,
                empresa      = dados.get('empresa', ''),
                ativo        = 'sim',
                senha        = dados.get('senha', ''),
                cargo        = dados.get('cargo', ''),
                admin_escopo = dados.get('admin_escopo', ''),
            )
            ok, msg = self.sheets.add_user(novo_usuario)
            if ok:
                logger.info(f"[AdminHierarchy] Usuário {novo_usuario.email} criado por {admin.email}")
            return ok, msg
        except Exception as e:
            logger.error(f"[AdminHierarchy] Erro ao criar usuário: {e}")
            return False, str(e)

    def editar_usuario(self, admin: User, target_email: str, dados: dict) -> Tuple[bool, str]:
        """Edita dados de um usuário existente."""
        target = self._find_user(target_email)
        if not target:
            return False, "Usuário não encontrado"
        if not self.can_manage_user(admin, target):
            return False, "Sem permissão para editar este usuário"

        # admin_depto não pode mudar departamento
        if admin.is_admin_depto and not admin.is_admin_empresa:
            dados.pop('departamento', None)
            dados.pop('empresa', None)

        try:
            ok, msg = self.sheets.update_user(target_email, **dados)
            if ok:
                logger.info(f"[AdminHierarchy] Usuário {target_email} editado por {admin.email}")
            return ok, msg
        except Exception as e:
            logger.error(f"[AdminHierarchy] Erro ao editar usuário: {e}")
            return False, str(e)

    def desativar_usuario(self, admin: User, target_email: str) -> Tuple[bool, str]:
        """Desativa um usuário."""
        target = self._find_user(target_email)
        if not target:
            return False, "Usuário não encontrado"
        if not self.can_manage_user(admin, target):
            return False, "Sem permissão para desativar este usuário"
        return self.sheets.update_user(target_email, ativo='nao')

    def reativar_usuario(self, admin: User, target_email: str) -> Tuple[bool, str]:
        """Reativa um usuário desativado."""
        target = self._find_user(target_email)
        if not target:
            return False, "Usuário não encontrado"
        if not self.can_manage_user(admin, target):
            return False, "Sem permissão para reativar este usuário"
        return self.sheets.update_user(target_email, ativo='sim')

    def reset_senha(self, admin: User, target_email: str, nova_senha: str) -> Tuple[bool, str]:
        """Redefine a senha de um usuário."""
        target = self._find_user(target_email)
        if not target:
            return False, "Usuário não encontrado"
        if not self.can_manage_user(admin, target):
            return False, "Sem permissão para redefinir senha deste usuário"
        if len(nova_senha) < 8:
            return False, "A senha deve ter pelo menos 8 caracteres"
        return self.sheets.update_user(target_email, senha=nova_senha)

    # ================================================================== #
    # OPERAÇÕES — MOVER USUÁRIO (departamento / cargo)
    # ================================================================== #

    def mover_usuario_departamento(
        self,
        admin: User,
        target_email: str,
        novo_departamento: str
    ) -> Tuple[bool, str]:
        """
        Move um usuário para outro departamento.
        Regras:
          - admin_global e admin_empresa podem mover para qualquer depto da empresa
          - admin_depto NÃO pode mover usuários entre deptos
        """
        target = self._find_user(target_email)
        if not target:
            return False, "Usuário não encontrado"
        if not self.can_manage_user(admin, target):
            return False, "Sem permissão para gerenciar este usuário"
        if admin.is_admin_depto and not admin.is_admin_empresa:
            return False, "Admin de departamento não pode mover usuários entre departamentos"
        if not novo_departamento.strip():
            return False, "Informe o departamento de destino"

        try:
            ok, msg = self.sheets.update_user(target_email, departamento=novo_departamento.strip())
            if ok:
                logger.info(
                    f"[AdminHierarchy] {target_email} movido para depto '{novo_departamento}' "
                    f"por {admin.email}"
                )
            return ok, msg
        except Exception as e:
            logger.error(f"[AdminHierarchy] mover_usuario_departamento: {e}")
            return False, str(e)

    def mover_usuario_cargo(
        self,
        admin: User,
        target_email: str,
        novo_cargo: str
    ) -> Tuple[bool, str]:
        """
        Atualiza o cargo de um usuário.
        Permite cargo vazio (para remover cargo).
        """
        target = self._find_user(target_email)
        if not target:
            return False, "Usuário não encontrado"
        if not self.can_manage_user(admin, target):
            return False, "Sem permissão para gerenciar este usuário"

        try:
            ok, msg = self.sheets.update_user(target_email, cargo=novo_cargo.strip())
            if ok:
                label = f"'{novo_cargo}'" if novo_cargo.strip() else 'sem cargo'
                logger.info(
                    f"[AdminHierarchy] {target_email} cargo alterado para {label} por {admin.email}"
                )
            return ok, msg
        except Exception as e:
            logger.error(f"[AdminHierarchy] mover_usuario_cargo: {e}")
            return False, str(e)

    # ================================================================== #
    # OPERAÇÕES — DEPARTAMENTOS
    # ================================================================== #

    def criar_departamento(self, empresa: str, nome: str, descricao: str = '') -> tuple:
        """Cria um departamento na empresa."""
        try:
            existentes = self.get_manageable_departamentos_raw(empresa)
            if nome in existentes:
                return False, f"Departamento '{nome}' já existe nesta empresa"
            ok, msg = self.sheets.add_departamento(empresa, nome, descricao)
            return ok, msg
        except Exception as e:
            logger.error(f"[AdminHierarchy] criar_departamento: {e}")
            return False, str(e)

    def renomear_departamento(self, empresa: str, nome_original: str, novo_nome: str) -> tuple:
        """Renomeia departamento e atualiza usuários vinculados."""
        try:
            ok, msg = self.sheets.rename_departamento(empresa, nome_original, novo_nome)
            return ok, msg
        except Exception as e:
            logger.error(f"[AdminHierarchy] renomear_departamento: {e}")
            return False, str(e)

    def excluir_departamento(self, empresa: str, nome: str) -> tuple:
        """Remove departamento (usuários permanecem, campo departamento fica como estava)."""
        try:
            ok, msg = self.sheets.delete_departamento(empresa, nome)
            return ok, msg
        except Exception as e:
            logger.error(f"[AdminHierarchy] excluir_departamento: {e}")
            return False, str(e)

    # ================================================================== #
    # OPERAÇÕES — CARGOS
    # ================================================================== #

    def criar_cargo(self, empresa: str, cargo: str) -> tuple:
        """Adiciona cargo ao catálogo da empresa."""
        try:
            ok, msg = self.sheets.add_cargo(empresa, cargo)
            return ok, msg
        except Exception as e:
            logger.error(f"[AdminHierarchy] criar_cargo: {e}")
            return False, str(e)

    def excluir_cargo(self, empresa: str, cargo: str) -> tuple:
        """Remove cargo do catálogo da empresa."""
        try:
            ok, msg = self.sheets.delete_cargo(empresa, cargo)
            return ok, msg
        except Exception as e:
            logger.error(f"[AdminHierarchy] excluir_cargo: {e}")
            return False, str(e)

    # ================================================================== #
    # HELPERS PRIVADOS
    # ================================================================== #

    def _find_user(self, email: str) -> Optional[User]:
        """Busca um usuário pelo email."""
        try:
            todos = self.sheets.get_all_users() or []
            for u in todos:
                if u.email.lower() == email.lower():
                    return u
            return None
        except Exception:
            return None

    def get_cargos_departamento(self, admin: User, empresa: str, departamento: str = None) -> list:
        """
        Retorna cargos filtrados pelo escopo do admin.
        - admin_global / admin_empresa → todos os cargos da empresa
        - admin_depto → apenas cargos usados nos seus departamentos
        """
        # Verifica escopo básico
        if not admin.pode_ver_empresa(empresa):
            return []
    
        # Admin depto: filtra pelos departamentos do escopo
        if admin.is_admin_depto and not admin.is_admin_empresa:
            meus_deptos = admin.escopo_departamentos
            if departamento and departamento not in meus_deptos:
                return []
            deptos_filtro = [departamento] if departamento else meus_deptos
    
            # Extrai cargos únicos dos usuários que estão nesses deptos
            try:
                usuarios = self.sheets.get_all_users() or []
                cargos = sorted(set(
                    u.cargo for u in usuarios
                    if u.empresa == empresa
                    and u.departamento in deptos_filtro
                    and u.cargo
                ))
                return cargos
            except Exception as e:
                logger.warning(f"[AdminHierarchy] get_cargos_departamento: {e}")
                return []
    
        # Para os demais níveis: retorna todos da empresa
        return self.get_cargos_empresa(empresa)
