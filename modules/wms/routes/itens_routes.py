from flask import Blueprint, render_template, request, jsonify, session
import logging

from utils.decorators import module_required, api_module_required, set_theme
from ..services.sankhya_integration_service import WmsSankhyaIntegrationService

logger = logging.getLogger(__name__)

# Blueprint
itens_bp = Blueprint('wms_itens', __name__)

# Serviço Sankhya
sankhya_service: WmsSankhyaIntegrationService = None

def init_itens_routes(sankhya_token=None):
    """Inicializa o serviço Sankhya"""
    global sankhya_service
    # FIX: Use a palavra-chave correta (sankhya_token) esperada pelo construtor
    sankhya_service = WmsSankhyaIntegrationService(sankhya_token=sankhya_token)

# ROTA: VISUALIZAR ITENS DE NOTA (HTML)

@itens_bp.route('/nota/<int:numnota>/itens')
@module_required('wms')
@set_theme('wms')
def visualizar_itens_nota(numnota):
    """
    Página HTML para visualizar itens de uma nota fiscal
    """
    logger.info(f"Visualizando itens da nota {numnota} por {session.get('user_email')}")
    
    # Obter código da empresa da query string (obrigatório)
    codemp = request.args.get('codemp', type=int)
    
    if not codemp:
        logger.error(f"CODEMP não fornecido para nota {numnota}")
        return render_template('errors/400.html', 
                             message="Código da empresa (CODEMP) é obrigatório"), 400
    
    try:
        # Buscar cabeçalho
        cabecalho = sankhya_service.get_cabecalho_nota(codemp, numnota)
        
        if not cabecalho:
            logger.warning(f" Nota {numnota} (Empresa {codemp}) não encontrada")
            return render_template('wms/itens_nota.html',
                                 error="Nota fiscal não encontrada",
                                 numnota=numnota,
                                 codemp=codemp)
        
        # Buscar itens
        # CORREÇÃO: Usar o NUNOTA obtido do cabeçalho para buscar itens.
        itens = sankhya_service.get_itens_nota(cabecalho.nunota)
        
        logger.info(f"Nota {numnota}: {len(itens)} item(ns) carregado(s)")
        
        # Calcular estatísticas para o template
        valor_total = sum(item.vlrtot_float for item in itens)
        
        stats_context = {
            'total_itens': len(itens),
            'valor_total_formatado': f"R$ {valor_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        }

        return render_template('wms/itens_nota.html',
                             cabecalho=cabecalho,
                             itens=itens,
                             numnota=numnota,
                             codemp=codemp,
                             stats=stats_context) # Incluir stats
        
    except Exception as e:
        logger.error(f"Erro ao buscar nota {numnota} (Empresa {codemp}): {e}")
        return render_template('wms/itens_nota.html',
                             error=f"Erro ao buscar nota: {str(e)}",
                             numnota=numnota,
                             codemp=codemp), 500

# API: VALIDAR NOTA (RETORNA SE EXISTE)

@itens_bp.route('/api/validar-nota', methods=['POST'])
@api_module_required('wms')
def api_validar_nota():
    """
    API para validar se uma nota existe no ERP
    
    Payload:
    {
        "codemp": 1,
        "numnota": 123456
    }
    
    Response:
    {
        "success": true,
        "existe": true,
        "nota": {...}
    }
    """
    try:
        data = request.get_json()
        codemp = data.get('codemp')
        numnota = data.get('numnota')
        
        # VALIDAÇÃO: Ambos campos são obrigatórios
        if not codemp or not numnota:
            return jsonify({
                'success': False,
                'error': 'CODEMP e NUMNOTA são obrigatórios'
            }), 400
        
        logger.info(f"Validando nota {numnota} (Empresa {codemp})")
        
        # CORREÇÃO: get_cabecalho_nota agora existe no serviço
        cabecalho = sankhya_service.get_cabecalho_nota(codemp, numnota)
        
        if cabecalho:
            # Buscar info resumida (que conta itens) usando o método get_info_resumida_nota (renomeado)
            resumo = sankhya_service.get_info_resumida_nota(codemp, numnota)
            
            if resumo.get('EXISTE'):
                return jsonify({
                    'success': True,
                    'existe': True,
                    'dados': { 
                        'numnota': resumo.get('NUMNOTA'),
                        'codemp': resumo.get('CODEMP'),
                        'nomeparc': resumo.get('NOMEPARC'),
                        'VLRNOTA': resumo.get('VLRNOTA'),
                        'TOTAL_ITENS': resumo.get('TOTAL_ITENS', 0)
                    }
                })
            else:
                 return jsonify({
                    'success': True,
                    'existe': False,
                    'message': 'Nota não encontrada'
                })
            
        else:
            return jsonify({
                'success': True,
                'existe': False,
                'message': 'Nota não encontrada'
            })
            
    except Exception as e:
        logger.error(f"Erro ao validar nota: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# API: BUSCAR ITENS DE NOTA (JSON)

@itens_bp.route('/api/nota/<int:numnota>/itens', methods=['GET'])
@api_module_required('wms')
def api_buscar_itens_nota(numnota):
    """
    API para buscar itens de uma nota
    
    Query params:
    - codemp: Código da empresa (obrigatório)
    
    Response:
    {
        "success": true,
        "itens": [...]
    }
    """
    try:
        codemp = request.args.get('codemp', type=int)
        
        if not codemp:
            return jsonify({
                'success': False,
                'error': 'CODEMP é obrigatório'
            }), 400
        
        logger.info(f"Buscando itens da nota {numnota} (Empresa {codemp})")
        
        # 1. Obter cabeçalho para pegar NUNOTA
        # CORREÇÃO: get_cabecalho_nota agora existe
        cabecalho = sankhya_service.get_cabecalho_nota(codemp, numnota)
        
        if not cabecalho:
            return jsonify({
                'success': False,
                'error': 'Cabeçalho da nota não encontrado (impossível obter NUNOTA)'
            }), 404
        
        # 2. Buscar itens usando NUNOTA
        # CORREÇÃO: get_itens_nota agora existe e espera apenas NUNOTA
        itens = sankhya_service.get_itens_nota(cabecalho.nunota)
        
        return jsonify({
            'success': True,
            'count': len(itens),
            'itens': [item.to_dict() for item in itens]
        })
        
    except Exception as e:
        logger.error(f"Erro ao buscar itens: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# API: BUSCAR CABEÇALHO DE NOTA (JSON)

@itens_bp.route('/api/nota/<int:numnota>/cabecalho', methods=['GET'])
@api_module_required('wms')
def api_buscar_cabecalho_nota(numnota):
    """
    API para buscar cabeçalho de uma nota
    
    Query params:
    - codemp: Código da empresa (obrigatório)
    
    Response:
    {
        "success": true,
        "cabecalho": {...}
    }
    """
    try:
        codemp = request.args.get('codemp', type=int)
        
        if not codemp:
            return jsonify({
                'success': False,
                'error': 'CODEMP é obrigatório'
            }), 400
        
        logger.info(f"Buscando cabeçalho da nota {numnota} (Empresa {codemp})")
        
        # CORREÇÃO: get_cabecalho_nota agora existe no serviço e retorna CabecalhoNota
        cabecalho = sankhya_service.get_cabecalho_nota(codemp, numnota)
        
        if cabecalho:
            return jsonify({
                'success': True,
                'cabecalho': cabecalho.to_dict()
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Nota não encontrada'
            }), 404
        
    except Exception as e:
        logger.error(f"Erro ao buscar cabeçalho: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
