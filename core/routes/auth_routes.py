""" 
core/routes/auth_routes.py - VERSÃO ATUALIZADA
NOVO: Suporte a alteração forçada de senha no próximo login
"""

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, current_app
import logging
import threading
import time
import ipaddress
from werkzeug.security import generate_password_hash
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)

# Serão injetados pelo app.py
auth_service = None
sheets_service = None
email_service = None
config = None

def init_auth_routes(auth_svc, sheets_svc, email_svc, app_config):
    """Inicializa rotas com serviços"""
    global auth_service, sheets_service, email_service, config
    auth_service = auth_svc
    sheets_service = sheets_svc
    email_service = email_svc
    config = app_config

def _preload_user_data(user, target_module: str):
    """
    PRÉ-CARREGA DADOS em background thread após login bem-sucedido.
    
    Dispara imediatamente após o login. Enquanto o usuário navega para
    o dashboard (redirect + render da página), os dados já estão sendo
    buscados. Quando a primeira requisição real chega, o cache já está
    quente.
    
    Args:
        user: Objeto User autenticado (contém empresa, módulos, etc.)
        target_module: Módulo para o qual o usuário está sendo redirecionado
    """
    def _worker():
        try:
            logger.info(f"[Preload] Iniciando preload para {user.email} → módulo: {target_module}")
            start = time.time()
            
            # SEMPRE: Refresh cache de usuários
            if sheets_service and sheets_service.connected:
                try:
                    _ = sheets_service.get_all_users()
                    logger.info("[Preload] Cache usuários verificado/carregado")
                except Exception as e:
                    logger.warning(f"[Preload] Erro ao preload usuários: {e}")
            
            elapsed = time.time() - start
            logger.info(f"[Preload] Concluído em {elapsed:.2f}s")
            
        except Exception as e:
            logger.error(f"[Preload] Erro: {e}")
    
    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()


