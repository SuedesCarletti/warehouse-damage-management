"""
modules/wms/routes/batch_import_routes.py - VERSÃO CORRIGIDA COM LIMPEZA DE NaN
"""
import logging
import os
import math
import numpy as np
import pandas as pd
from flask import Blueprint, request, jsonify, render_template, current_app, session
from werkzeug.utils import secure_filename
from modules.wms.services.batch_import_service import BatchImportService, BatchImportValidator
from modules.wms.services.wms_service import WmsAvariaService
from utils.decorators import login_required, module_required, screen_required, set_theme # ADDED

logger = logging.getLogger(__name__)

# Blueprint
batch_import_bp = Blueprint('batch_import', __name__, url_prefix='/wms/batch')

def allowed_file(filename):
    """Verifica se extensão do arquivo é permitida"""
    ALLOWED_EXTENSIONS = {'xlsx', 'xls', 'csv'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def limpar_nan_para_json(obj):
    """
    Remove valores NaN de dicts/listas recursivamente para permitir serialização JSON
    
    Args:
        obj: Objeto a ser limpo (dict, list, ou valor primitivo)
        
    Returns:
        Objeto limpo sem NaN
    """
    if isinstance(obj, dict):
        return {k: limpar_nan_para_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [limpar_nan_para_json(item) for item in obj]
    elif isinstance(obj, float):
        if math.isnan(obj):
            return None
        return obj
    elif pd.isna(obj):  # Captura pd.NaT, pd.NA, None, etc
        return None
    else:
        return obj


@batch_import_bp.route('/import', methods=['GET'])
@module_required('wms') # CRÍTICO: Define contexto de módulo
@screen_required('wms', 'batch_import') # NOVO: Proteção granular
@set_theme('wms') # CRÍTICO: Força o tema WMS para renderização correta da sidebar
@login_required
def pagina_importacao():
    """Página de upload para importão em lote"""
    logger.info(f"Rota /wms/batch/import acessada por {session.get('user_email')}")
    return render_template('wms/batch_import.html')


@batch_import_bp.route('/api/upload', methods=['POST'])
@login_required
def upload_arquivo():
    """API: Upload e processamento de arquivo para importação em lote"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'Nenhum arquivo enviado'}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'success': False, 'error': 'Nenhum arquivo selecionado'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({'success': False, 'error': 'Formato não permitido'}), 400
        
        chunk_size = int(request.form.get('chunk_size', 100))
        
        filename = secure_filename(file.filename)
        upload_folder = current_app.config.get('UPLOAD_FOLDER', '/tmp/uploads')
        os.makedirs(upload_folder, exist_ok=True)
        filepath = os.path.join(upload_folder, filename)
        file.save(filepath)
        
        logger.info(f"Arquivo salvo: {filepath}")
        
        sheets_service = current_app.sheets_service
        sankhya_service = current_app.sankhya_service
        wms_service = getattr(current_app, 'wms_service', None)
        
        if not sheets_service or not sankhya_service:
            raise RuntimeError("Serviços não inicializados")
        
        if not wms_service:
            logger.warning("wms_service não encontrado. Re-inicializando...")
            wms_service = WmsAvariaService(sheets_service)
            
        batch_service = BatchImportService(sankhya_service, wms_service)
        user_email = session.get('user_email', 'importacao_lote@sistema.com')
        
        logger.info(f"Processando em lote (chunk_size={chunk_size})...")
        resultados = batch_service.processar_arquivo(
            filepath=filepath,
            chunk_size=chunk_size,
            user_email=user_email
        )
        
        # LIMPAR NaN ANTES DE RETORNAR JSON
        resultados = limpar_nan_para_json(resultados)
        
        try:
            os.remove(filepath)
            logger.info(f"Arquivo removido: {filepath}")
        except Exception as e:
            logger.warning(f"Erro ao remover arquivo: {e}")
        
        return jsonify({'success': True, 'resultados': resultados})
    
    except Exception as e:
        logger.error(f"Erro: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@batch_import_bp.route('/api/validar', methods=['POST'])
@login_required
def validar_arquivo():
    """API: Valida arquivo sem importar"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'Nenhum arquivo enviado'}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'success': False, 'error': 'Nenhum arquivo selecionado'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({'success': False, 'error': 'Formato não permitido'}), 400
        
        filename = secure_filename(file.filename)
        upload_folder = current_app.config.get('UPLOAD_FOLDER', '/tmp/uploads')
        os.makedirs(upload_folder, exist_ok=True)
        filepath = os.path.join(upload_folder, filename)
        file.save(filepath)
        
        logger.info(f"Arquivo salvo para validação: {filepath}")
        
        sheets_service = current_app.sheets_service
        sankhya_service = current_app.sankhya_service
        wms_service = getattr(current_app, 'wms_service', None)
        
        if not sheets_service or not sankhya_service:
            raise RuntimeError("Serviços não inicializados")
        
        if not wms_service:
            from modules.wms.services.wms_service import WmsAvariaService
            wms_service = WmsAvariaService(sheets_service)

        validator = BatchImportValidator(sankhya_service, wms_service)
        
        if filepath.endswith('.csv'):
            df = pd.read_csv(filepath)
        else:
            df = pd.read_excel(filepath)
        
        resultados = {
            'total_linhas': len(df),
            'validadas': 0,
            'com_erro': 0,
            'com_aviso': 0,
            'erros': [],
            'avisos': []
        }
        
        for idx, row in df.iterrows():
            linha_num = idx + 2
            # LIMPAR NaN AO CONVERTER PARA DICT
            dados = row.to_dict()
            dados = limpar_nan_para_json(dados)
            
            validation = validator.validar_linha(linha_num, dados)
            
            if validation.valido:
                resultados['validadas'] += 1
            
            if validation.erros:
                resultados['com_erro'] += 1
                for erro in validation.erros:
                    resultados['erros'].append({
                        'linha': linha_num,
                        'campo': erro.campo,
                        'valor': erro.valor,
                        'erro': erro.erro
                    })
            
            if validation.warnings:
                resultados['com_aviso'] += 1
                for aviso in validation.warnings:
                    resultados['avisos'].append({
                        'linha': linha_num,
                        'campo': aviso.campo,
                        'valor': aviso.valor,
                        'aviso': aviso.erro
                    })
        
        # LIMPAR NaN ANTES DE RETORNAR JSON
        resultados = limpar_nan_para_json(resultados)
        
        try:
            os.remove(filepath)
        except Exception as e:
            logger.warning(f"Erro ao remover arquivo: {e}")
        
        return jsonify({
            'success': True,
            'apenas_validacao': True,
            'resultados': resultados
        })
    
    except Exception as e:
        logger.error(f"Erro: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@batch_import_bp.route('/api/template', methods=['GET'])
@login_required
def download_template():
    """API: Download de template"""
    from flask import send_file
    import io
    
    try:
        template_data = {
            'cod_empresa': ['6', '7'],
            'nota': ['123456', '789012'],
            'quantidade': [10, 5],
            'volume': [1.5, 0.8],
            'transportadora': ['TRANSP A', 'TRANSP B'],
            'valor_nf': [1000.00, 500.00],
            'motivo_devolucao': [1, 2],
            'codprod': [1001, 1002],
            'qtd_avariada': [2, 1]
        }
        
        df = pd.DataFrame(template_data)
        output = io.BytesIO()
        df.to_csv(output, index=False, encoding='utf-8')
        output.seek(0)
        
        return send_file(
            output,
            mimetype='text/csv',
            as_attachment=True,
            download_name='template_importacao_avarias.csv'
        )
    
    except Exception as e:
        logger.error(f"Erro: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
