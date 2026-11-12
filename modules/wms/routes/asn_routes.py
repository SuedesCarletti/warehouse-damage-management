"""
modules/wms/routes/asn_routes.py
Rotas do Portal ASN — Módulo 3 do Roadmap.

Blueprint : wms_asn
Prefix    : /wms/asn  (registrado em app.py)

Rotas UI:
  GET  /wms/asn/portal          → Portal da transportadora (dashboard + formulário)
  GET  /wms/asn/portal/sucesso  → Página de confirmação após criar ASN
  GET  /wms/asn/fila            → Fila de ASNs para o operador WMS

APIs (AJAX):
  POST /wms/asn/api/criar       → Cria novo ASN (transportadora)
  POST /wms/asn/api/receber     → Marca ASN como recebido + cria avaria (operador)
  POST /wms/asn/api/cancelar    → Cancela um ASN
  GET  /wms/asn/api/lista       → Lista ASNs com filtros
  GET  /wms/asn/api/stats       → Contadores para dashboard

Permissões:
  - Transportadoras: screen 'portal_asn' (só vê o portal)
  - Operadores WMS:  screen 'listar_avarias' ou 'avarias_registrar' (vê a fila)
  - Admin:           vê tudo
"""

import logging
import os
import tempfile
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, flash, current_app
from utils.decorators import module_required, set_theme
from ..services.asn_service import AsnService
from core.services.tenant_scope import get_current_empresa, require_empresa_match_or_404

logger = logging.getLogger(__name__)
asn_bp = Blueprint('wms_asn', __name__)
asn_service: AsnService = None

def init_asn_routes(sheets_service, email_service=None, wms_service=None, base_url: str = '',
                    automacao_service=None, sankhya_avaria_service=None, etiqueta_service=None):
    global asn_service
    asn_service = AsnService(
        sheets_service=sheets_service,
        email_service=email_service,
        wms_service=wms_service,
        base_url=base_url,
        automacao_service=automacao_service,
        sankhya_avaria_service=sankhya_avaria_service,
        etiqueta_service=etiqueta_service,
    )
    logger.info("Rotas ASN inicializadas")

def _is_transportadora() -> bool:
    nivel = session.get('user_level', '')
    return nivel == 'transportadora'

def _is_operador() -> bool:
    """Verifica se o usuário pode ver a fila de ASNs."""
    nivel = session.get('user_level', '')
    return nivel not in ('transportadora',)

# PORTAL DA TRANSPORTADORA

@asn_bp.route('/portal')
@module_required('wms')
@set_theme('wms')
def portal_transportadora():
    """
    Dashboard da transportadora:
      - Lista de seus ASNs (histórico)
      - Botão para criar novo ASN
      - Cards de status (agendados, recebidos)
    """
    email = session.get('user_email', '')
    nome = session.get('user_name', email)
    empresa_dest = session.get('user_empresa', '')

    logger.info(f"Portal ASN acessado por {email}")

    meus_asns = asn_service.get_by_transportadora(email) if asn_service else []

    # Stats para os cards
    agendados  = [a for a in meus_asns if a.status == 'agendado']
    recebidos  = [a for a in meus_asns if a.status == 'recebido']
    atrasados  = [a for a in agendados if a.atrasado]

    return render_template(
        'wms/asn/portal_transportadora.html',
        meus_asns=meus_asns,
        stats={
            'agendados': len(agendados),
            'recebidos': len(recebidos),
            'atrasados': len(atrasados),
            'total':     len(meus_asns),
        },
        nome_transportadora=nome,
        empresa_dest=empresa_dest,
    )

@asn_bp.route('/portal/sucesso')
@module_required('wms')
@set_theme('wms')
def portal_sucesso():
    """Página de confirmação após criar um ASN."""
    asn_id   = request.args.get('asn_id', '')
    nota     = request.args.get('nota', '')
    prevista = request.args.get('prevista', '')
    return render_template(
        'wms/asn/portal_sucesso.html',
        asn_id=asn_id,
        nota=nota,
        prevista=prevista,
    )


