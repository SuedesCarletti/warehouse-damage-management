# services/webhook_service.py

import requests
import json
import hmac
import hashlib
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class WebhookService:
    
    def __init__(self, sheets_service):
        self.sheets = sheets_service
    
    def trigger(self, evento, payload):
        """
        Dispara webhooks configurados para um evento
        
        Args:
            evento: Nome do evento (ex: 'avaria.criada')
            payload: Dados do evento
        """
        # Buscar webhooks ativos para este evento
        webhooks = self.get_webhooks_ativos(evento)
        
        for webhook in webhooks:
            try:
                # Preparar payload
                data = {
                    'evento': evento,
                    'timestamp': datetime.now().isoformat(),
                    'data': payload
                }
                
                # Enviar POST
                response = requests.post(
                    webhook['url'],
                    json=data,
                    headers={
                        'Content-Type': 'application/json',
                        'X-WMS-Signature': self.gerar_assinatura(data, webhook['secret'])
                    },
                    timeout=10
                )
                
                # Registrar log
                self.log_webhook(
                    webhook_id=webhook['id'],
                    evento=evento,
                    status_code=response.status_code,
                    sucesso=response.status_code == 200
                )
                
            except Exception as e:
                logger.error(f"Erro ao disparar webhook {webhook['id']}: {e}")
                self.log_webhook(
                    webhook_id=webhook['id'],
                    evento=evento,
                    erro=str(e),
                    sucesso=False
                )
    
    def gerar_assinatura(self, data, secret):
        """Gera assinatura HMAC para validação"""
        message = json.dumps(data, sort_keys=True)
        signature = hmac.new(
            secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()
        return signature
    
    def get_webhooks_ativos(self, evento):
        """Busca webhooks ativos no Sheets"""
        ws = self.sheets.sheet.worksheet('wms_webhooks')
        data = ws.get_all_records()
        
        return [
            w for w in data
            if w['ativo'] == 'sim' and evento in w['eventos'].split(',')
        ]
