"""
scripts/setup_automacoes_padrao.py
===================================
Pré-cadastra as automações de status recomendadas no Google Sheets.

Executar UMA VEZ após o deploy, via Railway ou localmente:
    python scripts/setup_automacoes_padrao.py

Ou chamar programaticamente no bootstrap do app:
    from scripts.setup_automacoes_padrao import setup_automacoes_padrao
    setup_automacoes_padrao(sheets_service, empresa='Matriz')

AUTOMAÇÕES CRIADAS POR PADRÃO:
─────────────────────────────────────────────────────────────
Trigger                │ Condição         │ Altera      │ Status
───────────────────────┼──────────────────┼─────────────┼──────────────────────────
asn_recebido           │ —                │ estoque     │ Aguardando Triagem
asn_recusado           │ —                │ estoque     │ Recusado CD
baixa_total            │ tipo_baixa=total │ indenizacao │ Pagto Ok - Total
baixa_parcial          │ tipo_baixa=parcial│indenizacao │ Pagto Ok - Parcial
─────────────────────────────────────────────────────────────
"""

import sys
import os
import logging

logger = logging.getLogger(__name__)

# Automações padrão recomendadas
AUTOMACOES_PADRAO = [
    {
        'trigger':        'asn_recebido',
        'condicao_campo': '',
        'condicao_valor': '',
        'target_type':    'estoque',
        'novo_status':    'Aguardando Triagem',
        'descricao':      'ASN recebido → avaria inicia em Aguardando Triagem',
    },
    {
        'trigger':        'asn_recusado',
        'condicao_campo': '',
        'condicao_valor': '',
        'target_type':    'estoque',
        'novo_status':    'Recusado CD',
        'descricao':      'ASN recusado pelo operador → Recusado CD',
    },
    {
        'trigger':        'baixa_pagamento',
        'condicao_campo': 'indenizacao',
        'condicao_valor': 'total',
        'target_type':    'indenizacao',
        'novo_status':    'Pagto Ok - Total',
        'descricao':      'Baixa de pagamento com indenizacao=total → Pagto Ok - Total',
    },
    {
        'trigger':        'baixa_pagamento',
        'condicao_campo': 'indenizacao',
        'condicao_valor': 'parcial',
        'target_type':    'indenizacao',
        'novo_status':    'Pagto Ok - Parcial',
        'descricao':      'Baixa de pagamento com indenizacao=parcial → Pagto Ok - Parcial',
    },
]


def setup_automacoes_padrao(sheets_service, empresa: str = '', criado_por: str = 'setup'):
    """
    Verifica e cria as automações padrão caso não existam.
    Idempotente — não duplica se já existir trigger igual.
    """
    try:
        from modules.wms.services.automacao_status_service import AutomacaoStatusService
        svc = AutomacaoStatusService(sheets_service=sheets_service)

        existentes = svc.get_all(empresa=empresa)
        triggers_existentes = {a.get('trigger') for a in existentes}

        criadas  = 0
        puladas  = 0

        for auto in AUTOMACOES_PADRAO:
            trigger = auto['trigger']

            # Pular se já existe um registro para esse trigger na empresa
            if trigger in triggers_existentes:
                logger.info(f"  ⏭ Trigger '{trigger}' já existe — ignorando")
                puladas += 1
                continue

            success, msg = svc.criar(empresa, auto, criado_por)
            if success:
                logger.info(f"  Criada: {trigger} → {auto['novo_status']}")
                criadas += 1
            else:
                logger.warning(f"  Falha ao criar '{trigger}': {msg}")

        logger.info(f"\nSetup concluído: {criadas} criadas, {puladas} já existiam")
        return criadas, puladas

    except Exception as e:
        logger.error(f"setup_automacoes_padrao: {e}")
        raise


# ── Execução direta ──────────────────────────────────────────────────────────
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    # Adicionar root do projeto ao path
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

    try:
        from config.settings import get_sheets_config
        from core.sheets_service import SheetsService  # ajustar conforme o projeto

        empresa = os.environ.get('EMPRESA_PADRAO', '')
        logger.info(f"Iniciando setup de automações padrão (empresa='{empresa or 'todas'}')")

        sheets = SheetsService(**get_sheets_config())
        criadas, puladas = setup_automacoes_padrao(sheets, empresa=empresa)
        print(f"\nDone: {criadas} automações criadas, {puladas} já existiam.")

    except ImportError as e:
        print(f"\nNão foi possível importar serviços automaticamente: {e}")
        print("Execute este script dentro do contexto do app Flask ou chame")
        print("setup_automacoes_padrao(sheets_service, empresa='SuaEmpresa') manualmente.")
