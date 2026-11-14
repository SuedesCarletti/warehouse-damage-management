"""
core/routes/admin_hierarchy_routes.py
Rotas dos 3 painéis administrativos hierárquicos.

Blueprints registrados:
  admin_global_bp  → /admin/global/...
  admin_empresa_bp → /admin/empresa/...
  admin_depto_bp   → /admin/depto/...
"""

import logging
import traceback
from functools import wraps
from werkzeug.security import generate_password_hash

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, session, flash, jsonify
)

from utils.decorators import (
    admin_global_required,
    admin_empresa_required,
    admin_depto_required,
    api_admin_global_required,
    api_admin_empresa_required,
    api_admin_depto_required,
    set_theme,
)

logger = logging.getLogger(__name__)

# DEFINIÇÃO DOS BLUEPRINTS

admin_entry_bp = Blueprint('admin_entry', __name__, url_prefix='/admin')
admin_global_bp = Blueprint('admin_global', __name__, url_prefix='/admin/global')
admin_empresa_bp = Blueprint('admin_empresa', __name__, url_prefix='/admin/empresa')
admin_depto_bp = Blueprint('admin_depto', __name__, url_prefix='/admin/depto')

# ─── Serviços injetados pelo app.py ─────────────────────────────────────────

sheets_service          = None
admin_hierarchy_service = None

from core.services.google_sheets_service import TELAS_PADRAO_POR_MODULO

NIVEIS_SISTEMA = ['admin_global', 'admin_empresa', 'admin_depto']

def init_admin_hierarchy_routes(sheets_svc, hierarchy_svc):
    global sheets_service, admin_hierarchy_service
    sheets_service          = sheets_svc
    admin_hierarchy_service = hierarchy_svc
    logger.info("Admin Hierarchy routes inicializadas")

def _current_admin():
    from core.models.user import User
    try:
        nivel = session.get('user_level', '')
        empresa = session.get('user_empresa', '')
        # admin_global can have an active context_empresa for navigation
        if nivel == 'admin_global' and session.get('context_empresa'):
            empresa = session['context_empresa']
        return User(
            nome        = session.get('user_name', ''),
            email       = session.get('user_email', ''),
            departamento= session.get('user_department', ''),
            nivel       = nivel,
            empresa     = empresa,
            admin_escopo= session.get('admin_escopo', None),
            ativo       = 'sim',
        )
    except Exception as e:
        logger.error(f"Erro ao reconstruir User da sessão: {e}")
        return None

# ROTAS ADMIN EMPRESA

@admin_empresa_bp.route('/api/salvar-permissoes', methods=['POST'])
@api_admin_empresa_required
def api_salvar_permissoes():
    try:
        data    = request.json or {}
        empresa = data.get('empresa', '').strip()
        perm    = data.get('permissoes', {})
        apenas_init = data.get('apenas_inicializar', False)

        if not empresa:
            return jsonify({'success': False, 'error': 'Empresa obrigatória'}), 400

        admin = _current_admin()
        if not admin.pode_ver_empresa(empresa):
            return jsonify({'success': False, 'error': 'Sem acesso a esta empresa'}), 403

        ok, msg = sheets_service.save_permissions_matrix(empresa, perm, apenas_inicializar=apenas_init)
        if ok:
            sheets_service.invalidate_permissions_cache()
            logger.info(f"[Permissões] {admin.email} salvou matrix de {empresa}")

        return jsonify({'success': ok, 'message' if ok else 'error': msg})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500

@admin_empresa_bp.route('/api/criar-combo-permissao', methods=['POST'])
@api_admin_empresa_required
def api_criar_combo_permissao():
    try:
        data   = request.json or {}
        empresa = data.get('empresa', '').strip()
        depto  = data.get('departamento', '').strip()
        cargo  = data.get('cargo', '').strip()

        if not all([empresa, depto, cargo]):
            return jsonify({'success': False, 'error': 'empresa, departamento e cargo obrigatórios'}), 400

        ok, msg = sheets_service.criar_combo_permissao(empresa, depto, cargo)
        return jsonify({'success': ok, 'message' if ok else 'error': msg})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500

@admin_empresa_bp.route('/api/remover-combo-permissao', methods=['POST'])
@api_admin_empresa_required
def api_remover_combo_permissao():
    try:
        data   = request.json or {}
        empresa = data.get('empresa', '').strip()
        depto  = data.get('departamento', '').strip()
        cargo  = data.get('cargo', '').strip()

        if not all([empresa, depto, cargo]):
            return jsonify({'success': False, 'error': 'empresa, departamento e cargo obrigatórios'}), 400

        ok, msg = sheets_service.remover_combo_permissao(empresa, depto, cargo)
        return jsonify({'success': ok, 'message' if ok else 'error': msg})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500

@admin_empresa_bp.route('/api/salvar-excecao-usuario', methods=['POST'])
@api_admin_empresa_required
def api_salvar_excecao_usuario():
    try:
        data    = request.json or {}
        empresa = data.get('empresa', '').strip()
        email   = data.get('email', '').strip()
        perms   = data.get('permissoes', {})

        if not empresa or not email:
            return jsonify({'success': False, 'error': 'empresa e email são obrigatórios'}), 400

        admin = _current_admin()
        if not admin.pode_ver_empresa(empresa):
            return jsonify({'success': False, 'error': 'Sem acesso a esta empresa'}), 403

        ok, msg = sheets_service.save_user_excecao(empresa, email, perms)
        if ok:
            sheets_service.invalidate_permissions_cache()
            logger.info(f"[Exceções] {admin.email} salvou exceção para {email} em {empresa}")

        return jsonify({'success': ok, 'message' if ok else 'error': msg})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_empresa_bp.route('/api/get-excecao-usuario', methods=['POST'])
