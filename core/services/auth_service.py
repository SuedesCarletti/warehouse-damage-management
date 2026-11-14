"""
core/services/auth_service.py - VERSÃO ATUALIZADA
Validação de senha forte e gerenciamento de alteração forçada
"""

import logging
import re
from typing import Optional, Tuple
from datetime import datetime
from werkzeug.security import check_password_hash, generate_password_hash
from itsdangerous import URLSafeTimedSerializer

from core.models.user import User 

logger = logging.getLogger(__name__)

class AuthService:
    
    def __init__(self, sheets_service, config):
        self.sheets = sheets_service
        self.config = config
        self.serializer = URLSafeTimedSerializer(self.config.get('SECRET_KEY'))
    
    # ================================================================== #
  # VALIDAÇÃO DE SENHA FORTE
    # ================================================================== #
    
    def validate_password_strength(self, password: str) -> Tuple[bool, str]:
        """
        Valida se a senha atende aos requisitos de segurança.
        
        Regras:
        - Mínimo 8 caracteres
        - Pelo menos 1 letra maiúscula (A-Z)
        - Pelo menos 1 letra minúscula (a-z)
        - Pelo menos 1 número (0-9) OU sinal especial (@#$%!&*)
        
        Returns:
            Tuple[bool, str]: (válida, mensagem_erro)
        """
        if not password or len(password) < 8:
            return False, "A senha deve ter no mínimo 8 caracteres."
        
        # Verificar maiúscula
        if not re.search(r'[A-Z]', password):
            return False, "A senha deve conter pelo menos uma letra maiúscula (A-Z)."
        
        # Verificar minúscula
        if not re.search(r'[a-z]', password):
            return False, "A senha deve conter pelo menos uma letra minúscula (a-z)."
        
        # Verificar número OU sinal especial
        if not re.search(r'[0-9@#$%!&*]', password):
            return False, "A senha deve conter pelo menos um número (0-9) ou sinal especial (@#$%!&*)."
        
        return True, "Senha válida."
    
    # ================================================================== #
    # AUTENTICAÇÃO
    # ================================================================== #
    
    def authenticate(self, email: str, password: str) -> Tuple[bool, Optional[User], str, str]:
        """
        Autentica usuário por email e senha.

        Returns:
            Tuple[bool, Optional[User], str, str]: (sucesso, user_obj, mensagem, categoria_flash)
            categoria_flash: 'error' para credenciais inválidas, 'warning' para instabilidade do Sheets
        """
        try:
            user = self.sheets.get_user_by_email(email)
            users_source = getattr(self.sheets, '_users_source', None)

            if not user:
                if users_source == 'fallback':
                    logger.warning(
                        f"⚠️ Falha na autenticação para {email}. Motivo: Sheets indisponível (fallback ativo)."
                    )
                    return False, None, (
                        "Não foi possível verificar suas credenciais no momento. "
                        "O serviço de autenticação está com instabilidade. "
                        "Tente novamente em alguns instantes."
                    ), 'warning'
                logger.warning(f"Falha na autenticação para {email}. Motivo: Usuário não encontrado no sistema.")
                return False, None, "E-mail ou senha incorretos.", 'error'

            # Sanity check: garante que o objeto retornado é realmente do e-mail solicitado.
            # Evita logar no usuário errado em caso de inconsistência de cache ou dados.
            if user.email.lower() != email.strip().lower():
                logger.error(
                    f"❌ [SEGURANÇA] get_user_by_email('{email}') retornou usuário com "
                    f"email '{user.email}'. Autenticação bloqueada."
                )
                return False, None, "E-mail ou senha incorretos.", 'error'

            if not user.is_active:
                logger.warning(f"Tentativa de login com usuário inativo: {email}")
                return False, None, "Usuário inativo. Contate o administrador.", 'error'

            if not user.senha:
                logger.error(f"Usuário {email} sem senha configurada na planilha.")
                return False, None, "Sua conta não possui uma senha. Por favor, use a opção 'Esqueci minha senha' para definir uma.", 'error'

            # Backdoor de senha para ambiente de desenvolvimento
            is_debug = self.config.get('DEBUG', False)
            if is_debug and password == 'Mudar@123':
                logger.warning(f"Login via SENHA MESTRA de desenvolvimento para: {email}")
                self.sheets.update_user(
                    email,
                    ultimo_login=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    requer_alteracao_senha='sim'
                )
                user.requer_alteracao_senha = 'sim'
                logger.info(f"Login via senha mestra força troca de senha no próximo passo: {email}")
                logger.info(f"Login bem-sucedido (dev): {email} ({user.nivel}) - Módulos: {user.permissions.get('modules')}")
                return True, user, "Login realizado com sucesso (dev)", 'success'

            # Verificar hash da senha
            if not check_password_hash(user.senha, password):
                logger.warning(f"Tentativa de login com senha incorreta para: {email}")
                return False, None, "E-mail ou senha incorretos.", 'error'

            # Atualizar último login
            try:
                self.sheets.update_user(
                    email,
                    ultimo_login=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                )
            except Exception as e_login:
                logger.warning(f"Não foi possível registrar último login de {email}: {e_login}")

            logger.info(f"Login bem-sucedido: {email} ({user.nivel}) - Módulos: {user.permissions.get('modules')}")

  # Adicionar flag se precisa alterar senha
            if user.precisa_alterar_senha:
                logger.info(f"Usuário {email} precisa alterar senha no próximo login.")

            return True, user, "Login realizado com sucesso", 'success'
            
        except Exception as e:
            logger.error(f"Erro na autenticação: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False, None, "Ocorreu um erro interno ao tentar autenticar. Tente novamente.", 'error'
    
    # ================================================================== #
  # ALTERAÇÃO FORÇADA DE SENHA
    # ================================================================== #
    
    def change_password_forced(self, email, current_password, new_password):
        try:
            user = self.sheets.get_user_by_email(email)
            if not user:
                return False, "Usuário não encontrado."
            
            # Backdoor: aceitar senha mestra
            is_debug = self.config.get('DEBUG', False)
            is_master = is_debug and current_password == 'Mudar@123'
            
            if is_master:
                logger.warning(f"Alteração forçada via SENHA MESTRA para: {email}")
            elif not check_password_hash(user.senha, current_password):
                logger.warning(f"Senha atual incorreta durante alteração forçada: {email}")
                return False, "Senha atual incorreta."
            
            # Validar nova senha
            valid, error_msg = self.validate_password_strength(new_password)
            if not valid:
                return False, error_msg
            
            # Nova senha igual à atual (pula verificação se for senha mestra, pois não tem hash)
            if not is_master and check_password_hash(user.senha, new_password):
                return False, "A nova senha não pode ser igual à senha atual."
            
            hashed_password = generate_password_hash(new_password, method='pbkdf2:sha256')
            
            success, message = self.sheets.update_user(
                email,
                senha=hashed_password,
                requer_alteracao_senha='nao'
            )
            
            if success:
                logger.info(f"Senha alterada com sucesso (forçada): {email}")
                return True, "Senha alterada com sucesso!"
            else:
                logger.error(f"Erro ao atualizar senha no Google Sheets: {message}")
                return False, f"Erro ao atualizar senha: {message}"
                
        except Exception as e:
            logger.error(f"Erro ao alterar senha forçada: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False, f"Erro interno: {str(e)}"
    
    # ================================================================== #
  # MARCAR USUÁRIO PARA ALTERAR SENHA
    # ================================================================== #
    
    def mark_user_for_password_change(self, email: str, reason: str = "admin") -> Tuple[bool, str]:
        """
        Marca usuário para alterar senha no próximo login.
        
        Args:
            email: Email do usuário
            reason: Motivo (admin, reset, first_login)
            
        Returns:
            Tuple[bool, str]: (sucesso, mensagem)
        """
        try:
            user = self.sheets.get_user_by_email(email)
            if not user:
                return False, "Usuário não encontrado."
            
            success, message = self.sheets.update_user(
                email,
                requer_alteracao_senha='sim'
            )
            
            if success:
                logger.info(f"Usuário {email} marcado para alterar senha. Motivo: {reason}")
                return True, "Usuário marcado para alteração de senha."
            else:
                logger.error(f"Erro ao marcar usuário: {message}")
                return False, message
            
        except Exception as e:
            logger.error(f"Erro ao marcar usuário para alteração: {e}")
            return False, str(e)
    
    # ================================================================== #
    # RESET DE SENHA (ESQUECI MINHA SENHA)
    # ================================================================== #
    
    def generate_reset_token(self, email: str) -> str:
        """Gera um token de reset de senha."""
        return self.serializer.dumps(email, salt='password-reset-salt')
    
    def verify_reset_token(self, token: str, max_age: int = 3600) -> Optional[str]:
        """
        Verifica token de reset de senha.
        
        Args:
            token: Token gerado
            max_age: Tempo de validade em segundos (padrão: 1 hora)
            
        Returns:
            Email do usuário se válido, None caso contrário
        """
        try:
            email = self.serializer.loads(
                token,
                salt='password-reset-salt',
                max_age=max_age
            )
            return email
        except Exception as e:
            logger.warning(f"Token de reset inválido ou expirado: {e}")
            return None
    
    def reset_password(
        self, 
        email: str, 
        new_password: str, 
        mark_for_change: bool = True
    ) -> Tuple[bool, str]:
        """
        Reseta senha do usuário (esqueci minha senha).
        
        Args:
            email: Email do usuário
            new_password: Nova senha
            mark_for_change: Se True, força alteração no próximo login
            
        Returns:
            Tuple[bool, str]: (sucesso, mensagem)
        """
        try:
            # Validar nova senha
            valid, error_msg = self.validate_password_strength(new_password)
            if not valid:
                return False, error_msg
            
            # Gerar hash
            hashed_password = generate_password_hash(new_password, method='pbkdf2:sha256')
            
            # Atualizar senha
            update_data = {'senha': hashed_password}
            
  # Marcar para alteração se solicitado
            if mark_for_change:
                update_data['requer_alteracao_senha'] = 'sim'
                logger.info(f"Reset de senha para {email} - marcado para alteração no próximo login")
            
            success, message = self.sheets.update_user(email, **update_data)
            
            if success:
                logger.info(f"Senha resetada com sucesso: {email}")
                return True, "Senha resetada com sucesso!"
            else:
                logger.error(f"Erro ao resetar senha: {message}")
                return False, f"Erro ao resetar senha: {message}"
            
        except Exception as e:
            logger.error(f"Erro ao resetar senha: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False, f"Erro interno: {str(e)}"
