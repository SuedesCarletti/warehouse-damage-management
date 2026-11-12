"""
modules/wms/routes/validacao_estoque_routes.py
Blueprint : wms_validacao_estoque
Prefix    : /wms/validacao-estoque

Rotas UI:
  GET  /                         → tela principal (lista de pendências)

APIs (AJAX):
  GET  /api/pendencias           → lista com filtros (status, codemp, dias)
  GET  /api/estatisticas         → contadores para o dashboard
  GET  /api/sugestoes-nf         → busca NF de transferência no Sankhya
  POST /api/validar              → valida e salva resultado
  POST /api/ignorar              → marca como Ignorado sem análise
"""

import logging
from flask import Blueprint, render_template, request, jsonify, session
from utils.decorators import module_required, set_theme
from ..services.validacao_estoque_service import ValidacaoEstoqueService

logger = logging.getLogger(__name__)

validacao_estoque_bp = Blueprint('wms_validacao_estoque', __name__)
_service: ValidacaoEstoqueService = None
_automacao_service = None
_wms_service = None


def init_validacao_estoque_routes(
    sankhya_service=None,
    cache_sankhya_service=None,
    sheets_service=None,
    automacao_service=None,
    wms_service=None,
):
    global _service, _automacao_service, _wms_service
    _service = ValidacaoEstoqueService(
        sankhya_service=sankhya_service,
        cache_sankhya_service=cache_sankhya_service,
        sheets_service=sheets_service,
    )
    _automacao_service = automacao_service
    _wms_service = wms_service
    logger.info('Rotas Validação de Estoque inicializadas')


def _disparar_automacao_validacao(trigger: str, avaria_id: str, contexto: dict,
                                   operador: str = 'sistema') -> list:
    """Dispara trigger de automação para avaria associada à validação de estoque."""
    if not _automacao_service or not _wms_service:
        logger.warning(f"automacao/wms_service indisponível — trigger '{trigger}' ignorado")
        return []
    if not avaria_id:
        logger.warning(f"avaria_id ausente — trigger '{trigger}' ignorado")
        return []
    try:
        avaria_obj = _wms_service.get_avaria_by_id(avaria_id)
        empresa    = getattr(avaria_obj, 'empresa', '') or ''
        resultados = _automacao_service.aplicar(
            trigger=trigger,
            avaria_id=avaria_id,
            empresa=empresa,
            contexto=contexto,
            operador_email=operador,
        )
        aplicados  = [r for r in resultados if r.sucesso]
        bloqueados = [r for r in resultados if r.bloqueado]
        if aplicados:
            logger.info(f"Automação '{trigger}' aplicada em {avaria_id}: {[r.novo_status for r in aplicados]}")
        if bloqueados:
            logger.warning(f"Automação '{trigger}' bloqueada em {avaria_id}: {[r.msg for r in bloqueados]}")
    except Exception as e:
        logger.error(f"Erro ao disparar automação '{trigger}' para {avaria_id}: {e}")
    return []


def _ctx_validacao(registro: dict) -> dict:
    """Monta contexto rico para trigger de validação de estoque."""
    codlocal = str(registro.get('codlocal_destino', '')).strip()
    descr    = str(registro.get('descr_local_destino', '')).strip()
    local_completo = f"{codlocal} - {descr}" if descr else codlocal
    return {
        'codlocal': codlocal,
        'local':    codlocal,
        'local_completo': local_completo,
        'descr_local_destino': descr,
    }


def _get_service() -> ValidacaoEstoqueService:
    if _service is None:
        raise RuntimeError('ValidacaoEstoqueService não inicializado')
    return _service


# ── UI ───────────────────────────────────────────────────────────────────────

@validacao_estoque_bp.route('/')
@module_required('wms')
@set_theme('wms')
def index():
    logger.info(f'Validação Estoque acessada por {session.get("user_email")}')
    svc     = _get_service()
    empresa = session.get('user_empresa', '')   # isolamento multi-tenant
    stats   = svc.estatisticas(empresa=empresa)
    return render_template('wms/validacao_estoque.html', stats=stats)


