"""
config.py - Configurações Centralizadas
Gerencia todas as variáveis de ambiente e configurações da aplicação
"""

import os
from pathlib import Path

# Base directory
BASE_DIR = Path(__file__).resolve().parent

class Config:
    """Configurações base da aplicação"""
    
    # Flask
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    DEBUG = os.environ.get('FLASK_ENV', 'production') == 'development'
    TESTING = False
    
    # Banco de Dados
    DATABASE_PATH = os.path.join(BASE_DIR, 'helpdesk.db')
    
    # Google Sheets
    GOOGLE_SHEETS_CREDENTIALS = os.environ.get('GOOGLE_SHEETS_CREDENTIALS')
    SPREADSHEET_ID = os.environ.get('SPREADSHEET_ID')
    GOOGLE_SHEETS_ENABLED = bool(GOOGLE_SHEETS_CREDENTIALS and SPREADSHEET_ID)
    
    # Email/SMTP
    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').lower() == 'true'
    MAIL_USE_SSL = os.environ.get('MAIL_USE_SSL', 'false').lower() == 'true'
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_FROM_NAME = os.environ.get('MAIL_FROM_NAME', 'Sistema de Chamados TI')
    MAIL_ENABLED = bool(MAIL_USERNAME and MAIL_PASSWORD)
    
    # Empresa
    COMPANY_NAME = os.environ.get('COMPANY_NAME', 'Sua Empresa')
    BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5000')
    # URL para acessos vindos da rede interna (ex: http://192.168.0.10:8080)
    INTERNAL_BASE_URL = os.environ.get('INTERNAL_BASE_URL', '')
    
    # Aplicação
    TICKETS_PER_PAGE = int(os.environ.get('TICKETS_PER_PAGE', 20))
    SESSION_TIMEOUT_HOURS = int(os.environ.get('SESSION_TIMEOUT_HOURS', 8))
    PASSWORD_RESET_TIMEOUT_SECONDS = int(os.environ.get('PASSWORD_RESET_TIMEOUT_SECONDS', 3600)) # 1 hora
    
    # Níveis de Usuário
    USER_LEVELS = {
        'admin': 'Administrador',
        'gestor': 'Gestor',
        'usuario': 'Usuário'
    }
    
    # Status de Tickets
    TICKET_STATUS = {
        'Aberto': 'warning',
        'Em Andamento': 'info',
        'Resolvido': 'success',
        'Fechado': 'secondary'
    }
    
    # Prioridades
    TICKET_PRIORITIES = ['Baixa', 'Média', 'Alta', 'Crítica']
    
    @staticmethod
    def validate():
        """Valida configurações essenciais"""
        errors = []
        warnings = []
        
        # Validar SECRET_KEY
        if Config.SECRET_KEY == 'dev-secret-key-change-in-production' and not Config.DEBUG:
            errors.append("SECRET_KEY não configurado para produção!")
        
        # Validar Google Sheets
        if not Config.GOOGLE_SHEETS_ENABLED:
            warnings.append("Google Sheets não configurado - usando dados padrão")
        
        # Validar Email
        if not Config.MAIL_ENABLED:
            warnings.append("Email não configurado - notificações desabilitadas")
        
        # Validar BASE_URL
        if Config.BASE_URL == 'http://localhost:5000' and not Config.DEBUG:
            errors.append("BASE_URL não configurado para produção!")
        
        return errors, warnings
    
    @staticmethod
    def get_summary():
        """Retorna resumo da configuração"""
        return {
            'debug': Config.DEBUG,
            'database': os.path.basename(Config.DATABASE_PATH),
            'google_sheets': 'Ativo' if Config.GOOGLE_SHEETS_ENABLED else 'Inativo',
            'email': 'Ativo' if Config.MAIL_ENABLED else 'Inativo',
            'company': Config.COMPANY_NAME,
            'base_url': Config.BASE_URL
        }


class DevelopmentConfig(Config):
    """Configurações para desenvolvimento"""
    DEBUG = True
    TESTING = False


class ProductionConfig(Config):
    """Configurações para produção"""
    DEBUG = False
    TESTING = False


class TestingConfig(Config):
    """Configurações para testes"""
    DEBUG = True
    TESTING = True
    DATABASE_PATH = ':memory:'  # SQLite em memória


# Selecionar configuração baseada no ambiente
config_by_name = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig
}

def get_config():
    """Retorna configuração baseada no ambiente"""
    env = os.environ.get('FLASK_ENV', 'production')
    return config_by_name.get(env, ProductionConfig)
