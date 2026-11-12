# api/v1/avarias_api.py
"""
api/v1/avarias_api.py
Endpoints REST para gerenciamento de avarias
"""

from flask import Blueprint, request, jsonify
from datetime import datetime
import logging
import traceback

logger = logging.getLogger(__name__)

# Blueprint
api_bp = Blueprint('avarias_rest_v1', __name__, url_prefix='/api/v1')
api_bp.strict_slashes = False 

# Serviços (serão injetados via init_api_routes)
wms_service = None
sheets_service = None
webhook_service = None


def init_api_routes(wms_svc, sheets_svc, webhook_svc=None):
    """Inicializa rotas com dependências"""
    global wms_service, sheets_service, webhook_service
    wms_service = wms_svc
    sheets_service = sheets_svc
    webhook_service = webhook_svc
    logger.info("API v1 inicializada")
    logger.info(f"   wms_service: {type(wms_service).__name__ if wms_service else 'None'}")
    logger.info(f"   sheets_service: {type(sheets_service).__name__ if sheets_service else 'None'}")


# DIAGNÓSTICO: loga TODA requisição que chega no blueprint

@api_bp.before_request
def log_every_request():
    import os
    logger.info(
        f"🔍 [avarias_rest_v1] PID:{os.getpid()} | "
        f"{request.method} {request.path} | "
        f"X-API-Key: {'✅ presente' if request.headers.get('X-API-Key') else '❌ ausente'}"
    )

# HEALTH CHECK

@api_bp.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'ok',
        'timestamp': datetime.now().isoformat(),
        'version': '1.0.0',
        'services': {
            'wms': wms_service is not None,
            'sheets': sheets_service is not None,
            'webhooks': webhook_service is not None
        }
    }), 200


# HELPERS - Validação de API Key

def _validate_api_key(api_key: str):
    """
    Valida API Key contra planilha wms_api_keys.
    Retorna (True, empresa) ou (False, None)
    """
    if not sheets_service:
        logger.error("[validate_api_key] sheets_service é None")
        return False, None
    
    try:
        ws = sheets_service.sheet.worksheet('wms_api_keys')
        rows = ws.get_all_records()
        
        for row in rows:
            if (str(row.get('api_key', '')).strip() == api_key.strip() and
                    str(row.get('ativo', '')).strip().lower() == 'sim'):
                
                # Verificar expiração (se preenchida)
                expira = str(row.get('expira_em', '')).strip()
                if expira:
                    try:
                        from datetime import datetime as dt
                        exp_date = dt.strptime(expira, '%d/%m/%Y')
                        if dt.now() > exp_date:
                            logger.warning(f"API Key expirada: empresa={row.get('empresa')}")
                            return False, None
                    except Exception:
                        pass
                
                empresa = str(row.get('empresa', '')).strip()
                logger.info(f"API Key válida — empresa: {empresa}, integração: {row.get('nome_integracao')}")
                return True, empresa
        
        logger.warning(f"API Key não encontrada ou inativa")
        return False, None
        
    except Exception as e:
        logger.error(f"Erro ao validar API Key: {e}")
        return False, None


def _require_api_key():
    """
    Verifica X-API-Key no header.
    Retorna (empresa, None) se válida, ou (None, response_json_401) se inválida.
    """
    api_key = request.headers.get('X-API-Key')
    
    if not api_key:
        logger.warning(f"Request sem X-API-Key: {request.path}")
        return None, (jsonify({'error': 'X-API-Key obrigatório'}), 401)
    
    valid, empresa = _validate_api_key(api_key)
    
    if not valid:
        logger.warning(f"API Key inválida para: {request.path}")
        return None, (jsonify({'error': 'API Key inválida ou expirada'}), 401)
    
    return empresa, None


# AVARIAS - LISTAR

@api_bp.route('/avarias', methods=['GET'])
def listar_avarias():
    """
    Lista avarias com filtros
    GET /api/v1/avarias?status_estoque=Pendente&limit=50
    Headers: X-API-Key
    """
    logger.info(f"[listar_avarias] Iniciando — path={request.path}")
    
    try:
        # Validar API Key
        empresa, err = _require_api_key()
        if err:
            return err
        
        logger.info(f"[listar_avarias] empresa={empresa}, validando wms_service...")
        
        # Verificar serviço
        if not wms_service:
            logger.error("[listar_avarias] wms_service é None")
            return jsonify({
                'error': 'Serviço WMS indisponível',
                'message': 'wms_service não inicializado'
            }), 503
        
        # Filtros
        filtros = {}
        
        if request.args.get('empresa'):
            filtros['cod_empresa'] = request.args.get('empresa')
        elif empresa:
            filtros['cod_empresa'] = empresa  # Filtrar pela empresa da API Key
        
        if request.args.get('status_estoque'):
            filtros['status_estoque'] = request.args.get('status_estoque')
        
        if request.args.get('status_indenizacao'):
            filtros['status_indenizacao'] = request.args.get('status_indenizacao')
        
        if request.args.get('data_inicio'):
            filtros['data_inicio'] = request.args.get('data_inicio')
        
        if request.args.get('data_fim'):
            filtros['data_fim'] = request.args.get('data_fim')
        
        limit = int(request.args.get('limit', 50))
        
        logger.info(f"[listar_avarias] Buscando com filtros={filtros}, limit={limit}")
        
        # Buscar avarias
        avarias = wms_service.listar_avarias(filtros=filtros, limit=limit)
        
        logger.info(f"[listar_avarias] Retornando {len(avarias)} avarias")
        
        return jsonify({
            'success': True,
            'total': len(avarias),
            'empresa': empresa,
            'filtros': filtros,
            'avarias': [a.to_dict() if hasattr(a, 'to_dict') else a for a in avarias]
        }), 200
        
    except Exception as e:
        logger.error(f"[listar_avarias] Erro: {e}")
        logger.error(traceback.format_exc())
        return jsonify({
            'error': 'Erro ao listar avarias',
            'message': str(e)
        }), 500