# ── APIs ─────────────────────────────────────────────────────────────────────

@validacao_estoque_bp.route('/api/pendencias')
@module_required('wms')
def api_pendencias():
    """GET /api/pendencias?status=Pendente&codemp=1&dias=30"""
    svc     = _get_service()
    status  = request.args.get('status', 'Pendente')
    codemp  = request.args.get('codemp', '')
    empresa = session.get('user_empresa', '')   # isolamento multi-tenant
    try:
        dias = int(request.args.get('dias', 30))
    except ValueError:
        dias = 30

    registros = svc.listar_pendencias(status=status, codemp=codemp, dias=dias, empresa=empresa)
    return jsonify({'success': True, 'total': len(registros), 'registros': registros})


@validacao_estoque_bp.route('/api/estatisticas')
@module_required('wms')
def api_estatisticas():
    svc     = _get_service()
    codemp  = request.args.get('codemp', '')
    empresa = session.get('user_empresa', '')   # isolamento multi-tenant
    try:
        dias = int(request.args.get('dias', 30))
    except ValueError:
        dias = 30
    return jsonify({'success': True, **svc.estatisticas(codemp=codemp, dias=dias, empresa=empresa)})


@validacao_estoque_bp.route('/api/sugestoes-nf')
@module_required('wms')
def api_sugestoes_nf():
    """GET /api/sugestoes-nf?codprod=X&data_movimento=DD/MM/YYYY&codemp=Y&codlocal_destino=Z"""
    svc              = _get_service()
    codprod          = request.args.get('codprod', '')
    codemp           = request.args.get('codemp', '')
    data_mov         = request.args.get('data_movimento', '')
    codlocal_destino = request.args.get('codlocal_destino', '')

    if not codprod:
        return jsonify({'success': False, 'error': 'codprod é obrigatório'}), 400

    sugestoes = svc.buscar_sugestoes_nf(
        codprod, codemp, data_mov,
        codlocal_destino=codlocal_destino,
    )
    return jsonify({'success': True, 'sugestoes': sugestoes})

@validacao_estoque_bp.route('/api/validar', methods=['POST'])
@module_required('wms')
def api_validar():
    """
    POST body:
    {
        id_validacao, numnota_erp, nunota_erp, codemp,
        observacao (opcional)
    }
    """
    svc  = _get_service()
    data = request.get_json(force=True) or {}

    id_val      = data.get('id_validacao', '')
    numnota_erp = data.get('numnota_erp', '')
    nunota_erp  = data.get('nunota_erp', '')
    codemp      = data.get('codemp', '')
    observacao  = data.get('observacao', '')
    validado_por = session.get('user_email', 'sistema')

    if not id_val:
        return jsonify({'success': False, 'error': 'id_validacao é obrigatório'}), 400

    logger.info(
        f'🔍 Validar {id_val} — NF {numnota_erp} / EMP {codemp} '
        f'por {validado_por}'
    )
    resultado = svc.validar_e_salvar(
        id_validacao=id_val,
        numnota_erp=numnota_erp,
        nunota_erp=nunota_erp,
        codemp=codemp,
        validado_por=validado_por,
        observacao=observacao,
    )

    if resultado.get('success'):
        registro  = resultado.get('registro', {})
        avaria_id = registro.get('avaria_id', '')
        _disparar_automacao_validacao(
            trigger='validacao_estoque_aprovada',
            avaria_id=avaria_id,
            contexto=_ctx_validacao(registro),
            operador=validado_por,
        )

    return jsonify(resultado), (200 if resultado.get('success') else 422)


