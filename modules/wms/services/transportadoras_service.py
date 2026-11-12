import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

class TransportadorasService:
    SHEET_NAME = 'Transportadoras_Normalizadas'
    # Cabeçalhos do formato novo (com coluna empresa)
    HEADERS_V2 = ['empresa', 'nome_original', 'nome_normalizado', 'atualizado_em']
    # Cabeçalhos do formato antigo (sem coluna empresa)
    HEADERS_V1 = ['nome_original', 'nome_normalizado', 'atualizado_em']

    def __init__(self, sheets_service, wms_service, sankhya_service):
        self.sheets_service = sheets_service
        self.wms_service = wms_service
        self.sankhya_service = sankhya_service
        self._ensure_sheet()

    def _ensure_sheet(self):
        """Garante que a aba existe. Não faz migração de estrutura para evitar chamadas extras de API."""
        try:
            self.sheets_service.sheet.worksheet(self.SHEET_NAME)
        except Exception:
            try:
                ws = self.sheets_service.sheet.add_worksheet(
                    title=self.SHEET_NAME, rows=1000, cols=4
                )
                ws.append_row(self.HEADERS_V2)
                logger.info(f"Aba {self.SHEET_NAME} criada com formato v2 (com empresa)")
            except Exception as e:
                logger.error(f"Erro ao criar aba {self.SHEET_NAME}: {e}")

    def _worksheet(self):
        return self.sheets_service.sheet.worksheet(self.SHEET_NAME)

    def _get_all_data(self):
        """
        Retorna (headers, rows) via uma única chamada get_all_values().
        Detecta automaticamente se a aba é v1 (sem empresa) ou v2 (com empresa).
        """
        ws = self._worksheet()
        values = ws.get_all_values()
        if not values:
            return [], [], False
        headers = [h.strip() for h in values[0]]
        rows = values[1:]
        has_empresa = 'empresa' in headers
        return ws, headers, rows, has_empresa

    def get_normalizacoes(self, empresa: str = '') -> Dict[str, str]:
        """
        Retorna mapeamento {nome_original: nome_normalizado} filtrado pela empresa.
        Compatível com aba v1 (sem empresa) e v2 (com empresa).
        """
        try:
            ws, headers, rows, has_empresa = self._get_all_data()
            if not headers:
                return {}

            if has_empresa:
                idx_emp = headers.index('empresa')
                idx_orig = headers.index('nome_original')
                idx_norm = headers.index('nome_normalizado')
                return {
                    str(row[idx_orig]).strip(): str(row[idx_norm]).strip()
                    for row in rows
                    if len(row) > idx_norm
                    and str(row[idx_orig]).strip()
                    and str(row[idx_emp]).strip() == empresa
                }
            else:
                # Formato antigo: sem isolamento de empresa — retorna tudo
                idx_orig = headers.index('nome_original') if 'nome_original' in headers else 0
                idx_norm = headers.index('nome_normalizado') if 'nome_normalizado' in headers else 1
                return {
                    str(row[idx_orig]).strip(): str(row[idx_norm]).strip()
                    for row in rows
                    if len(row) > idx_norm and str(row[idx_orig]).strip()
                }
        except Exception as e:
            logger.error(f"Erro ao buscar normalizações: {e}")
            return {}

    def salvar_normalizacao(self, nome_original: str, nome_normalizado: str, empresa: str = '') -> bool:
        """
        Salva ou atualiza uma normalização e aplica imediatamente nos registros de Avarias.
        Detecta o formato da aba e adapta os índices de coluna automaticamente.
        """
        try:
            ws, headers, rows, has_empresa = self._get_all_data()
            now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

            if has_empresa:
                idx_emp = headers.index('empresa')
                idx_orig = headers.index('nome_original')
                idx_norm = headers.index('nome_normalizado')
                idx_ts = headers.index('atualizado_em') if 'atualizado_em' in headers else idx_norm + 1

                for i, row in enumerate(rows):
                    if (len(row) > idx_orig
                            and str(row[idx_orig]).strip() == nome_original
                            and str(row[idx_emp]).strip() == empresa):
                        sheet_row = i + 2  # +1 cabeçalho, +1 base-1
                        ws.update_cell(sheet_row, idx_norm + 1, nome_normalizado or "")
                        ws.update_cell(sheet_row, idx_ts + 1, now)
                        # Aplica imediatamente nos registros de Avarias
                        if nome_normalizado:
                            self._aplicar_normalizacao_em_avarias(nome_original, nome_normalizado, empresa)
                        return True

                if nome_normalizado:
                    ws.append_row([empresa, nome_original, nome_normalizado, now])
                    self._aplicar_normalizacao_em_avarias(nome_original, nome_normalizado, empresa)
            else:
                # Formato antigo sem coluna empresa
                idx_orig = headers.index('nome_original') if 'nome_original' in headers else 0
                idx_norm = headers.index('nome_normalizado') if 'nome_normalizado' in headers else 1
                idx_ts = headers.index('atualizado_em') if 'atualizado_em' in headers else idx_norm + 1

                for i, row in enumerate(rows):
                    if len(row) > idx_orig and str(row[idx_orig]).strip() == nome_original:
                        sheet_row = i + 2
                        ws.update_cell(sheet_row, idx_norm + 1, nome_normalizado or "")
                        ws.update_cell(sheet_row, idx_ts + 1, now)
                        if nome_normalizado:
                            self._aplicar_normalizacao_em_avarias(nome_original, nome_normalizado, empresa)
                        return True

                if nome_normalizado:
                    ws.append_row([nome_original, nome_normalizado, now])
                    self._aplicar_normalizacao_em_avarias(nome_original, nome_normalizado, empresa)

            return True
        except Exception as e:
            logger.error(f"Erro ao salvar normalização: {e}")
            return False

    def _aplicar_normalizacao_em_avarias(self, nome_original: str, nome_normalizado: str, empresa: str = '') -> int:
        """
        Aplica uma normalização específica nos registros de Avarias que ainda têm o nome original.
        Mais eficiente que aplicar_normalizacao_em_massa pois trata apenas um nome por vez.
        Retorna a quantidade de registros atualizados.
        """
        try:
            avarias = self.wms_service.get_all_avarias(empresa=empresa)
            ws_avaria = self.sheets_service.sheet.worksheet('Avarias')
            headers = ws_avaria.row_values(1)
            if 'transportadora' not in headers:
                return 0

            col_idx = headers.index('transportadora') + 1
            ids_col = ws_avaria.col_values(1)
            id_to_row = {id_val: i + 1 for i, id_val in enumerate(ids_col)}

            updates = []
            for av in avarias:
                if str(av.transportadora).strip() == nome_original:
                    row_idx = id_to_row.get(av.id)
                    if row_idx:
                        updates.append({
                            'range': f"{self._col_num_to_letter(col_idx)}{row_idx}",
                            'values': [[nome_normalizado]]
                        })

            if updates:
                ws_avaria.batch_update(updates)
                # Invalida cache para que outras telas vejam os dados atualizados
                try:
                    self.sheets_service.invalidate_wms_cache()
                except Exception:
                    pass
                logger.info(f"Normalização aplicada em {len(updates)} avaria(s): '{nome_original}' → '{nome_normalizado}'")

            return len(updates)
        except Exception as e:
            logger.error(f"Erro ao aplicar normalização em Avarias: {e}")
            return 0

    def get_unique_transportadoras(self, empresa_contexto: str = '') -> List[Dict]:
        """Retorna lista de transportadoras únicas dos registros (Avarias) filtradas por empresa."""
        avarias = self.wms_service.get_all_avarias(empresa=empresa_contexto)
        unicas = set()
        for av in avarias:
            nome = str(av.transportadora).strip()
            if nome:
                unicas.add(nome)

        normalizacoes = self.get_normalizacoes(empresa=empresa_contexto)
        return [
            {'nome_original': nome, 'nome_normalizado': normalizacoes.get(nome, '')}
            for nome in sorted(unicas)
        ]

    def aplicar_normalizacao_em_massa(self, empresa_contexto: str = '') -> Tuple[int, int]:
        """Aplica as normalizações existentes em todos os registros de avaria da empresa."""
        normalizacoes = self.get_normalizacoes(empresa=empresa_contexto)
        avarias = self.wms_service.get_all_avarias(empresa=empresa_contexto)

        sucessos, erros = 0, 0
        ws_avaria = self.sheets_service.sheet.worksheet('Avarias')
        headers = ws_avaria.row_values(1)
        if 'transportadora' not in headers:
            return 0, 0

        col_idx = headers.index('transportadora') + 1
        ids_col = ws_avaria.col_values(1)
        id_to_row = {id_val: i + 1 for i, id_val in enumerate(ids_col)}

        updates = []
        for av in avarias:
            nome_atual = str(av.transportadora).strip()
            if nome_atual in normalizacoes and normalizacoes[nome_atual]:
                novo_nome = normalizacoes[nome_atual]
                if nome_atual != novo_nome:
                    row_idx = id_to_row.get(av.id)
                    if row_idx:
                        updates.append({
                            'range': f"{self._col_num_to_letter(col_idx)}{row_idx}",
                            'values': [[novo_nome]]
                        })

        if updates:
            try:
                ws_avaria.batch_update(updates)
                sucessos = len(updates)
            except Exception as e:
                logger.error(f"Erro no batch_update de Avarias: {e}")
                erros = len(updates)

        return sucessos, erros

    def _col_num_to_letter(self, n):
        string = ""
        while n > 0:
            n, remainder = divmod(n - 1, 26)
            string = chr(65 + remainder) + string
        return string

    def sincronizar_todas_notas_sankhya(self, empresa_contexto: str = '') -> Dict:
        """
        Consulta TODAS as notas dos registros (Avarias) no Sankhya.
        Prioriza NFD e só utiliza a NOTA quando não houver NFD.
        Se <SEM PARCEIRO> ou 0, mantém o nome da planilha.
        Filtra por empresa_contexto.
        """
        avarias = self.wms_service.get_all_avarias(empresa=empresa_contexto)
        normalizacoes = self.get_normalizacoes(empresa=empresa_contexto)

        ws_avaria = self.sheets_service.sheet.worksheet('Avarias')
        headers = ws_avaria.row_values(1)
        if 'transportadora' not in headers:
            return {"status": "error", "message": "Coluna transportadora não encontrada em Avarias"}

        col_idx = headers.index('transportadora') + 1
        ids_col = ws_avaria.col_values(1)
        id_to_row = {id_val: i + 1 for i, id_val in enumerate(ids_col)}

        updates = []
        processados = atualizados = mantidos = erros = 0

        for av in avarias:
            nota_escolhida = None
            if str(av.nfd).strip():
                nota_escolhida = str(av.nfd).strip()
            elif str(av.nota).strip():
                nota_escolhida = str(av.nota).strip()

            if not nota_escolhida:
                processados += 1
                continue

            cod_empresa = str(av.cod_empresa).strip()

            try:
                nota_sankhya = self.sankhya_service.buscar_nota_fiscal(nota_escolhida, codemp=cod_empresa)
                if not nota_sankhya:
                    nota_sankhya = self.sankhya_service.buscar_nota_fiscal(nota_escolhida)

                nome_sankhya = None
                if nota_sankhya:
                    cod_parc = nota_sankhya.get('CODPARCTRANSP')
                    if cod_parc and str(cod_parc).strip() != '0':
                        parceiro = self.sankhya_service.buscar_transportadora(cod_parc)
                        if parceiro:
                            nome_sankhya = parceiro.get('NOMEPARC')

                nome_atual = str(av.transportadora).strip()
                novo_nome = nome_atual

                if nome_sankhya and nome_sankhya.strip().upper() != '<SEM PARCEIRO>':
                    novo_nome = nome_sankhya.strip()

                if novo_nome in normalizacoes and normalizacoes[novo_nome]:
                    novo_nome = normalizacoes[novo_nome]

                if novo_nome != nome_atual:
                    row_idx = id_to_row.get(av.id)
                    if row_idx:
                        updates.append({
                            'range': f"{self._col_num_to_letter(col_idx)}{row_idx}",
                            'values': [[novo_nome]]
                        })
                        atualizados += 1
                else:
                    mantidos += 1

            except Exception as e:
                logger.error(f"Erro ao sincronizar Avaria {av.id}: {e}")
                erros += 1

            processados += 1

        if updates:
            try:
                ws_avaria.batch_update(updates)
            except Exception as e:
                logger.error(f"Erro no batch_update durante sync com Sankhya: {e}")
                erros += len(updates)
                atualizados = 0

        return {
            "status": "success",
            "processados": processados,
            "atualizados": atualizados,
            "mantidos": mantidos,
            "erros": erros
        }