@api_admin_empresa_required
def api_get_excecao_usuario():
    try:
        data    = request.json or {}
        empresa = data.get('empresa', '').strip()
        email   = data.get('email', '').strip()

        if not empresa or not email:
            return jsonify({'success': False, 'error': 'empresa e email são obrigatórios'}), 400

        excecoes = sheets_service.get_user_excecoes(empresa, email)
        return jsonify({'success': True, 'excecoes': excecoes})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_empresa_bp.route('/api/get-permissoes-efetivas-usuario', methods=['POST'])
@api_admin_empresa_required
def api_get_permissoes_efetivas_usuario():
    try:
        data    = request.json or {}
        empresa = data.get('empresa', '').strip()
        email   = data.get('email', '').strip()

        if not empresa or not email:
            return jsonify({'success': False, 'error': 'empresa e email são obrigatórios'}), 400

        admin = _current_admin()
        if not admin.pode_ver_empresa(empresa):
            return jsonify({'success': False, 'error': 'Sem acesso a esta empresa'}), 403

        users = admin_hierarchy_service.get_manageable_users(admin)
        target = next((u for u in users if u.email == email), None)
        if not target:
            return jsonify({'success': False, 'error': 'Usuário não encontrado'}), 404

        detail = sheets_service.get_user_permissions_detail(target)
        return jsonify({'success': True, 'detail': detail})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_global_bp.route('/api/salvar-excecao-usuario', methods=['POST'])
@api_admin_global_required
def api_global_salvar_excecao_usuario():
    try:
        data    = request.json or {}
        empresa = data.get('empresa', '').strip()
        email   = data.get('email', '').strip()
        perms   = data.get('permissoes', {})

        if not empresa or not email:
            return jsonify({'success': False, 'error': 'empresa e email são obrigatórios'}), 400

        ok, msg = sheets_service.save_user_excecao(empresa, email, perms)
        if ok:
            sheets_service.invalidate_permissions_cache()

        return jsonify({'success': ok, 'message' if ok else 'error': msg})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_global_bp.route('/api/get-excecao-usuario', methods=['POST'])
@api_admin_global_required
def api_global_get_excecao_usuario():
    try:
        data    = request.json or {}
        empresa = data.get('empresa', '').strip()
        email   = data.get('email', '').strip()

        if not empresa or not email:
            return jsonify({'success': False, 'error': 'empresa e email são obrigatórios'}), 400

        excecoes = sheets_service.get_user_excecoes(empresa, email)
        return jsonify({'success': True, 'excecoes': excecoes})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_global_bp.route('/api/get-permissoes-efetivas-usuario', methods=['POST'])
@api_admin_global_required
def api_global_get_permissoes_efetivas_usuario():
    try:
        data    = request.json or {}
        empresa = data.get('empresa', '').strip()
        email   = data.get('email', '').strip()

        if not empresa or not email:
            return jsonify({'success': False, 'error': 'empresa e email são obrigatórios'}), 400

        admin = _current_admin()
        users = admin_hierarchy_service.get_manageable_users(admin)
        target = next((u for u in users if u.email == email), None)
        if not target:
            return jsonify({'success': False, 'error': 'Usuário não encontrado'}), 404

        detail = sheets_service.get_user_permissions_detail(target)
        return jsonify({'success': True, 'detail': detail})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500

# BLUEPRINT: ADMIN GLOBAL  (/admin/global/...)

@admin_global_bp.route('/')
@admin_global_required
@set_theme('multisolution')
def global_panel():
    """Dashboard do Admin Global"""
    admin = _current_admin()
    stats = admin_hierarchy_service.get_dashboard_stats(admin)
    empresas_detalhes = {
        emp: sheets_service.company_permissions.get(emp, [])
        for emp in stats['empresas']
    }
    return render_template(
        'admin/global_panel.html',
        stats=stats,
        empresas_detalhes=empresas_detalhes,
    )

@admin_global_bp.route('/switch-empresa/<empresa>')
@admin_global_required
def switch_empresa(empresa):
    """Admin Global: alterna o contexto de empresa ativo."""
    if empresa == '_reset':
        session.pop('context_empresa', None)
        flash('Contexto global restaurado.', 'info')
        return redirect(url_for('admin_global.global_panel'))
    todas = list(sheets_service.company_permissions.keys())
    if empresa not in todas:
        flash(f'Empresa "{empresa}" não encontrada.', 'warning')
        return redirect(url_for('admin_global.global_panel'))
    session['context_empresa'] = empresa
    flash(f'Contexto alterado para {empresa}.', 'info')
    from flask import request as _req
    referrer = _req.referrer
    if referrer and '/admin/' in referrer:
        return redirect(referrer)
    return redirect(url_for('admin_global.global_panel'))


