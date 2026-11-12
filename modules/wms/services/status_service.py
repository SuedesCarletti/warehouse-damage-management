import logging
from typing import List, Tuple, Optional
from modules.wms.models.status import Status  # IMPORT ABSOLUTO CORRIGIDO
import time as _time


logger = logging.getLogger(__name__)

class StatusService:
    """Serviço para gerenciar Status de Avarias no Google Sheets"""
    
    def __init__(self, sheets_service):
        """
        Args:
            sheets_service: GoogleSheetsService instance
        """
        self.sheets = sheets_service
        self.sheet_name = "Status_Avarias"
        self._ensure_sheet()
        self._status_cache = {}           # { cache_key: (data, timestamp) }
        self._status_cache_ttl = 600      # 10 minutos
        self._status_cache_lock = __import__('threading').Lock()
    
    def _ensure_sheet(self):
        """Garante que a aba Status_Avarias existe e tem o novo cabeçalho 'status_type'"""
        if not self.sheets.connected:
            logger.warning(" Google Sheets não conectado - Status indisponível")
            return
        
        try:
            ws = self.sheets.sheet.worksheet(self.sheet_name)
            
            # ◡› CORREÇÃO DE CABEÇALHO (Garantir que 'status_type' existe)
            expected_headers = list(Status('temp', status_type='geral').to_dict().keys())
            current_headers = ws.row_values(1)
            
            if current_headers != expected_headers:
                logger.warning("Cabeçalho incorreto ou faltando 'status_type'. Tentando corrigir...")
                ws.update('A1', [expected_headers])
                logger.info("Cabeçalho atualizado.")

        except Exception as e:
            # Se a aba não existe, vamos criar
            if "No worksheet found" in str(e) or "worksheet" in str(e).lower():
                try:
                    logger.info("⌨Criando aba '{self.sheet_name}'...")
                    headers = list(Status('temp', status_type='geral').to_dict().keys())
                    ws = self.sheets.sheet.add_worksheet(
                        title=self.sheet_name,
                        rows=100,
                        cols=len(headers)
                    )
                    ws.update('A1', [headers])
                    
                    # Adicionar status padrões (AGORA COM TIPOS)
                    default_status = [
                        # Geral
                        ["Aguardando Triagem", "#ffc107", "sim", 1, "Avaria registrada, aguardando análise", "geral"],
                        ["Em Triagem", "#17a2b8", "sim", 2, "Avaria em processo de triagem", "geral"],
                        
                        # Estoque
                        ["Estoque Pendente", "#e74c3c", "sim", 10, "Aguardando entrada em estoque", "estoque"],
                        ["Estoque Disponível", "#28a745", "sim", 11, "Disponível para venda/reuso", "estoque"],
                        ["Estoque Descarte", "#6c757d", "sim", 12, "Enviado para descarte", "estoque"],
                        
                        # Indenização
                        ["Indenização Pendente", "#d35400", "sim", 20, "Aguardando definição de indenização", "indenizacao"],
                        ["Indenização Cobrada", "#3498db", "sim", 21, "Cobrança realizada ao responsável", "indenizacao"],
                        ["Indenização Finalizada", "#28a745", "sim", 22, "Processo de indenização concluído", "indenizacao"],
                        
                        # Finalização (Geral)
                        ["Finalizada", "#28a745", "sim", 99, "Processo de avaria concluído", "geral"]
                    ]
                    
                    ws.update(f'A2:F{len(default_status) + 1}', default_status)
                    logger.info(f"Aba '{self.sheet_name}' criada com status padrões (multi-tipo)")

                except Exception as create_e:
                    logger.error(f"Erro ao criar aba '{self.sheet_name}': {create_e}")
            else:
                 logger.error(f"Erro ao verificar aba '{self.sheet_name}': {e}")
    
    def get_all_status(self, include_inactive: bool = False, status_type=None):
        """
        Retorna todos os status, filtrando opcionalmente por tipo.
        ⚡ CACHE de 10 min com lock anti-stampede — 0 chamadas à API em regime normal.
    
        Invalidar cache após escrita: self._status_cache.clear()
        """
        cache_key = f"{include_inactive}:{status_type}"
        now = _time.time()
    
        # Fast path sem lock
        cached = self._status_cache.get(cache_key)
        if cached:
            data, ts = cached
            if (now - ts) < self._status_cache_ttl:
                return data
    
        # Cache frio: lock anti-stampede
        with self._status_cache_lock:
            # Double-check pós-lock
            cached = self._status_cache.get(cache_key)
            if cached:
                data, ts = cached
                if (_time.time() - ts) < self._status_cache_ttl:
                    return data
    
            # Leitura real — apenas 1 thread chega aqui por vez
            if not self.sheets.connected:
                return self._get_fallback_status(status_type)
    
            try:
                # Usa _get_sheet_data_cached do sheets service se disponível
                # (genérico, TTL configurável, já existente no sistema)
                if hasattr(self.sheets, '_get_sheet_data_cached'):
                    data_raw = self.sheets._get_sheet_data_cached(
                        self.sheet_name,
                        ttl=self._status_cache_ttl
                    )
                else:
                    ws = self.sheets.sheet.worksheet(self.sheet_name)
                    data_raw = ws.get_all_records()
    
                status_list = [Status.from_dict(row) for row in data_raw]
    
                if not include_inactive:
                    status_list = [s for s in status_list if s.ativo.lower() == 'sim']
    
                if status_type:
                    status_list = [s for s in status_list
                                   if s.status_type.lower() == status_type.lower()]
    
                status_list.sort(key=lambda x: x.ordem)
    
                logger.info(f"{len(status_list)} status (tipo: {status_type or 'todos'}) carregados")
    
                # Armazenar no cache local (filtragem por tipo/inactive é local, leitura é compartilhada)
                self._status_cache[cache_key] = (status_list, _time.time())
                return status_list
    
            except Exception as e:
                logger.error(f"Erro ao buscar status: {e}")
                return self._get_fallback_status(status_type)
    
    def get_status_by_name(self, name: str) -> Optional[Status]:
        # ... (implementation omitted for brevity) ...
        all_status = self.get_all_status(include_inactive=True)
        for status in all_status:
            if status.status.lower() == name.lower():
                return status
        return None
    
    def add_status(self, status: Status) -> Tuple[bool, str]:
        """
        Adiciona novo status
        """
        if not self.sheets.connected:
            return False, "Google Sheets não conectado"
        
        try:
            # Verificar duplicação
            existing = self.get_status_by_name(status.status)
            if existing:
                return False, f"Status '{status.status}' já existe"
            
            # Adicionar linha (agora com status_type)
            nova_linha = [
                status.status,
                status.cor,
                status.ativo,
                str(status.ordem),
                status.descricao,
                status.status_type # ◡›
            ]
            
            # Usar método de Sheets para garantir ordem/campos
            self.sheets.append_row(self.sheet_name, nova_linha)
            
            logger.info(f"Status '{status.status}' adicionado")
            return True, "Status adicionado com sucesso"
            
        except Exception as e:
            logger.error(f"Erro ao adicionar status: {e}")
            return False, str(e)
            
    def update_status(self, status_name: str, **updates) -> Tuple[bool, str]:
        """
        Atualiza dados de um status
        
        Args:
            status_name: Nome do status a atualizar
            **updates: Campos a atualizar
        
        Returns:
            Tuple (sucesso, mensagem)
        """
        if not self.sheets.connected:
            return False, "Google Sheets não conectado"
        
        try:
            ws = self.sheets.sheet.worksheet(self.sheet_name)
            
            # Encontrar linha do status
            all_data = ws.get_all_values()
            headers = all_data[0]
            status_col = headers.index('status') + 1
            
            status_row = None
            for idx, row in enumerate(all_data[1:], start=2):
                if row[status_col - 1].lower() == status_name.lower():
                    status_row = idx
                    break
            
            if not status_row:
                return False, "Status não encontrado"
            
            # Atualizar campos
            for field, value in updates.items():
                if field in headers:
                    col_idx = headers.index(field) + 1
                    ws.update_cell(status_row, col_idx, str(value))
            
            logger.info(f"Status '{status_name}' atualizado")
            return True, "Status atualizado com sucesso"
            
        except Exception as e:
            logger.error(f"Erro ao atualizar status: {e}")
            return False, str(e)
    
    def delete_status(self, status_name: str) -> Tuple[bool, str]:
        """
        Desativa status (soft delete)
        
        Args:
            status_name: Nome do status
        
        Returns:
            Tuple (sucesso, mensagem)
        """
        return self.update_status(status_name, ativo="nao")
    
    def reorder_status(self, status_order: List[str]) -> Tuple[bool, str]:
        """
        Reordena status
        
        Args:
            status_order: Lista com nomes dos status na ordem desejada
        
        Returns:
            Tuple (sucesso, mensagem)
        """
        try:
            for idx, status_name in enumerate(status_order, start=1):
                success, msg = self.update_status(status_name, ordem=idx)
                if not success:
                    return False, f"Erro ao reordenar '{status_name}': {msg}"
            
            logger.info("Status reordenados com sucesso")
            return True, "Status reordenados com sucesso"
            
        except Exception as e:
            logger.error(f"Erro ao reordenar status: {e}")
            return False, str(e)
    
    def _get_fallback_status(self, status_type: Optional[str] = None) -> List[Status]:
        """Status padrão quando Google Sheets não disponível"""
        logger.info("Usando status padrão (fallback)")
        default_status = [
            Status("Aguardando Triagem", "#ffc107", "sim", 1, "Avaria registrada, aguardando análise", "geral"),
            Status("Em Triagem", "#17a2b8", "sim", 2, "Avaria em processo de triagem", "geral"),
            Status("Finalizada", "#28a745", "sim", 99, "Processo de avaria concluído", "geral"),
            
            Status("Estoque Pendente", "#e74c3c", "sim", 10, "Aguardando entrada em estoque", "estoque"),
            Status("Estoque Disponível", "#28a745", "sim", 11, "Disponível para venda/reuso", "estoque"),
            Status("Estoque Descarte", "#6c757d", "sim", 12, "Enviado para descarte", "estoque"),
            
            Status("Indenização Pendente", "#d35400", "sim", 20, "Aguardando definição de indenização", "indenizacao"),
            Status("Indenização Cobrada", "#3498db", "sim", 21, "Cobrança realizada ao responsável", "indenizacao"),
            Status("Indenização Finalizada", "#28a745", "sim", 22, "Processo de indenização concluído", "indenizacao"),
        ]
        
        if status_type:
            return [s for s in default_status if s.status_type.lower() == status_type.lower()]
        
        return default_status
