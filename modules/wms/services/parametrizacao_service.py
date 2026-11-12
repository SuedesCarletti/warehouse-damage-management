import logging
import uuid
from datetime import datetime
from typing import List, Tuple
import traceback # IMPORT ADICIONADO

from ..models.regra_exclusao import RegraExclusao

logger = logging.getLogger(__name__)


class ParametrizacaoService:
    """CRUD de regras de exclusão para avarias pendentes"""

    SHEET_NAME = 'Parametrizacao_Pendentes'
    HEADERS = ['id', 'empresa', 'local', 'etiqueta', 'status_indenizacao', 'status_estoque',
           'ativo', 'criado_por', 'criado_em']

    def __init__(self, sheets_service):
        self.sheets = sheets_service
        self._ensure_sheet_exists()

    # Inicialização da aba

    def _ensure_sheet_exists(self):
        """Cria a aba com cabeçalho se ainda não existir"""
        if not self.sheets or not self.sheets.connected:
            logger.warning("ParametrizacaoService: Sheets não conectado")
            return

        try:
            ws = self.sheets.sheet.worksheet(self.SHEET_NAME)
            values = ws.get_all_values()
            
            # 1. Verificar Headers
            if not values or values[0] != self.HEADERS:
                logger.info(f"Cabeçalho incorreto ou vazio na aba '{self.SHEET_NAME}'. Atualizando...")
                ws.update('A1', [self.HEADERS])
                logger.info(f"Cabeçalho gravado na aba '{self.SHEET_NAME}'")

        except Exception as e:
            # 2. Se a aba não existe, criar
            error_message = str(e).lower()
            # FIX CRÍTICO: Incluir o nome da aba na verificação do erro
            if 'worksheet' in error_message or 'not found' in error_message or error_message == self.SHEET_NAME.lower():
                try:
                    logger.info("   Criando aba...")
                    ws = self.sheets.sheet.add_worksheet(
                        title=self.SHEET_NAME,
                        rows=100,
                        cols=len(self.HEADERS)
                    )
                    ws.update('A1', [self.HEADERS])
                    logger.info(f"Aba '{self.SHEET_NAME}' criada")
                except Exception as ce:
                    # Logar erro de criação
                    logger.error(f"Falha CRÍTICA ao criar aba '{self.SHEET_NAME}': {ce}")
                    logger.debug(traceback.format_exc())
            else:
                # Logar outro erro de acesso
                logger.error(f"Erro ao verificar aba '{self.SHEET_NAME}': {e}")
                logger.debug(traceback.format_exc())

    # Leitura

    # CORRETO — docstring indentada dentro do método
    def get_all_rules(self, include_inactive: bool = False, empresa: str = '') -> List[RegraExclusao]:
        """Retorna regras filtradas por empresa (string vazia = sem filtro / compatibilidade legada)"""
        if not self.sheets or not self.sheets.connected:
            return []
            
        # CORREÇÃO CRÍTICA: Garantir que a folha exista antes de tentar ler
        self._ensure_sheet_exists()

        try:
            ws = self.sheets.sheet.worksheet(self.SHEET_NAME)
            all_values = ws.get_all_values()

            if not all_values or len(all_values) < 2:
                return []

            headers = all_values[0]
            rules = []

            for row in all_values[1:]:
                # Ignorar linhas completamente vazias
                if not any(str(v).strip() for v in row):
                    continue

                record = {headers[i]: str(row[i]).strip() if i < len(row) else ''
                          for i in range(len(headers))}

                rule = RegraExclusao.from_dict(record)
                if not rule.id:
                    continue

                if include_inactive or rule.ativo == 'sim':
                    rules.append(rule)
                                
                if empresa:
                    rules = [r for r in rules if r.empresa == empresa or r.empresa == '']
            return rules

        except Exception as e:
            # CORREÇÃO DE LOG: Adicionar traceback para diagnóstico
            logger.error(f"Erro ao buscar regras: {e}")
            logger.debug(traceback.format_exc())
            return []

    # CORREÇÃO CRÍTICA: Adicionar método get_active_rules para resolver AttributeError
    def get_active_rules(self, empresa: str = '') -> List[RegraExclusao]:
        """Retorna apenas as regras ativas filtradas por empresa"""
        return self.get_all_rules(include_inactive=False, empresa=empresa)

    # Criação

    def add_rule(self, rule_data: dict, user_email: str) -> Tuple[bool, str]:
        """
        Cria nova regra.
        Valida: pelo menos 1 campo preenchido + sem duplicata.
        """
        if not self.sheets or not self.sheets.connected:
            return False, "Sheets não conectado"

        # CORREÇÃO CRÍTICA: Garantir que a folha exista antes de tentar escrever
        self._ensure_sheet_exists()
        
        # ── validar condições ──
        cond_fields = ['local', 'etiqueta', 'status_indenizacao', 'status_estoque']
        if not any(str(rule_data.get(f, '')).strip() for f in cond_fields):
            return False, "Pelo menos um campo deve ser preenchido"

        # ── detectar duplicata ──
        existing = self.get_all_rules(include_inactive=True) 
        for ex in existing:
            if (ex.local == str(rule_data.get('local', '')).strip() and
                    ex.etiqueta == str(rule_data.get('etiqueta', '')).strip() and
                    ex.status_indenizacao == str(rule_data.get('status_indenizacao', '')).strip() and
                    ex.status_estoque == str(rule_data.get('status_estoque', '')).strip()):
                return False, "Regra com essas condições já existe"

        try:
            rule = RegraExclusao(
                id=str(uuid.uuid4())[:8],
                empresa=str(rule_data.get('empresa', '')).strip(),    # ← NOVO
                local=str(rule_data.get('local', '')).strip(),
                etiqueta=str(rule_data.get('etiqueta', '')).strip(),
                status_indenizacao=str(rule_data.get('status_indenizacao', '')).strip(),
                status_estoque=str(rule_data.get('status_estoque', '')).strip(),
                ativo='sim',
                criado_por=user_email,
                criado_em=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            )
            
            ws.append_row([
                rule.id, rule.empresa, rule.local, rule.etiqueta, rule.status_indenizacao,  # ← empresa inserida
                rule.status_estoque, rule.ativo, rule.criado_por, rule.criado_em
            ])

            logger.info(f"Regra {rule.id} criada por {user_email}")
            return True, f"Regra criada com sucesso (ID: {rule.id})"

        except Exception as e:
            # CORREÇÃO DE LOG: Adicionar traceback para diagnóstico
            logger.error(f"Erro ao criar regra: {e}")
            logger.debug(traceback.format_exc())
            return False, str(e)

    # Atualização

    def update_rule(self, rule_id: str, rule_data: dict) -> Tuple[bool, str]:
        """Atualiza campos de condição (B–E). Não toca ativo / criado_por / criado_em."""
        if not self.sheets or not self.sheets.connected:
            return False, "Sheets não conectado"

        # Garantir existência
        self._ensure_sheet_exists()

        try:
            ws = self.sheets.sheet.worksheet(self.SHEET_NAME)
            all_values = ws.get_all_values()

            for row_idx, row in enumerate(all_values[1:], start=2):
                if row and str(row[0]).strip() == rule_id:
                    ws.update(f'B{row_idx}', [[str(rule_data.get('empresa', '')).strip()]])  # col B = empresa
                    ws.update(f'C{row_idx}:F{row_idx}', [[                                   # col C-F = condições
                        str(rule_data.get('local', '')).strip(),
                        str(rule_data.get('etiqueta', '')).strip(),
                        str(rule_data.get('status_indenizacao', '')).strip(),
                        str(rule_data.get('status_estoque', '')).strip(),
                    ]])
                    logger.info(f"Regra {rule_id} atualizada")
                    return True, "Regra atualizada com sucesso"

            return False, "Regra não encontrada"

        except Exception as e:
            import traceback
            logger.error(f"Erro ao atualizar regra {rule_id}: {e}")
            logger.debug(traceback.format_exc())
            return False, str(e)

    # Exclusão

    def delete_rule(self, rule_id: str) -> Tuple[bool, str]:
        """Remove uma regra limpando a linha inteira"""
        if not self.sheets or not self.sheets.connected:
            return False, "Sheets não conectado"

        # Garantir existência
        self._ensure_sheet_exists()

        try:
            ws = self.sheets.sheet.worksheet(self.SHEET_NAME)
            all_values = ws.get_all_values()

            for row_idx, row in enumerate(all_values[1:], start=2):
                if row and str(row[0]).strip() == rule_id:
                    # Limpar a linha, mas apenas as colunas que têm cabeçalho
                    end_col = chr(ord('A') + len(self.HEADERS) - 1)
                    ws.update(f'A{row_idx}:{end_col}{row_idx}', [[''] * len(self.HEADERS)])
                    logger.info(f"Regra {rule_id} removida")
                    return True, "Regra removida com sucesso"

            return False, "Regra não encontrada"

        except Exception as e:
            import traceback
            logger.error(f"Erro ao remover regra {rule_id}: {e}")
            logger.debug(traceback.format_exc())
            return False, str(e)

    # Toggle ativo / inativo

    def set_active(self, rule_id: str, ativo: bool) -> Tuple[bool, str]:
        """Alterna campo 'ativo' (coluna F) entre 'sim' e 'nao'"""
        if not self.sheets or not self.sheets.connected:
            return False, "Sheets não conectado"

        # Garantir existência
        self._ensure_sheet_exists()

        try:
            ws = self.sheets.sheet.worksheet(self.SHEET_NAME)
            all_values = ws.get_all_values()

            for row_idx, row in enumerate(all_values[1:], start=2):
                if row and str(row[0]).strip() == rule_id:
                    valor = 'sim' if ativo else 'nao'
                    # Coluna 'ativo' é a 6ª (F)
                    ws.update(f'F{row_idx}', [[valor]]) 
                    estado = 'ativada' if ativo else 'desativada'
                    logger.info(f"Regra {rule_id} {estado}")
                    return True, f"Regra {estado} com sucesso"

            return False, "Regra não encontrada"

        except Exception as e:
            import traceback
            logger.error(f"Erro ao alterar status da regra {rule_id}: {e}")
            logger.debug(traceback.format_exc())
            return False, str(e)

    # Opções para dropdowns

    def get_unique_locals(self) -> list:
        """
        Valores únicos de 'localizacao' existentes em Itens_Avarias.
        ⚡ Usa get_wms_itens_cached() em vez de get_all_records() direto.
        """
        if not self.sheets or not self.sheets.connected:
            return []
        try:
            # CACHE em vez de get_all_records('Itens_Avarias') direto
            records = self.sheets.get_wms_itens_cached()
            locs = {str(r.get('localizacao', '')).strip() for r in records}
            locs.discard('')
            return sorted(locs)
        except Exception as e:
            import traceback
            logger.error(f"Erro ao buscar localizações: {e}")
            logger.debug(traceback.format_exc())
            return []
    
    
    def get_unique_transportadoras(self) -> list:
        """
        Valores únicos de 'transportadora' existentes em Itens_Avarias.
        ⚡ Usa get_wms_itens_cached() em vez de get_all_records() direto.
        """
        if not self.sheets or not self.sheets.connected:
            return []
        try:
            # CACHE em vez de get_all_records('Itens_Avarias') direto
            records = self.sheets.get_wms_itens_cached()
            transportadoras = {str(r.get('transportadora', '')).strip() for r in records}
            transportadoras.discard('')
            return sorted(transportadoras)
        except Exception as e:
            import traceback
            logger.error(f"Erro ao buscar transportadoras: {e}")
            logger.debug(traceback.format_exc())
            return []