# ROTAS DE AUTENTICAÇÃO

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Página de login"""
    
    # Se já está logado, redirecionar
    if 'user_email' in session:
        # NOVO: Verificar se precisa alterar senha
        if session.get('requires_password_change', False):
            return redirect(url_for('auth.change_password_required'))
        
        # Redirecionar para módulo apropriado
        permissions = session.get('permissions', {})
        allowed_modules = permissions.get('modules', [])
        
        if allowed_modules:
            if len(allowed_modules) == 1:
                module = allowed_modules[0]
                if module == 'helpdesk':
                    return redirect(url_for('tickets.dashboard'))
                elif module == 'wms':
                    if session.get('user_level', '').strip() == 'transportadora':
                        return redirect(url_for('wms_asn.portal_transportadora'))
                    return redirect(url_for('wms.index'))
            return redirect(url_for('home.index'))

        # Se não tem módulos, fazer logout
        session.clear()
        flash('Seu usuário não tem permissão para acessar nenhum módulo. Contate o administrador.', 'error')
        return redirect(url_for('auth.login'))
    
    # Processar login
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        selected_module = request.form.get('module', '').strip()
        
        # 1. Autenticar usuário
        success, user, message, flash_category = auth_service.authenticate(email, password)
        if not success:
            session.clear()
            flash(message, flash_category)
            return render_template('login.html')

        allowed_modules = user.permissions.get('modules', [])

        # 2. Validar se o usuário tem permissão para acessar algum módulo
        if not allowed_modules:
            session.clear()
            flash('Seu usuário não tem permissão para acessar nenhum módulo. Contate o administrador.', 'error')
            return redirect(url_for('auth.login'))

        # 3. FLUXO SIMPLIFICADO: Se tem acesso a apenas 1 módulo, redirecionar direto
        if len(allowed_modules) == 1:
            target_module = allowed_modules[0]
            logger.info(f"Usuário {email} tem acesso a apenas 1 módulo: {target_module}. Redirecionando automaticamente.")
        
        # 4. Se tem acesso a múltiplos módulos
        elif len(allowed_modules) > 1:
            # Se não selecionou nenhum módulo, redirecionar para home (multisolution)
            if not selected_module:
                target_module = 'multisolution'
                logger.info(f"Usuário {email} tem múltiplos acessos e não selecionou módulo. Redirecionando para portal.")
            
            # Se selecionou 'multisolution', permitir acesso ao portal
            elif selected_module == 'multisolution':
                target_module = 'multisolution'
            
            # Se selecionou um módulo específico, validar se tem permissão
            elif selected_module in allowed_modules:
                target_module = selected_module
            
            # Se selecionou um módulo sem permissão, redirecionar para portal
            else:
                target_module = 'multisolution'
                flash(f'Você não tem acesso ao módulo {selected_module.upper()}. Redirecionando para o portal.', 'warning')
        
        else:
            # Caso improvável, mas seguro
            session.clear()
            flash('Erro ao processar permissões. Contate o administrador.', 'error')
            return redirect(url_for('auth.login'))
        
        # 5. Determinar a rota de redirecionamento com base no módulo validado
        if target_module == 'multisolution':
            redirect_to = 'home.index'
        elif target_module == 'helpdesk':
            redirect_to = 'tickets.dashboard'
        elif target_module == 'wms':
            # Transportadora vai direto ao portal ASN
            if (user.nivel or '').strip() == 'transportadora':
                redirect_to = 'wms_asn.portal_transportadora'
            else:
                redirect_to = 'wms.index'
        else:
            # Módulo não mapeado explicitamente: fallback para o portal principal
            # (evita erro para empresas com nomes de módulo inesperados)
            logger.warning(
                f"⚠️ [AUTH] Módulo '{target_module}' não mapeado para rota. "
                f"Módulos disponíveis do usuário: {allowed_modules}. "
                f"Redirecionando para home."
            )
            redirect_to = 'home.index'

        # 6. LIMPAR sessão antiga e salvar novas informações
        session.clear()
        session['user_email']      = user.email
        session['user_name']       = user.nome
        session['user_level']      = (user.nivel or '').strip()
        session['user_department'] = user.departamento
        session['user_empresa']    = user.empresa
        session['admin_escopo']    = user.admin_escopo or ''
        session['permissions']     = user.permissions
        session['active_theme']    = target_module
        
        # NOVO: Verificar se precisa alterar senha
        session['requires_password_change'] = user.precisa_alterar_senha
        
        # 7. NOVO: Se precisa alterar senha, redirecionar para página de alteração
        if user.precisa_alterar_senha:
            logger.info(f"Usuário {email} precisa alterar senha. Redirecionando...")
            flash('Por segurança, você precisa alterar sua senha antes de continuar.', 'warning')
            return redirect(url_for('auth.change_password_required'))
        
        # 8. Pré-carregar dados em background
        _preload_user_data(user, target_module)
        
        # 9. Tentar redirecionar com tratamento de erro
        try:
            logger.info(f"Login bem-sucedido: {email} | Empresa: {user.empresa} | Tema: {target_module} | Rota: {redirect_to}")
            flash('Login realizado com sucesso!', 'success')
            return redirect(url_for(redirect_to))
        except Exception as e:
            logger.error(f"Erro ao redirecionar para {redirect_to}: {e}")
            logger.error(f"   Módulo alvo: {target_module}")
            logger.error(f"   Módulos permitidos: {allowed_modules}")
            
            # Fallback: redirecionar para home
            flash(f'Login realizado, mas o módulo {target_module.upper()} está temporariamente indisponível. Redirecionando para home.', 'warning')
            try:
                return redirect(url_for('home.index'))
            except Exception as e2:
                logger.error(f"Erro crítico: nem home.index está disponível: {e2}")
                flash('Erro crítico no sistema. Contate o administrador.', 'error')
                session.clear()
                return redirect(url_for('auth.login'))
    
    return render_template('login.html')


# NOVO: ALTERAÇÃO FORÇADA DE SENHA

@auth_bp.route('/change-password-required', methods=['GET'])
def change_password_required():
    """
    Página que mostra modal de alteração de senha obrigatória.
    Usuário não pode fechar sem alterar.
    """
    # Verificar se está logado
    if 'user_email' not in session:
        flash('Você precisa estar logado.', 'warning')
        return redirect(url_for('auth.login'))
    
    # Verificar se realmente precisa alterar senha
    if not session.get('requires_password_change', False):
        # Se não precisa mais, redirecionar para dashboard
        permissions = session.get('permissions', {})
        allowed_modules = permissions.get('modules', [])
        
        if allowed_modules:
            if len(allowed_modules) == 1:
                module = allowed_modules[0]
                if module == 'helpdesk':
                    return redirect(url_for('tickets.dashboard'))
                elif module == 'wms':
                    # Mesma checagem do /login: transportadora vai pro portal, não pro dashboard
                    # (o dashboard exige a tela 'avarias_dashboard', que transportadora não tem —
                    # mandar transportadora pra lá causava loop de redirecionamento).
                    if session.get('user_level', '').strip() == 'transportadora':
                        return redirect(url_for('wms_asn.portal_transportadora'))
                    return redirect(url_for('wms.index'))
            return redirect(url_for('home.index'))

        return redirect(url_for('auth.login'))

    # Renderizar página com modal
    return render_template('change_password_required.html')


@auth_bp.route('/change-password', methods=['POST'])
def api_change_password_forced():
    """
    API para alterar senha quando forçado pelo sistema.
    """
    try:
        # Verificar se está logado
        if 'user_email' not in session:
            return jsonify({
                'success': False,
                'error': 'Sessão expirada. Faça login novamente.'
            }), 401
        
        # Verificar se realmente precisa alterar
        if not session.get('requires_password_change', False):
            return jsonify({
                'success': False,
                'error': 'Você não precisa alterar sua senha no momento.'
            }), 400
        
        data = request.get_json()
        current_password = data.get('current_password', '')
        new_password = data.get('new_password', '')
        confirm_password = data.get('confirm_password', '')
        
        # Validações básicas
        if not current_password or not new_password or not confirm_password:
            return jsonify({
                'success': False,
                'error': 'Todos os campos são obrigatórios.'
            }), 400
        
        if new_password != confirm_password:
            return jsonify({
                'success': False,
                'error': 'As senhas não coincidem.'
            }), 400
        
        # Alterar senha
        email = session['user_email']
        success, message = auth_service.change_password_forced(
            email, 
            current_password, 
            new_password
        )
        
        if success:
            # Remover flag de alteração obrigatória da sessão
            session['requires_password_change'] = False
            
            logger.info(f"Senha alterada com sucesso (forçada): {email}")
            
            # Determinar URL de redirecionamento
            permissions = session.get('permissions', {})
            allowed_modules = permissions.get('modules', [])
            
            redirect_url = url_for('home.index')
            if len(allowed_modules) == 1:
                module = allowed_modules[0]
                if module == 'helpdesk':
                    redirect_url = url_for('tickets.dashboard')
                elif module == 'wms':
                    # Mesma checagem do /login: transportadora vai pro portal, não pro
                    # dashboard (que exige a tela 'avarias_dashboard' — sem isso, loop).
                    if session.get('user_level', '').strip() == 'transportadora':
                        redirect_url = url_for('wms_asn.portal_transportadora')
                    else:
                        redirect_url = url_for('wms.index')
            
            return jsonify({
                'success': True,
                'message': message,
                'redirect_url': redirect_url
            })
        else:
            logger.warning(f"Falha ao alterar senha (forçada): {email} - {message}")
            return jsonify({
                'success': False,
                'error': message
            }), 400
            
    except Exception as e:
        logger.error(f"Erro na API de alteração forçada: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': 'Erro interno do servidor.'
        }), 500


# OUTRAS ROTAS

@auth_bp.route('/api/check-user-modules', methods=['POST'])
def check_user_modules():
    """
    API endpoint para verificar quais módulos um usuário tem acesso.
    Usado no login para adaptar dinamicamente a UI.
    """
    try:
        data = request.get_json()
        email = data.get('email', '').strip().lower()
        
        if not email or '@' not in email:
            return jsonify({
                'success': False,
                'error': 'Email inválido'
            }), 400
        
        # Buscar usuário
        user = sheets_service.get_user_by_email(email)
        
        if not user:
            return jsonify({
                'success': True,
                'modules': [],
                'message': 'Usuário não encontrado ou sem permissões'
            })
        
        if not user.is_active:
            return jsonify({
                'success': True,
                'modules': [],
                'message': 'Usuário inativo'
            })
        
        # Retornar módulos permitidos
        allowed_modules = user.permissions.get('modules', [])
        
        return jsonify({
            'success': True,
            'modules': allowed_modules,
            'user_name': user.nome,
            'company': user.empresa,
            'message': f'Usuário encontrado com {len(allowed_modules)} módulo(s)'
        })
        
    except Exception as e:
        logger.error(f"Erro ao verificar módulos do usuário: {e}")
        return jsonify({
            'success': False,
            'error': 'Erro interno do servidor'
        }), 500


@auth_bp.route('/logout')
def logout():
    """Logout do sistema"""
    user_name = session.get('user_name', 'Usuário')
    session.clear()
    flash(f'Até logo, {user_name}!', 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """Página para solicitar reset de senha."""
    if request.method == 'POST':
        email_input = request.form.get('email', '').strip()
        logger.info(f"[AUTH] Solicitado reset de senha para: '{email_input}'")
        
        # Busca case-insensitive garantida (ignora maiúsculas/minúsculas da planilha)
        users = sheets_service.get_all_users()
        user = next((u for u in users if u.email.strip().lower() == email_input.lower()), None)

        if not user:
            logger.warning(f"[AUTH] Usuário '{email_input}' não encontrado na aba de Usuários.")

        if user and email_service and email_service.enabled:
            logger.info(f"[AUTH] Usuário encontrado. Preparando envio de e-mail para {user.email}...")
            token = auth_service.generate_reset_token(user.email)
            
            # Resolve a URL base para o link de redefinição de senha.
            # Prioridade:
            #   1. INTERNAL_BASE_URL (se configurado e o acesso vem de IP privado)
            #   2. Auto-detecção via headers do browser (Origin → Referer → request.host)
            #      quando o IP do cliente é privado (evita jogar o usuário interno pro NAT)
            #   3. BASE_URL (se configurado com valor real, não localhost) — usuários externos
            #   4. Auto-detecção via headers como fallback final
            client_ip = (
                request.headers.get('X-Real-IP')
                or request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
                or request.remote_addr
                or ''
            )

            def _is_private_ip(ip_str):
                try:
                    return ipaddress.ip_address(ip_str).is_private
                except ValueError:
                    return False

            def _detect_from_request():
                origin = request.headers.get('Origin')
                referer = request.headers.get('Referer')
                if origin and origin != 'null':
                    return origin
                if referer:
                    parsed = urlparse(referer)
                    return f"{parsed.scheme}://{parsed.netloc}"
                forwarded_host = request.headers.get('X-Forwarded-Host')
                if forwarded_host:
                    scheme = request.headers.get('X-Forwarded-Proto', request.scheme)
                    return f"{scheme}://{forwarded_host}"
                scheme = request.headers.get('X-Forwarded-Proto', request.scheme)
                return f"{scheme}://{request.host}"

            internal_base_url = config.get('INTERNAL_BASE_URL', '').strip().rstrip('/')
            configured_base_url = config.get('BASE_URL', '').strip().rstrip('/')
            is_internal = _is_private_ip(client_ip)

            if internal_base_url and is_internal:
                # URL interna explícita configurada
                base_url = internal_base_url
                logger.info(f"[AUTH] IP interno ({client_ip}), usando INTERNAL_BASE_URL: {base_url}")
            elif is_internal:
                # IP interno sem INTERNAL_BASE_URL: usa o endereço real do browser
                # (evita mandar o usuário para o NAT, que pode não funcionar internamente)
                base_url = _detect_from_request()
                logger.info(f"[AUTH] IP interno ({client_ip}), URL detectada pelo browser: {base_url}")
            elif configured_base_url and configured_base_url not in ('http://localhost:5000', 'http://localhost'):
                # URL pública configurada: tem prioridade sobre Origin/Referer
                # (evita que a porta 8080 do browser sobrescreva a URL pública correta)
                base_url = configured_base_url
                logger.info(f"[AUTH] Usando BASE_URL configurada: {base_url}")
            else:
                base_url = _detect_from_request()
            
            base_url = base_url.rstrip('/')
            logger.info(f"[AUTH] Gerando link de redefinição de senha com a URL Base: {base_url}")
            email_sent, email_message = email_service.send_password_reset_email(user.email, token, base_url)
            
            if email_sent:
                logger.info(f"[AUTH] Solicitação de reset enfileirada: {base_url}/reset-password/<token>")
            else:
                logger.error(f"[AUTH] Falha ao enfileirar email de reset para {user.email}. Motivo: {email_message}")

        elif user and (not email_service or not email_service.enabled):
            logger.warning(f"[AUTH] E-mail não enviado para {user.email} pois o serviço SMTP está desabilitado (faltam credenciais).")
        
        flash('Se o seu e-mail estiver em nosso sistema, você receberá um link para redefinir sua senha.', 'info')
        return redirect(url_for('auth.login'))
    
    return render_template('forgot_password.html')


@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    """Página para efetivamente redefinir a senha com um token."""
    email = auth_service.verify_reset_token(token)
    if not email:
        flash('O link de redefinição de senha é inválido ou expirou.', 'error')
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        password = request.form.get('password')
        password_confirm = request.form.get('password_confirm')

        # ATUALIZADO: Usar validação de senha forte do auth_service
        valid, error_msg = auth_service.validate_password_strength(password)
        if not valid:
            flash(error_msg, 'error')
            return render_template('reset_password.html', token=token)

        if password != password_confirm:
            flash('As senhas não coincidem.', 'error')
            return render_template('reset_password.html', token=token)

        success, message = auth_service.reset_password(
            email,
            password,
            mark_for_change=False  # Usuário já escolheu a senha via link; não força nova troca
        )

        if success:
            flash('Sua senha foi redefinida com sucesso!', 'success')
            return redirect(url_for('auth.login'))
        else:
            flash(f'Ocorreu um erro ao redefinir sua senha: {message}', 'error')
    
    return render_template('reset_password.html', token=token)
