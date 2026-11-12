"""
routes/api_routes.py
Rotas API REST para operações CRUD
"""

from flask import Blueprint, jsonify, request, session
import logging
import traceback
from werkzeug.security import generate_password_hash

from utils.decorators import api_login_required, api_admin_required, api_manager_required
# CORRIGIDO: Importação dos modelos de seus locais corretos
from core.models import User
from modules.helpdesk.models import Category

logger = logging.getLogger(__name__)

api_bp = Blueprint('api', __name__, url_prefix='/api')

# Serviços injetados
ticket_service = None
sheets_service = None

def init_api_routes(ticket_svc, sheets_svc):
    """Inicializa rotas com serviços"""
    global ticket_service, sheets_service
    ticket_service = ticket_svc
    sheets_service = sheets_svc

# USUARIOS - API

@api_bp.route('/users', methods=['GET'])
@api_admin_required
def get_users():
    """Lista todos os usuários"""
    try:
        users = sheets_service.get_all_users()
        return jsonify({
            'success': True,
            'users': [u.to_dict() for u in users]
        })
    except Exception as e:
        logger.error(f"Erro ao listar usuários: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500

@api_bp.route('/users/add', methods=['POST'])
@api_admin_required
def add_user():
    """Adiciona novo usuário"""
    try:
        data = request.json
        
        # Criar objeto User (valida automaticamente)
        user = User(
            nome=data.get('nome'),
            email=data.get('email'),
            departamento=data.get('departamento'),
            nivel=data.get('nivel', 'usuario'),
            ativo=data.get('ativo', 'sim')
        )
        
        # Adicionar ao Google Sheets
        success, message = sheets_service.add_user(user)
        
        if success:
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'error': message}), 400
            
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Erro ao adicionar usuário: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500

@api_bp.route('/users/update', methods=['POST'])
@api_admin_required
def update_user():
    """Atualiza usuário"""
    try:
        data = request.json
        email = data.pop('email')  # Email é a chave
        
        # Validar campos se necessário
        # (pode criar um método de validação parcial)
        
        success, message = sheets_service.update_user(email, **data)
        
        if success:
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'error': message}), 400
            
    except Exception as e:
        logger.error(f"Erro ao atualizar usuário: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500

@api_bp.route('/users/reset-password', methods=['POST'])
@api_admin_required
def admin_reset_password():
    """(Admin) Redefine a senha de um usuário."""
    try:
        data = request.json
        email = data.get('email')
        new_password = data.get('new_password')

        if not email or not new_password:
            return jsonify({'success': False, 'error': 'Email e nova senha são obrigatórios'}), 400

        if len(new_password) < 8:
            return jsonify({'success': False, 'error': 'A senha deve ter pelo menos 8 caracteres'}), 400

        hashed_password = generate_password_hash(new_password, method='pbkdf2:sha256')
        success, message = sheets_service.update_user(email, senha=hashed_password)

        if success:
            logger.info(f"Senha do usuário {email} redefinida pelo admin {session.get('user_email')}")
            return jsonify({'success': True, 'message': 'Senha redefinida com sucesso!'})
        else:
            return jsonify({'success': False, 'error': message}), 400

    except Exception as e:
        logger.error(f"Erro ao redefinir senha (admin): {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@api_bp.route('/users/delete', methods=['POST'])
@api_admin_required
def delete_user():
    """Desativa usuário (soft delete)"""
    try:
        email = request.json.get('email')
        success, message = sheets_service.delete_user(email)
        
        if success:
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'error': message}), 400
            
    except Exception as e:
        logger.error(f"Erro ao deletar usuário: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500

# CATEGORIAS - API

@api_bp.route('/categories', methods=['GET'])
@api_admin_required
def get_categories():
    """Lista todas as categorias"""
    try:
        categories = sheets_service.get_all_categories()
        return jsonify({
            'success': True,
            'categories': [c.to_dict() for c in categories]
        })
    except Exception as e:
        logger.error(f"Erro ao listar categorias: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500

@api_bp.route('/categories/add', methods=['POST'])
@api_admin_required
def add_category():
    """Adiciona nova categoria"""
    try:
        data = request.json
        
        # Criar objeto Category (valida automaticamente)
        category = Category(
            categoria=data.get('categoria'),
            descricao=data.get('descricao'),
            email_terceiro=data.get('email_terceiro', ''),
            requer_gestor=data.get('requer_gestor', 'nao'),
            sla_horas=str(data.get('sla_horas', '24')),
            ativo=data.get('ativo', 'sim'),
            cor=data.get('cor', '#3498db')
        )
        
        # Adicionar ao Google Sheets
        success, message = sheets_service.add_category(category)
        
        if success:
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'error': message}), 400
            
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Erro ao adicionar categoria: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500

@api_bp.route('/categories/update', methods=['POST'])
@api_admin_required
def update_category():
    """Atualiza categoria"""
    try:
        data = request.json
        categoria = data.pop('categoria')  # Categoria é a chave
        
        success, message = sheets_service.update_category(categoria, **data)
        
        if success:
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'error': message}), 400
            
    except Exception as e:
        logger.error(f"Erro ao atualizar categoria: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500

@api_bp.route('/categories/delete', methods=['POST'])
@api_admin_required
def delete_category():
    """Desativa categoria (soft delete)"""
    try:
        categoria = request.json.get('categoria')
        success, message = sheets_service.delete_category(categoria)
        
        if success:
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'error': message}), 400
            
    except Exception as e:
        logger.error(f"Erro ao deletar categoria: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500

# TICKETS - API

@api_bp.route('/tickets/update-status/<ticket_number>', methods=['POST'])
@api_manager_required
def update_ticket_status(ticket_number):
    """Atualiza status de um ticket"""
    try:
        new_status = request.json.get('status')
        reason = request.json.get('reason', '')
        changed_by = session.get('user_email')
        
        success, message = ticket_service.update_status(
            ticket_number, new_status, changed_by, reason
        )
        
        if success:
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'error': message}), 400
            
    except Exception as e:
        logger.error(f"Erro ao atualizar status: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500

# GOOGLE SHEETS - STATUS

@api_bp.route('/sheets-status')
@api_admin_required
def sheets_status():
    """Retorna status da conexão com Google Sheets"""
    try:
        status = sheets_service.test_connection()
        return jsonify(status)
    except Exception as e:
        logger.error(f"Erro ao verificar status: {e}")
        logger.error(traceback.format_exc())
        return jsonify({
            'connected': False,
            'error': str(e),
            'message': 'Erro ao verificar status'
        }), 500

@api_bp.route('/retry-sheets-connection', methods=['POST'])
@api_admin_required
def retry_sheets_connection():
    """Tenta reconectar ao Google Sheets"""
    try:
        success = sheets_service.connect()
        
        if success:
            return jsonify({
                'success': True,
                'message': 'Reconectado com sucesso'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Não foi possível reconectar'
            }), 500
            
    except Exception as e:
        logger.error(f"Erro ao reconectar: {e}")
        logger.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
