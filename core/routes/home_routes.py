"""
core/routes/home_routes.py - VERSÃO CORRIGIDA
Adiciona user_permissions ao render_template para evitar erro Jinja2
"""

from flask import Blueprint, render_template, session, redirect, url_for, flash
import logging

from utils.decorators import login_required, set_theme

logger = logging.getLogger(__name__)

# Blueprint de rotas globais
home_bp = Blueprint('home', __name__)

@home_bp.route('/')
@set_theme('multisolution')
def index():
    """
    Página inicial - Redireciona baseado no estado de autenticação
    
    Lógica:
    - Se NÃO logado → Login
    - Se logado com 1 módulo → Dashboard daquele módulo
    - Se logado com múltiplos → Portal multi-soluções
    """
    # Se não estiver logado, redireciona para login
    if 'user_email' not in session:
        return redirect(url_for('auth.login'))

    permissions = session.get('permissions', {})
    allowed_modules = permissions.get('modules', [])

    # Se o usuário tem acesso a apenas UM módulo, redireciona direto para o dashboard dele
    if len(allowed_modules) == 1:
        module = allowed_modules[0]
        try:
            if module == 'helpdesk':
                logger.info(f"Usuário {session['user_email']} redirecionado para o HelpDesk (acesso único).")
                return redirect(url_for('tickets.dashboard'))
            elif module == 'wms':
                logger.info(f"Usuário {session['user_email']} redirecionado para o WMS (acesso único).")
                if session.get('user_level', '').strip() == 'transportadora':
                    return redirect(url_for('wms_asn.portal_transportadora'))
                return redirect(url_for('wms.index'))
        except Exception as e:
            logger.error(f"Erro ao redirecionar para {module}: {e}")
            flash(f'Módulo {module.upper()} está temporariamente indisponível. Mostrando portal.', 'warning')
            # Continua para mostrar o portal
    
    # Se tiver acesso a mais de um módulo (ou nenhum), mostra o portal de soluções
    logger.info(f"Usuário {session['user_email']} na home multi-soluções.")
    
    # CORREÇÃO: Passar user_permissions para o template
    return render_template('home_multisolucoes.html', user_permissions=permissions)

@home_bp.route('/about')
def about():
    """
    Página sobre o sistema
    
    Pode ser acessada sem login (informações públicas)
    """
    return render_template('about.html')


@home_bp.route('/help')
@login_required
def help_page():
    """
    Página de ajuda do sistema
    
    Requer login para ver instruções personalizadas
    """
    user_level = session.get('user_level', 'usuario')
    permissions = session.get('permissions', {})
    
    return render_template('help.html', 
                         user_level=user_level,
                         permissions=permissions)


@home_bp.route('/select-module')
@login_required
@set_theme('multisolution')
def select_module():
    """
    Página de seleção de módulo
    
    Permite usuário escolher qual módulo quer acessar
    (útil para usuários com múltiplos acessos)
    """
    permissions = session.get('permissions', {})
    allowed_modules = permissions.get('modules', [])
    
    # Se tem apenas 1 módulo, redireciona direto
    # Se tem apenas 1 módulo, redireciona direto
    if len(allowed_modules) == 1:
        module = allowed_modules[0]
        try:
            if module == 'helpdesk':
                return redirect(url_for('tickets.dashboard'))
            elif module == 'wms':
                nivel = session.get('user_level', '')
                if nivel == 'transportadora':
                    return redirect(url_for('wms_asn.portal_transportadora'))
                return redirect(url_for('wms.index'))
        except Exception as e:
            logger.error(f"Erro ao redirecionar para {module}: {e}")
            flash(f'Módulo {module.upper()} está temporariamente indisponível.', 'error')
            return render_template('select_module.html', 
                                 allowed_modules=allowed_modules,
                                 user_permissions=permissions)
    
    # Se tem múltiplos, mostra opções
    return render_template('select_module.html', 
                         allowed_modules=allowed_modules,
                         user_permissions=permissions)


@home_bp.route('/switch-module/<module>')
@login_required
def switch_module(module):
    """
    Permite trocar de módulo sem fazer logout
    
    Args:
        module: Nome do módulo (helpdesk, wms, multisolution)
    """
    permissions = session.get('permissions', {})
    allowed_modules = permissions.get('modules', [])
    
    # Validar se tem acesso ao módulo
    if module not in allowed_modules and module != 'multisolution':
        logger.warning(f"Usuário {session.get('user_email')} tentou acessar módulo sem permissão: {module}")
        flash(f'Você não tem permissão para acessar o módulo {module.upper()}.', 'error')
        return redirect(url_for('home.index'))
    
    # Atualizar tema na sessão
    session['active_theme'] = module
    
    # Redirecionar para dashboard do módulo com tratamento de erro
    try:
        if module == 'helpdesk':
            return redirect(url_for('tickets.dashboard'))
        elif module == 'wms':
            return redirect(url_for('wms.dashboard'))
        else:  # multisolution
            return redirect(url_for('home.index'))
    except Exception as e:
        logger.error(f"Erro ao trocar para módulo {module}: {e}")
        flash(f'Módulo {module.upper()} está temporariamente indisponível.', 'error')
        return redirect(url_for('home.index'))


# ERROR HANDLERS (podem ser registrados globalmente no app.py)

@home_bp.app_errorhandler(404)
def page_not_found(error):
    """Handler para erro 404"""
    logger.warning(f"404: {error}")
    return render_template('errors/404.html'), 404


@home_bp.app_errorhandler(500)
def internal_error(error):
    """Handler para erro 500"""
    logger.error(f"500: {error}")
    return render_template('errors/500.html'), 500