@admin_global_bp.route('/logar-como/<path:email>')
@admin_global_required
def logar_como(email):
    """Admin Global: assume temporariamente a sessão de outro usuário."""
    admin = _current_admin()
    users = admin_hierarchy_service.get_manageable_users(admin)
    target = next((u for u in users if u.email == email), None)
    if not target:
        flash(f'Usuário "{email}" não encontrado.', 'warning')
        return redirect(url_for('admin_global.global_usuarios'))

    session['_imp_backup'] = {
        'user_email':      session.get('user_email'),
        'user_name':       session.get('user_name'),
        'user_level':      session.get('user_level'),
        'user_empresa':    session.get('user_empresa'),
        'user_department': session.get('user_department'),
        'admin_escopo':    session.get('admin_escopo'),
        'permissions':     session.get('permissions'),
        'context_empresa': session.get('context_empresa'),
    }

    target.permissions = sheets_service.get_user_permissions(target)
    session['user_email']      = target.email
    session['user_name']       = target.nome
    session['user_level']      = target.nivel
    session['user_empresa']    = target.empresa
    session['user_department'] = target.departamento
    session['admin_escopo']    = getattr(target, 'admin_escopo', None)
    session['permissions']     = target.permissions
    session.pop('context_empresa', None)

    logger.info(f"[Impersonation] {session['_imp_backup']['user_email']} → {email}")
    flash(f'Você está navegando como {target.nome}. Use o banner amarelo para voltar.', 'info')
    return redirect(url_for('home.index'))


@admin_global_bp.route('/sair-impersonacao')
def sair_impersonacao():
    """Restaura a sessão do admin global após impersonação."""
    backup = session.pop('_imp_backup', None)
    if not backup:
        return redirect(url_for('home.index'))
    session['user_email']      = backup['user_email']
    session['user_name']       = backup['user_name']
    session['user_level']      = backup['user_level']
    session['user_empresa']    = backup['user_empresa']
    session['user_department'] = backup['user_department']
    session['admin_escopo']    = backup['admin_escopo']
    session['permissions']     = backup['permissions']
    session['context_empresa'] = backup.get('context_empresa')
    flash('Você voltou para sua conta de administrador global.', 'success')
    return redirect(url_for('admin_global.global_usuarios'))


@admin_global_bp.route('/usuarios')
@admin_global_required
@set_theme('multisolution')
def global_usuarios():
    """Lista todos os usuários do sistema"""
    admin = _current_admin()
    users = admin_hierarchy_service.get_manageable_users(admin)
    empresas = admin_hierarchy_service.get_manageable_empresas(admin)
    niveis   = admin_hierarchy_service.get_all_niveis_criáveis(admin)
    return render_template(
        'admin/global_usuarios.html',
        users=users,
        empresas=empresas,
        niveis=niveis,
    )


@admin_global_bp.route('/empresas')
@admin_global_required
@set_theme('multisolution')
def global_empresas():
    """Gerencia empresas e seus módulos"""
    admin  = _current_admin()
    todas  = admin_hierarchy_service.get_manageable_empresas(admin)
    detalhe = {
        emp: sheets_service.company_permissions.get(emp, [])
        for emp in todas
    }
    modulos_disponiveis = ['helpdesk', 'wms', 'portal']
    return render_template(
        'admin/global_empresas.html',
        empresas=detalhe,
        modulos_disponiveis=['helpdesk', 'wms', 'portal'],
    )

@admin_global_bp.route('/departamentos/<empresa>')
@admin_global_required
@set_theme('multisolution')
def global_departamentos(empresa):
    """Admin Global visualiza/gerencia departamentos de uma empresa específica"""
    admin  = _current_admin()
    deptos = admin_hierarchy_service.get_manageable_departamentos(admin, empresa)
    cargos = admin_hierarchy_service.get_cargos_empresa(empresa)

    # ← MUDANÇA: usa método dedicado (já filtra por empresa)
    by_depto = admin_hierarchy_service.get_usuarios_por_departamento(empresa)

    # ← MUDANÇA: passa todos_users filtrados pela empresa
    todos_users = [
        u for u in admin_hierarchy_service.get_manageable_users(admin)
        if u.empresa == empresa
    ]

    return render_template(
        'admin/empresa_departamentos.html',
        empresa=empresa,
        departamentos=deptos,
        users_by_depto=by_depto,
        todos_users=todos_users,
        cargos=cargos,
        back_url=url_for('admin_global.global_empresas'),
    )

@admin_empresa_bp.route('/niveis')
@admin_empresa_required
@set_theme('multisolution')
def empresa_niveis():
    """Gerencia permissões de telas por nível, cargo e departamento."""
    admin   = _current_admin()
    empresa = admin.escopo_empresa or admin.empresa

    if not empresa:
        flash('Empresa não definida para este administrador.', 'danger')
        return redirect(url_for('admin_empresa.empresa_panel'))

    modulos = admin_hierarchy_service.get_modulos_empresa(empresa)
    sheet_telas = sheets_service.get_telas_modulo(empresa)
    telas_por_modulo = {}
    for m in modulos:
        padrao = list(TELAS_PADRAO_POR_MODULO.get(m, []))
        for t in sheet_telas.get(m, []):
            if t not in padrao:
                padrao.append(t)
        telas_por_modulo[m] = padrao

    matrix_nivel    = sheets_service.get_permissions_matrix(empresa, tipo='nivel')
    matrix_depto    = sheets_service.get_permissions_matrix(empresa, tipo='departamento')
    matrix_cargo    = sheets_service.get_permissions_matrix(empresa, tipo='cargo')
    combos          = sheets_service.get_permissions_combos(empresa)
    departamentos   = admin_hierarchy_service.get_manageable_departamentos_raw(empresa)
    cargos          = sheets_service.get_cargos(empresa)
    telas_pendentes = sheets_service.get_telas_pendentes(empresa)

    usuarios_da_empresa = admin_hierarchy_service.get_manageable_users(admin)
    niveis_criáveis     = admin_hierarchy_service.get_all_niveis_criáveis(admin)

    return render_template(
        'admin/empresa_niveis.html',
        empresa=empresa,
        telas_por_modulo=telas_por_modulo,
        niveis_sistema=NIVEIS_SISTEMA,
        matrix_nivel=matrix_nivel,
        matrix_depto=matrix_depto,
        matrix_cargo=matrix_cargo,
        combos=combos,
        departamentos=departamentos,
        cargos=cargos,
        telas_pendentes=telas_pendentes,
        modulos_disponiveis=list(telas_por_modulo.keys()),
        back_url=url_for('admin_empresa.empresa_panel'),
        usuarios=usuarios_da_empresa,
        niveis_criáveis=niveis_criáveis,
    )