@validacao_estoque_bp.route('/api/ignorar', methods=['POST'])
@module_required('wms')
def api_ignorar():
    svc  = _get_service()
    data = request.get_json(force=True) or {}
    id_val      = data.get('id_validacao', '')
    observacao  = data.get('observacao', '')
    validado_por = session.get('user_email', 'sistema')

    if not id_val:
        return jsonify({'success': False, 'error': 'id_validacao é obrigatório'}), 400

    resultado = svc.validar_e_salvar(
        id_validacao=id_val,
        numnota_erp='', nunota_erp='', codemp='',
        validado_por=validado_por,
        observacao=observacao,
        forcar_status='Ignorado',
    )
    return jsonify(resultado)

@validacao_estoque_bp.route('/api/lotes')
@module_required('wms')
def api_lotes():
    """GET /api/lotes?status=Pendente&codemp=1&dias=90"""
    svc     = _get_service()
    status  = request.args.get('status', 'Pendente')
    codemp  = request.args.get('codemp', '')
    empresa = session.get('user_empresa', '')   # isolamento multi-tenant
    try:
        dias = int(request.args.get('dias', 90))
    except ValueError:
        dias = 90
    lotes = svc.listar_lotes(status=status, codemp=codemp, dias=dias, empresa=empresa)
    return jsonify({'success': True, 'total': len(lotes), 'lotes': lotes})

@validacao_estoque_bp.route('/api/lote/<id_lote>')
@module_required('wms')
def api_lote_detalhe(id_lote):
    """GET /api/lote/LOTE-XXXX — detalha lote + registros + snapshot atual"""
    svc       = _get_service()
    empresa   = session.get('user_empresa', '')
    resultado = svc.detalhar_lote(id_lote, empresa=empresa)
    return jsonify(resultado)

@validacao_estoque_bp.route('/api/gerar-lotes', methods=['POST'])
@module_required('wms')
def api_gerar_lotes():
    """
    POST /api/gerar-lotes
    Body: { codemp?, data_inicio, titulo? }
    Agrupa pendências por codlocal_destino e cria lotes em WMS_LotesValidacao.
    """
    svc         = _get_service()
    data        = request.get_json() or {}
 
    # empresa vem SEMPRE da sessão — nunca do body (isolamento multi-tenant).
    # codemp é filtro ERP opcional, completamente independente do tenant.
    empresa     = session.get('user_empresa', '')
    codemp      = (data.get('codemp') or '').strip()
    criado_por  = session.get('user_email', 'sistema')
    data_inicio = (data.get('data_inicio') or '').strip()
    data_fim    = (data.get('data_fim') or '').strip()
    titulo      = (data.get('titulo') or '').strip()
    codlocal    = (data.get('codlocal') or '').strip()

    logger.info(
        f'📦 Gerar lotes solicitado por {criado_por} '
        f'— empresa={empresa!r} codemp_filtro={codemp!r} (vazio=todas) '
        f'— período: "{data_inicio or "sem início"}" → "{data_fim or "hoje"}" '
        f'titulo: "{titulo or "(sem)"}"'
        + (f' codlocal={codlocal!r}' if codlocal else '')
    )
    resultado = svc.gerar_lotes_por_local(
        codemp=str(codemp),
        criado_por=criado_por,
        data_inicio=data_inicio,
        data_fim=data_fim,
        titulo=titulo,
        empresa=empresa,
        codlocal=codlocal,
    )
    return jsonify(resultado)

@validacao_estoque_bp.route('/api/gerar-lote-por-etiquetas', methods=['POST'])
@module_required('wms')
def api_gerar_lote_por_etiquetas():
    """
    POST /api/gerar-lote-por-etiquetas
    Body: { ids_etiquetas: [...], titulo?, codemp? }
    Cria um lote a partir de etiquetas escaneadas.
    """
    svc        = _get_service()
    data       = request.get_json() or {}
    empresa    = session.get('user_empresa', '')
    criado_por = session.get('user_email', 'sistema')
    codemp     = (data.get('codemp') or '').strip()
    titulo     = (data.get('titulo') or '').strip()
    ids_etiquetas = data.get('ids_etiquetas') or []

    if not ids_etiquetas:
        return jsonify({'success': False, 'error': 'ids_etiquetas é obrigatório'}), 400

    logger.info(
        f'📦 Gerar lote por etiquetas solicitado por {criado_por} '
        f'— empresa={empresa!r} qtd_etiquetas={len(ids_etiquetas)}'
    )
    resultado = svc.gerar_lote_por_etiquetas(
        ids_etiquetas=ids_etiquetas,
        titulo=titulo,
        codemp=codemp,
        criado_por=criado_por,
        empresa=empresa,
    )
    return jsonify(resultado)


