import logging
from typing import List, Tuple, Optional, Dict
from datetime import datetime

from modules.wms.models.avaria import Avaria
from core.services.google_sheets_service import GoogleSheetsService
from modules.wms.models.item_avaria import ItemAvaria # <<< FIX: Importação explícita

logger = logging.getLogger(__name__)

class WmsAvariaService:

    def __init__(self, sheets_service: GoogleSheetsService):
        self.sheets = sheets_service
        self.worksheet_name = "Avarias"
        self._headers_validated = False  # NOVO - Flag de controle
        self._ensure_sheet_exists()
        self._validate_and_fix_headers()

    # VALIDAÇÃO E CORREÇÃO AUTOMÁTICA DE CABEÇALHO

    def _validate_and_fix_headers(self):
        """Valida e corrige automaticamente o cabeçalho (executado apenas 1x)."""
        if not self.sheets.connected:
            logger.warning("WMS: Sem conexão com Sheets, pulando validação de cabeçalho")
            return
    
        # NOVO: Verificar se já foi validado nesta sessão
        if self._headers_validated:
            logger.debug("⏩ Cabeçalho já validado nesta sessão")
            return
        
        try:
            ws = self.sheets.sheet.worksheet(self.worksheet_name)
            
            # Cabeçalho esperado (ordem exata)
            expected_headers = list(Avaria().to_dict().keys())
            
            logger.info(f"◃ Validando cabeçalho da aba '{self.worksheet_name}'...")
            logger.debug(f"   Cabeçalho esperado: {expected_headers}")
            
            # Obter cabeçalho atual (linha 1)
            current_headers = ws.row_values(1)

            # Circuit breaker OPEN → row_values retorna None (≠ header vazio real)
            # Não confundir com lista vazia: None significa falha de leitura, não ausência de header.
            if current_headers is None:
                logger.warning(
                    "⚠️ _validate_and_fix_headers: row_values retornou None "
                    "(circuit breaker aberto ou falha de leitura). "
                    "Pulando validação para não sobrescrever cabeçalho válido."
                )
                return  # NÃO define _headers_validated=True → tentará novamente no próximo request

            logger.debug(f"   Cabeçalho atual: {current_headers}")
            
            # Verificar se precisa correção
            needs_fix = False
            
            if not current_headers:  # [] → cabeçalho genuinamente ausente
                logger.warning(" Cabeçalho vazio! Será criado.")
                needs_fix = True
            elif current_headers != expected_headers:
                logger.warning(" Cabeçalho incorreto ou incompleto! Será corrigido.")
                logger.debug(f"   Diferenças detectadas:")
                
                # Identificar colunas faltando
                missing = set(expected_headers) - set(current_headers)
                if missing:
                    logger.debug(f"   - Faltando: {missing}")
                
                # Identificar colunas extras
                extra = set(current_headers) - set(expected_headers)
                if extra:
                    logger.debug(f"   - Extras: {extra}")
                
                # Identificar diferença de ordem
                if set(current_headers) == set(expected_headers):
                    logger.debug(f"   - Ordem incorreta")
                
                needs_fix = True
            
            # Aplicar correção se necessário
            if needs_fix:
                logger.info("Corrigindo cabeçalho...")
                
                # Backup dos dados existentes (se houver)
                all_data = ws.get_all_values()
                data_rows = all_data[1:] if len(all_data) > 1 else []
                
                if data_rows:
                    logger.info(f"   Backup de {len(data_rows)} linhas de dados")
                
                # Atualizar cabeçalho
                ws.update('A1', [expected_headers])
                logger.info(f"   Cabeçalho atualizado com {len(expected_headers)} colunas")
                
                # Reordenar dados existentes se necessário
                if data_rows and current_headers:
                    logger.info("   ↩Reordenando dados existentes...")
                    
                    # Criar mapeamento de índices
                    old_to_new_mapping = {}
                    for i, old_col in enumerate(current_headers):
                        if old_col in expected_headers:
                            new_index = expected_headers.index(old_col)
                            old_to_new_mapping[i] = new_index
                    
                    # Reordenar cada linha
                    reordered_data = []
                    for row in data_rows:
                        new_row = [''] * len(expected_headers)
                        for old_idx, new_idx in old_to_new_mapping.items():
                            if old_idx < len(row):
                                new_row[new_idx] = row[old_idx]
                        reordered_data.append(new_row)
                    
                    # CORREÇÃO CRÍTICA: Calcular dinamicamente a última coluna
                    if reordered_data:
                        num_cols = len(expected_headers)
                        
                        # Função auxiliar para converter número em letra de coluna
                        def num_to_col_letter(n):
                            """Converte número de coluna (1-based) para letra (A, B, ..., AA, AB, etc)"""
                            result = ""
                            while n > 0:
                                n -= 1
                                result = chr(65 + (n % 26)) + result
                                n //= 26
                            return result
                        
                        last_col = num_to_col_letter(num_cols)
                        range_str = f'A2:{last_col}{len(reordered_data)+1}'
                        
                        logger.info(f"   Atualizando range: {range_str} ({num_cols} colunas)")
                        ws.update(range_str, reordered_data)
                        logger.info(f"   {len(reordered_data)} linhas reordenadas com sucesso")
                
                logger.info("Cabeçalho corrigido e dados preservados!")
            else:
                logger.info("Cabeçalho válido, nenhuma correção necessária")
            
            # NOVO: Marcar como validado
            self._headers_validated = True
        
        except Exception as e:
            logger.error(f"Erro ao validar/corrigir cabeçalho: {e}")
            logger.warning(" Continuando sem validação de cabeçalho")

    # CRIAR PLANILHA SE NÃO EXISTIR

    def _ensure_sheet_exists(self):
        """Cria a aba Avarias se não existir"""
        if not self.sheets.connected:
            logger.warning("WMS: Sem conexão com Sheets, pulando criação de aba")
            return
        
        try:
            # Tenta obter a aba
            self.sheets.sheet.worksheet(self.worksheet_name)
            logger.info(f"Aba '{self.worksheet_name}' já existe")
        except Exception as e:
            # Aba não existe, vamos criar
            if "No worksheet found" in str(e) or "worksheet" in str(e).lower():
                try:
                    logger.info(f"⌨Criando aba '{self.worksheet_name}'...")
                    headers = list(Avaria().to_dict().keys())
                    ws = self.sheets.sheet.add_worksheet(
                        title=self.worksheet_name, 
                        rows=1000, 
                        cols=len(headers)
                    )
                    ws.update('A1', [headers])
                    logger.info(f"Aba '{self.worksheet_name}' criada com {len(headers)} colunas")
                except Exception as create_e:
                    logger.error(f"Erro ao criar aba '{self.worksheet_name}': {create_e}")
            else:
                logger.error(f"Erro ao verificar aba '{self.worksheet_name}': {e}")

    def _ensure_itens_avarias_sheet_exists(self):
        """Cria a aba Itens_Avarias se não existir"""
        if not self.sheets.connected:
            logger.warning("WMS: Sem conexão com Sheets, pulando criação de aba Itens_Avarias")
            return
        
        try:
            # Tenta obter a aba
            self.sheets.sheet.worksheet('Itens_Avarias')
            logger.info("Aba 'Itens_Avarias' já existe")
        except Exception as e:
            # Aba não existe, vamos criar
            if "No worksheet found" in str(e) or "worksheet" in str(e).lower():
                try:
                    logger.info("⌨Criando aba 'Itens_Avarias'...")
                    headers = [
                        'avaria_id',
                        'sequencia',
                        'codprod',
                        'descrprod',
                        'qtdneg',
                        'qtd_avariada',
                        'codlocaldest',
                        'codlocalorig',
                        'localizacao',
                        'valor_item',
                        'codgrupoprod',
                        'codemp'
                    ]
                    ws = self.sheets.sheet.add_worksheet(
                        title='Itens_Avarias',
                        rows=10000,
                        cols=len(headers)
                    )
                    ws.update('A1', [headers])
                    logger.info(f"Aba 'Itens_Avarias' criada com {len(headers)} colunas")
                except Exception as create_e:
                    logger.error(f"Erro ao criar aba 'Itens_Avarias': {create_e}")
            else:
                logger.error(f"Erro ao verificar aba 'Itens_Avarias': {e}")
            
    # MÉTODOS EXISTENTES (manter como estão)
    
    def registrar_avaria(self, data: dict, user_email: str):
        """
        ⚡: Salva avaria + itens em 2 API calls (ao invés de N+1).
        
        ANTES: 1 append_row (avaria) + N append_rows (itens) = N+1 API calls
        DEPOIS: 1 append_row (avaria) + 1 batch_append_rows (todos itens) = 2 API calls
        """
        try:
            from modules.wms.models.avaria import Avaria
            
            # CRIAR OBJETO AVARIA (sem alteração)
            avaria = Avaria(
                cod_empresa=data.get('cod_empresa', ''),
                nota=data.get('nota', ''),
                nfd=data.get('nfd', ''),
                quantidade=data.get('quantidade', ''),
                volume=data.get('volume', ''),
                transportadora=data.get('transportadora', ''),
                cte=data.get('cte', ''),
                valor_nf=data.get('valor_nf', ''),
                valor_nfd=data.get('valor_nfd', ''),
                nfp=data.get('nfp', ''),
                valor_nfp=data.get('valor_nfp', ''),
                motivo_devolucao=data.get('motivo_devolucao', ''),
                cod_motivo_devolucao=data.get('cod_motivo_devolucao', ''),
                observacao=data.get('observacao', ''),
                registrado_por=user_email,
                empresa=data.get('empresa', ''),
                indenizacao=data.get('indenizacao', ''),
                reembolso_pct=data.get('reembolso_pct'),
                prazo_pagamento=data.get('prazo_pagamento', ''),
                data_pagamento=data.get('data_pagamento', ''),
                status_estoque=data.get('status_estoque', 'Pendente'),
                status_indenizacao=data.get('status_indenizacao', 'Pendente'),
            )
            
            # Use data_registro if provided
            if data.get('data_registro'):
                avaria.data_registro = data.get('data_registro')
            
            # 1. Salvar cabeçalho (1 API call)
            avaria_row = avaria.to_row()
            success, msg = self.sheets.append_row('Avarias', avaria_row)
            
            if not success:
                return False, None, f"Erro ao salvar cabeçalho: {msg}"
            
            # 2. OTIMIZAÇÃO: Salvar TODOS os itens em UMA chamada
            itens_data = data.get('itens', [])
            
            if itens_data:
                rows_to_append = []
                from modules.wms.models.item_avaria import _parse_valor_item
                for idx, item in enumerate(itens_data, start=1):
                    item_row = [
                        avaria.id,                                              # avaria_id
                        idx,                                                    # sequencia
                        item.get('codprod', ''),                               # codprod
                        item.get('descrprod', ''),                             # descrprod
                        _parse_valor_item(item.get('qtdneg', 0)),              # qtdneg
                        _parse_valor_item(item.get('qtd_avariada', 0)),        # qtd_avariada
                        item.get('codlocaldest', ''),                          # codlocaldest
                        item.get('codlocalorig', ''),                          # codlocalorig
                        item.get('localizacao', ''),                           # localizacao
                        f"{round(_parse_valor_item(item.get('valor_item', 0) or item.get('vlrtot', 0)), 2):.2f}", # valor_item (string com ponto para evitar locale PT-BR no Sheets)
                        item.get('codgrupoprod', ''),                          # codgrupoprod
                        item.get('codemp', ''),                                # codemp
                        '',                                                     # id_etiqueta
                        item.get('prefixo_estoque', ''),                       # prefixo_estoque
                        '',                                                     # etiqueta_anterior
                        'pendente',                                             # status_etiqueta
                        item.get('observacao_produto', ''),                    # observacao_produto
                        '',                                                     # data_identificacao
                        '',                                                     # data_triagem
                        '',                                                     # operador_identificacao
                        '',                                                     # operador_triagem
                        '',                                                     # link_fotos_drive
                        '',                                                     # pasta_id_drive
                        '',                                                     # fotos_ids
                    ]
                    rows_to_append.append(item_row)
                
                # UMA Única chamada para todos os itens!
                success_itens, msg_itens = self.sheets.batch_append_rows(
                    'Itens_Avarias', 
                    rows_to_append
                )
                
                if not success_itens:
                    logger.error(
                        f"❌ Cabeçalho da avaria {avaria.id} salvo mas itens falharam: {msg_itens}"
                    )
                    try:
                        self.update_avaria(
                            avaria.id,
                            alterado_por=user_email,
                            status_estoque='ERRO_ITENS',
                        )
                    except Exception as rollback_e:
                        logger.error(
                            f"❌ Falha ao marcar avaria {avaria.id} como ERRO_ITENS: {rollback_e}"
                        )
                    self.sheets.invalidate_wms_cache()
                    return False, avaria, (
                        f"Cabeçalho da avaria foi salvo, mas os itens não puderam ser gravados "
                        f"({msg_itens}). A avaria {avaria.id} foi marcada com status ERRO_ITENS "
                        f"para correção manual — não repita o registro sem verificar antes."
                    )

            # 3. Invalidar cache WMS
            self.sheets.invalidate_wms_cache()
            
            logger.info(f"Avaria {avaria.id} salva com {len(itens_data)} itens")
            return True, avaria, "Avaria registrada com sucesso"
            
        except Exception as e:
            logger.error(f"Erro ao registrar avaria: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False, None, str(e)

    def _get_expected_headers(self) -> list:
        """Retorna lista de cabeçalhos esperados na planilha de Avarias"""
        return [
            'id',
            'cod_empresa',
            'nota',
            'nfd',
            'quantidade',
            'volume',
            'transportadora',
            'cte',
            'valor_nf',
            'valor_nfd',  # NOVO
            'nfp',  # NOVO
            'valor_nfp',  # NOVO
            'motivo_devolucao',
            'indenizacao',
            'prazo_pagamento',
            'data_pagamento',  # NOVO
            'observacao',
            'data_registro',
            'status',
            'registrado_por',
            'etiqueta_inicial_impressa',
            'cod_triagem',
            'reembolso_pct',
            'observacao_final',
            'data_finalizacao',
            'finalizado_por'
        ]

    def get_itens_avaria(self, avaria_id: str) -> List[dict]:
        """Retorna todos os itens vinculados a uma avaria específica (como dicts)."""
        all_itens = self._get_all_item_avarias()
        result = []
        for i in all_itens:
            # Pode ser objeto ItemAvaria ou dict dependendo do cache
            aid = getattr(i, 'avaria_id', None) or (i.get('avaria_id') if isinstance(i, dict) else None)
            if str(aid) == str(avaria_id):
                result.append(i.to_dict() if hasattr(i, 'to_dict') else i)
        return result

    def get_avaria_by_id(self, avaria_id: str) -> Optional[Avaria]:
        """
        Busca avaria específica por ID.
        ⚡ USA CACHE: get_wms_avarias_cached() — zero leituras diretas ao Sheets.
        """
        if not self.sheets.connected:
            logger.warning("WMS: Sem conexão com Sheets")
            return None

        try:
            records = self.sheets.get_wms_avarias_cached()
            for record in records:
                if str(record.get('id', '')).strip() == avaria_id:
                    return Avaria.from_dict(record)

            logger.warning(f"WMS: Avaria {avaria_id} não encontrada")
            return None

        except Exception as e:
            logger.error(f"Erro ao buscar avaria {avaria_id}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    def get_all_avarias(self, filters: dict = None, empresa: str = None):
        """
        Busca avarias com filtro opcional por empresa.
        ⚡ USA CACHE: get_wms_avarias_cached() em vez de get_all_records() direto.
        Lock anti-stampede garante que 2 usuários simultâneos não disparam 2 reads.
    
        Args:
            filters: Dicionário de filtros (ex: {'status': 'Finalizada'})
            empresa: Filtrar por empresa (Matriz, Empresax, Thomas)
    
        Returns:
            List[Avaria]: Lista de objetos Avaria
        """
        if not self.sheets.connected:
            logger.warning("WMS: Sem conexão com Google Sheets")
            return []
    
        try:
            # CACHE em vez de get_all_records('Avarias') direto
            records = self.sheets.get_wms_avarias_cached()
    
            logger.info(f"Carregando avarias...")
            logger.info(f"   Cabeçalhos encontrados: {len(records[0].keys()) if records else 0} colunas")
            logger.info(f"   Linhas de dados: {len(records)}")
    
            avarias = []
            for record in records:
                try:
                    avaria = Avaria.from_dict(record)
                    avarias.append(avaria)
                except Exception as e:
                    logger.warning(f"Ignorando registro inválido: {e}")
    
            # Filtrar por empresa
            if empresa:
                avarias = [a for a in avarias if a.empresa == empresa]
                logger.info(f"Avarias carregadas: {len(avarias)}")
                logger.info(f"   Filtradas por empresa: {empresa}")
            else:
                logger.info(f"Avarias carregadas: {len(avarias)}")
        
            # Prioridade 1: empresa passada diretamente como kwarg
            if empresa:
                avarias = [a for a in avarias if a.empresa == empresa]
            
            # Prioridade 2 (defesa): empresa dentro do dict de filtros
            elif filters and filters.get('empresa'):
                empresa_filtro = filters['empresa']
                avarias = [a for a in avarias if a.empresa == empresa_filtro]
                logger.info(f"[defesa] Filtro empresa via filters dict: {empresa_filtro}")            
    
            # Aplicar filtros adicionais
            if filters:
                filtradas = avarias
    
                if filters.get('status'):
                    filtradas = [a for a in filtradas if a.status == filters['status']]
    
                if filters.get('registrado_por'):
                    filtradas = [a for a in filtradas if a.registrado_por == filters['registrado_por']]
    
                # Filtro por data
                data_inicio_str = filters.get('data_inicio')
                data_fim_str    = filters.get('data_fim')
                campo_filtro    = filters.get('campo_data_filtro', 'data_registro')
    
                if data_inicio_str or data_fim_str:
                    from datetime import datetime

                    CAMPOS_ITEM = {'data_triagem', 'data_identificacao'}

                    def _parse_date(s):
                        if not s:
                            return None
                        for fmt in ('%Y-%m-%d', '%d/%m/%Y'):
                            try:
                                return datetime.strptime(s.strip(), fmt)
                            except ValueError:
                                continue
                        return None

                    dt_inicio = _parse_date(data_inicio_str)
                    dt_fim    = _parse_date(data_fim_str)

                    # Pré-carregar itens agrupados (evita N+1 API calls)
                    itens_por_avaria = {}
                    if campo_filtro in CAMPOS_ITEM:
                        for it in self._get_all_item_avarias():
                            if it.status_etiqueta == 'baixada':
                                continue
                            if it.status_etiqueta == 'recusada' and not str(getattr(it, 'etiqueta_anterior', '') or '').strip():
                                continue
                            itens_por_avaria.setdefault(it.avaria_id, []).append(it)

                    def _avaria_no_intervalo(av):
                        av_dict = av.to_dict() if hasattr(av, 'to_dict') else av
                        if campo_filtro in CAMPOS_ITEM:
                            data_str = ''
                            for it in itens_por_avaria.get(av_dict.get('id', ''), []):
                                it_dict = it.to_dict() if hasattr(it, 'to_dict') else it
                                val = str(it_dict.get(campo_filtro, '') or '')
                                if val:
                                    data_str = val[:10]
                                    break
                        else:
                            data_str = av_dict.get(campo_filtro, '')
                        if not data_str:
                            return False
                        dt = _parse_date(str(data_str)[:10])
                        if dt is None:
                            return False
                        if dt_inicio and dt < dt_inicio:
                            return False
                        if dt_fim and dt > dt_fim:
                            return False
                        return True

                    filtradas = [av for av in filtradas if _avaria_no_intervalo(av)]
                    logger.info(
                        f"   📅 Filtro data ({campo_filtro}): "
                        f"{data_inicio_str} → {data_fim_str}: {len(filtradas)} avarias"
                    )
    
                return filtradas
    
            return avarias
    
        except Exception as e:
            logger.error(f"Erro ao buscar avarias: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []
        
    def update_avaria(self, avaria_id: str, alterado_por: str = '', **updates) -> Tuple[bool, str]:
        """
        Atualiza dados de uma avaria existente (VERSÃO FUNCIONAL ORIGINAL)
        
        ✅ CORREÇÃO: Restaura método simples que funciona corretamente
        
        Args:
            avaria_id: ID da avaria
            **updates: Campos a atualizar
        
        Returns:
            Tuple (sucesso, mensagem)
        """
        if not self.sheets.connected:
            return False, "Google Sheets não conectado"
        
        try:
            ws = self.sheets.sheet.worksheet("Avarias")
            
            # Encontrar linha da avaria
            all_data = ws.get_all_values()
            headers = all_data[0]
            id_col = headers.index('id') + 1
            
            avaria_row = None
            for idx, row in enumerate(all_data[1:], start=2):
                if len(row) > id_col - 1 and row[id_col - 1] == avaria_id:
                    avaria_row = idx
                    break

            if not avaria_row:
                return False, f"Avaria {avaria_id} não encontrada"

            # CRÍTICO: Ler valores atuais ANTES de atualizar
            valores_atuais = all_data[avaria_row - 1]
            # ── Auto-preencher rastreabilidade de alteração ────────────────────────────
            from datetime import datetime as _dt
            _now_str = _dt.now().strftime('%d/%m/%Y %H:%M:%S')
            if 'data_alteracao' not in updates:
                updates['data_alteracao'] = _now_str
            if 'alterado_por' not in updates and alterado_por:
                updates['alterado_por'] = alterado_por            
            
            # Atualizar campos — todas as células alteradas em UMA chamada batch_update
            updated_fields = []
            batch_cells = []
            for field, value in updates.items():
                if field not in headers:
                    # Column missing in sheet — add it so important fields (e.g. data_pagamento) are not lost
                    new_col_idx = len(headers) + 1
                    ws.update_cell(1, new_col_idx, field)
                    headers.append(field)
                    valores_atuais.append('')
                    logger.warning(f"   Coluna '{field}' não existia — adicionada na posição {new_col_idx}")

                col_idx = headers.index(field) + 1

                # Obter valor atual
                valor_atual = valores_atuais[col_idx - 1] if col_idx - 1 < len(valores_atuais) else ''

                # Converter valor para string (mantém vazios como vazios)
                novo_valor = str(value) if value is not None and str(value).strip() != '' else ''

                # Só atualizar se realmente mudou
                if valor_atual != novo_valor:
                    batch_cells.append({
                        'range': f"{self._col_index_to_letter(col_idx)}{avaria_row}",
                        'values': [[novo_valor]],
                    })
                    updated_fields.append(f"{field}('{valor_atual}'→'{novo_valor}')")
                    logger.info(f"   Atualizando {field}: '{valor_atual}' → '{novo_valor}'")
                else:
                    logger.debug(f"   ⏩ Pulando {field}: valor já é '{valor_atual}'")

            if batch_cells:
                ws.batch_update(batch_cells, value_input_option='USER_ENTERED')

            if updated_fields:
                logger.info(f"Avaria {avaria_id} atualizada: {', '.join(updated_fields)}")
                self.sheets.invalidate_wms_cache()
            else:
                logger.info(f"ℹAvaria {avaria_id}: nenhuma alteração necessária")
                
            return True, "Avaria atualizada com sucesso"
            
        except Exception as e:
            logger.error(f"Erro ao atualizar avaria {avaria_id}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False, str(e)

    # NOVO MÉTODO: ATUALIZAR ITEM DE AVARIA

    def update_item_avaria(self, avaria_id: str, sequencia: int, alterado_por: str = '',
                           id_etiqueta: str = '', **updates):
        """
        ⚡: Atualiza item usando batch_update.

        Se id_etiqueta for informado, busca pela etiqueta específica (evita ambiguidade
        quando há múltiplas linhas com o mesmo avaria_id+sequencia, ex: após recusa).
        """
        if not self.sheets.connected:
            return False, "Google Sheets não conectado"

        try:
            ws = self.sheets.sheet.worksheet("Itens_Avarias")

            # Obter todos os valores para encontrar a linha
            all_data = ws.get_all_values()
            if len(all_data) < 2:
                return False, "Planilha Itens_Avarias vazia"

            headers = all_data[0]

            # Encontrar índices de colunas
            try:
                id_col = headers.index('avaria_id')
                seq_col = headers.index('sequencia')
            except ValueError as e:
                return False, f"Coluna não encontrada em Itens_Avarias: {e}"

            etq_col = headers.index('id_etiqueta') if 'id_etiqueta' in headers else None

            # Encontrar a linha do item
            item_row = None
            seq_str = str(sequencia)

            for idx, row in enumerate(all_data[1:], start=2):
                if len(row) <= max(id_col, seq_col):
                    continue
                if row[id_col] != avaria_id or str(row[seq_col]) != seq_str:
                    continue
                # Se id_etiqueta informado, exige correspondência exata
                if id_etiqueta and etq_col is not None:
                    if etq_col < len(row) and row[etq_col] == id_etiqueta:
                        item_row = idx
                        break
                    continue
                item_row = idx
                break

            if not item_row:
                return False, f"Item não encontrado (Avaria: {avaria_id}, Seq: {sequencia})"
            # ── Auto-preencher rastreabilidade de alteração ────────────────────────────
            from datetime import datetime as _dt
            _now_str = _dt.now().strftime('%d/%m/%Y %H:%M:%S')
            if 'data_alteracao' not in updates:
                updates['data_alteracao'] = _now_str
            if 'alterado_por' not in updates and alterado_por:
                updates['alterado_por'] = alterado_por                
            
            # OTIMIZAÇÃO: Batch update (1 API call para N campos)
            batch_data = []
            updated_fields = []
            from modules.wms.models.item_avaria import _parse_valor_item
            
            for field, value in updates.items():
                if field in headers:
                    col_idx = headers.index(field) + 1
                    col_letter = self._col_index_to_letter(col_idx)
                    cell_address = f"{col_letter}{item_row}"
                    
                    # Garantir que campos numéricos sejam salvos com ponto decimal
                    if field in ('qtdneg', 'qtd_avariada', 'valor_item'):
                        value = _parse_valor_item(value)
                    
                    batch_data.append({
                        'range': cell_address,
                        'values': [[value if value is not None else '']]
                    })
                    updated_fields.append(field)
            
            if batch_data:
                ws.batch_update(batch_data, value_input_option='RAW')
            
            # Invalidar cache
            self.sheets.invalidate_wms_cache()
            
            logger.info(f"Item {avaria_id}/{sequencia} atualizado: {', '.join(updated_fields)}")
            return True, "Item de avaria atualizado com sucesso"
            
        except Exception as e:
            logger.error(f"Erro ao atualizar item: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False, str(e)

    def _col_index_to_letter(self, col_idx):
        """Converte índice de coluna (1-based) para letra (A, B, ..., Z, AA, AB...)"""
        result = ""
        while col_idx > 0:
            col_idx -= 1
            result = chr(col_idx % 26 + 65) + result
            col_idx //= 26
        return result

    def diagnosticar_planilha(self):
        """
        Diagnóstico da planilha de avarias para debug
        Útil para identificar problemas de compatibilidade
        """
        try:
            ws = self.sheets.sheet.worksheet(self.worksheet_name)
            all_data = ws.get_all_values()
            
            if not all_data:
                print("Planilha vazia")
                return
            
            headers = all_data[0]
            rows = all_data[1:]
            
            print("="*80)
            print("DIAGNÓSTICO DA PLANILHA DE AVARIAS")
            print("="*80)
            print(f"\nTotal de colunas: {len(headers)}")
            print(f"Total de linhas de dados: {len(rows)}")
            
            print(f"\nCabeçalhos encontrados:")
            for i, header in enumerate(headers, 1):
                print(f"   {i:2d}. {header}")
            
            # Verificar campos obrigatórios
            campos_novos = ['local', 'valor_nf', 'data_coleta', 'motivo_devolucao', 'indenizacao', 'prazo_pagamento']
            campos_antigos = ['nf_venda', 'localizacao', 'observacao_inicial']
            
            print(f"\nCampos novos encontrados:")
            for campo in campos_novos:
                existe = '✅' if campo in headers else '❌'
                print(f"   {existe} {campo}")
            
            print(f"\n↺Campos antigos encontrados:")
            for campo in campos_antigos:
                existe = '✅' if campo in headers else '❌'
                print(f"   {existe} {campo}")
            
            # Testar carregar primeira avaria
            if rows:
                print(f"\nTeste de carregamento da primeira avaria:")
                try:
                    primeira_avaria = Avaria.from_sheet_row(headers, rows[0])
                    if primeira_avaria:
                        print(f"   Sucesso!")
                        print(f"   ID: {primeira_avaria.id}")
                        print(f"   Nota: {primeira_avaria.nota}")
                        print(f"   Status: {primeira_avaria.status}")
                    else:
                        print(f"   Falha ao criar objeto Avaria")
                except Exception as e:
                    print(f"   Erro: {e}")
            
            print("="*80)
            
        except Exception as e:
            print(f"Erro no diagnóstico: {e}")   

    # Parametrização — filtrar pelo conjunto de regras ativas

    def apply_exclusion_rules(self, resultado: List[Dict], regras: list) -> List[Dict]:
        """
        Aplica regras de exclusão ao resultado de avarias pendentes.

        • OR entre regras  → basta UMA regra corresponder para excluir o item.
        • AND dentro da regra → TODAS as condições definidas devem bater.
        • Após filtragem recalcula rowspan para o agrupamento visual.

        Args:
            resultado: saída de get_untriaged_avarias()
            regras   : lista de RegraExclusao ativas

        Returns:
            Lista filtrada com rowspan / first_row recalculados.
        """
        if not regras or not resultado:
            return resultado

        # 1. filtrar
        filtered = []
        excluded_count = 0

        for row in resultado:
            avaria = row['avaria']
            item   = row['item']
            excluded = False

            for rule in regras:
                if rule.matches(avaria, item):
                    excluded = True
                    excluded_count += 1
                    break          # basta uma regra corresponder

            if not excluded:
                filtered.append(row)

        if excluded_count:
            logger.info(f"Parametrização: {excluded_count} item(s) excluído(s) pelas regras")

        # 2. recalcular rowspan agrupando por avaria
        groups: Dict[str, list] = {}
        for row in filtered:
            groups.setdefault(row['avaria'].id, []).append(row)

        result = []
        for rows in groups.values():
            n = len(rows)
            for idx, row in enumerate(rows):
                row['rowspan']  = n if idx == 0 else 0
                row['first_row'] = (idx == 0)
                result.append(row)

        return result

    def _aplicar_filtros(self, avarias: List[Avaria], filters: dict) -> List[Avaria]:
        """
        ✅ MODIFICADO: Aplica filtros à lista de avarias, removendo filtro 'status' geral.
        
        Args:
            avarias: Lista de avarias
            filters: Dicionário de filtros
            
        Returns:
            Lista de avarias filtradas
        """
        if not filters:
            return avarias
        
        filtradas = avarias
        
        # NOVO: Filtro por empresa
        if 'empresa' in filters and filters['empresa']:
            empresa_filtro = filters['empresa']
            filtradas = [av for av in filtradas if av.empresa == empresa_filtro]
            logger.info(f"   Filtro empresa '{empresa_filtro}': {len(filtradas)} avarias")
        
        # Filtro de busca genérica (search)
        if 'search' in filters and filters['search']:
            search_term = filters['search'].lower()
            filtradas = [
                av for av in filtradas
                if (search_term in av.id.lower() or
                    search_term in av.nota.lower() or
                    search_term in av.nfd.lower() or
                    search_term in av.transportadora.lower() or
                    search_term in av.cod_empresa.lower())
            ]
        
        # Filtro por status (Legacy general status filter REMOVIDO)
        # if 'status' in filters and filters['status']:
        #    status_filtro = filters['status']
        #    filtradas = [av for av in filtradas if av.status == status_filtro]
        
        # Filtro por nota
        if 'nota' in filters and filters['nota']:
            nota_filtro = filters['nota'].lower()
            filtradas = [av for av in filtradas if nota_filtro in av.nota.lower()]
        
        # Filtro por código de empresa
        if 'cod_empresa' in filters and filters['cod_empresa']:
            cod_filtro = filters['cod_empresa'].lower()
            filtradas = [av for av in filtradas if cod_filtro in av.cod_empresa.lower()]

        # ── Filtro de intervalo de datas ─────────────────────────────────────────────
        data_inicio_str = filters.get('data_inicio', '')
        data_fim_str    = filters.get('data_fim', '')
        campo_filtro    = filters.get('campo_data_filtro', 'data_registro') or 'data_registro'

        if data_inicio_str or data_fim_str:
            from datetime import datetime

            def _parse_date(s):
                """Tenta parsear YYYY-MM-DD ou DD/MM/YYYY."""
                for fmt in ('%Y-%m-%d', '%d/%m/%Y'):
                    try:
                        return datetime.strptime(s.strip(), fmt)
                    except ValueError:
                        continue
                return None

            dt_inicio = _parse_date(data_inicio_str) if data_inicio_str else None
            dt_fim    = _parse_date(data_fim_str)    if data_fim_str    else None

            def _avaria_no_intervalo(av):
                av_dict   = av.to_dict() if hasattr(av, 'to_dict') else av
                data_str  = av_dict.get(campo_filtro, '')
                if not data_str:
                    return False
                dt = _parse_date(str(data_str)[:10])
                if dt is None:
                    return False
                if dt_inicio and dt < dt_inicio:
                    return False
                if dt_fim and dt > dt_fim:
                    return False
                return True

            filtradas = [av for av in filtradas if _avaria_no_intervalo(av)]
            logger.info(
                f"   📅 Filtro data ({campo_filtro}): "
                f"{data_inicio_str} → {data_fim_str}: {len(filtradas)} avarias"
            )

        return filtradas

    def _get_all_item_avarias(self):
        """
        Busca todos os itens de avaria da aba 'Itens_Avarias'.
        ⚡ USA CACHE: get_wms_itens_cached() em vez de get_all_records() direto.
        """
        if not self.sheets.connected:
            return []
    
        try:
            records = self.sheets.get_wms_itens_cached()
            itens = [ItemAvaria.from_dict(r) for r in records if r.get('avaria_id')]
            
            try:
                validacoes = self.sheets.get_wms_validacoes_cached()
                validacoes_por_avaria = {}
                for v in validacoes:
                    av_id = str(v.get('avaria_id', '')).strip()
                    if av_id:
                        if av_id not in validacoes_por_avaria:
                            validacoes_por_avaria[av_id] = []
                        validacoes_por_avaria[av_id].append(v)
                        
                itens_dict = {i.id_etiqueta: i for i in itens if i.id_etiqueta}
                for item in itens:
                    if item.status_etiqueta == 'ativa' and item.localizacao != item.codlocaldest:
                        # Achamos um item pendente. Vamos calcular sua localização real.
                        # 1. Obter a cadeia de etiquetas até a 00
                        cadeia = [item.id_etiqueta]
                        curr_anterior = item.etiqueta_anterior
                        while curr_anterior:
                            cadeia.append(curr_anterior)
                            ant = itens_dict.get(curr_anterior)
                            curr_anterior = ant.etiqueta_anterior if ant else None
                            
                        # 2. Procurar a última validação APROVADA que pertence a essa cadeia
                        vals = validacoes_por_avaria.get(item.avaria_id, [])
                        # Vamos ordenar por data se possível, mas como estão na ordem de inserção, iterar de trás pra frente é razoável
                        # ou simplesmente achar as validações aprovadas da cadeia
                        aprovadas = [v for v in vals if v.get('status_validacao', '').strip().lower() == 'aprovado']
                        
                        # Procuramos a última aprovada cuja origem ou destino está na cadeia
                        loc_aprovada = None
                        for v in reversed(aprovadas):
                            orig = str(v.get('id_etiqueta_origem', '')).strip()
                            dest = str(v.get('id_etiqueta_destino', '')).strip()
                            if orig in cadeia or (dest and dest in cadeia):
                                loc_aprovada = str(v.get('codlocal_destino', '')).strip()
                                break
                                
                        if loc_aprovada:
                            item.localizacao = loc_aprovada
                        else:
                            # Nenhuma aprovação. A localização física é a origem da triagem original (00)
                            # ou a origem do próprio item se não achar a 00
                            origem = None
                            for ant_id in reversed(cadeia):
                                ant = itens_dict.get(ant_id)
                                if ant and ant.codlocalorig:
                                    origem = ant.codlocalorig
                                    break
                            if origem:
                                item.localizacao = origem
                                
            except Exception as e:
                logger.error(f"Erro ao inferir localizações de avarias pendentes: {e}")
                
            return itens
        except Exception as e:
            logger.error(f"Erro ao buscar itens de avaria: {e}")
            return []

    def get_untriaged_avarias(self, empresa: str = None) -> List[Dict]:
        """
        ✅ CORRIGIDO: Retorna avarias pendentes com estrutura compatível
        ✅ NOVO: Filtra etiquetas com status 'baixada'
        
        Args:
            empresa: ✅ NOVO - Filtrar por empresa (Matriz, Empresax, Thomas)
            
        Returns:
            Lista de dicionários: {'avaria': obj, 'item': obj, 'rowspan': int, 'first_row': bool}
        """
        try:
            # 1. Obter avarias pendentes (filtradas por empresa)
            all_avarias = self.get_all_avarias(empresa=empresa)
            pendentes_headers = []
            
            for av in all_avarias:
                etiqueta_impressa = (av.etiqueta_inicial_impressa == 'sim')
                triagem_finalizada = av.is_finalizada
                
                # Avaria está pendente se não tem etiqueta impressa OU não está finalizada
                if not (etiqueta_impressa and triagem_finalizada):
                    pendentes_headers.append(av)
            
            if empresa:
                logger.info(f"Avarias pendentes encontradas: {len(pendentes_headers)} (empresa: {empresa})")
            else:
                logger.info(f"Avarias pendentes encontradas: {len(pendentes_headers)}")
            
            # 2. Obter todos os itens e agrupar por avaria_id
            items_by_avaria = {}
            avaria_ids_com_itens = set()
            
            # Mapeamento para rastrear ancestrais das etiquetas
            ancestrais_map = {}
            
            try:
                all_itens = self._get_all_item_avarias()
                logger.info(f"{len(all_itens)} registros de itens carregados para avarias pendentes")
                
                # Primeiro passo: mapear todas as relações de ancestralidade
                for item in all_itens:
                    etq_id = str(item.id_etiqueta or '').strip()
                    etq_ant = str(item.etiqueta_anterior or '').strip()
                    if etq_id and etq_ant:
                        ancestrais_map[etq_id] = etq_ant

                for item in all_itens:
                    raw_id = str(item.avaria_id or '').strip()
                    if not raw_id:
                        continue
                    avaria_ids_com_itens.add(raw_id)
                    
                    status_etiq = item.status_etiqueta
                    etiqueta_anterior = str(item.etiqueta_anterior or '').strip()
                    
                    if status_etiq == 'baixada':
                        continue
                        
                    if raw_id not in items_by_avaria:
                        items_by_avaria[raw_id] = []
                    items_by_avaria[raw_id].append(item)
            except Exception as e:
                logger.warning(f"Erro ao buscar itens de avaria (avarias ainda serão exibidas): {e}")

            # 2.5 Obter etiquetas com lote pendente
            # Mapa: id_etiqueta → codlocal_destino da validação pendente
            etiquetas_com_lote_pendente: dict = {}
            try:
                from modules.wms.routes.validacao_estoque_routes import _service as _val_svc
                if _val_svc:
                    lotes_pendentes = _val_svc.listar_lotes(status='Pendente', codemp='', dias=365, empresa=empresa)
                    val_ids = set()
                    for lote in lotes_pendentes:
                        import json
                        v_ids_raw = lote.get('ids_validacoes', '[]')
                        if isinstance(v_ids_raw, str):
                            try:
                                v_ids_list = json.loads(v_ids_raw)
                            except:
                                v_ids_list = []
                        else:
                            v_ids_list = v_ids_raw
                        for v_id in v_ids_list:
                            val_ids.add(str(v_id).strip())
                            
                    if val_ids:
                        pendencias = _val_svc.listar_pendencias(status='Pendente', codemp='', dias=365, empresa=empresa)
                        for p in pendencias:
                            if str(p.get('id_validacao', '')).strip() in val_ids:
                                etq_orig    = str(p.get('id_etiqueta_origem', '')).strip()
                                etq_dest    = str(p.get('id_etiqueta_destino', '')).strip()
                                codlocal_p  = str(p.get('codlocal_destino', '')).strip()
                                if etq_orig:
                                    etiquetas_com_lote_pendente[etq_orig] = codlocal_p
                                if etq_dest:
                                    etiquetas_com_lote_pendente[etq_dest] = codlocal_p
            except Exception as e:
                logger.warning(f"Erro ao buscar lotes pendentes em get_untriaged_avarias: {e}")

            # 4. Montar lista plana com estrutura compatível (mais recentes primeiro)
            pendentes_headers.reverse()
            resultado = []

            for avaria in pendentes_headers:
                itens_avaria = items_by_avaria.get(str(avaria.id).strip(), [])
                num_itens = len(itens_avaria)
                
                if num_itens == 0:
                    # Se a avaria tem itens no sheet mas todos são baixada, não há
                    # nada a processar — omitir da fila de registro para não poluir a tela.
                    # Se a avaria nunca teve itens (avaria_id ausente em Itens_Avarias), exibir
                    # para que o operador possa identificar e tratar o caso.
                    if str(avaria.id).strip() in avaria_ids_com_itens:
                        continue
                    resultado.append({
                        'avaria': avaria,  # Objeto Avaria completo
                        'item': None,
                        'has_lote_pendente': False,
                        'rowspan': 1,
                        'first_row': True
                    })
                else:
                    # Avaria com itens - uma linha por item
                    def _best_item_date(campo):
                        for it in itens_avaria:
                            v = str(getattr(it, campo, '') or '')
                            if v.strip():
                                return v[:10]
                        return ''
                    
                    best_triagem       = _best_item_date('data_triagem')
                    best_identificacao = _best_item_date('data_identificacao')
                    
                    for idx, item in enumerate(itens_avaria):
                        # Atribuir o booleano de pendência no próprio objeto para facilitar no Jinja
                        etq_id      = getattr(item, 'id_etiqueta', '')
                        
                        # Rastrear ancestralidade para herdar pendência (ex: 00 baixada herda para 08 ativa)
                        is_pendente = False
                        codlocal_p  = ''
                        curr_etq    = etq_id
                        while curr_etq:
                            if curr_etq in etiquetas_com_lote_pendente:
                                is_pendente = True
                                codlocal_p  = etiquetas_com_lote_pendente[curr_etq]
                                break
                            curr_etq = ancestrais_map.get(curr_etq, '')
                            
                        setattr(item, 'has_lote_pendente', is_pendente)
                        setattr(item, 'codlocal_pendente', codlocal_p)
                        
                        resultado.append({
                            'avaria':                    avaria,
                            'item':                      item,
                            'has_lote_pendente':         is_pendente,
                            'codlocal_pendente':         codlocal_p,
                            'rowspan':                   num_itens if idx == 0 else 0,
                            'first_row':                 (idx == 0),
                            'best_data_triagem':         best_triagem,
                            'best_data_identificacao':   best_identificacao,
                        })
            
            return resultado
            
        except Exception as e:
            logger.error(f"Erro ao buscar avarias pendentes: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []
    
    def atualizar_avaria(self, avaria_id: str, updates: dict) -> Tuple[bool, str]:
        """
        Atualiza campos específicos de uma avaria existente usando batch_update
        
        ✅ CORREÇÃO CRÍTICA: Usa batch_update com valueInputOption='USER_ENTERED'
        para garantir que números sejam salvos como números, não como texto
        
        Args:
            avaria_id: ID da avaria a ser atualizada
            updates: Dicionário com campos e valores a atualizar
            
        Returns:
            Tuple[bool, str]: (sucesso, mensagem)
        """
        if not self.sheets.connected:
            return False, "Google Sheets não conectado"
        
        try:
            ws = self.sheets.sheet.worksheet("Avarias")
            
            # 1⃣ Encontrar linha da avaria e cabeçalhos
            all_data = ws.get_all_values()
            headers = all_data[0]
            id_col = headers.index('id') + 1
            
            avaria_row = None
            for idx, row in enumerate(all_data[1:], start=2):
                if len(row) > id_col - 1 and row[id_col - 1] == avaria_id:
                    avaria_row = idx
                    break

            if not avaria_row:
                return False, f"Avaria {avaria_id} não encontrada"

            logger.info(f"Atualizando avaria {avaria_id} (linha {avaria_row})...")
            logger.info(f"   Total de linhas na planilha: {len(all_data)}")
            
            # 2⃣ Lista de campos numéricos (devem ser salvos como float/int, não string)
            campos_numericos = {
                'valor_nf', 'valor_nfd', 'valor_nfp', 'valor_indenizacao',
                'quantidade', 'volume', 'reembolso_pct'
            }
            
            # 3⃣ Preparar batch de atualizações
            # Vamos usar batch_update com valueInputOption='USER_ENTERED'
            # Isso faz com que o Sheets interprete "123.45" como número, não texto
            
            batch_data = []
            updated_fields = []
            
            for field, value in updates.items():
                if field not in headers:
                    logger.warning(f"   Campo '{field}' não existe no cabeçalho, ignorando")
                    continue
                
                col_idx = headers.index(field) + 1
                cell_address = self._column_letter(col_idx) + str(avaria_row)
                
                # 4⃣ Converter valores numéricos para formato correto
                if field in campos_numericos:
                    try:
                        # Converter para string primeiro para tratar valores None/vazios
                        value_str = str(value).strip() if value else ''
                        
                        if not value_str or value_str.lower() in ['none', 'nan', '']:
                            # Valor vazio
                            final_value = ''
                        else:
                            # Limpar formatação brasileira (R$, vírgulas, pontos de milhar)
                            value_str = value_str.replace('R$', '').replace(' ', '').strip()
                            
                            # Se tem ponto E vírgula, é formato brasileiro (1.234,56)
                            if '.' in value_str and ',' in value_str:
                                value_str = value_str.replace('.', '').replace(',', '.')
                            # Se tem apenas vírgula, trocar por ponto
                            elif ',' in value_str:
                                value_str = value_str.replace(',', '.')
                            
                            # Converter para float
                            value_float = float(value_str)
                            
                            # CRÍTICO: Salvar como número (float), não string
                            # O Google Sheets API precisa receber NUMBER, não "NUMBER"
                            final_value = value_float
                            
                            logger.info(f"   '{field}' ({cell_address}): {value} → {value_float}")
                    
                    except (ValueError, TypeError) as e:
                        logger.warning(f"   Erro ao converter '{field}' = '{value}' para float: {e}")
                        # Fallback: salvar como string
                        final_value = str(value)
                else:
                    # Campos não numéricos: salvar como string
                    final_value = str(value) if value is not None else ''
                
                # Adicionar ao batch
                batch_data.append({
                    'range': cell_address,
                    'values': [[final_value]]
                })
                
                updated_fields.append(field)
            
            # 5⃣ Executar batch_update
            if batch_data:
                logger.info(f"DEBUG - Batch a ser enviado:")
                for item in batch_data:
                    logger.info(f"   {item['range']}: {item['values'][0][0]}")
                
                # CRÍTICO: valueInputOption='RAW'
                # Isso faz com que os valores sejam salvos exatamente como enviados
                ws.batch_update(batch_data, value_input_option='RAW')
                
                logger.info(f"Batch update executado com {len(batch_data)} células")
                
                # 6⃣ VERIFICAÇÃO: Ler valores salvos para confirmar
                # (Isso é temporário para debug)
                logger.info(f"DEBUG - Verificando valores salvos:")
                for field in ['valor_nf', 'valor_nfd', 'valor_indenizacao']:
                    if field in headers:
                        col_idx = headers.index(field) + 1
                        cell_address = self._column_letter(col_idx) + str(avaria_row)
                        try:
                            saved_value = ws.acell(cell_address).value
                            logger.info(f"   {field}: {saved_value}")
                        except Exception as e:
                            logger.warning(f"   {field}: erro ao ler - {e}")
            
            logger.info(f"Avaria {avaria_id} atualizada: {', '.join(updated_fields)}")
            return True, "Avaria atualizada com sucesso"
            
        except Exception as e:
            logger.error(f"Erro ao atualizar avaria {avaria_id}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False, str(e)
    
    
    def _column_letter(self, col_idx: int) -> str:
        """
        Converte índice de coluna (1-based) para letra(s) do Excel
        Exemplos: 1 -> A, 2 -> B, 27 -> AA, 28 -> AB
        """
        result = ""
        while col_idx > 0:
            col_idx -= 1
            result = chr(col_idx % 26 + 65) + result
            col_idx //= 26
        return result
    
    def get_item_avaria_por_seq(self, avaria_id: str, sequencia: int) -> Optional[dict]:
        """Retorna o item de avaria como dict ou None se não encontrado."""
        if not self.sheets.connected:
            return None
        try:
            ws = self.sheets.sheet.worksheet("Itens_Avarias")
            all_data = ws.get_all_values()
            if len(all_data) < 2:
                return None
            headers = all_data[0]
            seq_str = str(sequencia)
            try:
                id_col  = headers.index('avaria_id')
                seq_col = headers.index('sequencia')
            except ValueError:
                return None
            for row in all_data[1:]:
                if (len(row) > max(id_col, seq_col)
                        and row[id_col] == avaria_id
                        and row[seq_col] == seq_str):
                    return {h: (row[i] if i < len(row) else '') for i, h in enumerate(headers)}
        except Exception as e:
            logger.warning(f"get_item_avaria_por_seq({avaria_id}/{sequencia}): {e}")
        return None

    def ler_itens_avarias_raw(self):
        """
        Lê Itens_Avarias UMA vez e devolve (ws, headers, rows) para processamento em lote
        (evita reler a aba inteira a cada item). Devolve None se indisponível.
        """
        if not self.sheets.connected:
            return None
        try:
            ws = self.sheets.sheet.worksheet("Itens_Avarias")
            all_data = ws.get_all_values()
            if len(all_data) < 2:
                return None
            return ws, all_data[0], all_data[1:]
        except Exception as e:
            logger.warning(f"ler_itens_avarias_raw: {e}")
            return None

    @staticmethod
    def localizar_item_raw(headers: list, rows: list, avaria_id: str, sequencia: int):
        """Procura (avaria_id, sequencia) num snapshot de Itens_Avarias. Retorna (linha_na_planilha, dict) ou (None, None)."""
        try:
            id_col = headers.index('avaria_id')
            seq_col = headers.index('sequencia')
        except ValueError:
            return None, None
        seq_str = str(sequencia)
        for idx, row in enumerate(rows, start=2):
            if len(row) > max(id_col, seq_col) and row[id_col] == avaria_id and row[seq_col] == seq_str:
                return idx, {h: (row[i] if i < len(row) else '') for i, h in enumerate(headers)}
        return None, None

    def gravar_itens_avarias_lote(self, ws, headers: list, atualizacoes: list, novos_itens: list,
                                  alterado_por: str = '') -> None:
        """
        Grava em Itens_Avarias, em no máximo 2 chamadas à API:
          - atualizacoes: [(linha_na_planilha, {campo: valor})] → 1 batch_update
          - novos_itens:  [{campo: valor}] (dicts por cabeçalho) → 1 append_rows
        Mesma semântica de update_item_avaria / inserir_item_avaria (RAW, números com ponto).
        """
        from modules.wms.models.item_avaria import _parse_valor_item
        from datetime import datetime as _dt
        agora = _dt.now().strftime('%d/%m/%Y %H:%M:%S')

        cells = []
        for linha, campos in atualizacoes:
            campos = dict(campos)
            campos.setdefault('data_alteracao', agora)
            if alterado_por:
                campos.setdefault('alterado_por', alterado_por)
            for field, value in campos.items():
                if field not in headers:
                    continue
                if field in ('qtdneg', 'qtd_avariada', 'valor_item'):
                    value = _parse_valor_item(value)
                cells.append({
                    'range': f"{self._col_index_to_letter(headers.index(field) + 1)}{linha}",
                    'values': [[value if value is not None else '']],
                })
        if cells:
            ws.batch_update(cells, value_input_option='RAW')

        if novos_itens:
            linhas = []
            for item in novos_itens:
                item = dict(item)
                for f in ('qtdneg', 'qtd_avariada'):
                    if f in item:
                        item[f] = _parse_valor_item(item[f])
                if 'valor_item' in item:
                    item['valor_item'] = round(_parse_valor_item(item['valor_item']), 2)
                linhas.append([item.get(h, '') for h in headers])
            ws.append_rows(linhas, value_input_option='RAW')

        if cells or novos_itens:
            self.sheets.invalidate_wms_cache()

    def recusar_item_avaria(self, avaria_id: str, sequencia: int, qtd_recusada: float, operador_email: str) -> tuple:
        """
        Recusa um item de avaria, quebrando-o caso a recusa seja parcial, 
        de forma análoga ao comportamento do portal ASN.
        """
        avaria_id = str(avaria_id).strip()
        item = self.get_item_avaria_por_seq(avaria_id, sequencia)
        if not item:
            return False, "Item não encontrado."
            
        try:
            qtd_item = str(item.get('qtd_avariada') or item.get('qtdneg') or '').strip()
            if qtd_item and qtd_item != '0' and qtd_item != '0.0':
                qtd_total = int(float(qtd_item.replace(',', '.')))
            else:
                av_qtd = 0
                avaria = self.get_avaria_by_id(avaria_id)
                if avaria and str(avaria.quantidade).strip():
                    av_qtd = avaria.quantidade
                else:
                    try:
                        todas_raw = self.sheets.sheet.worksheet("Avarias").get_all_records()
                        for r in todas_raw:
                            if str(r.get('id', '')).strip() == avaria_id:
                                av_qtd = r.get('quantidade', 0)
                                break
                    except Exception as fallback_e:
                        logger.warning(f"Fallback avaria falhou: {fallback_e}")
                
                qtd_total = int(float(str(av_qtd or 0).strip().replace(',', '.') or 0))
        except (ValueError, TypeError):
            qtd_total = 0
            
        qtd_recusada_int = int(qtd_recusada)
        if qtd_recusada_int <= 0 or qtd_recusada_int > qtd_total:
            return False, f"Quantidade inválida. Máximo disponível: {qtd_total}"
            
        qtd_aceita = qtd_total - qtd_recusada_int
        
        try:
            if qtd_aceita > 0:
                # Quebra parcial
                self.update_item_avaria(avaria_id, sequencia, operador_email, qtd_avariada=qtd_aceita, qtdneg=qtd_aceita)
                nova_seq = sequencia * 100 + 99
                self.inserir_item_avaria(
                    avaria_id=avaria_id, sequencia=nova_seq,
                    codprod=item.get('codprod', ''), descrprod=item.get('descrprod', ''),
                    qtdneg=qtd_recusada_int, qtd_avariada=qtd_recusada_int,
                    codlocalorig=item.get('codlocalorig', ''), codgrupoprod=item.get('codgrupoprod', ''),
                    codemp=item.get('codemp', ''),
                    valor_item=float(str(item.get('valor_item') or 0).replace(',', '.') or 0),
                    status_etiqueta='recusada'
                )
            else:
                # Recusa total
                self.update_item_avaria(avaria_id, sequencia, operador_email, qtd_avariada=qtd_recusada_int, status_etiqueta='recusada')
                
            return True, "Item recusado com sucesso."
        except Exception as e:
            logger.error(f"Erro ao recusar item_avaria {avaria_id}/{sequencia}: {e}")
            return False, str(e)

    def inserir_item_avaria(
        self,
        avaria_id: str,
        sequencia: int,
        codprod: str,
        descrprod: str,
        qtdneg: float,
        qtd_avariada: float,
        localizacao: str = '',
        valor_item: float = 0,
        codlocalorig: str = '',
        codlocaldest: str = '',
        codgrupoprod: str = '',
        codemp: str = '',
        status_etiqueta: str = 'pendente',
    ) -> Tuple[bool, str]:
        """
        Insere um novo item de avaria no fim da planilha Itens_Avarias
        (Usado quando um item é dividido/splitado durante edição)
        
        Args:
            avaria_id: ID da avaria
            sequencia: Número de seqüência do item (já calculado pelo frontend)
            codprod: Código do produto
            descrprod: Descrição do produto
            qtdneg: Quantidade negociada
            qtd_avariada: Quantidade avariada (normalmente 1 para itens splitados)
            localizacao: Localização física
            valor_item: Valor unitário do item
            codlocalorig: Código do local de origem
            codlocaldest: Código do local de destino
            codgrupoprod: Código do grupo do produto
            codemp: Código da empresa
        
        Returns:
            Tuple[bool, str]: (Sucesso, Mensagem)
        """
        if not self.sheets.connected:
            return False, "Google Sheets não conectado"
        
        try:
            ws = self.sheets.sheet.worksheet("Itens_Avarias")
            
            # Obter cabeçalhos para garantir ordem correta
            all_data = ws.get_all_values()
            if len(all_data) < 1:
                return False, "Planilha Itens_Avarias sem cabeçalho"
            
            headers = all_data[0]
            
            # Preparar nova linha (mesma estrutura do salvar_avaria)
            from modules.wms.models.item_avaria import _parse_valor_item
            nova_linha = {
                'avaria_id': avaria_id,
                'sequencia': str(sequencia),
                'codprod': codprod,
                'descrprod': descrprod,
                'qtdneg': _parse_valor_item(qtdneg),
                'qtd_avariada': _parse_valor_item(qtd_avariada),
                'localizacao': localizacao,
                'valor_item': round(_parse_valor_item(valor_item), 2),
                'codlocalorig': codlocalorig,
                'codlocaldest': codlocaldest,
                'codgrupoprod': codgrupoprod,
                'codemp': codemp,
                'id_etiqueta': '',  # Será gerado depois
                'status_etiqueta': status_etiqueta
            }
            
            # Construir array de valores na ordem do cabeçalho
            valores_linha = []
            for header in headers:
                valores_linha.append(nova_linha.get(header, ''))
            
            # Inserir no fim da planilha usando o serviço centralizado
            # Isso garante que value_input_option='USER_ENTERED' seja usado e campos numéricos sejam tratados
            self.sheets.append_row('Itens_Avarias', valores_linha)
            
            logger.info(f"Item inserido: Avaria={avaria_id}, Seq={sequencia}, Prod={codprod}, Qtd={qtd_avariada}")
            
            return True, f"Item seq={sequencia} inserido com sucesso"
            
        except Exception as e:
            logger.error(f"Erro ao inserir item avaria: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False, str(e)

    def atualizar_data_pagamento(self, avaria_id, data_pagamento, observacao_adicional='', usuario='sistema', empresa=None):
        """
        Atualiza a data de pagamento de uma avaria
        
        Args:
            avaria_id: ID da avaria
            data_pagamento: Data no formato YYYY-MM-DD
            observacao_adicional: Observação adicional (opcional)
            usuario: Usuário que está fazendo a atualização
            empresa: Código da empresa para validação
        
        Returns:
            bool: True se atualizado com sucesso
        """
        try:
            if not self.sheets.connected:
                logger.warning("WMS: Sem conexão com Sheets")
                return False
            
            ws = self.sheets.sheet.worksheet(self.worksheet_name)
            avarias_data = ws.get_all_values()
            
            if not avarias_data or len(avarias_data) < 2:
                return False
            
            headers = avarias_data[0]
            
            # Encontrar índices das colunas
            try:
                col_id = headers.index('id')
                col_data_pagamento = headers.index('data_pagamento')
                col_observacao = headers.index('observacao')
                col_empresa = headers.index('empresa') if 'empresa' in headers else None
            except ValueError as e:
                logger.error(f"Coluna não encontrada: {e}")
                return False
            
            # Buscar avaria
            linha_avaria = None
            for idx, row in enumerate(avarias_data[1:], start=2):
                if row[col_id] == avaria_id:
                    # Validar empresa se fornecida
                    if empresa and col_empresa is not None and row[col_empresa] != empresa:
                        logger.warning(f"Avaria {avaria_id} não pertence à empresa {empresa}")
                        return False
                    
                    linha_avaria = idx
                    break
            
            if not linha_avaria:
                logger.warning(f"Avaria {avaria_id} não encontrada")
                return False
            
            # Atualizar data de pagamento
            ws.update_cell(linha_avaria, col_data_pagamento + 1, data_pagamento)
            
            # Adicionar observação se fornecida
            if observacao_adicional:
                observacao_atual = ws.cell(linha_avaria, col_observacao + 1).value or ''
                timestamp = datetime.now().strftime('%d/%m/%Y %H:%M')
                nova_observacao = f"{observacao_atual}\n[{timestamp}] {usuario}: {observacao_adicional}".strip()
                ws.update_cell(linha_avaria, col_observacao + 1, nova_observacao)
            
            logger.info(f"Data de pagamento atualizada para avaria {avaria_id}: {data_pagamento}")
            return True
            
        except Exception as e:
            logger.error(f"Erro ao atualizar data de pagamento: {e}")
            return False

    # ── COBRANÇAS ────────────────────────────────────────────────────────────────────────

    _COBRANCAS_SHEET = 'WMS_Cobranças'
    _COBRANCAS_HEADERS = ['id', 'id_avaria', 'empresa', 'tentativa', 'data_cobranca', 'usuario', 'observacao', 'tipo_cobranca']

    def _get_or_create_cobranças_ws(self):
        try:
            ws = self.sheets.sheet.worksheet(self._COBRANCAS_SHEET)
            # Garante que o cabeçalho inclui todas as colunas esperadas
            try:
                header = ws.row_values(1)
                missing = [col for col in self._COBRANCAS_HEADERS if col not in header]
                if missing:
                    for col in missing:
                        ws.add_cols(1)
                        col_idx = len(header) + missing.index(col) + 1
                        ws.update_cell(1, col_idx, col)
            except Exception as e:
                logger.warning(f"Não foi possível verificar/atualizar cabeçalho de cobranças: {e}")
            return ws
        except Exception:
            ws = self.sheets.sheet.add_worksheet(self._COBRANCAS_SHEET, rows=1000, cols=len(self._COBRANCAS_HEADERS))
            ws.append_row(self._COBRANCAS_HEADERS)
            return ws

    def get_cobranças_avaria(self, avaria_id: str, empresa: str = '') -> list:
        if not self.sheets.connected:
            return []
        try:
            ws = self._get_or_create_cobranças_ws()
            rows = ws.get_all_records()
            result = [r for r in rows if str(r.get('id_avaria', '')) == str(avaria_id)]
            if empresa:
                result = [r for r in result if str(r.get('empresa', '')) == str(empresa)]
            result.sort(key=lambda r: r.get('tentativa', 0))
            return result
        except Exception as e:
            logger.error(f"Erro ao buscar cobranças da avaria {avaria_id}: {e}")
            return []

    def registrar_cobrança(self, avaria_id: str, observacao: str, usuario: str, empresa: str = '', tipo_cobranca: str = 'Indenização') -> tuple:
        if not self.sheets.connected:
            return False, 'Google Sheets não conectado', {}
        try:
            ws = self._get_or_create_cobranças_ws()
            existentes = self.get_cobranças_avaria(avaria_id, empresa)
            tentativa  = len(existentes) + 1
            from datetime import datetime as _dt
            now_str = _dt.now().strftime('%d/%m/%Y %H:%M:%S')
            import uuid
            novo_id = f'COB{_dt.now().strftime("%Y%m%d%H%M%S")}{str(uuid.uuid4())[:4].upper()}'
            row = [novo_id, avaria_id, empresa, tentativa, now_str, usuario, observacao, tipo_cobranca]
            ws.append_row(row)
            record = dict(zip(self._COBRANCAS_HEADERS, row))
            logger.info(f"Cobrança {tentativa}ª registrada para avaria {avaria_id} (tipo={tipo_cobranca})")
            return True, f'{tentativa}ª cobrança registrada', record
        except Exception as e:
            logger.error(f"Erro ao registrar cobrança: {e}")
            return False, str(e), {}
