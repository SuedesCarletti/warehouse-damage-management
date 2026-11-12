"""
modules/wms/services/config_service.py
Serviço de configurações WMS — Módulo 4 do Roadmap.

Gerencia 4 tipos de configuração, cada um em sua própria aba no Google Sheets:

  WMS_Config          → chave-valor genérico por empresa
  WMS_Param_Alertas   → alertas de prazo do dashboard
  WMS_Param_Indeniz   → regras de indenização por transportadora/tipo
  WMS_Param_Cobranca  → funil de cobrança (etapas de escalação)

Todos os métodos seguem o padrão já estabelecido no projeto:
  - Verificam self.sheets.connected antes de operar
  - Retornam (bool, str) para operações de escrita
  - Usam _ensure_sheet_exists() para criar abas automaticamente
  - Logam erros sem propagar exceções desnecessárias
"""

import uuid
import logging
import traceback
import threading as _threading
import time as _time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class WmsConfigService:

    # ── nomes das abas no Sheets ───────────────────────────────────────────
    SHEET_CONFIG   = 'WMS_Config'
    SHEET_ALERTAS  = 'WMS_Param_Alertas'
    SHEET_INDENIZ  = 'WMS_Param_Indeniz'
    SHEET_COBRANCA = 'WMS_Param_Cobranca'
    SHEET_TEMPLATES = 'WMS_EmailTemplates'  

    # ── cabeçalhos esperados em cada aba ──────────────────────────────────
    HEADERS = {
        'WMS_Config': [
            'empresa', 'chave', 'valor', 'descricao', 'categoria', 'atualizado_em', 'atualizado_por'
        ],
        'WMS_Param_Alertas': [
            'id', 'empresa', 'nome', 'campo_data', 'dias_limite',
            'cor_badge', 'mensagem', 'ativo', 'criado_em', 'criado_por',
            'tipo_alerta', 'campo_parar', 'status_parar', 'logica_parada', 'dias_maximo',
            'tela_destino'
        ],
        'WMS_Param_Indeniz': [
            'id', 'empresa', 'transportadora', 'tipo_avaria',
            'percentual_reemb', 'valor_max', 'prazo_dias',
            'ativo', 'criado_em', 'criado_por'
        ],
        'WMS_Param_Cobranca': [
            'id', 'empresa', 'nome_etapa', 'ordem', 'dias_apos_avaria',
            'acao', 'destinatario', 'template_email',
            'ativo', 'criado_em', 'criado_por'
        ],
        'WMS_EmailTemplates': [
            'id', 'empresa', 'nome', 'assunto', 'html_body',
            'variaveis', 'ativo', 'criado_em', 'criado_por',
            'atualizado_em', 'atualizado_por',
        ],      
    }

    def __init__(self, sheets_service):
        self.sheets = sheets_service
        self._read_cache: dict = {}          # { sheet_name: (headers, rows, timestamp) }
        self._read_cache_ttl: int = 600      # 10 minutos
        self._read_cache_lock = _threading.Lock()
        logger.info("WmsConfigService inicializado")

    def get_etiqueta_inicial_config(self, empresa: str) -> dict:
        """
        Retorna configuração completa de destino para validação de estoque
        de etiquetas iniciais.

        Lê duas chaves do WMS_Config:
          - 'etiqueta_inicial_codlocal_padrao'  → codlocal padrão para todos os produtos
                                                   (ex: '696')
          - 'etiqueta_inicial_excecoes_json'    → exceções por campo:valor da avaria
                                                   (ex: '{"status_indenizacao:Indenização": "497"}')

        Lógica de resolução (aplicada pelo EtiquetaService):
          1. Verifica cada exceção configurada contra os status da avaria
          2. Se alguma bater → usa o codlocal da exceção
          3. Se nenhuma bater → usa o codlocal padrão
          4. Se padrão também não configurado → pendência registrada sem destino

        Args:
            empresa: Nome/código do tenant (ex: 'Empresax', 'Teste')

        Returns:
            {
              'codlocal_padrao': '696',           # str ou '' se não configurado
              'excecoes': {                        # dict vazio se não configurado
                'status_indenizacao:Indenização': '497',
                'status_estoque:Bloqueado': '500',
              }
            }
        """
        resultado = {'codlocal_padrao': '', 'excecoes': {}}
        try:
            import json as _json
            configs = self.get_all_configs(empresa)

            # Campo 1 — local padrão (valor simples, ex: '696')
            padrao = configs.get('etiqueta_inicial_codlocal_padrao', '').strip()
            resultado['codlocal_padrao'] = padrao

            # Campo 2 — exceções por status (JSON, ex: {"status_indenizacao:Indenização": "497"})
            raw_exc = configs.get('etiqueta_inicial_excecoes_json', '').strip()
            if raw_exc:
                resultado['excecoes'] = _json.loads(raw_exc)

        except Exception as e:
            logger.warning(f'get_etiqueta_inicial_config falhou: {e}')
        return resultado

    def get_automacao_prioridades(self, empresa: str) -> dict:
        """
        Returns configured trigger priorities from WMS_Config.
        Key: 'automacao_prioridade_triggers' (JSON string).
        Example value: {"asn_recusado": 10, "asn_recebido": 5, "item_recusado": 10}
        Returns empty dict if not configured (caller uses hardcoded defaults).
        """
        try:
            import json as _json
            configs = self.get_all_configs(empresa)
            raw = configs.get('automacao_prioridade_triggers', '').strip()
            if raw:
                return _json.loads(raw)
        except Exception as e:
            logger.warning(f'get_automacao_prioridades falhou: {e}')
        return {}

    # HELPERS INTERNOS

    def _ensure_sheet(self, sheet_name: str):
        """Garante que a aba existe e retorna o objeto worksheet."""
        if not self.sheets or not self.sheets.connected:
            return None
        
        headers = self.HEADERS.get(sheet_name, [])
        
        try:
            return self.sheets.sheet.worksheet(sheet_name)
        except Exception:
            try:
                ws = self.sheets.sheet.add_worksheet(
                    title=sheet_name, rows=500, cols=max(len(headers), 20)
                )
                if headers:
                    ws.update('A1', [headers])
                logger.info(f"Aba '{sheet_name}' criada")
                return ws
            except Exception as e:
                if 'already exists' in str(e).lower():
                    logger.debug(f"Aba '{sheet_name}' já existe (race condition ignorada)")
                    try:
                        return self.sheets.sheet.worksheet(sheet_name)
                    except Exception:
                        return None
                else:
                    logger.error(f"Não foi possível criar aba '{sheet_name}': {e}")
                    return None
                  
    def _read_all(self, sheet_name: str, force_refresh: bool = False):
        """
        Lê todos os registros de uma aba como lista de dicts.
        ⚡ CACHE de 10 min com lock anti-stampede.
        Antes: chamava ws.get_all_values() diretamente a cada request.
        Depois: 0 chamadas à API enquanto cache estiver quente.

        Invalide após escrita: self._invalidate_config_cache(sheet_name)

        force_refresh: ignora o cache (local a este processo) e força leitura
        direta do Sheets. Necessário para configs que precisam valer
        imediatamente entre processos gunicorn distintos — a invalidação de
        self._invalidate_config_cache() só afeta o processo que escreveu.
        """
        now = _time.time()

        # Fast path sem lock
        if not force_refresh:
            cached = self._read_cache.get(sheet_name)
            if cached:
                headers, rows, ts = cached
                if (now - ts) < self._read_cache_ttl:
                    return headers, rows

        # Cache frio (ou refresh forçado): lock anti-stampede
        with self._read_cache_lock:
            # Double-check pós-lock
            if not force_refresh:
                cached = self._read_cache.get(sheet_name)
                if cached:
                    headers, rows, ts = cached
                    if (_time.time() - ts) < self._read_cache_ttl:
                        return headers, rows
    
            # Leitura real — apenas 1 thread chega aqui por vez
            ws = self._ensure_sheet(sheet_name)
            if not ws:
                return [], []
    
            try:
                all_values = ws.get_all_values()
                if not all_values or len(all_values) < 2:
                    headers = all_values[0] if all_values else []
                    self._read_cache[sheet_name] = (headers, [], _time.time())
                    return headers, []
    
                headers = all_values[0]
                rows = []
                for row in all_values[1:]:
                    if not any(str(v).strip() for v in row):
                        continue
                    record = {
                        headers[i]: str(row[i]).strip() if i < len(row) else ''
                        for i in range(len(headers))
                    }
                    rows.append(record)
    
                self._read_cache[sheet_name] = (headers, rows, _time.time())
                logger.debug(f"[ConfigCache] '{sheet_name}': {len(rows)} registros cacheados")
                return headers, rows
    
            except Exception as e:
                logger.error(f"_read_all({sheet_name}): {e}")
                return [], []

    def _invalidate_config_cache(self, sheet_name: str = None):
        """..."""
        if sheet_name:
            self._read_cache.pop(sheet_name, None)
            logger.debug(f"[ConfigCache] Cache invalidado: '{sheet_name}'")
        else:
            self._read_cache.clear()
            logger.debug("[ConfigCache] Todo o cache invalidado")

    def _new_id(self) -> str:          # ← 4 espaços de indentação (nível de classe)
        return str(uuid.uuid4())[:8]

    def _now(self) -> str:             # ← 4 espaços de indentação (nível de classe)
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # CONFIGS GERAIS (chave-valor)

    def get_all_configs(self, empresa: str, force_refresh: bool = False) -> Dict[str, str]:
        """
        Retorna dict {chave: valor} filtrado pela empresa.
        Faz merge: configs globais (empresa='*') são sobrescritas pelas da empresa.

        force_refresh: ver _read_all — use para configs que precisam valer
        imediatamente após serem salvas por outro processo gunicorn.
        """
        _, rows = self._read_all(self.SHEET_CONFIG, force_refresh=force_refresh)
        configs = {}
        # Primeiro, configs globais
        for row in rows:
            if row.get('empresa', '') in ('*', ''):
                configs[row['chave']] = row.get('valor', '')
        # Depois, configs específicas da empresa (sobrescrevem)
        for row in rows:
            if row.get('empresa', '') == empresa:
                configs[row['chave']] = row.get('valor', '')
        return configs

    def get_config(self, empresa: str, chave: str, default: str = '') -> str:
        """Retorna o valor de uma chave específica."""
        configs = self.get_all_configs(empresa)
        return configs.get(chave, default)

    def set_config(self, empresa: str, chave: str, valor: str, user_email: str = 'sistema') -> Tuple[bool, str]:
        """
        Atualiza ou cria uma chave de config para a empresa.
        Se a linha já existe (empresa+chave), atualiza o valor.
        Caso contrário, insere nova linha.
        """
        if not self.sheets or not self.sheets.connected:
            return False, "Sheets não conectado"
        ws = self._ensure_sheet(self.SHEET_CONFIG)
        if not ws:
            return False, "Aba não disponível"
        try:
            all_values = ws.get_all_values()
            if not all_values:
                return False, "Aba vazia"
            headers = all_values[0]
            now = self._now()

            # Procura linha existente
            for row_idx, row in enumerate(all_values[1:], start=2):
                row_empresa = str(row[0]).strip() if len(row) > 0 else ''
                row_chave   = str(row[1]).strip() if len(row) > 1 else ''
                if row_empresa == empresa and row_chave == chave:
                    # Atualiza colunas valor, atualizado_em, atualizado_por
                    col_valor = headers.index('valor') + 1 if 'valor' in headers else 3
                    col_ts    = headers.index('atualizado_em') + 1 if 'atualizado_em' in headers else 6
                    col_user  = headers.index('atualizado_por') + 1 if 'atualizado_por' in headers else 7
                    ws.update_cell(row_idx, col_valor, valor)
                    ws.update_cell(row_idx, col_ts, now)
                    ws.update_cell(row_idx, col_user, user_email)
                    logger.info(f"Config atualizada: {empresa}/{chave}")
                    return True, f"Config '{chave}' atualizada"

            # Linha não existe — insere nova
            ws.append_row([empresa, chave, valor, '', '', now, user_email])
            logger.info(f"Config criada: {empresa}/{chave}")
            return True, f"Config '{chave}' criada"

        except Exception as e:
            logger.error(f"set_config({empresa}/{chave}): {e}")
            logger.debug(traceback.format_exc())
            return False, str(e)

    def batch_set_configs(self, empresa: str, configs_dict: Dict[str, str], user_email: str = 'sistema') -> Tuple[int, int, List[str]]:
        """
        Salva múltiplas configs em UMA ÚNICA leitura + writes em batch.
        
        Returns:
            (salvos, erros, lista_de_erros)
        """
        if not self.sheets or not self.sheets.connected:
            return 0, len(configs_dict), ['Sheets não conectado']
    
        ws = self._ensure_sheet(self.SHEET_CONFIG)
        if not ws:
            return 0, len(configs_dict), ['Aba não disponível']
    
        try:
            all_values = ws.get_all_values()  # ← ÚNICA leitura
            if not all_values:
                return 0, len(configs_dict), ['Aba vazia']
    
            headers = all_values[0]
            now = self._now()
    
            col_empresa = 0
            col_chave   = 1
            try:
                col_valor = headers.index('valor')
                col_ts    = headers.index('atualizado_em')
                col_user  = headers.index('atualizado_por')
            except ValueError:
                col_valor, col_ts, col_user = 2, 5, 6
    
            # Mapeia (empresa, chave) → row_idx (1-based, linha real na sheet)
            existentes: Dict[tuple, int] = {}
            for row_idx, row in enumerate(all_values[1:], start=2):
                row_emp   = str(row[col_empresa]).strip() if len(row) > col_empresa else ''
                row_chave = str(row[col_chave]).strip()   if len(row) > col_chave   else ''
                existentes[(row_emp, row_chave)] = row_idx
    
            # Separa updates (batch_update_cells) de inserts (batch_append_rows)
            cell_updates = []  # [{range, values}]
            novas_linhas = []  # [lista de colunas]
            erros        = []
    
            for chave, valor in configs_dict.items():
                key = (empresa, chave)
                if key in existentes:
                    row_idx = existentes[key]
                    # Cria range no formato A1 para cada célula
                    def _col_letter(n):
                        result = ''
                        while n >= 0:
                            result = chr(n % 26 + 65) + result
                            n = n // 26 - 1
                        return result
    
                    cell_updates.extend([
                        {'range': f'{_col_letter(col_valor)}{row_idx}',   'values': [[str(valor)]]},
                        {'range': f'{_col_letter(col_ts)}{row_idx}',      'values': [[now]]},
                        {'range': f'{_col_letter(col_user)}{row_idx}',    'values': [[user_email]]},
                    ])
                else:
                    novas_linhas.append([empresa, chave, str(valor), '', '', now, user_email])
    
            salvos = 0
    
            # ── Executa updates em batch ──
            if cell_updates:
                try:
                    ws.batch_update(cell_updates)
                    salvos += len(cell_updates) // 3  # 3 cells por config
                    logger.info(f"batch_set_configs: {salvos} configs atualizadas em batch")
                    self._invalidate_config_cache(self.SHEET_CONFIG)
                except Exception as e:
                    erros.append(f'batch_update: {e}')
                    logger.error(f"batch_set_configs update: {e}")
    
            # ── Executa inserts em batch ──
            if novas_linhas:
                try:
                    ws.append_rows(novas_linhas, value_input_option='USER_ENTERED')
                    salvos += len(novas_linhas)
                    logger.info(f"batch_set_configs: {len(novas_linhas)} configs criadas em batch")
                    self._invalidate_config_cache(self.SHEET_CONFIG)
                except Exception as e:
                    erros.append(f'append_rows: {e}')
                    logger.error(f"batch_set_configs insert: {e}")
    
            return salvos, len(erros), erros
    
        except Exception as e:
            logger.error(f"batch_set_configs geral: {e}")
            return 0, len(configs_dict), [str(e)]  

    # ALERTAS DO DASHBOARD

    def get_alertas(self, empresa: str) -> List[dict]:
        """Retorna todos os alertas da empresa (ativos e inativos)."""
        _, rows = self._read_all(self.SHEET_ALERTAS)
        return [r for r in rows if r.get('empresa') == empresa]

    def get_alertas_ativos(self, empresa: str) -> List[dict]:
        """
        Retorna apenas alertas ativos — usado pelo dashboard para avaliar avarias.

        Cada alerta tem:
          campo_data  → campo da avaria a comparar com hoje
          dias_limite → quantos dias de diferença dispara o alerta
          cor_badge   → classe Bootstrap (danger, warning, info...)
          mensagem    → texto do tooltip/badge
        """
        return [a for a in self.get_alertas(empresa) if a.get('ativo', 'sim') == 'sim']

    def add_alerta(self, empresa: str, data: dict, user_email: str) -> Tuple[bool, str]:
        """Cria novo alerta de prazo."""
        if not self.sheets or not self.sheets.connected:
            return False, "Sheets não conectado"
        ws = self._ensure_sheet(self.SHEET_ALERTAS)
        if not ws:
            return False, "Aba não disponível"

        # Validação mínima
        if not data.get('campo_data') or not data.get('dias_limite'):
            return False, "Campo de data e dias limite são obrigatórios"

        try:
          row = [
              self._new_id(),                                          # A - id
              empresa,                                                 # B - empresa
              data.get('nome', f"Alerta {data['campo_data']}"),       # C - nome
              data.get('campo_data', ''),                              # D - campo_data
              str(data.get('dias_limite', 15)),                        # E - dias_limite
              data.get('cor_badge', 'warning'),                        # F - cor_badge
              data.get('mensagem', ''),                                # G - mensagem
              'sim',                                                   # H - ativo
              self._now(),                                             # I - criado_em
              user_email,                                              # J - criado_por
              data.get('tipo_alerta', 'atraso'),                       # K - tipo_alerta (atraso|vencimento)
              data.get('campo_parar', ''),                             # L - campo que sinaliza resolução
              data.get('status_parar', ''),                            # M - CSV de status que param
              data.get('logica_parada', 'E'),                          # N - E = campo AND status | OU = qualquer
              str(data.get('dias_maximo', 0)),                         # O - 0=sem limite de tempo
          ]
          ws.append_row(row)
          logger.info(f"Alerta criado para {empresa}: {data.get('nome')}")
          self._invalidate_config_cache(self.SHEET_ALERTAS)  # ← adicionar
          return True, "Alerta criado com sucesso"
        except Exception as e:
            logger.error(f"add_alerta: {e}")
            return False, str(e)

    def update_alerta(self, empresa: str, alerta_id: str, data: dict) -> Tuple[bool, str]:
        """Atualiza campos de um alerta."""
        if not self.sheets or not self.sheets.connected:
            return False, "Sheets não conectado"
        ws = self._ensure_sheet(self.SHEET_ALERTAS)
        if not ws:
            return False, "Aba não disponível"
        try:
            all_values = ws.get_all_values()
            for row_idx, row in enumerate(all_values[1:], start=2):
                if str(row[0]).strip() == alerta_id and str(row[1]).strip() == empresa:
                    def _safe_row(r, idx, default=''):
                        return r[idx] if len(r) > idx else default
                    
                    ws.update(f'C{row_idx}:O{row_idx}', [[
                        data.get('nome',          _safe_row(row, 2)),
                        data.get('campo_data',    _safe_row(row, 3)),
                        str(data.get('dias_limite', _safe_row(row, 4, '15'))),
                        data.get('cor_badge',     _safe_row(row, 5, 'warning')),
                        data.get('mensagem',      _safe_row(row, 6)),
                        data.get('ativo',         _safe_row(row, 7, 'sim')),
                        # cols I, J mantidas (criado_em / criado_por) — pula para K via range
                        # ATENÇÃO: o range C→O inclui I e J; use os valores originais para não sobrescrever
                        _safe_row(row, 8),        # I - criado_em (preserva)
                        _safe_row(row, 9),        # J - criado_por (preserva)
                        data.get('tipo_alerta',   _safe_row(row, 10, 'atraso')),
                        data.get('campo_parar',   _safe_row(row, 11)),
                        data.get('status_parar',  _safe_row(row, 12)),
                        data.get('logica_parada', _safe_row(row, 13, 'E')),
                        str(data.get('dias_maximo', _safe_row(row, 14, '0'))),
                    ]])
                    self._invalidate_config_cache(self.SHEET_ALERTAS)  # ← adicionar
                    return True, "Alerta atualizado"
            return False, "Alerta não encontrado"
        except Exception as e:
            logger.error(f"update_alerta: {e}")
            return False, str(e)

    def delete_alerta(self, empresa: str, alerta_id: str) -> Tuple[bool, str]:
        """Remove um alerta (limpa a linha)."""
        self._invalidate_config_cache(self.SHEET_ALERTAS)  # ← adicionar
        return self._delete_row(self.SHEET_ALERTAS, empresa, alerta_id)

    # REGRAS DE INDENIZAÇÃO

    def get_regras_indenizacao(self, empresa: str) -> List[dict]:
        """Retorna todas as regras de indenização da empresa."""
        _, rows = self._read_all(self.SHEET_INDENIZ)
        return [r for r in rows if r.get('empresa') == empresa]

    def add_regra_indenizacao(self, empresa: str, data: dict, user_email: str) -> Tuple[bool, str]:
        """
        Cria regra de indenização.

        Exemplos de regras:
          transportadora='*', tipo_avaria='Quebra', percentual=80, valor_max=500
          transportadora='Jadlog', tipo_avaria='*', percentual=100, prazo_dias=30
        """
        if not self.sheets or not self.sheets.connected:
            return False, "Sheets não conectado"
        ws = self._ensure_sheet(self.SHEET_INDENIZ)
        if not ws:
            return False, "Aba não disponível"

        if not data.get('percentual_reemb'):
            return False, "Percentual de reembolso é obrigatório"

        try:
            row = [
                self._new_id(),
                empresa,
                data.get('transportadora', '*'),
                data.get('tipo_avaria', '*'),
                str(data.get('percentual_reemb', 0)),
                str(data.get('valor_max', '')),
                str(data.get('prazo_dias', 30)),
                'sim',
                self._now(),
                user_email,
            ]
            ws.append_row(row)
            logger.info(f"Regra indenização criada para {empresa}")
            return True, "Regra de indenização criada com sucesso"
        except Exception as e:
            logger.error(f"add_regra_indenizacao: {e}")
            return False, str(e)

    def update_regra_indenizacao(self, empresa: str, regra_id: str, data: dict) -> Tuple[bool, str]:
        """Atualiza uma regra de indenização."""
        if not self.sheets or not self.sheets.connected:
            return False, "Sheets não conectado"
        ws = self._ensure_sheet(self.SHEET_INDENIZ)
        if not ws:
            return False, "Aba não disponível"
        try:
            all_values = ws.get_all_values()
            for row_idx, row in enumerate(all_values[1:], start=2):
                if str(row[0]).strip() == regra_id and str(row[1]).strip() == empresa:
                    ws.update(f'C{row_idx}:H{row_idx}', [[
                        data.get('nome', row[2]),
                        data.get('campo_data', row[3]),
                        str(data.get('dias_limite', row[4])),
                        data.get('cor_badge', row[5]),
                        data.get('mensagem', row[6]),
                        data.get('ativo', row[7]),
                    ]])
                    # Atualiza campos extras (colunas K, L, M — índices 10, 11, 12)
                    if 'tipo_alerta' in data or 'status_ignorar' in data or 'dias_maximo' in data:
                        tipo   = data.get('tipo_alerta',  row[10] if len(row) > 10 else 'atraso')
                        status = data.get('status_ignorar', row[11] if len(row) > 11 else '')
                        dias_m = str(data.get('dias_maximo', row[12] if len(row) > 12 else 0))
                        ws.update(f'K{row_idx}:M{row_idx}', [[tipo, status, dias_m]])
                    return True, "Alerta atualizado"
                    return True, "Regra atualizada"
            return False, "Regra não encontrada"
        except Exception as e:
            logger.error(f"update_regra_indenizacao: {e}")
            return False, str(e)

    def delete_regra_indenizacao(self, empresa: str, regra_id: str) -> Tuple[bool, str]:
        """Remove uma regra de indenização."""
        return self._delete_row(self.SHEET_INDENIZ, empresa, regra_id)

    # FUNIL DE COBRANÇA

    def get_etapas_cobranca(self, empresa: str) -> List[dict]:
        """Retorna etapas do funil de cobrança ordenadas por 'ordem'."""
        _, rows = self._read_all(self.SHEET_COBRANCA)
        etapas = [r for r in rows if r.get('empresa') == empresa]
        # Ordena por campo 'ordem' numericamente
        try:
            etapas.sort(key=lambda x: int(x.get('ordem', 99)))
        except (ValueError, TypeError):
            pass
        return etapas

    def get_etapas_ativas(self, empresa: str) -> List[dict]:
        """Retorna apenas etapas ativas — usado pelo agendador de cobrança."""
        return [e for e in self.get_etapas_cobranca(empresa) if e.get('ativo', 'sim') == 'sim']

    def add_etapa_cobranca(self, empresa: str, data: dict, user_email: str) -> Tuple[bool, str]:
        """
        Cria etapa do funil de cobrança.

        Exemplo:
          nome='1ª Cobrança', ordem=1, dias=15, acao='email', destinatario='transportadora'
          nome='Escalação Gestão', ordem=2, dias=30, acao='ambos', destinatario='gestor'
          nome='Jurídico', ordem=3, dias=60, acao='notificacao', destinatario='gestor'
        """
        if not self.sheets or not self.sheets.connected:
            return False, "Sheets não conectado"
        ws = self._ensure_sheet(self.SHEET_COBRANCA)
        if not ws:
            return False, "Aba não disponível"

        if not data.get('nome_etapa') or not data.get('dias_apos_avaria'):
            return False, "Nome da etapa e dias são obrigatórios"

        try:
            # Próxima ordem automática se não informada
            _, rows = self._read_all(self.SHEET_COBRANCA)
            existentes = [r for r in rows if r.get('empresa') == empresa]
            proxima_ordem = max((int(r.get('ordem', 0)) for r in existentes), default=0) + 1

            row = [
                self._new_id(),
                empresa,
                data.get('nome_etapa', ''),
                str(data.get('ordem', proxima_ordem)),
                str(data.get('dias_apos_avaria', 15)),
                data.get('acao', 'email'),
                data.get('destinatario', 'transportadora'),
                data.get('template_email', ''),
                'sim',
                self._now(),
                user_email,
            ]
            ws.append_row(row)
            logger.info(f"Etapa cobrança criada para {empresa}: {data.get('nome_etapa')}")
            return True, "Etapa de cobrança criada com sucesso"
        except Exception as e:
            logger.error(f"add_etapa_cobranca: {e}")
            return False, str(e)

    def update_etapa_cobranca(self, empresa: str, etapa_id: str, data: dict) -> Tuple[bool, str]:
        """Atualiza uma etapa do funil."""
        if not self.sheets or not self.sheets.connected:
            return False, "Sheets não conectado"
        ws = self._ensure_sheet(self.SHEET_COBRANCA)
        if not ws:
            return False, "Aba não disponível"
        try:
            all_values = ws.get_all_values()
            for row_idx, row in enumerate(all_values[1:], start=2):
                if str(row[0]).strip() == etapa_id and str(row[1]).strip() == empresa:
                    ws.update(f'C{row_idx}:I{row_idx}', [[
                        data.get('nome_etapa', row[2] if len(row) > 2 else ''),
                        str(data.get('ordem', row[3] if len(row) > 3 else '1')),
                        str(data.get('dias_apos_avaria', row[4] if len(row) > 4 else '15')),
                        data.get('acao', row[5] if len(row) > 5 else 'email'),
                        data.get('destinatario', row[6] if len(row) > 6 else 'transportadora'),
                        data.get('template_email', row[7] if len(row) > 7 else ''),
                        data.get('ativo', row[8] if len(row) > 8 else 'sim'),
                    ]])
                    return True, "Etapa atualizada"
            return False, "Etapa não encontrada"
        except Exception as e:
            logger.error(f"update_etapa_cobranca: {e}")
            return False, str(e)

    def delete_etapa_cobranca(self, empresa: str, etapa_id: str) -> Tuple[bool, str]:
        """Remove uma etapa do funil."""
        return self._delete_row(self.SHEET_COBRANCA, empresa, etapa_id)

    # HELPER GENÉRICO DE DELEÇÃO

    def _delete_row(self, sheet_name: str, empresa: str, record_id: str) -> Tuple[bool, str]:
        """
        Remove um registro limpando a linha inteira.
        Funciona para qualquer aba que tenha id na col[0] e empresa na col[1].
        """
        if not self.sheets or not self.sheets.connected:
            return False, "Sheets não conectado"
        ws = self._ensure_sheet(sheet_name)
        if not ws:
            return False, "Aba não disponível"
        try:
            all_values = ws.get_all_values()
            for row_idx, row in enumerate(all_values[1:], start=2):
                if str(row[0]).strip() == record_id and str(row[1]).strip() == empresa:
                    n_cols = len(all_values[0])
                    col_end = chr(ord('A') + n_cols - 1)
                    ws.update(f'A{row_idx}:{col_end}{row_idx}', [[''] * n_cols])
                    logger.info(f"Registro {record_id} removido de {sheet_name}")
                    return True, "Removido com sucesso"
            return False, "Registro não encontrado"
        except Exception as e:
            logger.error(f"_delete_row({sheet_name}, {record_id}): {e}")
            logger.debug(traceback.format_exc())
            return False, str(e)

    # UTILITÁRIO — AVALIAÇÃO DE ALERTAS (para o dashboard)
  
    def avaliar_alertas_avaria(self, avaria: dict, empresa: str, alertas_ativos: list = None) -> List[dict]:
        """
        Avalia quais alertas se aplicam a uma avaria específica.

        Retorna lista de alertas disparados, cada um com:
          {nome, cor_badge, mensagem, dias_decorridos}

        Chamado pelo dashboard ao renderizar cada linha da tabela.

        Exemplo de uso no template:
          {% for alerta in alertas_avaria[avaria.id] %}
            <span class="badge bg-{{ alerta.cor_badge }}">{{ alerta.mensagem }}</span>
          {% endfor %}
        """
        if alertas_ativos is None:
            alertas_ativos = self.get_alertas_ativos(empresa)
        disparados = []
        hoje = datetime.now().date()

        for alerta in alertas_ativos:
            campo = alerta.get('campo_data', '')
            valor_campo = avaria.get(campo, '')
            if not valor_campo:
                continue
        
            try:
                # ── Parse da data do campo monitorado ─────────────────────────
                data_campo = None
                for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%d/%m/%Y', '%d/%m/%Y %H:%M'):
                    try:
                        data_campo = datetime.strptime(
                            valor_campo[:len(fmt.replace('%Y', 'XXXX').replace('%m', 'XX')
                                            .replace('%d', 'XX').replace('%H', 'XX')
                                            .replace('%M', 'XX').replace('%S', 'XX'))],
                            fmt
                        ).date()
                        break
                    except ValueError:
                        continue
                if data_campo is None:
                    continue
        
                # ── Parâmetros do alerta ───────────────────────────────────────
                dias_limite   = int(alerta.get('dias_limite', 15))
                dias_maximo   = int(alerta.get('dias_maximo', 0) or 0)
                tipo_alerta   = alerta.get('tipo_alerta', 'atraso')
                campo_parar   = (alerta.get('campo_parar', '') or '').strip()
                status_parar  = [
                    s.strip() for s in str(alerta.get('status_parar', '') or '').split(',')
                    if s.strip()
                ]
                logica_parada = (alerta.get('logica_parada', 'E') or 'E').upper()
        
                # ── Verifica condição de parada (resolução) ────────────────────
                campo_resolvido  = False
                status_resolvido = False
        
                if campo_parar:
                    # Para quando o campo sinalizador estiver preenchido
                    campo_resolvido = bool(str(avaria.get(campo_parar, '') or '').strip())
        
                if status_parar:
                    status_est  = str(avaria.get('status_estoque', '') or '').strip()
                    status_ind  = str(avaria.get('status_indenizacao', '') or '').strip()
                    status_resolvido = (status_est in status_parar) or (status_ind in status_parar)
        
                # Decide se para com base na lógica E / OU
                if campo_parar and status_parar:
                    # Ambos configurados — usa a lógica definida
                    if logica_parada == 'E':
                        if campo_resolvido and status_resolvido:
                            continue  # Condição de parada atingida
                    else:  # OU
                        if campo_resolvido or status_resolvido:
                            continue
                elif campo_parar:
                    if campo_resolvido:
                        continue
                elif status_parar:
                    if status_resolvido:
                        continue
        
                # ── Calcula se o alerta deve disparar ─────────────────────────
                deve_disparar  = False
                dias_referencia = 0
        
                if tipo_alerta == 'vencimento':
                    # Campo é data futura (ex: prazo_pagamento)
                    # Dispara quando faltam <= dias_limite dias
                    dias_restantes  = (data_campo - hoje).days   # positivo = futuro
                    dias_apos_venc  = -dias_restantes             # positivo = já venceu
                    dias_referencia = dias_restantes
        
                    if dias_restantes <= dias_limite:
                        # Para de exibir após dias_maximo dias do vencimento
                        if dias_maximo > 0 and dias_apos_venc > dias_maximo:
                            continue
                        deve_disparar = True
                else:
                    # tipo 'atraso' — campo é data passada (ex: data_registro)
                    # Dispara quando passaram >= dias_limite dias
                    dias_decorridos = (hoje - data_campo).days
                    dias_referencia = dias_decorridos
        
                    if dias_decorridos >= dias_limite:
                        if dias_maximo > 0 and dias_decorridos > dias_maximo:
                            continue
                        deve_disparar = True
        
                if deve_disparar:
                    mensagem = alerta.get('mensagem', '')
                    if not mensagem:
                        if tipo_alerta == 'vencimento':
                            if dias_referencia >= 0:
                                mensagem = f'{campo}: vence em {dias_referencia}d'
                            else:
                                mensagem = f'{campo}: vencido há {abs(dias_referencia)}d'
                        else:
                            mensagem = f'{campo}: {dias_referencia}d sem resolução'
        
                    disparados.append({
                        'nome':            alerta.get('nome', ''),
                        'cor_badge':       alerta.get('cor_badge', 'warning'),
                        'mensagem':        mensagem,
                        'dias_decorridos': dias_referencia,
                        'dias_limite':     dias_limite,
                        'campo':           campo,
                        'tipo_alerta':     tipo_alerta,
                    })
        
            except Exception:
                continue

        return disparados

    def get_templates(self, empresa: str) -> List[dict]:
        """Retorna todos os templates de e-mail da empresa."""
        _, rows = self._read_all(self.SHEET_TEMPLATES)
        return [r for r in rows if r.get('empresa') == empresa]
    
    
    def get_templates_ativos(self, empresa: str) -> List[dict]:
        """Retorna apenas templates ativos — usado pelos selects de cobrança."""
        return [t for t in self.get_templates(empresa) if t.get('ativo', 'sim') == 'sim']
    
    
    def get_template_by_id(self, empresa: str, template_id: str) -> Optional[dict]:
        """Retorna um template pelo ID."""
        for t in self.get_templates(empresa):
            if t.get('id') == template_id:
                return t
        return None
    
    
    def add_template(self, empresa: str, data: dict, user_email: str) -> Tuple[bool, str]:
        """
        Cria novo template de e-mail.
    
        Campos obrigatórios em data:
            nome    — nome de exibição (ex: '1ª Cobrança Transportadora')
            assunto — assunto do e-mail (suporta {{variáveis}})
            html_body — corpo HTML (suporta {{variáveis}})
    
        Campos opcionais:
            variaveis — string separada por vírgula com as variáveis disponíveis
                        (ex: 'transportadora,numero_nf,valor,prazo')
            ativo     — 'sim' (padrão) ou 'nao'
        """
        if not self.sheets or not self.sheets.connected:
            return False, "Sheets não conectado"
        ws = self._ensure_sheet(self.SHEET_TEMPLATES)
        if not ws:
            return False, "Aba não disponível"
    
        if not data.get('nome') or not data.get('html_body'):
            return False, "Nome e corpo HTML são obrigatórios"
    
        try:
            now = self._now()
            row = [
                self._new_id(),
                empresa,
                data.get('nome', ''),
                data.get('assunto', ''),
                data.get('html_body', ''),
                data.get('variaveis', ''),
                data.get('ativo', 'sim'),
                now,
                user_email,
                now,
                user_email,
            ]
            ws.append_row(row, value_input_option='USER_ENTERED')
            self._invalidate_config_cache(self.SHEET_TEMPLATES)
            logger.info(f"Template de e-mail criado: {empresa} / {data.get('nome')}")
            return True, "Template criado com sucesso"
        except Exception as e:
            logger.error(f"add_template: {e}")
            return False, str(e)
    
    
    def update_template(self, empresa: str, template_id: str, data: dict) -> Tuple[bool, str]:
        """Atualiza campos de um template existente."""
        if not self.sheets or not self.sheets.connected:
            return False, "Sheets não conectado"
        ws = self._ensure_sheet(self.SHEET_TEMPLATES)
        if not ws:
            return False, "Aba não disponível"
    
        try:
            all_values = ws.get_all_values()
            if not all_values:
                return False, "Aba vazia"
    
            headers = all_values[0]
            now = self._now()
    
            for row_idx, row in enumerate(all_values[1:], start=2):
                row_empresa = str(row[0]).strip() if len(row) > 0 else ''
                row_id      = str(row[headers.index('id')]).strip() if 'id' in headers else ''
    
                if row_empresa == empresa and row_id == template_id:
                    # Atualiza campos um a um para preservar os que não vieram
                    campos_editaveis = {
                        'nome':       data.get('nome'),
                        'assunto':    data.get('assunto'),
                        'html_body':  data.get('html_body'),
                        'variaveis':  data.get('variaveis'),
                        'ativo':      data.get('ativo'),
                        'atualizado_em':  now,
                        'atualizado_por': data.get('_user', 'sistema'),
                    }
                    for campo, valor in campos_editaveis.items():
                        if valor is not None and campo in headers:
                            col = headers.index(campo) + 1
                            ws.update_cell(row_idx, col, valor)
    
                    self._invalidate_config_cache(self.SHEET_TEMPLATES)
                    logger.info(f"Template atualizado: {empresa}/{template_id}")
                    return True, "Template atualizado com sucesso"
    
            return False, f"Template {template_id} não encontrado"
    
        except Exception as e:
            logger.error(f"update_template: {e}")
            return False, str(e)
    
    
    def delete_template(self, empresa: str, template_id: str) -> Tuple[bool, str]:
        """
        Desativa (soft-delete) um template definindo ativo='nao'.
        Não remove a linha para preservar histórico de cobranças.
        """
        return self.update_template(empresa, template_id, {'ativo': 'nao'})
    
    
    def get_smtp_config(self, empresa: str) -> dict:
        """
        Retorna as configurações SMTP da empresa como dict pronto para
        EmailService.update_smtp_config().
    
        Chaves retornadas:
            email_smtp_host, email_smtp_port, email_smtp_usuario,
            email_smtp_senha, email_smtp_tls, email_smtp_ssl
        """
        cfg = self.get_all_configs(empresa)
        return {
            'email_smtp_host':    cfg.get('email_smtp_host', ''),
            'email_smtp_port':    cfg.get('email_smtp_port', '587'),
            'email_smtp_usuario': cfg.get('email_smtp_usuario', ''),
            'email_smtp_senha':   cfg.get('email_smtp_senha', ''),
            'email_smtp_tls':     cfg.get('email_smtp_tls', 'sim'),
            'email_smtp_ssl':     cfg.get('email_smtp_ssl', 'nao'),
        }