@validacao_estoque_bp.route('/api/validar-lote', methods=['POST'])
@module_required('wms')
def api_validar_lote():
    """POST /api/validar-lote — valida todos os itens de um lote"""
    svc     = _get_service()
    data    = request.get_json() or {}
    id_lote = data.get('id_lote', '').strip()
    empresa = session.get('user_empresa', '')

    if not id_lote:
        return jsonify({'success': False, 'error': 'id_lote é obrigatório'}), 400

    # Isolamento: confirmar que o lote pertence à empresa da sessão
    lote = svc.buscar_lote_por_id(id_lote)
    if not lote:
        return jsonify({'success': False, 'error': 'Lote não encontrado'}), 404
    if empresa:
        lote_empresa = lote.get('empresa', '').strip()
        if lote_empresa != empresa:
            return jsonify({'success': False, 'error': 'Lote não encontrado'}), 404

    validado_por = session.get('user_email', 'sistema')
    resultado = svc.validar_lote(
        id_lote=id_lote,
        numnota_erp=data.get('numnota_erp', ''),
        nunota_erp=data.get('nunota_erp', ''),
        codemp=data.get('codemp', ''),
        validado_por=validado_por,
        observacao=data.get('observacao', ''),
        numnotas=data.get('numnotas', []),
    )

    if resultado.get('success'):
        for reg in resultado.get('registros', []):
            avaria_id = reg.get('avaria_id', '')
            _disparar_automacao_validacao(
                trigger='validacao_estoque_aprovada',
                avaria_id=avaria_id,
                contexto=_ctx_validacao(reg),
                operador=validado_por,
            )

    return jsonify(resultado)


@validacao_estoque_bp.route('/api/vincular-nf-lote', methods=['POST'])
@module_required('wms')
def api_vincular_nf_lote():
    """POST /api/vincular-nf-lote — salva a NF vinculada ao lote sem executar a validação técnica"""
    svc     = _get_service()
    data    = request.get_json() or {}
    id_lote = data.get('id_lote', '').strip()
    empresa = session.get('user_empresa', '')

    if not id_lote:
        return jsonify({'success': False, 'error': 'id_lote é obrigatório'}), 400

    lote = svc.buscar_lote_por_id(id_lote)
    if not lote:
        return jsonify({'success': False, 'error': 'Lote não encontrado'}), 404
    if empresa:
        lote_empresa = lote.get('empresa', '').strip()
        if lote_empresa != empresa:
            return jsonify({'success': False, 'error': 'Lote não encontrado'}), 404

    resultado = svc.vincular_nf_lote(
        id_lote=id_lote,
        numnota_erp=data.get('numnota_erp', ''),
        nunota_erp=data.get('nunota_erp', ''),
        codemp=data.get('codemp', ''),
        vinculado_por=session.get('user_email', 'sistema'),
        observacao=data.get('observacao', ''),
        numnotas=data.get('numnotas', []),
    )
    return jsonify(resultado), (200 if resultado.get('success') else 422)


