"""
api/v1/asn_api.py
REST API para o Portal ASN — integração com TMS de transportadoras.

Autenticação: header X-API-Key
  A key é validada na aba wms_api_keys do Google Sheets.
  Para uso com ASN, a key deve pertencer à transportadora.
  Os campos 'transportadora' e 'email_transportadora' podem ser adicionados
  à aba wms_api_keys; se ausentes, serão inferidos de 'nome_integracao' e 'empresa'.

Endpoints:
  GET  /api/v1/asn               → Lista ASNs da transportadora autenticada
  POST /api/v1/asn               → Cria novo ASN
  GET  /api/v1/asn/<id>          → Busca ASN por ID
  POST /api/v1/asn/<id>/cancelar → Cancela um ASN
  GET  /api/v1/asn/slots         → Consulta horários ocupados numa data

Exemplo de payload (POST /api/v1/asn):
  {
    "nota": "123456",
    "tipo_nota": "NF",
    "empresa_dest": "Matriz",
    "cte": "789",
    "volumes": "5",
    "data_prevista": "2024-12-25 14:00",
    "nome_motorista": "João Silva",
    "placa_veiculo": "ABC-1234",
    "descricao_avaria": "Produto avariado durante transporte"
  }
"""

import logging
import re
from datetime import datetime

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

asn_api_bp = Blueprint("asn_rest_v1", __name__, url_prefix="/api/v1/asn")
asn_api_bp.strict_slashes = False

_asn_service = None
_sheets_service = None


def init_asn_api_routes(asn_svc, sheets_svc):
    global _asn_service, _sheets_service
    _asn_service = asn_svc
    _sheets_service = sheets_svc


def _get_sheets():
    """Retorna sheets_service disponível — usa fallback para avarias_api se necessário."""
    if _sheets_service is not None:
        return _sheets_service
    try:
        import api.v1.avarias_api as _avarias
        svc = getattr(_avarias, "sheets_service", None)
        if svc:
            logger.warning("[asn_api] usando sheets_service de avarias_api como fallback")
        return svc
    except Exception:
        return None
    logger.info("ASN API v1 inicializada")


# Anti-cache

@asn_api_bp.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# Validação de API Key