# FILA DO OPERADOR WMS

@asn_bp.route('/fila')
@module_required('wms')
@set_theme('wms')
def fila_asn():
    """
    Fila de ASNs agendados — visão do operador WMS.
    Mostra ASNs pendentes de recebimento, com destaque para atrasados e
    entregas previstas para hoje.
    """
    if _is_transportadora():
        flash('Acesso restrito aos operadores do WMS.', 'warning')
        return redirect(url_for('wms_asn.portal_transportadora'))

    empresa = session.get('user_empresa', '')
    logger.info(f"Fila ASN acessada por {session.get('user_email')}")

    asns_agendados = asn_service.get_agendados(empresa=empresa) if asn_service else []
    stats = asn_service.get_stats(empresa=empresa) if asn_service else {}

    return render_template(
        'wms/asn/fila_operador.html',
        asns=asns_agendados,
        stats=stats,
        empresa=empresa,
    )


# APIS

@asn_bp.route('/api/criar', methods=['POST'])
@module_required('wms')
def api_criar_asn():
    """
    POST /wms/asn/api/criar
    Body: {nota, tipo_nota, cte, volumes, data_prevista, nome_motorista, placa_veiculo, descricao_avaria}
    Usada pelo formulário do portal da transportadora.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Corpo vazio'}), 400

        user_email = session.get('user_email', '')
        transportadora_nome = session.get('user_name', '') or session.get('user_department', '') or user_email
        empresa_dest = data.get('empresa_dest') or session.get('user_empresa', '')

        success, msg, asn = asn_service.criar_asn(
            data=data,
            user_email=user_email,
            transportadora_nome=transportadora_nome,
            empresa_dest=empresa_dest,
        )

        if success and asn:
            return jsonify({
                'success': True,
                'message': msg,
                'asn_id':  asn.id,
                'redirect': url_for('wms_asn.portal_sucesso',
                                    asn_id=asn.id,
                                    nota=asn.nota,
                                    prevista=asn.data_prevista),
            })
        return jsonify({'success': False, 'error': msg}), 400

    except Exception as e:
        logger.error(f"api_criar_asn: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@asn_bp.route('/api/processar', methods=['POST'])
@module_required('wms')
def api_processar_asn():
    """
    POST /wms/asn/api/processar
    Body: {asn_id, cod_empresa}
    Etapa 2: operador insere empresa → consulta Sankhya → cria avaria(s) → status=processado
    """
    if _is_transportadora():
        return jsonify({'success': False, 'error': 'Acesso negado'}), 403

    try:
        data = request.get_json()
        if not data or not data.get('asn_id'):
            return jsonify({'success': False, 'error': 'asn_id obrigatório'}), 400

        empresa = session.get('user_empresa', '')
        asn_id  = data['asn_id']

        # Isolamento por empresa: confirma que o ASN pertence à empresa do operador
        if empresa:
            asn_check = asn_service.get_by_id(asn_id)
            if not asn_check:
                return jsonify({'success': False, 'error': 'ASN não encontrado'}), 404
            _guard = require_empresa_match_or_404(asn_check.empresa_dest, empresa)
            if _guard:
                return _guard

        notas_empresas = data.get('notas_empresas') or {}
        cod_empresa_padrao = str(data.get('cod_empresa', '')).strip()

        # Aceita ou CODEMP padrão ou pelo menos um CODEMP por nota
        if not cod_empresa_padrao and not notas_empresas:
            return jsonify({'success': False, 'error': 'Informe o código da empresa'}), 400

        success, msg, avaria_ids = asn_service.processar_asn(
            asn_id=asn_id,
            cod_empresa=cod_empresa_padrao,
            operador_email=session.get('user_email', ''),
            notas_empresas=notas_empresas,
        )

        return jsonify({
            'success': success,
            ('message' if success else 'error'): msg,
            'avaria_ids': avaria_ids,
        }), (200 if success else 400)

    except Exception as e:
        logger.error(f"api_processar_asn: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@asn_bp.route('/api/receber', methods=['POST'])
@module_required('wms')
def api_receber_asn():
    """
    POST /wms/asn/api/receber
    Body: {asn_id, observacao, cod_empresa}
    Operador confirma o recebimento — cria avaria automaticamente.
    """
    if _is_transportadora():
        return jsonify({'success': False, 'error': 'Acesso negado'}), 403

    try:
        data = request.get_json()
        if not data or not data.get('asn_id'):
            return jsonify({'success': False, 'error': 'asn_id obrigatório'}), 400

        empresa = session.get('user_empresa', '')
        asn_id  = data['asn_id']

        # Isolamento por empresa: confirma que o ASN pertence à empresa do operador
        if empresa:
            asn_check = asn_service.get_by_id(asn_id)
            if not asn_check:
                return jsonify({'success': False, 'error': 'ASN não encontrado'}), 404
            _guard = require_empresa_match_or_404(asn_check.empresa_dest, empresa)
            if _guard:
                return _guard

        success, msg, avaria_id = asn_service.receber_asn(
            asn_id=asn_id,
            operador_email=session.get('user_email', ''),
            observacao=data.get('observacao', ''),
            cod_empresa=data.get('cod_empresa'),
            produtos_recusados=data.get('produtos_recusados') or [],
            itens_conferencia=data.get('itens_conferencia') or [],
        )

        response = {'success': success, ('message' if success else 'error'): msg}
        if avaria_id:
            response['avaria_id'] = avaria_id
            response['avaria_url'] = url_for('wms.registrar_avarias')

        return jsonify(response), (200 if success else 400)

    except Exception as e:
        logger.error(f"api_receber_asn: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@asn_bp.route('/api/receber-parcial', methods=['POST'])
@module_required('wms')
def api_receber_parcial_asn():
    """
    POST /wms/asn/api/receber-parcial
    Body: {asn_id, notas_recebidas: [], observacao: ''}
    Registra o recebimento parcial de uma ou mais notas inteiras no ASN.
    """
    if _is_transportadora():
        return jsonify({'success': False, 'error': 'Acesso negado'}), 403

    try:
        data = request.get_json()
        if not data or not data.get('asn_id'):
            return jsonify({'success': False, 'error': 'asn_id obrigatório'}), 400

        empresa = session.get('user_empresa', '')
        asn_id = data['asn_id']
        notas_recebidas = data.get('notas_recebidas') or []

        if not notas_recebidas:
            return jsonify({'success': False, 'error': 'Nenhuma nota selecionada para recebimento'}), 400

        if empresa:
            asn_check = asn_service.get_by_id(asn_id)
            if not asn_check:
                return jsonify({'success': False, 'error': 'ASN não encontrado'}), 404
            _guard = require_empresa_match_or_404(asn_check.empresa_dest, empresa)
            if _guard:
                return _guard

        success, msg, avaria_id, novo_status = asn_service.receber_parcial_asn(
            asn_id=asn_id,
            notas_recebidas=notas_recebidas,
            operador_email=session.get('user_email', ''),
            observacao=data.get('observacao', ''),
            cod_empresa=data.get('cod_empresa'),
            produtos_recusados=data.get('produtos_recusados') or [],
            itens_conferencia=data.get('itens_conferencia') or [],
        )

        response = {'success': success, ('message' if success else 'error'): msg}
        if success:
            response['novo_status'] = novo_status
            if avaria_id:
                response['avaria_id'] = avaria_id
                response['avaria_url'] = url_for('wms.registrar_avarias')
        return jsonify(response), (200 if success else 400)

    except Exception as e:
        logger.error(f"api_receber_parcial_asn: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@asn_bp.route('/api/slots-disponiveis', methods=['GET'])
@module_required('wms')
def api_slots_disponiveis():
    """
    GET /wms/asn/api/slots-disponiveis?data=YYYY-MM-DD
    Retorna os slots de horário já ocupados para a data informada.
    Resposta: {success: true, ocupados: ['14:00', '14:40']}
    """
    data = request.args.get('data', '').strip()
    if not data:
        return jsonify({'success': False, 'error': 'data obrigatória'}), 400

    asn_id  = request.args.get('asn_id', '').strip()
    empresa = session.get('user_empresa', '')
    try:
        ocupados = asn_service.get_slots_ocupados(data, empresa, excluir_asn_id=asn_id)
        return jsonify({'success': True, 'ocupados': ocupados})
    except Exception as e:
        logger.error(f"api_slots_disponiveis: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@asn_bp.route('/api/config-atendimento', methods=['GET'])
@module_required('wms')
def api_get_config_atendimento():
    """
    GET /wms/asn/api/config-atendimento
    Retorna a configuração vigente de dias/horários de atendimento para
    agendamento de ASN da empresa do usuário logado (transportadora ou
    operador). Usada para montar os seletores de data/horário no portal e
    nos modais de edição.
    """
    empresa = session.get('user_empresa', '')
    try:
        cfg = asn_service.get_config_atendimento(empresa)
        resp = jsonify({'success': True, **cfg})
        resp.headers['Cache-Control'] = 'no-store'
        return resp
    except Exception as e:
        logger.error(f"api_get_config_atendimento: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@asn_bp.route('/api/config-atendimento', methods=['POST'])
@module_required('wms')
def api_set_config_atendimento():
    """
    POST /wms/asn/api/config-atendimento
    Body: {dias_permitidos: [1,2,3], hora_inicio: 'HH:MM', hora_fim: 'HH:MM', intervalo_min: int}
    Atualiza a janela de dias/horários de atendimento da empresa.
    Restrito a operadores WMS — transportadoras não podem alterar esta configuração.
    """
    if _is_transportadora():
        return jsonify({'success': False, 'error': 'Sem permissão para alterar esta configuração'}), 403

    data = request.get_json() or {}
    empresa    = session.get('user_empresa', '')
    user_email = session.get('user_email', '')

    try:
        success, msg = asn_service.set_config_atendimento(empresa, data, user_email)
        return jsonify({'success': success, ('message' if success else 'error'): msg}), (200 if success else 400)
    except Exception as e:
        logger.error(f"api_set_config_atendimento: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@asn_bp.route('/api/validar-nota', methods=['GET'])
@module_required('wms')
def api_validar_nota():
    """
    GET /wms/asn/api/validar-nota?nota=12345
    Verifica se a NF já está registrada como avaria no Sheets.
    Retorna: {existe: bool, avaria_id: str|null, status: str|null}
    """
    if _is_transportadora():
        return jsonify({'success': False, 'error': 'Acesso negado'}), 403

    nota = request.args.get('nota', '').strip()
    if not nota:
        return jsonify({'success': False, 'error': 'nota obrigatória'}), 400

    empresa = session.get('user_empresa', '')

    try:
        # Verificar na aba de avarias via wms_service
        from modules.wms.services.wms_service import WmsAvariaService
        svc = WmsAvariaService(asn_service.sheets)
        todas = svc.get_all_avarias(empresa=empresa)

        for av in todas:
            nota_av = str(getattr(av, 'nota', '') or '').strip()
            nfd_av  = str(getattr(av, 'nfd', '') or '').strip()
            if nota_av == nota or (nfd_av and nfd_av == nota):
                return jsonify({
                    'success': True,
                    'existe': True,
                    'avaria_id': getattr(av, 'id', ''),
                    'status': getattr(av, 'status_estoque', ''),
                    'transportadora': getattr(av, 'transportadora', ''),
                })

        return jsonify({'success': True, 'existe': False})

    except Exception as e:
        logger.error(f"api_validar_nota: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@asn_bp.route('/api/editar', methods=['POST'])
@module_required('wms')
def api_editar_asn():
    """
    POST /wms/asn/api/editar
    Body: {asn_id, nota, tipo_nota, notas_adicionais, cte, volumes, descricao_avaria, data_prevista}
    Edita campos de NF/NFD (e, opcionalmente, o dia/horário de atendimento) de um
    ASN ainda com status 'agendado'.
    Permitido tanto para transportadoras (próprio ASN) quanto para operadores.
    """
    try:
        data = request.get_json()
        if not data or not data.get('asn_id'):
            return jsonify({'success': False, 'error': 'asn_id obrigatório'}), 400

        asn_id     = data['asn_id']
        user_email = session.get('user_email', '')
        empresa    = session.get('user_empresa', '')

        asn = asn_service.get_by_id(asn_id)
        if not asn:
            return jsonify({'success': False, 'error': 'ASN não encontrado'}), 404

        # Transportadora só edita os próprios ASNs
        if _is_transportadora():
            if asn.email_transportadora.lower() != user_email.lower():
                return jsonify({'success': False, 'error': 'Sem permissão para editar este ASN'}), 403
        else:
            # Operador: isolamento por empresa
            _guard = require_empresa_match_or_404(asn.empresa_dest, empresa)
            if _guard:
                return _guard

        campos = {k: data[k] for k in ('nota', 'tipo_nota', 'notas_adicionais', 'cte', 'volumes', 'descricao_avaria', 'data_prevista') if k in data}
        success, msg = asn_service.editar_asn(asn_id, campos, user_email)
        return jsonify({'success': success, ('message' if success else 'error'): msg}), (200 if success else 400)

    except Exception as e:
        logger.error(f"api_editar_asn: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@asn_bp.route('/api/cancelar', methods=['POST'])
@module_required('wms')
def api_cancelar_asn():
    try:
        data = request.get_json()
        if not data or not data.get('asn_id'):
            return jsonify({'success': False, 'error': 'asn_id obrigatório'}), 400
        asn_id     = data['asn_id']
        motivo     = data.get('motivo', 'Cancelado pelo usuário')
        user_email = session.get('user_email', '')
        empresa    = session.get('user_empresa', '')

        asn = asn_service.get_by_id(asn_id)
        if not asn:
            return jsonify({'success': False, 'error': 'ASN não encontrado'}), 404

        if _is_transportadora():
            if asn.email_transportadora.lower() != user_email.lower():
                return jsonify({'success': False, 'error': 'Sem permissão'}), 403
        else:
            # Isolamento por empresa: operador só cancela ASNs da sua empresa
            _guard = require_empresa_match_or_404(asn.empresa_dest, empresa)
            if _guard:
                return _guard

        success, msg = asn_service.cancelar_asn(asn_id, motivo, user_email)
        return jsonify({'success': success, ('message' if success else 'error'): msg}), (200 if success else 400)
    except Exception as e:
        logger.error(f"api_cancelar_asn: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@asn_bp.route('/api/recusar', methods=['POST'])
@module_required('wms')
def api_recusar_asn():
    """
    POST /wms/asn/api/recusar
    Recusa um ASN agendado — ação exclusiva do operador WMS.
    """
    if _is_transportadora():      # ← DENTRO da função
        return jsonify({'success': False, 'error': 'Acesso negado'}), 403

    try:
        data = request.get_json()
        if not data or not data.get('asn_id'):
            return jsonify({'success': False, 'error': 'asn_id obrigatório'}), 400

        asn_id         = data['asn_id']
        motivo         = data.get('motivo', 'Recusado pelo operador').strip()
        avaria_id      = data.get('avaria_id', '')
        empresa        = session.get('user_empresa', '')
        notas_recusadas = data.get('notas_recusadas') or []

        if not motivo:
            return jsonify({'success': False, 'error': 'Informe o motivo da recusa'}), 400

        # Isolamento por empresa: operador só recusa ASNs da sua empresa
        if empresa:
            asn_check = asn_service.get_by_id(asn_id)
            if not asn_check:
                return jsonify({'success': False, 'error': 'ASN não encontrado'}), 404
            _guard = require_empresa_match_or_404(asn_check.empresa_dest, empresa)
            if _guard:
                return _guard

        success, msg = asn_service.recusar_asn(
            asn_id=asn_id,
            motivo=motivo,
            user_email=session.get('user_email', ''),
            avaria_id=avaria_id,
            empresa=empresa,
            notas_recusadas=notas_recusadas,
        )
        return jsonify({'success': success, ('message' if success else 'error'): msg}), (200 if success else 400)

    except Exception as e:
        logger.error(f"api_recusar_asn: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
      
@asn_bp.route('/api/itens-asn', methods=['GET'])
@module_required('wms')
def api_itens_asn():
    """
    GET /wms/asn/api/itens-asn?asn_id=ASN-xxx
    Retorna itens de TODAS as notas do ASN (NF e NFD).
    Lê as abas Avarias e Itens_Avarias diretamente — sem cache.
    """
    if _is_transportadora():
        return jsonify({'success': False, 'error': 'Acesso negado'}), 403

    asn_id = request.args.get('asn_id', '').strip()
    if not asn_id:
        return jsonify({'success': False, 'error': 'asn_id obrigatório'}), 400

    try:
        empresa = session.get('user_empresa', '')

        asn = asn_service.get_by_id(asn_id)
        if not asn:
            return jsonify({'success': False, 'error': 'ASN não encontrado'}), 404

        # Isolamento por empresa
        _guard = require_empresa_match_or_404(asn.empresa_dest, empresa)
        if _guard:
            return _guard

        empresa_asn = asn.empresa_dest  # usa empresa do ASN como âncora para filtrar avarias

        # ── 1. Todos os números de nota do ASN (principal + adicionais) ──
        notas_asn = set()
        if asn.nota:
            notas_asn.add(str(asn.nota).strip())
        if asn.notas_adicionais:
            for extra in asn.notas_adicionais.split(','):
                parte = extra.strip().split('~')[0]
                partes = parte.split(':')
                num = partes[1].strip() if len(partes) > 1 else partes[0].strip()
                if num:
                    notas_asn.add(num)
        notas_asn.discard('')

        # ── 2. IDs gravados no campo avaria_id do ASN ──
        avaria_ids = {av.strip() for av in str(asn.avaria_id or '').split(',') if av.strip()}
        tem_vinculo_formal = bool(avaria_ids)

        # ── 3. Lê a aba Avarias diretamente e mapeia os dados ──
        # Filtra por empresa_dest do ASN para evitar colisão de notas entre empresas.
        avaria_nota_map = {}  # {avaria_id: nota}
        avarias_raw = []
        try:
            avarias_raw = asn_service.sheets.get_all_records('Avarias')
            for r in avarias_raw:
                nota_r    = str(r.get('nota',    '') or '').strip()
                nfd_r     = str(r.get('nfd',     '') or '').strip()
                id_r      = str(r.get('id',      '') or '').strip()
                empresa_r = str(r.get('empresa', '') or '').strip()
                if not id_r:
                    continue
                # Só amplia por nota se a avaria pertence à mesma empresa do ASN
                if empresa_asn and empresa_r and empresa_r != empresa_asn:
                    # Avaria de outra empresa: inclui apenas se o id já estava no ASN
                    if id_r not in avaria_ids:
                        continue
                
                # Se o ASN já tem os avaria_id validados formalmente no processamento, NÃO amplia por número (evitando NFD homônima)
                if not tem_vinculo_formal and (nota_r in notas_asn or nfd_r in notas_asn):
                    avaria_ids.add(id_r)
                if id_r in avaria_ids:
                    if nfd_r and nfd_r in notas_asn:
                        exibicao = nfd_r
                    elif nota_r and nota_r in notas_asn:
                        exibicao = nota_r
                    else:
                        exibicao = nfd_r or nota_r
                    avaria_nota_map[id_r] = {
                        'exibicao': exibicao,
                        'nota': nota_r,
                        'nfd': nfd_r
                    }
        except Exception as e:
            logger.warning(f"Erro ao ler aba Avarias: {e}")

        # ── 4. Itens de todos os avaria_ids ──
        try:
            itens_raw = asn_service.sheets.get_all_records('Itens_Avarias')
        except Exception as e:
            logger.warning(f"Erro ao ler Itens_Avarias: {e}")
            itens_raw = []

        from modules.wms.models.item_avaria import ItemAvaria

        # Identifica para cada avaria_id se existe alguma quantidade avariada > 0 cadastrada no Registrar Avarias
        tem_qtd_avariada = set()
        for r in itens_raw:
            av_id = str(r.get('avaria_id', '') or '').strip()
            if av_id in avaria_ids:
                try:
                    if float(r.get('qtd_avariada', 0) or 0) > 0:
                        tem_qtd_avariada.add(av_id)
                except (ValueError, TypeError):
                    pass

        itens = []
        for r in itens_raw:
            av_id = str(r.get('avaria_id', '') or '').strip()
            if av_id not in avaria_ids:
                continue
            if r.get('status_etiqueta') == 'baixada':
                continue
            try:
                it = ItemAvaria.from_dict(r)
                info = avaria_nota_map.get(av_id, {})
                qtd_ava = float(it.qtd_avariada or 0)
                qtd_neg = float(it.qtdneg or 0)

                # Se a nota possui itens com qtd_avariada (Registrar Avarias), essa é a quantidade real da conferência.
                # Caso contrário (remessa 100% íntegra ou gerada automaticamente sem edição), usa qtdneg.
                qtd_ref = qtd_ava if (av_id in tem_qtd_avariada) else qtd_neg
                if qtd_ref <= 0:
                    continue

                ja_proc = bool(str(getattr(it, 'id_etiqueta', '') or '').strip() or getattr(it, 'status_etiqueta', 'pendente') != 'pendente')
                itens.append({
                    'avaria_id':    av_id,
                    'nota':         info.get('exibicao', '') if isinstance(info, dict) else str(info),
                    'nf_orig':      info.get('nota', '') if isinstance(info, dict) else '',
                    'nfd':          info.get('nfd', '') if isinstance(info, dict) else '',
                    'sequencia':    it.sequencia,
                    'codprod':      str(it.codprod or ''),
                    'descrprod':    str(it.descrprod or ''),
                    'qtdneg':       qtd_ref,
                    'qtd_avariada': qtd_ava,
                    'qtd_orig_nf':  qtd_neg,
                    'ja_processado': ja_proc,
                    'status_etiqueta': str(getattr(it, 'status_etiqueta', 'pendente')),
                    'id_etiqueta':  str(getattr(it, 'id_etiqueta', '') or '').strip(),
                })
            except Exception as item_err:
                logger.warning(f"Item ignorado: {item_err}")

        logger.info(f"itens-asn {asn_id}: {len(itens)} itens | avaria_ids={avaria_ids} | notas={notas_asn}")
        return jsonify({
            'success':   True,
            'itens':     itens,
            'avaria_id': ','.join(avaria_ids),
            '_debug': {
                'notas_asn':        list(notas_asn),
                'avaria_ids':       list(avaria_ids),
                'asn_avaria_id':    asn.avaria_id or '',
                'total_avarias_raw': len(avarias_raw),
                'total_itens_raw':   len(itens_raw),
                'itens_retornados':  len(itens),
            },
        })

    except Exception as e:
        logger.error(f"api_itens_asn: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@asn_bp.route('/api/lista', methods=['GET'])
@module_required('wms')
def api_lista_asns():
    """
    GET /wms/asn/api/lista?status=agendado&empresa=Matriz
    Retorna lista de ASNs em JSON (para AJAX / futura integração).
    Transportadora vê apenas os próprios; operadores veem todos da empresa.
    """
    try:
        empresa = session.get('user_empresa', '')
        status  = request.args.get('status', '')

        if _is_transportadora():
            asns = asn_service.get_by_transportadora(session.get('user_email', ''))
        else:
            asns = asn_service.get_all(empresa=empresa, status=status)

        return jsonify({
            'success': True,
            'asns': [a.to_dict() for a in asns],
            'total': len(asns),
        })
    except Exception as e:
        logger.error(f"api_lista_asns: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@asn_bp.route('/api/stats', methods=['GET'])
@module_required('wms')
def api_stats():
    """GET /wms/asn/api/stats — contadores para cards do dashboard."""
    try:
        empresa = session.get('user_empresa', '')
        stats = asn_service.get_stats(empresa=empresa) if asn_service else {}
        return jsonify({'success': True, **stats})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@asn_bp.route('/api/upload-fotos', methods=['POST'])
@module_required('wms')
def api_upload_fotos_asn():
    """
    POST /wms/asn/api/upload-fotos (multipart/form-data)
    Fields: asn_id (str), fotos (files, max 10)
    Upload fotos do recebimento para o Google Drive.
    """
    if _is_transportadora():
        return jsonify({'success': False, 'error': 'Acesso negado'}), 403

    asn_id = request.form.get('asn_id', '').strip()
    if not asn_id:
        return jsonify({'success': False, 'error': 'asn_id obrigatório'}), 400

    fotos = request.files.getlist('fotos')
    if not fotos or len(fotos) == 0:
        return jsonify({'success': False, 'error': 'Nenhuma foto enviada'}), 400
    if len(fotos) > 10:
        return jsonify({'success': False, 'error': 'Máximo de 10 fotos por upload'}), 400

    ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp'}
    for foto in fotos:
        ext = (foto.filename.rsplit('.', 1)[-1] if '.' in foto.filename else '').lower()
        if ext not in ALLOWED_EXTENSIONS:
            return jsonify({'success': False, 'error': f'Extensão não permitida: {foto.filename}. Use jpg, jpeg, png ou webp.'}), 400

    empresa = session.get('user_empresa', '')
    asn = asn_service.get_by_id(asn_id)
    if not asn:
        return jsonify({'success': False, 'error': 'ASN não encontrado'}), 404
    _guard = require_empresa_match_or_404(asn.empresa_dest, empresa)
    if _guard:
        return _guard

    drive_service = getattr(current_app, 'drive_upload_service', None)
    if not drive_service:
        return jsonify({'success': False, 'error': 'Serviço de upload de fotos não disponível'}), 503

    temp_files = []
    try:
        for foto in fotos:
            ext = (foto.filename.rsplit('.', 1)[-1] if '.' in foto.filename else 'jpg').lower()
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}')
            foto.save(tmp.name)
            tmp.close()
            temp_files.append(tmp.name)

        nf = asn.nota or asn_id
        sucesso, link_pasta, mensagem, lista_fotos = drive_service.upload_fotos_produto(
            id_etiqueta=f"ASN-{asn_id}",
            filepaths=temp_files,
            nfd=None,
            nf=nf,
        )

        if sucesso:
            asn_service.update_link_fotos(asn_id, link_pasta)
            return jsonify({
                'success': True,
                'link_drive': link_pasta,
                'total_fotos': len(lista_fotos) if lista_fotos else len(temp_files),
                'asn_id': asn_id,
            })
        else:
            logger.error(f"upload_fotos_produto falhou para ASN {asn_id}: {mensagem}")
            return jsonify({'success': False, 'error': mensagem or 'Falha no upload para o Drive'}), 500

    except Exception as e:
        logger.error(f"api_upload_fotos_asn({asn_id}): {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

    finally:
        for path in temp_files:
            try:
                os.remove(path)
            except Exception:
                pass
