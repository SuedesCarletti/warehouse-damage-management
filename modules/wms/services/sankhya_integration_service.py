import logging
import sys
import os
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)

# Importar serviço Sankhya existente
SANKHYA_AVAILABLE = False
try:
    # Ajustar importações para o novo local centralizado em core/services/sankhya
    from core.services.sankhya.service import SankhyaService
    from core.services.sankhya.auth import SankhyaAuth
    SANKHYA_AVAILABLE = True
    logger.info("Integração Sankhya disponível")
except ImportError as e:
    SANKHYA_AVAILABLE = False
    logger.warning(f" Sankhya service não disponível. Certifique-se de que core.services.sankhya existe: {e}")

# --- FIX: Importar modelos WMS para uso em type hints e criação de objetos ---
from modules.wms.models.item_nota import ItemNota, CabecalhoNota


class WmsSankhyaIntegrationService:
    """
    Serviço de integração entre WMS e Sankhya ERP
    Foca em operações de consulta de notas fiscais
    """
    
    def __init__(self, sankhya_token: Optional[str] = None):
        """
        Inicializa o serviço de integração
        
        Args:
            sankhya_token: Token de autenticação Sankhya (opcional)
        """
        self.sankhya_available = SANKHYA_AVAILABLE
        self.sankhya_service = None
        
        if SANKHYA_AVAILABLE:
            try:
                # CORREÇÃO: Se token não fornecido, tentar obter via autenticação
                if not sankhya_token:
                    logger.info("Tentando autenticar no Sankhya...")
                    auth = SankhyaAuth()
                    # Usa o método login() fornecido pelo usuário.
                    sankhya_token = auth.login()
                    
                    if sankhya_token:
                        logger.info("Autenticação Sankhya bem-sucedida")
                    else:
                        logger.warning(" Falha na autenticação: Token não obtido")
                
                # Inicializar serviço se token disponível
                if sankhya_token:
                    self.sankhya_service = SankhyaService(token=sankhya_token)
                    logger.info("SankhyaService inicializado")
                else:
                    logger.warning(" Token Sankhya não disponível")
                    
            except Exception as e:
                logger.error(f"Erro ao inicializar integração Sankhya: {e}")
                import traceback
                logger.error(traceback.format_exc())
                self.sankhya_service = None
                self.sankhya_available = False # Desativar se a inicialização falhar
    
    # ↺ RENOMEADO: buscar_nota_completa -> get_nota_completa
    def get_nota_completa(self, codemp: int, numnota: int) -> Optional[Dict]:
        """
        Busca nota completa (cabeçalho + itens) por CODEMP + NUMNOTA
        
        Args:
            codemp: Código da empresa
            numnota: Número da nota fiscal
            
        Returns:
            Dict com 'cabecalho', 'itens', 'valor_total' ou None se não encontrado
        """
        if not self.sankhya_service:
            logger.error("SankhyaService não disponível para buscar_nota_completa")
            return None
        
        try:
            logger.info(f"Buscando nota completa: CODEMP {codemp}, NUMNOTA {numnota}")
            
            # Buscar cabeçalho (USANDO O NOVO NOME DO MÉTODO)
            cabecalho = self.get_cabecalho_nota(codemp, numnota)
            if not cabecalho:
                logger.warning(f" Cabeçalho da nota {numnota} (empresa {codemp}) não encontrado")
                return None
            
            # Obter NUNOTA do objeto CabecalhoNota
            nunota = cabecalho.nunota
            
            # Buscar itens (USANDO O NOVO NOME DO MÉTODO)
            itens = self.get_itens_nota(nunota)
            
            # Calcular valor total dos itens
            valor_total_itens = sum(
                item.vlrtot_float for item in itens
            )
            
            resultado = {
                'cabecalho': cabecalho,
                'itens': itens,
                'valor_total': valor_total_itens,
                'total_itens': len(itens)
            }
            
            logger.info(f"Nota encontrada: NUNOTA {nunota}, {len(itens)} itens")
            return resultado
            
        except Exception as e:
            logger.error(f"Erro ao buscar nota completa CODEMP {codemp}, NUMNOTA {numnota}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    # ↺ RENOMEADO: buscar_cabecalho_nota -> get_cabecalho_nota
    def get_cabecalho_nota(self, codemp: int, numnota: int) -> Optional[CabecalhoNota]:
        """
        Busca cabeçalho da nota (TGFCAB) por CODEMP + NUMNOTA
        
        Args:
            codemp: Código da empresa
            numnota: Número da nota fiscal
            
        Returns:
            Objeto CabecalhoNota ou None se não encontrado
        """
        if not self.sankhya_service:
            logger.error("SankhyaService não disponível")
            return None
        
        # REMOVIDO: from modules.wms.models.item_nota import CabecalhoNota
        
        try:
            # Campos do cabeçalho
            campos = [
                "NUNOTA", "NUMNOTA", "CODEMP", "TIPMOV", "DTNEG", "DTALTER",
                "CODPARC", "NOMEPARC", "CODTIPOPER", "DESCRTIPOPER",
                "VLRNOTA", "VLRFRETE", "VLRDESC", "OBSERVACAO",
                "CODVEND", "NOMEVEND", "STATUSNOTA", "CODNAT",
                "DESCRNAT", "AD_PEDIDOWEB"
            ]
            
            criteria = {
                "expression": {
                    "$": f"this.CODEMP = {codemp} AND this.NUMNOTA = {numnota}"
                }
            }

            # CORREÇÃO: Filtrar por CODEMP E NUMNOTA
            resultado = self.sankhya_service.load_records(
                entity="Cabecalho",
                fields=",".join(campos),
                criteria=criteria,
                limit=1
            )
            
            if resultado.get("status") == "1":
                entities = resultado.get("responseBody", {}).get("entities", {})
                total = int(entities.get("total", "0"))
                
                if total > 0:
                    entity = entities.get("entity", {})
                    if isinstance(entity, list):
                        entity = entity[0]
                    
                    # Mapear campos
                    cabecalho_dict = {} 
                    for idx, campo in enumerate(campos):
                        # Nota: SankhyaService._extract_value já mapeia
                        valor = self.sankhya_service._extract_value(entity.get(f'f{idx}'))
                        cabecalho_dict[campo] = valor
                    
                    logger.info(f"Cabeçalho encontrado: NUNOTA {cabecalho_dict.get('NUNOTA')}, CODEMP {codemp}, NUMNOTA {numnota}")
                    
                    # ↺ Converter para o Modelo CabecalhoNota
                    return CabecalhoNota.from_sankhya_dict(cabecalho_dict)
                else:
                    logger.warning(f" Nenhum cabeçalho encontrado para CODEMP {codemp}, NUMNOTA {numnota}")
                    return None
            else:
                error_msg = resultado.get("statusMessage", "Erro desconhecido")
                logger.error(f"Erro ao buscar cabeçalho: {error_msg}")
                return None
                
        except Exception as e:
            logger.error(f"Erro ao buscar cabeçalho da nota CODEMP {codemp}, NUMNOTA {numnota}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    # ↺ RENOMEADO: buscar_itens_nota -> get_itens_nota
    def get_itens_nota(self, nunota: int) -> List[ItemNota]:
        """
        Busca itens da nota (TGFITE) por NUNOTA
        
        Args:
            nunota: Chave única da nota (NUNOTA)
            
        Returns:
            Lista de objetos ItemNota
        """
        if not self.sankhya_service:
            logger.error("SankhyaService não disponível")
            return []
        
        # REMOVIDO: from modules.wms.models.item_nota import ItemNota
        
        try:
            # Campos dos itens
            campos = [
                "NUNOTA", "SEQUENCIA", "CODPROD", "DESCRPROD",
                "QTDNEG", "CODVOL", "VLRUNIT", "VLRTOT", "VLRDESC",
                "PERCDESC", "CODLOCALORIG", "CONTROLE", "NUNFE",
                "AD_CODBARRAS"
            ]
            
            criteria = {
                "expression": {
                    "$": f"this.NUNOTA = {nunota}"
                }
            }

            resultado = self.sankhya_service.load_records(
                entity="ItemNota",
                fields=",".join(campos),
                criteria=criteria,
                limit=1000
            )
            
            if resultado.get("status") == "1":
                entities = resultado.get("responseBody", {}).get("entities", {})
                total = int(entities.get("total", "0"))
                
                if total == 0:
                    logger.warning(f" Nenhum item encontrado para NUNOTA {nunota}")
                    return []
                
                entity_list = entities.get("entity", [])
                if not isinstance(entity_list, list):
                    entity_list = [entity_list]
                
                itens_list = []
                for entity in entity_list:
                    item_dict = {}
                    for idx, campo in enumerate(campos):
                        valor = self.sankhya_service._extract_value(entity.get(f'f{idx}'))
                        item_dict[campo] = valor
                    
                    # ↺ Converter para o Modelo ItemNota
                    itens_list.append(ItemNota.from_sankhya_dict(item_dict))
                
                logger.info(f"{len(itens_list)} itens encontrados para NUNOTA {nunota}")
                return itens_list # RETORNA LISTA DE OBJETOS ItemNota
            else:
                error_msg = resultado.get("statusMessage", "Erro desconhecido")
                logger.error(f"Erro ao buscar itens: {error_msg}")
                return []
                
        except Exception as e:
            logger.error(f"Erro ao buscar itens da nota {nunota}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []
    
    # ↺ RENOMEADO: buscar_info_resumida_nota -> get_info_resumida_nota
    def get_info_resumida_nota(self, codemp: int, numnota: int) -> Dict:
        """
        Busca informações resumidas de uma nota para validação rápida
        
        Args:
            codemp: Código da empresa
            numnota: Número da nota fiscal
            
        Returns:
            Dict com informações básicas ou {"EXISTE": False} se não encontrado
        """
        if not self.sankhya_service:
            logger.error("SankhyaService não disponível")
            return {"EXISTE": False, "ERRO": "Serviço não disponível"}
        
        try:
            # Buscar apenas campos essenciais
            campos = ["NUNOTA", "NUMNOTA", "CODEMP", "NOMEPARC", "VLRNOTA", "DTNEG", "STATUSNOTA"]
            
            criteria = {
                "expression": {
                    "$": f"this.CODEMP = {codemp} AND this.NUMNOTA = {numnota}"
                }
            }

            # CORREÇÃO: Filtrar por CODEMP E NUMNOTA
            resultado = self.sankhya_service.load_records(
                entity="Cabecalho",
                fields=",".join(campos),
                criteria=criteria,
                limit=1
            )
            
            if resultado.get("status") == "1":
                entities = resultado.get("responseBody", {}).get("entities", {})
                total = int(entities.get("total", "0"))
                
                if total > 0:
                    entity = entities.get("entity", {})
                    if isinstance(entity, list):
                        entity = entity[0]
                    
                    info = {"EXISTE": True}
                    for idx, campo in enumerate(campos):
                        valor = self.sankhya_service._extract_value(entity.get(f'f{idx}'))
                        info[campo] = valor
                    
                    # Buscar quantidade de itens
                    itens_count = self._contar_itens_nota(info.get('NUNOTA'))
                    info['TOTAL_ITENS'] = itens_count
                    
                    logger.info(f"Info resumida: CODEMP {codemp}, NUMNOTA {numnota} existe (NUNOTA {info.get('NUNOTA')})")
                    return info
                else:
                    logger.warning(f" Nota não encontrada: CODEMP {codemp}, NUMNOTA {numnota}")
                    return {"EXISTE": False, "CODEMP": codemp, "NUMNOTA": numnota}
            else:
                error_msg = resultado.get("statusMessage", "Erro desconhecido")
                logger.error(f"Erro ao buscar info resumida: {error_msg}")
                return {"EXISTE": False, "ERRO": error_msg}
                
        except Exception as e:
            logger.error(f"Erro ao buscar info resumida CODEMP {codemp}, NUMNOTA {numnota}: {e}")
            return {"EXISTE": False, "ERRO": str(e)}
    
    def _contar_itens_nota(self, nunota: int) -> int:
        """
        Conta quantidade de itens de uma nota
        
        Args:
            nunota: Chave única da nota
            
        Returns:
            Quantidade de itens
        """
        try:
            resultado = self.sankhya_service.load_records(
                entity="ItemNota",
                fields="SEQUENCIA",
                criteria={
                    "expression": {
                        "$": f"this.NUNOTA = {nunota}"
                    }
                },
                limit=1000
            )
            
            if resultado.get("status") == "1":
                entities = resultado.get("responseBody", {}).get("entities", {})
                return int(entities.get("total", "0"))
            else:
                return 0
                
        except Exception:
            return 0