@validacao_estoque_bp.route('/api/sync-localizacao-lote', methods=['POST'])
@module_required('wms')
def api_sync_localizacao_lote():
    """POST /api/sync-localizacao-lote — re-aplica localizacao em Itens_Avarias para lote já aprovado"""
    svc     = _get_service()
    data    = request.get_json() or {}
    id_lote = data.get('id_lote', '').strip()
    empresa = session.get('user_empresa', '')

    if not id_lote:
        return jsonify({'success': False, 'error': 'id_lote é obrigatório'}), 400

    lote = svc.buscar_lote_por_id(id_lote)
    if not lote:
        return jsonify({'success': False, 'error': 'Lote não encontrado'}), 404
    if empresa:
        lote_empresa = lote.get('empresa', '').strip()
        if lote_empresa != empresa:
            return jsonify({'success': False, 'error': 'Lote não encontrado'}), 404

    if lote.get('status') != 'Aprovado':
        return jsonify({'success': False, 'error': 'Lote não está aprovado'}), 400

    codlocal   = lote.get('codlocal', '')
    descrlocal = lote.get('descrlocal', '')
    if not codlocal:
        return jsonify({'success': False, 'error': 'Lote sem codlocal definido'}), 400

    localizacao_fmt = f"{codlocal} - {descrlocal}" if descrlocal else str(codlocal)

    try:
        import json as _json
        ids_val = _json.loads(lote.get('ids_validacoes', '[]') or '[]')
        registros = svc.listar_pendencias(status='Aprovado', codemp='', dias=365)
        ids_set   = set(ids_val)
        resultados_lote = [
            {'resultado': 'Aprovado', 'registro': r}
            for r in registros
            if r.get('id_validacao') in ids_set
        ]
        svc._atualizar_localizacao_itens_aprovados(localizacao_fmt, resultados_lote)
        return jsonify({'success': True, 'itens_processados': len(resultados_lote)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@validacao_estoque_bp.route('/api/fechar-lote', methods=['POST'])
@module_required('wms')
def api_fechar_lote():
    """POST /api/fechar-lote — fecha um lote fisicamente sem validação técnica"""
    svc     = _get_service()
    data    = request.get_json() or {}
    id_lote = data.get('id_lote', '').strip()
    empresa = session.get('user_empresa', '')

    if not id_lote:
        return jsonify({'success': False, 'error': 'id_lote é obrigatório'}), 400

    lote = svc.buscar_lote_por_id(id_lote)
    if not lote:
        return jsonify({'success': False, 'error': 'Lote não encontrado'}), 404
    if empresa:
        lote_empresa = lote.get('empresa', '').strip()
        if lote_empresa != empresa:
            return jsonify({'success': False, 'error': 'Lote não encontrado'}), 404

    resultado = svc.fechar_lote(
        id_lote=id_lote,
        fechado_por=session.get('user_email', 'sistema'),
        observacao=data.get('observacao', ''),
    )
    return jsonify(resultado)


@validacao_estoque_bp.route('/api/ignorar-lote', methods=['POST'])
@module_required('wms')
def api_ignorar_lote():
    """POST /api/ignorar-lote"""
    svc     = _get_service()
    data    = request.get_json() or {}
    id_lote = data.get('id_lote', '').strip()
    empresa = session.get('user_empresa', '')

    if not id_lote:
        return jsonify({'success': False, 'error': 'id_lote é obrigatório'}), 400

    lote = svc.buscar_lote_por_id(id_lote)
    if not lote:
        return jsonify({'success': False, 'error': 'Lote não encontrado'}), 404
    if empresa:
        lote_empresa = lote.get('empresa', '').strip()
        if lote_empresa and lote_empresa != empresa:
            return jsonify({'success': False, 'error': 'Lote não encontrado'}), 404

    resultado = svc.ignorar_lote(
        id_lote=id_lote,
        validado_por=session.get('user_email', 'sistema'),
        observacao=data.get('observacao', ''),
    )
    return jsonify(resultado)


@validacao_estoque_bp.route('/api/estatisticas-lotes')
@module_required('wms')
def api_estatisticas_lotes():
    svc     = _get_service()
    codemp  = request.args.get('codemp', '')
    empresa = session.get('user_empresa', '')   # isolamento multi-tenant
    try:
        dias = int(request.args.get('dias', 90))
    except ValueError:
        dias = 90
    return jsonify({'success': True, **svc.estatisticas_lotes(codemp=codemp, dias=dias, empresa=empresa)})
