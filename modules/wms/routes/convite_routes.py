"""
modules/wms/routes/convite_routes.py
Rotas de convite e auto-cadastro de transportadoras.

Blueprint : wms_convite
Prefix    : /wms/convite  (autenticado — operador)
            /cadastro/transportadora  (público — sem login)

Rotas autenticadas (operador):
  POST /wms/convite/api/gerar    → Gera token e retorna link
  GET  /wms/convite/api/lista    → Lista convites da empresa

Rota pública:
  GET  /cadastro/transportadora/<token>   → Página de auto-cadastro
  POST /cadastro/transportadora/<token>   → Processa cadastro
"""

import logging
from flask import (
    Blueprint, render_template, request, jsonify,
    session, redirect, url_for, flash
)

from utils.decorators import module_required
from ..services.convite_service import ConviteService

logger = logging.getLogger(__name__)

# Blueprints — dois blueprints separados para prefixos distintos
convite_bp  = Blueprint('wms_convite',  __name__)   # /wms/convite
cadastro_bp = Blueprint('wms_cadastro', __name__)   # /cadastro/transportadora

convite_service: ConviteService = None


def init_convite_routes(sheets_service):
    """Chamado pelo app.py durante o bootstrap."""
    global convite_service
    convite_service = ConviteService(sheets_service)
    logger.info("Rotas de convite inicializadas")


# APIS AUTENTICADAS — operador WMS

@convite_bp.route('/api/gerar', methods=['POST'])
@module_required('wms')
def api_gerar_convite():
    """
    POST /wms/convite/api/gerar
    Gera um token de convite para a empresa do operador logado.
    Retorna o link completo pronto para copiar.
    """
    empresa    = session.get('user_empresa', '')
    criado_por = session.get('user_email', '')

    if not empresa:
        return jsonify({'success': False, 'error': 'Empresa não identificada na sessão'}), 400

    success, msg, token = convite_service.gerar_convite(
        empresa=empresa,
        criado_por=criado_por,
    )

    if not success:
        return jsonify({'success': False, 'error': msg}), 500

    # Convite é sempre para acesso externo: usa a URL pública (NAT), nunca o IP interno.
    from flask import current_app
    import os
    import ipaddress
    from urllib.parse import urlparse

    def _eh_interna(url):
        host = urlparse(url).hostname or ''
        if host in ('localhost', ''):
            return True
        try:
            return ipaddress.ip_address(host).is_private
        except ValueError:
            return False

    base_url = (
        os.environ.get('PUBLIC_BASE_URL', '').strip()
        or current_app.config.get('BASE_URL', '').strip()
    ).rstrip('/')

    if not base_url or _eh_interna(base_url):
        logger.error("Convite: PUBLIC_BASE_URL não configurada com o endereço NAT público")
        return jsonify({
            'success': False,
            'error': 'URL pública (NAT) não configurada. Defina PUBLIC_BASE_URL no servidor.',
        }), 500

    link = f"{base_url}/cadastro/transportadora/{token}"

    logger.info(f"Convite gerado por {criado_por} para {empresa}")
    return jsonify({
        'success': True,
        'message': msg,
        'link': link,
        'token': token,
        'expira_em': '72 horas',
    })


@convite_bp.route('/api/lista', methods=['GET'])
@module_required('wms')
def api_lista_convites():
    """
    GET /wms/convite/api/lista
    Lista convites gerados pela empresa do operador logado.
    """
    empresa = session.get('user_empresa', '')
    convites = convite_service.listar_convites(empresa=empresa) if convite_service else []
    return jsonify({'success': True, 'convites': convites, 'total': len(convites)})


# ROTA PÚBLICA — auto-cadastro da transportadora

@cadastro_bp.route('/transportadora/<token>', methods=['GET'])
def cadastro_get(token: str):
    """
    Exibe o formulário de auto-cadastro se o token for válido.
    Acessível sem login.
    """
    if not convite_service:
        return render_template('wms/convite/erro_convite.html',
                               mensagem="Serviço temporariamente indisponível."), 503

    valido, msg, convite = convite_service.validar_token(token)
    if not valido:
        return render_template('wms/convite/erro_convite.html', mensagem=msg), 400

    return render_template(
        'wms/convite/cadastro_transportadora.html',
        token=token,
        empresa=convite.get('empresa', ''),
        expira_em=convite.get('expira_em', ''),
    )


@cadastro_bp.route('/transportadora/<token>', methods=['POST'])
def cadastro_post(token: str):
    """
    Processa o formulário de auto-cadastro.
    Valida o token novamente, cria o usuário e redireciona para o login.
    """
    if not convite_service:
        flash("Serviço temporariamente indisponível.", "danger")
        return redirect(url_for('wms_cadastro.cadastro_get', token=token))

    nome  = request.form.get('nome', '').strip()
    email = request.form.get('email', '').strip()
    senha = request.form.get('senha', '')
    conf  = request.form.get('confirmar_senha', '')

    # Validações básicas no servidor
    erros = []
    if not nome or len(nome) < 3:
        erros.append("Nome deve ter pelo menos 3 caracteres.")
    if not email or '@' not in email:
        erros.append("E-mail inválido.")
    if not senha or len(senha) < 6:
        erros.append("Senha deve ter pelo menos 6 caracteres.")
    if senha != conf:
        erros.append("As senhas não coincidem.")

    if erros:
        # Re-valida token para exibir a página novamente
        _, _, convite = convite_service.validar_token(token)
        return render_template(
            'wms/convite/cadastro_transportadora.html',
            token=token,
            empresa=convite.get('empresa', '') if convite else '',
            expira_em=convite.get('expira_em', '') if convite else '',
            erros=erros,
            nome=nome,
            email=email,
        )

    success, msg = convite_service.usar_convite(
        token=token,
        nome=nome,
        email=email,
        senha=senha,
    )

    if success:
        return render_template('wms/convite/cadastro_sucesso.html', email=email)

    # Erro — recarrega o formulário
    _, _, convite = convite_service.validar_token(token)
    return render_template(
        'wms/convite/cadastro_transportadora.html',
        token=token,
        empresa=convite.get('empresa', '') if convite else '',
        expira_em=convite.get('expira_em', '') if convite else '',
        erros=[msg],
        nome=nome,
        email=email,
    )
