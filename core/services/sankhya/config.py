import os
import logging
logger = logging.getLogger(__name__)

def load_config():
    """Reads Sankhya specific environment variables needed by SankhyaAuth."""
    config = {
        # Default BASE_URL assumed to be the root of the API endpoint (e.g., http://host:port/mge)
        'SANKHYA_BASE_URL': os.environ.get('SANKHYA_BASE_URL', 'http://localhost:8080/mge'),
        'SANKHYA_USERNAME': os.environ.get('SANKHYA_USERNAME'),
        'SANKHYA_PASSWORD': os.environ.get('SANKHYA_PASSWORD'),
        'SANKHYA_TOKEN': os.environ.get('SANKHYA_AUTH_TOKEN', 'YOUR_STATIC_API_TOKEN'),
        'SANKHYA_APPKEY': os.environ.get('SANKHYA_APPKEY', 'WMS_APP_KEY')
    }
    
    if not config['SANKHYA_USERNAME'] or not config['SANKHYA_PASSWORD']:
        logger.warning("Sankhya credentials (USERNAME/PASSWORD) not configured.")
        
    return config
