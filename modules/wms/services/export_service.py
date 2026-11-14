"""
modules/wms/services/export_service.py (VERSÃO CORRIGIDA)

Correções aplicadas:
1. Método _adicionar_info_filtros melhorado para mostrar "Campo: Valor"
2. Totalizadores adicionados em Consultar Avarias
3. Melhor tratamento de dados em Registrar Avarias
"""

import logging
from datetime import datetime
from typing import List, Dict, Any
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
from openpyxl.utils import get_column_letter

logger = logging.getLogger(__name__)


class ExportService:
    """Serviço para exportação de relatórios de avarias"""
    
    # Cores do tema (baseadas no Bootstrap Success)
    COLOR_HEADER = "198754"  # Verde success
    COLOR_HEADER_TEXT = "FFFFFF"  # Branco
    COLOR_SUBHEADER = "E7F4EE"  # Verde claro
    COLOR_TOTAL = "FFC107"  # Amarelo warning
    COLOR_ZEBRA_1 = "FFFFFF"  # Branco
    COLOR_ZEBRA_2 = "F8F9FA"  # Cinza claro
    
    def __init__(self):
        """Inicializa o serviço de exportação"""
        pass
    
    def exportar_consultar_avarias(
        self,
        dados: List[Dict[str, Any]],
        colunas_visiveis: List[Dict[str, str]],
        filtros_ativos: Dict[str, str] = None,
        totalizadores: Dict[str, float] = None
    ) -> BytesIO:
        """
        Exporta dados de Consultar Avarias para Excel
        
        Args:
            dados: Lista de dicionários com os dados das linhas
            colunas_visiveis: Lista de dicionários com {name, key} das colunas
            filtros_ativos: Dicionário com filtros aplicados (opcional)
            totalizadores: Dicionário com valores totalizados (opcional)
            
        Returns:
            BytesIO com o arquivo Excel gerado
        """
        try:
            wb = Workbook()
            ws = wb.active
            ws.title = "Consultar Avarias"
            
            # 1. Escrever cabeçalhos
            headers = [col['name'] for col in colunas_visiveis]
            for col_idx, header in enumerate(headers, 1):
                cell = ws.cell(row=1, column=col_idx, value=header)
                cell.font = Font(bold=True, color=self.COLOR_HEADER_TEXT, size=11)
                cell.fill = PatternFill(start_color=self.COLOR_HEADER, end_color=self.COLOR_HEADER, fill_type="solid")
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                cell.border = self._get_border()
            
            # 2. Escrever dados
            for row_idx, row_data in enumerate(dados, 2):
                for col_idx, col_info in enumerate(colunas_visiveis, 1):
                    key = col_info['key']
                    value = self._format_value(key, row_data.get(key, ''))

                    cell = ws.cell(row=row_idx, column=col_idx, value=value)
                    cell.alignment = Alignment(horizontal="left", vertical="center")
                    cell.border = self._get_border()

                    # Aplicar zebra striping
                    if row_idx % 2 == 0:
                        cell.fill = PatternFill(start_color=self.COLOR_ZEBRA_2, end_color=self.COLOR_ZEBRA_2, fill_type="solid")

            current_row = len(dados) + 2
            
            # 3. Adicionar totalizadores (se houver)
            if totalizadores:
                self._adicionar_linha_totalizadores(ws, current_row, colunas_visiveis, totalizadores)
                current_row += 1
            
            # 4. Ajustar larguras das colunas
            self._auto_ajustar_larguras(ws, headers)
            
            # 5. Adicionar filtros automáticos
            ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"
            
            # 6. Congelar primeira linha
            ws.freeze_panes = "A2"
            
            # 7. Adicionar informações de filtros (se houver)
            if filtros_ativos:
                self._adicionar_info_filtros(ws, filtros_ativos, current_row + 1)
            
            # Salvar em BytesIO
            output = BytesIO()
            wb.save(output)
            output.seek(0)
            
            logger.info(f"Excel de Consultar Avarias gerado: {len(dados)} registros, {len(headers)} colunas")
            return output
            
        except Exception as e:
            logger.error(f"Erro ao exportar Consultar Avarias: {e}")
            raise
    
    def exportar_registrar_avarias(
        self,
        dados: List[Dict[str, Any]],
        colunas_visiveis: List[Dict[str, str]],
        totalizadores: Dict[str, float] = None,
        filtros_ativos: Dict[str, str] = None
    ) -> BytesIO:
        """
        Exporta dados de Registrar Avarias para Excel (formato agrupado)
        
        Args:
            dados: Lista de dicionários com estrutura agrupada
                   [{'avaria': {...}, 'itens': [{...}, {...}]}, ...]
            colunas_visiveis: Lista de dicionários com {name, key, type} das colunas
                             type pode ser: 'header', 'item', 'both'
            totalizadores: Dicionário com valores totalizados (opcional)
            filtros_ativos: Dicionário com filtros aplicados (opcional)
            
        Returns:
            BytesIO com o arquivo Excel gerado
        """
        try:
            wb = Workbook()
            ws = wb.active
            ws.title = "Registrar Avarias"
            
            # 1. Escrever cabeçalhos
            headers = [col['name'] for col in colunas_visiveis]
            for col_idx, header in enumerate(headers, 1):
                cell = ws.cell(row=1, column=col_idx, value=header)
                cell.font = Font(bold=True, color=self.COLOR_HEADER_TEXT, size=11)
                cell.fill = PatternFill(start_color=self.COLOR_HEADER, end_color=self.COLOR_HEADER, fill_type="solid")
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                cell.border = self._get_border()
            
            current_row = 2
            total_itens = 0
            
            # 2. Escrever dados agrupados
            for grupo in dados:
                avaria = grupo['avaria']
                itens = grupo['itens']
                num_itens = len(itens)
                
                if num_itens == 0:
                    continue
                
                # ETAPA 1: Processar colunas do tipo 'header' e 'both'
                for col_idx, col_info in enumerate(colunas_visiveis, 1):
                    col_type = col_info.get('type', 'both')
                    key = col_info['key']
                    
                    if col_type in ['header', 'both']:
                        value = self._format_value(key, avaria.get(key, ''))

                        # Escrever na primeira célula (antes do merge!)
                        cell = ws.cell(row=current_row, column=col_idx, value=value)
                        cell.alignment = Alignment(horizontal="center", vertical="center")
                        cell.border = self._get_border()
                        cell.fill = PatternFill(start_color=self.COLOR_SUBHEADER, end_color=self.COLOR_SUBHEADER, fill_type="solid")
                        cell.font = Font(bold=True, size=10)
                        
                        # Fazer merge se houver múltiplos itens
                        if num_itens > 1:
                            ws.merge_cells(
                                start_row=current_row,
                                start_column=col_idx,
                                end_row=current_row + num_itens - 1,
                                end_column=col_idx
                            )
                
                # ETAPA 2: Processar itens (colunas 'item' e 'both')
                for item_idx, item in enumerate(itens):
                    row = current_row + item_idx
                    
                    for col_idx, col_info in enumerate(colunas_visiveis, 1):
                        col_type = col_info.get('type', 'both')
                        key = col_info['key']
                        
                        # CORREÇÃO: Para colunas 'item', sempre escrever dados do item
                        if col_type == 'item':
                            value = self._format_value(key, item.get(key, ''))
                            cell = ws.cell(row=row, column=col_idx, value=value)
                            cell.alignment = Alignment(horizontal="left", vertical="center")
                            cell.border = self._get_border()
                            
                            # Zebra striping nos itens
                            if item_idx % 2 == 1:
                                cell.fill = PatternFill(start_color=self.COLOR_ZEBRA_2, end_color=self.COLOR_ZEBRA_2, fill_type="solid")
                        
                        # Para colunas 'both', já foi escrito no header com merge
                        # Não fazer nada aqui para evitar sobrescrever
                
                current_row += num_itens
                total_itens += num_itens
            
            # 3. Adicionar totalizadores (se houver)
            if totalizadores:
                self._adicionar_linha_totalizadores(ws, current_row, colunas_visiveis, totalizadores)
                current_row += 1
            
            # 4. Ajustar larguras das colunas
            self._auto_ajustar_larguras(ws, headers)
            
            # 5. Adicionar filtros automáticos
            ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"
            
            # 6. Congelar primeira linha
            ws.freeze_panes = "A2"
            
            # 7. Adicionar informações de filtros (se houver)
            if filtros_ativos:
                self._adicionar_info_filtros(ws, filtros_ativos, current_row + 1)
            
            # Salvar em BytesIO
            output = BytesIO()
            wb.save(output)
            output.seek(0)
            
            logger.info(f"Excel de Registrar Avarias gerado: {len(dados)} grupos, {total_itens} itens, {len(headers)} colunas")
            return output
            
        except Exception as e:
            logger.error(f"Erro ao exportar Registrar Avarias: {e}")
            raise
    
    # Colunas fixas para exportação de Consultar Avarias com itens
    EXPORT_COLUMNS_COM_ITENS = [
        {'name': 'Cód. Empresa',       'key': 'cod_empresa'},
        {'name': 'Status Indenização', 'key': 'status_indenizacao'},
        {'name': 'Nota',               'key': 'nota'},
        {'name': 'NFD',                'key': 'nfd'},
        {'name': 'Quantidade',         'key': 'quantidade'},
        {'name': 'Valor NDS',          'key': 'valor_item'},
        {'name': 'Cód. Produto',       'key': 'codprod'},
        {'name': 'Produto',            'key': 'descrprod'},
        {'name': 'Qtd Avariada',       'key': 'qtd_avariada'},
        {'name': 'Localização',        'key': 'localizacao'},
        {'name': 'Transportadora',     'key': 'transportadora'},
        {'name': '% Indenização',      'key': 'reembolso_pct'},
        {'name': 'Valor Indenização',  'key': 'valor_indenizacao'},
        {'name': 'Prazo Pgto',         'key': 'prazo_pagamento'},
        {'name': 'Data Pgto',          'key': 'data_pagamento'},
        {'name': 'Data Registro',      'key': 'data_registro'},
        {'name': 'Data Triagem',       'key': 'data_triagem'},
        {'name': 'Data Finalização',   'key': 'data_finalizacao'},
    ]

    def exportar_consultar_avarias_com_itens(
        self,
        avarias: List[Dict[str, Any]],
        itens_por_avaria: Dict[str, List[Dict[str, Any]]],
        filtros_ativos: Dict[str, str] = None
    ) -> BytesIO:
        """
        Exporta Consultar Avarias com itens em formato flat (uma linha por item).

        Args:
            avarias: Lista de dicts com dados das avarias visíveis
            itens_por_avaria: Dict {avaria_id: [item_dict, ...]}
            filtros_ativos: Filtros aplicados (opcional)

        Returns:
            BytesIO com o arquivo Excel gerado
        """
        try:
            wb = Workbook()
            ws = wb.active
            ws.title = "Consultar Avarias"

            colunas = self.EXPORT_COLUMNS_COM_ITENS
            headers = [col['name'] for col in colunas]

            # 1. Cabeçalhos
            for col_idx, header in enumerate(headers, 1):
                cell = ws.cell(row=1, column=col_idx, value=header)
                cell.font = Font(bold=True, color=self.COLOR_HEADER_TEXT, size=11)
                cell.fill = PatternFill(start_color=self.COLOR_HEADER, end_color=self.COLOR_HEADER, fill_type="solid")
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                cell.border = self._get_border()

            # 2. Dados: uma linha por item de cada avaria
            current_row = 2
            for avaria in avarias:
                avaria_id = (avaria.get('avaria_id', '') or avaria.get('id', '')).strip()
                itens = itens_por_avaria.get(avaria_id, [])

                if not itens:
                    # Avaria sem itens: gera uma linha só com dados da avaria
                    row_data = {**avaria}
                    for col_idx, col_info in enumerate(colunas, 1):
                        key = col_info['key']
                        value = self._format_value(key, row_data.get(key, ''))
                        cell = ws.cell(row=current_row, column=col_idx, value=value)
                        cell.alignment = Alignment(horizontal="left", vertical="center")
                        cell.border = self._get_border()
                        if current_row % 2 == 0:
                            cell.fill = PatternFill(start_color=self.COLOR_ZEBRA_2, end_color=self.COLOR_ZEBRA_2, fill_type="solid")
                    current_row += 1
                else:
                    for item in itens:
                        row_data = {**avaria, **item}
                        for col_idx, col_info in enumerate(colunas, 1):
                            key = col_info['key']
                            value = self._format_value(key, row_data.get(key, ''))
                            cell = ws.cell(row=current_row, column=col_idx, value=value)
                            cell.alignment = Alignment(horizontal="left", vertical="center")
                            cell.border = self._get_border()
                            if current_row % 2 == 0:
                                cell.fill = PatternFill(start_color=self.COLOR_ZEBRA_2, end_color=self.COLOR_ZEBRA_2, fill_type="solid")
                        current_row += 1

            # 3. Larguras e filtros automáticos
            self._auto_ajustar_larguras(ws, headers)
            ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"
            ws.freeze_panes = "A2"

            # 4. Info de filtros
            if filtros_ativos:
                self._adicionar_info_filtros(ws, filtros_ativos, current_row + 1)

            output = BytesIO()
            wb.save(output)
            output.seek(0)

            total_linhas = current_row - 2
            logger.info(f"Excel Consultar Avarias (com itens) gerado: {total_linhas} linhas, {len(avarias)} avarias")
            return output

        except Exception as e:
            logger.error(f"Erro ao exportar Consultar Avarias com itens: {e}")
            raise

    # MÉTODOS AUXILIARES
    
    DATE_KEYS = {
        'data_registro', 'data_triagem', 'data_finalizacao', 'data_pagamento',
        'prazo_pagamento', 'data_coleta', 'data_entrega', 'data_protocolo_mkt',
        'data_identificacao', 'criado_em', 'data_cadastro', 'ultimo_login',
        'data_consulta', 'expira_em',
    }

    def _format_date_br(self, value) -> str:
        """Converte YYYY-MM-DD ou YYYY-MM-DD HH:MM:SS para DD/MM/YYYY."""
        if not value:
            return ''
        s = str(value).strip()
        try:
            if len(s) >= 10 and s[4] == '-' and s[7] == '-':
                return f"{s[8:10]}/{s[5:7]}/{s[:4]}"
        except Exception:
            pass
        return s

    def _format_value(self, key: str, value) -> str:
        """Aplica formatação de data se o campo for uma data."""
        if key in self.DATE_KEYS:
            return self._format_date_br(value)
        return value

    def _get_border(self):
        """Retorna estilo de borda padrão"""
        return Border(
            left=Side(style='thin', color='CCCCCC'),
            right=Side(style='thin', color='CCCCCC'),
            top=Side(style='thin', color='CCCCCC'),
            bottom=Side(style='thin', color='CCCCCC')
        )
    
    def _auto_ajustar_larguras(self, ws, headers):
        """Ajusta largura das colunas automaticamente"""
        for col_idx, header in enumerate(headers, 1):
            # Largura baseada no tamanho do cabeçalho + margem
            largura = max(len(str(header)) + 2, 12)
            ws.column_dimensions[get_column_letter(col_idx)].width = largura
    
    def _adicionar_linha_totalizadores(self, ws, row: int, colunas_visiveis: List[Dict], totalizadores: Dict[str, float]):
        """Adiciona linha de totalizadores"""
        for col_idx, col_info in enumerate(colunas_visiveis, 1):
            key = col_info['key']
            
            if col_idx == 1:
                # Primeira coluna: Label "TOTAIS"
                cell = ws.cell(row=row, column=col_idx, value="TOTAIS")
                cell.font = Font(bold=True, size=10)
                cell.alignment = Alignment(horizontal="center", vertical="center")
            elif key in totalizadores:
                # Colunas com totalizadores
                cell = ws.cell(row=row, column=col_idx, value=totalizadores[key])
                cell.font = Font(bold=True, size=10)
                cell.alignment = Alignment(horizontal="left", vertical="center")
            else:
                # Colunas vazias
                cell = ws.cell(row=row, column=col_idx, value="")
            
            # Aplicar formatação
            cell.fill = PatternFill(start_color=self.COLOR_TOTAL, end_color=self.COLOR_TOTAL, fill_type="solid")
            cell.border = self._get_border()
    
    def _adicionar_info_filtros(self, ws, filtros_ativos: Dict[str, str], start_row: int):
        """
        MELHORADO: Adiciona informações sobre filtros aplicados
        Agora mostra "Campo: Valor" em vez de "Filtro: valor"
        """
        if not filtros_ativos:
            return
        
        # Título
        ws.cell(row=start_row, column=1, value="FILTROS APLICADOS:").font = Font(bold=True, size=10)
        start_row += 1
        
        # Listar filtros com nome do campo
        for campo, valor in filtros_ativos.items():
            # Formatação: "• Campo: Valor"
            texto_filtro = f"• {campo}: {valor}"
            ws.cell(row=start_row, column=1, value=texto_filtro).font = Font(size=9)
            start_row += 1


# INSTÂNCIA GLOBAL
export_service = ExportService()
