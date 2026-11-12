import os
import sys
import logging
from datetime import datetime

# Adiciona o diretório raiz ao path para importar os módulos
sys.path.append(os.getcwd())

from core.services.google_sheets_service import GoogleSheetsService
from core.services.sankhya.service import SankhyaService
from modules.wms.services.wms_service import WmsAvariaService
from modules.wms.services.transportadoras_service import TransportadorasService

# Configuração de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def run_sync(empresa_alvo=None):
    logger.info("Iniciando script de sincronização de transportadoras...")
    
    # 1. Inicializa Serviços
    try:
        credentials_b64 = os.environ.get('GOOGLE_SHEETS_CREDENTIALS')
        spreadsheet_id = os.environ.get('SPREADSHEET_ID')
        
        if not credentials_b64 or not spreadsheet_id:
            logger.error("Variáveis de ambiente GOOGLE_SHEETS_CREDENTIALS ou SPREADSHEET_ID não configuradas.")
            return

        sheets_service = GoogleSheetsService(credentials_b64=credentials_b64, spreadsheet_id=spreadsheet_id)
        if not sheets_service.connected:
            logger.error("Falha ao conectar ao Google Sheets.")
            return
            
        wms_service = WmsAvariaService(sheets_service)
        sankhya_service = SankhyaService()
        
        if not sankhya_service.enabled:
            logger.error("Sankhya Service não está habilitado. Verifique as credenciais.")
            return
            
        transp_service = TransportadorasService(sheets_service, wms_service, sankhya_service)
        
        # 2. Executa Sincronização
        logger.info(f"Sincronizando transportadoras para a empresa: {empresa_alvo if empresa_alvo else 'TODAS'}...")
        
        # O método sincronizar_todas_notas_sankhya já faz a lógica de:
        # - Priorizar NFD
        # - Ignorar <SEM PARCEIRO> ou 0
        # - Manter nome da planilha se falhar ou se for 0
        # - Atualizar em lote no Sheets
        
        resultado = transp_service.sincronizar_todas_notas_sankhya(empresa_contexto=empresa_alvo or '')
        
        # 3. Relatório
        logger.info("=" * 50)
        logger.info("RELATÓRIO DE SINCRONIZAÇÃO")
        logger.info("=" * 50)
        if resultado.get('status') == 'success':
            logger.info(f"Processados: {resultado.get('processados')}")
            logger.info(f"Atualizados: {resultado.get('atualizados')}")
            logger.info(f"⏸ Mantidos:    {resultado.get('mantidos')}")
            logger.info(f"Erros:       {resultado.get('erros')}")
        else:
            logger.error(f"Falha no processo: {resultado.get('message')}")
        logger.info("=" * 50)

    except Exception as e:
        logger.error(f"Erro inesperado: {e}")
        import traceback
        logger.error(traceback.format_exc())

if __name__ == "__main__":
    # Se quiser rodar apenas para uma empresa específica, passe como argumento
    # Ex: python scripts/sync_transportadoras_sankhya.py Empresax
    empresa = sys.argv[1] if len(sys.argv) > 1 else None
    run_sync(empresa)
