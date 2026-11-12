"""
core/services/sankhya/service.py - VERSÃO 6.0 (ZERO COLD START)
=================================================================

PROBLEMA RESOLVIDO: Primeiro request demorava 36s porque cada requests.post()
criava uma NOVA conexão TCP+SSL (DNS + handshake + TLS = ~6-8s por call).

MUDANÇAS CRÍTICAS v6.0:
1. requests.Session() compartilhada (connection pooling/keep-alive)
2. Login integrado via session (mesma conexão para auth + queries)  
3. Warm-up REAL no startup (token + query leve = conexão pré-aquecida)
4. JOINs via entity path (ItemNota+Produto em 1 query)
5. includePresentationFields="N" (payload menor)
6. Queries parametrizadas (? ao invés de interpolação)

PERFORMANCE ESPERADA:
  ANTES:  1ª consulta ~36s (4 conexões TCP+SSL novas)
  DEPOIS: 1ª consulta ~6-8s (conexão já aquecida no startup)
  
  Subsequentes: 6-12s → 4-8s (JOINs + payload menor)
"""

import os
import logging
import requests
import time
import threading
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
import traceback

logger = logging.getLogger(__name__)


def parse_valor_sankhya(value, default: float = 0.0) -> float:
    """
    Converte um valor monetário/numérico vindo do Sankhya para float, tolerando
    formato BR (1.234,56) e US (1,234.56).

    Fonte única de verdade para esse parse: antes havia 3 implementações
    independentes (sankhya_avaria_service._safe_float, item_avaria._parse_valor_item
    e um passthrough sem parse nenhum em batch_import_service), o que já causou
    um ciclo de commits de conversão errada entre centavos e reais.
    """
    if value is None or (isinstance(value, str) and value.strip() == ''):
        return default
    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).replace('R$', '').strip()
    if not s:
        return default

    tem_virgula = ',' in s
    tem_ponto = '.' in s

    try:
        if tem_virgula and tem_ponto:
            if s.rfind(',') > s.rfind('.'):
                # Formato BR: 1.234,56
                s = s.replace('.', '').replace(',', '.')
            else:
                # Formato US: 1,234.56
                s = s.replace(',', '')
        elif tem_virgula:
            # Formato BR simples: 532,31
            s = s.replace(',', '.')
        return float(s)
    except (ValueError, TypeError):
        return default


