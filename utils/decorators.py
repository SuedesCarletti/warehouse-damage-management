"""
utils/decorators.py
Decorators de autenticação e autorização — com suporte à hierarquia de 3 níveis admin

HIERARQUIA:
  admin_global  (100) → acessa tudo
  admin_empresa  (80) → acessa apenas sua empresa
  admin_depto    (60) → acessa apenas seu departamento
  gestor         (40) → usuário funcional com visibilidade ampla
  usuario        (20) → usuário final
"""

import logging
from functools import wraps
from flask import session, redirect, url_for, flash, jsonify, request

logger = logging.getLogger(__name__)


# HELPERS INTERNOS

def _get_user_nivel() -> str:
    return session.get('user_level', '').strip()  # ← strip aqui

def _get_user_empresa() -> str:
    return session.get('user_empresa', '')

def _get_user_depto() -> str:
    return session.get('user_department', '')

def _get_admin_escopo() -> str:
    return session.get('admin_escopo', '')

def _is_logged_in() -> bool:
    return 'user_email' in session


# DECORATORS EXISTENTES (mantidos para backward-compat)

def set_theme(theme_name):
    """Define o tema ativo na sessão para renderização do template."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            session['active_theme'] = theme_name
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def login_required(f):
    """Requer que usuário esteja logado"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not _is_logged_in():
            flash('Você precisa estar logado para acessar esta página.', 'warning')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function

def module_required(module_name):
    """Valida se o usuário tem acesso a um módulo específico."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not _is_logged_in():
                flash('Você precisa estar logado.', 'warning')
                return redirect(url_for('auth.login'))

            permissions = session.get('permissions', {})
            allowed_modules = permissions.get('modules', [])

            if module_name not in allowed_modules:
                logger.warning(f"Acesso negado: {session.get('user_email')} → {module_name}")
                flash(f'Você não tem permissão para acessar o módulo {module_name.upper()}.', 'error')
                if allowed_modules:
                    return redirect(url_for('home.index'))
                session.clear()
                flash('Seu usuário não tem permissão para acessar nenhum módulo.', 'error')
                return redirect(url_for('auth.login'))

            return f(*args, **kwargs)
        return decorated_function
    return decorator

def screen_required(module_name, screen_name, nivel='visualizar'):
    """Valida se o usuário tem acesso a uma tela específica no nível exigido."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not _is_logged_in():
                flash('Você precisa estar logado.', 'warning')
                return redirect(url_for('auth.login'))

            permissions = session.get('permissions', {})
            allowed_modules = permissions.get('modules', [])

            if module_name not in allowed_modules:
                flash(f'Você não tem acesso ao módulo {module_name.upper()}.', 'error')
                return redirect(url_for('home.index'))

            screens = permissions.get('screens', {}).get(module_name, {})
            valor = screens.get('*') or screens.get(screen_name)

            if not valor:
                has_access = False
            elif nivel == 'visualizar':
                has_access = True
            else:
                has_access = valor in ('editar', 'admin') or valor is True

            if not has_access:
                logger.warning(f"Acesso negado à tela: {session.get('user_email')} → {module_name}.{screen_name} (nivel={nivel})")
                flash('Você não tem permissão para esta ação.', 'error')
                if module_name == 'helpdesk':
                    return redirect(url_for('tickets.dashboard'))
                elif module_name == 'wms':
                    # Transportadora nunca tem 'avarias_dashboard' — mandar pra wms.index
                    # (que exige essa mesma tela) causava loop infinito de redirecionamento
                    # quando a própria wms.index era a rota negada.
                    if session.get('user_level', '').strip() == 'transportadora':
                        return redirect(url_for('wms_asn.portal_transportadora'))
                    return redirect(url_for('wms.index'))
                return redirect(url_for('home.index'))

            return f(*args, **kwargs)
        return decorated_function
    return decorator

def has_screen_permission(modulo: str, tela: str, nivel: str = 'visualizar') -> bool:
    """Check if session has screen access at the given level. nivel: 'visualizar' | 'editar'"""
    permissions = session.get('permissions', {})
    screens = permissions.get('screens', {}).get(modulo, {})
    valor = screens.get('*') or screens.get(tela)
    if not valor:
        return False
    if nivel == 'visualizar':
        return True
    return valor in ('editar', 'admin') or valor is True