@admin_global_bp.route('/niveis/<empresa>')
@admin_global_required
@set_theme('multisolution')
def global_niveis(empresa):
    """Admin Global gerencia permissões de telas de qualquer empresa."""
    admin = _current_admin()

    modulos = admin_hierarchy_service.get_modulos_empresa(empresa)
    sheet_telas = sheets_service.get_telas_modulo(empresa)
    telas_por_modulo = {}
    for m in modulos:
        padrao = list(TELAS_PADRAO_POR_MODULO.get(m, []))
        for t in sheet_telas.get(m, []):
            if t not in padrao:
                padrao.append(t)
        telas_por_modulo[m] = padrao

    matrix_nivel    = sheets_service.get_permissions_matrix(empresa, tipo='nivel')
    matrix_depto    = sheets_service.get_permissions_matrix(empresa, tipo='departamento')
    matrix_cargo    = sheets_service.get_permissions_matrix(empresa, tipo='cargo')
    combos          = sheets_service.get_permissions_combos(empresa)
    departamentos   = admin_hierarchy_service.get_manageable_departamentos_raw(empresa)
    cargos          = sheets_service.get_cargos(empresa)
    telas_pendentes = sheets_service.get_telas_pendentes(empresa)

    todos_users         = admin_hierarchy_service.get_manageable_users(admin)
    usuarios_da_empresa = [u for u in todos_users if u.empresa == empresa]
    niveis_criáveis     = admin_hierarchy_service.get_all_niveis_criáveis(admin)

    return render_template(
        'admin/empresa_niveis.html',
        empresa=empresa,
        telas_por_modulo=telas_por_modulo,
        niveis_sistema=NIVEIS_SISTEMA,
        matrix_nivel=matrix_nivel,
        matrix_depto=matrix_depto,
        matrix_cargo=matrix_cargo,
        combos=combos,
        departamentos=departamentos,
        cargos=cargos,
        telas_pendentes=telas_pendentes,
        modulos_disponiveis=list(telas_por_modulo.keys()),
        back_url=url_for('admin_global.global_departamentos', empresa=empresa),
        usuarios=usuarios_da_empresa,
        niveis_criáveis=niveis_criáveis,
    )
  
# ─── API: Admin Global ───────────────────────────────────────────────────────

@admin_global_bp.route('/api/criar-usuario', methods=['POST'])
@api_admin_global_required
def api_global_criar_usuario():
    return _api_criar_usuario()


@admin_global_bp.route('/api/editar-usuario', methods=['POST'])
@api_admin_global_required
def api_global_editar_usuario():
    return _api_editar_usuario()


@admin_global_bp.route('/api/desativar-usuario', methods=['POST'])
@api_admin_global_required
def api_global_desativar_usuario():
    return _api_toggle_usuario(ativo=False)


@admin_global_bp.route('/api/reativar-usuario', methods=['POST'])
@api_admin_global_required
def api_global_reativar_usuario():
    return _api_toggle_usuario(ativo=True)


@admin_global_bp.route('/api/criar-empresa', methods=['POST'])
@api_admin_global_required
def api_criar_empresa():
    """Cria nova empresa"""
    try:
        data         = request.json or {}
        nome_empresa = data.get('nome_empresa', '').strip()
        modulos      = data.get('modulos', [])

        if not nome_empresa:
            return jsonify({'success': False, 'error': 'Nome da empresa é obrigatório'}), 400
        if not modulos:
            return jsonify({'success': False, 'error': 'Selecione ao menos um módulo'}), 400

        admin = _current_admin()
        ok, msg = admin_hierarchy_service.criar_empresa(admin, nome_empresa, modulos)
        return jsonify({'success': ok, 'message' if ok else 'error': msg})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_global_bp.route('/api/editar-empresa', methods=['POST'])
@api_admin_global_required
def api_editar_empresa():
    """Atualiza módulos de uma empresa"""
    try:
        data         = request.json or {}
        nome_empresa = data.get('nome_empresa', '').strip()
        modulos      = data.get('modulos', [])

        if not nome_empresa:
            return jsonify({'success': False, 'error': 'Nome da empresa é obrigatório'}), 400

        admin = _current_admin()
        ok, msg = admin_hierarchy_service.editar_modulos_empresa(admin, nome_empresa, modulos)
        return jsonify({'success': ok, 'message' if ok else 'error': msg})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_global_bp.route('/api/reset-senha', methods=['POST'])
@api_admin_global_required
def api_global_reset_senha():
    return _api_reset_senha()


