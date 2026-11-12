"""
modules/wms/services/convite_service.py
Gerencia convites de auto-cadastro para transportadoras.

Fluxo:
  1. Operador WMS gera um token via gerar_convite()
  2. Token é salvo na aba 'ASN_Convites' com validade de 72h
  3. Link público é montado com o token
  4. Transportadora acessa o link, preenche nome/email/senha
  5. usar_convite() valida o token e cria o usuário automaticamente
  6. Token é marcado como usado (uso único)

Permissões do usuário criado (fixas):
  - nivel       : transportadora
  - modulos     : wms
  - tela        : portal_asn
  - empresa     : herdada do operador que gerou o convite
"""

import uuid
import logging
import traceback
from datetime import datetime, timedelta
from typing import Optional, Tuple
from werkzeug.security import generate_password_hash

logger = logging.getLogger(__name__)

# ── Aba no Google Sheets ───────────────────────────────────────────────────
SHEET_NAME = 'ASN_Convites'
HEADERS = [
    'id', 'token', 'empresa', 'criado_por', 'criado_em',
    'expira_em', 'usado', 'usado_em', 'email_cadastrado'
]

# Validade padrão em horas
VALIDADE_HORAS = 72


class ConviteService:

    def __init__(self, sheets_service):
        self.sheets = sheets_service
        # Cache de referências de worksheet — evita chamar .worksheet() a cada request
        # Invalidar com: self._ws_cache.clear() (ex: após reconectar o Sheets)
        self._ws_cache: dict = {}
        logger.info("WmsConfigService inicializado")

    # SETUP

    def _ensure_sheet(self, sheet_name: str):
        """
        Garante que a aba existe e retorna o objeto worksheet.

        ✅ FIX loop infinito de 429:
          - Worksheet é cacheado após primeira obtenção bem-sucedida.
            Requests subsequentes não chamam a API.
          - Se worksheet() falhar com 429, retorna None imediatamente
            sem tentar add_worksheet (que também retornaria 429 e geraria
            o log infinito observado nos logs de produção).
        """
        if not self.sheets or not self.sheets.connected:
            return None

        # ── Cache hit: retorna sem chamar API ────────────────────────────
        if sheet_name in self._ws_cache:
            return self._ws_cache[sheet_name]

        headers = self.HEADERS.get(sheet_name, [])

        # ── Tenta obter aba existente ────────────────────────────────────
        try:
            ws = self.sheets.sheet.worksheet(sheet_name)
            self._ws_cache[sheet_name] = ws   # cacheia referência
            return ws

        except Exception as e:
            err_str = str(e)

            # ── 429: rate limit — NÃO tenta criar, apenas avisa e sai ───
            # Sem este check, caia no except genérico abaixo que chamava
            # add_worksheet → outro 429 → log infinito
            if '429' in err_str or 'Quota exceeded' in err_str:
                logger.warning(
                    f"⚠️ [429] Aba '{sheet_name}' inacessível por rate limit. "
                    f"Retornando None sem tentar criar."
                )
                return None

            # ── Aba realmente não existe: tenta criar ───────────────────
            try:
                ws = self.sheets.sheet.add_worksheet(
                    title=sheet_name,
                    rows=500,
                    cols=max(len(headers), 20)
                )
                if headers:
                    ws.update('A1', [headers])
                logger.info(f"Aba '{sheet_name}' criada")
                self._ws_cache[sheet_name] = ws
                return ws

            except Exception as create_e:
                create_str = str(create_e)

                if '429' in create_str or 'Quota exceeded' in create_str:
                    # Falhou por rate limit durante criação — não loga como erro
                    logger.warning(
                        f"⚠️ [429] Não foi possível criar aba '{sheet_name}' "
                        f"por rate limit. Retornando None."
                    )
                    return None

                if 'already exists' in create_str.lower():
                    # Race condition: outra instância criou primeiro
                    logger.debug(f"Aba '{sheet_name}' já existe (race condition)")
                    try:
                        ws = self.sheets.sheet.worksheet(sheet_name)
                        self._ws_cache[sheet_name] = ws
                        return ws
                    except Exception:
                        return None

                logger.error(f"Não foi possível criar aba '{sheet_name}': {create_e}")
                return None

    def _worksheet(self):
        if not self.sheets or not self.sheets.connected:
            return None
        try:
            return self.sheets.sheet.worksheet(SHEET_NAME)
        except Exception as e:
            logger.error(f"Worksheet {SHEET_NAME}: {e}")
            return None

    def _now(self) -> str:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def _parse_dt(self, s: str) -> Optional[datetime]:
        try:
            return datetime.strptime(s, '%Y-%m-%d %H:%M:%S')
        except Exception:
            return None

    # GERAÇÃO DE CONVITE

    def gerar_convite(self, empresa: str, criado_por: str) -> Tuple[bool, str, Optional[str]]:
        """
        Gera um token de convite para uma empresa.

        Returns:
            (success, message, token)
        """
        ws = self._worksheet()
        if not ws:
            return False, "Google Sheets não disponível", None

        try:
            convite_id = str(uuid.uuid4())[:8].upper()
            token      = str(uuid.uuid4())
            agora      = datetime.now()
            expira_em  = (agora + timedelta(hours=VALIDADE_HORAS)).strftime('%Y-%m-%d %H:%M:%S')

            ws.append_row([
                convite_id,
                token,
                empresa,
                criado_por,
                agora.strftime('%Y-%m-%d %H:%M:%S'),
                expira_em,
                'nao',   # usado
                '',      # usado_em
                '',      # email_cadastrado
            ])

            logger.info(f"Convite {convite_id} gerado por {criado_por} para empresa {empresa}")
            return True, "Convite gerado com sucesso", token

        except Exception as e:
            logger.error(f"gerar_convite: {e}")
            return False, str(e), None

    # VALIDAÇÃO

    def validar_token(self, token: str) -> Tuple[bool, str, Optional[dict]]:
        """
        Valida se um token é válido e não expirado.

        Returns:
            (valido, mensagem, dados_do_convite)
        """
        ws = self._worksheet()
        if not ws:
            return False, "Serviço indisponível", None

        try:
            all_values = ws.get_all_values()
            if not all_values or len(all_values) < 2:
                return False, "Token inválido", None

            headers = all_values[0]
            for row in all_values[1:]:
                record = {headers[i]: row[i] if i < len(row) else '' for i in range(len(headers))}

                if record.get('token', '') != token:
                    continue

                # Token encontrado — verificar uso
                if record.get('usado', 'nao') == 'sim':
                    return False, "Este link já foi utilizado. Solicite um novo convite.", None

                # Verificar expiração
                expira = self._parse_dt(record.get('expira_em', ''))
                if expira and datetime.now() > expira:
                    return False, "Este link expirou. Solicite um novo convite.", None

                return True, "Token válido", record

            return False, "Token inválido ou não encontrado", None

        except Exception as e:
            logger.error(f"validar_token: {e}")
            return False, "Erro ao validar token", None

    # USO DO CONVITE — CRIA USUÁRIO

    def usar_convite(self, token: str, nome: str, email: str, senha: str) -> Tuple[bool, str]:
        """
        Valida o token e cria o usuário transportadora no Google Sheets.

        Permissões fixas criadas:
          - nivel = transportadora
          - empresa = empresa do convite
          - módulo wms, tela portal_asn

        Returns:
            (success, message)
        """
        # 1. Validar token
        valido, msg, convite = self.validar_token(token)
        if not valido:
            return False, msg

        empresa = convite.get('empresa', '')

        # 2. Verificar se email já existe
        usuario_existente = self.sheets.get_user_by_email(email)
        if usuario_existente:
            return False, "Este e-mail já está cadastrado no sistema."

        # 3. Criar usuário
        try:
            from core.models.user import User
            senha_hash = generate_password_hash(senha)

            novo_usuario = User(
                nome=nome.strip(),
                email=email.strip().lower(),
                departamento='Transportadora',
                nivel='transportadora',
                ativo='sim',
                senha=senha_hash,
                empresa=empresa,
                data_cadastro=datetime.now().strftime('%Y-%m-%d'),
                ultimo_login='',
            )

            success, msg_user = self.sheets.add_user(novo_usuario)
            if not success:
                return False, f"Erro ao criar usuário: {msg_user}"

        except Exception as e:
            logger.error(f"usar_convite — criar usuário: {e}")
            return False, "Erro interno ao criar usuário."

        # 4. Garantir permissão transportadora no Sheets (wms / portal_asn)
        try:
            self._garantir_permissao_transportadora(empresa)
        except Exception as e:
            logger.warning(f"Permissão transportadora não garantida: {e}")
            # Não bloqueia — usuário já foi criado

        # 5. Marcar convite como usado
        try:
            self._marcar_usado(token, email)
        except Exception as e:
            logger.warning(f"Não foi possível marcar convite como usado: {e}")

        logger.info(f"Transportadora {email} cadastrada via convite para empresa {empresa}")
        return True, "Cadastro realizado com sucesso!"

    # HELPERS INTERNOS

    def _marcar_usado(self, token: str, email_cadastrado: str):
        """Marca o token como usado na planilha."""
        ws = self._worksheet()
        if not ws:
            return

        all_values = ws.get_all_values()
        if not all_values:
            return

        headers = all_values[0]
        token_col = headers.index('token') + 1     # 1-indexed
        usado_col = headers.index('usado') + 1
        usado_em_col = headers.index('usado_em') + 1
        email_col = headers.index('email_cadastrado') + 1

        for i, row in enumerate(all_values[1:], start=2):
            if len(row) > token_col - 1 and row[token_col - 1] == token:
                ws.update_cell(i, usado_col, 'sim')
                ws.update_cell(i, usado_em_col, self._now())
                ws.update_cell(i, email_col, email_cadastrado)
                break

    def _garantir_permissao_transportadora(self, empresa: str):
        """
        Garante que existe uma linha na aba Permissoes para
        nivel=transportadora, modulo=wms, tela=portal_asn,
        NO FORMATO CORRETO: empresa | tipo | perfil | modulo | tela | permitido
        """
        if not self.sheets or not self.sheets.connected:
            return
    
        try:
            ws = self.sheets.sheet.worksheet('Permissoes')
            all_values = ws.get_all_values()
            headers = all_values[0] if all_values else []
    
            # Verifica se já existe no formato novo
            for row in all_values[1:]:
                record = {headers[i]: row[i] if i < len(row) else '' for i in range(len(headers))}
                if (record.get('empresa', '').strip().lower() == empresa.lower()
                        and record.get('tipo', '').strip() == 'nivel'
                        and record.get('perfil', '').strip() == 'transportadora'
                        and record.get('modulo', '').strip() == 'wms'
                        and record.get('tela', '').strip() == 'portal_asn'
                        and record.get('permitido', '').strip() == 'sim'):
                    logger.info(f"Permissão transportadora já existe para {empresa}")
                    return  # Já existe
    
            # Insere no formato correto: empresa | tipo | perfil | modulo | tela | permitido
            ws.append_row([empresa, 'nivel', 'transportadora', 'wms', 'portal_asn', 'sim'])
            logger.info(f"Permissão transportadora/wms/portal_asn garantida para {empresa}")
    
            # Invalida o cache de permissões
            if hasattr(self.sheets, 'invalidate_permissions_cache'):
                self.sheets.invalidate_permissions_cache()
            if hasattr(self.sheets, '_matrix_cache'):
                self.sheets._matrix_cache.pop(f'perm_matrix_{empresa}', None)
    
        except Exception as e:
            logger.warning(f"_garantir_permissao_transportadora: {e}")
      
    # LISTAGEM

    def listar_convites(self, empresa: str = '') -> list:
        """Retorna convites gerados, opcionalmente filtrados por empresa."""
        ws = self._worksheet()
        if not ws:
            return []

        try:
            all_values = ws.get_all_values()
            if not all_values or len(all_values) < 2:
                return []

            headers = all_values[0]
            result = []
            for row in all_values[1:]:
                if not any(str(v).strip() for v in row):
                    continue
                record = {headers[i]: row[i] if i < len(row) else '' for i in range(len(headers))}
                if empresa and record.get('empresa', '') != empresa:
                    continue

                # Calcular status legível
                usado = record.get('usado', 'nao') == 'sim'
                expira = self._parse_dt(record.get('expira_em', ''))
                expirado = expira and datetime.now() > expira

                record['status_label'] = (
                    'Usado'    if usado     else
                    'Expirado' if expirado  else
                    'Ativo'
                )
                record['status_class'] = (
                    'secondary' if usado    else
                    'danger'    if expirado else
                    'success'
                )
                result.append(record)

            result.sort(key=lambda r: r.get('criado_em', ''), reverse=True)
            return result

        except Exception as e:
            logger.error(f"listar_convites: {e}")
            return []
