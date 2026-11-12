import logging
import time
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, flash, current_app
import logging
import json
from utils.decorators import module_required, screen_required, set_theme
from ..services.wms_service import WmsAvariaService
from ..services.printing_service import BrotherPrinterService
from ..services.status_service import StatusService # FIX: Convertido para importação absoluta
from datetime import datetime
import calendar
from collections import defaultdict
from modules.wms.services.export_service import ExportService
from flask import send_file
import traceback
from modules.wms.services.dashboard_service import (
    aplicar_filtros,
    get_cod_empresas_disponiveis,
    calcular_financeiro,
    calcular_operacional,
    calcular_qualidade,
    calcular_cobranca,
    calcular_produtividade,
)

logger = logging.getLogger(__name__)


def _normalizar_monetario(valor) -> float:
    """
    Converte valor em qualquer formato para float.
    Retorna 0.0 se o valor for inválido.
    """
    if valor is None or str(valor).strip() == '':
        return 0.0
    if isinstance(valor, (int, float)):
        return float(valor)
    
    s = str(valor).replace('R$', '').replace(' ', '').strip()
    if not s:
        return 0.0

    # Se já é um float string puro
    try:
        if ',' not in s:
            return round(float(s), 2)
    except ValueError:
        pass

    tem_virgula = ',' in s
    tem_ponto = '.' in s

    if tem_virgula and tem_ponto:
        if s.rfind(',') > s.rfind('.'):
            # Formato BR: 1.234,56
            s = s.replace('.', '').replace(',', '.')
        else:
            # Formato US: 1,234.56
            s = s.replace(',', '')
    elif tem_virgula:
        s = s.replace(',', '.')
    
    try:
        return round(float(s), 2)
    except (ValueError, TypeError):
        return 0.0

# CORREÇÃO: Mudar de 'wms_blueprint' para 'wms'
wms_bp = Blueprint('wms', __name__)

wms_service: WmsAvariaService = None
printing_service: BrotherPrinterService = None
sankhya_avaria_service = None # Novo: Serviço de Avaria com mapeamento Sankhya (Injetado)
status_service = None # DECLARANDO GLOBALMENTE
param_service  = None # Parametrização (regras de exclusão)
export_service = ExportService()
automacao_service = None  # AutomacaoStatusService (injetado pelo app.py)

# Fields available for filtering/display (Now contains ALL 36 possible fields from Avaria model)
# Mapeamento do nome interno (do modelo) para o nome de exibição
FULL_AVARIA_FIELD_DISPLAY_MAP = {
    'id': 'ID Avaria',
    'cod_empresa': 'Cód. Empresa',
    'nota': 'Nota Fiscal (Venda)',
    'nfd': 'NFD (Devolução)',
    'quantidade': 'Qtd Nota',
#    'volume': 'Volume',
    'transportadora': 'Transportadora',
    'cte': 'CTE',
    'valor_nf': 'Valor NF',
    'valor_nfd': 'Valor NFD',
    'nfp': 'NFP',
    'valor_nfp': 'Valor NFP',
    'motivo_devolucao': 'Motivo Devolução',
    'cod_motivo_devolucao': 'Cód. Motivo Devolução',
    'motivo_inicial': 'Motivo Inicial (Sistema)',
    'data_protocolo_mkt': 'Data Protocolo MKT',
    'cod_motivo_inicial': 'Cód. Motivo Inicial (Sistema)',
    'indenizacao': 'Indenização Status',
    'valor_indenizacao': 'Valor Indenização',
    'prazo_pagamento': 'Prazo Pgto',
    'data_pagamento': 'Data Baixa / Pgto',
    'data_entrega': 'Data Entrega',
    'data_coleta': 'Data Coleta',
    'cod_rastreio_correios': 'Rastreio Correios',
    'observacao': 'Observação Inicial',
    'data_registro': 'Data Registro',
    'status_estoque': 'Status Estoque',
    'status_indenizacao': 'Status Indenização',
    'registrado_por': 'Registrado Por',
    'empresa': 'Empresa',
    'etiqueta_inicial_impressa': 'Etiqueta Impressa',
    'cod_triagem': 'Cód. Triagem',
    'reembolso_pct': 'Reembolso %',
    'observacao_final': 'Observação Final',
    'data_finalizacao': 'Data Finalização',
    'finalizado_por': 'Finalizado Por'
}
AVARIA_FIELDS = list(FULL_AVARIA_FIELD_DISPLAY_MAP.keys()) # Lista de 36 chaves
AVARIA_DISPLAY_NAMES = list(FULL_AVARIA_FIELD_DISPLAY_MAP.values()) # Lista de 36 nomes de exibição

def get_sankhya_service():
    """Retorna instância do serviço Sankhya se disponível"""
    try:
        from ..services.sankhya_integration_service import WmsSankhyaIntegrationService
        return WmsSankhyaIntegrationService()
    except:
        return None

def init_wms_routes(sheets_service, auth_service, app_config, sankhya_avaria_svc=None):
    global wms_service, printing_service, status_service, sankhya_avaria_service, param_service
    wms_service = WmsAvariaService(sheets_service)
    printing_service = BrotherPrinterService(sheets_service, wms_service)
    status_service = StatusService(sheets_service)
    sankhya_avaria_service = sankhya_avaria_svc # ATRIBUIÇÃO DO NOVO SERVIÇO

    # Parametrização — regras de exclusão de avarias pendentes
    from ..services.parametrizacao_service import ParametrizacaoService
    param_service = ParametrizacaoService(sheets_service)

# ROTA PRINCIPAL DO WMS

@wms_bp.route('/')
@module_required('wms')
@screen_required('wms', 'avarias_dashboard')
@set_theme('wms')
def index():
    """
    Dashboard analítico WMS.
    
    Permissões:
    - Acesso à rota: controlado por avarias_dashboard (já existente)
    - Painéis visíveis: controlado por dash_* (opcional/granular)
      → se NENHUM dash_* estiver configurado, mostra TODOS os painéis
      → se ALGUM dash_* estiver configurado, aplica controle granular
    """
    user_empresa = session.get('user_empresa')
    permissions  = session.get('permissions', {})
    wms_screens  = permissions.get('screens', {}).get('wms', {})
    is_admin     = session.get('user_level', '') in ('admin_global', 'admin_empresa', 'admin')
    is_wildcard  = wms_screens.get('*', False)

    PAINEIS = ['financeiro', 'operacional', 'qualidade', 'cobranca', 'produtividade']

    # Verifica se alguma screen dash_* está explicitamente configurada
    tem_config_granular = any(
        wms_screens.get(f'dash_{p}', False) for p in PAINEIS
    )

    if is_admin or is_wildcard or not tem_config_granular:
        # Admin, wildcard, ou nenhuma config fina → mostra todos os painéis
        paineis_permitidos = {p: True for p in PAINEIS}
    else:
        # Controle granular: só mostra painéis com screen explícita
        paineis_permitidos = {
            p: bool(wms_screens.get(f'dash_{p}', False))
            for p in PAINEIS
        }

    # Configurações de data por painel (da parametrização)
    dashboard_configs = {}
    try:
        import modules.wms.routes.parametrizacao_routes as _param_rt
        _cfg_svc = getattr(_param_rt, 'config_service', None)
        if _cfg_svc:
            all_cfg = _cfg_svc.get_all_configs(user_empresa)
            dashboard_configs = {k: v for k, v in all_cfg.items() if k.startswith('dash_')}
    except Exception as e:
        logger.warning(f"Erro ao carregar configs de dashboard: {e}")

    campo_data_por_painel = {
        'financeiro':    dashboard_configs.get('dash_financeiro_campo_data',    'data_registro'),
        'operacional':   dashboard_configs.get('dash_operacional_campo_data',   'data_registro'),
        'qualidade':     dashboard_configs.get('dash_qualidade_campo_data',     'data_registro'),
        'cobranca':      dashboard_configs.get('dash_cobranca_campo_data',      'prazo_pagamento'),
        'produtividade': dashboard_configs.get('dash_produtividade_campo_data', 'data_registro'),
    }

    # Lista de cod_empresa disponíveis nas avarias (para o filtro)
    cod_empresas_disponiveis = []
    try:
        avarias_meta = wms_service.get_all_avarias(empresa=user_empresa)
        cod_empresas_disponiveis = get_cod_empresas_disponiveis(avarias_meta)
    except Exception as e:
        logger.warning(f"Erro ao obter cod_empresas: {e}")

    # Listas de status para os filtros visuais do dashboard
    status_estoque_list     = status_service.get_all_status(status_type='estoque')     if status_service else []
    status_indenizacao_list = status_service.get_all_status(status_type='indenizacao') if status_service else []

    # Lista de transportadoras disponíveis (a partir das avarias já carregadas)
    transportadoras_list = []
    try:
        transp_set = set()
        for a in avarias_meta:
            v = (getattr(a, 'transportadora', None) or (a.get('transportadora') if isinstance(a, dict) else None) or '').strip()
            if v:
                transp_set.add(v)
        transportadoras_list = sorted(transp_set)
    except Exception as e:
        logger.warning(f"Erro ao obter transportadoras: {e}")

    return render_template(
        'wms/avarias_dashboard.html',
        paineis_permitidos=paineis_permitidos,
        campo_data_por_painel=campo_data_por_painel,
        cod_empresas_disponiveis=cod_empresas_disponiveis,
        dashboard_configs=dashboard_configs,
        status_estoque_list=status_estoque_list,
        status_indenizacao_list=status_indenizacao_list,
        transportadoras_list=transportadoras_list,
    )

# DASHBOARD

@wms_bp.route('/dashboard')
@module_required('wms')
@screen_required('wms', 'avarias_dashboard')
@set_theme('wms')
def dashboard():
    """Redireciona para o dashboard analítico principal."""
    return redirect(url_for('wms.index'))


def _parse_filtros_request() -> dict:
    """Extrai e valida parâmetros de filtro da querystring."""
    raw_ind   = request.args.get('status_indenizacao', '')
    raw_est   = request.args.get('status_estoque', '')
    raw_transp = request.args.get('transportadora', '')
    raw_motivo = request.args.get('motivo', '')
    raw_operador = request.args.get('operador', '')
    raw_codprod = request.args.get('codprod', '')
    raw_nota = request.args.get('nota', '')
    raw_codlocal = request.args.get('codlocal', '')
    return {
        'cod_empresa':        request.args.get('cod_empresa', ''),
        'data_inicio':        request.args.get('data_inicio', ''),
        'data_fim':           request.args.get('data_fim', ''),
        'campo_data':         request.args.get('campo_data', 'data_registro'),
        'status_indenizacao': [s.strip() for s in raw_ind.split(',') if s.strip()],
        'status_estoque':     [s.strip() for s in raw_est.split(',') if s.strip()],
        'transportadora':     [s.strip() for s in raw_transp.split('|') if s.strip()],
        'motivo':             [s.strip() for s in raw_motivo.split(',') if s.strip()],
        'operador':           [s.strip() for s in raw_operador.split(',') if s.strip()],
        'codprod':            [s.strip() for s in raw_codprod.split(',') if s.strip()],
        'nota':               [s.strip() for s in raw_nota.split(',') if s.strip()],
        'codlocal':           [s.strip() for s in raw_codlocal.split(',') if s.strip()],
    }

def _get_avarias_filtradas(filtros: dict) -> list:
    """Busca avarias do tenant e aplica filtros extras."""
    user_empresa = session.get('user_empresa')
    avarias = wms_service.get_all_avarias(empresa=user_empresa)
    return aplicar_filtros(
        avarias,
        cod_empresa=filtros.get('cod_empresa'),
        data_inicio=filtros.get('data_inicio'),
        data_fim=filtros.get('data_fim'),
        campo_data=filtros.get('campo_data', 'data_registro'),
        status_indenizacao=filtros.get('status_indenizacao') or None,
        status_estoque=filtros.get('status_estoque') or None,
        transportadora=filtros.get('transportadora') or None,
        motivo=filtros.get('motivo') or None,
        operador=filtros.get('operador') or None,
        codprod=filtros.get('codprod') or None,
        nota=filtros.get('nota') or None,
        codlocal=filtros.get('codlocal') or None,
    )

def _get_status_indenizaveis(user_empresa: str) -> list:
    """Retorna lista de status configurados como indenizáveis na parametrização."""
    try:
        import modules.wms.routes.parametrizacao_routes as _param_rt
        _cfg_svc = getattr(_param_rt, 'config_service', None)
        if not _cfg_svc:
            return []
        cfg = _cfg_svc.get_all_configs(user_empresa)
        raw = cfg.get('dash_status_indenizaveis', '')
        if raw:
            return [s.strip() for s in raw.split(',') if s.strip()]
    except Exception:
        pass
    return []

# REGISTRAR AVARIA

