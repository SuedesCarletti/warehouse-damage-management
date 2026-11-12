from flask import Blueprint, render_template, session
import logging

from utils.decorators import module_required, screen_required, set_theme

logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

ticket_service = None
sheets_service = None

def init_admin_routes(ticket_svc, sheets_svc):
    global ticket_service, sheets_service
    ticket_service = ticket_svc
    sheets_service = sheets_svc

@admin_bp.route('/')
@module_required('helpdesk')  # VALIDAÇÃO DE MÓDULO
@screen_required('helpdesk', 'admin_panel')  # VALIDAÇÃO DE TELA ESPECÍFICA
@set_theme('helpdesk')
def index():
    # Página admin principal
    stats = ticket_service.get_statistics()
    users = sheets_service.get_all_users()
    categories = sheets_service.get_all_categories()
    
    admin_stats = {
        'total_tickets': stats['total'],
        'total_users': len(users),
        'total_categories': len(categories),
        'resolved_tickets': stats['resolvidos'] + stats['fechados']
    }
    
    sheets_status = sheets_service.test_connection()
    recent_tickets = ticket_service.get_tickets()[:20]
    
    return render_template('helpdesk/admin.html',
                         stats=admin_stats,
                         sheets_status=sheets_status,
                         tickets=recent_tickets,
                         SPREADSHEET_ID=sheets_service.spreadsheet_id)

@admin_bp.route('/users')
@module_required('helpdesk')
@screen_required('helpdesk', 'admin_panel')
@set_theme('helpdesk')
def manage_users():
    users = sheets_service.get_all_users()
    return render_template('helpdesk/admin/manage_users.html', users=users)

@admin_bp.route('/categories')
@module_required('helpdesk')
@screen_required('helpdesk', 'admin_panel')
@set_theme('helpdesk')
def manage_categories():
    categories = sheets_service.get_all_categories()
    return render_template('helpdesk/admin/manage_categories.html', categories=categories)
