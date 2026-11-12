"""
modules/wms/routes/avarias_standalone_routes.py
Rotas de avarias com suporte completo a etiquetas e automações de status.
"""

import logging
import traceback
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, current_app
from werkzeug.utils import secure_filename
from datetime import datetime
import os
import tempfile
import json

try:
    from utils.decorators import api_module_required
except ImportError:
    def api_module_required(module_name):
        def decorator(f):
            return f
        return decorator

logger = logging.getLogger(__name__)

# Blueprint
avarias_bp = Blueprint('avarias', __name__)

# SERVIÇOS GLOBAIS (inicializados pelo init_avarias_routes)
sankhya_service       = None  # SankhyaService
avaria_service        = None  # SankhyaAvariaService - operações Sankhya
wms_service           = None  # WmsAvariaService - operações Sheets
auth_service          = None  # AuthService
etiqueta_service_g    = None  # EtiquetaService
cache_sankhya_service = None  # CacheSankhyaService
automacao_service_g   = None  # AutomacaoStatusService


def init_avarias_routes(sankhya_svc, avaria_svc, auth_svc, etiqueta_svc=None,
                        cache_sankhya_svc=None, wms_svc=None, automacao_service=None):
    """
    Inicializa rotas com as dependências necessárias.

    Args:
        sankhya_svc:       SankhyaService
        avaria_svc:        SankhyaAvariaService (operações Sankhya)
        auth_svc:          AuthService
        etiqueta_svc:      EtiquetaService
        cache_sankhya_svc: CacheSankhyaService
        wms_svc:           WmsAvariaService (operações Sheets)
        automacao_service: AutomacaoStatusService
    """
    global sankhya_service, avaria_service, wms_service, auth_service, \
           etiqueta_service_g, cache_sankhya_service, automacao_service_g

    logger.info("=" * 80)
    logger.info("INICIALIZANDO ROTAS DE AVARIAS")
    logger.info("=" * 80)

    sankhya_service       = sankhya_svc
    avaria_service        = avaria_svc
    wms_service           = wms_svc
    auth_service          = auth_svc
    etiqueta_service_g    = etiqueta_svc
    cache_sankhya_service = cache_sankhya_svc
    automacao_service_g   = automacao_service

    logger.info(f"   sankhya_service:       {type(sankhya_service).__name__       if sankhya_service       else 'None'}")
    logger.info(f"   avaria_service:        {type(avaria_service).__name__        if avaria_service        else 'None'}")
    logger.info(f"   wms_service:           {type(wms_service).__name__           if wms_service           else 'None'}")
    logger.info(f"   auth_service:          {type(auth_service).__name__          if auth_service          else 'None'}")
    logger.info(f"   etiqueta_service:      {type(etiqueta_service_g).__name__    if etiqueta_service_g    else 'None'}")
    logger.info(f"   cache_sankhya_service: {type(cache_sankhya_service).__name__ if cache_sankhya_service else 'None'}")
    logger.info(f"   automacao_service:     {type(automacao_service_g).__name__   if automacao_service_g   else 'None'}")

    if not avaria_service:
        logger.warning(" avaria_service não inicializado — algumas funcionalidades podem não funcionar")
    if not wms_service:
        logger.warning(" wms_service não inicializado — atualização de itens não funcionará")
    if not etiqueta_service_g:
        logger.warning(" etiqueta_service não inicializado — geração de etiquetas não funcionará")
    if not automacao_service_g:
        logger.warning(" automacao_service não inicializado — triggers de status não serão disparados")

    logger.info("=" * 80)
    logger.info("Rotas de avarias inicializadas")
    logger.info("=" * 80)


# HELPERS

def check_permission(screen_name: str, nivel: str = 'visualizar') -> bool:
    """Verifica se usuário tem permissão para acessar a tela no nível exigido."""
    if 'user_email' not in session:
        return False
    permissions = session.get('permissions', {})
    allowed_modules = permissions.get('modules', [])
    if 'wms' not in allowed_modules:
        return False
    screens = permissions.get('screens', {}).get('wms', {})
    valor = screens.get('*') or screens.get(screen_name)
    if not valor:
        return False
    if nivel == 'visualizar':
        return True
    return valor in ('editar', 'admin') or valor is True


def require_permission(screen_name: str, nivel: str = 'visualizar'):
    """Decorator para verificar permissões."""
    def decorator(f):
        def decorated_function(*args, **kwargs):
            if not check_permission(screen_name, nivel):
                return jsonify({'success': False, 'error': 'Sem permissão para esta ação'}), 403
            return f(*args, **kwargs)
        decorated_function.__name__ = f.__name__
        return decorated_function
    return decorator


def _disparar_automacao(trigger: str, avaria_id: str, contexto: dict = None,
                        operador: str = 'sistema', bloqueante: bool = False):
    """
    Dispara um trigger de automação de status para uma avaria.

    Args:
        trigger:    Nome do trigger (ex: 'etiqueta_inicial_gerada')
        avaria_id:  ID da avaria
        contexto:   Dados extras passados às condições da regra
        operador:   Email do operador que disparou a ação
        bloqueante: Se True, retorna lista de mensagens de bloqueio para o caller
                    decidir rejeitar a requisição. Se False, é totalmente silencioso
                    (background) — erros só aparecem no log.

    Returns:
        bloqueante=False → [] sempre (não interrompe o fluxo)
        bloqueante=True  → List[str] com mensagens de bloqueio (vazia = sem bloqueios)
    """
    if not automacao_service_g:
        logger.warning(f"automacao_service não disponível — trigger '{trigger}' ignorado")
        return []
    if not wms_service:
        logger.warning(f"wms_service não disponível — empresa não identificada para trigger '{trigger}'")
        return []

    try:
        avaria_obj = wms_service.get_avaria_by_id(avaria_id)
        empresa    = getattr(avaria_obj, 'empresa', '') or ''
        
        ctx = contexto or {}
        
        # Enriquecer contexto com dados do primeiro item se não houver local no header
        if not ctx.get('local') and not getattr(avaria_obj, 'local', ''):
            itens = wms_service.get_itens_avaria(avaria_id)
            if itens:
                item = itens[0]
                ctx['codlocalorig'] = str(item.get('codlocalorig', '')).strip()
                ctx['local_item']   = str(item.get('localizacao', '') or item.get('local', '')).strip()
                # Se ainda não temos um local no contexto, usamos o do item como fallback
                if not ctx.get('local'):
                    ctx['local'] = ctx['local_item'] or ctx['codlocalorig']

        resultados = automacao_service_g.aplicar(
            trigger=trigger,
            avaria_id=avaria_id,
            empresa=empresa,
            contexto=ctx,
            operador_email=operador,
        )

        aplicados  = [r for r in resultados if r.sucesso]
        bloqueados = [r for r in resultados if r.bloqueado]

        if aplicados:
            logger.info(
                f"🤖 Automação '{trigger}' aplicada em {avaria_id}: "
                f"{[r.novo_status for r in aplicados]}"
            )
        if bloqueados:
            logger.warning(
                f"🚫 Automação '{trigger}' bloqueada em {avaria_id}: "
                f"{[r.msg for r in bloqueados]}"
            )

        if bloqueante:
            return automacao_service_g.extrair_erros_visiveis(resultados)

        return []

    except Exception as e:
        logger.warning(f"Automação '{trigger}' falhou para {avaria_id}: {e}")
        return []