@wms_bp.route('/registrar', methods=['GET'])
@module_required('wms')
@screen_required('wms', 'avarias_registrar')
@set_theme('wms')
def registrar_avarias():
    user_empresa = session.get('user_empresa')
    avarias_pendentes = wms_service.get_untriaged_avarias(empresa=user_empresa)

    # Aplicar regras de parametrização (exclusão configurável)
    if param_service:
        try:
            regras_ativas = param_service.get_active_rules(empresa=user_empresa)
            if regras_ativas:
                avarias_pendentes = wms_service.apply_exclusion_rules(avarias_pendentes, regras_ativas)
                logger.info(f"{len(regras_ativas)} regras de exclusão aplicadas")
        except Exception as e:
            logger.warning(f"Erro ao aplicar regras de parametrização: {e}")
    
    # ── NOVO: Filtro de alerta — exclui avarias cujo status de parada está ativo ──
    alerta_status_parar  = request.args.get('alerta_status_parar', '').strip()
    alerta_campo_parar   = request.args.get('alerta_campo_parar',  '').strip()
    alerta_logica_parada = request.args.get('alerta_logica_parada', 'OU').strip().upper()
    
    if alerta_status_parar:
        status_parar_list = [
            s.strip().lower()
            for s in alerta_status_parar.split(',') if s.strip()
        ]
    
        def _pendente_esta_parada(row):
            """row é um dict com chave 'avaria' (objeto Avaria)."""
            av = row.get('avaria')
            if av is None:
                return False
            av_dict = av.to_dict() if hasattr(av, 'to_dict') else av
    
            status_est = str(av_dict.get('status_estoque',     '') or '').strip().lower()
            status_ind = str(av_dict.get('status_indenizacao', '') or '').strip().lower()
            status_resolvido = (status_est in status_parar_list) or (status_ind in status_parar_list)
    
            campo_resolvido = False
            if alerta_campo_parar:
                campo_resolvido = bool(str(av_dict.get(alerta_campo_parar, '') or '').strip())
    
            if alerta_campo_parar and status_parar_list:
                if alerta_logica_parada == 'E':
                    return campo_resolvido and status_resolvido
                else:
                    return campo_resolvido or status_resolvido
            elif alerta_campo_parar:
                return campo_resolvido
            else:
                return status_resolvido
    
        antes = len(avarias_pendentes)
        avarias_pendentes = [r for r in avarias_pendentes if not _pendente_esta_parada(r)]
        logger.info(
            f"Filtro alerta (registrar): {len(avarias_pendentes)}/{antes} avarias ativas "
            f"(status_parar={alerta_status_parar!r}, logica={alerta_logica_parada})"
        )
        active_filters['alerta_nome'] = request.args.get('alerta_nome', '')

    # ── NOVO: Ler filtros de data da URL (igual ao consultar_avarias) ──────
    active_filters = {}
    data_inicio = request.args.get('data_inicio', '')
    data_fim    = request.args.get('data_fim', '')
    campo_data  = request.args.get('campo_data_filtro', 'data_registro')

    if data_inicio:
        active_filters['data_inicio'] = data_inicio
    if data_fim:
        active_filters['data_fim'] = data_fim
    if data_inicio or data_fim:
        active_filters['campo_data_filtro'] = campo_data

    # Carregar status filtrados por tipo para os selects
    status_estoque_list     = status_service.get_all_status(status_type='estoque')
    status_indenizacao_list = status_service.get_all_status(status_type='indenizacao')

    if status_service:
        status_objects   = status_service.get_all_status(include_inactive=False)
        status_color_map = {s.status: s.cor for s in status_objects}
    else:
        status_color_map = {}

    logger.info(f"Registro: {len(avarias_pendentes)} avarias pendentes da empresa {user_empresa}")

    return render_template(
        'wms/registrar_avarias.html',
        avarias_pendentes=avarias_pendentes,
        status_estoque_list=status_estoque_list,
        status_indenizacao_list=status_indenizacao_list,
        status_color_map=status_color_map,
        active_filters=active_filters,          # NOVO
    )

def _render_avaria_fragment(avaria_id: str, empresa: str) -> str | None:
    """
    Renderiza as linhas <tr> de uma avaria recém-salva para injeção direta no cliente.
    Lê direto do Sheets (cache já foi invalidado antes desta chamada).
    """
    try:
        from flask import render_template
        rows_todas = wms_service.get_untriaged_avarias(empresa=empresa)
        new_rows = [r for r in rows_todas if r['avaria'].id == avaria_id]
        if not new_rows:
            logger.warning(f"Fragment: avaria {avaria_id} não encontrada após salvar")
            return None

        # Se todos os itens da nova avaria estão None, os itens ainda não foram
        # carregados pelo Sheets. Retorna None para o cliente fazer reload com delay.
        if all(r.get('item') is None for r in new_rows):
            logger.warning(f"Fragment: avaria {avaria_id} sem itens carregados, cliente fará reload")
            return None

        status_objects   = status_service.get_all_status(include_inactive=False) if status_service else []
        status_color_map = {s.status: s.cor for s in status_objects}

        return render_template(
            'wms/partials/_avaria_rows.html',
            avarias_pendentes=new_rows,
            status_color_map=status_color_map,
        )
    except Exception as e:
        logger.warning(f"Erro ao renderizar fragment da avaria {avaria_id}: {e}")
        return None
    
@wms_bp.route('/registrar', methods=['POST'])
@module_required('wms')
@screen_required('wms', 'avarias_registrar', nivel='editar')
def registrar_avaria_post():
    """
    Processa registro de avaria capturando todos os campos do modalRegistrarAvaria
    """
    data = request.form
    user_email = session.get('user_email', 'sistema')
    user_empresa = session.get('user_empresa')
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    def _responder(sucesso, mensagem, categoria='success'):
        """Retorna JSON para AJAX ou redirect para form normal."""
        if is_ajax:
            if sucesso:
                return jsonify({'success': True, 'message': mensagem})
            else:
                return jsonify({'success': False, 'error': mensagem})
        flash(mensagem, categoria if sucesso else 'error')
        return redirect(url_for('wms.registrar_avarias'))
    
    try:
        itens_json = data.get('itens_selecionados', '[]')
        itens_selecionados = json.loads(itens_json) if itens_json else []
        
        if not itens_selecionados:
            return _responder(False, 'Nenhum item selecionado. Selecione pelo menos um item avariado.')
            
    except json.JSONDecodeError as e:
        logger.error(f"Erro ao decodificar itens JSON: {e}")
        return _responder(False, 'Erro ao processar itens selecionados.')
    
    try:
        if sankhya_avaria_service:
            nota = data.get('nota', '')
            cod_empresa = data.get('cod_empresa', '')
            nunota_selecionado = data.get('nunota', '')
            
            if not nota or not cod_empresa:
                return _responder(False, 'Nota fiscal e código da empresa são obrigatórios.')
            
            # 1. Buscar dados base no Sankhya
            dados_cabecalho, itens_sankhya, erro_msg = sankhya_avaria_service.buscar_e_mapear_nota(
                numnota=nota,
                codemp=cod_empresa,
                nunota=nunota_selecionado if nunota_selecionado else None
            )
            
            if erro_msg or not dados_cabecalho:
                flash(f"Erro ao buscar nota: {erro_msg}", 'error')
                return redirect(url_for('wms.registrar_avarias'))

            # CORREÇÃO CRÍTICA: Garantir que 'nota' e 'valor_nf' do formulário/serviço sejam incluídos
            dados_cabecalho['nota'] = nota  # Forçar atribuição da nota vinda da busca/modal
            
            if data.get('transportadora'):
                dados_cabecalho['transportadora'] = data.get('transportadora')
            
            # Priorizar valor do formulário (editado no modal), senão usar o que veio do Sankhya
            if data.get('valor_nf'):
                dados_cabecalho['valor_nf'] = _normalizar_monetario(data.get('valor_nf'))
            elif not dados_cabecalho.get('valor_nf') and dados_cabecalho.get('vlrnota'):
                dados_cabecalho['valor_nf'] = _normalizar_monetario(dados_cabecalho.get('vlrnota'))
            
            # 2. Preparar dados do formulário (Campos Adicionais)
            # NORMALIZAÇÃO DE ITENS: converter campos numéricos para float string com ponto
            for it in itens_selecionados:
                for k in ['qtdneg', 'qtd_avariada', 'valor_item']:
                    if k in it:
                        it[k] = _normalizar_monetario(it[k])

            dados_formulario = {
                'cte': data.get('cte', ''),
                'nfd': data.get('nfd', ''),
                'valor_nfd': _normalizar_monetario(data.get('valor_nfd', '')),
                'nfp': data.get('nfp', ''),
                'valor_nfp': _normalizar_monetario(data.get('valor_nfp', '')),
                'prazo_pagamento': data.get('prazo_pagamento', ''),
                'data_pagamento': data.get('data_pagamento', ''),
                'motivo_devolucao': data.get('motivo_devolucao', ''),
                'indenizacao': data.get('indenizacao', ''),
                'cod_rastreio_correios': data.get('cod_rastreio_correios', ''),
                'observacao': data.get('observacao', ''),
                'itens_selecionados': itens_selecionados,
                'data_entrega': data.get('data_entrega', ''),
                'status_estoque': data.get('status_estoque', 'Pendente'),
                'status_indenizacao': data.get('status_indenizacao', 'Pendente'),
                'data_protocolo_mkt': data.get('data_protocolo_mkt', ''),
                'reembolso_pct': _normalizar_monetario(data.get('reembolso_pct', 0))
            }
            
            # Injetar NFD e VALOR NFD no cabeçalho se necessário para compatibilidade
            if dados_formulario.get('nfd'):
                dados_cabecalho['nfd'] = dados_formulario['nfd']
            if dados_formulario.get('valor_nfd'):
                dados_cabecalho['valor_nfd'] = dados_formulario['valor_nfd']

            # PONTO 3: Normalizar campos monetários no cabeçalho também
            for _campo_mon in ['valor_nf', 'valor_nfd', 'valor_nfp', 'valor_indenizacao']:
                if _campo_mon in dados_cabecalho:
                    dados_cabecalho[_campo_mon] = _normalizar_monetario(dados_cabecalho[_campo_mon])
            
            # 3. Salvar avaria
            sucesso, avaria_id_novo, mensagem = sankhya_avaria_service.salvar_avaria(
                dados_cabecalho,
                itens_selecionados,
                dados_formulario,
                user_email,
                user_empresa
            )
            
            if sucesso:
                # Invalidar cache no worker atual antes de ler o fragment
                try:
                    if wms_service and hasattr(wms_service, 'sheets'):
                        wms_service.sheets.invalidate_wms_cache()
                except Exception as e_inv:
                    logger.warning(f"Falha ao invalidar cache: {e_inv}")

                if is_ajax:
                    # Aguarda 1s para garantir que o Sheets propagou os novos itens antes de ler
                    time.sleep(1)
                    rows_html = _render_avaria_fragment(avaria_id_novo, user_empresa) if avaria_id_novo else None
                    return jsonify({
                        'success':   True,
                        'message':   mensagem,
                        'avaria_id': avaria_id_novo,
                        'rows_html': rows_html,
                    })
                flash(mensagem, 'success')
                return redirect(url_for('wms.registrar_avarias'))
            else:
                return _responder(False, f"Erro ao salvar: {mensagem}")
        
        else:
            return _responder(False, "Sistema Sankhya indisponível.")
    
    except Exception as e:
        logger.error(f"Erro ao registrar avaria: {e}")
        return _responder(False, f"Erro ao registrar avaria: {str(e)}")
        
        
@wms_bp.route('/avaria/api/recusar-item', methods=['POST'])
@module_required('wms')
def api_recusar_item_avaria():
    try:
        data = request.json
        if not data:
            return jsonify({'success': False, 'message': 'Dados inválidos'})
            
        avaria_id = data.get('avaria_id')
        sequencia = data.get('sequencia')
        qtd_recusada = data.get('qtd_recusada')
        
        if not all([avaria_id, sequencia, qtd_recusada]):
            return jsonify({'success': False, 'message': 'Dados incompletos'})
            
        try:
            seq_int = int(sequencia)
            qtd_float = float(str(qtd_recusada).replace(',', '.'))
        except ValueError:
            return jsonify({'success': False, 'message': 'Valores numéricos inválidos'})
            
        user_email = session.get('user_email', 'sistema')
        user_empresa = session.get('user_empresa', '')
        
        global wms_service
        if not wms_service:
            return jsonify({'success': False, 'message': 'Serviço indisponível.'})
            
        sucesso, msg = wms_service.recusar_item_avaria(avaria_id, seq_int, qtd_float, user_email)
        
        if sucesso:
            if automacao_service:
                try:
                    resultados_auto = automacao_service.aplicar(
                        trigger='asn_recusado',
                        avaria_id=avaria_id,
                        empresa=user_empresa,
                        operador_email=user_email
                    )
                    msgs_bloqueio = automacao_service.extrair_erros_visiveis(resultados_auto)
                    if msgs_bloqueio:
                        msg += " (⚠️ " + "; ".join(msgs_bloqueio) + ")"
                except Exception as auto_err:
                    logger.warning(f"Erro na automação 'asn_recusado' (avaria {avaria_id}): {auto_err}")

            # Invalida cache e aguarda 1s
            if hasattr(wms_service, 'sheets') and hasattr(wms_service.sheets, 'invalidate_wms_cache'):
                wms_service.sheets.invalidate_wms_cache()
            time.sleep(1)
            rows_html = _render_avaria_fragment(avaria_id, user_empresa)
            return jsonify({'success': True, 'message': msg, 'rows_html': rows_html})
        else:
            return jsonify({'success': False, 'message': msg})
            
    except Exception as e:
        logger.error(f"Erro no api_recusar_item_avaria: {e}")
        return jsonify({'success': False, 'message': str(e)})

