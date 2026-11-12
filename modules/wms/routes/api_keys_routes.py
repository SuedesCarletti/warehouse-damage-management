"""
modules/wms/routes/api_keys_routes.py
Gerenciamento de API Keys por empresa (Admin Empresa)

Endpoints UI:
  GET  /wms/api-keys/              → Tela de gerenciamento
  GET  /wms/api-keys/dashboard     → Dados do dashboard WMS (API interna)

Endpoints API:
  POST /wms/api-keys/api/gerar     → Gerar nova API Key
  POST /wms/api-keys/api/revogar   → Revogar key (ativo=nao)
  GET  /wms/api-keys/api/listar    → Listar keys da empresa (JSON)

Planilha: wms_api_keys
Colunas : api_key | empresa | nome_integracao | permissoes |
          criado_por | criado_em | expira_em | ativo | ultimo_uso
"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, render_template, request, session

from utils.decorators import module_required, set_theme

logger = logging.getLogger(__name__)

# Blueprint
api_keys_bp = Blueprint("wms_api_keys", __name__)

_sheets_service = None


def init_api_keys_routes(sheets_svc):
    """Chamado pelo app.py durante o bootstrap."""
    global _sheets_service
    _sheets_service = sheets_svc
    logger.info("Rotas de API Keys WMS inicializadas")


# Helpers internos
WORKSHEET = "wms_api_keys"
HEADERS   = [
    "api_key", "empresa", "nome_integracao", "permissoes",
    "criado_por", "criado_em", "expira_em", "ativo", "ultimo_uso"
]


def _get_ws():
    """Retorna (ou cria) a aba wms_api_keys."""
    try:
        return _sheets_service.sheet.worksheet(WORKSHEET)
    except Exception:
        ws = _sheets_service.sheet.add_worksheet(title=WORKSHEET, rows=500, cols=len(HEADERS))
        ws.append_row(HEADERS)
        logger.info(f"Aba '{WORKSHEET}' criada automaticamente")
        return ws


def _all_keys():
    """Retorna todas as linhas como lista de dicts."""
    try:
        return _get_ws().get_all_records()
    except Exception as e:
        logger.error(f"Erro ao ler api keys: {e}")
        return []


def _keys_for_empresa(empresa: str):
    """Filtra keys da empresa (sem expor o valor completo)."""
    rows = _all_keys()
    result = []
    for r in rows:
        if str(r.get("empresa", "")).strip() == str(empresa).strip():
            raw = str(r.get("api_key", ""))
            r["api_key_masked"] = f"{'*' * (len(raw) - 8)}{raw[-8:]}" if len(raw) > 8 else "****"
            r["api_key"] = ""
            result.append(r)
    return result


def _gerar_key(empresa: str, nome: str, criado_por: str) -> str:
    """Cria uma API Key segura, persiste no Sheets e retorna o valor claro."""
    token    = secrets.token_hex(32)
    ts       = datetime.now().isoformat()
    api_key  = hashlib.sha256(f"{empresa}:{nome}:{ts}:{token}".encode()).hexdigest()

    expira   = (datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d")
    agora    = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        ws = _get_ws()
        ws.append_row([
            api_key,
            empresa,
            nome,
            "read,write",
            criado_por,
            agora,
            expira,
            "sim",
            ""
        ])
        logger.info(f"API Key gerada para empresa={empresa}, integração='{nome}' por {criado_por}")
        return api_key
    except Exception as e:
        logger.error(f"Erro ao salvar API Key: {e}")
        raise


def _revogar_key(empresa: str, nome_integracao: str) -> bool:
    """Marca a key como ativo=nao (revogação)."""
    try:
        ws   = _get_ws()
        rows = ws.get_all_records()

        for idx, row in enumerate(rows, start=2):
            if (
                str(row.get("empresa", "")).strip() == str(empresa).strip()
                and row.get("nome_integracao", "").strip() == nome_integracao.strip()
                and row.get("ativo", "").lower() == "sim"
            ):
                col_ativo = HEADERS.index("ativo") + 1
                ws.update_cell(idx, col_ativo, "nao")
                logger.info(f"API Key revogada: empresa={empresa}, '{nome_integracao}'")
                return True

        return False
    except Exception as e:
        logger.error(f"Erro ao revogar key: {e}")
        return False


def _is_admin_empresa():
    """Verifica se o usuário tem nível admin_empresa ou superior."""
    nivel = session.get("user_level", "")
    return nivel in ("admin_empresa", "admin", "admin_global", "gestor")


def _user_empresa():
    return session.get("user_empresa", "")


# Rotas UI

@api_keys_bp.route("/wms/api-keys/")
@module_required("wms")
@set_theme("wms")
def gerenciar_api_keys():
    """Tela de gerenciamento de API Keys da empresa."""
    if not _is_admin_empresa():
        return render_template(
            "errors/403.html",
            mensagem="Apenas administradores de empresa podem gerenciar API Keys."
        ), 403

    empresa     = _user_empresa()
    keys        = _keys_for_empresa(empresa) if _sheets_service else []
    ativas      = sum(1 for k in keys if k.get("ativo", "").lower() == "sim")
    revogadas   = len(keys) - ativas

    return render_template(
        "wms/api_keys.html",
        empresa=empresa,
        keys=keys,
        total=len(keys),
        ativas=ativas,
        revogadas=revogadas,
        user_email=session.get("user_email", ""),
    )


@api_keys_bp.route("/wms/api-keys/dashboard-data")
@module_required("wms")
def dashboard_data():
    """
    Retorna dados resumidos para o dashboard WMS (JSON interno).
    Usado pelo template dashboard_wms.html via fetch.
    """
    from modules.wms.services.wms_service import WmsAvariaService  # import local para evitar ciclo

    empresa = _user_empresa()

    try:
        svc    = WmsAvariaService(_sheets_service)
        todas  = svc.get_all_avarias(empresa=empresa)

        pendentes  = [a for a in todas if getattr(a, "status_estoque", "") in ("Pendente", "pendente", "")]
        triadas    = [a for a in todas if getattr(a, "status_estoque", "") in ("Triado", "Triagem", "triado")]
        finalizadas= [a for a in todas if getattr(a, "status_estoque", "") in ("Finalizado", "Baixado", "finalizado")]

        # Valor total indenizações pagas
        valor_total = 0.0
        for a in todas:
            try:
                v = float(str(getattr(a, "valor_indenizacao", 0) or 0).replace(",", ".").replace("R$", "").strip())
                valor_total += v
            except Exception:
                pass

        # Últimas 5 avarias
        recentes = []
        for a in sorted(todas, key=lambda x: getattr(x, "data_registro", ""), reverse=True)[:5]:
            recentes.append({
                "id":               getattr(a, "id_avaria", ""),
                "nota":             getattr(a, "nota", ""),
                "transportadora":   getattr(a, "transportadora", ""),
                "status_estoque":   getattr(a, "status_estoque", ""),
                "data_registro":    getattr(a, "data_registro", ""),
            })

        return jsonify({
            "success":   True,
            "total":     len(todas),
            "pendentes": len(pendentes),
            "triadas":   len(triadas),
            "finalizadas": len(finalizadas),
            "valor_total": f"{valor_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            "recentes":  recentes,
        })

    except Exception as e:
        logger.error(f"dashboard_data error: {e}")
        return jsonify({"success": False, "total": 0, "pendentes": 0,
                        "triadas": 0, "finalizadas": 0, "valor_total": "0,00", "recentes": []})


# Rotas API (JSON)

@api_keys_bp.route("/wms/api-keys/api/listar")
@module_required("wms")
def api_listar():
    if not _is_admin_empresa():
        return jsonify({"success": False, "error": "Sem permissão"}), 403

    keys = _keys_for_empresa(_user_empresa())
    return jsonify({"success": True, "keys": keys, "total": len(keys)})


@api_keys_bp.route("/wms/api-keys/api/gerar", methods=["POST"])
@module_required("wms")
def api_gerar():
    """Gera nova API Key. Retorna o valor CLARO apenas uma vez."""
    if not _is_admin_empresa():
        return jsonify({"success": False, "error": "Sem permissão"}), 403

    if not _sheets_service:
        return jsonify({"success": False, "error": "Serviço indisponível"}), 500

    data  = request.get_json() or {}
    nome  = (data.get("nome_integracao") or "").strip()

    if not nome:
        return jsonify({"success": False, "error": "Informe o nome da integração"}), 400
    if len(nome) > 60:
        return jsonify({"success": False, "error": "Nome muito longo (máx 60 chars)"}), 400

    empresa = _user_empresa()

    # Impedir duplicata ativa com mesmo nome
    existentes = _keys_for_empresa(empresa)
    for k in existentes:
        if k.get("nome_integracao", "").strip() == nome and k.get("ativo", "").lower() == "sim":
            return jsonify({
                "success": False,
                "error": f"Já existe uma key ativa com o nome '{nome}'. Revogue-a primeiro."
            }), 409

    try:
        api_key = _gerar_key(
            empresa=empresa,
            nome=nome,
            criado_por=session.get("user_email", "desconhecido")
        )
        expira = (datetime.now() + timedelta(days=365)).strftime("%d/%m/%Y")

        return jsonify({
            "success":        True,
            "api_key":        api_key,           # ← exposto UMA VEZ
            "nome_integracao": nome,
            "expira_em":      expira,
            "message":        "Guarde esta chave agora. Ela não será exibida novamente."
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_keys_bp.route("/wms/api-keys/api/revogar", methods=["POST"])
@module_required("wms")
def api_revogar():
    """Revoga (desativa) uma API Key da empresa."""
    if not _is_admin_empresa():
        return jsonify({"success": False, "error": "Sem permissão"}), 403

    data = request.get_json() or {}
    nome = (data.get("nome_integracao") or "").strip()

    if not nome:
        return jsonify({"success": False, "error": "nome_integracao obrigatório"}), 400

    ok = _revogar_key(_user_empresa(), nome)

    if ok:
        return jsonify({"success": True, "message": f"Key '{nome}' revogada com sucesso."})
    return jsonify({"success": False, "error": "Key não encontrada ou já revogada."}), 404
