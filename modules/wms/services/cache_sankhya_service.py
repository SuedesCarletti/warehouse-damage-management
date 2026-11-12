"""
cache_sankhya_service.py - VERSÃO 4.0
Service para cache de dados do Sankhya que mudam raramente
Otimiza performance evitando consultas repetidas ao ERP

VERSÃO 4.0 - NOVOS RECURSOS:
- ✅ Cache de Motivos de Devolução (AD_CADMOTDEV)
- ✅ Correção na visualização de locais na triagem (número completo)
- ✅ Mantém todas as funcionalidades anteriores

Dados cacheados:
- TGFLOC (Locais Financeiros) - TTL: 24 horas
- TGFPAR (Transportadoras) - TTL: 6 horas
- AD_CADMOTDEV (Motivos de Devolução) - TTL: 24 horas
"""

import logging
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class CacheSankhyaService:
    """
    Gerencia cache de dados do Sankhya que mudam raramente
    """
    
    def __init__(self, sankhya_service):
        """
        Inicializa service de cache
        
        Args:
            sankhya_service: Instância do SankhyaService
        """
        self.sankhya = sankhya_service
        
        # Cache de Locais Financeiros
        self._cache_locais = None
        self._cache_locais_timestamp = None
        self._cache_locais_ttl = 24 * 60 * 60  # 24 horas
        
        # Cache de Transportadoras
        self._cache_transportadoras = None
        self._cache_transportadoras_timestamp = None
        self._cache_transportadoras_ttl = 6 * 60 * 60  # 6 horas
        
        # Cache de Motivos de Devolução
        self._cache_motivos = None
        self._cache_motivos_timestamp = None
        self._cache_motivos_ttl = 24 * 60 * 60  # 24 horas
        
        logger.info("CacheSankhyaService v4.0 inicializado (com Motivos de Devolução)")
        
    # LOCAIS FINANCEIROS (TGFLOC)
    
    def obter_locais_financeiros(self, forcar_refresh: bool = False) -> Dict[int, Dict]:
        """
        Obtém locais financeiros com cache de 24 horas
        
        Args:
            forcar_refresh: Se True, ignora cache e busca no Sankhya
            
        Returns:
            Dicionário {codlocal: dados_local}
            Exemplo: {7: {'codlocal': 7, 'prefixo': '07', 'descrlocal': 'Estoque Disponível'}}
        """
        try:
            # Verificar cache
            if not forcar_refresh and self._cache_locais and self._cache_locais_timestamp:
                elapsed = time.time() - self._cache_locais_timestamp
                if elapsed < self._cache_locais_ttl:
                    tempo_restante = int((self._cache_locais_ttl - elapsed) / 60)
                    logger.debug(f"Cache locais ({tempo_restante} min restantes)")
                    return self._cache_locais
            
            # Buscar do Sankhya
            logger.info("Buscando TGFLOC do Sankhya...")
            
            locais_raw = self.sankhya.load_records(
                entity='LocalFinanceiro',
                fields='CODLOCAL,DESCRLOCAL,ATIVO',
                paginate_all=True 
            )
            
            # Acessar responseBody.entities
            entities_wrapper = locais_raw.get("responseBody", {}).get("entities", {})
            total = int(entities_wrapper.get("total", "0"))
            
            logger.info(f"Total de locais retornados: {total}")
            
            if total == 0:
                logger.warning("Nenhum local financeiro encontrado no Sankhya")
                return self._cache_locais or {}
            
            # Processar dados
            locais_processados = {}
            
            entities = entities_wrapper.get('entity', [])
            if not isinstance(entities, list):
                entities = [entities]
            
            logger.info(f"Processando {len(entities)} entidades...")
            
            for idx, entity in enumerate(entities):
                try:
                    # Extrair valores dos campos (f0, f1, f2)
                    codlocal = int(self._extract_value(entity.get('f0', {})))
                    descrlocal = self._extract_value(entity.get('f1', {}))
                    ativo = self._extract_value(entity.get('f2', {}))
                    
                    # Formatar prefixo com 2 dígitos
                    prefixo_raw = codlocal % 100 if codlocal > 99 else codlocal
                    prefixo = f"{prefixo_raw:02d}"
                    
                    locais_processados[codlocal] = {
                        'codlocal': codlocal,
                        'prefixo': prefixo,
                        'descrlocal': descrlocal,
                        'ativo': ativo
                    }
                    
                    logger.debug(f"  Local {idx+1}/{len(entities)}: {prefixo} - {descrlocal}")
                    
                except Exception as e:
                    logger.warning(f"Erro ao processar local {idx+1}: {e}")
                    continue
            
            # Atualizar cache
            self._cache_locais = locais_processados
            self._cache_locais_timestamp = time.time()
            
            logger.info(f"{len(locais_processados)} locais cacheados por 24h")
            
            return locais_processados
            
        except Exception as e:
            logger.error(f"Erro ao obter locais: {e}", exc_info=True)
            return self._cache_locais or {}

    def obter_local_por_codlocal(self, codlocal: int) -> Optional[Dict]:
        """
        Obtém informações completas do local
        
        Returns:
            Dict com codlocal, descrlocal, prefixo, etc.
        """
        try:
            locais = self.obter_locais_financeiros()
            return locais.get(codlocal)
        except Exception as e:
            logger.error(f"Erro ao buscar local {codlocal}: {e}")
            return None
    
    def validar_prefixo(self, prefixo: str) -> bool:
        """
        Valida se um prefixo existe no Sankhya
        
        Args:
            prefixo: Prefixo com 2 dígitos (ex: "07")
            
        Returns:
            True se válido, False caso contrário
        """
        try:
            # "00" é prefixo especial - sempre válido
            if prefixo == "00":
                logger.debug("Prefixo '00' (temporário) - válido")
                return True
            
            # Converter para int e validar
            try:
                codlocal = int(prefixo)
            except ValueError:
                logger.warning(f"Prefixo inválido (não numérico): {prefixo}")
                return False
            
            locais = self.obter_locais_financeiros()
            valido = codlocal in locais
            
            if valido:
                logger.debug(f"Prefixo '{prefixo}' válido: {locais[codlocal]['descrlocal']}")
            else:
                logger.warning(f"Prefixo '{prefixo}' não encontrado no Sankhya")
            
            return valido
            
        except Exception as e:
            logger.error(f"Erro ao validar prefixo: {e}")
            return False
    
    def obter_descricao_local(self, prefixo: str) -> str:
        """
        Obtém descrição de um local pelo prefixo
        
        Args:
            prefixo: Prefixo com 2 dígitos
            
        Returns:
            Descrição do local ou string vazia
        """
        try:
            # Prefixo 00 é temporário
            if prefixo == "00":
                return "Temporário (aguardando triagem)"
            
            codlocal = int(prefixo)
            locais = self.obter_locais_financeiros()
            local = locais.get(codlocal)
            
            if local:
                return local['descrlocal']
            
            return ""
            
        except Exception as e:
            logger.error(f"Erro ao obter descrição: {e}")
            return ""

    def obter_prefixo_por_codlocal(self, codlocal: int) -> Optional[str]:
        """
        Obtém prefixo de 2 dígitos dado o codlocal (inteiro), tratando 0 como '00'
        """
        if codlocal == 0:
            return "00"
        
        try:
            locais = self.obter_locais_financeiros()
            local = locais.get(codlocal)
            
            if local:
                return local['prefixo']
                
            return None
        except Exception as e:
            logger.error(f"Erro ao obter prefixo para codlocal {codlocal}: {e}")
            return None

    def listar_locais_para_selecao(self, apenas_estoque: bool = False) -> List[Dict]:
        """
        Lista locais formatados para uso em select/dropdown
        ✅ CORREÇÃO v4.0: Retorna número completo (ex: '407 - Estoque Disponível')
        
        Args:
            apenas_estoque: DEPRECATED - mantido para compatibilidade
            
        Returns:
            Lista de dicts com value e label
            Exemplo: [{'value': 407, 'label': '407 - Estoque Disponível'}, ...]
        """
        try:
            locais = self.obter_locais_financeiros()
            
            lista = []
            for codlocal, dados in sorted(locais.items()):
                # CORREÇÃO: Usar codlocal completo no label (407 em vez de 07)
                lista.append({
                    'value': codlocal,
                    'label': f"{codlocal} - {dados['descrlocal']}"
                })
            
            return lista
            
        except Exception as e:
            logger.error(f"Erro ao listar locais: {e}")
            return []
    
    # TRANSPORTADORAS (TGFPAR)
    
    def obter_transportadoras(self, forcar_refresh: bool = False) -> Dict[int, Dict]:
        """
        Obtém transportadoras com cache de 6 horas
        
        Args:
            forcar_refresh: Se True, ignora cache
            
        Returns:
            Dicionário {codparc: dados_transportadora}
        """
        try:
            # Verificar cache
            if not forcar_refresh and self._cache_transportadoras and self._cache_transportadoras_timestamp:
                elapsed = time.time() - self._cache_transportadoras_timestamp
                if elapsed < self._cache_transportadoras_ttl:
                    tempo_restante = int((self._cache_transportadoras_ttl - elapsed) / 60)
                    logger.debug(f"Cache transportadoras ({tempo_restante} min restantes)")
                    return self._cache_transportadoras
            
            # Buscar do Sankhya
            logger.info("Buscando transportadoras do Sankhya...")
            
            transportadoras_raw = self.sankhya.load_records(
                entity='Parceiro',
                fields='CODPARC,NOMEPARC,ATIVO,TRANSPORTADORA',
                criteria={
                    'expression': {
                        '$': 'this.TRANSPORTADORA = \'S\' AND this.ATIVO = \'S\''
                    }
                },
                paginate_all=True # <--- ADIÇÃO CONSISTENTE
            )
            
            # Acessar responseBody.entities
            entities_wrapper = transportadoras_raw.get("responseBody", {}).get("entities", {})
            total = int(entities_wrapper.get("total", "0"))
            
            logger.info(f"Total de transportadoras retornadas: {total}")
            
            if total == 0:
                logger.warning("Nenhuma transportadora encontrada")
                return self._cache_transportadoras or {}
            
            # Processar dados
            transportadoras_processadas = {}
            
            entities = entities_wrapper.get('entity', [])
            if not isinstance(entities, list):
                entities = [entities]
            
            for entity in entities:
                try:
                    codparc = int(self._extract_value(entity.get('f0', {})))
                    nomeparc = self._extract_value(entity.get('f1', {}))
                    ativo = self._extract_value(entity.get('f2', {}))
                    transportadora = self._extract_value(entity.get('f3', {}))
                    
                    transportadoras_processadas[codparc] = {
                        'codparc': codparc,
                        'nomeparc': nomeparc,
                        'ativo': ativo,
                        'transportadora': transportadora
                    }
                    
                except Exception as e:
                    logger.warning(f"Erro ao processar transportadora: {e}")
                    continue
            
            # Atualizar cache
            self._cache_transportadoras = transportadoras_processadas
            self._cache_transportadoras_timestamp = time.time()
            
            logger.info(f"{len(transportadoras_processadas)} transportadoras cacheadas por 6h")
            
            return transportadoras_processadas
            
        except Exception as e:
            logger.error(f"Erro ao obter transportadoras: {e}", exc_info=True)
            return self._cache_transportadoras or {}
    
    def listar_transportadoras_para_selecao(self) -> List[Dict]:
        """
        Lista transportadoras formatadas para select/dropdown
        
        Returns:
            Lista de dicts com value e label
        """
        try:
            transportadoras = self.obter_transportadoras()
            
            lista = []
            for codparc, dados in sorted(transportadoras.items(), key=lambda x: x[1]['nomeparc']):
                lista.append({
                    'value': codparc,
                    'label': dados['nomeparc']
                })
            
            return lista
            
        except Exception as e:
            logger.error(f"Erro ao listar transportadoras: {e}")
            return []
    
    # MOTIVOS DE DEVOLUÇÃO (AD_CADMOTDEV)
    
    def obter_motivos_devolucao(self, forcar_refresh: bool = False) -> Dict[int, Dict]:
        """
        Obtém motivos de devolução com cache de 24 horas
        
        Args:
            forcar_refresh: Se True, ignora cache e busca no Sankhya
            
        Returns:
            Dicionário {codmotdev: dados_motivo}
            Exemplo: {1: {'codmotdev': 1, 'descrmotdev': 'Produto Danificado'}}
        """
        try:
            # Verificar cache
            if not forcar_refresh and self._cache_motivos and self._cache_motivos_timestamp:
                elapsed = time.time() - self._cache_motivos_timestamp
                if elapsed < self._cache_motivos_ttl:
                    tempo_restante = int((self._cache_motivos_ttl - elapsed) / 60)
                    logger.debug(f"Cache motivos ({tempo_restante} min restantes)")
                    return self._cache_motivos
            
            # Buscar do Sankhya
            logger.info("Buscando AD_CADMOTDEV do Sankhya...")
            
            motivos_raw = self.sankhya.load_records(
                entity='AD_CADMOTDEV',
                fields='CODMOTDEV,DESCRMOTDEV',
                criteria=None,  # Busca todos os motivos
                paginate_all=True # <--- ADIÇÃO CRÍTICA
            )
            
            # Acessar responseBody.entities
            entities_wrapper = motivos_raw.get("responseBody", {}).get("entities", {})
            total = int(entities_wrapper.get("total", "0"))
            
            logger.info(f"Total de motivos retornados: {total}")
            
            if total == 0:
                logger.warning("Nenhum motivo de devolução encontrado no Sankhya")
                return self._cache_motivos or {}
            
            # Processar dados
            motivos_processados = {}
            
            entities = entities_wrapper.get('entity', [])
            if not isinstance(entities, list):
                entities = [entities]
            
            logger.info(f"Processando {len(entities)} motivos...")
            
            for idx, entity in enumerate(entities):
                try:
                    # Extrair valores dos campos (f0, f1)
                    codmotdev = int(self._extract_value(entity.get('f0', {})))
                    descrmotdev = self._extract_value(entity.get('f1', {}))
                    
                    motivos_processados[codmotdev] = {
                        'codmotdev': codmotdev,
                        'descrmotdev': descrmotdev
                    }
                    
                    logger.debug(f"  Motivo {idx+1}/{len(entities)}: {codmotdev} - {descrmotdev}")
                    
                except Exception as e:
                    logger.warning(f"Erro ao processar motivo {idx+1}: {e}")
                    continue
            
            # Atualizar cache
            self._cache_motivos = motivos_processados
            self._cache_motivos_timestamp = time.time()
            
            logger.info(f"{len(motivos_processados)} motivos cacheados por 24h")
            
            return motivos_processados
            
        except Exception as e:
            logger.error(f"Erro ao obter motivos de devolução: {e}", exc_info=True)
            return self._cache_motivos or {}
    
    def obter_motivo_por_codigo(self, codmotdev: int) -> Optional[Dict]:
        """
        Obtém informações completas de um motivo de devolução
        
        Args:
            codmotdev: Código do motivo
            
        Returns:
            Dict com codmotdev e descrmotdev ou None
        """
        try:
            motivos = self.obter_motivos_devolucao()
            return motivos.get(codmotdev)
        except Exception as e:
            logger.error(f"Erro ao buscar motivo {codmotdev}: {e}")
            return None
    
    def obter_descricao_motivo(self, codmotdev: int) -> str:
        """
        Obtém descrição de um motivo pelo código
        
        Args:
            codmotdev: Código do motivo
            
        Returns:
            Descrição do motivo ou string vazia
        """
        try:
            motivos = self.obter_motivos_devolucao()
            motivo = motivos.get(codmotdev)
            
            if motivo:
                return motivo['descrmotdev']
            
            return ""
            
        except Exception as e:
            logger.error(f"Erro ao obter descrição do motivo: {e}")
            return ""
    
    def listar_motivos_para_selecao(self) -> List[Dict]:
        """
        Lista motivos formatados para uso em select/dropdown
        
        Returns:
            Lista de dicts com value e label
            Exemplo: [{'value': 1, 'label': '1 - Produto Danificado'}, ...]
        """
        try:
            motivos = self.obter_motivos_devolucao()
            
            lista = []
            for codmotdev, dados in sorted(motivos.items()):
                lista.append({
                    'value': codmotdev,
                    'label': f"{codmotdev} - {dados['descrmotdev']}"
                })
            
            return lista
            
        except Exception as e:
            logger.error(f"Erro ao listar motivos: {e}")
            return []
    
    # UTILITÁRIOS
    
    def _extract_value(self, field_data):
        """
        Extrai valor de um campo da resposta Sankhya
        
        Args:
            field_data: Dados do campo (ex: {'$': 'valor'})
            
        Returns:
            Valor extraído ou None
        """
        if isinstance(field_data, dict):
            return field_data.get('$')
        return field_data
    
    def limpar_cache(self):
        """Limpa todo o cache forçando refresh na próxima consulta"""
        self._cache_locais = None
        self._cache_locais_timestamp = None
        self._cache_transportadoras = None
        self._cache_transportadoras_timestamp = None
        self._cache_motivos = None
        self._cache_motivos_timestamp = None
        logger.info("Cache limpo - próximas consultas buscarão do Sankhya")