# CONSULTAR AVARIAS

@wms_bp.route('/consultar', methods=['GET'])
@module_required('wms')
@screen_required('wms', 'avarias_dashboard')
@set_theme('wms')
def consultar_avarias():
    """
    ✅ MODIFICADO: Consulta com filtro automático por empresa.
    Removido filtro 'status' geral.
    """
    try:
        logger.info(f"◢Consultar Avarias acessado por {session.get('user_email')}")
        
        # ADICIONAR ESTA LINHA
        user_empresa = session.get('user_empresa')
        
        # Obtendo filtros dos parâmetros GET
        request_filters = request.args
        # REMOVIDO 'status'
        filterable_fields = ['nota', 'cod_empresa', 'search']
        
        active_filters = {}
        for field in filterable_fields:
            value = request_filters.get(field)
            if value:
                active_filters[field] = value

        # ── Filtros de intervalo de datas (vindos do sino ou do usuário) ──
        if request_filters.get('data_inicio'):
            active_filters['data_inicio'] = request_filters.get('data_inicio')
        if request_filters.get('data_fim'):
            active_filters['data_fim'] = request_filters.get('data_fim')
        # campo_data_filtro: qual campo da avaria será comparado com o intervalo
        # default: data_registro
        active_filters['campo_data_filtro'] = request_filters.get(
            'campo_data_filtro', 'data_registro'
        ) if (request_filters.get('data_inicio') or request_filters.get('data_fim')) else ''
        
        # NOVO: SEMPRE adicionar filtro de empresa
        active_filters['empresa'] = user_empresa
        
        # Busca total de avarias (da empresa)
        total_avarias_count = len(wms_service.get_all_avarias(empresa=user_empresa))
        
        # Busca avarias com filtros aplicados (mais recentes primeiro)
        avarias = list(reversed(wms_service.get_all_avarias(filters=active_filters, empresa=user_empresa)))

        # ── Filtro de alerta: exclui avarias cujo status de parada está ativo ──────
        alerta_status_parar  = request_filters.get('alerta_status_parar', '').strip()
        alerta_campo_parar   = request_filters.get('alerta_campo_parar',  '').strip()
        alerta_logica_parada = request_filters.get('alerta_logica_parada', 'OU').strip().upper()
        
        if alerta_status_parar:
            status_parar_list = [
                s.strip().lower()
                for s in alerta_status_parar.split(',') if s.strip()
            ]
        
            def _avaria_esta_parada(a):
                """Replica a lógica do config_service.avaliar_alertas_avaria."""
                av = a.to_dict() if hasattr(a, 'to_dict') else (a if isinstance(a, dict) else {})
        
                # Verifica status de parada (igual ao config_service: checa estoque E indenizacao)
                status_est = str(av.get('status_estoque',     '') or '').strip().lower()
                status_ind = str(av.get('status_indenizacao', '') or '').strip().lower()
                status_resolvido = (status_est in status_parar_list) or (status_ind in status_parar_list)
        
                # Verifica campo_parar (campo extra que quando preenchido para o alerta)
                campo_resolvido = False
                if alerta_campo_parar:
                    campo_resolvido = bool(str(av.get(alerta_campo_parar, '') or '').strip())
        
                # Aplica lógica E / OU (mesma do config_service)
                if alerta_campo_parar and status_parar_list:
                    if alerta_logica_parada == 'E':
                        return campo_resolvido and status_resolvido
                    else:  # OU
                        return campo_resolvido or status_resolvido
                elif alerta_campo_parar:
                    return campo_resolvido
                else:
                    return status_resolvido
        
            avarias = [a for a in avarias if not _avaria_esta_parada(a)]
            logger.info(
                f"Filtro alerta aplicado: {len(avarias)} avaria(s) ativas "
                f"(excluídas por status_parar={alerta_status_parar!r}, logica={alerta_logica_parada})"
            )        
        
        # Verificar disponibilidade do Sankhya
        sankhya_disponivel = get_sankhya_service() is not None

        # NOVO: JSON com todos os 36 nomes de exibição
        full_columns_json = json.dumps(AVARIA_DISPLAY_NAMES)

        # NOVO: CARREGAR MAPA DE CORES DE STATUS PARA O TEMPLATE
        # Nota: 'status_service' é globalmente definido e setado por init_wms_routes
        if status_service:
            status_objects          = status_service.get_all_status(include_inactive=False)
            status_color_map        = {s.status: s.cor for s in status_objects}
            status_estoque_list     = status_service.get_all_status(status_type='estoque')
            status_indenizacao_list = status_service.get_all_status(status_type='indenizacao')
        else:
            status_color_map        = {}
            status_estoque_list     = []
            status_indenizacao_list = []

        # Calcular soma de valor_item por avaria para exibição na coluna "Valor NDS"
        valor_itens_por_avaria = {}
        try:
            avaria_ids = {a.id for a in avarias}
            for item in wms_service._get_all_item_avarias():
                if item.status_etiqueta == 'baixada':
                    continue
                if item.avaria_id in avaria_ids:
                    valor_itens_por_avaria[item.avaria_id] = (
                        valor_itens_por_avaria.get(item.avaria_id, 0.0) + float(item.valor_item or 0)
                    )
        except Exception as e:
            logger.warning(f"Erro ao calcular valor_itens_por_avaria: {e}")
            valor_itens_por_avaria = {}

        return render_template('wms/consultar_avarias.html',
            avarias=avarias,
            total_avarias=total_avarias_count,
            campos_disponiveis=AVARIA_FIELDS,
            advanced_columns_json=full_columns_json,
            active_filters=active_filters,
            sankhya_disponivel=sankhya_disponivel,
            status_color_map=status_color_map,
            status_estoque_list=status_estoque_list,
            status_indenizacao_list=status_indenizacao_list,
            valor_itens_por_avaria=valor_itens_por_avaria
        )
    
    except Exception as e:
        logger.error(f"Erro ao consultar avarias: {e}")
        flash('Erro ao carregar avarias.', 'error')
        return redirect(url_for('wms.dashboard'))

def _format_dtneg_display(date_str: str) -> str:
    """
    Formata DTNEG para exibição (DD/MM/YYYY).
    Aceita ambos os formatos que o Sankhya pode retornar:
      - DD/MM/YYYY  (formato nativo do ERP — já está no formato desejado)
      - YYYY-MM-DD  (formato ISO — converte para DD/MM/YYYY)
    """
    if not date_str or len(date_str) < 10:
        return date_str
    try:
        if '-' in date_str:
            return datetime.strptime(date_str, '%Y-%m-%d').strftime('%d/%m/%Y')
        # Já no formato DD/MM/YYYY
        return date_str
    except ValueError:
        return date_str  # Formato não reconhecido — retorna como está

@wms_bp.route('/api/validar-multiplas-notas', methods=['POST'])
@module_required('wms')
def api_validar_multiplas_notas():
    """
    API: Busca por NUMNOTA e retorna lista de notas correspondentes.
    Usado para casos onde um NUMNOTA tem múltiplos NUNOTA/TIPMOV (ex: NF e NFD).
    """
    try:
        data = request.get_json()
        numnota = data.get('numnota')
        codemp = data.get('codemp')
        
        if not numnota or not codemp:
            return jsonify({
                'success': False,
                'error': 'Número da NF (numnota) e Código da Empresa (codemp) são obrigatórios.'
            }), 400
        
        if not sankhya_avaria_service or not sankhya_avaria_service.sankhya.enabled:
            return jsonify({
                'success': False,
                'error': 'Serviço Sankhya indisponível.'
            }), 503
            
        # Chamar o novo método que retorna a lista
        # Usamos o método interno do SankhyaService que retorna a lista completa
        notas_list_raw = sankhya_avaria_service.sankhya.buscar_notas_fiscais_list(
            numnota=numnota,
            codemp=codemp
        )
        
        if not notas_list_raw:
            return jsonify({
                'success': True,
                'count': 0,
                'notas': [],
                'message': f'Nenhuma nota encontrada para {numnota} na empresa {codemp}'
            })
            
        # Preparar dados para retorno (apenas campos essenciais para seleção)
        notas_formatadas = []
        for nota in notas_list_raw:
            # Buscar transportadora (usando cache)
            transportadora_nome = nota.get('NOMEPARC_TRANSP', 'N/A')
            
            # O serviço de avaria já tem o cache injetado
            if sankhya_avaria_service.cache_sankhya and nota.get('CODPARCTRANSP'):
                 try:
                    codparc_int = int(nota.get('CODPARCTRANSP'))
                    transp_data = sankhya_avaria_service.cache_sankhya.obter_transportadoras().get(codparc_int)
                    if transp_data:
                        transportadora_nome = transp_data.get('nomeparc')
                 except:
                    # Ignore cache failure
                    pass
            
            notas_formatadas.append({
                'numnota': nota.get('NUMNOTA'),
                'nunota': nota.get('NUNOTA'),
                'codemp': nota.get('CODEMP'),
                'tipmov': nota.get('TIPMOV'),
                'vlrnota': nota.get('VLRNOTA'),
                'transportadora': transportadora_nome,
                'data_exibicao': _format_dtneg_display(nota.get('DTNEG')),
                'descricao_tipo': 'Venda' if nota.get('TIPMOV') == 'V' else 'Devolução' if nota.get('TIPMOV') == 'D' else nota.get('TIPMOV')
            })
            
        return jsonify({
            'success': True,
            'count': len(notas_formatadas),
            'notas': notas_formatadas
        })
        
    except Exception as e:
        logger.error(f"Erro ao buscar múltiplas notas: {e}")
        logger.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': f'Erro interno: {str(e)}'
        }), 500