# AVARIAS - BUSCAR POR ID

@api_bp.route('/avarias/<avaria_id>', methods=['GET'])
def obter_avaria(avaria_id):
    """GET /api/v1/avarias/<avaria_id>"""
    logger.info(f"[obter_avaria] avaria_id={avaria_id}")
    
    try:
        empresa, err = _require_api_key()
        if err:
            return err
        
        if not wms_service:
            return jsonify({'error': 'Serviço WMS indisponível'}), 503
        
        avaria = wms_service.get_avaria(avaria_id)
        
        if not avaria:
            return jsonify({
                'error': 'Avaria não encontrada',
                'avaria_id': avaria_id
            }), 404
        
        return jsonify(avaria.to_dict() if hasattr(avaria, 'to_dict') else avaria), 200
        
    except Exception as e:
        logger.error(f"[obter_avaria] {avaria_id}: {e}")
        return jsonify({'error': 'Erro ao buscar avaria', 'message': str(e)}), 500


# AVARIAS - CRIAR

@api_bp.route('/avarias', methods=['POST'])
def criar_avaria():
    """POST /api/v1/avarias"""
    logger.info(f"[criar_avaria] Iniciando")
    
    try:
        empresa, err = _require_api_key()
        if err:
            return err
        
        if not wms_service:
            return jsonify({'error': 'Serviço WMS indisponível'}), 503
        
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Body JSON não fornecido'}), 400
        
        required = ['nota', 'cod_empresa', 'itens']
        missing = [f for f in required if f not in data]
        if missing:
            return jsonify({'error': 'Campos obrigatórios faltando', 'missing_fields': missing}), 400
        
        if not data['itens']:
            return jsonify({'error': 'Campo "itens" deve conter ao menos 1 item'}), 400
        
        avaria_id = wms_service.criar_avaria(
            nota=data['nota'],
            cod_empresa=data['cod_empresa'],
            transportadora=data.get('transportadora', ''),
            volume=data.get('volume', 1),
            cte=data.get('cte', ''),
            nfd=data.get('nfd', ''),
            valor_nf=data.get('valor_nf', 0),
            itens_selecionados=data['itens'],
            registrado_por=f"API:{empresa}"
        )
        
        logger.info(f"[criar_avaria] Criada: {avaria_id}")
        
        if webhook_service:
            try:
                webhook_service.trigger('avaria.criada', {
                    'avaria_id': avaria_id,
                    'nota': data['nota'],
                    'cod_empresa': data['cod_empresa']
                })
            except Exception as wh_err:
                logger.warning(f"Webhook falhou (não crítico): {wh_err}")
        
        return jsonify({
            'success': True,
            'avaria_id': avaria_id,
            'message': 'Avaria criada com sucesso'
        }), 201
        
    except Exception as e:
        logger.error(f"[criar_avaria] {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Erro ao criar avaria', 'message': str(e)}), 500


# AVARIAS - ATUALIZAR

@api_bp.route('/avarias/<avaria_id>', methods=['PATCH'])
def atualizar_avaria(avaria_id):
    """PATCH /api/v1/avarias/<avaria_id>"""
    logger.info(f"[atualizar_avaria] avaria_id={avaria_id}")
    
    try:
        empresa, err = _require_api_key()
        if err:
            return err
        
        if not wms_service:
            return jsonify({'error': 'Serviço WMS indisponível'}), 503
        
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Nenhum campo para atualizar'}), 400
        
        success, message = wms_service.update_avaria(avaria_id, **data)
        
        if not success:
            return jsonify({'error': 'Erro ao atualizar', 'message': message}), 400
        
        if webhook_service:
            try:
                webhook_service.trigger('avaria.atualizada', {
                    'avaria_id': avaria_id,
                    'campos_alterados': list(data.keys())
                })
            except Exception:
                pass
        
        return jsonify({
            'success': True,
            'message': message,
            'campos_alterados': list(data.keys())
        }), 200
        
    except Exception as e:
        logger.error(f"[atualizar_avaria] {avaria_id}: {e}")
        return jsonify({'error': 'Erro ao atualizar', 'message': str(e)}), 500


# ADMIN - GERAR API KEY (legado)

@api_bp.route('/admin/api-keys', methods=['POST'])
def criar_api_key():
    """POST /api/v1/admin/api-keys — use /wms/api-keys/api/gerar de preferência"""
    logger.info(f"[criar_api_key] Iniciando")
    try:
        from api.v1.auth_api import gerar_api_key
        data = request.get_json()
        
        if not data or 'cliente' not in data:
            return jsonify({'error': 'Campo "cliente" obrigatório'}), 400
        
        api_key = gerar_api_key(data['cliente'])
        
        ws = sheets_service.sheet.worksheet('wms_api_keys')
        ws.append_row([
            api_key,
            data['cliente'],
            data.get('permissoes', 'read'),
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            data.get('expira_em', ''),
            'sim'
        ])
        
        return jsonify({
            'success': True,
            'api_key': api_key,
            'cliente': data['cliente'],
            'message': 'API Key criada com sucesso'
        }), 201
        
    except Exception as e:
        logger.error(f"[criar_api_key] {e}")
        return jsonify({'error': 'Erro ao criar API Key', 'message': str(e)}), 500
        
@api_bp.after_request
def no_cache(response):
    """Impede que Railway/CDN faça cache de respostas da API"""
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response