def _obter_avaria_id_da_etiqueta(id_etiqueta: str) -> str:
    """
    Tenta recuperar o avaria_id a partir de uma etiqueta.
    Retorna string vazia se não for possível.
    """
    if not etiqueta_service_g or not id_etiqueta:
        return ''
    try:
        valida, dados, _ = etiqueta_service_g.validar_etiqueta(id_etiqueta)
        if valida and dados:
            return dados.get('avaria_id', '')
    except Exception:
        pass
    return ''


# ROTAS — PÁGINAS

@avarias_bp.route('/registrar-avaria')
def registrar_avaria_page():
    """Página para registrar nova avaria."""
    if not check_permission('registrar_avaria'):
        return redirect(url_for('home.index'))
    return render_template('wms/registrar_avaria.html',
                           user_name=session.get('user_name'),
                           user_level=session.get('user_level'))


@avarias_bp.route('/listar-avarias')
def listar_avarias_page():
    """Página para listar avarias."""
    if not check_permission('listar_avarias'):
        return redirect(url_for('home.index'))
    return render_template('wms/listar_avarias.html',
                           user_name=session.get('user_name'),
                           user_level=session.get('user_level'))


@avarias_bp.route('/detalhes-avaria/<avaria_id>')
def detalhes_avaria_page(avaria_id):
    """Página com detalhes de uma avaria específica."""
    if not check_permission('listar_avarias'):
        return redirect(url_for('home.index'))
    return render_template('wms/detalhes_avaria.html',
                           avaria_id=avaria_id,
                           user_name=session.get('user_name'),
                           user_level=session.get('user_level'))


# ROTAS — API — VALIDAÇÃO E BUSCA DE NOTAS