@wms_bp.route('/baixas-multiplas', methods=['POST'])
@module_required('wms')
@screen_required('wms', 'avarias_dashboard', nivel='editar')
def baixas_multiplas():
    """
    Aplica data de pagamento, status de indenização e automações para múltiplas avarias.
    Aceita tanto 'ids' (legado) quanto 'avaria_ids' no payload.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'Payload JSON inválido'}), 400

        # Compatibilidade: aceita 'ids' (legado) e 'avaria_ids' (novo)
        avaria_ids = data.get('avaria_ids') or data.get('ids', [])
        data_pagamento = data.get('data_pagamento', '')
        status_indenizacao = data.get('status_indenizacao', '')
        observacao_lote = data.get('observacao', '')
        user_email = session.get('user_email', 'sistema')
        user_empresa = session.get('user_empresa')

        if not avaria_ids or not data_pagamento:
            return jsonify({
                'success': False,
                'message': 'IDs e data de pagamento são obrigatórios'
            }), 400

        logger.info(f"Baixa múltipla: {len(avaria_ids)} avarias por {user_email}")
        logger.info(f"   Data pgto: {data_pagamento}, Status: {status_indenizacao or 'N/A'}")

        resultados = []
        erros = []

        for avaria_id in avaria_ids:
            try:
                avaria = wms_service.get_avaria_by_id(avaria_id)
                if not avaria:
                    erros.append({'id': avaria_id, 'erro': 'Avaria não encontrada'})
                    continue

                # Montar campos de atualização
                updates = {'data_pagamento': data_pagamento}

                if status_indenizacao:
                    updates['status_indenizacao'] = status_indenizacao

                if observacao_lote:
                    obs_existente = getattr(avaria, 'observacao', '') or ''
                    nova_obs = f"{obs_existente}\n[Baixa Lote - {user_email}] {observacao_lote}".strip()
                    updates['observacao'] = nova_obs

                # Atualizar no Sheets
                success, message = wms_service.update_avaria(
                    avaria_id, alterado_por=session.get('user_email', ''), **updates
                )

                if success:
                    resultados.append(avaria_id)
                    logger.info(f"  {avaria_id} atualizado")

                    # Disparar automação de status
                    if automacao_service and data_pagamento:
                        try:
                            empresa_avaria = getattr(avaria, 'empresa', user_empresa) or user_empresa
                            resultados_auto = automacao_service.aplicar(
                                trigger='baixa_pagamento',
                                avaria_id=avaria_id,
                                empresa=empresa_avaria,
                                contexto={},
                                operador_email=user_email,
                            )
                            msgs_bloqueio = automacao_service.extrair_erros_visiveis(resultados_auto)
                            if msgs_bloqueio:
                                logger.warning(f"  Automação bloqueada para {avaria_id}: {msgs_bloqueio}")
                            else:
                                aplicadas = [r for r in resultados_auto if r.sucesso]
                                if aplicadas:
                                    logger.info(f"  Automação aplicada para {avaria_id}: {[r.novo_status for r in aplicadas]}")
                        except Exception as e_auto:
                            logger.error(f"  Erro na automação de {avaria_id}: {e_auto}")
                else:
                    erros.append({'id': avaria_id, 'erro': message})

            except Exception as e_item:
                logger.error(f"Erro ao processar avaria {avaria_id}: {e_item}")
                erros.append({'id': avaria_id, 'erro': str(e_item)})

        logger.info(f"Baixas múltiplas: {len(resultados)} atualizadas por {user_email}")

        # Invalidar cache após atualização em lote
        if resultados and hasattr(wms_service, 'sheets') and hasattr(wms_service.sheets, 'invalidate_wms_cache'):
            wms_service.sheets.invalidate_wms_cache()

        return jsonify({
            'success': True,
            'atualizados': len(resultados),
            'total': len(avaria_ids),
            'erros': erros,
            'message': f"{len(resultados)} avaria(s) atualizada(s) com sucesso."
        })

    except Exception as e:
        logger.error(f"Erro em baixas múltiplas: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500
        
# IMPRESSÃO DE ETIQUETAS (Existing)

@wms_bp.route('/imprimir/inicial/<avaria_id>', methods=['POST'])
@module_required('wms')
def imprimir_etiqueta_inicial(avaria_id):
    # ... existing implementation ...
    avaria = wms_service.get_avaria_by_id(avaria_id)
    if not avaria:
        return jsonify({'success': False, 'error': 'Avaria não encontrada'}), 404

    # O printing_service foi corrigido para usar avaria.nota
    success, message = printing_service.print_etiqueta_inicial(avaria)
    return jsonify({'success': success, 'message': message})

@wms_bp.route('/imprimir/final/<avaria_id>', methods=['POST'])
@module_required('wms')
def imprimir_etiqueta_final(avaria_id):
    # ... existing implementation ...
    avaria = wms_service.get_avaria_by_id(avaria_id)
    if not avaria:
        return jsonify({'success': False, 'error': 'Avaria não encontrada'}), 404
    
    # O printing_service foi corrigido para usar avaria.nota
    success, message = printing_service.print_etiqueta_final(avaria)
    return jsonify({'success': success, 'message': message})

@wms_bp.route('/api/registrar_impressao', methods=['POST'])
@module_required('wms')
def registrar_impressao():
    """
    Chamado pelo frontend (print_direct.js) após confirmação de sucesso
    na impressora Brother.

    Payload JSON:
      { "avaria_id": "AV-0001-001", "tipo": "inicial" | "final" }

    Ações:
      - Para 'inicial': marca etiqueta_inicial_impressa = 'sim' na avaria
      - Dispara trigger 'etiqueta_inicial_gerada' ou 'etiqueta_final_gerada'
      - Se automação bloqueada: retorna erro 409 com mensagem visível

    Response (sucesso):
      { "success": true, "message": "...", "automacoes": [...] }

    Response (bloqueio):
      { "success": false, "bloqueado": true, "message": "mensagem do bloqueio" }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Payload inválido'}), 400

        avaria_id = (data.get('avaria_id') or '').strip()
        tipo      = (data.get('tipo') or '').strip().lower()  # 'inicial' ou 'final'

        if not avaria_id:
            return jsonify({'success': False, 'error': 'avaria_id obrigatório'}), 400
        if tipo not in ('inicial', 'final'):
            return jsonify({'success': False, 'error': "tipo deve ser 'inicial' ou 'final'"}), 400

        user_email  = session.get('user_email', 'sistema')
        user_empresa = session.get('user_empresa', '')

        # Verificar se avaria existe
        avaria = wms_service.get_avaria_by_id(avaria_id)
        if not avaria:
            return jsonify({'success': False, 'error': 'Avaria não encontrada'}), 404

        # Para etiqueta inicial: marcar como impressa
        if tipo == 'inicial':
            ok, msg = wms_service.update_avaria(avaria_id, etiqueta_inicial_impressa='sim')
            if not ok:
                logger.warning(f"Não foi possível marcar etiqueta_inicial_impressa: {msg}")

        # Bloco removido a pedido do usuário: não deve baixar etiqueta por impressão.
        # if tipo == 'final':
        #     try:
        #         ...


        # Disparar automação
        trigger = f'etiqueta_{tipo}_gerada'  # 'etiqueta_inicial_gerada' ou 'etiqueta_final_gerada'
        resultados_auto = []

        if automacao_service:
            empresa_avaria = getattr(avaria, 'empresa', user_empresa) or user_empresa
            
            # Contexto enriquecido com localização para facilitar matching de regras
            ctx = {
                'tipo_etiqueta': tipo,
                'local': getattr(avaria, 'local', ''),
                'codlocal': getattr(avaria, 'local', ''),
                'cod_triagem': getattr(avaria, 'cod_triagem', ''),
            }
            
            # Fallback para dados do item se header estiver vazio
            if not ctx['local']:
                itens = wms_service.get_itens_avaria(avaria_id)
                if itens:
                    item = itens[0]
                    ctx['codlocalorig'] = str(item.get('codlocalorig', '')).strip()
                    ctx['local_item']   = str(item.get('localizacao', '') or item.get('local', '')).strip()
                    ctx['local']        = ctx['local_item'] or ctx['codlocalorig']
            
            resultados_auto = automacao_service.aplicar(
                trigger=trigger,
                avaria_id=avaria_id,
                empresa=empresa_avaria,
                contexto=ctx,
                operador_email=user_email,
            )

            # Retornar erro visível se automação bloqueou
            msgs_bloqueio = automacao_service.extrair_erros_visiveis(resultados_auto)
            if msgs_bloqueio:
                return jsonify({
                    'success': False,
                    'bloqueado': True,
                    'message': msgs_bloqueio[0],
                }), 409  # Conflict — operação válida mas bloqueada por regra de negócio

        aplicadas = [r.to_dict() for r in resultados_auto if r.sucesso]
        logger.info(
            f"🏷️ Impressão {tipo} registrada: {avaria_id} por {user_email} "
            f"({len(aplicadas)} automação(ões) aplicada(s))"
        )

        return jsonify({
            'success':   True,
            'message':   f"Impressão da etiqueta {tipo} registrada com sucesso",
            'automacoes': aplicadas,
        })

    except Exception as e:
        logger.error(f"registrar_impressao: {e}")
        import traceback; logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500

# API (Existing) - UPDATED TO INCLUDE ITEMS

@wms_bp.route('/api/avaria/<avaria_id>', methods=['GET'])
@module_required('wms')
def get_avaria_json(avaria_id):
    """
    API endpoint para obter detalhes de uma avaria específica, incluindo itens.
    Retorna JSON com todos os campos, incluindo formatações
    """
    try:
        user_empresa = session.get('user_empresa')
        user_level   = session.get('user_level', '')

        def _empresa_autorizada(avaria_obj):
            """Retorna False se o usuário não tem acesso a esta avaria."""
            if user_level == 'admin_global':
                return True
            if not user_empresa:
                return False
            return avaria_obj.empresa == user_empresa

        # Usar o serviço que retorna avaria + itens (sankhya_avaria_service)
        if not sankhya_avaria_service:
            # Fallback if Sankhya service isn't available, return header only
            avaria = wms_service.get_avaria_by_id(avaria_id)
            if not avaria:
                 return jsonify({'success': False, 'message': 'Avaria não encontrada'}), 404
            if not _empresa_autorizada(avaria):
                return jsonify({'success': False, 'message': 'Avaria não encontrada'}), 404

            avaria_dict = avaria.to_dict()
            avaria_dict['valor_nf_formatado'] = avaria.valor_nf_formatado

            # ADICIONAR NOVO CAMPO FORMATADO
            avaria_dict['valor_indenizacao_formatado'] = avaria.valor_indenizacao_formatado

            return jsonify({
                'success': True,
                'avaria': avaria_dict,
                'itens': []
            })

        avaria_detalhada = sankhya_avaria_service.obter_avaria_detalhada(avaria_id)

        if not avaria_detalhada:
            return jsonify({
                'success': False,
                'message': 'Avaria não encontrada'
            }), 404

        avaria = avaria_detalhada['avaria']

        if not _empresa_autorizada(avaria):
            return jsonify({'success': False, 'message': 'Avaria não encontrada'}), 404

        itens = avaria_detalhada['itens']
        
        # Converter avaria para dict e adicionar campos formatados
        avaria_dict = avaria.to_dict()
        
        # ⇋ ADICIONAR VALOR FORMATADO
        avaria_dict['valor_nf_formatado'] = avaria.valor_nf_formatado
        
        # ADICIONAR NOVO CAMPO FORMATADO
        avaria_dict['valor_indenizacao_formatado'] = avaria.valor_indenizacao_formatado 
        
        return jsonify({
            'success': True,
            'avaria': avaria_dict,
            'itens': itens # Retorna lista de dicionários de itens
        })
        
    except Exception as e:
        logger.error(f"Erro ao buscar avaria {avaria_id}: {e}")
        return jsonify({
            'success': False,
            'message': f'Erro ao buscar avaria: {str(e)}'
        }), 500

# ADICIONAR ESTA ROTA NO ARQUIVO modules/wms/routes/wms_routes.py
# Inserir após a seção "# API (Existing)"

@wms_bp.route('/api/alterar-status', methods=['POST'])
def alterar_status_wms():
    """
    API para alterar status de uma avaria
    Requer permissão: 'avarias_alterar_status'
    """
    try:
        import json
        data = request.get_json()
        
        avaria_id = data.get('avaria_id')
        novo_status = data.get('novo_status')
        observacao = data.get('observacao', '')
        user_email = session.get('user_email', 'sistema')
        
        # Validação
        if not avaria_id or not novo_status:
            return jsonify({
                'success': False,
                'error': 'Dados incompletos: avaria_id e novo_status são obrigatórios'
            }), 400
        
        # Validar status
        status_validos = ['Aguardando Triagem', 'Em Triagem', 'Estoque Disponível', 'Finalizada']
        if novo_status not in status_validos:
            return jsonify({
                'success': False,
                'error': f'Status inválido. Valores aceitos: {", ".join(status_validos)}'
            }), 400
        
        logger.info(f"↻ Alterando status da avaria {avaria_id} para '{novo_status}' por {user_email}")
        
        # Buscar avaria atual
        avaria = wms_service.get_avaria_by_id(avaria_id)
        if not avaria:
            return jsonify({
                'success': False,
                'error': 'Avaria não encontrada'
            }), 404
        
        status_anterior = avaria.status
        
        # Preparar dados de atualização
        updates = {
            'status': novo_status
        }
        
        # Adicionar observação se fornecida
        if observacao:
            observacao_completa = f"{avaria.observacao or ''}\n[{user_email}] Mudança de status de '{status_anterior}' para '{novo_status}': {observacao}".strip()
            updates['observacao'] = observacao_completa
        
        # Atualizar no Sheets
        success, message = wms_service.update_avaria(avaria_id, **updates)
        
        if success:
            logger.info(f"Status da avaria {avaria_id} alterado: {status_anterior} → {novo_status}")
            return jsonify({
                'success': True,
                'message': f'Status alterado de "{status_anterior}" para "{novo_status}"',
                'avaria_id': avaria_id,
                'novo_status': novo_status
            })
        else:
            logger.error(f"Falha ao alterar status: {message}")
            return jsonify({
                'success': False,
                'error': message
            }), 500
        
    except Exception as e:
        logger.error(f"Erro ao alterar status: {e}")
        logger.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': f'Erro interno: {str(e)}'
        }), 500