def screen_action_required(modulo: str, tela: str, nivel: str = 'editar'):
    """Decorator requiring a specific access level on a screen."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not _is_logged_in():
                flash('Você precisa estar logado.', 'warning')
                return redirect(url_for('auth.login'))
            if not has_screen_permission(modulo, tela, nivel):
                if request.is_json or request.path.startswith('/api/'):
                    return jsonify({'success': False, 'error': 'Sem permissão para esta ação'}), 403
                flash('Você não tem permissão para esta ação.', 'error')
                return redirect(url_for('home.index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def admin_required(f):
    """Backward-compat: requer gestor ou qualquer admin."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not _is_logged_in():
            flash('Você precisa estar logado.', 'warning')
            return redirect(url_for('auth.login'))
        nivel = _get_user_nivel()
        if nivel not in ('admin', 'gestor', 'admin_global', 'admin_empresa', 'admin_depto'):
            flash('Acesso negado. Você não tem permissão de administrador.', 'error')
            return redirect(url_for('tickets.dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def manager_required(f):
    """Requer gestor ou admin"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not _is_logged_in():
            flash('Você precisa estar logado.', 'warning')
            return redirect(url_for('auth.login'))
        if _get_user_nivel() not in ('gestor', 'admin', 'admin_global', 'admin_empresa', 'admin_depto'):
            flash('Acesso negado. Apenas gestores podem acessar.', 'error')
            return redirect(url_for('tickets.dashboard'))
        return f(*args, **kwargs)
    return decorated_function


# DECORATORS DA HIERARQUIA ADMIN (3 NÍVEIS)

def admin_global_required(f):
    """
    Nível 1 — Requer admin_global.
    Apenas o administrador global do sistema pode acessar.

    Uso:
        @admin_global_required
        def gerenciar_empresas():
            ...
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not _is_logged_in():
            flash('Você precisa estar logado.', 'warning')
            return redirect(url_for('auth.login'))

        nivel = _get_user_nivel()
        if nivel != 'admin_global':
            logger.warning(f"[admin_global_required] Acesso negado: {session.get('user_email')} (nível: {nivel})")
            flash('Acesso restrito ao administrador global do sistema.', 'error')
            return redirect(url_for('home.index'))

        return f(*args, **kwargs)
    return decorated_function


def admin_empresa_required(f):
    """
    Nível 2 — Requer admin_empresa ou superior (admin_global).

    Injeta automaticamente no kwargs o escopo de empresa:
      - admin_global → escopo_empresa = None  (vê todas)
      - admin_empresa → escopo_empresa = "Matriz"  (vê só a sua)

    Uso:
        @admin_empresa_required
        def listar_usuarios():
            escopo = session.get('admin_escopo_empresa')  # filtra por este valor
            ...
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not _is_logged_in():
            flash('Você precisa estar logado.', 'warning')
            return redirect(url_for('auth.login'))

        nivel = _get_user_nivel()
        if nivel not in ('admin_global', 'admin_empresa', 'admin'):
            logger.warning(f"[admin_empresa_required] Acesso negado: {session.get('user_email')} (nível: {nivel})")
            flash('Acesso restrito a administradores de empresa.', 'error')
            return redirect(url_for('home.index'))

        # Calcular escopo de empresa ativo para esta requisição
        if nivel == 'admin_global':
            session['_admin_escopo_empresa'] = None       # Sem restrição
        else:
            escopo = _get_admin_escopo()
            session['_admin_escopo_empresa'] = escopo.split(':')[0].strip() if escopo else _get_user_empresa()

        return f(*args, **kwargs)
    return decorated_function


def admin_depto_required(f):
    """
    Nível 3 — Requer admin_depto ou superior.

    Injeta na sessão:
      - _admin_escopo_empresa: empresa do escopo
      - _admin_escopo_depto:   departamento do escopo (None para admins globais/empresa)

    Uso:
        @admin_depto_required
        def gerenciar_equipe():
            empresa = session.get('_admin_escopo_empresa')
            depto   = session.get('_admin_escopo_depto')
            ...
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not _is_logged_in():
            flash('Você precisa estar logado.', 'warning')
            return redirect(url_for('auth.login'))

        nivel = _get_user_nivel()
        if nivel not in ('admin_global', 'admin_empresa', 'admin', 'admin_depto'):
            logger.warning(f"[admin_depto_required] Acesso negado: {session.get('user_email')} (nível: {nivel})")
            flash('Acesso restrito a administradores.', 'error')
            return redirect(url_for('home.index'))

        escopo = _get_admin_escopo()
        partes = escopo.split(':') if escopo and escopo != '*' else []

        if nivel == 'admin_global':
            session['_admin_escopo_empresa'] = None
            session['_admin_escopo_depto'] = None
        elif nivel in ('admin_empresa', 'admin'):
            session['_admin_escopo_empresa'] = partes[0].strip() if partes else _get_user_empresa()
            session['_admin_escopo_depto'] = None  # Vê todos os deptos da empresa
        else:  # admin_depto
            session['_admin_escopo_empresa'] = partes[0].strip() if len(partes) >= 1 else _get_user_empresa()
            session['_admin_escopo_depto'] = partes[1].strip() if len(partes) >= 2 else _get_user_depto()

        return f(*args, **kwargs)
    return decorated_function


# VARIANTES API (retornam JSON)

def api_login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not _is_logged_in():
            return jsonify({'success': False, 'error': 'Não autenticado'}), 401
        return f(*args, **kwargs)
    return decorated_function

def api_admin_required(f):
    """Backward-compat: requer qualquer admin (JSON)"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not _is_logged_in():
            return jsonify({'success': False, 'error': 'Não autenticado'}), 401
        if _get_user_nivel() not in ('admin', 'admin_global', 'admin_empresa', 'admin_depto'):
            return jsonify({'success': False, 'error': 'Sem permissão'}), 403
        return f(*args, **kwargs)
    return decorated_function

def api_manager_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not _is_logged_in():
            return jsonify({'success': False, 'error': 'Não autenticado'}), 401
        if _get_user_nivel() not in ('gestor', 'admin', 'admin_global', 'admin_empresa', 'admin_depto'):
            return jsonify({'success': False, 'error': 'Sem permissão'}), 403
        return f(*args, **kwargs)
    return decorated_function

def api_admin_global_required(f):
    """API: requer admin_global (JSON)"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not _is_logged_in():
            return jsonify({'success': False, 'error': 'Não autenticado'}), 401
        if _get_user_nivel() != 'admin_global':
            return jsonify({'success': False, 'error': 'Restrito ao admin global'}), 403
        return f(*args, **kwargs)
    return decorated_function

def api_admin_empresa_required(f):
    """API: requer admin_empresa ou superior (JSON)"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not _is_logged_in():
            return jsonify({'success': False, 'error': 'Não autenticado'}), 401
        if _get_user_nivel() not in ('admin_global', 'admin_empresa', 'admin'):
            return jsonify({'success': False, 'error': 'Restrito a admin de empresa'}), 403
        return f(*args, **kwargs)
    return decorated_function

def api_admin_depto_required(f):
    """API: requer qualquer admin (JSON)"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not _is_logged_in():
            return jsonify({'success': False, 'error': 'Não autenticado'}), 401
        if _get_user_nivel() not in ('admin_global', 'admin_empresa', 'admin', 'admin_depto'):
            return jsonify({'success': False, 'error': 'Restrito a administradores'}), 403
        return f(*args, **kwargs)
    return decorated_function

def has_screen_permission(modulo: str, tela: str, nivel: str = 'visualizar') -> bool:
    """Check if session has screen access at the given level. nivel: 'visualizar' | 'editar'"""
    permissions = session.get('permissions', {})
    screens = permissions.get('screens', {}).get(modulo, {})
    valor = screens.get('*') or screens.get(tela)
    if not valor:
        return False
    if nivel == 'visualizar':
        return True
    return valor in ('editar', 'admin') or valor is True


def screen_action_required(modulo: str, tela: str, nivel: str = 'editar'):
    """Decorator requiring a specific access level on a screen."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not _is_logged_in():
                flash('Você precisa estar logado.', 'warning')
                return redirect(url_for('auth.login'))
            if not has_screen_permission(modulo, tela, nivel):
                if request.is_json or request.path.startswith('/api/'):
                    return jsonify({'success': False, 'error': 'Sem permissão para esta ação'}), 403
                flash('Você não tem permissão para esta ação.', 'error')
                return redirect(url_for('home.index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def api_module_required(module_name):
    """Versão API do module_required — retorna JSON"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not _is_logged_in():
                return jsonify({'success': False, 'error': 'Não autenticado'}), 401
            permissions = session.get('permissions', {})
            if module_name not in permissions.get('modules', []):
                return jsonify({'success': False, 'error': f'Sem acesso ao módulo {module_name}'}), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator
