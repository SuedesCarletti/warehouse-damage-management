"""
modules/wms/services/historico_estoque_service.py

v4.0 - Captura diária de fechamento às 20:00 BRT:
  - Job diário executa às 20:00 horário de Brasília (UTC-3)
  - Registro diário via TGFEST (posição de fechamento do dia)
  - Data mínima: 01/01/2024 — datas anteriores são ignoradas
  - Histórico já salvo no WMS_Estoque_Snap (via E3 loadView + VW_ESTOQUE_DASH)
  - Snapshot do gráfico (WMS_Estoque_Snap) via E0 → WMS_Evolucao_Estoque
  - Nunca bloqueia a app: background thread isolado, timeout por query
"""

import logging
import time
import threading
import calendar
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

COLUNAS_SCHEMA = ['data_posicao', 'cod_empresa', 'codlocal', 'descrlocal', 'qtd_total', 'valor_total', 'data_captura']
ABA_NOME = 'WMS_Evolucao_Estoque'

# Datas anteriores a este limite não são capturadas nem processadas
DATA_MINIMA_ESTOQUE = date(2024, 1, 1)


class HistoricoEstoqueService:
    def __init__(self, sankhya_service, sheets_service, cache_sankhya_service=None):
        self.sankhya = sankhya_service
        self.sheets = sheets_service
        self.cache_sankhya = cache_sankhya_service
        self.aba_nome = ABA_NOME

        self._garantir_aba_existe()
        self._iniciar_job_diario()

    # SETUP DA ABA

    def _garantir_aba_existe(self):
        """Verifica/cria aba e migra schema antigo se necessário."""
        try:
            ws = self.sheets.sheet.worksheet(self.aba_nome)
            cabecalho = ws.row_values(1)
            if 'codlocal' not in cabecalho:
                logger.info(f"Migrando schema de {self.aba_nome} para v3.0...")
                self._migrar_schema_v2(ws)
            else:
                logger.info(f"Aba {self.aba_nome} ok (schema v3.0)")
        except Exception:
            try:
                ws = self.sheets.sheet.add_worksheet(title=self.aba_nome, rows=5000, cols=len(COLUNAS_SCHEMA))
                ws.append_row(COLUNAS_SCHEMA, value_input_option='RAW')
                logger.info(f"Aba {self.aba_nome} criada.")
            except Exception as e:
                logger.error(f"Erro ao criar aba {self.aba_nome}: {e}")

    def _migrar_schema_v2(self, ws):
        try:
            dados = ws.get_all_values()
            if not dados:
                ws.append_row(COLUNAS_SCHEMA, value_input_option='RAW')
                return
            nova_planilha = [COLUNAS_SCHEMA]
            for linha in dados[1:]:
                if len(linha) < 2:
                    continue
                nova_planilha.append([
                    linha[0] if len(linha) > 0 else '',
                    linha[1] if len(linha) > 1 else '',
                    '?',
                    'Migrado (sem local)',
                    linha[2] if len(linha) > 2 else '',
                    linha[3] if len(linha) > 3 else '',
                    linha[4] if len(linha) > 4 else '',
                ])
            ws.clear()
            ws.update(nova_planilha, value_input_option='USER_ENTERED')
            logger.info(f"Migração: {len(nova_planilha)-1} linha(s).")
        except Exception as e:
            logger.error(f"Erro ao migrar schema: {e}")

    # DIAGNÓSTICO — valida colunas reais do TGFITE no Sankhya

    def diagnosticar_colunas_tgfite(self) -> Dict:
        """
        Consulta quais colunas de localização existem no TGFITE.
        Use ANTES de ativar o retroativo para confirmar os nomes corretos.
        Retorna dict com colunas encontradas e SQL sugerido.
        """
        query = """
            SELECT COLUMN_NAME
            FROM ALL_TAB_COLUMNS
            WHERE TABLE_NAME = 'TGFITE'
              AND COLUMN_NAME LIKE 'CODLOCAL%'
            ORDER BY COLUMN_NAME
        """
        try:
            resultado = self._executar_dbexplorer_raw(query)
            colunas = [str(r[0]) for r in resultado] if resultado else []
            logger.info(f"Colunas CODLOCAL* em TGFITE: {colunas}")
            return {
                'success': True,
                'colunas_tgfite': colunas,
                'tem_codlocaldest': 'CODLOCALDEST' in colunas,
                'tem_codlocalorig': 'CODLOCALORIG' in colunas,
                'tem_codlocal': 'CODLOCAL' in colunas,
                'recomendacao': (
                    'SQL usa CODLOCALDEST/CODLOCALORIG — OK'
                    if 'CODLOCALDEST' in colunas
                    else 'ATENÇÃO: CODLOCALDEST não encontrado — verificar estrutura TGFITE'
                ),
            }
        except Exception as e:
            logger.error(f"Erro ao diagnosticar TGFITE: {e}")
            return {'success': False, 'erro': str(e)}

    # CAPTURA — snapshot por local (TGFEST = hoje, TGFITE+TGFCAB = histórico)

    def capturar_e_salvar_data(self, data_ref_str: str, empresa: str = None) -> bool:
        """
        Captura posição de estoque por CODEMP+CODLOCAL para uma data e salva no Sheets.
        Verifica duplicidade antes de gravar.
        Datas anteriores a DATA_MINIMA_ESTOQUE (01/01/2024) são ignoradas.
        """
        # Bloqueia captura de dados antes de 01/01/2024
        try:
            _dt = datetime.strptime(data_ref_str, '%d/%m/%Y').date()
            if _dt < DATA_MINIMA_ESTOQUE:
                logger.info(f"⏭{data_ref_str} anterior a {DATA_MINIMA_ESTOQUE.strftime('%d/%m/%Y')} — ignorado.")
                return True
        except ValueError:
            pass  # data inválida — deixa prosseguir, _capturar_e_gravar vai tratar

        logger.info(f"Capturando estoque para: {data_ref_str}")
        try:
            ws = self.sheets.sheet.worksheet(self.aba_nome)
            if self._ja_foi_capturado(ws, data_ref_str, empresa):
                logger.info(f"⏭{data_ref_str} já existe. Ignorando.")
                return True
            return self._capturar_e_gravar(ws, data_ref_str, empresa)
        except Exception as e:
            logger.error(f"Erro ao capturar {data_ref_str}: {e}")
            return False

    def _capturar_e_gravar(self, ws, data_ref_str: str, empresa: str = None) -> bool:
        """Executa a consulta Sankhya e grava no Sheets. ws já validado."""
        hoje = datetime.now().date()
        try:
            data_alvo = datetime.strptime(data_ref_str, '%d/%m/%Y').date()
        except ValueError:
            data_alvo = hoje

        if data_alvo >= hoje:
            logger.info(f"TGFEST (snapshot atual) para {data_ref_str}")
            linhas = self._consultar_snapshot_atual(empresa)
        else:
            logger.info(f"TGFITE+TGFCAB (histórico) para {data_ref_str}")
            linhas = self._consultar_via_movimentos(data_ref_str, empresa)

        if linhas is None:
            logger.error(f"Sankhya retornou erro para {data_ref_str}.")
            return False
        if not linhas:
            logger.info(f"ℹSem estoque para {data_ref_str} (resultado vazio).")
            return True

        mapa_locais = self._get_mapa_locais()
        data_captura = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
        linhas_sheets = [
            [
                data_ref_str,
                str(codemp_val),
                str(codlocal),
                mapa_locais.get(str(codlocal), {}).get('descrlocal', ''),
                float(qtd),
                float(valor),
                data_captura,
            ]
            for codlocal, codemp_val, qtd, valor in linhas
        ]

        if linhas_sheets:
            ws.append_rows(linhas_sheets, value_input_option='USER_ENTERED')
            logger.info(f"{len(linhas_sheets)} linha(s) → {data_ref_str}.")
        return True

    # CARGA RETROATIVA — somente início/fim de cada mês + mês atual diário

    def processar_retroativo(self, data_inicio_str: str, empresa: str = None):
        """
        Captura retroativa via TGFEST (posição atual) revertendo movimentos (TGFITE+TGFCAB).
        Data mínima: 01/01/2024 — datas anteriores são ignoradas.

        Estratégia por período:
          • Meses passados  → apenas dia 01 e último dia de cada mês (abertura + fechamento)
          • Mês atual       → cada dia do início do mês até ontem (progressão diária)

        Exemplo: data_inicio=01/01/2024, hoje=11/05/2026
          Jan/24–Abr/26: 01/01, 31/01, ... (2 datas/mês = ~58 datas)
          Mai/26 atual:  01/05, 02/05, ..., 10/05 (10 datas)
        """
        try:
            data_inicio = datetime.strptime(data_inicio_str, '%d/%m/%Y').date()
        except ValueError:
            logger.error(f"Data inválida: {data_inicio_str}")
            return

        # Garante que não captura dados anteriores a 01/01/2024
        if data_inicio < DATA_MINIMA_ESTOQUE:
            logger.info(f"data_inicio {data_inicio_str} anterior ao limite — ajustado para {DATA_MINIMA_ESTOQUE.strftime('%d/%m/%Y')}.")
            data_inicio = DATA_MINIMA_ESTOQUE

        hoje = date.today()
        ontem = hoje - timedelta(days=1)
        mes_corrente_inicio = hoje.replace(day=1)

        # Monta lista de datas a capturar
        datas_alvo: List[date] = []

        # Meses passados: primeiro e último dia de cada mês
        mes = data_inicio.replace(day=1)
        while mes < mes_corrente_inicio:
            primeiro = mes
            ultimo = mes.replace(day=calendar.monthrange(mes.year, mes.month)[1])
            if primeiro >= data_inicio:
                datas_alvo.append(primeiro)
            if ultimo != primeiro and ultimo < hoje:
                datas_alvo.append(ultimo)
            # Avança para o próximo mês
            if mes.month == 12:
                mes = mes.replace(year=mes.year + 1, month=1)
            else:
                mes = mes.replace(month=mes.month + 1)

        # Mês atual: cada dia do início até ontem
        dia = max(mes_corrente_inicio, data_inicio)
        while dia <= ontem:
            datas_alvo.append(dia)
            dia += timedelta(days=1)

        total = len(datas_alvo)
        logger.info(f"Retroativo: {total} data(s) a processar (empresa={empresa or 'todas'}).")
        logger.info(f"   Estratégia: início/fim de cada mês + diário do mês atual.")

        # Pré-carrega datas já no Sheets (1 chamada apenas)
        datas_capturadas: set = set()
        try:
            ws = self.sheets.sheet.worksheet(self.aba_nome)
            todos = ws.get_all_values()
            headers = todos[0] if todos else []
            idx_d = headers.index('data_posicao') if 'data_posicao' in headers else 0
            idx_e = headers.index('cod_empresa')  if 'cod_empresa'  in headers else 1
            for row in todos[1:]:
                if len(row) <= idx_d:
                    continue
                chave = f"{row[idx_d]}|{row[idx_e]}" if (empresa and len(row) > idx_e) else row[idx_d]
                datas_capturadas.add(chave)
            logger.info(f"{len(datas_capturadas)} data(s) já no Sheets.")
        except Exception as e:
            logger.warning(f"Pré-carga de datas falhou: {e}. Sem dedup.")
            ws = None

        # Processa cada data com pausa para não sobrecarregar Sankhya nem Sheets
        ok = 0
        for i, data_alvo in enumerate(datas_alvo, 1):
            data_str = data_alvo.strftime('%d/%m/%Y')
            chave = f"{data_str}|{empresa}" if empresa else data_str

            if chave in datas_capturadas:
                logger.info(f"⏭[{i}/{total}] {data_str} já existe.")
                continue

            try:
                if ws is None:
                    ws = self.sheets.sheet.worksheet(self.aba_nome)
                sucesso = self._capturar_e_gravar(ws, data_str, empresa)
            except Exception as e:
                logger.error(f"[{i}/{total}] {data_str}: {e}")
                sucesso = False

            if sucesso:
                datas_capturadas.add(chave)
                ok += 1
                logger.info(f"[{i}/{total}] {data_str} OK.")
            else:
                logger.warning(f"[{i}/{total}] {data_str} falhou.")

            time.sleep(3)  # 3s entre datas: ~1 req/3s está bem abaixo dos limites

        logger.info(f"Retroativo concluído: {ok}/{total} data(s) gravada(s).")

    # ESTRATÉGIAS DE CONSULTA SANKHYA

    def _consultar_snapshot_atual(self, empresa: str = None) -> Optional[List]:
        """Posição ATUAL via TGFEST — para snapshot do dia corrente às 23:00."""
        filtro = f"AND EST.CODEMP = {empresa}" if empresa else ""
        query = f"""
            SELECT
                EST.CODEMP,
                EST.CODLOCAL,
                SUM(EST.ESTOQUE)                 AS QTD_TOTAL,
                SUM(EST.ESTOQUE * EST.CUSTOUNIT)  AS VALOR_TOTAL
            FROM TGFEST EST
            WHERE EST.ESTOQUE > 0
            {filtro}
            GROUP BY EST.CODEMP, EST.CODLOCAL
            ORDER BY EST.CODEMP, EST.CODLOCAL
        """
        return self._executar_dbexplorer_raw(query)

    def _consultar_via_movimentos(self, data_ref_str: str, empresa: str = None) -> Optional[List]:
        """
        Calcula posição de estoque revertendo movimentos recentes a partir do saldo atual (TGFEST).
        Abordagem ultra otimizada (Reverse Lookup) que previne Timeouts (Erro 502) no Oracle.
        """
        filtro_est = f"AND EST.CODEMP = {empresa}" if empresa else ""
        filtro_ite = f"AND ITE.CODEMP = {empresa}" if empresa else ""
        
        query = f"""
            SELECT CODEMP, CODLOCAL,
                   SUM(DELTA_QTD)    AS QTD_TOTAL,
                   SUM(DELTA_VALOR)  AS VALOR_TOTAL
            FROM (
                -- 1. Posição ATUAL (hoje)
                SELECT EST.CODEMP, 
                       EST.CODLOCAL, 
                       EST.ESTOQUE AS DELTA_QTD, 
                       EST.ESTOQUE * NVL(EST.CUSTOUNIT, 0) AS DELTA_VALOR
                FROM TGFEST EST
                INNER JOIN TGFPRO PRO ON EST.CODPROD = PRO.CODPROD
                WHERE EST.ESTOQUE <> 0
                  AND PRO.USOPROD = 'R'
                  {filtro_est}

                UNION ALL

                -- 2. REVERTER Entradas (subtrair do local) ocorridas APÓS a data alvo
                SELECT ITE.CODEMP,
                       ITE.CODLOCALORIG AS CODLOCAL,
                       -ITE.QTDNEG AS DELTA_QTD,
                       -(ITE.QTDNEG * NVL(ITE.VLRUNIT, 0)) AS DELTA_VALOR
                FROM TGFITE ITE
                INNER JOIN TGFPRO PRO ON ITE.CODPROD = PRO.CODPROD
                JOIN TGFCAB CAB ON ITE.NUNOTA = CAB.NUNOTA
                WHERE CAB.STATUSNOTA = 'L'
                  AND CAB.DTNEG >= TO_DATE('{data_ref_str}', 'DD/MM/YYYY') + 1
                  AND CAB.TIPMOV IN ('E', 'C', 'D')
                  AND ITE.CODLOCALORIG IS NOT NULL
                  AND PRO.USOPROD = 'R'
                  {filtro_ite}

                UNION ALL

                -- 3. REVERTER Saídas/Transferências (adicionar de volta ao local) ocorridas APÓS a data alvo
                SELECT ITE.CODEMP,
                       ITE.CODLOCALORIG AS CODLOCAL,
                       ITE.QTDNEG AS DELTA_QTD,
                       (ITE.QTDNEG * NVL(ITE.VLRUNIT, 0)) AS DELTA_VALOR
                FROM TGFITE ITE
                INNER JOIN TGFPRO PRO ON ITE.CODPROD = PRO.CODPROD
                JOIN TGFCAB CAB ON ITE.NUNOTA = CAB.NUNOTA
                WHERE CAB.STATUSNOTA = 'L'
                  AND CAB.DTNEG >= TO_DATE('{data_ref_str}', 'DD/MM/YYYY') + 1
                  AND CAB.TIPMOV IN ('S', 'V', 'T')
                  AND ITE.CODLOCALORIG IS NOT NULL
                  AND PRO.USOPROD = 'R'
                  {filtro_ite}
            ) MOVS
            GROUP BY CODEMP, CODLOCAL
            HAVING SUM(DELTA_QTD) <> 0
            ORDER BY CODEMP, CODLOCAL
        """
        return self._executar_dbexplorer_raw(query)

    # LOADVIEW — consulta view Sankhya via CRUDServiceProvider.loadView

    def _executar_via_loadview(self, view_name: str, fields: List[str], where: str) -> Optional[List[Dict]]:
        """
        Consulta uma view Oracle via CRUDServiceProvider.loadView.
        Retorna lista de dicts com campos nomeados (chaves em maiúsculas), ou None em caso de erro.
        """
        if not self.sankhya or not getattr(self.sankhya, 'enabled', False):
            return None
        try:
            fields_str = ','.join(fields)
            response = self.sankhya.load_view(view_name, fields_str, where)

            if not response or response.get('status') != '1':
                msg = response.get('statusMessage', str(response)) if response else 'sem resposta'
                logger.warning(f"loadView {view_name} erro: {msg}")
                return None

            body = response.get('responseBody', {})
            # loadView usa 'records'/'record' (não 'rows'/'row')
            rows_raw = body.get('rows') or body.get('records') or []

            logger.info(f"loadView {view_name} body keys: {list(body.keys())} | rows_raw type: {type(rows_raw).__name__}")

            # Formato: {"row":[...]} ou {"record":[...]} ou objeto único
            if isinstance(rows_raw, dict):
                rows_raw = rows_raw.get('row') or rows_raw.get('record') or []
                if isinstance(rows_raw, dict):
                    rows_raw = [rows_raw]

            if not rows_raw:
                logger.info(f"ℹloadView {view_name}: rows vazio (view inacessível ou sem dados para o filtro)")
                return []

            resultado = []
            for row in rows_raw:
                if not isinstance(row, dict):
                    continue
                # Normaliza chaves para maiúsculas (Sankhya pode retornar minúsculas)
                row_upper = {k.upper(): v for k, v in row.items()}
                rec = {}
                for field in fields:
                    fname = field.strip().upper()
                    val_obj = row_upper.get(fname, '')
                    if isinstance(val_obj, dict):
                        rec[fname] = val_obj.get('$', '')
                    else:
                        rec[fname] = str(val_obj) if val_obj is not None else ''
                resultado.append(rec)

            logger.info(f"loadView {view_name}: {len(resultado)} linha(s).")
            return resultado

        except Exception as e:
            logger.error(f"loadView {view_name}: {e}")
            return None

    # DBEXPLORER — executa SQL via Sankhya e parseia resposta

    def _executar_dbexplorer_raw(self, query_sql: str, timeout: int = 30) -> Optional[List]:
        """
        Executa SQL via DbExplorerSP.executeQuery e retorna lista de listas.
        Trata os 3 formatos de resposta conhecidos do Sankhya.
        """
        if not self.sankhya or not getattr(self.sankhya, 'enabled', False):
            return None
        try:
            import json
            token = self.sankhya._get_valid_token()
            if not token:
                return None
            self.sankhya.token = token

            url = self.sankhya.get_url('DbExplorerSP.executeQuery')
            payload = {
                "serviceName": "DbExplorerSP.executeQuery",
                "requestBody": {"sql": query_sql},
            }
            
            logger.info(f"[DbExplorerSP] Payload enviado: {json.dumps(payload)}")
            response = self.sankhya.post_request(url, payload, timeout=timeout)
            logger.info(f"[DbExplorerSP] Resposta recebida: {json.dumps(response)}")

            if not response or response.get('status') != '1':
                msg = response.get('statusMessage', str(response)) if response else 'sem resposta'
                logger.warning(f"DbExplorer erro: {msg}")
                return None

            body = response.get('responseBody', {})
            rows_raw = body.get('rows', [])

            # Formato 3: dict com sub-lista {'row': [...]}
            if isinstance(rows_raw, dict):
                rows_raw = rows_raw.get('row', [])
                if isinstance(rows_raw, dict):
                    rows_raw = [rows_raw]

            if not rows_raw:
                return []

            # Log de diagnóstico para identificar formato (apenas 1x por chamada)
            sample = rows_raw[0] if rows_raw else None
            logger.debug(f"DbExplorer sample row type={type(sample).__name__}: {str(sample)[:150]}")

            resultado = []
            for row in rows_raw:
                if isinstance(row, list):
                    # Formato 1: lista simples [v0, v1, v2, ...]
                    resultado.append(row)
                elif isinstance(row, dict):
                    raw_str = row.get('$', '')
                    if raw_str:
                        # Formato 2: {'$': 'v0;v1;v2;...'}
                        resultado.append(str(raw_str).split(';'))
                    else:
                        # Formato 4: dict com chaves nomeadas
                        resultado.append(list(row.values()))

            logger.info(f"DbExplorer: {len(resultado)} linha(s).")
            return resultado

        except Exception as e:
            logger.error(f"DbExplorer: {e}")
            return None

    # CONSULTA DE EVOLUÇÃO (API / dashboard)

    def consultar_evolucao(
        self,
        data_inicio: str = None,
        data_fim: str = None,
        cod_empresa: str = None,
        codlocal: str = None,
    ) -> Dict:
        """
        Lê dados de evolução do Sheets (WMS_Evolucao_Estoque).
        Rápido e sem chamada ao Sankhya — usado pelo dashboard.

        Consulta via VW_ESTOQUE_DASH disponível em _consultar_evolucao_via_view()
        e no endpoint /api/estoque/snapshot-sankhya (modal "Buscar Sankhya").
        """
        return self._consultar_evolucao_sheets(data_inicio, data_fim, cod_empresa, codlocal)

    def _consultar_evolucao_via_view(
        self,
        cod_empresa: str = None,
        codlocal: str = None,
        meses_atras: int = 13
    ) -> Dict:
        """
        Consulta VW_ESTOQUE_DASH — tenta loadView primeiro, depois DbExplorerSP.
        A view retorna um registro por (CODEMP, CODPROD, CODLOCAL, MES_REF) com
        o saldo de fechamento do mês. Não há colunas DT, QTDMOV ou TIPO_PONTO.
        """
        base_parts = []
        if cod_empresa:
            base_parts.append(f"CODEMP = {int(cod_empresa)}")
        if codlocal:
            base_parts.append(f"CODLOCAL = {int(codlocal)}")

        hoje = date.today()
        mes_inicio = hoje.replace(day=1)
        for _ in range(meses_atras - 1):
            mes_inicio = (mes_inicio - timedelta(days=1)).replace(day=1)
        mes_iso = mes_inicio.strftime('%Y-%m-%d')

        where_sql      = " AND ".join(base_parts + [f"MES_REF >= DATE '{mes_iso}'"])
        where_sql_dbex = " AND ".join(base_parts + [f"MES_REF >= TO_DATE('{mes_iso}', 'YYYY-MM-DD')"])

        fields = [
            'CODEMP', 'CODPROD', 'DESCRPROD', 'CODGRUPOPROD', 'DESCRGRUPOPROD',
            'DESCRMARCA', 'CODLOCAL', 'DESCRLOCAL', 'MES_REF', 'SALDO', 'VALOR_TOTAL',
        ]

        logger.info(f"[Evolução] loadView VW_ESTOQUE_DASH | WHERE: {where_sql}")
        linhas = self._executar_via_loadview('VW_ESTOQUE_DASH', fields, where_sql)

        if linhas is None:
            fields_str = ','.join(fields)
            query_sql = f"SELECT {fields_str} FROM VW_ESTOQUE_DASH WHERE {where_sql_dbex}"
            logger.info(f"[Evolução] Fallback DbExplorerSP | SQL: {query_sql}")
            linhas_raw = self._executar_dbexplorer_raw(query_sql)
            if linhas_raw is None:
                return {'registros': [], 'locais': [], 'datas': [], 'total': 0, 'erro': 'Erro ao consultar VW_ESTOQUE_DASH'}
            linhas = [dict(zip(fields, [str(v) if v is not None else '' for v in row])) for row in linhas_raw]

        registros, locais_set, datas_set = [], {}, set()

        for row in linhas:
            emp            = str(row.get('CODEMP', '')          or '')
            codprod        = str(row.get('CODPROD', '')         or '')
            descrprod      = str(row.get('DESCRPROD', '')       or '')
            codgrupoprod   = str(row.get('CODGRUPOPROD', '')    or '')
            descrgrupoprod = str(row.get('DESCRGRUPOPROD', '')  or '')
            descrmarca     = str(row.get('DESCRMARCA', '')      or '')
            cl             = str(row.get('CODLOCAL', '')        or '')
            descr          = str(row.get('DESCRLOCAL', '')      or '')
            mes_ref_raw    = str(row.get('MES_REF', '')         or '')

            try: saldo = float(str(row.get('SALDO', '0') or '0').replace(',', '.'))
            except ValueError: saldo = 0.0

            try: valor = float(str(row.get('VALOR_TOTAL', '0') or '0').replace(',', '.'))
            except ValueError: valor = 0.0

            # Normaliza MES_REF para DD/MM/YYYY (usado como data_posicao)
            mes_ref_dd = mes_ref_raw
            if mes_ref_raw and '-' in mes_ref_raw:
                try:
                    mes_ref_dd = datetime.strptime(mes_ref_raw[:10], '%Y-%m-%d').strftime('%d/%m/%Y')
                except ValueError:
                    pass

            rec = {
                'data_posicao':   mes_ref_dd,
                'cod_empresa':    emp,
                'codlocal':       cl,
                'descrlocal':     descr,
                'codprod':        codprod,
                'descrprod':      descrprod,
                'codgrupoprod':   codgrupoprod,
                'descrgrupoprod': descrgrupoprod,
                'descrmarca':     descrmarca,
                'mes_ref':        mes_ref_raw,
                'qtd_total':      saldo,
                'valor_total':    valor,
                'data_captura':   'view',
            }
            registros.append(rec)
            datas_set.add(mes_ref_dd)
            if cl and cl not in locais_set:
                locais_set[cl] = descr

        registros.sort(key=lambda r: r.get('data_posicao', ''))
        locais = [{'codlocal': k, 'descrlocal': v} for k, v in sorted(locais_set.items())]
        logger.info(f"VW_ESTOQUE_DASH: {len(registros)} registro(s).")
        return {'registros': registros, 'locais': locais, 'datas': sorted(datas_set), 'total': len(registros)}

    def _consultar_snapshot_data_especifica(
        self,
        data_ref: str,
        cod_empresa: str = None,
        codlocal: str = None,
        data_fim: str = None
    ) -> Dict:
        """
        Consulta VW_ESTOQUE_DASH filtrando por MES_REF.
        A view armazena o fechamento mensal — não há colunas DT ou TIPO_PONTO.
        Usa a mesma regra de mapeamento de data→MES_REF de consultar_abertura_mes_ref.
        """
        def parse_date(d_str):
            for fmt in ('%Y-%m-%d', '%d/%m/%Y'):
                try: return datetime.strptime(d_str[:10], fmt)
                except ValueError: pass
            return datetime.now()

        def _mes_anterior(dt):
            if dt.month == 1:
                return datetime(dt.year - 1, 12, 1)
            return datetime(dt.year, dt.month - 1, 1)

        def _mes_ref_de(dt):
            if dt.day == 1:
                return _mes_anterior(dt)
            return datetime(dt.year, dt.month, 1)

        dt_ini = parse_date(data_ref)
        dt_fim = parse_date(data_fim) if (data_fim and data_fim != data_ref) else None

        mr_ini = _mes_ref_de(dt_ini)
        mr_fim = _mes_ref_de(dt_fim) if dt_fim else mr_ini
        mr_ini_iso  = mr_ini.strftime('%Y-%m-%d')
        mr_fim_iso  = mr_fim.strftime('%Y-%m-%d')
        mr_ini_ddmm = mr_ini.strftime('%d/%m/%Y')
        mr_fim_ddmm = mr_fim.strftime('%d/%m/%Y')

        where_parts = []
        if cod_empresa:
            where_parts.append(f"CODEMP = {int(cod_empresa)}")
        if codlocal:
            where_parts.append(f"CODLOCAL = {int(codlocal)}")

        where_parts_lv   = where_parts + [f"MES_REF BETWEEN DATE '{mr_ini_iso}' AND DATE '{mr_fim_iso}'"]
        where_parts_dbex = where_parts + [f"TRUNC(MES_REF) BETWEEN TO_DATE('{mr_ini_ddmm}','DD/MM/YYYY') AND TO_DATE('{mr_fim_ddmm}','DD/MM/YYYY')"]

        where_sql      = " AND ".join(where_parts_lv)
        where_sql_dbex = " AND ".join(where_parts_dbex)
        fields = ['CODEMP', 'CODLOCAL', 'DESCRLOCAL', 'MES_REF', 'SALDO', 'VALOR_TOTAL']

        logger.info(f"[Snapshot] loadView VW_ESTOQUE_DASH | WHERE: {where_sql}")
        linhas = self._executar_via_loadview('VW_ESTOQUE_DASH', fields, where_sql)

        if linhas is None:
            fields_str = ','.join(fields)
            query_sql = f"SELECT {fields_str} FROM VW_ESTOQUE_DASH WHERE {where_sql_dbex}"
            logger.info(f"[Snapshot] Fallback DbExplorerSP | SQL: {query_sql}")
            linhas_raw = self._executar_dbexplorer_raw(query_sql)
            if linhas_raw is None:
                return {'registros': [], 'locais': [], 'total': 0, 'erro': 'Erro ao consultar VW_ESTOQUE_DASH'}
            linhas = [dict(zip(fields, [str(v) if v is not None else '' for v in row])) for row in linhas_raw]

        agrupado = {}
        locais_set = {}

        for row in linhas:
            emp     = str(row.get('CODEMP', '')     or '')
            cl      = str(row.get('CODLOCAL', '')   or '')
            descr   = str(row.get('DESCRLOCAL', '') or '')
            mr_raw  = str(row.get('MES_REF', '')    or '')

            try: saldo = float(str(row.get('SALDO', '0') or '0').replace(',', '.'))
            except ValueError: saldo = 0.0

            try: valor = float(str(row.get('VALOR_TOTAL', '0') or '0').replace(',', '.'))
            except ValueError: valor = 0.0

            mr_dd = mr_raw
            if mr_raw and '-' in mr_raw:
                try:
                    mr_dd = datetime.strptime(mr_raw[:10], '%Y-%m-%d').strftime('%d/%m/%Y')
                except ValueError:
                    pass

            if not cl:
                continue

            chave = f"{mr_raw}_{emp}_{cl}"
            if chave not in agrupado:
                agrupado[chave] = {
                    'data_posicao': mr_dd,
                    'cod_empresa':  emp,
                    'codlocal':     cl,
                    'descrlocal':   descr,
                    'qtd_total':    0.0,
                    'valor_total':  0.0,
                }

            agrupado[chave]['qtd_total']   += saldo
            agrupado[chave]['valor_total'] += valor
            if cl not in locais_set:
                locais_set[cl] = descr

        return {
            'registros': list(agrupado.values()),
            'locais':    [{'codlocal': k, 'descrlocal': v} for k, v in sorted(locais_set.items())],
            'total':     len(agrupado),
        }

    def consultar_abertura_mes_ref(
        self,
        data_ref: str,
        data_fim: str = None,
        cod_empresa: str = None,
        codlocal: str = None,
    ) -> Dict:
        """
        Consulta VW_ESTOQUE_DASH agrupado por (MES_REF, CODLOCAL).

        A view armazena o saldo de FECHAMENTO de cada mês (um registro por
        CODPROD/CODLOCAL/MES_REF). O saldo de ABERTURA de um mês é igual ao
        saldo de FECHAMENTO do mês anterior.

        Regra de datas → MES_REF consultado:
          - Dia 1 do mês → MES_REF = 1º do mês ANTERIOR (abertura = fechamento do mês anterior)
          - Qualquer outro dia → MES_REF = 1º do mesmo mês (fechamento daquele mês)

        Para range 01/01–01/02 gera três pontos:
          01/01/2026 → MES_REF 2025-12-01 (abertura de jan = fechamento de dez)
          31/01/2026 → MES_REF 2026-01-01 (fechamento de jan = abertura de fev, demonstrativo)
          01/02/2026 → MES_REF 2026-01-01 (abertura de fev = fechamento de jan)
        """
        def _parse(d):
            for fmt in ('%Y-%m-%d', '%d/%m/%Y'):
                try: return datetime.strptime(d[:10], fmt)
                except ValueError: pass
            return datetime.now()

        dt_ini = _parse(data_ref)
        dt_fim = _parse(data_fim) if data_fim else None

        def _mes_anterior(dt):
            if dt.month == 1:
                return datetime(dt.year - 1, 12, 1)
            return datetime(dt.year, dt.month - 1, 1)

        def _mes_ref_de(dt):
            """Mapeia uma data para o MES_REF correto na view."""
            if dt.day == 1:
                return _mes_anterior(dt)
            return datetime(dt.year, dt.month, 1)

        pontos = []  # [(display_date_str, mes_ref_iso)]
        if dt_fim is None or dt_ini.date() == dt_fim.date():
            mr = _mes_ref_de(dt_ini)
            pontos.append((dt_ini.strftime('%d/%m/%Y'), mr.strftime('%Y-%m-%d')))
        else:
            cur_ano, cur_mes = dt_ini.year, dt_ini.month
            fim_ano, fim_mes = dt_fim.year, dt_fim.month
            while (cur_ano, cur_mes) <= (fim_ano, fim_mes):
                primeiro = datetime(cur_ano, cur_mes, 1)
                ult_d    = calendar.monthrange(cur_ano, cur_mes)[1]
                ultimo   = datetime(cur_ano, cur_mes, ult_d)
                # 1º do mês dentro do range → abertura (usa mês anterior)
                if dt_ini.date() <= primeiro.date() <= dt_fim.date():
                    mr_p = _mes_ref_de(primeiro)
                    pontos.append((primeiro.strftime('%d/%m/%Y'), mr_p.strftime('%Y-%m-%d')))
                # Último do mês dentro do range → fechamento (usa mesmo mês, demonstrativo)
                if dt_ini.date() <= ultimo.date() <= dt_fim.date():
                    mr_u = datetime(cur_ano, cur_mes, 1)
                    pontos.append((ultimo.strftime('%d/%m/%Y'), mr_u.strftime('%Y-%m-%d')))
                if cur_mes == 12: cur_ano += 1; cur_mes = 1
                else: cur_mes += 1

        if not pontos:
            return {'registros': [], 'total': 0, 'mes_ref': data_ref}

        meses_unicos = sorted({p[1] for p in pontos})
        mr_ini_iso   = meses_unicos[0]
        mr_fim_iso   = meses_unicos[-1]
        mr_ini_dd    = datetime.strptime(mr_ini_iso, '%Y-%m-%d').strftime('%d/%m/%Y')
        mr_fim_dd    = datetime.strptime(mr_fim_iso, '%Y-%m-%d').strftime('%d/%m/%Y')

        base_parts = []
        if cod_empresa:
            base_parts.append(f"CODEMP = {int(cod_empresa)}")
        if codlocal:
            base_parts.append(f"CODLOCAL = {int(codlocal)}")

        where_parts      = base_parts + [
            f"MES_REF BETWEEN DATE '{mr_ini_iso}' AND DATE '{mr_fim_iso}'",
        ]
        where_parts_dbex = base_parts + [
            f"TRUNC(MES_REF) BETWEEN TO_DATE('{mr_ini_dd}','DD/MM/YYYY') AND TO_DATE('{mr_fim_dd}','DD/MM/YYYY')",
        ]
        where_sql      = " AND ".join(where_parts)
        where_sql_dbex = " AND ".join(where_parts_dbex)
        fields = ['CODLOCAL', 'DESCRLOCAL', 'MES_REF', 'SALDO', 'VALOR_TOTAL']

        logger.info(f"[AberturaMesRef] loadView VW_ESTOQUE_DASH | WHERE: {where_sql}")
        linhas = self._executar_via_loadview('VW_ESTOQUE_DASH', fields, where_sql)

        if linhas is None:
            fields_str = ','.join(fields)
            query_sql = f"SELECT {fields_str} FROM VW_ESTOQUE_DASH WHERE {where_sql_dbex}"
            logger.info(f"[AberturaMesRef] Fallback DbExplorerSP | SQL: {query_sql}")
            linhas_raw = self._executar_dbexplorer_raw(query_sql)
            if linhas_raw is None:
                return {'registros': [], 'total': 0, 'mes_ref': data_ref,
                        'erro': 'Erro ao consultar VW_ESTOQUE_DASH'}
            linhas = [dict(zip(fields, [str(v) if v is not None else '' for v in row]))
                      for row in linhas_raw]

        logger.info(f"[AberturaMesRef] Total de linhas retornadas: {len(linhas)}")

        def _norm_iso(d):
            for fmt in ('%Y-%m-%d', '%d/%m/%Y'):
                try: return datetime.strptime(str(d)[:10], fmt).strftime('%Y-%m-%d')
                except Exception: pass
            return ''

        # Agrupa por (mes_ref_iso, codlocal) somando SALDO e VALOR_TOTAL de todos os produtos
        por_mes = {}
        for row in linhas:
            cl    = str(row.get('CODLOCAL', '') or '')
            descr = str(row.get('DESCRLOCAL', '') or '')
            mr    = _norm_iso(row.get('MES_REF', ''))
            try: saldo = float(str(row.get('SALDO', '0') or '0').replace(',', '.'))
            except ValueError: saldo = 0.0
            try: valor = float(str(row.get('VALOR_TOTAL', '0') or '0').replace(',', '.'))
            except ValueError: valor = 0.0
            if not cl or not mr: continue
            chave = (mr, cl)
            if chave not in por_mes:
                por_mes[chave] = {'codlocal': cl, 'descrlocal': descr,
                                  'mes_ref': mr, 'saldo': 0.0, 'valor_total': 0.0}
            por_mes[chave]['saldo']       += saldo
            por_mes[chave]['valor_total'] += valor

        # Expande: para cada ponto (display_date, mes_ref_iso) gera uma linha por local
        registros = []
        for display_dd, mr_iso in pontos:
            for (mr, cl), dados in sorted(por_mes.items(), key=lambda x: x[0][1]):
                if mr != mr_iso: continue
                registros.append({
                    'codlocal':    cl,
                    'descrlocal':  dados['descrlocal'],
                    'mes_ref':     display_dd,
                    'saldo':       dados['saldo'],
                    'valor_total': dados['valor_total'],
                })

        return {
            'registros': registros,
            'total':     len(registros),
            'mes_ref':   pontos[0][0] if pontos else data_ref,
        }

    def _filtrar_por_data(self, resultado: Dict, data_inicio: str, data_fim: str) -> Dict:
        """Filtra registros já carregados por intervalo de datas."""
        try:
            dt_ini = datetime.strptime(data_inicio, '%d/%m/%Y') if data_inicio else None
            dt_fim = datetime.strptime(data_fim,    '%d/%m/%Y') if data_fim    else None
        except ValueError:
            return resultado

        filtrados = []
        for rec in resultado.get('registros', []):
            dp = rec.get('data_posicao', '')
            try:
                dt_rec = datetime.strptime(dp[:10], '%d/%m/%Y')
                if dt_ini and dt_rec < dt_ini:
                    continue
                if dt_fim and dt_rec > dt_fim:
                    continue
            except ValueError:
                pass
            filtrados.append(rec)

        datas_set = sorted({r['data_posicao'] for r in filtrados})
        locais_set = {}
        for r in filtrados:
            cl = r.get('codlocal', '')
            if cl and cl not in locais_set:
                locais_set[cl] = r.get('descrlocal', '')
        locais = [{'codlocal': k, 'descrlocal': v} for k, v in sorted(locais_set.items())]
        return {'registros': filtrados, 'locais': locais, 'datas': datas_set, 'total': len(filtrados)}

    def _consultar_evolucao_sheets(
        self,
        data_inicio: str = None,
        data_fim: str = None,
        cod_empresa: str = None,
        codlocal: str = None,
    ) -> Dict:
        """Lê dados de evolução do Sheets (fallback)."""
        try:
            ws = self.sheets.sheet.worksheet(self.aba_nome)
            rows = ws.get_all_values()
            if len(rows) < 2:
                return {'registros': [], 'locais': [], 'datas': [], 'total': 0}

            headers = rows[0]
            dt_ini = datetime.strptime(data_inicio, '%d/%m/%Y') if data_inicio else None
            dt_fim = datetime.strptime(data_fim,    '%d/%m/%Y') if data_fim    else None

            registros, locais_set, datas_set = [], {}, set()

            for row in rows[1:]:
                rec = dict(zip(headers, row + [''] * max(0, len(headers) - len(row))))

                if cod_empresa and rec.get('cod_empresa', '') not in ('', cod_empresa):
                    continue
                if codlocal and rec.get('codlocal', '') != str(codlocal):
                    continue

                data_pos = rec.get('data_posicao', '')
                if data_pos:
                    try:
                        dt_rec = datetime.strptime(data_pos[:10], '%d/%m/%Y')
                        if dt_ini and dt_rec < dt_ini:
                            continue
                        if dt_fim and dt_rec > dt_fim:
                            continue
                    except Exception:
                        pass

                try:
                    rec['qtd_total'] = float(str(rec.get('qtd_total', 0)).replace(',', '.'))
                except (ValueError, TypeError):
                    rec['qtd_total'] = 0.0
                try:
                    rec['valor_total'] = float(str(rec.get('valor_total', 0)).replace(',', '.'))
                except (ValueError, TypeError):
                    rec['valor_total'] = 0.0

                registros.append(rec)
                datas_set.add(data_pos)
                cl = rec.get('codlocal', '')
                if cl and cl not in locais_set:
                    locais_set[cl] = rec.get('descrlocal', '')

            registros.sort(key=lambda r: r.get('data_posicao', ''))
            locais = [{'codlocal': k, 'descrlocal': v} for k, v in sorted(locais_set.items())]
            return {'registros': registros, 'locais': locais, 'datas': sorted(datas_set), 'total': len(registros)}

        except Exception as e:
            logger.error(f"Erro ao consultar evolução (Sheets): {e}")
            return {'registros': [], 'locais': [], 'datas': [], 'total': 0}

    # SNAPSHOT GRÁFICO EVOLUÇÃO — leitura rápida via Sheets

    ABA_SNAP_GRAFICO  = 'WMS_Estoque_Snap'
    COLUNAS_SNAP      = ['mes_ref', 'codlocal', 'descrlocal', 'saldo', 'valor_total', 'atualizado_em']

    # status em memória: empresa → {processando, atualizado_em, erro}
    _snap_status: Dict[str, dict] = {}
    _snap_lock = threading.Lock()

    def get_snap_status(self, empresa: str = '') -> dict:
        return dict(self._snap_status.get(empresa or '', {
            'processando': False, 'atualizado_em': None, 'erro': None,
        }))

    def _set_snap_status(self, empresa: str, **kw):
        with self._snap_lock:
            cur = dict(self._snap_status.get(empresa or '', {}))
            cur.update(kw)
            self._snap_status[empresa or ''] = cur

    def _garantir_aba_snap(self):
        """Cria a aba WMS_Estoque_Snap se ainda não existir."""
        try:
            self.sheets.sheet.worksheet(self.ABA_SNAP_GRAFICO)
        except Exception:
            try:
                ws = self.sheets.sheet.add_worksheet(
                    title=self.ABA_SNAP_GRAFICO, rows=5000,
                    cols=len(self.COLUNAS_SNAP),
                )
                ws.append_row(self.COLUNAS_SNAP, value_input_option='RAW')
                logger.info(f"Aba {self.ABA_SNAP_GRAFICO} criada.")
            except Exception as e:
                logger.error(f"Erro ao criar aba {self.ABA_SNAP_GRAFICO}: {e}")

    def atualizar_snapshot_grafico(self, cod_empresa=None) -> Dict:
        """
        Agrega dados de estoque e salva snapshot por (MES_REF, CODLOCAL) em WMS_Estoque_Snap.
        Estratégias em ordem de prioridade (sem E3 — evita timeout de 6+ min):

          E0 (primária): WMS_Evolucao_Estoque → sem Sankhya, rápido (<1 s)
          E1 (fallback): loadView VW_ESTOQUE_DASH, janela completa 24 meses
          E2 (fallback): DbExplorerSP GROUP BY VW_ESTOQUE_DASH (timeout 90 s)

        Deve ser chamado em background thread — use iniciar_snapshot_grafico_async().
        """
        empresa = str(cod_empresa or '')
        self._set_snap_status(empresa, processando=True, erro=None)

        self._garantir_aba_snap()

        # Janela temporal: últimos 24 meses completos
        meses_atras = 24
        hoje_dt     = datetime.now()
        fields      = ['CODLOCAL', 'DESCRLOCAL', 'MES_REF', 'SALDO', 'VALOR_TOTAL']

        # Gera lista de meses (YYYY-MM-01) do mais antigo ao mais recente
        meses_lista = []
        for i in range(meses_atras, -1, -1):
            m = hoje_dt.month - i
            y = hoje_dt.year
            while m <= 0:
                m += 12
                y -= 1
            meses_lista.append(datetime(y, m, 1).strftime('%Y-%m-%d'))
        mr_ini_iso = meses_lista[0]
        mr_fim_iso = meses_lista[-1]

        linhas = None  # será preenchida pela primeira estratégia que funcionar

        # ── E0: WMS_Evolucao_Estoque → sem Sankhya, sem timeout ──
        logger.info("[SnapGrafico] E0 WMS_Evolucao_Estoque → agregando por (mês, local)…")
        try:
            ws_ev   = self.sheets.sheet.worksheet(self.aba_nome)
            rows_ev = ws_ev.get_all_values()
            if len(rows_ev) >= 2:
                headers_ev = [h.lower() for h in rows_ev[0]]
                hmap_ev    = {h: i for i, h in enumerate(headers_ev)}

                def _col_ev(row, col):
                    idx = hmap_ev.get(col, -1)
                    return row[idx] if 0 <= idx < len(row) else ''

                def _parse_dt_ev(s):
                    for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
                        try: return datetime.strptime(s[:10], fmt).date()
                        except ValueError: pass
                    return None

                # Limite inferior: início do mês mais antigo da janela
                mes_ini_date = datetime.strptime(mr_ini_iso, '%Y-%m-%d').date()

                por_chave_ev: Dict[str, dict] = {}
                for row_ev in rows_ev[1:]:
                    cl_ev = _col_ev(row_ev, 'codlocal')
                    if not cl_ev:
                        continue
                    if cod_empresa and _col_ev(row_ev, 'cod_empresa') not in ('', str(cod_empresa)):
                        continue
                    dt_ev = _parse_dt_ev(_col_ev(row_ev, 'data_posicao'))
                    if not dt_ev or dt_ev.replace(day=1) < mes_ini_date:
                        continue
                    mes_str = dt_ev.strftime('%Y-%m-01')
                    try: saldo_ev = float(str(_col_ev(row_ev, 'qtd_total')).replace(',', '.'))
                    except ValueError: saldo_ev = 0.0
                    try: valor_ev = float(str(_col_ev(row_ev, 'valor_total')).replace(',', '.'))
                    except ValueError: valor_ev = 0.0
                    descr_ev  = _col_ev(row_ev, 'descrlocal')
                    chave_ev  = f"{mes_str}|{cl_ev}"
                    # Mantém a posição de fechamento do mês (data mais recente do mês)
                    if chave_ev not in por_chave_ev or dt_ev > por_chave_ev[chave_ev]['_date']:
                        por_chave_ev[chave_ev] = {
                            'CODLOCAL': cl_ev, 'DESCRLOCAL': descr_ev,
                            'MES_REF': mes_str, 'SALDO': saldo_ev, 'VALOR_TOTAL': valor_ev,
                            '_date': dt_ev,
                        }

                if por_chave_ev:
                    linhas = list(por_chave_ev.values())
                    logger.info(f"[SnapGrafico] E0 OK — {len(linhas)} entradas (WMS_Evolucao_Estoque).")
                else:
                    logger.info("[SnapGrafico] E0 WMS_Evolucao_Estoque vazio — tentando Sankhya.")
        except Exception as e0e:
            logger.warning(f"[SnapGrafico] E0 falhou: {e0e}")

        # ── Fallback Sankhya — somente se E0 não produziu dados ──
        if linhas is None:
            # Valida Sankhya antes de qualquer consulta
            if not self.sankhya or not getattr(self.sankhya, 'enabled', False):
                msg = (
                    'WMS_Evolucao_Estoque vazio e Sankhya não configurado/desabilitado '
                    '(verifique SANKHYA_BASE_URL/SANKHYA_APPKEY ou popule WMS_Evolucao_Estoque)'
                )
                logger.error(f"[SnapGrafico] {msg}")
                self._set_snap_status(empresa, processando=False, erro=msg)
                return {'sucesso': False, 'erro': msg, 'total': 0}

            token = None
            try:
                token = self.sankhya._get_valid_token()
            except Exception as te:
                msg = f'WMS_Evolucao_Estoque vazio e falha ao obter token Sankhya: {te}'
                logger.error(f"[SnapGrafico] {msg}")
                self._set_snap_status(empresa, processando=False, erro=msg)
                return {'sucesso': False, 'erro': msg, 'total': 0}
            if not token:
                msg = 'WMS_Evolucao_Estoque vazio e token Sankhya não disponível — verifique credenciais'
                logger.error(f"[SnapGrafico] {msg}")
                self._set_snap_status(empresa, processando=False, erro=msg)
                return {'sucesso': False, 'erro': msg, 'total': 0}

            # Filtro de empresa
            empresa_parts_lv = []
            empresa_parts_db = ''
            if cod_empresa:
                try:
                    codemp_int = int(cod_empresa)
                    empresa_parts_lv.append(f"CODEMP = {codemp_int}")
                    empresa_parts_db = f"AND CODEMP = {codemp_int}"
                except (ValueError, TypeError):
                    pass

            # ── E1: loadView — janela completa de 24 meses (1 chamada) ──
            where_lv = " AND ".join(empresa_parts_lv + [
                f"MES_REF BETWEEN DATE '{mr_ini_iso}' AND DATE '{mr_fim_iso}'"
            ])
            try:
                logger.info(f"[SnapGrafico] E1 loadView janela completa | WHERE: {where_lv}")
                res = self._executar_via_loadview('VW_ESTOQUE_DASH', fields, where_lv)
                if res is not None:
                    linhas = res
                    logger.info(f"[SnapGrafico] E1 OK — {len(linhas)} linhas brutas.")
            except Exception as e:
                logger.warning(f"[SnapGrafico] E1 falhou: {e}")

            # ── E2: DbExplorer com GROUP BY (Oracle agrega, retorna poucas linhas) ──
            if linhas is None:
                sql_db = (
                    f"SELECT CODLOCAL, MAX(DESCRLOCAL) AS DESCRLOCAL, "
                    f"TRUNC(MES_REF,'MM') AS MES_REF, "
                    f"SUM(SALDO) AS SALDO, SUM(VALOR_TOTAL) AS VALOR_TOTAL "
                    f"FROM VW_ESTOQUE_DASH "
                    f"WHERE MES_REF BETWEEN TO_DATE('{mr_ini_iso}','YYYY-MM-DD') "
                    f"AND TO_DATE('{mr_fim_iso}','YYYY-MM-DD') "
                    f"{empresa_parts_db} "
                    f"GROUP BY CODLOCAL, TRUNC(MES_REF,'MM') "
                    f"ORDER BY TRUNC(MES_REF,'MM'), CODLOCAL"
                )
                try:
                    logger.info(f"[SnapGrafico] E2 DbExplorer GROUP BY | SQL: {sql_db}")
                    raw = self._executar_dbexplorer_raw(sql_db, timeout=90)
                    if raw is not None:
                        linhas = [dict(zip(fields, [str(v) if v is not None else '' for v in r]))
                                  for r in raw]
                        logger.info(f"[SnapGrafico] E2 OK — {len(linhas)} linhas agregadas.")
                except Exception as e:
                    logger.warning(f"[SnapGrafico] E2 falhou: {e}")

            # ── E3: loadView mês a mês — escrita INCREMENTAL após cada mês ──
            # Mais recente primeiro: chart exibe dados em ~15 s sem aguardar todo o histórico.
            # Pula meses já presentes no snap (idempotente).
            if linhas is None:
                logger.info(f"[SnapGrafico] E3 loadView mês a mês incremental ({len(meses_lista)} meses)…")

                # Pre-carrega snap existente para merge incremental
                snap_cache: Dict[str, list] = {}
                ws_snap_e3 = None
                try:
                    ws_snap_e3  = self.sheets.sheet.worksheet(self.ABA_SNAP_GRAFICO)
                    rows_snap = ws_snap_e3.get_all_values()
                    if len(rows_snap) >= 2:
                        for sr in rows_snap[1:]:
                            if len(sr) >= 2:
                                snap_cache[f"{sr[0]}|{sr[1]}"] = list(sr)
                    logger.info(f"[SnapGrafico] E3 snap pré-carregado: {len(snap_cache)} entradas.")
                except Exception as sce:
                    logger.warning(f"[SnapGrafico] E3 pré-carga snap falhou: {sce}")

                meses_lista_rev = list(reversed(meses_lista))  # mais recente primeiro
                total_meses     = len(meses_lista_rev)
                meses_ok        = 0

                for i, mes_iso in enumerate(meses_lista_rev, 1):
                    # Pula mês que já está no snap
                    if any(k.startswith(mes_iso) for k in snap_cache):
                        logger.info(f"[SnapGrafico] E3 {mes_iso}: já no snap — pulando.")
                        meses_ok += 1
                        self._set_snap_status(empresa, processando=True,
                                              progresso=f'{i}/{total_meses} meses', erro=None)
                        continue

                    where_mes = " AND ".join(empresa_parts_lv + [f"MES_REF = DATE '{mes_iso}'"])
                    try:
                        res_mes = self._executar_via_loadview('VW_ESTOQUE_DASH', fields, where_mes)
                        if res_mes:
                            # Agrega por (mes, codlocal)
                            agg_mes: Dict[str, dict] = {}
                            for row_m in res_mes:
                                cl_m   = str(row_m.get('CODLOCAL', '')   or '')
                                dc_m   = str(row_m.get('DESCRLOCAL', '') or '')
                                if not cl_m: continue
                                try: sd_m = float(str(row_m.get('SALDO', '0') or '0').replace(',', '.'))
                                except ValueError: sd_m = 0.0
                                try: vl_m = float(str(row_m.get('VALOR_TOTAL', '0') or '0').replace(',', '.'))
                                except ValueError: vl_m = 0.0
                                k_m = f"{mes_iso}|{cl_m}"
                                if k_m not in agg_mes:
                                    agg_mes[k_m] = {'mes_ref': mes_iso, 'codlocal': cl_m,
                                                    'descrlocal': dc_m, 'saldo': 0.0, 'valor_total': 0.0}
                                agg_mes[k_m]['saldo']       += sd_m
                                agg_mes[k_m]['valor_total'] += vl_m

                            agora_e3 = datetime.now().strftime('%d/%m/%Y %H:%M')
                            for k_m, v_m in agg_mes.items():
                                snap_cache[k_m] = [
                                    v_m['mes_ref'], v_m['codlocal'], v_m['descrlocal'],
                                    round(v_m['saldo'], 4), round(v_m['valor_total'], 4), agora_e3,
                                ]

                            # Grava snap incrementalmente (mais recente no topo)
                            if ws_snap_e3:
                                try:
                                    linhas_e3 = [self.COLUNAS_SNAP] + sorted(
                                        snap_cache.values(), key=lambda x: (x[0], x[1])
                                    )
                                    ws_snap_e3.clear()
                                    ws_snap_e3.update(linhas_e3, value_input_option='USER_ENTERED')
                                except Exception as we3:
                                    logger.warning(f"[SnapGrafico] E3 write {mes_iso} falhou: {we3}")

                            meses_ok += 1
                            self._set_snap_status(empresa, processando=True,
                                                  progresso=f'{i}/{total_meses} meses', erro=None)
                            logger.info(f"[SnapGrafico] E3 {mes_iso}: {len(agg_mes)} locais. "
                                        f"Snap total: {len(snap_cache)}.")
                        else:
                            logger.info(f"[SnapGrafico] E3 {mes_iso}: sem dados.")
                    except Exception as me3:
                        logger.warning(f"[SnapGrafico] E3 {mes_iso} falhou: {me3}")
                    time.sleep(0.3)

                if snap_cache:
                    # E3 teve sucesso (total ou parcial) — retorna antes do bloco comum
                    agora_e3 = datetime.now().strftime('%d/%m/%Y %H:%M')
                    total_e3 = len(snap_cache)
                    logger.info(f"[SnapGrafico] E3 concluída: {meses_ok}/{total_meses} meses. "
                                f"Snap: {total_e3} entradas.")
                    self._set_snap_status(empresa, processando=False,
                                         atualizado_em=agora_e3, erro=None, progresso=None)
                    return {'sucesso': True, 'total': total_e3, 'atualizado_em': agora_e3}

                logger.error(f"[SnapGrafico] E3 falhou em todos os {total_meses} meses.")

        if linhas is None:
            msg = (
                'WMS_Evolucao_Estoque vazio e VW_ESTOQUE_DASH inacessível via loadView e DbExplorer '
                '— verifique permissão/existência da view ou popule WMS_Evolucao_Estoque com o job diário'
            )
            logger.error(f"[SnapGrafico] {msg}")
            self._set_snap_status(empresa, processando=False, erro=msg)
            return {'sucesso': False, 'erro': msg, 'total': 0}

        if not linhas:
            msg = (
                f'WMS_Evolucao_Estoque vazio e VW_ESTOQUE_DASH sem registros '
                f'nos últimos {meses_atras} meses'
            )
            logger.warning(f"[SnapGrafico] {msg}")
            self._set_snap_status(empresa, processando=False, erro=msg)
            return {'sucesso': False, 'erro': msg, 'total': 0}

        # Agrupa por (MES_REF, CODLOCAL) — compatível com E0 e E1/E2
        por_chave: Dict[str, dict] = {}
        for row in linhas:
            cl    = str(row.get('CODLOCAL', '')   or '')
            descr = str(row.get('DESCRLOCAL', '') or '')
            mr    = str(row.get('MES_REF', '')    or '')
            # Normaliza MES_REF para YYYY-MM-DD
            for fmt in ('%Y-%m-%d', '%d/%m/%Y'):
                try:
                    mr = datetime.strptime(mr[:10], fmt).strftime('%Y-%m-%d')
                    break
                except (ValueError, TypeError):
                    pass
            if not cl or not mr:
                continue
            try: saldo = float(str(row.get('SALDO', '0') or '0').replace(',', '.'))
            except ValueError: saldo = 0.0
            try: valor = float(str(row.get('VALOR_TOTAL', '0') or '0').replace(',', '.'))
            except ValueError: valor = 0.0

            chave = f"{mr}|{cl}"
            if chave not in por_chave:
                por_chave[chave] = {'mes_ref': mr, 'codlocal': cl, 'descrlocal': descr,
                                    'saldo': 0.0, 'valor_total': 0.0}
            por_chave[chave]['saldo']       += saldo
            por_chave[chave]['valor_total'] += valor

        agora = datetime.now().strftime('%d/%m/%Y %H:%M')
        linhas_sheet = [self.COLUNAS_SNAP]
        for dados in sorted(por_chave.values(), key=lambda x: (x['mes_ref'], x['codlocal'])):
            linhas_sheet.append([
                dados['mes_ref'],
                dados['codlocal'],
                dados['descrlocal'],
                round(dados['saldo'], 4),
                round(dados['valor_total'], 4),
                agora,
            ])

        ws = self.sheets.sheet.worksheet(self.ABA_SNAP_GRAFICO)
        ws.clear()
        ws.update(linhas_sheet, value_input_option='USER_ENTERED')

        total = len(linhas_sheet) - 1
        logger.info(f"[SnapGrafico] Snapshot salvo: {total} local(is)/mês. Atualizado em {agora}.")
        self._set_snap_status(empresa, processando=False, atualizado_em=agora, erro=None)
        return {'sucesso': True, 'total': total, 'atualizado_em': agora}

    def iniciar_snapshot_grafico_async(self, cod_empresa=None):
        """Inicia atualizar_snapshot_grafico em background thread. Retorna imediatamente."""
        empresa = str(cod_empresa or '')
        status = self.get_snap_status(empresa)
        if status.get('processando'):
            return  # já está rodando
        t = threading.Thread(
            target=self.atualizar_snapshot_grafico,
            args=(cod_empresa,),
            daemon=True,
            name=f'snap-grafico-{empresa}',
        )
        t.start()

    # HELPER: gráfico diretamente de WMS_Evolucao_Estoque (sem Sankhya)

    def _construir_grafico_from_evolucao_sheets(
        self,
        codlocais=None,
        data_inicio=None,
        data_fim=None,
        cod_empresa=None,
    ) -> Optional[Dict]:
        """
        Lê WMS_Evolucao_Estoque e agrupa por mês/local para o gráfico.
        Para cada (mês, codlocal) usa o ÚLTIMO registro do mês (posição de fechamento).
        Retorna mesmo formato de construir_grafico_evolucao, ou None se vazio.
        """
        try:
            ws = self.sheets.sheet.worksheet(self.aba_nome)
            rows = ws.get_all_values()
            if len(rows) < 2:
                return None

            headers = [h.lower() for h in rows[0]]
            hmap = {h: i for i, h in enumerate(headers)}

            def _col(row, col):
                idx = hmap.get(col, -1)
                return row[idx] if 0 <= idx < len(row) else ''

            def _parse_dt(s):
                for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
                    try: return datetime.strptime(s[:10], fmt).date()
                    except ValueError: pass
                return None

            d_ini = _parse_dt(data_inicio) if data_inicio else None
            d_fim = _parse_dt(data_fim)    if data_fim    else None
            locais_filtro = {str(c).strip() for c in codlocais} if codlocais else None

            # (YYYY-MM, codlocal) → {date, saldo, valor, descr}
            por_mes_local: Dict = {}
            for row in rows[1:]:
                cl = _col(row, 'codlocal')
                if locais_filtro and cl not in locais_filtro:
                    continue
                if cod_empresa and _col(row, 'cod_empresa') not in ('', str(cod_empresa)):
                    continue
                dt = _parse_dt(_col(row, 'data_posicao'))
                if not dt:
                    continue
                if d_ini and dt < d_ini:
                    continue
                if d_fim and dt > d_fim:
                    continue
                mes = dt.strftime('%Y-%m')
                key = (mes, cl)
                try: saldo = float(str(_col(row, 'qtd_total')).replace(',', '.'))
                except ValueError: saldo = 0.0
                try: valor = float(str(_col(row, 'valor_total')).replace(',', '.'))
                except ValueError: valor = 0.0
                descr = _col(row, 'descrlocal')
                # Mantém a entrada mais recente do mês (posição de fechamento)
                if key not in por_mes_local or dt > por_mes_local[key]['date']:
                    por_mes_local[key] = {'date': dt, 'saldo': saldo, 'valor': valor, 'descr': descr}

            if not por_mes_local:
                return None

            meses_pt = {'01':'Jan','02':'Fev','03':'Mar','04':'Abr','05':'Mai','06':'Jun',
                        '07':'Jul','08':'Ago','09':'Set','10':'Out','11':'Nov','12':'Dez'}

            # Soma por mês
            por_mes: Dict[str, list] = {}
            for (mes, _cl), v in por_mes_local.items():
                if mes not in por_mes:
                    por_mes[mes] = [0.0, 0.0]
                por_mes[mes][0] += v['saldo']
                por_mes[mes][1] += v['valor']

            labels, saldos, valores = [], [], []
            for chave in sorted(por_mes):
                try:
                    ano, m = chave.split('-')
                    labels.append(f"{meses_pt.get(m, m)}/{ano[2:]}")
                except (ValueError, KeyError):
                    labels.append(chave)
                saldos.append(round(por_mes[chave][0], 2))
                valores.append(round(por_mes[chave][1], 2))

            logger.info(f"[Evolucao] Fallback WMS_Evolucao_Estoque: {len(labels)} mês(es).")
            return {
                'labels': labels, 'saldos': saldos, 'valores': valores,
                'modo': 'mensal', 'atualizado_em': 'via WMS_Evolucao_Estoque',
            }
        except Exception as e:
            logger.error(f"[Evolucao] Erro lendo WMS_Evolucao_Estoque: {e}")
            return None

    def construir_grafico_evolucao(
        self,
        cod_empresa=None,
        codlocais=None,
        data_inicio=None,
        data_fim=None,
    ):
        """
        Lê WMS_Estoque_Snap (Sheets) e constrói dados do gráfico de evolução.
        Quando WMS_Estoque_Snap estiver vazio, usa WMS_Evolucao_Estoque diretamente.
        Rápido — sem chamada ao Sankhya.
        """
        _vazio = {'labels': [], 'saldos': [], 'valores': [], 'modo': 'mensal',
                  'atualizado_em': None, 'snap_vazio': True}

        def _tentar_fallback_evolucao():
            """Fallback: constrói o gráfico direto de WMS_Evolucao_Estoque."""
            fb = self._construir_grafico_from_evolucao_sheets(
                codlocais=codlocais, data_inicio=data_inicio,
                data_fim=data_fim, cod_empresa=cod_empresa,
            )
            return fb if fb else _vazio

        try:
            try:
                ws = self.sheets.sheet.worksheet(self.ABA_SNAP_GRAFICO)
            except Exception:
                # aba ainda não existe — tenta WMS_Evolucao_Estoque antes de retornar vazio
                return _tentar_fallback_evolucao()

            rows = ws.get_all_values()
            if len(rows) < 2:
                return _tentar_fallback_evolucao()

            header = [h.lower() for h in rows[0]]
            idx = {col: header.index(col) for col in self.COLUNAS_SNAP if col in header}

            def _parse_date(d_str):
                if not d_str:
                    return None
                for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%Y'):
                    try: return datetime.strptime(d_str[:10], fmt).date()
                    except ValueError: pass
                return None

            d_ini = _parse_date(data_inicio)
            d_fim = _parse_date(data_fim)

            # codlocais aceitos (set de strings para comparação rápida)
            locais_filtro = {str(c).strip() for c in codlocais} if codlocais else None

            meses_pt = {
                '01': 'Jan', '02': 'Fev', '03': 'Mar', '04': 'Abr',
                '05': 'Mai', '06': 'Jun', '07': 'Jul', '08': 'Ago',
                '09': 'Set', '10': 'Out', '11': 'Nov', '12': 'Dez',
            }

            por_mes: Dict[str, list] = {}  # YYYY-MM → [saldo_total, valor_total]

            # Lê atualizado_em da primeira linha do snap (independente do filtro)
            atualizado_em = None
            if 'atualizado_em' in idx and len(rows) >= 2:
                atualizado_em = rows[1][idx['atualizado_em']] or None

            for row in rows[1:]:
                mr_raw = row[idx['mes_ref']]   if 'mes_ref'   in idx else ''
                cl     = row[idx['codlocal']]  if 'codlocal'  in idx else ''
                if locais_filtro and cl not in locais_filtro:
                    continue
                mr_date = _parse_date(mr_raw)
                if not mr_date:
                    continue
                if d_ini and mr_date < d_ini.replace(day=1):
                    continue
                if d_fim and mr_date > d_fim.replace(day=1):
                    continue
                try: saldo = float(str(row[idx['saldo']] if 'saldo' in idx else '0').replace(',', '.'))
                except ValueError: saldo = 0.0
                try: valor = float(str(row[idx['valor_total']] if 'valor_total' in idx else '0').replace(',', '.'))
                except ValueError: valor = 0.0
                chave = mr_raw[:7]  # YYYY-MM
                if chave not in por_mes:
                    por_mes[chave] = [0.0, 0.0]
                por_mes[chave][0] += saldo
                por_mes[chave][1] += valor

            if not por_mes:
                # WMS_Estoque_Snap existe mas sem dados para o filtro corrente.
                # Não sinaliza snap_vazio=True para não acionar atualização Sankhya desnecessária.
                return {
                    'labels': [], 'saldos': [], 'valores': [],
                    'modo': 'mensal', 'atualizado_em': atualizado_em,
                    'snap_vazio': False,
                }

            labels, saldos, valores = [], [], []
            for chave in sorted(por_mes):
                try:
                    ano, mes = chave.split('-')
                    labels.append(f"{meses_pt.get(mes, mes)}/{ano[2:]}")
                except (ValueError, KeyError):
                    labels.append(chave)
                saldos.append(round(por_mes[chave][0], 2))
                valores.append(round(por_mes[chave][1], 2))

            logger.info(f"[Evolucao] Sheets snap: {len(labels)} ponto(s) | atualizado {atualizado_em}")
            return {
                'labels': labels, 'saldos': saldos, 'valores': valores,
                'modo': 'mensal', 'atualizado_em': atualizado_em,
            }

        except Exception as e:
            logger.error(f"[Evolucao] Erro ao ler snapshot do Sheets: {e}")
            return None

    def resumo_por_local(
        self,
        data_inicio: str = None,
        data_fim: str = None,
        cod_empresa: str = None,
    ) -> List[Dict]:
        """Resumo agrupado por local com variação entre primeira e última data."""
        dados = self.consultar_evolucao(data_inicio, data_fim, cod_empresa)
        registros = dados.get('registros', [])
        if not registros:
            return []

        por_local: Dict[str, List] = {}
        for rec in registros:
            por_local.setdefault(rec.get('codlocal', '?'), []).append(rec)

        resultado = []
        for cl, recs in por_local.items():
            primeiro, ultimo = recs[0], recs[-1]
            qtd_ini, qtd_fim = primeiro['qtd_total'], ultimo['qtd_total']
            variacao = qtd_fim - qtd_ini
            pct = round((variacao / qtd_ini * 100), 2) if qtd_ini else 0.0
            resultado.append({
                'codlocal':     cl,
                'descrlocal':   primeiro.get('descrlocal', ''),
                'data_inicio':  primeiro.get('data_posicao', ''),
                'qtd_inicio':   qtd_ini,
                'valor_inicio': primeiro['valor_total'],
                'data_fim':     ultimo.get('data_posicao', ''),
                'qtd_fim':      qtd_fim,
                'valor_fim':    ultimo['valor_total'],
                'variacao_qtd': round(variacao, 4),
                'variacao_pct': pct,
            })

        resultado.sort(key=lambda x: x['codlocal'])
        return resultado

    # HELPERS

    def _ja_foi_capturado(self, ws, data_ref_str: str, empresa: str = None) -> bool:
        try:
            todos = ws.get_all_values()
            if len(todos) < 2:
                return False
            headers = todos[0]
            idx_d = headers.index('data_posicao') if 'data_posicao' in headers else 0
            idx_e = headers.index('cod_empresa')  if 'cod_empresa'  in headers else 1
            for row in todos[1:]:
                if len(row) <= idx_d or row[idx_d] != data_ref_str:
                    continue
                if empresa and len(row) > idx_e and row[idx_e] != str(empresa):
                    continue
                return True
            return False
        except Exception as e:
            logger.warning(f"Erro ao verificar duplicidade: {e}")
            return False

    def _get_mapa_locais(self) -> Dict[str, Dict]:
        if not self.cache_sankhya:
            return {}
        try:
            locais = self.cache_sankhya.obter_locais_financeiros()
            return {
                str(cod): {
                    'descrlocal': d.get('descrlocal', ''),
                    'prefixo':    str(d.get('prefixo', '')).zfill(2),
                }
                for cod, d in locais.items()
            }
        except Exception as e:
            logger.warning(f"Erro ao obter mapa de locais: {e}")
            return {}

    # JOB DIÁRIO — fechamento às 20:00 horário de Brasília (UTC-3)

    @staticmethod
    def _agora_brasilia():
        """Retorna datetime atual no fuso de Brasília (UTC-3 fixo — DST abolido em 2019)."""
        from datetime import timezone, timedelta as _td
        return datetime.now(tz=timezone.utc).astimezone(timezone(timedelta(hours=-3)))

    def _iniciar_job_diario(self):
        import os
        if os.environ.get('DISABLE_BACKGROUND_JOBS', 'False').lower() == 'true':
            logger.info("⏸Job diário desativado (modo CRON ativo).")
            return

        def _worker():
            logger.info("⏳ Job diário de estoque iniciado (executa às 20:00 BRT).")
            while True:
                agora_brt = self._agora_brasilia()
                # Executa exatamente às 20:00 BRT (fechamento do dia)
                if agora_brt.hour == 20 and agora_brt.minute == 0:
                    data_hoje_str = agora_brt.strftime('%d/%m/%Y')
                    logger.info(f"⏰ Fechamento diário — {data_hoje_str} (20:00 BRT)…")
                    self.capturar_e_salvar_data(data_hoje_str)
                    time.sleep(65)  # evita dupla execução dentro do mesmo minuto
                time.sleep(30)

        t = threading.Thread(target=_worker, daemon=True, name='wms-estoque-diario')
        t.start()