@wms_bp.route('/api/avaria/<avaria_id>/editar', methods=['PUT', 'POST'])
@module_required('wms')
@screen_required('wms', 'listar_avarias', nivel='editar')
def api_editar_avaria(avaria_id):
    """
    API para editar avaria existente
    ✅ CORRIGIDO: Usa update_avaria() + calcula valor_indenizacao
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'success': False, 'error': 'Dados não fornecidos'}), 400

        # FIX: definir variáveis de sessão no início da função
        user_empresa = session.get('user_empresa', '')
        user_email   = session.get('user_email', '')

        # Buscar avaria atual (precisamos do valor_nf/nfd atual para o cálculo)
        avaria = wms_service.get_avaria_by_id(avaria_id)
        if not avaria:
            return jsonify({'success': False, 'error': f'Avaria {avaria_id} não encontrada'}), 404
        
        # NOVO: Extrair arrays de itens novos e modificados
        itens_novos = data.pop('itens_novos', [])
        itens_modificados = data.pop('itens_modificados', [])
        
        logger.info(f"Editando avaria {avaria_id}:")
        logger.info(f"   - Campos do cabeçalho: {len(data)}")
        logger.info(f"   - Itens novos (split): {len(itens_novos)}")
        logger.info(f"   - Itens modificados: {len(itens_modificados)}")
        
        # Campos permitidos para edição do cabeçalho
        allowed_fields = [
            'cod_empresa', 'nota', 'nfd', 'quantidade', 'volume',
            'transportadora', 'cte',
            'valor_nf', 'valor_nfd', 'nfp', 'valor_nfp',
            'data_coleta', 'cod_rastreio_correios',
            'motivo_devolucao', 'indenizacao', 'prazo_pagamento', 'data_pagamento',
            'reembolso_pct', 'observacao',
            'status_estoque', 'status_indenizacao',
            'data_protocolo_mkt', 'cod_motivo_devolucao',
            'motivo_devolucao', 'cod_motivo_inicial', 'motivo_inicial', 'data_entrega'
        ]
        
        # Filtrar apenas campos permitidos
        updates = {k: v for k, v in data.items() if k in allowed_fields}

        # PONTO 3: Normalizar campos monetários para formato americano (ponto decimal)
        # Sempre força o formato "XXXX.XX" para evitar armazenamento com vírgula no Sheets
        _campos_monetarios = ['valor_nf', 'valor_nfd', 'valor_nfp', 'valor_indenizacao']
        for _campo in _campos_monetarios:
            if _campo in updates:
                updates[_campo] = f"{_normalizar_monetario(updates[_campo]):.2f}"
        # reembolso_pct: normaliza e mantém como float string
        if 'reembolso_pct' in updates:
            updates['reembolso_pct'] = f"{_normalizar_monetario(updates['reembolso_pct']):.2f}"

        # Corrigir campos monetários da avaria existente se estiverem com vírgula no Sheets
        # Isso resolve dados históricos que foram salvos com formato errado
        for _campo in _campos_monetarios + ['reembolso_pct']:
            _val_atual = getattr(avaria, _campo, None)
            if _val_atual and ',' in str(_val_atual) and _campo not in updates:
                updates[_campo] = f"{_normalizar_monetario(_val_atual):.2f}"
                logger.info(f"   Corrigindo formato de {_campo}: '{_val_atual}' → '{updates[_campo]}'")
        
        # RECALCULAR VALOR INDENIZAÇÃO SE NECESSÁRIO 
        if sankhya_avaria_service:

            # ── Ler locais configurados ANTES do gatilho (necessário para Bug 4) ──
            _locais_list = []
            try:
                import modules.wms.routes.parametrizacao_routes as _param_rt
                _cfg_svc_local = getattr(_param_rt, 'config_service', None)
                if _cfg_svc_local:
                    _raw_locais = _cfg_svc_local.get_config(
                        user_empresa, 'indenizacao_locais_contabilizar', ''
                    )
                    _locais_list = [l.strip() for l in _raw_locais.split(',') if l.strip()]
            except Exception as _e:
                logger.warning(f"Erro ao ler locais indenizáveis: {_e}")

            # ── Gatilho do recálculo ────────────────────────────────────────────
            # Recalcula se:
            #   (a) campos financeiros/tipo mudaram no cabeçalho
            #   (b) itens foram modificados ou adicionados (mudança manual de local/qtd)
            #   (c) há locais configurados → sempre recalcula para capturar localizações
            #       atualizadas por triagem (etiqueta ativa bloqueia edição no frontend,
            #       então itens_modificados chega vazio mesmo após triagem mudar o local)
            recalc_needed = (
                'indenizacao'      in updates
                or 'reembolso_pct' in updates
                or 'valor_nf'      in updates
                or 'valor_nfd'     in updates
                or bool(itens_modificados)
                or bool(itens_novos)
                # ← sempre recalcula: itens têm prioridade sobre NF/NFD,
                #   então qualquer mudança de item (inclusive triagem) deve refletir
                or True
            )

            if recalc_needed:
                logger.info("Recalculando valor de indenização...")

                new_indenizacao       = updates.get('indenizacao', avaria.indenizacao)
                new_reembolso_pct_raw = updates.get('reembolso_pct', avaria.reembolso_pct)
                try:
                    new_reembolso_pct = float(new_reembolso_pct_raw or 0)
                except (ValueError, TypeError):
                    new_reembolso_pct = 0.0

                new_valor_nf  = updates.get('valor_nf',  avaria.valor_nf)
                new_valor_nfd = updates.get('valor_nfd', avaria.valor_nfd)

                # ── Calcular valor base pelos itens ─────────────────────────────
                # Hierarquia: locais filtrados > todos os itens > NFD > NF
                _valor_itens_indenizaveis = 0.0
                try:
                    # 1. Ler itens atuais do Sheets (excluir 'baixada' — triagem cria nova linha)
                    _itens_db = [
                        i for i in wms_service._get_all_item_avarias()
                        if i.avaria_id == avaria_id
                        and getattr(i, 'status_etiqueta', '') != 'baixada'
                    ]
                    _itens_dicts = [i.to_dict() for i in _itens_db]

                    # 2. Mesclar localizações do payload (ainda não persistidas)
                    _loc_overrides = {
                        int(im['sequencia']): im['localizacao']
                        for im in itens_modificados
                        if 'localizacao' in im and 'sequencia' in im
                    }
                    for _it in _itens_dicts:
                        _seq = int(_it.get('sequencia', 0))
                        if _seq in _loc_overrides:
                            _loc_ant = _it.get('localizacao', '')
                            _it['localizacao'] = _loc_overrides[_seq]
                            logger.debug(f"   seq={_seq} loc: '{_loc_ant}' → '{_loc_overrides[_seq]}'")

                    # 3. Incluir itens novos (splits) do payload
                    for _novo in itens_novos:
                        _itens_dicts.append(dict(_novo))

                    if _locais_list:
                        # Locais configurados → filtrar somente itens nesses locais
                        _valor_itens_indenizaveis = (
                            sankhya_avaria_service.calcular_valor_base_por_locais(
                                _itens_dicts, _locais_list
                            )
                        )
                        logger.info(
                            f"   📍 Locais configurados → R$ {_valor_itens_indenizaveis:.2f} "
                            f"({len(_locais_list)} locais, {len(_itens_dicts)} itens)"
                        )
                    else:
                        # Sem locais → soma TODOS os itens (valor_item × qtd_avariada)
                        for _it in _itens_dicts:
                            try:
                                _vi = float(str(_it.get('valor_item', 0) or 0).replace(',', '.'))
                                _qa = float(str(_it.get('qtd_avariada', 1) or 1).replace(',', '.'))
                                _valor_itens_indenizaveis += _vi * _qa
                            except (ValueError, TypeError):
                                pass
                        logger.info(
                            f"   📍 Sem locais → soma todos os itens: "
                            f"R$ {_valor_itens_indenizaveis:.2f} ({len(_itens_dicts)} itens)"
                        )

                except Exception as _e:
                    logger.warning(f"Erro ao calcular valor itens: {_e}")
                    logger.debug("", exc_info=True)
                        
                valor_indenizacao_calc = sankhya_avaria_service.calculate_update_value(
                    valor_nf=str(new_valor_nf),
                    valor_nfd=str(new_valor_nfd),
                    indenizacao_type=new_indenizacao,
                    reembolso_pct=new_reembolso_pct,
                    valor_itens_indenizaveis=_valor_itens_indenizaveis,
                )

                updates['valor_indenizacao'] = f"{valor_indenizacao_calc:.2f}"
                logger.info(f"Valor indenização recalculado: R$ {valor_indenizacao_calc:.2f}")
        
        # CRÍTICO: Remover campos 'status' geral (não deve ser atualizado aqui)
        if 'status' in updates:
            del updates['status']
        if 'status_geral' in updates:
            del updates['status_geral']
        
        # 1⃣ ATUALIZAR CABEÇALHO DA AVARIA usando update_avaria
        if updates:
            logger.info(f"Atualizando cabeçalho da avaria {avaria_id}...")
            logger.info(f"   Campos a atualizar: {list(updates.keys())}")
            
            # USAR update_avaria() - método funcional simples
            success, message = wms_service.update_avaria(
                avaria_id, alterado_por=session.get('user_email', ''), **updates
            )
            
            if not success:
                logger.error(f"Erro ao atualizar cabeçalho: {message}")
                return jsonify({'success': False, 'error': f'Erro ao atualizar cabeçalho: {message}'}), 500
            
            logger.info(f"Cabeçalho atualizado: {list(updates.keys())}")
        
        # 2⃣ PROCESSAR ITENS MODIFICADOS (UPDATE)
        if itens_modificados:
            logger.info(f"Processando {len(itens_modificados)} itens modificados...")
            
            for item in itens_modificados:
                sequencia = item.get('sequencia')
                
                if not sequencia:
                    logger.warning(f"Item sem sequência, pulando: {item}")
                    continue
                
                # Preparar dados para atualização
                item_updates = {}
                
                if 'qtd_avariada' in item:
                    item_updates['qtd_avariada'] = str(item['qtd_avariada'])
                
                if 'localizacao' in item:
                    item_updates['localizacao'] = str(item['localizacao'])
                
                # Atualizar item
                success, msg = wms_service.update_item_avaria(
                    avaria_id, sequencia, alterado_por=session.get('user_email', ''), **item_updates
                )
                
                if not success:
                    logger.error(f"Erro ao atualizar item seq={sequencia}: {message}")
                else:
                    logger.info(f"Item seq={sequencia} atualizado: {item_updates}")
        
        # 3⃣ PROCESSAR ITENS NOVOS (INSERT)
        if itens_novos:
            logger.info(f"Processando {len(itens_novos)} itens novos (splitados)...")
            
            for item in itens_novos:
                # Validar campos obrigatórios
                if not all(k in item for k in ['sequencia', 'codprod', 'qtd_avariada']):
                    logger.warning(f"Item novo com campos faltando, pulando: {item}")
                    continue
                
                # Inserir novo item no fim da planilha
                success, message = wms_service.inserir_item_avaria(
                    avaria_id=avaria_id,
                    sequencia=int(item['sequencia']),
                    codprod=str(item['codprod']),
                    descrprod=str(item.get('descrprod', '')),
                    qtdneg=float(str(item.get('qtdneg', 0)).replace(',', '.')),
                    qtd_avariada=float(str(item['qtd_avariada']).replace(',', '.')),
                    localizacao=str(item.get('localizacao', '')),
                    valor_item=float(str(item.get('valor_item', 0)).replace(',', '.')),
                    codlocalorig=str(item.get('codlocalorig', '')),
                    codlocaldest=str(item.get('codlocaldest', '')),
                    codgrupoprod=str(item.get('codgrupoprod', '')),
                    codemp=str(item.get('codemp', ''))
                )
                
                if not success:
                    logger.error(f"Erro ao inserir item seq={item['sequencia']}: {message}")
                else:
                    logger.info(f"Item seq={item['sequencia']} inserido com sucesso")
        
        # 4⃣ INVALIDAR CACHE WMS — garante que a listagem buscará dados frescos
        # Usa o mesmo padrão da baixa múltipla (invalidate_wms_cache)
        # Não causa 429: apenas zera ponteiros em memória, sem chamada ao Sheets
        try:
            if hasattr(wms_service, 'sheets') and hasattr(wms_service.sheets, 'invalidate_wms_cache'):
                wms_service.sheets.invalidate_wms_cache()
        except Exception as e_cache:
            logger.warning(f"Falha ao invalidar cache WMS: {e_cache}")
        
        return jsonify({
            'success': True,
            'message': 'Avaria atualizada com sucesso',
            'detalhes': {
                'cabecalho_atualizado': len(updates) > 0,
                'itens_modificados': len(itens_modificados),
                'itens_novos': len(itens_novos)
            }
        })
        
    except Exception as e:
        logger.error(f"Erro ao editar avaria: {e}")
        logger.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': f'Erro interno: {str(e)}'
        }), 500

@wms_bp.route('/api/avarias/baixa-multipla', methods=['POST'])
@module_required('wms')
@screen_required('wms', 'avarias_dashboard', nivel='editar')
def api_baixa_multipla():
    """
    API para aplicar data de pagamento, status e observação em múltiplas avarias.
    
    Payload JSON:
    {
        "avaria_ids": ["AV-0001-001", "AV-0001-002", ...],
        "data_pagamento": "2025-01-15",
        "status_indenizacao": "Pago",         // opcional
        "observacao": "Pagamento lote jan/25"  // opcional
    }
    
    Response:
    {
        "success": true,
        "atualizados": 5,
        "erros": [ {"id": "AV-xxx", "erro": "msg"} ],
        "message": "5 avaria(s) atualizada(s) com sucesso"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Payload JSON inválido'}), 400
        
        avaria_ids = data.get('avaria_ids', [])
        data_pagamento = data.get('data_pagamento', '')
        status_indenizacao = data.get('status_indenizacao', '')
        tipo_baixa = data.get('tipo_baixa', '')  # 'total' ou 'parcial' (opcional)        
        observacao_lote = data.get('observacao', '')
        user_email = session.get('user_email', 'sistema')
        user_empresa = session.get('user_empresa')

        # Validações
        if not avaria_ids:
            return jsonify({'success': False, 'error': 'Nenhuma avaria selecionada'}), 400
        
        if not data_pagamento:
            return jsonify({'success': False, 'error': 'Data de pagamento é obrigatória'}), 400
        
        if len(avaria_ids) > 100:
            return jsonify({'success': False, 'error': 'Máximo 100 avarias por operação'}), 400

        logger.info(f"Baixa múltipla: {len(avaria_ids)} avarias por {user_email}")
        logger.info(f"   Data pgto: {data_pagamento}, Status: {status_indenizacao or 'N/A'}")

        resultados = []
        erros = []

        for avaria_id in avaria_ids:
            try:
                # Verificar se avaria pertence à empresa do usuário
                avaria = wms_service.get_avaria_by_id(avaria_id)
                if not avaria:
                    erros.append({'id': avaria_id, 'erro': 'Avaria não encontrada'})
                    continue
                
                if hasattr(avaria, 'empresa') and avaria.empresa != user_empresa:
                    erros.append({'id': avaria_id, 'erro': 'Sem permissão (empresa diferente)'})
                    continue

                # Montar campos de atualização
                updates = {
                    'data_pagamento': data_pagamento
                }

                if status_indenizacao:
                    updates['status_indenizacao'] = status_indenizacao

                if observacao_lote:
                    # Concatenar observação sem substituir a existente
                    obs_existente = getattr(avaria, 'observacao', '') or ''
                    nova_obs = f"{obs_existente}\n[Baixa Lote - {user_email}] {observacao_lote}".strip()
                    updates['observacao'] = nova_obs

                # Atualizar no Sheets
                success, message = wms_service.update_avaria(avaria_id, **updates)

                if success:
                    resultados.append(avaria_id)
                    logger.info(f"  {avaria_id} atualizado")

                    # ── Disparar automação de status ──────────────────────
                    if automacao_service and data_pagamento:
                        try:
                            empresa_avaria = getattr(avaria, 'empresa', user_empresa) or user_empresa
                            resultados_auto = automacao_service.aplicar(
                                trigger='baixa_pagamento',
                                avaria_id=avaria_id,
                                empresa=empresa_avaria,
                                contexto={},   # campo indenizacao lido direto da avaria
                                operador_email=user_email,
                            )

                            # ADICIONE ESTES LOGS:
                            logger.info(f"Automação disparada para {avaria_id}: {len(resultados_auto)} regra(s) avaliada(s)")
                            for r in resultados_auto:
                                logger.info(f"   → Regra {r.auto_id} | status={r.status} | msg={r.msg}")                            
                            # Checar se alguma automação bloqueou
                            msgs_bloqueio = automacao_service.extrair_erros_visiveis(resultados_auto)
                            if msgs_bloqueio:
                                # Não reverte o pagamento, mas reporta o bloqueio como aviso
                                for msg_b in msgs_bloqueio:
                                    logger.warning(f"  Automação bloqueada para {avaria_id}: {msg_b}")
                                erros.append({
                                    'id': avaria_id,
                                    'erro': f"Pagamento registrado, mas automação bloqueada: {msgs_bloqueio[0]}"
                                })
                        except Exception as auto_err:
                            logger.warning(f"  Automação baixa ignorada para {avaria_id}: {auto_err}")
                else:
                    erros.append({'id': avaria_id, 'erro': message})
                    logger.warning(f"  {avaria_id}: {message}")

            except Exception as item_error:
                erros.append({'id': avaria_id, 'erro': str(item_error)})
                logger.error(f"  {avaria_id}: {item_error}")

        # Invalidar cache WMS após operação em lote
        if resultados and hasattr(wms_service, 'sheets') and hasattr(wms_service.sheets, 'invalidate_wms_cache'):
            wms_service.sheets.invalidate_wms_cache()

        logger.info(f"Baixa múltipla concluída: {len(resultados)} OK, {len(erros)} erros")

        return jsonify({
            'success': True,
            'atualizados': len(resultados),
            'erros': erros,
            'message': f'{len(resultados)} avaria(s) atualizada(s) com sucesso'
        })

    except Exception as e:
        logger.error(f"Erro na baixa múltipla: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': f'Erro interno: {str(e)}'
        }), 500

