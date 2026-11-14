import logging
import json
from typing import Tuple
from datetime import datetime
import uuid

from modules.wms.models.avaria import Avaria
from modules.wms.services.wms_service import WmsAvariaService

logger = logging.getLogger(__name__)

class BrotherPrinterService:
    """
    Serviço de impressão usando FILA no Google Sheets.
    
    Funcionamento:
    1. Servidor (Railway/Linux) adiciona job na fila
    2. Cliente Windows local faz polling e imprime
    3. Cliente atualiza status do job
    
    Não requer SDK b-PAC no servidor!
    """
    
    def __init__(self, sheets_service, wms_service: WmsAvariaService):
        """
        Args:
            sheets_service: GoogleSheetsService para acessar fila
            wms_service: WmsAvariaService para atualizar status da avaria
        """
        self.sheets = sheets_service
        self.wms_service = wms_service
        self.queue_sheet_name = "FilaImpressao"
        self._ensure_queue_sheet()
    
    def _ensure_queue_sheet(self):
        """Garante que a aba FilaImpressao existe"""
        if not self.sheets.connected:
            logger.warning(" Google Sheets não conectado - fila de impressão indisponível")
            return
        
        try:
            self.sheets.sheet.worksheet(self.queue_sheet_name)
            logger.info(f"Aba '{self.queue_sheet_name}' existe")
        except:
            try:
                logger.info(f"⎘Criando aba '{self.queue_sheet_name}'...")
                headers = ["id", "tipo", "avaria_id", "dados_json", "status", "criado_em", "processado_em", "erro"]
                ws = self.sheets.sheet.add_worksheet(
                    title=self.queue_sheet_name, 
                    rows=1000, 
                    cols=len(headers)
                )
                ws.update('A1', [headers])
                logger.info(f"Aba '{self.queue_sheet_name}' criada")
            except Exception as e:
                logger.error(f"Erro ao criar aba de fila: {e}")
    
    def _add_print_job(self, tipo: str, avaria_id: str, dados: dict) -> Tuple[bool, str]:
        """
        Adiciona job de impressão na fila
        """
        if not self.sheets.connected:
            return False, "Sistema de impressão indisponível (sem conexão com Google Sheets)"
        
        try:
            job_id = str(uuid.uuid4())[:8]
            
            job_data = [
                job_id,
                tipo,
                avaria_id,
                json.dumps(dados, ensure_ascii=False),
                'pendente',
                datetime.now().isoformat(),
                '',  # processado_em
                ''   # erro
            ]
            
            ws = self.sheets.sheet.worksheet(self.queue_sheet_name)
            ws.append_row(job_data)
            
            logger.info(f"Job de impressão adicionado: {job_id} (tipo={tipo}, avaria={avaria_id})")
            return True, f"Enviado para impressão (Job #{job_id})"
            
        except Exception as e:
            logger.error(f"Erro ao adicionar job de impressão: {e}")
            return False, f"Erro ao enviar para impressão: {str(e)}"
    
    def print_etiqueta_inicial(self, avaria: Avaria) -> Tuple[bool, str]:
        """
        Envia etiqueta inicial para fila de impressão
        """
        id_etiqueta = avaria.id  # fallback seguro: só substituído se houver item ativo
        codprod = ''
        try:
            itens = self.wms_service.get_itens_avaria(avaria.id)
            if itens:
                # FIX: itens é uma lista de dicts
                item_ativo = next(
                    (i for i in itens if i.get('id_etiqueta', '') and i.get('status_etiqueta', '') == 'ativa'),
                    None
                )
                if item_ativo:
                    id_etiqueta = item_ativo.get('id_etiqueta')
                    codprod = item_ativo.get('codprod', '')
                else:
                    logger.warning(
                        f"⚠️ Nenhum item com status 'ativa' encontrado para avaria {avaria.id}. "
                        f"Tentando usar fallback."
                    )
                    fallback_item = next((i for i in itens if i.get('id_etiqueta', '')), None)
                    if fallback_item:
                        id_etiqueta = fallback_item.get('id_etiqueta')
                        codprod = fallback_item.get('codprod', '')
                        logger.info(f"Fallback inicial acionado: usou id_etiqueta {id_etiqueta}")
                    else:
                        logger.warning("Nenhum item com id_etiqueta. Usando avaria.id")
        except Exception as e:
            logger.warning(f"Não foi possível obter id_etiqueta dos itens: {e}. Usando avaria.id como fallback.")
        
        # CORREÇÃO: Usar avaria.nota em vez de avaria.nf_venda
        dados = {
            'txtNF': avaria.nota,
            'txtVolume': avaria.volume,
            'txtTransportadora': avaria.transportadora,
            'txtCTE': avaria.cte,
            'barcodeID': id_etiqueta,   # ← CORRIGIDO: ID da própria etiqueta
            'txtID': id_etiqueta,        # ← CORRIGIDO
            'txtCodProd': codprod,       # ← CORRIGIDO: Adicionado código do produto
            'template': 'layout_etiqueta_inicial.lbx'
        }
        
        success, message = self._add_print_job('inicial', avaria.id, dados)
        
        # ⁹⃥ PASSO CRÍTICO: ATUALIZAR STATUS NO SHEET
        if success:
            update_success, update_msg = self.wms_service.update_avaria(
                avaria.id, 
                etiqueta_inicial_impressa='sim'
            )
            if not update_success:
                logger.error(f"Falha ao atualizar status de impressão para avaria {avaria.id}: {update_msg}")
            
        return success, message
    
    def print_etiqueta_final(self, avaria: Avaria) -> Tuple[bool, str]:
        """
        Envia etiqueta final para fila de impressão
        """
        id_etiqueta = avaria.id  # fallback seguro: só substituído se houver item ativo
        codprod = ''
        try:
            itens = self.wms_service.get_itens_avaria(avaria.id)
            if itens:
                # FIX: itens é uma lista de dicts
                item_ativo = next(
                    (i for i in itens if i.get('id_etiqueta', '') and i.get('status_etiqueta', '') == 'ativa'),
                    None
                )
                if item_ativo:
                    id_etiqueta = item_ativo.get('id_etiqueta')
                    codprod = item_ativo.get('codprod', '')
                else:
                    logger.warning(
                        f"⚠️ Nenhum item com status 'ativa' encontrado para avaria {avaria.id}. "
                        f"Tentando usar fallback."
                    )
                    fallback_item = next((i for i in itens if i.get('id_etiqueta', '')), None)
                    if fallback_item:
                        id_etiqueta = fallback_item.get('id_etiqueta')
                        codprod = fallback_item.get('codprod', '')
                        logger.info(f"Fallback final acionado: usou id_etiqueta {id_etiqueta}")
                    else:
                        logger.warning("Nenhum item com id_etiqueta. Usando avaria.id")
        except Exception as e:
            logger.warning(f"Não foi possível obter id_etiqueta dos itens: {e}. Usando avaria.id como fallback.")        
        # CORREÇÃO: Usar avaria.nota em vez de avaria.nf_venda
        dados = {
            'txtNF': avaria.nota,
            'txtVolume': avaria.volume,
            'txtTransportadora': avaria.transportadora,
            'txtCTE': avaria.cte,
            'barcodeID': id_etiqueta,   # ← CORRIGIDO: ID da própria etiqueta
            'txtID': id_etiqueta,        # ← CORRIGIDO
            'txtCodProd': codprod,       # ← CORRIGIDO: Adicionado código do produto
            'txtNFD': avaria.nfd or 'N/A',
            'txtCodTriagem': avaria.cod_triagem or 'N/A',
            'txtReembolso': f"{avaria.reembolso_pct or 0}%%",
            'template': 'layout_etiqueta_final.lbx'
        }
        
        return self._add_print_job('final', avaria.id, dados)
        
    def get_queue_status(self) -> dict:
        """
        Retorna status da fila de impressão — cache de 30s.
        """
        import time as _time
        if not self.sheets.connected:
            return {'pendentes': 0, 'processados': 0, 'com_erro': 0, 'erro': 'Sem conexão'}
    
        # Fast path: cache ainda válido
        now = _time.time()
        if (self._queue_status_cache is not None and
                (now - self._queue_status_cache_ts) < self._queue_status_cache_ttl):
            return self._queue_status_cache
    
        try:
            ws = self.sheets.sheet.worksheet(self.queue_sheet_name)
            records = ws.get_all_records()
            
            result = {
                'pendentes':    len([r for r in records if r.get('status') == 'pendente']),
                'processados':  len([r for r in records if r.get('status') == 'processado']),
                'com_erro':     len([r for r in records if r.get('status') == 'erro']),
            }
            self._queue_status_cache = result
            self._queue_status_cache_ts = now
            return result
            
        except Exception as e:
            logger.error(f"Erro ao consultar status da fila: {e}")
            return {'pendentes': 0, 'processados': 0, 'com_erro': 0, 'erro': str(e)}
