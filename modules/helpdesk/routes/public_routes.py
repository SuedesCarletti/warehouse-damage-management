from flask import Blueprint, render_template, redirect, url_for
import logging
import base64

logger = logging.getLogger(__name__)

# CORREÇÃO: Removido o argumento 'template_folder' para usar o diretório global de templates.
public_bp = Blueprint('public', __name__)

# Serviço injetado
ticket_service = None
sheets_service = None
config = None

def init_public_routes(ticket_svc, sheets_svc, app_config):
    """Inicializa rotas com serviço"""
    global ticket_service, sheets_service, config
    ticket_service = ticket_svc
    sheets_service = sheets_svc
    config = app_config

# A rota @public_bp.route('/') foi removida daqui para evitar conflitos.
# A lógica de redirecionamento foi movida para o app.py principal.

@public_bp.route('/resolve-ticket/<resolution_token>')
def resolve_ticket(resolution_token):
    """Resolução de ticket via token (para terceiros)"""
    success, ticket = ticket_service.resolve_by_token(resolution_token)
    
    if success and ticket:
        return render_template('helpdesk/resolution_success.html',
                             ticket_number=ticket.ticket_number,
                             user_name=ticket.user_name)
    else:
        return render_template('helpdesk/resolution_error.html')

@public_bp.route('/track-email/<ticket_number>')
def track_email(ticket_number):
    """Pixel de tracking para emails (marca como visualizado)"""
    ticket_service.mark_email_opened(ticket_number)
    
    # Retornar pixel transparente 1x1
    pixel = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='
    )
    
    return pixel, 200, {'Content-Type': 'image/png'}
