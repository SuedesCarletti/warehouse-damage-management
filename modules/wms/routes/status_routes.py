import logging
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, session

from utils.decorators import module_required, screen_required, set_theme
from ..services.status_service import StatusService
from modules.wms.models.status import Status

logger = logging.getLogger(__name__)

# Blueprint
status_bp = Blueprint('wms_status', __name__, url_prefix='/wms/status')

status_service: StatusService = None

def init_status_routes(sheets_service):
    """Inicializa o serviço de status"""
    global status_service
    status_service = StatusService(sheets_service)

# PÁGINA DE GERENCIAMENTO DE STATUS

@status_bp.route('/manage')
@module_required('wms')
@screen_required('wms', 'gerenciar_status')  # Nova permissão
@set_theme('wms')
def manage_status():
    """Página de gerenciamento de status"""
    logger.info(f"░ Gerenciamento de Status acessado por {session.get('user_email')}")
    
    status_list = status_service.get_all_status(include_inactive=True)
    
    return render_template(
        'wms/manage_status.html',
        status_list=status_list
    )

# API - LISTAR STATUS

@status_bp.route('/api/list', methods=['GET'])
@module_required('wms')
def api_list_status():
    """API para listar status ativos"""
    try:
        include_inactive = request.args.get('include_inactive', 'false').lower() == 'true'
        status_type = request.args.get('status_type') # ◠› Permitir filtro por tipo na API
        status_list = status_service.get_all_status(include_inactive=include_inactive, status_type=status_type)
        
        return jsonify({
            'success': True,
            'status_list': [s.to_dict() for s in status_list]
        })
    except Exception as e:
        logger.error(f"Erro ao listar status: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# API - ADICIONAR STATUS

@status_bp.route('/api/add', methods=['POST'])
@module_required('wms')
@screen_required('wms', 'gerenciar_status')
def api_add_status():
    """API para adicionar novo status"""
    try:
        data = request.get_json()
        
        # Validar campos obrigatórios
        if not data.get('status'):
            return jsonify({'success': False, 'error': 'Nome do status é obrigatório'}), 400
        
        # Criar objeto Status
        new_status = Status(
            status=data['status'],
            cor=data.get('cor', '#6c757d'),
            ativo=data.get('ativo', 'sim'),
            ordem=int(data.get('ordem', 999)),
            descricao=data.get('descricao', ''),
            status_type=data.get('status_type', 'geral') # ◠› Capturar tipo
        )
        
        # Adicionar
        success, message = status_service.add_status(new_status)
        
        if success:
            logger.info(f"Status '{new_status.status}' adicionado por {session.get('user_email')}")
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'error': message}), 400
            
    except Exception as e:
        logger.error(f"Erro ao adicionar status: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# API - ATUALIZAR STATUS

@status_bp.route('/api/update/<status_name>', methods=['PUT', 'POST'])
@module_required('wms')
@screen_required('wms', 'gerenciar_status')
def api_update_status(status_name):
    """API para atualizar status"""
    try:
        data = request.get_json()
        
        # Montar updates
        updates = {}
        if 'cor' in data:
            updates['cor'] = data['cor']
        if 'ativo' in data:
            updates['ativo'] = data['ativo']
        if 'ordem' in data:
            updates['ordem'] = data['ordem']
        if 'descricao' in data:
            updates['descricao'] = data['descricao']
        # ◠› Adicionar status_type para edição
        if 'status_type' in data:
            updates['status_type'] = data['status_type']
        
        if not updates:
            return jsonify({'success': False, 'error': 'Nenhum campo para atualizar'}), 400
        
        # Atualizar
        success, message = status_service.update_status(status_name, **updates)
        
        if success:
            logger.info(f"Status '{status_name}' atualizado por {session.get('user_email')}")
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'error': message}), 400
            
    except Exception as e:
        logger.error(f"Erro ao atualizar status: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# API - DELETAR (DESATIVAR) STATUS

@status_bp.route('/api/delete/<status_name>', methods=['DELETE', 'POST'])
@module_required('wms')
@screen_required('wms', 'gerenciar_status')
def api_delete_status(status_name):
    """API para desativar status"""
    try:
        success, message = status_service.delete_status(status_name)
        
        if success:
            logger.info(f"Status '{status_name}' desativado por {session.get('user_email')}")
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'error': message}), 400
            
    except Exception as e:
        logger.error(f"Erro ao desativar status: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# API - REORDENAR STATUS

@status_bp.route('/api/reorder', methods=['POST'])
@module_required('wms')
@screen_required('wms', 'gerenciar_status')
def api_reorder_status():
    """API para reordenar status"""
    try:
        data = request.get_json()
        status_order = data.get('order', [])
        
        if not status_order:
            return jsonify({'success': False, 'error': 'Lista de ordenação vazia'}), 400
        
        success, message = status_service.reorder_status(status_order)
        
        if success:
            logger.info(f"Status reordenados por {session.get('user_email')}")
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'error': message}), 400
            
    except Exception as e:
        logger.error(f"Erro ao reordenar status: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