def _validate_api_key(api_key: str):
    """
    Valida API Key contra a aba wms_api_keys.
    Retorna (True, key_info) ou (False, None).

    key_info é um dict com:
      empresa, nome_integracao, transportadora, email_transportadora
    """
    sheets = _get_sheets()
    if not sheets:
        logger.error("[asn_api] _validate_api_key: nenhum sheets_service disponível")
        return False, None

    try:
        ws = sheets.sheet.worksheet("wms_api_keys")
        rows = ws.get_all_records()
        logger.info(f"[asn_api] validando key — {len(rows)} registro(s) na aba wms_api_keys")

        for row in rows:
            row_key = str(row.get("api_key", "")).strip()
            row_ativo = str(row.get("ativo", "")).strip().lower()
            if row_key == api_key.strip():
                if row_ativo != "sim":
                    logger.warning(f"[asn_api] key encontrada mas ativo='{row_ativo}'")
                    return False, None
            if (
                row_key == api_key.strip()
                and row_ativo == "sim"
            ):
                expira = str(row.get("expira_em", "")).strip()
                if expira:
                    try:
                        for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
                            try:
                                exp_date = datetime.strptime(expira, fmt)
                                if datetime.now() > exp_date:
                                    logger.warning("[asn_api] API Key expirada")
                                    return False, None
                                break
                            except ValueError:
                                continue
                    except Exception:
                        pass

                empresa = str(row.get("empresa", "")).strip()
                nome_integracao = str(row.get("nome_integracao", "")).strip()

                # Suporta colunas opcionais transportadora / email_transportadora
                transportadora = str(row.get("transportadora", "")).strip() or nome_integracao
                email_raw = str(row.get("email_transportadora", "")).strip()
                if not email_raw:
                    slug = re.sub(r"[^a-z0-9]", "_", nome_integracao.lower())
                    email_raw = f"api_{slug}@{empresa.lower()}.tms"

                # Registra último uso (best-effort)
                try:
                    headers = ws.row_values(1)
                    if "ultimo_uso" in headers:
                        col_uso = headers.index("ultimo_uso") + 1
                        for idx, r in enumerate(rows, start=2):
                            if str(r.get("api_key", "")).strip() == api_key.strip():
                                ws.update_cell(idx, col_uso, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                                break
                except Exception:
                    pass

                key_info = {
                    "empresa": empresa,
                    "nome_integracao": nome_integracao,
                    "transportadora": transportadora,
                    "email_transportadora": email_raw,
                }
                logger.info(
                    f"[asn_api] API Key válida — transportadora={transportadora}, empresa={empresa}"
                )
                return True, key_info

        logger.warning("[asn_api] API Key não encontrada ou inativa")
        return False, None

    except Exception as e:
        logger.error(f"[asn_api] Erro ao validar API Key: {e}")
        return False, None


def _require_api_key():
    """
    Extrai e valida X-API-Key do header.
    Retorna (key_info, None) se válida, ou (None, response_tuple) se inválida.
    """
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        return None, (jsonify({"error": "Header X-API-Key obrigatório"}), 401)

    valid, key_info = _validate_api_key(api_key)
    if not valid:
        return None, (jsonify({"error": "API Key inválida ou expirada"}), 401)

    return key_info, None


# Constantes de agendamento (espelham asn_service.py)

_DIAS_VALIDOS = {1: "Terça-feira", 2: "Quarta-feira", 3: "Quinta-feira"}
_NOMES_DIA = {
    0: "Segunda-feira", 1: "Terça-feira", 2: "Quarta-feira",
    3: "Quinta-feira",  4: "Sexta-feira", 5: "Sábado",       6: "Domingo",
}
_SLOTS_VALIDOS = {"14:00", "14:40", "15:20", "16:00", "16:40"}


# Helpers de validação de negócio

def _validar_empresa_dest(empresa_dest: str):
    """
    Verifica se a empresa destinatária existe e tem o módulo WMS ativo.
    Retorna dict de erro (422) ou None se OK.
    """
    sheets = _get_sheets()
    if not sheets:
        return None  # sem sheets, deixa o serviço tratar
    try:
        perms = getattr(sheets, "company_permissions", None) or {}
        if not perms:
            return None  # cache vazio, pula validação
        empresas_wms = set()
        for nome, mods in perms.items():
            mods_list = mods if isinstance(mods, (list, set)) else [m.strip() for m in str(mods).split(",")]
            if "wms" in mods_list:
                empresas_wms.add(nome)
        if empresas_wms and empresa_dest not in empresas_wms:
            return {
                "error": "empresa_dest inválida",
                "message": f"A empresa '{empresa_dest}' não foi encontrada ou não possui o módulo WMS ativo.",
                "empresas_disponiveis": sorted(empresas_wms),
            }
    except Exception as exc:
        logger.warning(f"[asn_api] _validar_empresa_dest: {exc}")
    return None


def _validar_agendamento(data_prevista_str: str, empresa_dest: str = ""):
    """
    Valida todas as regras de agendamento:
      1. Não retroativa
      2. Dia da semana: Ter–Qui
      3. Slot de horário válido
      4. Slot disponível (não ocupado)
    Retorna dict de erro ou None se OK.
    """
    try:
        dt = datetime.strptime(data_prevista_str[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return {
            "error": "data_prevista inválida",
            "message": "Use o formato YYYY-MM-DD HH:MM (ex: 2025-06-10 14:00).",
        }

    if dt <= datetime.now():
        return {
            "error": "data_prevista inválida",
            "message": "Não é possível agendar em data/hora retroativa ou igual ao momento atual.",
        }

    if dt.weekday() not in _DIAS_VALIDOS:
        return {
            "error": "data_prevista inválida",
            "message": (
                f"{_NOMES_DIA.get(dt.weekday())} não é um dia de recebimento. "
                f"Agende para: {', '.join(_DIAS_VALIDOS.values())}."
            ),
            "dias_disponiveis": list(_DIAS_VALIDOS.values()),
        }

    horario = dt.strftime("%H:%M")
    if horario not in _SLOTS_VALIDOS:
        return {
            "error": "horário inválido",
            "message": (
                f"'{horario}' não é um slot de recebimento válido. "
                f"Horários disponíveis: {', '.join(sorted(_SLOTS_VALIDOS))}."
            ),
            "slots_validos": sorted(_SLOTS_VALIDOS),
        }

    if _asn_service:
        try:
            ocupados = _asn_service.get_slots_ocupados(dt.strftime("%Y-%m-%d"), empresa_dest)
            if horario in ocupados:
                livres = sorted(_SLOTS_VALIDOS - set(ocupados))
                return {
                    "error": "slot indisponível",
                    "message": (
                        f"Horário {horario} já está reservado para {dt.strftime('%d/%m/%Y')}. "
                        f"Use GET /api/v1/asn/slots?data={dt.strftime('%Y-%m-%d')}"
                        f"&empresa={empresa_dest} para consultar disponibilidade."
                    ),
                    "slots_livres": livres,
                }
        except Exception as exc:
            logger.warning(f"[asn_api] _validar_agendamento slots: {exc}")

    return None


# GET /api/v1/asn/health — Diagnóstico

@asn_api_bp.route("/health", methods=["GET"])
def health():
    """GET /api/v1/asn/health — sem autenticação, mostra estado dos serviços."""
    sheets = _get_sheets()
    keys_count = 0
    sheets_ok = False
    if sheets:
        try:
            ws = sheets.sheet.worksheet("wms_api_keys")
            keys_count = len(ws.get_all_records())
            sheets_ok = True
        except Exception as e:
            logger.error(f"[asn_api] health — erro ao ler wms_api_keys: {e}")

    return jsonify({
        "status": "ok" if (sheets_ok and _asn_service) else "degraded",
        "asn_service": _asn_service is not None,
        "sheets_service": sheets is not None,
        "sheets_connected": sheets_ok,
        "api_keys_registered": keys_count,
    }), 200


# GET /api/v1/asn — Lista ASNs da transportadora

@asn_api_bp.route("", methods=["GET"])
def listar_asns():
    """
    Lista ASNs da transportadora autenticada.

    Query params opcionais:
      status  — filtra por status (agendado | processado | recebido | cancelado | recusado)
      limit   — máximo de registros (default: 100)

    Resposta:
      {
        "success": true,
        "total": 3,
        "asns": [ { ...campos do ASN... } ]
      }
    """
    key_info, err = _require_api_key()
    if err:
        return err

    if not _asn_service:
        return jsonify({"error": "Serviço ASN indisponível"}), 503

    status = request.args.get("status", "").strip()
    limit = int(request.args.get("limit", 100))

    try:
        email = key_info["email_transportadora"]
        asns = _asn_service.get_by_transportadora(email)

        if status:
            asns = [a for a in asns if a.status == status]

        asns = asns[:limit]

        return jsonify({
            "success": True,
            "total": len(asns),
            "transportadora": key_info["transportadora"],
            "asns": [a.to_dict() for a in asns],
        }), 200

    except Exception as e:
        logger.error(f"[asn_api] listar_asns: {e}")
        return jsonify({"error": "Erro ao listar ASNs", "message": str(e)}), 500


# POST /api/v1/asn — Cria novo ASN

@asn_api_bp.route("", methods=["POST"])
def criar_asn():
    """
    Cria um novo ASN (Advanced Shipping Notice).

    Body JSON:
      Obrigatórios:
        nota          — número da NF ou NFD
        empresa_dest  — empresa destinatária (ex: "Matriz")
        data_prevista — data/hora prevista de entrega (YYYY-MM-DD HH:MM)

      Opcionais:
        tipo_nota        — "NF" | "NFD" (default: "NF")
        cte              — número do CT-e
        volumes          — quantidade de volumes
        nome_motorista   — nome do motorista
        placa_veiculo    — placa do veículo
        descricao_avaria — descrição prévia do dano/motivo

    Resposta 201:
      {
        "success": true,
        "asn_id": "ASN-20241225-0001",
        "message": "ASN criado com sucesso"
      }
    """
    key_info, err = _require_api_key()
    if err:
        return err

    if not _asn_service:
        return jsonify({"error": "Serviço ASN indisponível"}), 503

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Body JSON obrigatório"}), 400

    # ── 1. Campos obrigatórios ──────────────────────────────────────────────
    obrigatorios = ["nota", "empresa_dest", "data_prevista"]
    faltando = [f for f in obrigatorios if not str(data.get(f, "")).strip()]
    if faltando:
        return jsonify({
            "error": "Campos obrigatórios ausentes",
            "missing_fields": faltando,
            "dica": "Consulte /api/v1/asn/docs para o formato completo.",
        }), 400

    empresa_dest = str(data["empresa_dest"]).strip()
    data_prevista = str(data["data_prevista"]).strip()

    # ── 2. Normaliza separador ISO (T → espaço) ─────────────────────────────
    data_prevista = data_prevista.replace("T", " ")

    # ── 3. Valida empresa destinatária ──────────────────────────────────────
    erro_empresa = _validar_empresa_dest(empresa_dest)
    if erro_empresa:
        return jsonify(erro_empresa), 422

    # ── 4. Valida regras de agendamento ─────────────────────────────────────
    erro_agenda = _validar_agendamento(data_prevista, empresa_dest)
    if erro_agenda:
        status_code = 409 if erro_agenda.get("error") == "slot indisponível" else 422
        return jsonify(erro_agenda), status_code

    try:
        success, msg, asn = _asn_service.criar_asn(
            data={**data, "data_prevista": data_prevista},
            user_email=key_info["email_transportadora"],
            transportadora_nome=key_info["transportadora"],
            empresa_dest=empresa_dest,
        )

        if success and asn:
            logger.info(
                f"[asn_api] ASN criado via API: {asn.id} "
                f"(transportadora={key_info['transportadora']})"
            )
            return jsonify({
                "success": True,
                "asn_id": asn.id,
                "status": asn.status,
                "message": msg,
            }), 201

        return jsonify({"error": msg}), 400

    except Exception as e:
        logger.error(f"[asn_api] criar_asn: {e}")
        return jsonify({"error": "Erro ao criar ASN", "message": str(e)}), 500


# GET /api/v1/asn/<id> — Busca ASN por ID

@asn_api_bp.route("/<asn_id>", methods=["GET"])
def obter_asn(asn_id):
    """
    Retorna os dados de um ASN específico.
    A transportadora só pode consultar seus próprios ASNs.

    Resposta 200:
      { ...campos do ASN... }

    Resposta 404:
      { "error": "ASN não encontrado" }
    """
    key_info, err = _require_api_key()
    if err:
        return err

    if not _asn_service:
        return jsonify({"error": "Serviço ASN indisponível"}), 503

    try:
        asn = _asn_service.get_by_id(asn_id)
        if not asn:
            return jsonify({"error": "ASN não encontrado"}), 404

        if asn.email_transportadora.lower() != key_info["email_transportadora"].lower():
            return jsonify({"error": "ASN não encontrado"}), 404

        return jsonify(asn.to_dict()), 200

    except Exception as e:
        logger.error(f"[asn_api] obter_asn {asn_id}: {e}")
        return jsonify({"error": "Erro ao buscar ASN", "message": str(e)}), 500


# POST /api/v1/asn/<id>/cancelar — Cancela um ASN

@asn_api_bp.route("/<asn_id>/cancelar", methods=["POST"])
def cancelar_asn(asn_id):
    """
    Cancela um ASN agendado.
    Só é possível cancelar ASNs com status 'agendado'.

    Body JSON (opcional):
      { "motivo": "Carga não saiu do CD de origem" }

    Resposta 200:
      { "success": true, "message": "ASN cancelado com sucesso" }
    """
    key_info, err = _require_api_key()
    if err:
        return err

    if not _asn_service:
        return jsonify({"error": "Serviço ASN indisponível"}), 503

    data = request.get_json(silent=True) or {}
    motivo = str(data.get("motivo", "Cancelado via API")).strip() or "Cancelado via API"

    try:
        asn = _asn_service.get_by_id(asn_id)
        if not asn:
            return jsonify({"error": "ASN não encontrado"}), 404

        if asn.email_transportadora.lower() != key_info["email_transportadora"].lower():
            return jsonify({"error": "ASN não encontrado"}), 404

        if asn.status not in ("agendado",):
            return jsonify({
                "error": f"Não é possível cancelar um ASN com status '{asn.status}'"
            }), 409

        success, msg = _asn_service.cancelar_asn(
            asn_id=asn_id,
            motivo=motivo,
            user_email=key_info["email_transportadora"],
        )

        if success:
            logger.info(
                f"[asn_api] ASN {asn_id} cancelado via API "
                f"(transportadora={key_info['transportadora']})"
            )
            return jsonify({"success": True, "message": msg}), 200

        return jsonify({"error": msg}), 400

    except Exception as e:
        logger.error(f"[asn_api] cancelar_asn {asn_id}: {e}")
        return jsonify({"error": "Erro ao cancelar ASN", "message": str(e)}), 500


# GET /api/v1/asn/slots — Horários ocupados numa data

@asn_api_bp.route("/slots", methods=["GET"])
def slots_disponiveis():
    """
    Retorna os slots de horário já ocupados para uma data.
    Útil para o TMS evitar criar ASNs em horários conflitantes.

    Query params:
      data        — obrigatório, formato YYYY-MM-DD
      empresa     — opcional, filtra por empresa destinatária

    Resposta:
      {
        "success": true,
        "data": "2024-12-25",
        "ocupados": ["14:00", "14:40"]
      }
    """
    key_info, err = _require_api_key()
    if err:
        return err

    if not _asn_service:
        return jsonify({"error": "Serviço ASN indisponível"}), 503

    data = request.args.get("data", "").strip()
    if not data:
        return jsonify({"error": "Parâmetro 'data' obrigatório (YYYY-MM-DD)"}), 400

    try:
        datetime.strptime(data, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Formato de data inválido. Use YYYY-MM-DD"}), 400

    empresa = request.args.get("empresa", "").strip()

    try:
        ocupados = _asn_service.get_slots_ocupados(data, empresa)
        return jsonify({
            "success": True,
            "data": data,
            "ocupados": ocupados,
        }), 200

    except Exception as e:
        logger.error(f"[asn_api] slots_disponiveis: {e}")
        return jsonify({"error": "Erro ao buscar slots", "message": str(e)}), 500


# GET /api/v1/asn/docs — Documentação pública (sem autenticação)

_DOCS_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>API ASN — Documentação</title>
<style>
  :root{--green:#28a745;--dark:#1a1e21;--surface:#242a2e;--border:#2e3a30;
        --text:#d4e8d4;--muted:#7a9a7a;--mono:'Courier New',monospace}
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--dark);color:var(--text);font-family:'Segoe UI',sans-serif;line-height:1.6;padding:2rem 1rem}
  .wrap{max-width:860px;margin:0 auto}
  h1{color:var(--green);font-family:var(--mono);font-size:1.6rem;margin-bottom:.3rem}
  .subtitle{color:var(--muted);font-size:.9rem;margin-bottom:2rem}
  h2{color:var(--green);font-family:var(--mono);font-size:1.1rem;margin:2rem 0 .8rem;
     border-bottom:1px solid var(--border);padding-bottom:.4rem}
  h3{font-size:.95rem;margin:1.2rem 0 .4rem;color:#fff}
  .endpoint{background:var(--surface);border:1px solid var(--border);border-radius:8px;
             padding:1.2rem 1.4rem;margin-bottom:1.2rem}
  .method{display:inline-block;font-family:var(--mono);font-size:.75rem;font-weight:700;
           padding:.15rem .55rem;border-radius:4px;margin-right:.6rem;vertical-align:middle}
  .get{background:rgba(23,162,184,.2);color:#5bc0de;border:1px solid #17a2b8}
  .post{background:rgba(40,167,69,.2);color:#5cdd7a;border:1px solid var(--green)}
  .path{font-family:var(--mono);font-size:.95rem;vertical-align:middle}
  .desc{color:var(--muted);font-size:.85rem;margin:.5rem 0 .8rem}
  table{width:100%;border-collapse:collapse;font-size:.83rem;margin:.6rem 0}
  th{background:rgba(0,0,0,.3);color:var(--muted);text-align:left;padding:.5rem .8rem;
     border:1px solid var(--border);font-weight:600;font-size:.75rem;text-transform:uppercase}
  td{padding:.45rem .8rem;border:1px solid var(--border);vertical-align:top}
  td code,th code{background:rgba(0,0,0,.4);border-radius:3px;padding:.1rem .35rem;
                   font-family:var(--mono);font-size:.8rem;color:#7fff7f}
  .req{color:#f0a055}
  .opt{color:var(--muted)}
  pre{background:#0a1a0f;border:1px solid var(--border);border-radius:6px;
      padding:1rem;overflow-x:auto;font-family:var(--mono);font-size:.8rem;
      color:#7fff7f;margin:.6rem 0}
  .badge{display:inline-block;font-family:var(--mono);font-size:.7rem;
          padding:.1rem .45rem;border-radius:3px;margin:.1rem}
  .b-warn{background:rgba(192,57,43,.15);color:#e07272;border:1px solid #7a2020}
  .b-ok{background:rgba(40,167,69,.15);color:#5cdd7a;border:1px solid #27643a}
  .b-info{background:rgba(23,162,184,.15);color:#5bc0de;border:1px solid #0e6a7a}
  .note{background:rgba(230,126,34,.08);border-left:3px solid #e67e22;
        padding:.7rem 1rem;border-radius:0 6px 6px 0;font-size:.83rem;margin:.8rem 0;color:#f0a055}
  .auth-box{background:rgba(40,167,69,.06);border:1px solid var(--border);
             border-radius:8px;padding:1rem 1.2rem;margin-bottom:1.5rem}
</style>
</head>
<body>
<div class="wrap">

<h1>&#x1F4E6; API do Portal ASN</h1>
<p class="subtitle">Advanced Shipping Notice — Integração para Transportadoras e TMS</p>

<!-- AUTH -->
<div class="auth-box">
  <h3 style="color:var(--green);margin-bottom:.5rem">&#x1F511; Autenticação</h3>
  <p style="font-size:.85rem">Todas as requisições (exceto <code>/docs</code> e <code>/health</code>)
  exigem o header:</p>
  <pre>X-API-Key: SUA_CHAVE_AQUI</pre>
  <p style="font-size:.82rem;color:var(--muted)">
    Chaves são geradas pelo administrador da empresa em <strong>WMS → Credenciais de API</strong>.
  </p>
</div>

<!-- REGRAS DE AGENDAMENTO -->
<h2>&#x1F4CB; Regras de Agendamento</h2>
<table>
  <tr><th>Regra</th><th>Valor</th></tr>
  <tr><td>Dias permitidos</td><td>Terça, Quarta e Quinta-feira</td></tr>
  <tr><td>Slots disponíveis</td><td><code>14:00</code> <code>14:40</code> <code>15:20</code> <code>16:00</code> <code>16:40</code></td></tr>
  <tr><td>Data retroativa</td><td>&#x274C; Não permitida</td></tr>
  <tr><td>Formato de data</td><td><code>YYYY-MM-DD HH:MM</code> ou <code>YYYY-MM-DDTHH:MM</code></td></tr>
</table>

<!-- ENDPOINTS -->
<h2>&#x1F4E1; Endpoints</h2>

<div class="endpoint">
  <span class="method get">GET</span><span class="path">/api/v1/asn</span>
  <p class="desc">Lista todos os ASNs da transportadora autenticada.</p>
  <h3>Query params</h3>
  <table>
    <tr><th>Param</th><th>Tipo</th><th>Descrição</th></tr>
    <tr><td><code>status</code></td><td class="opt">opcional</td><td>Filtra por status: <code>agendado</code> <code>processado</code> <code>recebido</code> <code>cancelado</code> <code>recusado</code></td></tr>
    <tr><td><code>limit</code></td><td class="opt">opcional</td><td>Máximo de registros (padrão: 100)</td></tr>
  </table>
  <pre>curl -H "X-API-Key: SUA_CHAVE" /api/v1/asn?status=agendado&limit=20</pre>
</div>

<div class="endpoint">
  <span class="method post">POST</span><span class="path">/api/v1/asn</span>
  <p class="desc">Cria um novo ASN. Retorna <code>201</code> em caso de sucesso.</p>
  <h3>Body JSON</h3>
  <table>
    <tr><th>Campo</th><th></th><th>Tipo</th><th>Descrição</th></tr>
    <tr><td><code>nota</code></td><td class="req">obrigatório</td><td>string</td><td>Número da NF ou NFD</td></tr>
    <tr><td><code>empresa_dest</code></td><td class="req">obrigatório</td><td>string</td><td>Nome exato da empresa destinatária</td></tr>
    <tr><td><code>data_prevista</code></td><td class="req">obrigatório</td><td>string</td><td>Data/hora no formato <code>YYYY-MM-DD HH:MM</code></td></tr>
    <tr><td><code>tipo_nota</code></td><td class="opt">opcional</td><td>string</td><td><code>NF</code> (padrão) ou <code>NFD</code></td></tr>
    <tr><td><code>cte</code></td><td class="opt">opcional</td><td>string</td><td>Número do CT-e</td></tr>
    <tr><td><code>volumes</code></td><td class="opt">opcional</td><td>string</td><td>Quantidade de volumes</td></tr>
    <tr><td><code>nome_motorista</code></td><td class="opt">opcional</td><td>string</td><td>Nome do motorista</td></tr>
    <tr><td><code>placa_veiculo</code></td><td class="opt">opcional</td><td>string</td><td>Placa do veículo</td></tr>
    <tr><td><code>descricao_avaria</code></td><td class="opt">opcional</td><td>string</td><td>Descrição prévia do dano ou motivo</td></tr>
  </table>
  <pre>{
  "nota": "123456",
  "empresa_dest": "Matriz",
  "data_prevista": "2025-06-10 14:00",
  "tipo_nota": "NF",
  "cte": "789",
  "volumes": "5",
  "nome_motorista": "João Silva",
  "placa_veiculo": "ABC-1234",
  "descricao_avaria": "Produto avariado durante transporte"
}</pre>
  <h3>Resposta 201</h3>
  <pre>{"success": true, "asn_id": "ASN-20250610-0001", "status": "agendado", "message": "..."}</pre>
  <h3>Erros possíveis</h3>
  <table>
    <tr><th>HTTP</th><th>Erro</th><th>Causa</th></tr>
    <tr><td>400</td><td>Campos obrigatórios ausentes</td><td><code>nota</code>, <code>empresa_dest</code> ou <code>data_prevista</code> faltando</td></tr>
    <tr><td>422</td><td>empresa_dest inválida</td><td>Empresa não encontrada ou sem módulo WMS</td></tr>
    <tr><td>422</td><td>data_prevista inválida</td><td>Data retroativa, dia não permitido ou horário inválido</td></tr>
    <tr><td>409</td><td>slot indisponível</td><td>Horário já reservado — consulte <code>/api/v1/asn/slots</code></td></tr>
  </table>
</div>

<div class="endpoint">
  <span class="method get">GET</span><span class="path">/api/v1/asn/{id}</span>
  <p class="desc">Retorna os dados de um ASN específico. A transportadora só acessa seus próprios ASNs.</p>
  <pre>curl -H "X-API-Key: SUA_CHAVE" /api/v1/asn/ASN-20250610-0001</pre>
</div>

<div class="endpoint">
  <span class="method post">POST</span><span class="path">/api/v1/asn/{id}/cancelar</span>
  <p class="desc">Cancela um ASN. Só é possível cancelar ASNs com status <code>agendado</code>.</p>
  <table>
    <tr><th>Campo</th><th></th><th>Descrição</th></tr>
    <tr><td><code>motivo</code></td><td class="opt">opcional</td><td>Motivo do cancelamento (padrão: "Cancelado via API")</td></tr>
  </table>
  <pre>{"motivo": "Carga não saiu do CD de origem"}</pre>
</div>

<div class="endpoint">
  <span class="method get">GET</span><span class="path">/api/v1/asn/slots</span>
  <p class="desc">Retorna os horários já ocupados para uma data. Use antes de criar um ASN para verificar disponibilidade.</p>
  <table>
    <tr><th>Param</th><th></th><th>Descrição</th></tr>
    <tr><td><code>data</code></td><td class="req">obrigatório</td><td>Formato <code>YYYY-MM-DD</code></td></tr>
    <tr><td><code>empresa</code></td><td class="opt">opcional</td><td>Filtra por empresa destinatária</td></tr>
  </table>
  <pre>curl -H "X-API-Key: SUA_CHAVE" "/api/v1/asn/slots?data=2025-06-10&empresa=Matriz"</pre>
  <h3>Resposta</h3>
  <pre>{"success": true, "data": "2025-06-10", "ocupados": ["14:00", "14:40"]}</pre>
</div>

<div class="endpoint">
  <span class="method get">GET</span><span class="path">/api/v1/asn/health</span>
  <p class="desc">Verifica o estado dos serviços. Não requer autenticação.</p>
  <pre>{"status": "ok", "asn_service": true, "sheets_service": true, "sheets_connected": true, "api_keys_registered": 3}</pre>
</div>

<!-- STATUS -->
<h2>&#x1F504; Status do ASN</h2>
<table>
  <tr><th>Status</th><th>Descrição</th></tr>
  <tr><td><span class="badge b-warn">agendado</span></td><td>ASN criado, aguardando recebimento pelo WMS</td></tr>
  <tr><td><span class="badge b-info">processado</span></td><td>Operador WMS vinculou ao sistema interno</td></tr>
  <tr><td><span class="badge b-ok">recebido</span></td><td>Carga recebida fisicamente no CD</td></tr>
  <tr><td><span class="badge" style="background:rgba(108,117,125,.15);color:#adb5bd;border:1px solid #6c757d">cancelado</span></td><td>ASN cancelado pela transportadora ou operador</td></tr>
  <tr><td><span class="badge b-warn" style="color:#e07272;border-color:#7a2020">recusado</span></td><td>Recusado pelo operador WMS</td></tr>
</table>

<p style="color:var(--muted);font-size:.78rem;margin-top:2.5rem;text-align:center">
  MultiSoluções &mdash; Portal ASN API v1
</p>

</div>
</body>
</html>"""


@asn_api_bp.route("/docs", methods=["GET"])
def documentacao():
    """GET /api/v1/asn/docs — Documentação pública da API (sem autenticação)."""
    from flask import Response
    return Response(_DOCS_HTML, mimetype="text/html")