@wms_bp.route('/exportar/consultar-avarias', methods=['POST'])
@module_required('wms')
@screen_required('wms', 'avarias_dashboard')
def exportar_consultar_avarias():
    """
    Exporta Consultar Avarias para Excel com itens (uma linha por item).

    Recebe JSON com:
    - dados: Lista de objetos com dados das linhas visíveis. Cada objeto deve
             conter 'avaria_id' e os campos da avaria coletados da tabela HTML.
    - filtros_ativos: Dicionário com filtros aplicados (opcional)
    """
    try:
        data = request.get_json()

        if not data or 'dados' not in data:
            return jsonify({'success': False, 'error': 'Nenhum dado recebido'}), 400

        dados = data['dados']
        filtros_ativos = data.get('filtros_ativos', {})

        # Coletar IDs únicos das avarias visíveis (strip para evitar espaços do Sheets)
        avaria_id_set = {row.get('avaria_id', '').strip() for row in dados if row.get('avaria_id', '').strip()}

        # Buscar todos os itens e indexar por avaria_id
        # Exclui etiquetas 'baixada' (históricas de movimentação) — igual ao registrar avarias
        itens_por_avaria: dict = {}
        if avaria_id_set:
            all_itens = wms_service._get_all_item_avarias()
            for item in all_itens:
                if item.status_etiqueta == 'baixada':
                    continue
                aid = (item.avaria_id or '').strip()
                if aid in avaria_id_set:
                    itens_por_avaria.setdefault(aid, []).append(item.to_dict())

        # Calcular valor_indenizacao por item usando a mesma parametrização do registro
        if itens_por_avaria and sankhya_avaria_service:
            try:
                # Carregar avarias reais do Sheets para obter indenizacao e reembolso_pct
                user_empresa_export = session.get('user_empresa')
                avarias_reais = wms_service.get_all_avarias(empresa=user_empresa_export)
                avarias_map = {av.id.strip(): av for av in avarias_reais if av.id}

                # Buscar locais indenizáveis configurados
                locais_list = []
                try:
                    import modules.wms.routes.parametrizacao_routes as _param_rt_exp
                    _cfg_svc_exp = getattr(_param_rt_exp, 'config_service', None)
                    if _cfg_svc_exp:
                        raw_locais = _cfg_svc_exp.get_config(user_empresa_export, 'indenizacao_locais_contabilizar', '')
                        locais_list = [l.strip() for l in raw_locais.split(',') if l.strip()]
                except Exception as _e_loc:
                    logger.warning(f"Erro ao buscar locais indenizáveis (export): {_e_loc}")

                for aid, itens_lista in itens_por_avaria.items():
                    avaria_real = avarias_map.get(aid)
                    if not avaria_real:
                        continue
                    indenizacao_tipo = str(avaria_real.indenizacao or '').strip()
                    reembolso_pct = float(str(avaria_real.reembolso_pct or 0).replace(',', '.'))

                    for item_d in itens_lista:
                        loc = str(item_d.get('localizacao', '') or '').strip()
                        codlocaldest = str(item_d.get('codlocaldest', '') or '').strip()
                        campo_loc = loc if loc else codlocaldest

                        # Verificar se item está em local indenizável (ou sem configuração = todos)
                        item_indenizavel = False
                        if locais_list:
                            item_indenizavel = sankhya_avaria_service._bate_local(campo_loc, locais_list)
                        else:
                            item_indenizavel = True

                        if item_indenizavel:
                            vi = float(str(item_d.get('valor_item', 0) or 0).replace(',', '.'))
                            qa = float(str(item_d.get('qtd_avariada', 1) or 1).replace(',', '.'))
                            valor_base_item = vi * qa
                            tipo_norm = indenizacao_tipo.lower()
                            if tipo_norm in ('total', 'indenização total', 'indenizacao total'):
                                item_d['valor_indenizacao'] = round(valor_base_item, 2)
                            elif tipo_norm in ('nao_indenizavel', 'não indenizável', 'nao indenizavel', 'não indenizavel'):
                                item_d['valor_indenizacao'] = 0.0
                            else:
                                item_d['valor_indenizacao'] = round(valor_base_item * reembolso_pct / 100.0, 2)
                        else:
                            item_d['valor_indenizacao'] = 0.0
            except Exception as _e_ind:
                logger.warning(f"Erro ao calcular valor_indenizacao por item (export): {_e_ind}")

        total_itens = sum(len(v) for v in itens_por_avaria.values())
        logger.info(
            f"📊 Exportando Consultar Avarias com itens: "
            f"{len(dados)} avarias, {total_itens} itens encontrados"
        )

        excel_file = export_service.exportar_consultar_avarias_com_itens(
            avarias=dados,
            itens_por_avaria=itens_por_avaria,
            filtros_ativos=filtros_ativos
        )

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"consultar_avarias_{timestamp}.xlsx"
        logger.info(f"Excel gerado: {filename}")

        return send_file(
            excel_file,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        logger.error(f"Erro ao exportar Consultar Avarias: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500
        
@wms_bp.route('/exportar/registrar-avarias', methods=['POST'])
@module_required('wms')
@screen_required('wms', 'avarias_registrar')
def exportar_registrar_avarias():
    """
    Exporta dados filtrados de Registrar Avarias para Excel (formato agrupado)
    
    Recebe JSON com:
    - dados: Lista de grupos {avaria: {...}, itens: [{...}, ...]}
    - colunas_visiveis: Lista de {name, key, type} das colunas visíveis
    - totalizadores: Dicionário com totais (opcional)
    - filtros_ativos: Dicionário com filtros aplicados (opcional)
    """
    try:
        data = request.get_json()
        
        if not data or 'dados' not in data or 'colunas_visiveis' not in data:
            return jsonify({
                'success': False,
                'error': 'Dados ou colunas visíveis não fornecidos'
            }), 400
        
        dados = data['dados']
        colunas_visiveis = data['colunas_visiveis']
        totalizadores = data.get('totalizadores', {})
        filtros_ativos = data.get('filtros_ativos', {})
        
        # Contar total de itens
        total_itens = sum(len(grupo.get('itens', [])) for grupo in dados)
        
        logger.info(f"Exportando Registrar Avarias: {len(dados)} grupos, {total_itens} itens, {len(colunas_visiveis)} colunas")
        
        # Gerar Excel
        excel_file = export_service.exportar_registrar_avarias(
            dados=dados,
            colunas_visiveis=colunas_visiveis,
            totalizadores=totalizadores,
            filtros_ativos=filtros_ativos
        )
        
        # Nome do arquivo com timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"registrar_avarias_{timestamp}.xlsx"
        
        logger.info(f"Excel gerado: {filename}")
        
        return send_file(
            excel_file,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        logger.error(f"Erro ao exportar Registrar Avarias: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@wms_bp.route('/api/dashboard/financeiro')
@module_required('wms')
@screen_required('wms', 'avarias_dashboard')
def api_dashboard_financeiro():
    """API: dados do painel financeiro (KPIs e gráficos de avarias).
    O gráfico de evolução de estoque é carregado separadamente via
    /api/dashboard/financeiro/evolucao para não bloquear esta resposta.
    """
    try:
        filtros  = _parse_filtros_request()
        avarias  = _get_avarias_filtradas(filtros)
        empresa  = session.get('user_empresa')
        cfg      = _get_status_configs(empresa)

        status_indenizaveis = _parse_status_list(cfg, 'dash_status_indenizaveis', [])
        status_pago         = _parse_status_list(cfg, 'dash_status_pago', [])
        status_pago_parcial = _parse_status_list(cfg, 'dash_status_pago_parcial', [])
        status_pendente     = _parse_status_list(cfg, 'dash_status_pendente', [])
        status_em_atraso    = _parse_status_list(cfg, 'dash_status_em_atraso', [])

        dados = calcular_financeiro(
            avarias,
            data_inicio=filtros.get('data_inicio'),
            data_fim=filtros.get('data_fim'),
            status_indenizaveis = status_indenizaveis or None,
            status_pago         = status_pago or None,
            status_pago_parcial = status_pago_parcial or None,
            status_pendente     = status_pendente or None,
            status_em_atraso    = status_em_atraso or None,
        )

        return jsonify({'success': True, 'data': {
            **dados,
            'status_config': {
                'pago':     status_pago or [],
                'pendente': status_pendente or [],
                'atraso':   status_em_atraso or [],
            }
        }})
    except Exception as e:
        logger.error(f"API dashboard financeiro: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@wms_bp.route('/api/dashboard/financeiro/evolucao')
@module_required('wms')
@screen_required('wms', 'avarias_dashboard')
def api_dashboard_financeiro_evolucao():
    """API dedicada ao gráfico de Evolução de Saldo de Estoque WMS.
    Lê o snapshot do Google Sheets de forma isolada para não impactar
    os outros visuais do painel financeiro.
    """
    try:
        filtros = _parse_filtros_request()
        empresa = session.get('user_empresa')
        cfg     = _get_status_configs(empresa)

        evolucao_codlocais_str = cfg.get('dash_evolucao_codlocais', '')
        evolucao_codlocais = [s.strip() for s in evolucao_codlocais_str.split(',') if s.strip()] if evolucao_codlocais_str else []

        svc_estoque = getattr(current_app, 'historico_estoque_service', None)
        if not svc_estoque:
            return jsonify({'success': False, 'error': 'HistoricoEstoqueService indisponível'}), 503

        grafico = svc_estoque.construir_grafico_evolucao(
            cod_empresa=filtros.get('cod_empresa') or None,
            codlocais=evolucao_codlocais or None,
            data_inicio=filtros.get('data_inicio') or None,
            data_fim=filtros.get('data_fim') or None,
        )

        return jsonify({'success': True, 'data': grafico or {'labels': [], 'saldos': [], 'valores': [], 'modo': 'mensal', 'atualizado_em': None, 'snap_vazio': True}})
    except Exception as e:
        logger.error(f"API evolucao estoque: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@wms_bp.route('/api/estoque/evolucao')
@module_required('wms')
@screen_required('wms', 'avarias_dashboard')
def api_estoque_evolucao():
    """API: dados brutos do WMS_Evolucao_Estoque para validação/tabela."""
    try:
        data_inicio  = request.args.get('data_inicio', '')
        data_fim     = request.args.get('data_fim', '')
        cod_empresa  = request.args.get('cod_empresa', '')
        codlocal     = request.args.get('codlocal', '')
        page         = max(1, int(request.args.get('page', 1)))
        per_page     = min(200, max(1, int(request.args.get('per_page', 50))))

        if not wms_service or not wms_service.sheets:
            return jsonify({'success': False, 'error': 'Sheets não conectado'}), 500

        ws   = wms_service.sheets.sheet.worksheet('WMS_Evolucao_Estoque')
        rows = ws.get_all_values()
        if len(rows) < 2:
            return jsonify({
                'success': True, 'data': [], 'total': 0, 'sheet_rows': max(0, len(rows) - 1),
                'headers': rows[0] if rows else [], 'pages': 0, 'page': page,
            })

        headers = rows[0]
        dt_ini_f = dt_fim_f = None
        if data_inicio:
            try:
                dt_ini_f = datetime.strptime(data_inicio, '%Y-%m-%d')
            except ValueError:
                pass
        if data_fim:
            try:
                dt_fim_f = datetime.strptime(data_fim, '%Y-%m-%d')
            except ValueError:
                pass

        def _to_float(v):
            try:
                s = str(v).replace('.', '').replace(',', '.').strip()
                return float(s) if s else 0.0
            except Exception:
                return 0.0

        def _parse_dp(dp_str):
            for fmt in ('%d/%m/%Y', '%d/%m/%Y %H:%M:%S', '%Y-%m-%d'):
                try: return datetime.strptime(dp_str[:len(fmt.replace('%d','DD').replace('%m','MM').replace('%Y','YYYY').replace('%H','HH').replace('%M','MI').replace('%S','SS'))], fmt)
                except Exception: pass
            try: return datetime.strptime(dp_str[:10], '%d/%m/%Y')
            except Exception: return None

        # Agrupa por (mes_ref, codlocal) usando o 1º dia capturado do mês (= abertura)
        # mes_ref derivado: 1º dia do mês da data_posicao
        grupos = {}  # chave: (mes_ref_str, codlocal)
        for row in rows[1:]:
            if not any(v.strip() for v in row if isinstance(v, str)):
                continue
            rec = dict(zip(headers, row + [''] * max(0, len(headers) - len(row))))
            if cod_empresa and str(rec.get('cod_empresa', '')) not in ('', str(cod_empresa)):
                continue
            if codlocal and str(rec.get('codlocal', '')) != str(codlocal):
                continue
            dp_str = str(rec.get('data_posicao', ''))
            dt_h = None
            if dp_str:
                try: dt_h = datetime.strptime(dp_str[:10], '%d/%m/%Y')
                except Exception: pass
            if dt_h and (dt_ini_f or dt_fim_f):
                if dt_ini_f and dt_h < dt_ini_f:
                    continue
                if dt_fim_f and dt_h > dt_fim_f:
                    continue
            if dt_h is None:
                continue
            mes_ref_str = datetime(dt_h.year, dt_h.month, 1).strftime('%d/%m/%Y')
            cl = str(rec.get('codlocal', ''))
            chave = (mes_ref_str, cl)
            if chave not in grupos:
                grupos[chave] = {
                    'codlocal':    cl,
                    'descrlocal':  rec.get('descrlocal', ''),
                    'mes_ref':     mes_ref_str,
                    'saldo':       0.0,
                    'valor_total': 0.0,
                    '_dia_min':    dt_h.day,
                    '_qtd_dia_min': 0.0,
                    '_val_dia_min': 0.0,
                }
            # Mantém apenas os valores do 1º dia capturado (abertura)
            if dt_h.day < grupos[chave]['_dia_min']:
                grupos[chave]['_dia_min']     = dt_h.day
                grupos[chave]['_qtd_dia_min'] = _to_float(rec.get('qtd_total', 0))
                grupos[chave]['_val_dia_min'] = _to_float(rec.get('valor_total', 0))
            elif dt_h.day == grupos[chave]['_dia_min']:
                grupos[chave]['_qtd_dia_min'] += _to_float(rec.get('qtd_total', 0))
                grupos[chave]['_val_dia_min'] += _to_float(rec.get('valor_total', 0))

        registros = []
        for g in grupos.values():
            registros.append({
                'codlocal':    g['codlocal'],
                'descrlocal':  g['descrlocal'],
                'mes_ref':     g['mes_ref'],
                'saldo':       g['_qtd_dia_min'],
                'valor_total': g['_val_dia_min'],
            })
        registros.sort(key=lambda r: (r.get('mes_ref', ''), r.get('codlocal', '')))

        total  = len(registros)
        offset = (page - 1) * per_page
        return jsonify({
            'success':    True,
            'data':       registros[offset:offset + per_page],
            'total':      total,
            'sheet_rows': len(rows) - 1,
            'page':       page,
            'per_page':   per_page,
            'pages':      max(1, (total + per_page - 1) // per_page),
        })
    except Exception as e:
        logger.error(f"API estoque evolucao: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@wms_bp.route('/api/estoque/snapshot-sankhya')
@module_required('wms')
@screen_required('wms', 'avarias_dashboard')
def api_estoque_snapshot_sankhya():
    """
    Consulta estoque via CRUDServiceProvider.loadView (VW_ESTOQUE_DASH).
    Requer que a view exista no Oracle/Sankhya.
    """
    try:
        svc = getattr(current_app, 'historico_estoque_service', None)
        if not svc:
            return jsonify({
                'success': False,
                'error': 'HistoricoEstoqueService não disponível (Sankhya inativo ou desabilitado na config)',
            }), 503

        cod_empresa = request.args.get('empresa', '').strip()
        codlocal    = request.args.get('codlocal', '').strip()
        data_ref    = (request.args.get('data_ref', '') or request.args.get('data_inicio', '')).strip()
        data_fim    = request.args.get('data_fim', '').strip()

        for v in ('', 'undefined', 'null'):
            if data_ref == v: data_ref = datetime.now().strftime('%Y-%m-%d')
            if data_fim == v: data_fim = ''

        logger.info(f"[API Sankhya AberturaMesRef] data_ref={data_ref} | data_fim={data_fim} | empresa={cod_empresa} | local={codlocal}")

        resultado = svc.consultar_abertura_mes_ref(
            data_ref=data_ref,
            data_fim=data_fim or None,
            cod_empresa=cod_empresa or None,
            codlocal=codlocal or None,
        )

        if resultado.get('erro') and not resultado.get('registros'):
            return jsonify({'success': False, 'error': resultado['erro']}), 502

        return jsonify({
            'success':  True,
            'data':     resultado['registros'],
            'total':    resultado['total'],
            'mes_ref':  resultado.get('mes_ref', ''),
            'fonte':    'Sankhya · VW_ESTOQUE_DASH',
        })
    except Exception as e:
        logger.error(f"API snapshot sankhya: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@wms_bp.route('/api/estoque/snapshot/grafico', methods=['POST'])
@module_required('wms')
@screen_required('wms', 'avarias_dashboard')
def api_atualizar_snapshot_grafico():
    """
    Inicia a consulta ao Sankhya em background thread e retorna 202 imediatamente.
    O cliente deve fazer polling em GET /api/estoque/snapshot/grafico/status.
    """
    try:
        svc = getattr(current_app, 'historico_estoque_service', None)
        if not svc:
            return jsonify({'success': False, 'error': 'HistoricoEstoqueService indisponível'}), 503

        empresa = session.get('user_empresa', '')
        svc.iniciar_snapshot_grafico_async(cod_empresa=empresa or None)
        return jsonify({'success': True, 'status': 'processing',
                        'message': 'Snapshot iniciado em background.'}), 202
    except Exception as e:
        logger.error(f"API snapshot grafico: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@wms_bp.route('/api/estoque/snapshot/grafico/status', methods=['GET'])
@module_required('wms')
@screen_required('wms', 'avarias_dashboard')
def api_snap_grafico_status():
    """Retorna o status atual do snapshot em background (polling pelo frontend)."""
    try:
        svc = getattr(current_app, 'historico_estoque_service', None)
        if not svc:
            return jsonify({'success': False, 'error': 'HistoricoEstoqueService indisponível'}), 503
        empresa = session.get('user_empresa', '')
        status = svc.get_snap_status(empresa)
        return jsonify({'success': True, **status})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@wms_bp.route('/api/dashboard/operacional')
@module_required('wms')
@screen_required('wms', 'avarias_dashboard')
def api_dashboard_operacional():
    """API: dados do painel operacional."""
    try:
        filtros = _parse_filtros_request()
        avarias = _get_avarias_filtradas(filtros)
        empresa = session.get('user_empresa')
        cfg     = _get_status_configs(empresa)

        # Buscar itens para calcular localização corretamente
        avaria_ids = {str(a.id).strip() for a in avarias}
        todos_itens = wms_service._get_all_item_avarias()
        itens = [i for i in todos_itens if str(i.avaria_id).strip() in avaria_ids and i.status_etiqueta != 'baixada']

        # Buscar ASNs agendados para incluir na previsão de entrega
        asns_agendados = []
        try:
            from modules.wms.routes.asn_routes import asn_service as _asn_svc
            if _asn_svc:
                asns_agendados = _asn_svc.get_agendados(empresa=empresa)
        except Exception as e_asn:
            logger.warning(f"Erro ao buscar ASNs para dashboard: {e_asn}")

        dados = calcular_operacional(
            avarias,
            itens             = itens if itens else None,
            status_pendente   = _parse_status_list(cfg, 'dash_op_status_pendente',   []) or None,
            status_nao_chegou = _parse_status_list(cfg, 'dash_op_status_nao_chegou', []) or None,
            status_triagem    = _parse_status_list(cfg, 'dash_op_status_triagem',    []) or None,
            status_etiquetado = _parse_status_list(cfg, 'dash_op_status_etiquetado', []) or None,
            status_finalizado = _parse_status_list(cfg, 'dash_op_status_finalizado', []) or None,
            asns_agendados    = asns_agendados,
        )
        return jsonify({'success': True, 'data': dados})
    except Exception as e:
        logger.error(f"API dashboard operacional: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@wms_bp.route('/api/dashboard/qualidade')
@module_required('wms')
@screen_required('wms', 'avarias_dashboard')
def api_dashboard_qualidade():
    """API: dados do painel de qualidade."""
    try:
        filtros = _parse_filtros_request()
        avarias = _get_avarias_filtradas(filtros)
        avaria_ids = {a.id for a in avarias}
        todos_itens = wms_service._get_all_item_avarias()
        itens = [i for i in todos_itens if i.avaria_id in avaria_ids and i.status_etiqueta != 'baixada']
        dados = calcular_qualidade(avarias, itens=itens if itens else None)
        return jsonify({'success': True, 'data': dados})
    except Exception as e:
        logger.error(f"API dashboard qualidade: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@wms_bp.route('/api/dashboard/cobranca')
@module_required('wms')
@screen_required('wms', 'avarias_dashboard')
def api_dashboard_cobranca():
    """API: dados do painel de cobrança."""
    try:
        filtros = _parse_filtros_request()
        logger.info(f"DEBUG FILTROS COBRANCA RECEBIDOS: {filtros}")
        avarias = _get_avarias_filtradas(filtros)
        logger.info(f"DEBUG AVARIAS APOS FILTRO: {len(avarias)}")
        empresa = session.get('user_empresa')
        cfg     = _get_status_configs(empresa)

        status_indenizaveis = _parse_status_list(cfg, 'dash_status_indenizaveis', [])
        status_pago         = _parse_status_list(cfg, 'dash_status_pago', [])
        status_pago_parcial = _parse_status_list(cfg, 'dash_status_pago_parcial', [])
        status_pendente     = _parse_status_list(cfg, 'dash_status_pendente', [])
        status_em_atraso    = _parse_status_list(cfg, 'dash_status_em_atraso', [])
        funil_etapas        = _build_funil_etapas(cfg)

        dados = calcular_cobranca(
            avarias,
            status_indenizaveis = status_indenizaveis or None,
            status_pago         = status_pago or None,
            status_pago_parcial = status_pago_parcial or None,
            status_pendente     = status_pendente or None,
            status_em_atraso    = status_em_atraso or None,
            funil_etapas        = funil_etapas,
        )
        return jsonify({'success': True, 'data': dados})
    except Exception as e:
        logger.error(f"API dashboard cobrança: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500
        
@wms_bp.route('/api/avaria/<avaria_id>/cobranças', methods=['GET'])
@module_required('wms')
@screen_required('wms', 'avarias_dashboard')
def api_get_cobranças(avaria_id):
    empresa = session.get('user_empresa', '')
    try:
        registros = wms_service.get_cobranças_avaria(avaria_id, empresa)
        return jsonify({'success': True, 'data': registros})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@wms_bp.route('/api/avaria/<avaria_id>/cobranças', methods=['POST'])
@module_required('wms')
@screen_required('wms', 'avarias_dashboard', nivel='editar')
def api_registrar_cobrança(avaria_id):
    empresa  = session.get('user_empresa', '')
    usuario  = session.get('user_name') or session.get('user_email', 'operador')
    body     = request.get_json(silent=True) or {}
    observacao     = (body.get('observacao') or '').strip()
    tipo_cobranca  = (body.get('tipo_cobranca') or 'Indenização').strip()
    try:
        ok, msg, record = wms_service.registrar_cobrança(avaria_id, observacao, usuario, empresa, tipo_cobranca)
        if ok:
            return jsonify({'success': True, 'message': msg, 'data': record})
        return jsonify({'success': False, 'error': msg}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@wms_bp.route('/api/avaria/<avaria_id>/marcar-pago', methods=['POST'])
@module_required('wms')
@screen_required('wms', 'avarias_dashboard', nivel='editar')
def api_marcar_pago(avaria_id):
    """
    Marca avaria como paga com a data informada pelo usuário.
    - indenizacao == 'total' ou reembolso_pct >= 100 → dash_status_pago
    - indenizacao == 'parcial'                        → dash_status_pago_parcial
    Recebe JSON: { data_pagamento: 'YYYY-MM-DD', observacao: '...' }
    """
    empresa = session.get('user_empresa', '')
    usuario = session.get('user_name') or session.get('user_email', 'operador')
    try:
        body        = request.get_json(silent=True) or {}
        data_raw    = (body.get('data_pagamento') or '').strip()   # ISO: YYYY-MM-DD
        observacao  = (body.get('observacao') or '').strip()

        if not data_raw:
            return jsonify({'success': False, 'error': 'Data de pagamento obrigatória'}), 400

        # Converte de YYYY-MM-DD para dd/mm/YYYY (formato do sistema)
        from datetime import datetime as _dt
        try:
            data_pgto = _dt.strptime(data_raw, '%Y-%m-%d').strftime('%d/%m/%Y')
        except ValueError:
            data_pgto = data_raw  # aceita se já vier em outro formato

        cfg            = _get_status_configs(empresa)
        status_pago    = _parse_status_list(cfg, 'dash_status_pago',         [])
        status_parcial = _parse_status_list(cfg, 'dash_status_pago_parcial', [])
        logger.info(f"marcar-pago | empresa={empresa} | dash_status_pago={status_pago} | dash_status_pago_parcial={status_parcial}")

        # Busca a avaria para decidir total vs parcial
        avaria = wms_service.get_avaria_by_id(avaria_id)
        if not avaria:
            return jsonify({'success': False, 'error': 'Avaria não encontrada'}), 404

        indenizacao_tipo = (getattr(avaria, 'indenizacao', '') or '').strip().lower()
        try:
            reembolso = float(getattr(avaria, 'reembolso_pct', None) or 0)
        except (ValueError, TypeError):
            reembolso = 0.0

        eh_parcial = (indenizacao_tipo == 'parcial') or (0 < reembolso < 99.9)

        if eh_parcial:
            novo_status = status_parcial[0] if status_parcial else 'Pagto Ok - Parcial'
        else:
            novo_status = status_pago[0]    if status_pago    else 'Pagto Ok - Total'

        logger.info(f"marcar-pago | avaria={avaria_id} | indenizacao={indenizacao_tipo} | reembolso={reembolso} | novo_status={novo_status} | data={data_pgto}")

        updates = dict(status_indenizacao=novo_status, data_pagamento=data_pgto)
        if observacao:
            # Acrescenta ao campo observacao sem sobrescrever
            obs_atual = (getattr(avaria, 'observacao', '') or '').strip()
            from datetime import datetime as _dt2
            ts = _dt2.now().strftime('%d/%m/%Y %H:%M')
            updates['observacao'] = f"{obs_atual}\n[{ts}] {usuario}: {observacao}".strip()

        logger.info(f"marcar-pago | chamando update_avaria com campos: {list(updates.keys())}")
        ok, msg = wms_service.update_avaria(avaria_id, alterado_por=usuario, **updates)
        logger.info(f"marcar-pago | update_avaria retornou: ok={ok}, msg={msg}")
        if ok:
            return jsonify({'success': True, 'message': f'Marcado como "{novo_status}" em {data_pgto}'})
        return jsonify({'success': False, 'error': msg}), 400
    except Exception as e:
        logger.error(f"marcar-pago: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@wms_bp.route('/api/dashboard/produtividade')
@module_required('wms')
@screen_required('wms', 'avarias_dashboard')
def api_dashboard_produtividade():
    """API: dados do painel de produtividade."""
    try:
        filtros = _parse_filtros_request()
        avarias = _get_avarias_filtradas(filtros)
        dados = calcular_produtividade(avarias)
        return jsonify({'success': True, 'data': dados})
    except Exception as e:
        logger.error(f"API dashboard produtividade: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

NOVAS_SCREENS_DASHBOARD = [
    {'tela': 'dash_financeiro',    'descricao': 'Dashboard — Painel Financeiro / Indenizações'},
    {'tela': 'dash_operacional',   'descricao': 'Dashboard — Painel Operacional'},
    {'tela': 'dash_qualidade',     'descricao': 'Dashboard — Painel de Qualidade / Motivos'},
    {'tela': 'dash_cobranca',      'descricao': 'Dashboard — Painel de Cobrança / Funil'},
    {'tela': 'dash_produtividade', 'descricao': 'Dashboard — Painel de Produtividade'},
]


@wms_bp.route('/admin/migrar-screens-dashboard')
@module_required('wms')
def migrar_screens_dashboard():
    """
    Migração one-shot: insere screens do dashboard analítico na aba TelasModulos.
    Só pode ser executada por admins.
    """
    user_level = session.get('user_level', '')
    if user_level not in ('admin_global', 'admin_empresa', 'admin'):
        return jsonify({'success': False, 'error': 'Apenas admins podem executar migrações'}), 403

    user_empresa = session.get('user_empresa')

    try:
        # Precisa do sheets_service — acessado via wms_service
        sheets = wms_service.sheets
        if not sheets or not sheets.connected:
            return jsonify({'success': False, 'error': 'Sheets não conectado'}), 500

        # Determina empresas alvo
        # admin_global migra todas; admin_empresa migra só a própria
        if user_level == 'admin_global':
            try:
                ws_empresas = sheets.sheet.worksheet('Empresas')
                rows = ws_empresas.get_all_records()
                empresas_alvo = []
                for r in rows:
                    mods = str(r.get('modulos_permitidos', '')).split(',')
                    if 'wms' in [m.strip() for m in mods]:
                        empresas_alvo.append(str(r.get('empresa', '')).strip())
                empresas_alvo = [e for e in empresas_alvo if e]
            except Exception:
                empresas_alvo = [user_empresa]
        else:
            empresas_alvo = [user_empresa]

        if not empresas_alvo:
            return jsonify({'success': False, 'error': 'Nenhuma empresa com WMS encontrada'}), 400

        # Garante que a aba TelasModulos existe
        try:
            ws = sheets.sheet.worksheet('TelasModulos')
        except Exception:
            ws = sheets.sheet.add_worksheet(title='TelasModulos', rows=500, cols=5)
            ws.append_row(['empresa', 'modulo', 'tela', 'descricao', 'ativo'])
            logger.info("Aba TelasModulos criada")

        # Lê registros existentes para evitar duplicatas
        existing = ws.get_all_records()
        existing_set = {
            (str(r.get('empresa', '')).strip(), str(r.get('modulo', '')).strip(), str(r.get('tela', '')).strip())
            for r in existing
        }

        inseridos = []
        ignorados = []

        for empresa in empresas_alvo:
            for screen in NOVAS_SCREENS_DASHBOARD:
                chave = (empresa, 'wms', screen['tela'])
                if chave in existing_set:
                    ignorados.append(f"{empresa}/{screen['tela']} (já existe)")
                    continue
                ws.append_row([empresa, 'wms', screen['tela'], screen['descricao'], 'sim'])
                existing_set.add(chave)
                inseridos.append(f"{empresa}/{screen['tela']}")
                logger.info(f"Screen inserida: {chave}")

        # Invalida cache de telas
        if hasattr(sheets, '_telas_cache'):
            sheets._telas_cache.clear()
        if hasattr(sheets, 'invalidate_permissions_cache'):
            sheets.invalidate_permissions_cache()

        return jsonify({
            'success':   True,
            'inseridos':  inseridos,
            'ignorados':  ignorados,
            'empresas':   empresas_alvo,
            'mensagem':   f'{len(inseridos)} screen(s) inserida(s), {len(ignorados)} já existiam',
        })

    except Exception as e:
        import traceback
        logger.error(f"Erro na migração de screens: {e}\n{traceback.format_exc()}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ADICIONAR após a função _get_status_indenizaveis existente:

def _get_status_configs(empresa: str) -> dict:
    """Lê todas as configs de status do dashboard da parametrização."""
    try:
        import modules.wms.routes.parametrizacao_routes as _param_rt
        _cfg_svc = getattr(_param_rt, 'config_service', None)
        if not _cfg_svc:
            return {}
        all_cfg = _cfg_svc.get_all_configs(empresa)
        return {k: v for k, v in all_cfg.items() if k.startswith('dash_')}
    except Exception as e:
        logger.warning(f"Erro ao ler status_configs: {e}")
        return {}


def _parse_status_list(cfg: dict, key: str, default: list) -> list:
    """Converte string separada por vírgula em lista normalizada."""
    val = cfg.get(key, '')
    if not val or not val.strip():
        return default
    return [s.strip() for s in val.split(',') if s.strip()]


def _build_funil_etapas(cfg: dict) -> list:
    """Constrói lista de etapas do funil a partir das configs."""
    etapas = []
    for i in range(1, 16):
        label  = cfg.get(f'dash_funil_etapa{i}_label', '').strip()
        status = cfg.get(f'dash_funil_etapa{i}_status', '').strip()
        if label:
            etapas.append({
                'label': label,
                'status': [s.strip() for s in status.split(',') if s.strip()] if status else [],
            })
    return etapas or None
