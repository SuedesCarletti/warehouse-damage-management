import logging
from flask import Blueprint, render_template, request, flash, redirect, url_for, session

from utils.decorators import module_required, screen_required, set_theme

logger = logging.getLogger(__name__)

# Blueprints e serviços globais
ticket_bp = Blueprint('tickets', __name__, url_prefix='/tickets')
ticket_service = None
sheets_service = None
auth_service = None

def init_ticket_routes(ticket_svc, sheets_svc, auth_svc):
    """Inicializa as rotas com os serviços necessários"""
    global ticket_service, sheets_service, auth_service
    ticket_service = ticket_svc
    sheets_service = sheets_svc
    auth_service = auth_svc

# DASHBOARD

@ticket_bp.route('/dashboard')
@module_required('helpdesk')
@screen_required('helpdesk', 'dashboard')
@set_theme('helpdesk')
def dashboard():
    """Dashboard de tickets"""
    logger.info(f"Dashboard acessado por {session.get('user_email')}")
    
    # NOVO: Obter empresa do usuário
    user_empresa = session.get('user_empresa')
    user_email = session.get('user_email')
    user_level = session.get('user_level')
    
    # FILTRO AUTOMÁTICO: Apenas tickets da empresa do usuário
    filters = {'empresa': user_empresa}
    
    # Permissões para ver todos os tickets (da empresa)
    permissions = session.get('permissions', {})
    screens = permissions.get('screens', {}).get('helpdesk', {})
    can_see_all = screens.get('*', False) or screens.get('list_tickets_all', False)
    
    # Se não pode ver todos, filtra pelos próprios tickets
    if not can_see_all:
        filters['user_email'] = user_email
    
    tickets = ticket_service.get_tickets(filters)
    
    # Estatísticas (apenas da empresa)
    stats = {
        'total': ticket_service.db.get_ticket_count({'empresa': user_empresa}),
        'abertos': ticket_service.db.get_ticket_count({'empresa': user_empresa, 'status': 'Aberto'}),
        'em_andamento': ticket_service.db.get_ticket_count({'empresa': user_empresa, 'status': 'Em Andamento'}),
        'resolvidos': ticket_service.db.get_ticket_count({'empresa': user_empresa, 'status': 'Resolvido'})
    }
    
    logger.info(f"Carregados {len(tickets)} tickets da empresa {user_empresa}")
    
    return render_template('helpdesk/dashboard.html', 
                         tickets=tickets, 
                         stats=stats)

# LISTAR TICKETS

@ticket_bp.route('/')
@module_required('helpdesk')
@screen_required('helpdesk', 'list_tickets_own')
@set_theme('helpdesk')
def list_tickets():
    """Lista tickets do usuário"""
    logger.info(f"Lista de tickets acessada por {session.get('user_email')}")
    
    user_email = session.get('user_email')
    user_empresa = session.get('user_empresa')  # NOVO
    
    # Verificar permissão para ver todos os tickets
    permissions = session.get('permissions', {})
    screens = permissions.get('screens', {}).get('helpdesk', {})
    can_see_all = screens.get('*', False) or screens.get('list_tickets_all', False)
    
    if not can_see_all and not screens.get('list_tickets_own', False):
        flash('Você não tem permissão para acessar os tickets.', 'error')
        return redirect(url_for('tickets.dashboard'))
    
    # FILTRO AUTOMÁTICO: Sempre filtrar pela empresa
    filters = {'empresa': user_empresa}
    
    # Aplicar filtros baseados nas permissões
    if not can_see_all:
        filters['user_email'] = user_email
    
    # Filtros adicionais da URL
    if request.args.get('status'):
        filters['status'] = request.args.get('status')
    if request.args.get('priority'):
        filters['priority'] = request.args.get('priority')
    if request.args.get('search'):
        filters['search'] = request.args.get('search')
    
    tickets = ticket_service.get_tickets(filters)
    
    logger.info(f"Listados {len(tickets)} tickets da empresa {user_empresa}")
    
    return render_template('helpdesk/tickets.html', tickets=tickets)

# CRIAR NOVO TICKET

@ticket_bp.route('/new', methods=['GET', 'POST'])
@module_required('helpdesk')
@screen_required('helpdesk', 'new_ticket')
@set_theme('helpdesk')
def new_ticket():
    """Criar novo ticket"""
    logger.info(f"Novo ticket acessado por {session.get('user_email')}")
    
    if request.method == 'POST':
        # NOVO: Incluir empresa do usuário automaticamente
        ticket_data = {
            'user_email': session['user_email'],
            'user_name': session['user_name'],
            'department': session['user_department'],
            'empresa': session['user_empresa'],  # NOVO CAMPO
            'category': request.form.get('category'),
            'subject': request.form.get('subject'),
            'description': request.form.get('description'),
            'priority': request.form.get('priority'),
            'created_by': session['user_email']
        }
        
        success, message, ticket = ticket_service.create_ticket(ticket_data)
        
        if success:
            logger.info(f"Ticket {message} criado por {session['user_email']} (Empresa: {session['user_empresa']})")
            flash(f'Chamado #{message} criado com sucesso!', 'success')
            return redirect(url_for('tickets.view_ticket', ticket_number=message))
        else:
            logger.error(f"Erro ao criar ticket: {message}")
            flash(f'Erro ao criar chamado: {message}', 'error')
    
    categories = [c for c in sheets_service.get_all_categories() if c.is_active]
    
    return render_template('helpdesk/new_ticket.html', categories=categories)

# VISUALIZAR TICKET

@ticket_bp.route('/<ticket_number>')
@module_required('helpdesk')
@screen_required('helpdesk', 'view_ticket')
@set_theme('helpdesk')
def view_ticket(ticket_number):
    """Visualizar ticket específico"""
    logger.info(f"Ticket {ticket_number} acessado por {session.get('user_email')}")
    
    ticket = ticket_service.get_ticket(ticket_number)
    
    if not ticket:
        logger.warning(f"Ticket {ticket_number} não encontrado")
        flash('Chamado não encontrado.', 'error')
        return redirect(url_for('tickets.list_tickets'))
    
    # NOVO: Verificar se o ticket é da mesma empresa
    user_empresa = session.get('user_empresa')
    if ticket.empresa != user_empresa:
        logger.warning(f"Usuário {session.get('user_email')} tentou acessar ticket de outra empresa")
        flash('Você não tem permissão para visualizar este chamado.', 'error')
        return redirect(url_for('tickets.list_tickets'))
    
    # Verificar permissão para ver este ticket específico
    user_email = session.get('user_email')
    permissions = session.get('permissions', {})
    screens = permissions.get('screens', {}).get('helpdesk', {})
    
    # Pode ver se: tem acesso total OU é o dono do ticket
    can_see_all = screens.get('*', False) or screens.get('list_tickets_all', False)
    is_owner = ticket.user_email.lower() == user_email.lower()
    
    if not (can_see_all or is_owner):
        logger.warning(f"Usuário {user_email} tentou acessar ticket de outro usuário: {ticket_number}")
        flash('Você não tem permissão para visualizar este chamado.', 'error')
        return redirect(url_for('tickets.list_tickets'))
    
    status_logs = ticket_service.get_status_logs(ticket_number)
    
    return render_template('helpdesk/view_ticket.html', 
                         ticket=ticket,
                         status_logs=status_logs)
