import logging
from flask import Blueprint, render_template, request, jsonify, session, flash
from utils.decorators import module_required, set_theme
from ..services.transportadoras_service import TransportadorasService

logger = logging.getLogger(__name__)

transportadoras_bp = Blueprint('wms_transportadoras', __name__)
transp_service: TransportadorasService = None

def init_transportadoras_routes(sheets_service, wms_service, sankhya_service):
    global transp_service
    transp_service = TransportadorasService(
        sheets_service=sheets_service,
        wms_service=wms_service,
        sankhya_service=sankhya_service
    )
    logger.info("Rotas de Transportadoras inicializadas")

@transportadoras_bp.route('/normalizacao')
@module_required('wms')
@set_theme('wms')
def tela_normalizacao():
    """Tela de normalização de nomes de transportadoras."""
    if not transp_service:
        flash("Serviço de transportadoras não inicializado.", "danger")
        return render_template('wms/transportadoras/normalizacao.html', transportadoras=[])
    
    empresa = session.get('user_empresa', '')
    transportadoras = transp_service.get_unique_transportadoras(empresa_contexto=empresa)
    return render_template('wms/transportadoras/normalizacao.html', transportadoras=transportadoras)

@transportadoras_bp.route('/api/save', methods=['POST'])
@module_required('wms')
def save_normalizacao():
    data = request.get_json()
    original = data.get('nome_original')
    normalizado = data.get('nome_normalizado')
    empresa = session.get('user_empresa', '')

    if not original:
        return jsonify({"success": False, "message": "Nome original é obrigatório"}), 400

    success = transp_service.salvar_normalizacao(original, normalizado, empresa=empresa)
    if success:
        return jsonify({"success": True, "message": "Normalização salva com sucesso"})
    else:
        return jsonify({"success": False, "message": "Erro ao salvar normalização"}), 500

@transportadoras_bp.route('/api/apply', methods=['POST'])
@module_required('wms')
def apply_normalizacoes():
    """Aplica as normalizações em todos os registros."""
    empresa = session.get('user_empresa', '')
    sucessos, erros = transp_service.aplicar_normalizacao_em_massa(empresa_contexto=empresa)
    return jsonify({
        "success": True, 
        "message": f"Normalização aplicada: {sucessos} registros atualizados, {erros} erros."
    })

@transportadoras_bp.route('/api/sync_sankhya', methods=['POST'])
@module_required('wms')
def sync_sankhya():
    """Sincroniza transportadoras com o Sankhya."""
    empresa = session.get('user_empresa', '')
    resultado = transp_service.sincronizar_todas_notas_sankhya(empresa_contexto=empresa)
    return jsonify(resultado)
