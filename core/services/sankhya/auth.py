import requests
import logging
from .config import load_config

logger = logging.getLogger(__name__)

class SankhyaAuth:
    """
    Context manager for Sankhya authentication
    Ensures automatic login/logout
    """
    
    def __init__(self, token=None):
        self.config = load_config()
        self.token = token
    
    def login(self, usuario=None, senha=None):
        """
        Performs login to Sankhya following official documentation
        """
        username = usuario or self.config['SANKHYA_USERNAME']
        password = senha or self.config['SANKHYA_PASSWORD']
        base_url = self.config["SANKHYA_BASE_URL"]
        
        if not username or not password:
            logger.error("[SANKHYA AUTH] Missing username or password for login.")
            raise ValueError("Sankhya credentials are required.")

        headers = {
            'token': self.config['SANKHYA_TOKEN'],
            'appkey': self.config['SANKHYA_APPKEY'],
            'username': username,
            'password': password
        }
        
        login_url = f'{base_url}/login'
        
        logger.info(f"[SANKHYA AUTH] Authenticating: {username}")
        # >>> DEBUG TEMPORÁRIO PARA VALIDAÇÃO DE TOKENS EM AMBIENTE <<< 
        # CONFIRMAR QUE ESTES VALORES BATEM COM O POSTMAN DE SUCESSO
        debug_appkey = headers['appkey'][:4] if headers['appkey'] else 'N/A'
        debug_token = headers['token'][:4] if headers['token'] else 'N/A'
        logger.info(f"[SANKHYA AUTH] Using URL: {login_url}")
        logger.info(f"[SANKHYA AUTH] Using AppKey: {debug_appkey}... and Token: {debug_token}...")
        # >>> FIM DEBUG TEMPORÁRIO <<< 
        
        try:
            response = requests.post(login_url, headers=headers, timeout=30)
            logger.info(f"[SANKHYA AUTH] Status: {response.status_code}")
            
            response.raise_for_status()
            
            response_data = response.json()
            bearer_token = response_data.get('bearerToken')
            
            if not bearer_token:
                logger.error(f"[SANKHYA AUTH] Response: {response.text}")
                raise ValueError("Bearer token not found in response")
                
            logger.info(f"[SANKHYA AUTH] Login successful")
            self.token = bearer_token
            return bearer_token
            
        except requests.exceptions.HTTPError as e:
            logger.error(f"[SANKHYA AUTH] HTTP Error: {e}")
            logger.error(f"[SANKHYA AUTH] Response: {response.text}")
            raise ConnectionError(f"Sankhya Login Failed (HTTP {response.status_code}): {response.text}")
        except Exception as e:
            logger.error(f"[SANKHYA AUTH] Error during login: {e}")
            raise ConnectionError(f"Sankhya Login Failed: {e}")

    def logout(self):
        """
        Performs logout from Sankhya
        """
        # ... (rest of the code omitted for brevity)
        pass

    def __enter__(self):
        """Context manager enter"""
        self.token = self.login()
        return self.token

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.logout()