class SankhyaService:
    """
    Service para integração com API Sankhya (VERSÃO 6.0 - ZERO COLD START)
    
    Otimizações:
    - requests.Session() com connection pooling (elimina DNS+TCP+SSL repetidos)
    - Token com TTL de 15 minutos (class-level, thread-safe)
    - Warm-up no startup (pré-aquece conexão HTTP + token)
    - JOINs via entity path (menos queries)
    - includePresentationFields="N" (payload menor)
    """
    
    # CLASS-LEVEL: Token + Session compartilhados entre todas as instâncias
    
    _shared_token = None
    _shared_token_timestamp = None
    _shared_token_ttl = 15 * 60  # 15 minutos
    _token_lock = threading.Lock()
    
    # v6.0: Session compartilhada (connection pooling)
    _shared_session = None
    _session_lock = threading.Lock()
    _warmed_up = False
    
    def __init__(self, base_url: str = None, token: str = None, appkey: str = None, codemp: str = None):
        """Inicializa serviço Sankhya"""
        self.base_url = base_url or os.environ.get('SANKHYA_BASE_URL')
        self.appkey = appkey or os.environ.get('SANKHYA_APPKEY')
        self.codemp = codemp or os.environ.get('SANKHYA_CODEMP')
        
        if token:
            self.token = token
        else:
            self.token = None
        
        if not self.base_url or not self.appkey:
            logger.warning("Sankhya não configurado (SANKHYA_BASE_URL ou SANKHYA_APPKEY)")
            self.enabled = False
        else:
            self.enabled = True
            logger.info(f"Sankhya Service v6.0 inicializado: {self.base_url}")
    
    # v6.0: SESSION MANAGEMENT (Connection Pooling)
    
    @classmethod
    def _get_session(cls) -> requests.Session:
        """
        ⚡ v6.0: Retorna session compartilhada com connection pooling.
        
        ANTES: requests.post() cria nova conexão TCP+SSL a cada call (~6-8s)
        DEPOIS: Session reutiliza conexão existente (~0.1-0.3s)
        """
        if cls._shared_session is None:
            with cls._session_lock:
                if cls._shared_session is None:
                    session = requests.Session()
                    # Configurar pool de conexões
                    adapter = requests.adapters.HTTPAdapter(
                        pool_connections=5,      # Máx conexões no pool
                        pool_maxsize=10,         # Máx conexões por host
                        max_retries=2,           # Retry automático
                        pool_block=False
                    )
                    session.mount('http://', adapter)
                    session.mount('https://', adapter)
                    cls._shared_session = session
                    logger.info("[Session] Pool de conexões HTTP criado")
        return cls._shared_session
    
    # v6.0: WARM-UP (pré-aquece conexão + token no startup)
    
    @classmethod
    def warm_up(cls, base_url: str = None, appkey: str = None):
        """
        ⚡ v6.0: Pré-aquece TUDO no startup da aplicação.
        
        Faz login + query leve usando a Session compartilhada.
        Resultado: quando o usuário fizer a primeira consulta,
        a conexão TCP+SSL JÁ ESTÁ estabelecida e o token JÁ EXISTE.
        
        ANTES: 1ª consulta do usuário = 36s (4x novo TCP+SSL + login)
        DEPOIS: 1ª consulta do usuário = 4-8s (conexão reusada)
        
        Chamar no app.py após SankhyaService() ser criado:
            if sankhya_service.enabled:
                SankhyaService.warm_up()
        """
        if cls._warmed_up:
            logger.info("[Warm-up] Já executado, pulando...")
            return True
        
        _base_url = base_url or os.environ.get('SANKHYA_BASE_URL')
        _appkey = appkey or os.environ.get('SANKHYA_APPKEY')
        
        if not _base_url or not _appkey:
            logger.warning("[Warm-up] Não configurado, pulando...")
            return False
        
        try:
            logger.info("[Warm-up] Iniciando pré-aquecimento...")
            start = time.time()
            
            # 1. Criar session e fazer login (estabelece TCP+SSL)
            session = cls._get_session()
            
            # 2. Login direto pela session (mesma conexão)
            from core.services.sankhya.config import load_config
            config = load_config()
            
            login_headers = {
                'token': config['SANKHYA_TOKEN'],
                'appkey': config['SANKHYA_APPKEY'],
                'username': config['SANKHYA_USERNAME'],
                'password': config['SANKHYA_PASSWORD']
            }
            
            login_url = f"{_base_url}/login"
            logger.info(f"[Warm-up] Login em {login_url}...")
            
            resp = session.post(login_url, headers=login_headers, timeout=30)
            resp.raise_for_status()
            
            bearer_token = resp.json().get('bearerToken')
            if not bearer_token:
                logger.error("[Warm-up] Login falhou - sem bearerToken")
                return False
            
            # Salvar token
            with cls._token_lock:
                cls._shared_token = bearer_token
                cls._shared_token_timestamp = time.time()
            
            login_time = time.time() - start
            logger.info(f"[Warm-up] Token obtido em {login_time:.1f}s")
            
            # 3. Query leve para "aquecer" a conexão com o endpoint de queries
            query_start = time.time()
            query_url = f"{_base_url}/gateway/v1/mge/service.sbr?serviceName=CRUDServiceProvider.loadRecords&outputType=json"
            
            warm_payload = {
                "serviceName": "CRUDServiceProvider.loadRecords",
                "requestBody": {
                    "dataSet": {
                        "rootEntity": "Empresa",
                        "includePresentationFields": "N",
                        "offsetPage": "0",
                        "entity": {
                            "fieldset": {"list": "CODEMP"}
                        },
                        "criteria": {
                            "expression": {"$": "CODEMP = ?"},
                            "parameter": [{"$": "0", "type": "I"}]
                        }
                    }
                }
            }
            
            warm_headers = {
                'Content-Type': 'application/json',
                'appkey': _appkey,
                'Authorization': f'Bearer {bearer_token}'
            }
            
            resp2 = session.post(query_url, json=warm_payload, headers=warm_headers, timeout=30)
            query_time = time.time() - query_start
            
            total_time = time.time() - start
            cls._warmed_up = True
            
            logger.info(f"[Warm-up] COMPLETO em {total_time:.1f}s (login: {login_time:.1f}s, query: {query_time:.1f}s)")
            logger.info(f"[Warm-up] Conexão HTTP pré-aquecida. Próximas queries serão ~1-2s cada.")
            return True
            
        except Exception as e:
            logger.error(f"[Warm-up] Erro (não-fatal): {e}")
            logger.warning("[Warm-up] Sistema funcionará normalmente, mas 1ª consulta será mais lenta")
            return False
    
    # TOKEN MANAGEMENT (v5.0 mantido + v6.0 usa session)
    
    def _get_valid_token(self) -> Optional[str]:
        """
        ⚡ Obtém token válido com reuso + session pooling.
        
        v6.0: Login usa a session compartilhada (mesma conexão TCP+SSL)
        """
        # 1. Check rápido sem lock
        if (SankhyaService._shared_token and 
            SankhyaService._shared_token_timestamp and
            (time.time() - SankhyaService._shared_token_timestamp) < SankhyaService._shared_token_ttl):
            return SankhyaService._shared_token
        
        # 2. Precisa renovar → lock
        with SankhyaService._token_lock:
            # Double-check
            if (SankhyaService._shared_token and 
                SankhyaService._shared_token_timestamp and
                (time.time() - SankhyaService._shared_token_timestamp) < SankhyaService._shared_token_ttl):
                return SankhyaService._shared_token
            
            # 3. v6.0: Login via session compartilhada
            try:
                logger.info("[Token Manager] Renovando token via session pooling...")
                start = time.time()
                
                session = SankhyaService._get_session()
                
                from core.services.sankhya.config import load_config
                config = load_config()
                
                login_headers = {
                    'token': config['SANKHYA_TOKEN'],
                    'appkey': config['SANKHYA_APPKEY'],
                    'username': config['SANKHYA_USERNAME'],
                    'password': config['SANKHYA_PASSWORD']
                }
                
                login_url = f"{self.base_url}/login"
                resp = session.post(login_url, headers=login_headers, timeout=30)
                resp.raise_for_status()
                
                new_token = resp.json().get('bearerToken')
                
                if new_token:
                    SankhyaService._shared_token = new_token
                    SankhyaService._shared_token_timestamp = time.time()
                    elapsed = time.time() - start
                    logger.info(f"[Token Manager] Token renovado em {elapsed:.1f}s (TTL: 15min)")
                    return new_token
                else:
                    logger.error("[Token Manager] Resposta sem bearerToken")
                    return None
                    
            except Exception as e:
                logger.error(f"[Token Manager] Erro na renovação: {e}")
                return None
    
    @classmethod
    def invalidate_token(cls):
        """Força invalidação do token (usar em caso de erro 401)"""
        with cls._token_lock:
            cls._shared_token = None
            cls._shared_token_timestamp = None
            logger.info("[Token Manager] Token invalidado manualmente")
    
    # MÉTODOS BASE HTTP (v6.0: usa session)
    
    def get_headers(self) -> Dict[str, str]:
        """Retorna headers para requisições"""
        headers = {
            'Content-Type': 'application/json',
            'appkey': self.appkey
        }
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        return headers
    
    def get_url(self, service_name: str) -> str:
        """Constrói URL para serviços MGE"""
        return f"{self.base_url}/gateway/v1/mge/service.sbr?serviceName={service_name}&outputType=json"
    
    def post_request(self, url: str, payload: Dict, timeout: int = 30) -> Optional[Dict]:
        """
        ⚡ v6.0: Usa session compartilhada (connection pooling).

        ANTES: requests.post() → nova conexão TCP+SSL cada vez (~6-8s overhead)
        DEPOIS: session.post() → reutiliza conexão existente (~0.1-0.3s overhead)
        """
        headers = self.get_headers()

        try:
            logger.debug(f"POST {url}")

            if not self.token:
                raise ValueError("Token de autenticação não disponível")

            # v6.0: Session com connection pooling
            session = SankhyaService._get_session()
            response = session.post(url, json=payload, headers=headers, timeout=timeout)
            
            response.raise_for_status()
            result = response.json()
            
            sankhya_status = result.get("status", "unknown")
            if sankhya_status != "1":
                status_msg = result.get("statusMessage", "Erro não especificado")
                logger.error(f"Erro no Sankhya: {status_msg}")
            
            return result
            
        except requests.exceptions.HTTPError as e:
            if hasattr(e, 'response') and e.response is not None and e.response.status_code == 401:
                logger.warning("Token expirado (401), invalidando...")
                SankhyaService.invalidate_token()
            logger.error(f"Erro HTTP: {e}")
            raise
        except requests.exceptions.Timeout:
            logger.error(f"⏰ Timeout na requisição para {url}")
            raise
        except Exception as e:
            logger.error(f"Erro na requisição: {e}")
            raise
    
    # LOAD RECORDS (v6.0: suporte a JOINs + parametrização)
    
    def load_records(self, entity: str, fields: str = None, criteria: Dict = None, 
                     limit: int = None, offset_page: int = None, 
                     paginate_all: bool = False,
                     joined_entities: List[Dict] = None) -> Optional[Dict]:
        """
        ⚡ v6.0: Suporte a JOINs via entity path.
        
        Args:
            entity: Nome da entidade raiz (ex: 'CabecalhoNota')
            fields: Campos da entidade principal (separados por vírgula)
            criteria: Critério de filtro
            limit: Máximo de registros
            offset_page: Página do resultado
            paginate_all: Se True, itera todas as páginas
            joined_entities: ⚡ NOVO! Lista de JOINs:
                [{"path": "Produto", "fields": "DESCRPROD,CODGRUPOPROD"}]
                
        Returns:
            Resposta da API ou None
        """
        token = self._get_valid_token()
        if not token:
            logger.error("Não foi possível obter token válido")
            return None
        
        self.token = token
        
        try:
            is_paginated_query = paginate_all and limit is None
            PAGE_LIMIT = 50
            page_limit = limit if limit is not None else (PAGE_LIMIT if is_paginated_query else None)
            current_offset_page = offset_page if offset_page is not None else 0
            
            all_entities = []
            
            while True:
                logger.debug(f"  > Requisitando {entity} | Página: {current_offset_page}")
                
                # v6.0: Montar payload otimizado
                dataset = {
                    "rootEntity": entity,
                    "includePresentationFields": "N",  # v6.0: payload menor
                    "offsetPage": str(current_offset_page)
                }
                
                if page_limit is not None:
                    dataset["limit"] = str(page_limit)
                
                # v6.0: Entity com JOINs
                if joined_entities:
                    # Modo JOIN: entity é um array de objetos
                    entity_array = []
                    
                    # Entidade principal
                    entity_array.append({
                        "path": "",
                        "fieldset": {"list": fields} if fields else {"list": "*"}
                    })
                    
                    # Entidades joined
                    for je in joined_entities:
                        entity_array.append({
                            "path": je["path"],
                            "fieldset": {"list": je["fields"]}
                        })
                    
                    dataset["entity"] = entity_array
                    dataset["tryJoinedFields"] = "true"
                else:
                    # Modo simples (compatível com v5.0)
                    if fields:
                        dataset["entity"] = {
                            "fieldset": {"list": fields}
                        }
                
                if criteria:
                    dataset["criteria"] = criteria
                
                payload = {
                    "serviceName": "CRUDServiceProvider.loadRecords",
                    "requestBody": {"dataSet": dataset}
                }
                
                # Fazer requisição (via session pooling)
                url = self.get_url("CRUDServiceProvider.loadRecords")
                resultado = self.post_request(url, payload)
                
                if resultado is None or resultado.get("status") != "1":
                    if is_paginated_query and all_entities:
                        logger.warning(f"Falha na página {current_offset_page}. Retornando parciais.")
                        break
                    else:
                        return resultado
                
                # v6.0: Suporte a dois formatos de resposta
                response_body = resultado.get("responseBody", {})
                
                # Formato entities (entity wrapper)
                entities_wrapper = response_body.get("entities", {})
                current_entities = entities_wrapper.get("entity", [])
                
                # v6.0: Formato alternativo (result array para JOINs)
                if not current_entities and "result" in response_body:
                    # Quando usa JOINs, resposta pode vir como "result" array
                    result_array = response_body.get("result", [])
                    if result_array:
                        # Converter result array para formato entity compatível
                        current_entities = self._convert_result_to_entities(result_array)
                        if not entities_wrapper:
                            entities_wrapper = {
                                "total": str(response_body.get("total", len(result_array))),
                                "entity": current_entities
                            }
                            resultado["responseBody"]["entities"] = entities_wrapper
                
                if current_entities and not isinstance(current_entities, list):
                    current_entities = [current_entities]
                
                if not is_paginated_query:
                    return resultado
                
                num_returned = len(current_entities)
                all_entities.extend(current_entities)
                
                logger.info(f"Paginação: {entity} - Página {current_offset_page} retornou {num_returned} registros.")
                
                if num_returned < PAGE_LIMIT:
                    break
                
                current_offset_page += 1
            
            return {
                "status": "1",
                "responseBody": {
                    "entities": {
                        "total": str(len(all_entities)),
                        "entity": all_entities
                    }
                }
            }
            
        except requests.exceptions.HTTPError as e:
            if hasattr(e, 'response') and e.response is not None and e.response.status_code == 401:
                logger.warning("Token expirou durante consulta, renovando...")
                SankhyaService.invalidate_token()
                token = self._get_valid_token()
                if token:
                    self.token = token
                    return self.post_request(
                        self.get_url("CRUDServiceProvider.loadRecords"),
                        payload
                    )
            raise
        except Exception as e:
            logger.error(f"Erro em load_records: {e}")
            raise
    
    # LOAD VIEW

    def load_view(self, view_name: str, fields: str, where: str = None) -> Optional[Dict]:
        """
        Consulta uma view Oracle via CRUDServiceProvider.loadView.
        """
        import json
        token = self._get_valid_token()
        if not token:
            logger.error("load_view: token indisponível")
            return None
        self.token = token

        url = self.get_url('CRUDServiceProvider.loadView')

        # Formatando os fields para loadView Sankhya API:
        field_list = [{"$": f.strip()} for f in fields.split(',') if f.strip()]

        query: Dict = {
            "viewName": view_name,
            "fields": {"field": field_list},
        }
        if where:
            query["where"] = {"$": where}

        payload = {
            "serviceName": "CRUDServiceProvider.loadView",
            "requestBody": {"query": query},
        }

        try:
            logger.info(f"[Sankhya loadView] Payload Enviado: {json.dumps(payload)}")
            response = self.post_request(url, payload, timeout=90)
            logger.info(f"[Sankhya loadView] Resposta Recebida: {json.dumps(response)}")
            return response
        except Exception as e:
            logger.error(f"load_view({view_name}): {e}")
            return None

    def _convert_result_to_entities(self, result_array: list) -> list:
        """
        Converte formato result[] (usado em JOINs) para formato entity[].
        
        result: [["val1", "val2", ...], [...]]
        → entity: [{"f0": {"$": "val1"}, "f1": {"$": "val2"}, ...}, ...]
        """
        entities = []
        for row in result_array:
            entity = {}
            if isinstance(row, list):
                for i, val in enumerate(row):
                    entity[f"f{i}"] = {"$": str(val) if val is not None else ""}
            entities.append(entity)
        return entities
    
    # EXTRAÇÃO DE VALORES
    
    def _extract_value(self, field_obj):
        """Extrai valor do objeto de campo do Sankhya"""
        if not field_obj:
            return ""
        if isinstance(field_obj, dict):
            return field_obj.get('$', '')
        return str(field_obj) if field_obj is not None else ""

    @staticmethod
    def parse_valor_sankhya(value, default: float = 0.0) -> float:
        """Atalho de instância para o parser monetário canônico (ver módulo)."""
        return parse_valor_sankhya(value, default)

    # HELPER: Critério parametrizado (v6.0)
    
    def _make_criteria(self, expression: str, params: list = None) -> Dict:
        """
        ⚡ v6.0: Cria critério parametrizado (mais seguro e potencialmente mais rápido).
        
        Uso:
            criteria = self._make_criteria(
                "this.NUMNOTA = ? AND this.CODEMP = ?",
                [{"value": "12345", "type": "I"}, {"value": "1", "type": "I"}]
            )
        """
        criteria = {"expression": {"$": expression}}
        
        if params:
            criteria["parameter"] = [
                {"$": str(p["value"]), "type": p.get("type", "S")}
                for p in params
            ]
        
        return criteria
    
    # MÉTODOS OTIMIZADOS PARA AVARIAS (v6.0)
    
    def buscar_notas_fiscais_list(self, numnota: str, codemp: str = None, 
                                   tipmov: str = None, limit: int = None) -> Optional[List[Dict]]:
        """Busca todas as notas que correspondem aos critérios."""
        try:
            logger.info(f"Buscando notas para NUMNOTA {numnota} (CODEMP: {codemp})...")
            
            # v6.0: Queries parametrizadas
            expr_parts = ["this.NUMNOTA = ?"]
            params = [{"value": numnota, "type": "I"}]
            
            if codemp:
                expr_parts.append("this.CODEMP = ?")
                params.append({"value": codemp, "type": "I"})
            if tipmov:
                expr_parts.append("this.TIPMOV = ?")
                params.append({"value": tipmov, "type": "S"})
            
            criteria = self._make_criteria(" AND ".join(expr_parts), params)
            fields = "NUNOTA,NUMNOTA,CODEMP,TIPMOV,DTNEG,QTDVOL,VOLUME,CODPARCTRANSP,AD_NFORIGEM,CODPARC,VLRNOTA,VLRDESTAQUE,VLRFRETE"
            
            paginate = True if limit is None else False
            resultado = self.load_records(
                entity='CabecalhoNota',
                fields=fields,
                criteria=criteria,
                limit=limit,
                paginate_all=paginate
            )
            
            if resultado and resultado.get("status") == "1":
                entities = resultado.get("responseBody", {}).get("entities", {})
                total = int(entities.get("total", "0"))
                
                if total > 0:
                    entity_list = entities.get("entity", [])
                    if not isinstance(entity_list, list):
                        entity_list = [entity_list]
                    
                    notas_encontradas = []
                    for entity in entity_list:
                        nota = {
                            'NUNOTA': self._extract_value(entity.get('f0')),
                            'NUMNOTA': self._extract_value(entity.get('f1')),
                            'CODEMP': self._extract_value(entity.get('f2')),
                            'TIPMOV': self._extract_value(entity.get('f3')),
                            'DTNEG': self._extract_value(entity.get('f4')),
                            'QTDVOL': self._extract_value(entity.get('f5')),
                            'VOLUME': self._extract_value(entity.get('f6')),
                            'CODPARCTRANSP': self._extract_value(entity.get('f7')),
                            'AD_NFORIGEM': self._extract_value(entity.get('f8')),
                            'CODPARC': self._extract_value(entity.get('f9')),
                            'VLRNOTA': self._extract_value(entity.get('f10')),
                            'VLRDESTAQUE': self._extract_value(entity.get('f11')),
                            'VLRFRETE': self._extract_value(entity.get('f12')),
                        }
                        notas_encontradas.append(nota)
                    
                    logger.info(f"{len(notas_encontradas)} nota(s) encontrada(s)")
                    return notas_encontradas
            
            logger.warning(f"Nota {numnota} não encontrada")
            return None
            
        except Exception as e:
            logger.error(f"Erro ao buscar notas fiscais: {e}")
            logger.debug(traceback.format_exc())
            return None

    def buscar_nota_fiscal(self, numnota: str, codemp: str = None, tipmov: str = None) -> Optional[Dict]:
        """Busca UMA nota fiscal (retorna apenas a primeira)"""
        try:
            notas = self.buscar_notas_fiscais_list(numnota, codemp, tipmov, limit=1)
            if notas:
                nota = notas[0]
                logger.info(f"Nota {numnota} encontrada: NUNOTA={nota['NUNOTA']}")
                return nota
            logger.warning(f"Nota {numnota} não encontrada")
            return None
        except Exception as e:
            logger.error(f"Erro ao buscar nota fiscal: {e}")
            logger.debug(traceback.format_exc())
            return None

    def buscar_nota_fiscal_por_nunota(self, nunota: str) -> Optional[Dict]:
        """Busca nota fiscal pelo NUNOTA (identificador único)."""
        try:
            logger.info(f"Buscando nota por NUNOTA {nunota}...")
            
            criteria = self._make_criteria("this.NUNOTA = ?", [{"value": nunota, "type": "I"}])
            fields = "NUNOTA,NUMNOTA,CODEMP,TIPMOV,DTNEG,QTDVOL,VOLUME,CODPARCTRANSP,AD_NFORIGEM,CODPARC,VLRNOTA,VLRDESTAQUE,VLRFRETE"
            
            resultado = self.load_records(
                entity='CabecalhoNota',
                fields=fields,
                criteria=criteria,
                limit=1
            )
            
            if resultado and resultado.get("status") == "1":
                entities = resultado.get("responseBody", {}).get("entities", {})
                total = int(entities.get("total", "0"))
                
                if total > 0:
                    entity_list = entities.get("entity", [])
                    if not isinstance(entity_list, list):
                        entity_list = [entity_list]
                    
                    entity = entity_list[0]
                    nota = {
                        'NUNOTA': self._extract_value(entity.get('f0')),
                        'NUMNOTA': self._extract_value(entity.get('f1')),
                        'CODEMP': self._extract_value(entity.get('f2')),
                        'TIPMOV': self._extract_value(entity.get('f3')),
                        'DTNEG': self._extract_value(entity.get('f4')),
                        'QTDVOL': self._extract_value(entity.get('f5')),
                        'VOLUME': self._extract_value(entity.get('f6')),
                        'CODPARCTRANSP': self._extract_value(entity.get('f7')),
                        'AD_NFORIGEM': self._extract_value(entity.get('f8')),
                        'CODPARC': self._extract_value(entity.get('f9')),
                        'VLRNOTA': self._extract_value(entity.get('f10')),
                        'VLRDESTAQUE': self._extract_value(entity.get('f11')),
                        'VLRFRETE': self._extract_value(entity.get('f12')),
                    }
                    logger.info(f"Nota NUNOTA {nunota} encontrada")
                    return nota
            
            logger.warning(f"Nota NUNOTA {nunota} não encontrada")
            return None
            
        except Exception as e:
            logger.error(f"Erro ao buscar nota por NUNOTA: {e}")
            logger.debug(traceback.format_exc())
            return None

    def buscar_itens_nota(self, nunota: str) -> Optional[List[Dict]]:
        """
        ⚡ v6.0: Busca itens + dados do produto em UMA ÚNICA query via JOIN.
        
        ANTES (v5.0): 2 queries — ItemNota + Produto batch (~4-6s)
        DEPOIS (v6.0): 1 query — ItemNota JOIN Produto (~2-3s)
        """
        try:
            logger.info(f"Buscando itens+produtos da nota NUNOTA {nunota} (JOIN)...")
            
            criteria = self._make_criteria("this.NUNOTA = ?", [{"value": nunota, "type": "I"}])
            
            # v6.0: JOIN com Produto na mesma query
            resultado = self.load_records(
                entity='ItemNota',
                fields="NUNOTA,SEQUENCIA,CODPROD,QTDNEG,CODLOCALORIG,CODVOL,VLRUNIT,VLRTOT,VLRDESC,PERCDESC,CODCFO,AD_VLRFRETEECOMMERCE,VLRSUBST,M3",
                criteria=criteria,
                paginate_all=True,
                joined_entities=[
                    {"path": "Produto", "fields": "DESCRPROD,CODGRUPOPROD"}
                ]
            )

            if resultado and resultado.get("status") == "1":
                entities = resultado.get("responseBody", {}).get("entities", {})
                total = int(entities.get("total", "0"))

                if total > 0:
                    entity_list = entities.get("entity", [])
                    if not isinstance(entity_list, list):
                        entity_list = [entity_list]

                    itens = []
                    for entity in entity_list:
                        item = {
                            'NUNOTA':                self._extract_value(entity.get('f0')),
                            'SEQUENCIA':             self._extract_value(entity.get('f1')),
                            'CODPROD':               self._extract_value(entity.get('f2')),
                            'QTDNEG':                self._extract_value(entity.get('f3')),
                            'CODLOCALORIG':          self._extract_value(entity.get('f4')),
                            'CODVOL':                self._extract_value(entity.get('f5')),
                            'VLRUNIT':               self._extract_value(entity.get('f6')),
                            'VLRTOT':                self._extract_value(entity.get('f7')),
                            'VLRDESC':               self._extract_value(entity.get('f8')),
                            'PERCDESC':              self._extract_value(entity.get('f9')),
                            'CODCFO':                self._extract_value(entity.get('f10')),
                            'AD_VLRFRETEECOMMERCE':  self._extract_value(entity.get('f11')),
                            'VLRSUBST':              self._extract_value(entity.get('f12')),
                            'M3':                    self._extract_value(entity.get('f13')),
                            'DESCRPROD':             self._extract_value(entity.get('f14')),
                            'CODGRUPOPROD':          self._extract_value(entity.get('f15')),
                        }              
                        itens.append(item)
                    
                    logger.info(f"{len(itens)} itens+produtos encontrados para NUNOTA {nunota}")
                    return itens
            
            logger.warning(f"Nenhum item encontrado para NUNOTA {nunota}")
            return []
            
        except Exception as e:
            # Fallback: se JOIN falhar, tentar sem JOIN (compatibilidade)
            logger.warning(f"JOIN falhou, tentando sem JOIN: {e}")
            return self._buscar_itens_nota_sem_join(nunota)

    def _buscar_itens_nota_sem_join(self, nunota: str) -> Optional[List[Dict]]:
        """Fallback: busca itens SEM join (compatibilidade com v5.0)"""
        try:
            criteria = self._make_criteria("this.NUNOTA = ?", [{"value": nunota, "type": "I"}])
            fields = "NUNOTA,SEQUENCIA,CODPROD,QTDNEG,CODLOCALORIG,CODVOL,VLRUNIT,VLRTOT,VLRDESC,PERCDESC,CODCFO,AD_VLRFRETEECOMMERCE,VLRSUBST,M3"

            resultado = self.load_records(
                entity='ItemNota',
                fields=fields,
                criteria=criteria,
                paginate_all=True
            )

            if resultado and resultado.get("status") == "1":
                entities = resultado.get("responseBody", {}).get("entities", {})
                total = int(entities.get("total", "0"))

                if total > 0:
                    entity_list = entities.get("entity", [])
                    if not isinstance(entity_list, list):
                        entity_list = [entity_list]

                    itens = []
                    for entity in entity_list:
                        item = {
                            'NUNOTA':                self._extract_value(entity.get('f0')),
                            'SEQUENCIA':             self._extract_value(entity.get('f1')),
                            'CODPROD':               self._extract_value(entity.get('f2')),
                            'QTDNEG':                self._extract_value(entity.get('f3')),
                            'CODLOCALORIG':          self._extract_value(entity.get('f4')),
                            'CODVOL':                self._extract_value(entity.get('f5')),
                            'VLRUNIT':               self._extract_value(entity.get('f6')),
                            'VLRTOT':                self._extract_value(entity.get('f7')),
                            'VLRDESC':               self._extract_value(entity.get('f8')),
                            'PERCDESC':              self._extract_value(entity.get('f9')),
                            'CODCFO':                self._extract_value(entity.get('f10')),
                            'AD_VLRFRETEECOMMERCE':  self._extract_value(entity.get('f11')),
                            'VLRSUBST':              self._extract_value(entity.get('f12')),
                            'M3':                    self._extract_value(entity.get('f13')),
                            'DESCRPROD':             '',
                            'CODGRUPOPROD':          '',
                        }
                        itens.append(item)
                    
                    logger.info(f"{len(itens)} itens encontrados (sem JOIN)")
                    return itens
            
            return []
        except Exception as e:
            logger.error(f"Erro no fallback buscar_itens: {e}")
            return []

    def buscar_produto(self, codprod: str) -> Optional[Dict]:
        """Busca dados de um produto por CODPROD."""
        try:
            criteria = self._make_criteria("this.CODPROD = ?", [{"value": codprod, "type": "I"}])
            fields = "CODPROD,DESCRPROD,CODGRUPOPROD"
            
            resultado = self.load_records(
                entity='Produto',
                fields=fields,
                criteria=criteria,
                limit=1
            )
            
            if resultado and resultado.get("status") == "1":
                entities = resultado.get("responseBody", {}).get("entities", {})
                total = int(entities.get("total", "0"))
                
                if total > 0:
                    entity_list = entities.get("entity", [])
                    if not isinstance(entity_list, list):
                        entity_list = [entity_list]
                    
                    entity = entity_list[0]
                    produto = {
                        'CODPROD': self._extract_value(entity.get('f0')),
                        'DESCRPROD': self._extract_value(entity.get('f1')),
                        'CODGRUPOPROD': self._extract_value(entity.get('f2'))
                    }
                    return produto
            
            return None
        except Exception as e:
            logger.error(f"Erro ao buscar produto {codprod}: {e}")
            return None

    def buscar_produtos_batch(self, codprods: List[str]) -> Dict[str, Dict]:
        """
        ⚡ Busca N produtos em UMA query via IN clause.
        
        Mantido como fallback para quando JOIN não funcionar.
        """
        if not codprods:
            return {}
        
        # Deduplicate
        unique_codprods = list(set([str(c) for c in codprods if c]))
        
        if not unique_codprods:
            return {}
        
        try:
            logger.info(f"Busca batch: {len(unique_codprods)} produtos...")
            
            in_clause = ", ".join(unique_codprods)
            criteria = {"expression": {"$": f"this.CODPROD IN ({in_clause})"}}
            fields = "CODPROD,DESCRPROD,CODGRUPOPROD"
            
            resultado = self.load_records(
                entity='Produto',
                fields=fields,
                criteria=criteria,
                paginate_all=True
            )
            
            produtos_map = {}
            
            if resultado and resultado.get("status") == "1":
                entities = resultado.get("responseBody", {}).get("entities", {})
                total = int(entities.get("total", "0"))
                
                if total > 0:
                    entity_list = entities.get("entity", [])
                    if not isinstance(entity_list, list):
                        entity_list = [entity_list]
                    
                    for entity in entity_list:
                        codprod = self._extract_value(entity.get('f0'))
                        if codprod:
                            produtos_map[codprod] = {
                                'CODPROD': codprod,
                                'DESCRPROD': self._extract_value(entity.get('f1')),
                                'CODGRUPOPROD': self._extract_value(entity.get('f2'))
                            }
                    
                    logger.info(f"{len(produtos_map)} produtos encontrados em batch")
            
            return produtos_map
            
        except Exception as e:
            logger.error(f"Erro no buscar_produtos_batch: {e}")
            return {}

    def buscar_dados_completos_nota(self, numnota: str, codemp: str = None,
                                     nunota: str = None, tipmov: str = None) -> Tuple[Optional[Dict], Optional[List[Dict]]]:
        """
        ⚡ v6.0: Busca cabeçalho + itens+produtos em MENOS queries.
        
        v5.0: 3 queries (cabeçalho + itens + produtos batch)
        v6.0: 2 queries (cabeçalho + itens JOIN produto)
        
        Com session pooling: ~4-6s total (vs 36s antes)
        """
        try:
            start = time.time()
            
            # 1. Buscar cabeçalho (~1-2s com session reuse)
            if nunota:
                logger.info(f"[v6.0] Buscando nota NUNOTA {nunota}...")
                cabecalho = self.buscar_nota_fiscal_por_nunota(nunota)
            else:
                logger.info(f"[v6.0] Buscando nota {numnota} (TIPMOV: {tipmov or 'auto'})...")
                # NUMNOTA não é único no Sankhya (quem é único é o NUNOTA) — sem o filtro de
                # TIPMOV, uma NF e uma NFD que coincidentemente compartilham o mesmo NUMNOTA
                # podem colidir e a query pega arbitrariamente a primeira que o banco retornar.
                cabecalho = self.buscar_nota_fiscal(numnota, codemp, tipmov=tipmov)
            
            if not cabecalho:
                return None, None
            
            nunota = cabecalho.get('NUNOTA')
            cab_time = time.time() - start
            
            # 2. v6.0: Buscar itens + produtos em UMA query (JOIN)
            itens_start = time.time()
            itens = self.buscar_itens_nota(nunota)
            
            if not itens:
                return cabecalho, []
            
            # Verificar se JOIN trouxe os dados do produto
            primeiro_item = itens[0] if itens else {}
            join_ok = bool(primeiro_item.get('DESCRPROD'))
            
            if not join_ok:
                # Fallback: buscar produtos em batch separado
                logger.info("JOIN sem DESCRPROD, usando batch fallback...")
                codprods = [item.get('CODPROD') for item in itens if item.get('CODPROD')]
                produtos_map = self.buscar_produtos_batch(codprods)
                
                for item in itens:
                    codprod = item.get('CODPROD', '')
                    produto = produtos_map.get(codprod, {})
                    item['DESCRPROD'] = produto.get('DESCRPROD', f'Produto {codprod}')
                    item['CODGRUPOPROD'] = produto.get('CODGRUPOPROD', '')
            
            total_time = time.time() - start
            itens_time = time.time() - itens_start
            
            queries_count = 2 if join_ok else 3
            logger.info(f"[v6.0] Nota completa em {total_time:.1f}s ({queries_count} queries) "
                       f"| Cabeçalho: {cab_time:.1f}s | Itens: {itens_time:.1f}s "
                       f"| JOIN: {'✅' if join_ok else '❌ (fallback batch)'}")
            
            return cabecalho, itens
            
        except Exception as e:
            logger.error(f"Erro ao buscar dados completos: {e}")
            logger.debug(traceback.format_exc())
            return None, None

    # BUSCA DE NOTA ORIGEM POR NFD
    
    def buscar_nota_origem_por_nfd(self, numnota_nfd: str, codemp: str = None) -> Optional[Dict]:
        """
        Busca a nota fiscal de origem a partir da NFD (nota de devolução).
        Fluxo: NFD → campo AD_NFORIGEM → busca NF de venda original
        """
        try:
            logger.info(f"Buscando NFD {numnota_nfd}...")
            nfd = self.buscar_nota_fiscal(numnota_nfd, codemp, tipmov='D')
            
            if not nfd:
                logger.warning(f"NFD {numnota_nfd} não encontrada")
                return None
            
            numnota_origem = nfd.get('AD_NFORIGEM')
            
            if not numnota_origem:
                logger.warning(f"NFD {numnota_nfd} sem campo AD_NFORIGEM preenchido")
                return None
            
            logger.info(f"Buscando NF origem {numnota_origem}...")
            nf_origem = self.buscar_nota_fiscal(numnota_origem, codemp, tipmov='V')
            
            if nf_origem:
                logger.info(f"NF origem {numnota_origem} encontrada")
            else:
                logger.warning(f"NF origem {numnota_origem} não encontrada")
            
            return nf_origem
            
        except Exception as e:
            logger.error(f"Erro ao buscar nota origem: {e}")
            logger.debug(traceback.format_exc())
            return None

    # BUSCA DE PARCEIRO / TRANSPORTADORA
    
    def buscar_parceiro(self, codparc: str) -> Optional[Dict]:
        """Busca informações do parceiro pelo código."""
        try:
            criteria = self._make_criteria("this.CODPARC = ?", [{"value": codparc, "type": "I"}])
            fields = "CODPARC,NOMEPARC,TIPPESSOA,CGC_CPF"
            
            resultado = self.load_records(
                entity='Parceiro',
                fields=fields,
                criteria=criteria,
                limit=1
            )
            
            if resultado and resultado.get("status") == "1":
                entities = resultado.get("responseBody", {}).get("entities", {})
                total = int(entities.get("total", "0"))
                
                if total > 0:
                    entity_list = entities.get("entity", [])
                    if not isinstance(entity_list, list):
                        entity_list = [entity_list]
                    
                    entity = entity_list[0]
                    parceiro = {
                        'CODPARC': self._extract_value(entity.get('f0')),
                        'NOMEPARC': self._extract_value(entity.get('f1')),
                        'TIPPESSOA': self._extract_value(entity.get('f2')),
                        'CGC_CPF': self._extract_value(entity.get('f3'))
                    }
                    logger.info(f"Parceiro {codparc} encontrado: {parceiro['NOMEPARC']}")
                    return parceiro
            
            logger.warning(f"Parceiro {codparc} não encontrado")
            return None
            
        except Exception as e:
            logger.error(f"Erro ao buscar parceiro: {e}")
            logger.debug(traceback.format_exc())
            return None

    def buscar_transportadora(self, codparc: str) -> Optional[Dict]:
        """Busca informações da transportadora (é um parceiro no Sankhya)."""
        return self.buscar_parceiro(codparc)