@avarias_bp.route('/api/validar-nota', methods=['POST'])
def validar_nota_api():
    """API: Valida nota (NF ou NFD) e retorna TODOS os dados necessários."""
    try:
        data    = request.get_json()
        numnota = data.get('numnota')
        codemp  = data.get('codemp')
        nunota  = data.get('nunota')  # NUNOTA selecionado (opcional)

        if not numnota or not codemp:
            return jsonify({'success': False, 'error': 'numnota e codemp são obrigatórios'}), 400

        logger.info(f"Validando nota {numnota} (CODEMP: {codemp})...")

        if avaria_service:
            dados_cabecalho, itens_mapeados, erro_msg = \
                avaria_service.buscar_e_mapear_nota(numnota, codemp, nunota=nunota)

            if erro_msg:
                return jsonify({'success': True, 'existe': False, 'error': erro_msg})

            if not dados_cabecalho:
                return jsonify({'success': True, 'existe': False, 'error': 'Nota não encontrada'})

            return jsonify({
                'success': True,
                'existe': True,
                'dados': {
                    'nf_venda':      dados_cabecalho.get('nf_venda', ''),
                    'parceiro':      dados_cabecalho.get('parceiro', ''),
                    'transportadora': dados_cabecalho.get('transportadora', ''),
                    'volume':        dados_cabecalho.get('volume', ''),
                    'quantidade':    dados_cabecalho.get('quantidade', ''),
                    'vlrnota':       dados_cabecalho.get('vlrnota', ''),
                    'valor_nfd':     dados_cabecalho.get('valor_nfd', ''),
                    'nfd':           dados_cabecalho.get('nfd', ''),
                    'data_nota':     dados_cabecalho.get('data_nota', ''),
                    'data_nfd':      dados_cabecalho.get('data_nfd', ''),
                    'tipmov':        dados_cabecalho.get('tipmov_origem', ''),
                    'total_itens':   len(itens_mapeados) if itens_mapeados else 0,
                    'itens':         itens_mapeados if itens_mapeados else [],
                }
            })

        return jsonify({'success': False, 'error': 'Serviço Sankhya não disponível'}), 503

    except Exception as e:
        logger.error(f"Erro ao validar nota: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@avarias_bp.route('/api/buscar-nota', methods=['POST'])
@require_permission('registrar_avaria', 'editar')
def buscar_nota_api():
    """API: Busca nota fiscal no Sankhya."""
    try:
        data        = request.get_json()
        numnota     = data.get('numnota')
        codemp      = data.get('codemp')
        incluir_nfd = data.get('incluir_nfd')

        if not numnota:
            return jsonify({'success': False, 'error': 'Número da nota é obrigatório'}), 400

        logger.info(f"Buscando nota {numnota}...")

        dados_avaria, itens, erro = avaria_service.buscar_e_mapear_nota(
            numnota=numnota,
            codemp=codemp,
            incluir_nfd=incluir_nfd
        )

        if erro:
            return jsonify({'success': False, 'error': erro}), 404

        return jsonify({
            'success':        True,
            'dados_cabecalho': dados_avaria,
            'itens':          itens,
            'total_itens':    len(itens)
        })

    except Exception as e:
        logger.error(f"Erro ao buscar nota: {e}")
        return jsonify({'success': False, 'error': f"Erro ao buscar nota: {str(e)}"}), 500


# ROTAS — API — SALVAR E GERENCIAR AVARIAS

@avarias_bp.route('/api/salvar-avaria', methods=['POST'])
@require_permission('registrar_avaria', 'editar')
def salvar_avaria_api():
    """API: Salva nova avaria no Google Sheets."""
    try:
        data             = request.get_json()
        dados_cabecalho  = data.get('dados_cabecalho')
        itens            = data.get('itens')
        dados_formulario = data.get('dados_formulario')

        if not dados_cabecalho or not itens:
            return jsonify({'success': False, 'error': 'Dados incompletos'}), 400

        user_email = session.get('user_email')
        logger.info("Salvando avaria...")

        sucesso, avaria_id_novo, mensagem = avaria_service.salvar_avaria(
            dados_cabecalho=dados_cabecalho,
            itens=itens,
            dados_formulario=dados_formulario,
            user_email=user_email
        )
        
        if sucesso:
            _disparar_automacao('avaria_criada', avaria_id_novo or '', operador=user_email or 'sistema')
            return jsonify({'success': True, 'avaria_id': avaria_id_novo, 'message': mensagem})
        else:
            return jsonify({'success': False, 'error': mensagem}), 500

    except Exception as e:
        logger.error(f"Erro ao salvar avaria: {e}")
        return jsonify({'success': False, 'error': f"Erro ao salvar avaria: {str(e)}"}), 500


@avarias_bp.route('/api/listar-avarias', methods=['GET'])
@require_permission('listar_avarias')
def listar_avarias_api():
    """API: Lista avarias com filtros opcionais."""
    try:
        filtros    = {}

        if request.args.get('registrado_por'):
            filtros['registrado_por'] = request.args.get('registrado_por')

        permissions = session.get('permissions', {})
        screens_wms = permissions.get('screens', {}).get('wms', {})
        can_see_all = screens_wms.get('*', False) or screens_wms.get('ver_todas_avarias', False)
        if not can_see_all:
            filtros['registrado_por'] = session.get('user_email')

        logger.info(f"Listando avarias com filtros: {filtros}")
        avarias = avaria_service.listar_avarias(filtros)

        return jsonify({'success': True, 'avarias': avarias, 'total': len(avarias)})

    except Exception as e:
        logger.error(f"Erro ao listar avarias: {e}")
        return jsonify({'success': False, 'error': f"Erro ao listar avarias: {str(e)}"}), 500


@avarias_bp.route('/api/avaria/<avaria_id>', methods=['GET'])
@require_permission('listar_avarias')
def obter_avaria_api(avaria_id):
    """API: Obtém detalhes de uma avaria específica."""
    try:
        logger.info(f"Buscando avaria {avaria_id}...")
        avaria_detalhada = avaria_service.obter_avaria_detalhada(avaria_id)

        if not avaria_detalhada:
            return jsonify({'success': False, 'error': 'Avaria não encontrada'}), 404

        return jsonify({
            'success': True,
            'avaria': avaria_detalhada['avaria'].to_dict(),
            'itens':  avaria_detalhada['itens']
        })

    except Exception as e:
        logger.error(f"Erro ao obter avaria: {e}")
        return jsonify({'success': False, 'error': f"Erro ao obter avaria: {str(e)}"}), 500


@avarias_bp.route('/api/validar-conexao-sankhya', methods=['GET'])
@require_permission('registrar_avaria', 'editar')
def validar_conexao_sankhya():
    """API: Valida se a conexão com Sankhya está funcionando."""
    try:
        if not sankhya_service or not sankhya_service.enabled:
            return jsonify({'success': False, 'conectado': False, 'error': 'Sankhya não configurado'})

        resultado = sankhya_service.load_records(
            entity="Empresa",
            fields="CODEMP,RAZAOSOCIAL",
            limit=1
        )

        if resultado and resultado.get("status") == "1":
            return jsonify({'success': True, 'conectado': True, 'message': 'Conexão com Sankhya OK'})
        else:
            return jsonify({
                'success': False,
                'conectado': False,
                'error': resultado.get("statusMessage", "Erro na consulta")
            })

    except Exception as e:
        logger.error(f"Erro ao validar conexão: {e}")
        return jsonify({'success': False, 'conectado': False, 'error': str(e)}), 500


# ROTAS — API — ATUALIZAR ITEM DE AVARIA

@avarias_bp.route('/api/avaria/item/update', methods=['POST'])
@api_module_required('wms')
def update_item_avaria_api():
    """
    API: Atualiza a quantidade avariada ou localização de um item específico.
    (Usado para edições básicas antes da etiqueta 00 ser gerada.)
    """
    try:
        data        = request.get_json()
        avaria_id   = data.get('avaria_id')
        sequencia   = data.get('sequencia')
        qtd_avariada = data.get('qtd_avariada')
        localizacao = data.get('localizacao')

        if not avaria_id or sequencia is None:
            return jsonify({'success': False, 'error': 'ID da avaria e sequência são obrigatórios'}), 400

        updates = {}
        if qtd_avariada is not None:
            updates['qtd_avariada'] = qtd_avariada
        if localizacao is not None:
            updates['localizacao'] = localizacao

        if not updates:
            return jsonify({'success': False, 'error': 'Nenhum campo para atualizar'}), 400

        if not wms_service:
            return jsonify({'success': False, 'error': 'WMS Service indisponível'}), 503

        # Check ownership permission before editing
        user_email = session.get('user_email', '')
        permissions = session.get('permissions', {})
        screens_wms = permissions.get('screens', {}).get('wms', {})
        can_edit_others = screens_wms.get('*', False) or screens_wms.get('editar_avaria_alheia', False)
        if not can_edit_others and avaria_service:
            avaria_detalhada = avaria_service.obter_avaria_detalhada(avaria_id)
            if avaria_detalhada:
                registrado_por = getattr(avaria_detalhada.get('avaria'), 'registrado_por', '') or ''
                if registrado_por and registrado_por != user_email:
                    return jsonify({'success': False, 'error': 'Sem permissão para editar avaria de outro usuário'}), 403

        success, message = wms_service.update_item_avaria(
            avaria_id=avaria_id,
            sequencia=sequencia,
            **updates
        )

        if success:
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'error': message}), 500

    except Exception as e:
        logger.error(f"Erro ao atualizar item de avaria: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': f"Erro interno: {str(e)}"}), 500


# ROTAS — API — ETIQUETAS

@avarias_bp.route('/api/etiquetas/gerar-iniciais', methods=['POST'])
@api_module_required('wms')
def gerar_etiquetas_iniciais_api():
    """API: Gera etiquetas iniciais (prefixo 00) para produtos de uma avaria."""
    try:
        if not etiqueta_service_g:
            logger.error("etiqueta_service não inicializado!")
            return jsonify({'success': False, 'error': 'Serviço de etiquetas temporariamente indisponível'}), 503

        data      = request.get_json()
        avaria_id = data.get('avaria_id') if data else None

        if not avaria_id or avaria_id == '' or avaria_id == 'undefined':
            return jsonify({'success': False, 'error': f"avaria_id obrigatório (recebido: '{avaria_id}')"}), 400

        operador = session.get('user_email', 'sistema')
        empresa  = session.get('user_empresa', '')
        sucesso, etiquetas, msg, codlocal_destino = etiqueta_service_g.criar_etiquetas_iniciais(avaria_id, operador, empresa)

        if sucesso:
            logger.info(f"{len(etiquetas)} etiquetas geradas")
            erros_auto = _disparar_automacao(
                trigger='etiqueta_inicial_gerada',
                avaria_id=avaria_id,
                contexto={
                    'tipo_etiqueta': 'inicial',
                    'codlocal_destino': codlocal_destino,
                    'codlocaldest': codlocal_destino,
                },
                operador=operador,
                bloqueante=True,
            )
            if erros_auto:
                return jsonify({
                    'success': False,
                    'bloqueado': True,
                    'message': ' | '.join(erros_auto),
                }), 409
            return jsonify({'success': True, 'message': msg, 'etiquetas': etiquetas})
        else:
            logger.error(f"Falha ao gerar etiquetas: {msg}")
            return jsonify({'success': False, 'error': msg}), 400

    except Exception as e:
        logger.error(f"Exception em gerar_etiquetas_iniciais_api: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@avarias_bp.route('/api/etiquetas/triar', methods=['POST'])
@api_module_required('wms')
def triar_produto_api():
    """API: Realiza triagem de produto (muda etiqueta de 00 para local definitivo)."""
    try:
        if not etiqueta_service_g:
            return jsonify({'success': False, 'error': 'Serviço de etiquetas indisponível'}), 503

        data             = request.get_json()
        id_etiqueta_00   = data.get('id_etiqueta_00')
        codlocal_destino = data.get('codlocal_destino')
        observacao       = data.get('observacao', '')

        if not id_etiqueta_00 or not codlocal_destino:
            return jsonify({'success': False, 'error': 'Dados incompletos'}), 400

        operador  = session.get('user_email', 'sistema')
        empresa   = session.get('user_empresa', '')

        # Recuperar avaria_id antes da triagem (para disparar automação depois)
        avaria_id = _obter_avaria_id_da_etiqueta(id_etiqueta_00)

        sucesso, novo_id, msg = etiqueta_service_g.triar_produto(
            id_etiqueta_00,
            int(codlocal_destino),
            operador,
            observacao,
            empresa=empresa,
        )
        if sucesso:
            if avaria_id:
                # ── Atualizar campo 'local' da avaria ANTES de disparar triggers ──
                local_str = str(codlocal_destino)
                if wms_service:
                    try:
                        wms_service.update_avaria(avaria_id, local=local_str)
                        logger.info(f"avaria {avaria_id}.local atualizado para '{local_str}'")
                    except Exception as e_loc:
                        logger.warning(f"Não foi possível atualizar local da avaria: {e_loc}")

                # ── item_localizado (bloqueante) ──────────────────────────────
                erros_auto = _disparar_automacao(
                    trigger='item_localizado',
                    avaria_id=avaria_id,
                    contexto={'prefixo': novo_id.split('-')[0] if novo_id else '', 'codlocal': codlocal_destino, 'local': local_str},
                    operador=operador,
                    bloqueante=True,
                )
                if erros_auto:
                    return jsonify({
                        'success': False,
                        'bloqueado': True,
                        'message': ' | '.join(erros_auto),
                    }), 409

                # ── etiqueta_final_gerada (bloqueante) — avaria saiu do 00 ───
                erros_final = _disparar_automacao(
                    trigger='etiqueta_final_gerada',
                    avaria_id=avaria_id,
                    contexto={'codlocal': codlocal_destino, 'local': local_str, 'tipo_etiqueta': 'final'},
                    operador=operador,
                    bloqueante=True,
                )
                if erros_final:
                    return jsonify({
                        'success': False,
                        'bloqueado': True,
                        'message': ' | '.join(erros_final),
                    }), 409

            return jsonify({'success': True, 'message': msg, 'novo_id_etiqueta': novo_id})
        else:
            return jsonify({'success': False, 'error': msg}), 400

    except Exception as e:
        logger.error(f"Erro na triagem: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@avarias_bp.route('/api/etiquetas/baixar', methods=['POST'])
@api_module_required('wms')
def baixar_etiqueta_api():
    """API: Baixa etiqueta após movimentação (venda, transferência, descarte)."""
    try:
        if not etiqueta_service_g:
            return jsonify({'success': False, 'error': 'Serviço de etiquetas indisponível'}), 503

        data              = request.get_json()
        id_etiqueta       = data.get('id_etiqueta')
        tipo_movimentacao = data.get('tipo_movimentacao', 'SAIDA')
        observacao        = data.get('observacao', '')
        quantidade_recusada = float(data.get('quantidade_recusada') or 0)

        if not id_etiqueta:
            return jsonify({'success': False, 'error': 'ID da etiqueta obrigatório'}), 400

        operador  = session.get('user_email', 'sistema')
        avaria_id = _obter_avaria_id_da_etiqueta(id_etiqueta)
        empresa   = session.get('user_empresa', '')

        id_etiqueta_final = id_etiqueta

        if tipo_movimentacao == 'RECUSA':
            # Fluxo completo de recusa: gera 1ª etiqueta (00) + 2ª etiqueta (97→497)
            sucesso, id_etiqueta_97, msg = etiqueta_service_g.recusar_item(
                id_etiqueta=id_etiqueta,
                quantidade_recusada=quantidade_recusada,
                operador=operador,
                empresa=empresa,
                observacao=observacao,
                criar_validacao=False,  # por enquanto não gerar lote/pendência para o 497 (pedido do usuário)
            )
            if sucesso:
                id_etiqueta_final = id_etiqueta_97
        else:
            sucesso, msg = etiqueta_service_g.baixar_etiqueta(
                id_etiqueta,
                tipo_movimentacao,
                operador,
                observacao
            )

        if sucesso:
            if avaria_id:
                trigger_auto = 'item_recusado' if tipo_movimentacao == 'RECUSA' else 'baixa_pagamento'
                erros_auto = _disparar_automacao(
                    trigger=trigger_auto,
                    avaria_id=avaria_id,
                    contexto={'tipo_movimentacao': tipo_movimentacao},
                    operador=operador,
                    bloqueante=True,
                )
                if erros_auto:
                    return jsonify({
                        'success': False,
                        'bloqueado': True,
                        'message': ' | '.join(erros_auto),
                    }), 409
            return jsonify({'success': True, 'message': msg, 'id_etiqueta_final': id_etiqueta_final})
        else:
            return jsonify({'success': False, 'error': msg}), 400

    except Exception as e:
        logger.error(f"Erro ao baixar etiqueta: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@avarias_bp.route('/api/etiquetas/recusar', methods=['POST'])
@api_module_required('wms')
def recusar_etiqueta_api():
    """Endpoint dedicado para recusa de item: gera etiqueta 00 + etiqueta 97→local centena."""
    import traceback as _tb
    try:
        if not etiqueta_service_g:
            logger.error("recusar_etiqueta_api: etiqueta_service_g não inicializado")
            return jsonify({'success': False, 'error': 'Serviço de etiquetas indisponível'}), 503

        data = request.get_json()
        id_etiqueta         = data.get('id_etiqueta', '').strip()
        observacao          = data.get('observacao', '')
        quantidade_recusada = float(data.get('quantidade_recusada') or 0)

        logger.info(f"recusar_etiqueta_api: etiqueta={id_etiqueta} qtd={quantidade_recusada}")

        if not id_etiqueta:
            return jsonify({'success': False, 'error': 'id_etiqueta obrigatório'}), 400

        operador = session.get('user_email', 'sistema')
        empresa  = session.get('user_empresa', '')
        avaria_id = _obter_avaria_id_da_etiqueta(id_etiqueta)

        sucesso, id_etiqueta_97, msg = etiqueta_service_g.recusar_item(
            id_etiqueta=id_etiqueta,
            quantidade_recusada=quantidade_recusada,
            operador=operador,
            empresa=empresa,
            observacao=observacao,
            criar_validacao=False,  # por enquanto não gerar lote/pendência para o 497 (pedido do usuário)
        )

        logger.info(f"{'' if sucesso else ''} recusar_item → sucesso={sucesso} etq97={id_etiqueta_97} msg={msg}")

        if not sucesso:
            return jsonify({'success': False, 'error': msg}), 400

        id_etiqueta_final = id_etiqueta_97 or id_etiqueta

        if avaria_id:
            erros_auto = _disparar_automacao(
                trigger='item_recusado',
                avaria_id=avaria_id,
                contexto={'tipo_movimentacao': 'RECUSA'},
                operador=operador,
                bloqueante=True,
            )
            if erros_auto:
                return jsonify({'success': False, 'bloqueado': True, 'message': ' | '.join(erros_auto)}), 409

        return jsonify({'success': True, 'message': msg, 'id_etiqueta_final': id_etiqueta_final})

    except Exception as e:
        logger.error(f"recusar_etiqueta_api exception: {e}\n{_tb.format_exc()}")
        return jsonify({'success': False, 'error': str(e)}), 500


@avarias_bp.route('/api/etiquetas/transferir', methods=['POST'])
@api_module_required('wms')
def transferir_etiqueta_api():
    """
    API: Transfere produto para outro local WMS (ex: 696 → local definitivo).
    Usa transferir_produto, que cria registro de validação com origem correta
    e auto-lote para o local de destino (exibindo "696 → local_final").
    """
    try:
        if not etiqueta_service_g:
            return jsonify({'success': False, 'error': 'Serviço de etiquetas indisponível'}), 503

        data                = request.get_json()
        id_etiqueta_origem  = data.get('id_etiqueta_origem')
        codlocal_destino    = data.get('codlocal_destino')
        observacao          = data.get('observacao', '')

        if not id_etiqueta_origem or not codlocal_destino:
            return jsonify({'success': False, 'error': 'Dados incompletos'}), 400

        operador  = session.get('user_email', 'sistema')
        empresa   = session.get('user_empresa', '')
        avaria_id = _obter_avaria_id_da_etiqueta(id_etiqueta_origem)

        sucesso, nova_etiqueta, msg = etiqueta_service_g.transferir_produto(
            id_etiqueta_origem,
            int(codlocal_destino),
            operador,
            observacao,
            empresa=empresa,
        )

        if sucesso:
            if avaria_id:
                # ── Atualizar campo 'local' da avaria ANTES de disparar triggers ──
                # Necessário porque _valor_campo() prioriza o atributo da avaria
                # sobre o contexto. Se não atualizarmos aqui, condições como
                # {"campo": "local", "valor": "497"} leriam o local antigo.
                local_str = str(codlocal_destino)
                if wms_service:
                    try:
                        wms_service.update_avaria(avaria_id, local=local_str)
                        logger.info(f"avaria {avaria_id}.local atualizado para '{local_str}'")
                    except Exception as e_loc:
                        logger.warning(f"Não foi possível atualizar local da avaria: {e_loc}")

                # ── Disparar item_transferido (silencioso) ────────────────────
                _disparar_automacao(
                    trigger='item_transferido',
                    avaria_id=avaria_id,
                    contexto={'codlocal': codlocal_destino, 'local': local_str, 'nova_etiqueta': nova_etiqueta},
                    operador=operador,
                )

                # ── Disparar etiqueta_final_gerada (bloqueante) ───────────────
                # Regras parametrizadas com esse trigger + condição de local
                # (ex: local == "497") são avaliadas com o valor já atualizado.
                erros_auto = _disparar_automacao(
                    trigger='etiqueta_final_gerada',
                    avaria_id=avaria_id,
                    contexto={'codlocal': codlocal_destino, 'local': local_str, 'tipo_etiqueta': 'final'},
                    operador=operador,
                    bloqueante=True,
                )
                if erros_auto:
                    return jsonify({
                        'success': False,
                        'bloqueado': True,
                        'message': ' | '.join(erros_auto),
                    }), 409

            return jsonify({'success': True, 'message': msg, 'nova_etiqueta': nova_etiqueta})
        else:
            return jsonify({'success': False, 'error': msg}), 400

    except Exception as e:
        logger.error(f"Erro ao transferir etiqueta: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@avarias_bp.route('/api/etiquetas/validar', methods=['POST'])
@api_module_required('wms')
def validar_etiqueta_api():
    """API: Valida etiqueta (bipagem/digitação manual)."""
    try:
        if not etiqueta_service_g:
            return jsonify({'success': False, 'error': 'Serviço de etiquetas indisponível'}), 503

        data        = request.get_json()
        id_etiqueta = data.get('id_etiqueta')

        if not id_etiqueta:
            return jsonify({'success': False, 'error': 'id_etiqueta obrigatório'}), 400

        valida, dados, msg = etiqueta_service_g.validar_etiqueta(id_etiqueta)

        if valida:
            return jsonify({'success': True, 'valida': True, 'dados': dados})
        else:
            return jsonify({
                'success': True,
                'valida': False,
                'erro': msg or 'Etiqueta inválida (não encontrada ou erro de leitura)'
            })

    except Exception as e:
        logger.error(f"Erro na validação: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@avarias_bp.route('/api/etiquetas/quebrar', methods=['POST'])
@api_module_required('wms')
def quebrar_produto_api():
    """
    API: Quebra produto em múltiplos itens.
    Baixa etiqueta origem e gera múltiplas novas etiquetas.
    """
    try:
        if not etiqueta_service_g:
            return jsonify({'success': False, 'error': 'Serviço de etiquetas indisponível'}), 503

        data               = request.get_json()
        id_etiqueta_origem = data.get('id_etiqueta_origem')
        quebras            = data.get('quebras', [])

        if not id_etiqueta_origem or not quebras:
            return jsonify({'success': False, 'error': 'Dados incompletos'}), 400

        # Validar quantidade mínima para quebra
        valida, dados_etiqueta, msg_val = etiqueta_service_g.validar_etiqueta(id_etiqueta_origem)
        if not valida:
            return jsonify({'success': False, 'error': f'Erro ao validar etiqueta de origem: {msg_val}'}), 400

        try:
            qtd_original = float(dados_etiqueta.get('qtd_avariada', 0))
            if qtd_original <= 1:
                return jsonify({
                    'success': False,
                    'error': f'A quantidade do item ({qtd_original}) é insuficiente para quebra. É necessário ter mais de 1 unidade.'
                }), 400
        except (ValueError, TypeError):
            return jsonify({'success': False, 'error': 'Erro ao processar quantidade do item para quebra.'}), 400

        if len(quebras) < 2:
            return jsonify({'success': False, 'error': 'São necessárias pelo menos 2 quebras'}), 400

        operador  = session.get('user_email', 'sistema')
        avaria_id = _obter_avaria_id_da_etiqueta(id_etiqueta_origem)

        sucesso, novas_etiquetas, msg = etiqueta_service_g.quebrar_produto(
            id_etiqueta_origem,
            quebras,
            operador
        )

        if sucesso:
            # Não há trigger específico de quebra, mas se quiser adicionar no futuro é aqui
            return jsonify({'success': True, 'message': msg, 'novas_etiquetas': novas_etiquetas})
        else:
            return jsonify({'success': False, 'error': msg}), 400

    except Exception as e:
        logger.error(f"Erro ao quebrar produto: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ROTAS — API — LOCAIS E MOTIVOS

@avarias_bp.route('/api/locais-financeiros')
@api_module_required('wms')
def listar_locais_financeiros_api():
    """API: Lista locais financeiros (TGFLOC) do Sankhya."""
    try:
        if not cache_sankhya_service:
            logger.error("cache_sankhya_service não inicializado!")
            return jsonify({'success': False, 'error': 'Cache Sankhya indisponível'}), 503

        locais = cache_sankhya_service.listar_locais_para_selecao(apenas_estoque=False)
        return jsonify({'success': True, 'locais': locais, 'total': len(locais)})

    except Exception as e:
        logger.error(f"[API] Erro ao listar locais: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': f'Erro interno: {str(e)}'}), 500


@avarias_bp.route('/api/motivos-devolucao')
@api_module_required('wms')
def listar_motivos_devolucao_api():
    """API: Lista motivos de devolução (AD_CADMOTDEV) do Sankhya."""
    try:
        if not cache_sankhya_service:
            logger.error("cache_sankhya_service não inicializado!")
            return jsonify({'success': False, 'error': 'Cache Sankhya indisponível'}), 503

        logger.info("Carregando motivos de devolução...")
        motivos = cache_sankhya_service.listar_motivos_para_selecao()
        logger.info(f"{len(motivos)} motivos carregados")

        return jsonify({'success': True, 'motivos': motivos, 'total': len(motivos)})

    except Exception as e:
        logger.error(f"[API] Erro ao listar motivos: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': f'Erro interno: {str(e)}'}), 500


# ROTAS — API — UPLOAD DE FOTOS

@avarias_bp.route('/api/produto/upload-fotos', methods=['POST'])
@api_module_required('wms')
def upload_fotos_drive_api():
    """
    Faz upload de fotos para o Google Drive com estrutura hierárquica NFD/Etiqueta.

    Fluxo:
    1. Validar etiqueta e fotos recebidas
    2. Buscar NFD/NF da avaria
    3. Recuperar pasta_id_drive existente (se houver)
    4. Upload hierárquico para o Drive
    5. Salvar pasta_id no Google Sheets
    """
    temp_files = []

    try:
        # ── 1. Validar dados recebidos ────────────────────────────────────
        id_etiqueta = request.form.get('id_etiqueta', '').strip()
        avaria_id_form = request.form.get('avaria_id', '').strip()
        sequencia_form = request.form.get('sequencia', '').strip()
        fotos       = request.files.getlist('fotos')

        if not id_etiqueta and not (avaria_id_form and sequencia_form):
            return jsonify({'success': False, 'error': 'id_etiqueta ou avaria_id/sequencia obrigatórios'}), 400
        if not fotos:
            return jsonify({'success': False, 'error': 'Nenhuma foto enviada'}), 400
        if len(fotos) > 10:
            return jsonify({'success': False, 'error': 'Máximo de 10 fotos'}), 400

        # ── 2. Validar extensões ──────────────────────────────────────────
        allowed_extensions = {'jpg', 'jpeg', 'png', 'webp'}
        for foto in fotos:
            if '.' not in foto.filename:
                return jsonify({'success': False, 'error': f'Arquivo inválido: {foto.filename}'}), 400
            ext = foto.filename.rsplit('.', 1)[1].lower()
            if ext not in allowed_extensions:
                return jsonify({'success': False, 'error': f'Tipo não permitido: {ext}'}), 400

        # ── 3. Obter serviços necessários ─────────────────────────────────
        if not hasattr(current_app, 'drive_upload_service'):
            logger.error("DriveUploadService não inicializado no app")
            return jsonify({'success': False, 'error': 'Serviço indisponível'}), 503

        drive_service          = current_app.drive_upload_service
        etiqueta_service       = current_app.etiqueta_service
        wms_svc                = current_app.wms_service
        avaria_sankhya_service = current_app.avaria_sankhya_service

        if not etiqueta_service:
            logger.error("EtiquetaService não inicializado")
            return jsonify({'success': False, 'error': 'Serviço indisponível'}), 503

        # ── 4. Buscar dados da avaria (NFD, NF, pasta_id existente) ───────
        pasta_id_existente = None
        nfd                = None
        nf                 = None
        avaria_id          = None
        sequencia          = None
        valida             = False

        logger.info(f"Buscando dados da etiqueta: {id_etiqueta or 'sem etiqueta'}")
        
        if id_etiqueta:
            valida, dados_etiqueta, msg = etiqueta_service.validar_etiqueta(id_etiqueta)
            if valida:
                avaria_id          = dados_etiqueta.get('avaria_id')
                sequencia          = dados_etiqueta.get('sequencia')
                pasta_id_existente = dados_etiqueta.get('pasta_id_drive')
        else:
            try:
                seq_int = int(sequencia_form)
                item_avaria = wms_svc.get_item_avaria_por_seq(avaria_id_form, seq_int)
                if item_avaria:
                    valida = True
                    avaria_id = avaria_id_form
                    sequencia = seq_int
                    pasta_id_existente = item_avaria.get('pasta_id_drive')
                    # Criar ID fictício para que o Drive crie uma pasta com esse nome
                    id_etiqueta = f"RECUSADO-{avaria_id}-SEQ{sequencia}"
            except Exception as e:
                logger.error(f"Erro ao buscar item_avaria: {e}")

        if valida:
            if avaria_sankhya_service and avaria_id:
                logger.info(f"Buscando NFD/NF da avaria: {avaria_id}")
                try:
                    avaria = avaria_service.obter_avaria_detalhada(avaria_id)
                    if avaria and 'avaria' in avaria:
                        avaria_header = avaria['avaria']
                        nfd = avaria_header.nfd  if hasattr(avaria_header, 'nfd')  else None
                        nf  = avaria_header.nota if hasattr(avaria_header, 'nota') else None
                        logger.info(f"NFD: {nfd}, NF: {nf}")

                        if not pasta_id_existente and 'itens' in avaria:
                            for item in avaria['itens']:
                                if item.get('sequencia') == sequencia:
                                    pasta_id_existente = item.get('pasta_id_drive')
                                    break
                except Exception as e:
                    logger.error(f"Erro ao buscar avaria: {e}")

        if pasta_id_existente:
            logger.info(f"Pasta existente encontrada: {pasta_id_existente}")
        else:
            logger.info("Primeira vez enviando fotos para esta etiqueta")

        # ── 5. Salvar fotos temporariamente ───────────────────────────────
        logger.info(f"Recebidas {len(fotos)} fotos para etiqueta {id_etiqueta}")
        for foto in fotos:
            filename  = secure_filename(foto.filename)
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f'_{filename}')
            foto.save(temp_file.name)
            temp_files.append(temp_file.name)
            logger.info(f"Foto salva temporariamente: {filename}")

        # ── 6. Upload com estrutura hierárquica ───────────────────────────
        logger.info("Iniciando upload para Google Drive...")
        sucesso, link_pasta, mensagem, lista_fotos = drive_service.upload_fotos_produto(
            id_etiqueta=id_etiqueta,
            filepaths=temp_files,
            nfd=nfd,
            nf=nf
        )

        # ── 7. Salvar informações no Google Sheets ────────────────────────
        if sucesso and lista_fotos and wms_svc and valida:
            logger.info("Salvando informações das fotos no Sheets...")
            pasta_id       = drive_service.extract_folder_id_from_link(link_pasta)
            fotos_ids_json = json.dumps(lista_fotos)

            updates_dict = {
                'link_fotos_drive': link_pasta,
                'pasta_id_drive': pasta_id,
                'fotos_ids': fotos_ids_json
            }
            if not id_etiqueta.startswith('RECUSADO-'):
                updates_dict['id_etiqueta'] = id_etiqueta

            success_update, msg_update = wms_svc.update_item_avaria(
                avaria_id=avaria_id,
                sequencia=sequencia,
                **updates_dict
            )
            if success_update:
                logger.info(f"Sheets atualizado com pasta_id: {pasta_id}")
            else:
                logger.warning(f"Falha ao atualizar Sheets: {msg_update}")

# ── 7b. Invalidar cache WMS (link_fotos_drive gravado em Itens_Avarias) ──
        try:
            if wms_svc and hasattr(wms_svc, 'sheets') and hasattr(wms_svc.sheets, 'invalidate_wms_cache'):
                wms_svc.sheets.invalidate_wms_cache()
                logger.info("Cache WMS invalidado após upload de fotos")
        except Exception as e_cache:
            logger.warning(f"Falha ao invalidar cache WMS após upload fotos: {e_cache}")        

        # ── 8. Retornar resultado ─────────────────────────────────────────
        if sucesso:
            logger.info(f"Upload concluído: {link_pasta}")
            return jsonify({
                'success':     True,
                'message':     mensagem,
                'link_drive':  link_pasta,
                'id_etiqueta': id_etiqueta,
                'total_fotos': len(lista_fotos)
            })
        else:
            logger.error(f"Falha no upload: {mensagem}")
            return jsonify({'success': False, 'error': f'Erro no upload: {mensagem}'}), 500

    except Exception as e:
        logger.error(f"Erro ao upload de fotos: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': f'Erro ao processar upload: {str(e)}'}), 500

    finally:
        # ── 9. Limpar arquivos temporários ────────────────────────────────
        for temp_file in temp_files:
            try:
                if os.path.exists(temp_file):
                    os.unlink(temp_file)
                    logger.info(f"Arquivo temporário removido: {temp_file}")
            except Exception as e:
                logger.warning(f"Não foi possível remover arquivo temporário: {e}")


@avarias_bp.route('/api/produto/<id_etiqueta>/fotos', methods=['GET'])
@api_module_required('wms')
def listar_fotos_etiqueta_api(id_etiqueta):
    """
    Retorna link da pasta de fotos e contagem total.
    Não lista fotos individualmente — o usuário acessa a pasta diretamente.
    """
    try:
        logger.info(f"Buscando pasta de fotos da etiqueta: {id_etiqueta}")

        if not hasattr(current_app, 'drive_upload_service'):
            return jsonify({'success': False, 'error': 'Serviço indisponível'}), 503

        drive_service          = current_app.drive_upload_service
        etiqueta_service       = current_app.etiqueta_service
        wms_svc                = current_app.wms_service
        avaria_sankhya_service = current_app.avaria_sankhya_service

        if not etiqueta_service:
            return jsonify({'success': False, 'error': 'Serviço indisponível'}), 503

        valida, dados_etiqueta, msg = etiqueta_service.validar_etiqueta(id_etiqueta)
        if not valida:
            return jsonify({'success': False, 'error': f'Etiqueta inválida: {msg}'}), 404

        pasta_id_drive   = dados_etiqueta.get('pasta_id_drive')
        link_fotos_drive = dados_etiqueta.get('link_fotos_drive')

        if not pasta_id_drive and link_fotos_drive:
            pasta_id_drive = drive_service.extract_folder_id_from_link(link_fotos_drive)

        # Fallback via Avaria Service
        if not pasta_id_drive:
            logger.info("pasta_id não encontrado via etiqueta_service — tentando fallback")
            avaria_id_et = dados_etiqueta.get('avaria_id')
            sequencia_et = dados_etiqueta.get('sequencia')

            if avaria_sankhya_service and avaria_id_et and sequencia_et:
                try:
                    avaria = avaria_sankhya_service.obter_avaria_detalhada(avaria_id_et)
                    if avaria and 'itens' in avaria:
                        for item in avaria['itens']:
                            if item.get('sequencia') == sequencia_et:
                                pasta_id_drive   = item.get('pasta_id_drive')
                                link_fotos_drive = item.get('link_fotos_drive')
                                if pasta_id_drive:
                                    logger.info(f"pasta_id encontrado via fallback: {pasta_id_drive}")
                                break
                except Exception as e:
                    logger.error(f"Erro no fallback de pasta_id: {e}")

        if not pasta_id_drive:
            logger.info(f"Nenhuma pasta encontrada para {id_etiqueta}")
            return jsonify({
                'success':     True,
                'id_etiqueta': id_etiqueta,
                'total_fotos': 0,
                'link_pasta':  None,
                'tem_fotos':   False
            })

        sucesso, lista_fotos, mensagem = drive_service.listar_fotos_etiqueta(pasta_id_drive)

        if sucesso:
            logger.info(f"{len(lista_fotos)} foto(s) encontrada(s)")
            fotos_serializadas = [
                {
                    'file_id':       f.get('file_id', ''),
                    'filename':      f.get('filename', ''),
                    'thumbnail_url': f.get('thumbnailLink', ''),
                    'view_url':      f.get('webViewLink', ''),
                }
                for f in lista_fotos
            ]
            return jsonify({
                'success':     True,
                'id_etiqueta': id_etiqueta,
                'total_fotos': len(lista_fotos),
                'link_pasta':  link_fotos_drive,
                'tem_fotos':   len(lista_fotos) > 0,
                'fotos':       fotos_serializadas,
            })
        else:
            logger.error(f"Erro ao contar fotos: {mensagem}")
            return jsonify({'success': False, 'error': f'Erro ao acessar pasta: {mensagem}'}), 500

    except Exception as e:
        logger.error(f"Erro ao buscar fotos: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': f'Erro: {str(e)}'}), 500
