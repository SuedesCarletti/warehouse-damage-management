"""
core/services/email_service.py
Serviço unificado de envio de e-mails — MultiSoluções.

Responsabilidades:
  - Envio SMTP para todos os módulos (HelpDesk, WMS/ASN, WMS/Cobrança)
  - Suporte a remetente sobrescrito (from_override) sem precisar da senha
    de cada usuário — a autenticação SMTP continua usando a conta de serviço,
    mas o cabeçalho From exibe o e-mail desejado (funciona em Zimbra com relay)
  - Reconfiguração em tempo de execução via update_smtp_config() — as
    parametrizações WMS chamam esse método após salvar config no Sheets
  - Teste de conexão SMTP para a tela de parametrizações
  - Renderização de templates HTML com substituição de variáveis {{chave}}

Compatibilidade:
  - A assinatura de __init__(config) e send_email() foram preservadas.
  - Todos os call-sites existentes (ASN, HelpDesk, convite) continuam
    funcionando sem nenhuma alteração.

Arquitetura de remetente (Zimbra / qualquer relay autenticado):
  ┌─────────────────────────────────────────────────────────┐
  │  SMTP Auth   →  conta de serviço  (env MAIL_USERNAME)   │
  │  From header →  from_override  OU  email_remetente_wms  │
  │               OU  username  (fallback)                   │
  └─────────────────────────────────────────────────────────┘
  O Zimbra aceita From diferente do usuário autenticado desde que o
  relay interno esteja configurado (porta 587, SASL habilitado).
  Nenhuma senha individual de usuário é necessária.
"""

import smtplib
import logging
import re
import time
import threading
import socket
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class EmailService:
    """
    Serviço SMTP compartilhado entre todos os módulos da plataforma.

    Inicializado uma única vez no app.py com as variáveis de ambiente.
    Pode ser reconfigurado em tempo de execução via update_smtp_config()
    quando o operador salva as configurações SMTP nas parametrizações WMS.
    """

    def __init__(self, config: dict):
        """
        Inicializa a partir do dicionário de configuração do Flask (app.config).

        Args:
            config: dict com chaves MAIL_SERVER, MAIL_PORT, MAIL_USERNAME,
                    MAIL_PASSWORD, MAIL_USE_TLS, MAIL_USE_SSL, MAIL_FROM_NAME,
                    COMPANY_NAME, MAIL_ENABLED.
        """
        self._load_from_dict(config)
        logger.info(
            f"EmailService inicializado — "
            f"{'habilitado' if self.enabled else 'desabilitado'} "
            f"({'TLS' if self.use_tls else 'SSL' if self.use_ssl else 'plain'})"
        )

    # CONFIGURAÇÃO

    def _load_from_dict(self, cfg: dict) -> None:
        """Carrega/recarrega atributos SMTP a partir de um dicionário."""
        self.smtp_server = str(cfg.get('MAIL_SERVER') or cfg.get('email_smtp_host') or '').strip()
        self.smtp_port   = int(cfg.get('MAIL_PORT') or cfg.get('email_smtp_port') or 587)
        self.use_tls     = str(cfg.get('MAIL_USE_TLS', cfg.get('email_smtp_tls', True))).lower() not in ('false', 'nao', '0')
        self.use_ssl     = str(cfg.get('MAIL_USE_SSL', cfg.get('email_smtp_ssl', False))).lower() in ('true', 'sim', '1')
        
        self.username    = str(cfg.get('MAIL_USERNAME') or cfg.get('email_smtp_usuario') or '').strip()
        self.password    = str(cfg.get('MAIL_PASSWORD') or cfg.get('email_smtp_senha') or '').strip()
        
        self.from_name   = str(cfg.get('MAIL_FROM_NAME', 'MultiSoluções WMS') or 'MultiSoluções WMS').strip()
        self.company_name = str(cfg.get('COMPANY_NAME', 'MultiSoluções') or 'MultiSoluções').strip()

        # enabled = True somente quando servidor e credenciais estão presentes
        self.enabled = bool(self.smtp_server and self.username and self.password)

    def update_smtp_config(self, wms_config: dict) -> None:
        """
        Recarrega configuração SMTP a partir das chaves salvas no WMS_Config
        (Google Sheets). Chamado pelas parametrizações WMS após salvar.

        Chaves esperadas no wms_config:
          email_smtp_host, email_smtp_port, email_smtp_usuario,
          email_smtp_senha, email_smtp_tls, email_smtp_ssl,
          MAIL_FROM_NAME, COMPANY_NAME

        Se alguma chave estiver ausente, mantém o valor anterior (env var).
        """
        merged = {}
        # Preserva valores existentes como base
        merged['MAIL_SERVER']   = wms_config.get('email_smtp_host') or self.smtp_server
        merged['MAIL_PORT']     = wms_config.get('email_smtp_port') or self.smtp_port
        merged['MAIL_USERNAME'] = wms_config.get('email_smtp_usuario') or self.username
        merged['MAIL_PASSWORD'] = wms_config.get('email_smtp_senha') or self.password
        merged['MAIL_USE_TLS']  = wms_config.get('email_smtp_tls', self.use_tls)
        merged['MAIL_USE_SSL']  = wms_config.get('email_smtp_ssl', self.use_ssl)
        merged['MAIL_FROM_NAME'] = wms_config.get('MAIL_FROM_NAME', self.from_name)
        merged['COMPANY_NAME']  = wms_config.get('COMPANY_NAME', self.company_name)

        self._load_from_dict(merged)
        logger.info(
            f"🔄 EmailService reconfigurado — servidor: {self.smtp_server}:{self.smtp_port} "
            f"| usuário: {self.username} | enabled: {self.enabled}"
        )

    # ENVIO PRINCIPAL

    def send_email(
        self,
        to_addresses: List[str],
        subject: str,
        html_body: str,
        cc_addresses: Optional[List[str]] = None,
        from_override: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Envia um e-mail HTML.

        Args:
            to_addresses:  Lista de destinatários.
            subject:       Assunto do e-mail.
            html_body:     Corpo em HTML.
            cc_addresses:  Cópia (opcional).
            from_override: E-mail a exibir no campo "De" (From header).
                           Se None, usa o username da conta de serviço.
                           A autenticação SMTP sempre usa self.username/password,
                           apenas o cabeçalho From é substituído.
                           Útil para "enviar como usuário logado" no Zimbra.

        Returns:
            (True, "Email enviado com sucesso") ou (False, "mensagem de erro")
        """
        if not self.enabled:
            logger.warning("EmailService: tentativa de envio sem configuração SMTP")
            return False, "Email não configurado — configure SMTP nas parametrizações WMS"

        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject

            # ── Remetente visível ──────────────────────────────────────────
            if from_override:
                # Extrai nome amigável se vier no formato "Nome <email>"
                m = re.match(r'^(.+?)\s*<(.+?)>$', from_override)
                if m:
                    msg['From'] = from_override          # já formatado
                else:
                    msg['From'] = f"{self.from_name} <{from_override}>"
            else:
                msg['From'] = f"{self.from_name} <{self.username}>"

            msg['To'] = ', '.join(to_addresses)
            if cc_addresses:
                msg['Cc'] = ', '.join(cc_addresses)

            msg.attach(MIMEText(html_body, 'html', 'utf-8'))

            # ── Conexão SMTP ───────────────────────────────────────────────
            if self.use_ssl:
                server = smtplib.SMTP_SSL(self.smtp_server, self.smtp_port, timeout=30)
            else:
                server = smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=30)
                if self.use_tls:
                    server.starttls()

            server.login(self.username, self.password)

            all_recipients = list(to_addresses)
            if cc_addresses:
                all_recipients.extend(cc_addresses)

            server.send_message(msg, to_addrs=all_recipients)
            server.quit()

            logger.info(
                f"Email enviado | De: {msg['From']} | Para: {len(all_recipients)} dest. | Assunto: {subject[:60]}"
            )
            return True, "Email enviado com sucesso"

        except smtplib.SMTPAuthenticationError as e:
            msg_err = f"Falha de autenticação SMTP: {e}"
            logger.error(f"{msg_err}")
            return False, msg_err
        except smtplib.SMTPConnectError as e:
            msg_err = f"Não foi possível conectar ao servidor SMTP ({self.smtp_server}:{self.smtp_port}): {e}"
            logger.error(f"{msg_err}")
            return False, msg_err
        except (TimeoutError, socket.timeout):
            msg_err = f"Timeout ao conectar ao servidor SMTP ({self.smtp_server}:{self.smtp_port}). Verifique host, porta e firewall."
            logger.error(f"{msg_err}")
            return False, msg_err
        except Exception as e:
            logger.error(f"Erro ao enviar email: {e}")
            return False, str(e)

    def send_email_async(
        self,
        to_addresses: List[str],
        subject: str,
        html_body: str,
        cc_addresses: Optional[List[str]] = None,
        from_override: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Envia e-mail em background (assíncrono) com mecanismo de retentativas.
        Evita o bloqueio da requisição HTTP do usuário (WMS ASN, Auth, etc).
        """
        if not self.enabled:
            logger.warning("EmailService: tentativa de envio sem configuração SMTP")
            return False, "Email não configurado"

        def background_sender():
            max_retries = 3
            logger.info(f"⏳ [E-MAIL] Iniciando envio assíncrono para: {to_addresses}")
            
            for attempt in range(1, max_retries + 1):
                start_time = time.time()
                try:
                    success, msg = self.send_email(
                        to_addresses=to_addresses,
                        subject=subject,
                        html_body=html_body,
                        cc_addresses=cc_addresses,
                        from_override=from_override
                    )
                    elapsed = time.time() - start_time
                    
                    if success:
                        logger.info(f"[E-MAIL] Envio concluído ({elapsed:.2f}s) para: {to_addresses}")
                        break
                    else:
                        logger.warning(f"[E-MAIL] Falha na tentativa {attempt}/{max_retries} ({elapsed:.2f}s): {msg}")
                except Exception as e:
                    elapsed = time.time() - start_time
                    logger.error(f"[E-MAIL] Exceção na tentativa {attempt}/{max_retries} ({elapsed:.2f}s): {e}")
                
                if attempt < max_retries:
                    sleep_time = 2 ** attempt  # Backoff de 2s, depois 4s
                    logger.info(f"⏳ [E-MAIL] Aguardando {sleep_time}s antes da próxima tentativa...")
                    time.sleep(sleep_time)

        threading.Thread(target=background_sender, daemon=True).start()
        return True, "Email enfileirado para envio assíncrono"

    # TESTE DE CONEXÃO (usado pelo botão "Testar" nas parametrizações)

    def test_connection(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        usuario: Optional[str] = None,
        senha: Optional[str] = None,
        use_tls: Optional[bool] = None,
        use_ssl: Optional[bool] = None,
    ) -> Tuple[bool, str]:
        """
        Testa a conexão SMTP sem enviar e-mail.

        Aceita parâmetros explícitos (teste antes de salvar) ou usa a
        configuração atual do serviço se nenhum parâmetro for fornecido.

        Returns:
            (True, "Conexão bem-sucedida — servidor: xxx:yyy") ou (False, "erro")
        """
        _host    = str(host or self.smtp_server or '').strip()
        _port    = int(port or self.smtp_port)
        _user    = str(usuario or self.username or '').strip()
        _pass    = str(senha   or self.password or '').strip()
        
        if use_tls is not None:
            _tls = str(use_tls).lower() not in ('false', 'nao', '0', '')
        else:
            _tls = self.use_tls
            
        if use_ssl is not None:
            _ssl = str(use_ssl).lower() in ('true', 'sim', '1')
        else:
            _ssl = self.use_ssl

        if not _host or not _user or not _pass:
            return False, "Preencha servidor, usuário e senha antes de testar"

        # Prevenir erro comum de configuração de porta/SSL (Porta 587 com SSL Direto)
        if _port == 587 and _ssl:
            return False, "❌ Configuração incorreta: A porta 587 requer 'STARTTLS = sim' e 'SSL direto = nao'. Para usar SSL direto, altere a porta para 465."

        try:
            if _ssl:
                server = smtplib.SMTP_SSL(_host, _port, timeout=30)
            else:
                server = smtplib.SMTP(_host, _port, timeout=30)
                if _tls:
                    server.starttls()

            server.login(_user, _pass)
            server.quit()

            logger.info(f"Teste SMTP bem-sucedido: {_host}:{_port} ({_user})")
            return True, f"Conexão bem-sucedida — {_host}:{_port} | Autenticado como {_user}"

        except smtplib.SMTPAuthenticationError:
            return False, f"❌ Usuário ou senha inválidos para {_user} em {_host}"
        except smtplib.SMTPConnectError as e:
            return False, f"❌ Não foi possível conectar a {_host}:{_port} — {e}"
        except ConnectionRefusedError:
            return False, f"❌ Conexão recusada por {_host}:{_port} — verifique o servidor e a porta"
        except (TimeoutError, socket.timeout):
            return False, f"❌ Timeout ao conectar em {_host}:{_port} (Pode ser bloqueio de firewall na porta)"
        except Exception as e:
            return False, f"❌ Erro: {e}"

    # TEMPLATES DE E-MAIL

    def render_template_str(self, html_template: str, variables: dict) -> str:
        """
        Substitui variáveis {{chave}} no template HTML.

        Exemplo:
            template = "<p>Olá {{transportadora}}, sua NF é {{numero_nf}}.</p>"
            variables = {"transportadora": "Rapidex", "numero_nf": "12345"}
            → "<p>Olá Rapidex, sua NF é 12345.</p>"

        Variáveis não encontradas são mantidas como {{chave}} (não quebram).
        """
        def replacer(match):
            key = match.group(1).strip()
            return str(variables.get(key, match.group(0)))  # mantém original se não encontrado

        return re.sub(r'\{\{(\w+)\}\}', replacer, html_template)

    def send_template_email(
        self,
        to_addresses: List[str],
        subject: str,
        html_template: str,
        variables: dict,
        cc_addresses: Optional[List[str]] = None,
        from_override: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Renderiza um template e envia o e-mail resultante.

        Args:
            html_template: HTML com marcadores {{chave}}.
            variables:     Dicionário de substituições.
            Demais args:   Idênticos a send_email().
        """
        html_body = self.render_template_str(html_template, variables)
        rendered_subject = self.render_template_str(subject, variables)
        return self.send_email_async(
            to_addresses=to_addresses,
            subject=rendered_subject,
            html_body=html_body,
            cc_addresses=cc_addresses,
            from_override=from_override,
        )

    # NOTIFICAÇÕES LEGADAS — HelpDesk (mantidas para compatibilidade)

    def send_ticket_notification(
        self,
        ticket,
        recipient_email: str,
        base_url: str,
    ) -> Tuple[bool, str]:
        """
        Envia notificação de novo ticket (HelpDesk).
        Mantido sem alterações para compatibilidade total.
        """
        tracking_pixel   = f"{base_url}/track-email/{ticket.ticket_number}"
        resolution_link  = f"{base_url}/resolve-ticket/{ticket.resolution_token}"

        html_body = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: 'Segoe UI', sans-serif; margin: 0; padding: 0; background: #f4f4f4; }}
        .container {{ max-width: 650px; margin: 20px auto; background: white; border-radius: 8px; }}
        .header {{ background: linear-gradient(135deg, #2c5aa0, #1e3a5f); color: white; padding: 25px; text-align: center; }}
        .content {{ padding: 25px; }}
        .ticket-card {{ background: #f8f9fa; border-left: 4px solid #2c5aa0; padding: 20px; margin: 20px 0; }}
        .action-button {{ display: block; width: fit-content; margin: 25px auto; padding: 15px 30px;
                         background: #28a745; color: white; text-decoration: none; border-radius: 25px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎫 Novo Chamado de TI</h1>
            <h2>#{ticket.ticket_number}</h2>
        </div>
        <div class="content">
            <div class="ticket-card">
                <p><strong>Solicitante:</strong> {ticket.user_name}</p>
                <p><strong>E-mail:</strong> {ticket.user_email}</p>
                <p><strong>Departamento:</strong> {ticket.department}</p>
                <p><strong>Categoria:</strong> {ticket.category}</p>
                <p><strong>Prioridade:</strong> {ticket.priority}</p>
            </div>
            <h3>📋 Assunto</h3>
            <p>{ticket.subject}</p>
            <h3>📝 Descrição</h3>
            <div style="background: white; padding: 15px; border: 1px solid #dee2e6;">
                {ticket.description.replace(chr(10), '<br>')}
            </div>
            <a href="{resolution_link}" class="action-button">Marcar como Resolvido</a>
        </div>
    </div>
    <img src="{tracking_pixel}" width="1" height="1" style="display:none;">
</body>
</html>'''

        subject = f'Novo Chamado TI #{ticket.ticket_number} - {ticket.subject}'
        return self.send_email_async([recipient_email], subject, html_body, [ticket.user_email])

    def send_password_reset_email(
        self,
        user_email: str,
        token: str,
        base_url: str,
    ) -> Tuple[bool, str]:
        """Envia e-mail com link para reset de senha. Compatibilidade HelpDesk."""
        reset_link = f"{base_url}/auth/reset-password/{token}"

        html_body = f'''<!DOCTYPE html>
<html>
<head><meta charset="UTF-8">
<style>
  body {{ font-family: 'Segoe UI', sans-serif; background: #f4f4f4; }}
  .container {{ max-width: 500px; margin: 30px auto; background: #fff; border-radius: 8px; overflow: hidden; }}
  .header {{ background: linear-gradient(135deg, #2c5aa0, #1e3a5f); color: white; padding: 24px; text-align: center; }}
  .content {{ padding: 28px; }}
  .btn {{ display: block; width: fit-content; margin: 20px auto; padding: 14px 32px;
          background: #2c5aa0; color: white; text-decoration: none; border-radius: 6px; font-weight: 600; }}
  .footer {{ padding: 12px; background: #f8f9fa; font-size: .8rem; color: #666; text-align: center; }}
</style>
</head>
<body>
<div class="container">
  <div class="header"><h2>🔐 Redefinição de Senha</h2></div>
  <div class="content">
    <p>Você solicitou a redefinição de senha. Clique no botão abaixo para criar uma nova senha.</p>
    <a href="{reset_link}" class="btn">Redefinir Senha</a>
    <p style="font-size:.85rem;color:#666;margin-top:20px;">
      Este link é válido por 1 hora. Se você não fez esta solicitação, ignore este e-mail.
    </p>
  </div>
  <div class="footer">{self.company_name}</div>
</div>
</body>
</html>'''

        return self.send_email_async(
            [user_email],
            f'🔐 Redefinição de senha — {self.company_name}',
            html_body,
        )