@admin_global_bp.route('/api/recarregar-cache', methods=['POST'])
@api_admin_global_required
def api_recarregar_cache():
    """Força a limpeza de todos os caches (útil quando dados são alterados diretamente no Sheets)."""
    try:
        sheets_service.invalidate_users_cache()
        sheets_service.invalidate_permissions_cache()
        if hasattr(sheets_service, '_telas_cache'):
            sheets_service._telas_cache.clear()
        logger.info(f"[Admin] {session.get('user_email')} forçou recarga de cache")
        return jsonify({'success': True, 'message': 'Cache recarregado. Usuários precisam fazer novo login para ver as alterações.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# BLUEPRINT: ADMIN EMPRESA  (/admin/empresa/...)

@admin_empresa_bp.route('/')
@admin_empresa_required
@set_theme('multisolution')
def empresa_panel():
    """Dashboard do Admin Empresa"""
    admin   = _current_admin()
    stats   = admin_hierarchy_service.get_dashboard_stats(admin)
    empresa = admin.escopo_empresa
    modulos = admin_hierarchy_service.get_modulos_empresa(empresa) if empresa else []
    deptos  = admin_hierarchy_service.get_manageable_departamentos(admin, empresa) if empresa else []
    return render_template(
        'admin/empresa_panel.html',
        stats=stats,
        empresa=empresa,
        modulos=modulos,
        departamentos=deptos,
    )


@admin_empresa_bp.route('/usuarios')
@admin_empresa_required
@set_theme('multisolution')
def empresa_usuarios():
    admin   = _current_admin()
    empresa = admin.escopo_empresa or admin.empresa
    users   = admin_hierarchy_service.get_manageable_users(admin)
    niveis  = admin_hierarchy_service.get_all_niveis_criáveis(admin)
    deptos  = admin_hierarchy_service.get_manageable_departamentos(admin, empresa)

    # ← MUDANÇA: usa método com filtro por escopo
    cargos = admin_hierarchy_service.get_cargos_departamento(admin, empresa)

    return render_template(
        'admin/empresa_usuarios.html',
        users=users, niveis=niveis,
        departamentos=deptos, cargos=cargos,
        empresa=empresa, empresas=[empresa],
    )

@admin_empresa_bp.route('/departamentos')
@admin_empresa_required
@set_theme('multisolution')
def empresa_departamentos():
    """Visualiza e gerencia departamentos da empresa"""
    admin   = _current_admin()
    empresa = admin.escopo_empresa or admin.empresa
    deptos  = admin_hierarchy_service.get_manageable_departamentos(admin, empresa)
    cargos  = admin_hierarchy_service.get_cargos_empresa(empresa)

    # ← MUDANÇA: usa método dedicado (filtra por empresa, chave '' para sem depto)
    by_depto   = admin_hierarchy_service.get_usuarios_por_departamento(empresa)

    # ← MUDANÇA: passa todos_users para o modal de vincular usuário
    todos_users = admin_hierarchy_service.get_manageable_users(admin)

    return render_template(
        'admin/empresa_departamentos.html',
        empresa=empresa,
        departamentos=deptos,
        users_by_depto=by_depto,
        todos_users=todos_users,
        cargos=cargos,
    )

@admin_empresa_bp.route('/api/criar-departamento', methods=['POST'])
@api_admin_empresa_required
def api_criar_departamento():
    """Cria novo departamento"""
    try:
        data    = request.json or {}
        empresa = data.get('empresa', '').strip()
        nome    = data.get('nome', '').strip()
        desc    = data.get('descricao', '').strip()

        if not nome:
            return jsonify({'success': False, 'error': 'Nome do departamento é obrigatório'}), 400

        ok, msg = admin_hierarchy_service.criar_departamento(empresa, nome, desc)
        return jsonify({'success': ok, 'message' if ok else 'error': msg})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_empresa_bp.route('/api/editar-departamento', methods=['POST'])
@api_admin_empresa_required
def api_editar_departamento():
    """Renomeia departamento"""
    try:
        data         = request.json or {}
        empresa      = data.get('empresa', '').strip()
        nome_original = data.get('nome_original', '').strip()
        novo_nome    = data.get('novo_nome', '').strip()

        if not nome_original or not novo_nome:
            return jsonify({'success': False, 'error': 'Campos obrigatórios ausentes'}), 400

        ok, msg = admin_hierarchy_service.renomear_departamento(empresa, nome_original, novo_nome)
        return jsonify({'success': ok, 'message' if ok else 'error': msg})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_empresa_bp.route('/api/excluir-departamento', methods=['POST'])
@api_admin_empresa_required
def api_excluir_departamento():
    """Remove departamento (não remove usuários)"""
    try:
        data    = request.json or {}
        empresa = data.get('empresa', '').strip()
        nome    = data.get('nome', '').strip()

        if not nome:
            return jsonify({'success': False, 'error': 'Nome do departamento é obrigatório'}), 400

        ok, msg = admin_hierarchy_service.excluir_departamento(empresa, nome)
        return jsonify({'success': ok, 'message' if ok else 'error': msg})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_empresa_bp.route('/api/criar-cargo', methods=['POST'])
@api_admin_empresa_required
def api_criar_cargo():
    """Adiciona cargo à empresa"""
    try:
        data    = request.json or {}
        empresa = data.get('empresa', '').strip()
        cargo   = data.get('cargo', '').strip()

        if not cargo:
            return jsonify({'success': False, 'error': 'Nome do cargo é obrigatório'}), 400

        ok, msg = admin_hierarchy_service.criar_cargo(empresa, cargo)
        return jsonify({'success': ok, 'message' if ok else 'error': msg})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_empresa_bp.route('/api/excluir-cargo', methods=['POST'])
@api_admin_empresa_required
def api_excluir_cargo():
    """Remove cargo da empresa"""
    try:
        data    = request.json or {}
        empresa = data.get('empresa', '').strip()
        cargo   = data.get('cargo', '').strip()

        if not cargo:
            return jsonify({'success': False, 'error': 'Nome do cargo é obrigatório'}), 400

        ok, msg = admin_hierarchy_service.excluir_cargo(empresa, cargo)
        return jsonify({'success': ok, 'message' if ok else 'error': msg})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500

# ─── API: Admin Empresa ──────────────────────────────────────────────────────

@admin_empresa_bp.route('/api/criar-usuario', methods=['POST'])
@api_admin_empresa_required
def api_empresa_criar_usuario():
    return _api_criar_usuario()


@admin_empresa_bp.route('/api/editar-usuario', methods=['POST'])
@api_admin_empresa_required
def api_empresa_editar_usuario():
    return _api_editar_usuario()


@admin_empresa_bp.route('/api/desativar-usuario', methods=['POST'])
@api_admin_empresa_required
def api_empresa_desativar_usuario():
    return _api_toggle_usuario(ativo=False)


@admin_empresa_bp.route('/api/reativar-usuario', methods=['POST'])
@api_admin_empresa_required
def api_empresa_reativar_usuario():
    return _api_toggle_usuario(ativo=True)


@admin_empresa_bp.route('/api/reset-senha', methods=['POST'])
@api_admin_empresa_required
def api_empresa_reset_senha():
    return _api_reset_senha()


# BLUEPRINT: ADMIN DEPARTAMENTO  (/admin/depto/...)

@admin_depto_bp.route('/')
@admin_depto_required
@set_theme('multisolution')
def depto_panel():
    """Dashboard do Admin Departamento"""
    admin   = _current_admin()
    stats   = admin_hierarchy_service.get_dashboard_stats(admin)
    empresa = admin.escopo_empresa or admin.empresa
    depto   = admin.escopo_departamento or admin.departamento
    return render_template(
        'admin/depto_panel.html',
        stats=stats,
        empresa=empresa,
        departamento=depto,
    )


@admin_depto_bp.route('/usuarios')
@admin_depto_required
@set_theme('multisolution')
def depto_usuarios():
    admin   = _current_admin()
    empresa = admin.escopo_empresa or admin.empresa

    users  = admin_hierarchy_service.get_manageable_users(admin)
    niveis = admin_hierarchy_service.get_all_niveis_criáveis(admin)

    # ← MUDANÇA: cargos restritos ao escopo do depto
    cargos = admin_hierarchy_service.get_cargos_departamento(admin, empresa)
    deptos = admin_hierarchy_service.get_manageable_departamentos(admin, empresa)

    return render_template(
        'admin/depto_usuarios.html',
        users=users, niveis=niveis,
        cargos=cargos,
        departamentos=deptos,
        empresa=empresa,
        departamento=admin.escopo_departamento or admin.departamento,
    )

# ─── API: Admin Depto ────────────────────────────────────────────────────────

@admin_depto_bp.route('/api/criar-usuario', methods=['POST'])
@api_admin_depto_required
def api_depto_criar_usuario():
    return _api_criar_usuario()


@admin_depto_bp.route('/api/editar-usuario', methods=['POST'])
@api_admin_depto_required
def api_depto_editar_usuario():
    return _api_editar_usuario()


@admin_depto_bp.route('/api/desativar-usuario', methods=['POST'])
@api_admin_depto_required
def api_depto_desativar_usuario():
    return _api_toggle_usuario(ativo=False)


@admin_depto_bp.route('/api/reativar-usuario', methods=['POST'])
@api_admin_depto_required
def api_depto_reativar_usuario():
    return _api_toggle_usuario(ativo=True)


@admin_depto_bp.route('/api/reset-senha', methods=['POST'])
@api_admin_depto_required
def api_depto_reset_senha():
    return _api_reset_senha()

# ROTA DE ENTRADA INTELIGENTE  (/admin/)

@admin_entry_bp.route('/')
def admin_entry():
    """
    Redireciona para o painel correto baseado no nível do usuário.
    - admin_global   → /admin/global/
    - admin_empresa  → /admin/empresa/
    - admin_depto    → /admin/depto/
    - outros         → home
    """
    if 'user_email' not in session:
        return redirect(url_for('auth.login'))

    nivel = session.get('user_level', '')
    mapping = {
        'admin_global':  'admin_global.global_panel',
        'admin_empresa': 'admin_empresa.empresa_panel',
        'admin':         'admin_empresa.empresa_panel',   # backward-compat
        'admin_depto':   'admin_depto.depto_panel',
    }
    destino = mapping.get(nivel)
    if destino:
        return redirect(url_for(destino))

    flash('Você não possui acesso ao painel administrativo.', 'warning')
    return redirect(url_for('home.index'))


# FUNÇÕES COMPARTILHADAS (DRY entre os 3 blueprints)

def _api_criar_usuario():
    """Lógica comum de criação de usuário via API."""
    try:
        data = request.json or {}
        admin = _current_admin()

        required = ['nome', 'email', 'departamento', 'nivel', 'empresa', 'senha']
        missing  = [f for f in required if not data.get(f)]
        if missing:
            return jsonify({'success': False, 'error': f"Campos obrigatórios: {', '.join(missing)}"}), 400

        if len(data['senha']) < 8:
            return jsonify({'success': False, 'error': 'Senha deve ter ao menos 8 caracteres'}), 400

        data['senha'] = generate_password_hash(data['senha'], method='pbkdf2:sha256')

        # ── Monta admin_escopo ─────────────────────────────────────────────
        nivel = data['nivel']
        if nivel == 'admin_global':
            data['admin_escopo'] = '*'

        elif nivel in ('admin_empresa', 'admin'):
            data['admin_escopo'] = data['empresa']

        elif nivel == 'admin_depto':
            # Aceita lista de departamentos: o front pode enviar
            # departamento="TI" (string) ou departamentos=["TI","RH"] (lista)
            deptos_raw = data.get('departamentos') or data.get('departamento')
            if isinstance(deptos_raw, list):
                deptos_str = ','.join(d.strip() for d in deptos_raw if d.strip())
            else:
                deptos_str = str(deptos_raw).strip()
            data['admin_escopo'] = f"{data['empresa']}:{deptos_str}"
            # Normaliza campo departamento para o principal (1º da lista)
            data['departamento'] = deptos_str.split(',')[0].strip()
        else:
            data['admin_escopo'] = ''

        ok, msg = admin_hierarchy_service.criar_usuario(admin, data)
        if ok:
            sheets_service.invalidate_users_cache()
        return jsonify({'success': ok, 'message' if ok else 'error': msg})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500

def _api_editar_usuario():
    """Lógica comum de edição de usuário via API."""
    try:
        data  = request.json or {}
        email = data.pop('email', None)
        admin = _current_admin()

        if not email:
            return jsonify({'success': False, 'error': 'Email é obrigatório'}), 400

        novo_nivel = data.get('nivel')
        if novo_nivel:
            empresa      = data.get('empresa', admin.empresa)
            departamento = data.get('departamento', admin.departamento)
            deptos_raw   = data.get('departamentos') or departamento

            if novo_nivel == 'admin_global':
                data['admin_escopo'] = '*'
            elif novo_nivel in ('admin_empresa', 'admin'):
                data['admin_escopo'] = empresa
            elif novo_nivel == 'admin_depto':
                if isinstance(deptos_raw, list):
                    deptos_str = ','.join(d.strip() for d in deptos_raw if d.strip())
                else:
                    deptos_str = str(deptos_raw).strip()
                data['admin_escopo'] = f"{empresa}:{deptos_str}"
                data['departamento'] = deptos_str.split(',')[0].strip()
            else:
                data['admin_escopo'] = ''

        ok, msg = admin_hierarchy_service.editar_usuario(admin, email, data)
        if ok:
            sheets_service.invalidate_users_cache()
        return jsonify({'success': ok, 'message' if ok else 'error': msg})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500

def _api_toggle_usuario(ativo: bool):
    """Ativa ou desativa um usuário."""
    try:
        data  = request.json or {}
        email = data.get('email')
        admin = _current_admin()

        if not email:
            return jsonify({'success': False, 'error': 'Email é obrigatório'}), 400

        if ativo:
            ok, msg = admin_hierarchy_service.reativar_usuario(admin, email)
        else:
            ok, msg = admin_hierarchy_service.desativar_usuario(admin, email)

        if ok:
            sheets_service.invalidate_users_cache()
        return jsonify({'success': ok, 'message' if ok else 'error': msg})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


def _api_reset_senha():
    """Reset de senha — disponível para todos os admins."""
    try:
        data          = request.json or {}
        email         = data.get('email')
        nova_senha    = data.get('nova_senha')
        admin         = _current_admin()

        if not email or not nova_senha:
            return jsonify({'success': False, 'error': 'Email e nova senha são obrigatórios'}), 400
        if len(nova_senha) < 8:
            return jsonify({'success': False, 'error': 'Senha deve ter ao menos 8 caracteres'}), 400

        # Valida que o admin pode gerenciar este usuário
        from core.models.user import User
        all_users = sheets_service.get_all_users()
        target = next((u for u in all_users if u.email.lower() == email.lower()), None)
        if not target:
            return jsonify({'success': False, 'error': 'Usuário não encontrado'}), 404
        if not admin_hierarchy_service.can_manage_user(admin, target):
            return jsonify({'success': False, 'error': 'Sem permissão para redefinir senha deste usuário'}), 403

        hashed = generate_password_hash(nova_senha, method='pbkdf2:sha256')
        ok, msg = sheets_service.update_user(email, senha=hashed)
        if ok:
            logger.info(f"[AdminHierarchy] Senha de {email} redefinida por {admin.email}")
            sheets_service.invalidate_users_cache()
        return jsonify({'success': ok, 'message' if ok else 'error': msg})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500



@admin_empresa_bp.route('/cargos')
@admin_empresa_required
@set_theme('multisolution')
def empresa_cargos():
    """Tela de gerenciamento de cargos da empresa."""
    admin   = _current_admin()
    empresa = admin.escopo_empresa or admin.empresa
    cargos  = admin_hierarchy_service.get_cargos_empresa(empresa)
    users   = admin_hierarchy_service.get_manageable_users(admin)
    deptos  = admin_hierarchy_service.get_manageable_departamentos(admin, empresa)
    by_cargo = admin_hierarchy_service.get_usuarios_por_cargo(empresa)
    return render_template(
        'admin/empresa_cargos.html',
        empresa=empresa,
        cargos=cargos,
        users_by_cargo=by_cargo,
        todos_users=users,
        departamentos=deptos,
    )


@admin_empresa_bp.route('/api/mover-usuario-cargo', methods=['POST'])
@api_admin_empresa_required
def api_mover_usuario_cargo():
    """Atualiza o cargo de um usuário."""
    try:
        data      = request.json or {}
        email     = data.get('email', '').strip()
        novo_cargo = data.get('cargo', '').strip()

        if not email:
            return jsonify({'success': False, 'error': 'email é obrigatório'}), 400

        admin = _current_admin()
        ok, msg = admin_hierarchy_service.mover_usuario_cargo(admin, email, novo_cargo)
        if ok:
            sheets_service.invalidate_users_cache()
        return jsonify({'success': ok, 'message' if ok else 'error': msg})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_empresa_bp.route('/api/criar-perfil-com-permissoes', methods=['POST'])
@api_admin_empresa_required
def api_criar_perfil_com_permissoes():
    """Cria um perfil (combo / cargo / departamento) e salva suas permissões."""
    try:
        data    = request.json or {}
        empresa = data.get('empresa', '').strip()
        tipo    = data.get('tipo', '').strip().lower()
        depto   = data.get('departamento', '').strip()
        cargo   = data.get('cargo', '').strip()
        perms   = data.get('permissoes', {})

        if not empresa:
            return jsonify({'success': False, 'error': 'empresa é obrigatória'}), 400
        if tipo not in ('combo', 'cargo', 'departamento'):
            return jsonify({'success': False, 'error': 'tipo deve ser combo, cargo ou departamento'}), 400
        if tipo in ('combo', 'departamento') and not depto:
            return jsonify({'success': False, 'error': 'departamento é obrigatório para este tipo'}), 400
        if tipo in ('combo', 'cargo') and not cargo:
            return jsonify({'success': False, 'error': 'cargo é obrigatório para este tipo'}), 400

        admin = _current_admin()
        if not admin.pode_ver_empresa(empresa):
            return jsonify({'success': False, 'error': 'Sem acesso a esta empresa'}), 403

        criado = False
        if tipo == 'combo':
            ok, msg = sheets_service.criar_combo_permissao(empresa, depto, cargo)
            if not ok and 'já existe' not in msg.lower():
                return jsonify({'success': False, 'error': msg}), 400
            criado = True
            perfil_key = f'{depto}::{cargo}'
        elif tipo == 'cargo':
            matrix_atual = sheets_service.get_permissions_matrix(empresa, tipo='cargo')
            if cargo not in matrix_atual:
                criado = True
            perfil_key = cargo
        else:
            matrix_atual = sheets_service.get_permissions_matrix(empresa, tipo='departamento')
            if depto not in matrix_atual:
                criado = True
            perfil_key = depto

        perm_payload = {
            tipo: {
                perfil_key: {
                    modulo: {tela: marcado for tela, marcado in telas.items() if isinstance(marcado, bool)}
                    for modulo, telas in perms.items() if isinstance(telas, dict)
                }
            }
        }

        ok, msg = sheets_service.save_permissions_matrix(empresa, perm_payload, apenas_inicializar=False)
        if not ok:
            return jsonify({'success': False, 'error': msg}), 500

        sheets_service.invalidate_permissions_cache()
        logger.info(f"[Perfil+Perm] {admin.email} criou tipo={tipo} perfil={perfil_key} em {empresa}")

        return jsonify({'success': True, 'criado': criado, 'tipo': tipo, 'perfil': perfil_key, 'message': msg})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_empresa_bp.route('/api/mover-usuario-departamento', methods=['POST'])
@api_admin_empresa_required
def api_mover_usuario_departamento():
    """Move um usuário para outro departamento."""
    try:
        data              = request.json or {}
        email             = data.get('email', '').strip()
        novo_departamento = data.get('departamento', '').strip()

        if not email or not novo_departamento:
            return jsonify({'success': False, 'error': 'email e departamento são obrigatórios'}), 400

        admin = _current_admin()
        ok, msg = admin_hierarchy_service.mover_usuario_departamento(admin, email, novo_departamento)
        if ok:
            sheets_service.invalidate_users_cache()
        return jsonify({'success': ok, 'message' if ok else 'error': msg})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_global_bp.route('/cargos/<empresa>')
@admin_global_required
@set_theme('multisolution')
def global_cargos(empresa):
    """Admin Global gerencia cargos de qualquer empresa."""
    admin  = _current_admin()
    cargos = admin_hierarchy_service.get_cargos_empresa(empresa)
    users  = admin_hierarchy_service.get_manageable_users(admin)
    deptos = admin_hierarchy_service.get_manageable_departamentos(admin, empresa)
    by_cargo = admin_hierarchy_service.get_usuarios_por_cargo(empresa)
    todos_users = [u for u in users if u.empresa == empresa]
    return render_template(
        'admin/empresa_cargos.html',
        empresa=empresa,
        cargos=cargos,
        users_by_cargo=by_cargo,
        todos_users=todos_users,
        departamentos=deptos,
        back_url=url_for('admin_global.global_departamentos', empresa=empresa),
    )


@admin_global_bp.route('/api/mover-usuario-cargo', methods=['POST'])
@api_admin_global_required
def api_global_mover_usuario_cargo():
    """Admin Global: atualiza cargo de um usuário."""
    try:
        data      = request.json or {}
        email     = data.get('email', '').strip()
        novo_cargo = data.get('cargo', '').strip()

        if not email:
            return jsonify({'success': False, 'error': 'email é obrigatório'}), 400

        admin = _current_admin()
        ok, msg = admin_hierarchy_service.mover_usuario_cargo(admin, email, novo_cargo)
        if ok:
            sheets_service.invalidate_users_cache()
        return jsonify({'success': ok, 'message' if ok else 'error': msg})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_global_bp.route('/api/mover-usuario-departamento', methods=['POST'])
@api_admin_global_required
def api_global_mover_usuario_departamento():
    """Admin Global: move usuário para outro departamento."""
    try:
        data              = request.json or {}
        email             = data.get('email', '').strip()
        novo_departamento = data.get('departamento', '').strip()

        if not email or not novo_departamento:
            return jsonify({'success': False, 'error': 'email e departamento são obrigatórios'}), 400

        admin = _current_admin()
        ok, msg = admin_hierarchy_service.mover_usuario_departamento(admin, email, novo_departamento)
        if ok:
            sheets_service.invalidate_users_cache()
        return jsonify({'success': ok, 'message' if ok else 'error': msg})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


