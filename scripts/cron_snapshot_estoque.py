#!/usr/bin/env python3
"""
scripts/cron_snapshot_estoque.py

Script para ser executado via CRONTAB em servidores Linux.
Executar via Cron é a melhor prática para tarefas agendadas, garantindo
que rodem exatamente 1x, independente de quantos workers Gunicorn você tem.

Configuração no Linux (crontab -e):
0 23 * * * cd /workspaces/MultiSolucoes && python3 scripts/cron_snapshot_estoque.py >> logs/cron_estoque.log 2>&1
"""

import os
import sys
from datetime import datetime

# Adiciona o diretório raiz ao path do Python
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app

def run_snapshot():
    agora = datetime.now()
    print(f"[{agora.strftime('%Y-%m-%d %H:%M:%S')}] Iniciando rotina CRON de Snapshot de Estoque...")
    
    # Cria o contexto da aplicação Flask para acessar os serviços
    app = create_app()
    
    if hasattr(app, 'historico_estoque_service') and app.historico_estoque_service:
        data_hoje = agora.strftime('%d/%m/%Y')
        sucesso = app.historico_estoque_service.capturar_e_salvar_data(data_hoje)
        status = "SUCESSO" if sucesso else "FALHA/IGNORADO"
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Rotina finalizada. Status: {status}")
    else:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ERRO: historico_estoque_service não inicializado.")

if __name__ == '__main__':
    run_snapshot()
