#!/usr/bin/env python3
"""
Script para execução local do Sistema de Chamados TI
Execute: python run_local.py
"""

import os
import sys
import subprocess
from pathlib import Path

def install_dependencies():
    """Instala dependências se necessário"""
    try:
        import flask
        import gspread
        print("Dependências verificadas")
        return True
    except ImportError:
        print("Instalando dependências...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
            print("Dependências instaladas!")
            return True
        except Exception as e:
            print(f"Erro ao instalar dependências: {e}")
            return False

def load_env():
    """Carrega variáveis do arquivo .env"""
    env_file = Path('.env')
    
    if not env_file.exists():
        print(" Arquivo .env não encontrado!")
        print("Copie .env.example para .env e configure suas variáveis")
        
        # Criar .env básico
        with open('.env', 'w') as f:
            f.write("""# Configurações básicas - CONFIGURE ANTES DE USAR
SECRET_KEY=dev-key-change-in-production
FLASK_ENV=development

# Email - CONFIGURE COM SEUS DADOS
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=seu-email@gmail.com
MAIL_PASSWORD=sua-senha-de-app

# Google Sheets - EXECUTE setup_sheets.py
GOOGLE_SHEETS_CREDENTIALS=
SPREADSHEET_ID=

# Empresa
COMPANY_NAME=Sua Empresa

# Porta
PORT=5000
""")
        
        print("Arquivo .env criado - CONFIGURE ANTES DE CONTINUAR")
        return False
    
    # Carregar variáveis
    with open('.env', 'r') as f:
        for line in f:
            if '=' in line and not line.startswith('#'):
                key, value = line.strip().split('=', 1)
                os.environ[key] = value
    
    print("Variáveis de ambiente carregadas")
    return True

def check_configuration():
    """Verifica configuração básica"""
    required = [
        'SECRET_KEY',
        'MAIL_USERNAME', 
        'MAIL_PASSWORD',
        'GOOGLE_SHEETS_CREDENTIALS',
        'SPREADSHEET_ID'
    ]
    
    missing = []
    for var in required:
        if not os.environ.get(var):
            missing.append(var)
    
    if missing:
        print(f" Variáveis não configuradas: {', '.join(missing)}")
        print("Configure no arquivo .env ou execute:")
        print("   - python setup_sheets.py (para Google Sheets)")
        print("   - Configure email manualmente")
        return False
    
    print("Configuração básica verificada")
    return True

def main():
    """Função principal"""
    print("Sistema de Chamados TI - Execução Local")
    print("=" * 50)
    
    # Verificar dependências
    if not install_dependencies():
        return
    
    # Carregar configurações
    if not load_env():
        return
    
    # Verificar configuração
    config_ok = check_configuration()
    
    if not config_ok:
        print("\n Sistema não totalmente configurado")
        print("   O sistema funcionará com dados padrão")
        response = input("   Continuar mesmo assim? (s/N): ").lower()
        if response != 's':
            return
    
    print("\nIniciando servidor...")
    print("Acesse: http://localhost:5000")
    print("Pressione Ctrl+C para parar")
    print("=" * 50)
    
    # Definir ambiente de desenvolvimento
    os.environ['FLASK_ENV'] = 'development'
    
    # Importar e executar app
    try:
        from app import app
        port = int(os.environ.get('PORT', 5000))
        app.run(debug=os.environ.get('FLASK_DEBUG') == '1', host=os.environ.get('HOST', '127.0.0.1'), port=port)
    except KeyboardInterrupt:
        print("\nServidor parado pelo usuário")
    except Exception as e:
        print(f"\nErro ao iniciar servidor: {e}")

if __name__ == "__main__":
    main()
