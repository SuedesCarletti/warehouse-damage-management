"""
modules/wms/services/validacao_estoque_service.py
Validação de estoque WMS — nova arquitetura.

Fluxo:
  1. EtiquetaService chama registrar_pendencia_triagem() após cada triagem/transferência
     → Snapshot TGFEST capturado na hora exata
     → Registro criado em WMS_ValidacoesEstoque (status=Pendente)

  2. Supervisor abre a tela → vê lista de pendências
     → Clica Validar → informa NF de transferência (com sugestão automática)
     → Sistema re-consulta TGFEST e compara com snapshot salvo
     → Resultado salvo: Aprovado | Inconsistente | Ignorado

Aba Sheets: WMS_ValidacoesEstoque
"""

import json
import logging
import threading
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from core.services.tenant_scope import empresa_visivel

logger = logging.getLogger(__name__)

SHEET_VALIDACOES  = 'WMS_ValidacoesEstoque'
ENTITY_ESTOQUE    = 'Estoque'
PREFIXO_VENDA     = '01'
SHEET_LOTES = 'WMS_LotesValidacao'

COLUNAS = [
    'id_validacao',
    'status_validacao',      # Pendente | Aprovado | Inconsistente | Ignorado
    'id_etiqueta_origem',
    'id_etiqueta_destino',
    'avaria_id',
    'codemp',
    'codprod',
    'descrprod',
    'quantidade',
    'codlocal_origem',
    'descr_local_origem',
    'prefixo_origem',
    'codlocal_destino',
    'descr_local_destino',
    'prefixo_destino',
    'operador_movimento',
    'data_movimento',
    'numnota_erp',
    'nunota_erp',
    'validado_por',
    'data_validacao',
    'resultado',
    'observacao',
    'snapshot_antes_json',
    'snapshot_depois_json',
    'numnota_venda',       # NF de origem da avaria (venda/saída)
    'numnota_devolucao',   # NF de devolução da avaria
    'codparc',             # Código do parceiro (CODPARC do Sankhya)
    'empresa',             # Nome do tenant (ex: 'Empresax') — isolamento multi-tenant
]

COLUNAS_LOTES = [
    'id_lote',
    'codlocal',
    'descrlocal',
    'codemp',
    'status',           # Pendente | Aprovado | Inconsistente | Ignorado
    'qtd_itens',
    'ids_validacoes',   # JSON: ['VAL-XXX', ...]
    'data_criacao',
    'criado_por',
    'numnota_erp',
    'nunota_erp',
    'validado_por',
    'data_validacao',
    'resultado',
    'observacao',
    'snapshot_local_json',
    'titulo',           # Título livre inserido pelo operador (etiqueta final)
    'preview_json',     # JSON: dados do 1º item para exibição no card
    'numnotas_json',    # JSON: [{numnota, nunota}, ...] — múltiplas NFs
    'empresa',          # Nome do tenant (ex: 'Empresax') — chave de isolamento multi-tenant
]

class ValidacaoEstoqueService:

    @staticmethod
    def _col_num_to_letter(n: int) -> str:
        string = ""
        while n > 0:
            n, remainder = divmod(n - 1, 26)
            string = chr(65 + remainder) + string
        return string

    def __init__(self, sankhya_service=None, cache_sankhya_service=None, sheets_service=None):
        self.sankhya          = sankhya_service
        self.cache_sankhya    = cache_sankhya_service
        self.sheets           = sheets_service
        self._ws_cache        = None
        self._ws_lotes_cache  = None
        logger.info('ValidacaoEstoqueService inicializado')

    # CHAMADO PELO EtiquetaService — registrar pendência após triagem

    def registrar_pendencia_triagem(
        self,
        id_etiqueta_origem: str,
        id_etiqueta_destino: str,
        avaria_id: str,
        codprod: str,
        descrprod: str,
        quantidade: float,
        codlocal_origem: str,
        codlocal_destino: str,
        operador: str,
        codemp: str = '',
        numnota_venda: str = '',
        numnota_devolucao: str = '',
        codparc: str = '',
        empresa: str = '',      # nome do tenant — isolamento multi-tenant
    ) -> str:
        """
        Cria registro pendente de validação e captura snapshot TGFEST.
        Chamado de forma não-bloqueante pelo EtiquetaService.
        Nunca levanta exceção — erros são logados e ignorados.
        Retorna o id_validacao criado ('' em caso de erro).
        """
        try:
            mapa     = self._mapa_locais()
            snap_raw = self._capturar_snapshot(codprod, codemp, mapa, descrprod=descrprod)
            n_locais = len(snap_raw.get('saldos', {}))
            logger.info(
                f'📸 Snapshot capturado para pendência: CODPROD={codprod} '
                f'CODEMP={codemp} DESCRPROD="{descrprod}" '
                f'— {n_locais} local(is): {list(snap_raw.get("saldos", {}).keys())}'
            )


            prefixo_orig = self._prefixo(codlocal_origem, mapa)
            prefixo_dest = self._prefixo(codlocal_destino, mapa)
            descr_orig   = mapa.get(str(codlocal_origem), {}).get('descrlocal', '')
            descr_dest   = mapa.get(str(codlocal_destino), {}).get('descrlocal', '')

            registro = {
                'id_validacao':        str(uuid.uuid4())[:12].upper(),
                'status_validacao':    'Pendente',
                'id_etiqueta_origem':  id_etiqueta_origem,
                'id_etiqueta_destino': id_etiqueta_destino,
                'avaria_id':           avaria_id,
                'codemp':              codemp,
                'codprod':             codprod,
                'descrprod':           descrprod,
                'quantidade':          quantidade,
                'codlocal_origem':     codlocal_origem,
                'descr_local_origem':  descr_orig,
                'prefixo_origem':      prefixo_orig,
                'codlocal_destino':    codlocal_destino,
                'descr_local_destino': descr_dest,
                'prefixo_destino':     prefixo_dest,
                'operador_movimento':  operador,
                'data_movimento':      datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
                'numnota_erp':         '',
                'nunota_erp':          '',
                'validado_por':        '',
                'data_validacao':      '',
                'resultado':           '',
                'observacao':          '',
                'snapshot_antes_json': json.dumps(snap_raw, ensure_ascii=False),
                'snapshot_depois_json':'',
                'numnota_venda':       numnota_venda,
                'numnota_devolucao':   numnota_devolucao,
                'codparc':             codparc,
                'empresa':             empresa,
            }

            ws = self._get_ws()
            ws.append_row(
                [registro[col] for col in COLUNAS],
                value_input_option='USER_ENTERED'
            )
            id_val = registro['id_validacao']
            logger.info(
                f'Pendência registrada: {id_val} '
                f'({codprod} | {codlocal_origem}→{codlocal_destino})'
            )
            return id_val

        except Exception as e:
            logger.error(f'Erro ao registrar pendência (não-fatal): {e}', exc_info=True)
            return ''

    # LISTAGEM DE PENDÊNCIAS

    def listar_pendencias(
        self,
        status: str = 'Pendente',
        codemp: str = '',
        dias: int = 30,
        empresa: str = '',      # nome do tenant — '' inclui registros legados sem empresa
    ) -> List[Dict]:
        """
        Retorna lista de registros de validação com filtros opcionais.

        Args:
            status: 'Pendente' | 'Aprovado' | 'Inconsistente' | 'Ignorado' | '' (todos)
            codemp: filtro de empresa ERP ('' = todas)
            dias: janela de dias para trás a partir de hoje
            empresa: nome do tenant para isolamento multi-tenant ('' = todos, inclui legados)
        """
        try:
            ws   = self._get_ws()
            rows = ws.get_all_values()

            # Log diagnóstico — revela se a aba está vazia ANTES de qualquer filtro
            total_raw = max(0, len(rows) - 1)
            logger.debug(
                f'📊 listar_pendencias: aba={SHEET_VALIDACOES} '
                f'total_linhas_raw={total_raw} '
                f'| filtros: status={status!r} codemp={codemp!r} dias={dias}'
            )

            if len(rows) < 2:
                logger.warning(
                    f'⚠️ listar_pendencias: aba {SHEET_VALIDACOES!r} tem {len(rows)} '
                    f'linha(s) — aba vazia ou apenas cabeçalho. '
                    f'Verifique se registrar_pendencia_triagem está gravando na planilha correta.'
                )
                return []

            headers = rows[0]
            data_corte = datetime.now() - timedelta(days=dias)

            resultado = []
            for row in rows[1:]:
                rec = dict(zip(headers, row + [''] * (len(headers) - len(row))))

                # Filtro de status
                if status and rec.get('status_validacao', '') != status:
                    continue

                # Filtro de empresa ERP (codemp numérico)
                if codemp and rec.get('codemp', '') not in ('', codemp):
                    continue

                # Filtro de tenant (empresa); registros legados com empresa='' são incluídos
                if not empresa_visivel(rec.get('empresa', ''), empresa):
                    continue

                # Filtro de data
                try:
                    dt = datetime.strptime(
                        rec.get('data_movimento', '')[:16], '%d/%m/%Y %H:%M'
                    )
                    if dt < data_corte:
                        continue
                except Exception:
                    pass

                resultado.append(rec)

            # Mais recentes primeiro
            resultado.sort(
                key=lambda r: r.get('data_movimento', ''), reverse=True
            )
            return resultado

        except Exception as e:
            logger.error(f'Erro ao listar pendências: {e}', exc_info=True)
            return []

    def buscar_por_id(self, id_validacao: str) -> Optional[Dict]:
        """Busca um registro específico pelo id_validacao."""
        try:
            ws   = self._get_ws()
            rows = ws.get_all_values()
            if len(rows) < 2:
                return None

            headers = rows[0]
            idx_id  = headers.index('id_validacao')

            for i, row in enumerate(rows[1:], start=2):
                if len(row) > idx_id and row[idx_id] == id_validacao:
                    rec = dict(zip(headers, row + [''] * (len(headers) - len(row))))
                    rec['_linha'] = i
                    return rec

            return None
        except Exception as e:
            logger.error(f'Erro ao buscar id {id_validacao}: {e}')
            return None

    # SUGESTÕES DE NF (busca TIPMOV=T no Sankhya)

    def buscar_sugestoes_nf(
        self,
        codprod: str,
        codemp: str,
        data_movimento: str,
        janela_antes: int = 3,
        janela_depois: int = 14,
        codlocal_destino: str = '',
    ) -> List[Dict]:
        """
        Busca notas de transferência (TIPMOV=T) próximas à data do movimento
        que contenham o produto informado movimentado para o local destino do lote.
        Retorna lista de sugestões para o supervisor selecionar.
        """
        if not self.sankhya or not self.sankhya.enabled:
            logger.warning(
                f'⚠️ _capturar_snapshot: sankhya indisponível '
                f'(sankhya={bool(self.sankhya)}, enabled={getattr(self.sankhya, "enabled", False)}) '
                f'— snapshot vazio para CODPROD={codprod}'
            )
            return {}

        try:
            try:
                dt_base = datetime.strptime(data_movimento[:10], '%d/%m/%Y')
            except Exception:
                dt_base = datetime.now()

            dt_inicio = (dt_base - timedelta(days=janela_antes)).strftime('%d/%m/%Y')
            dt_fim    = (dt_base + timedelta(days=janela_depois)).strftime('%d/%m/%Y')

            expr_parts = ['this.TIPMOV = ?', 'this.DTNEG BETWEEN ? AND ?']
            params = [
                {'value': 'T',      'type': 'S'},
                {'value': dt_inicio,'type': 'D'},
                {'value': dt_fim,   'type': 'D'},
            ]
            if codemp:
                expr_parts.append('this.CODEMP = ?')
                params.append({'value': codemp, 'type': 'I'})

            resultado = self.sankhya.load_records(
                entity='CabecalhoNota',
                fields='NUNOTA,NUMNOTA,DTNEG',
                criteria=self.sankhya._make_criteria(' AND '.join(expr_parts), params),
                limit=200,
            )

            if not resultado or resultado.get('status') != '1':
                return []

            entities    = resultado.get('responseBody', {}).get('entities', {})
            if int(entities.get('total', '0')) == 0:
                return []

            entity_list = entities.get('entity', [])
            if not isinstance(entity_list, list):
                entity_list = [entity_list]

            # Filtrar as que têm o produto movimentado para o local destino do lote
            sugestoes = []
            for ent in entity_list:
                nunota_s = self.sankhya._extract_value(ent.get('f0'))
                numnota  = self.sankhya._extract_value(ent.get('f1'))
                dtneg    = self.sankhya._extract_value(ent.get('f2'))

                if self._nota_tem_produto(nunota_s, codprod, codlocal_destino=codlocal_destino):
                    sugestoes.append({
                        'nunota':  nunota_s,
                        'numnota': numnota,
                        'dtneg':   dtneg,
                    })

            return sugestoes[:10]

        except Exception as e:
            logger.error(f'Erro ao buscar sugestões NF: {e}', exc_info=True)
            return []

    def _nota_tem_produto(self, nunota: str, codprod: str, codlocal_destino: str = '') -> bool:
        try:
            expr  = 'this.NUNOTA = ? AND this.CODPROD = ?'
            plist = [{'value': nunota, 'type': 'I'}, {'value': codprod, 'type': 'I'}]
            # Filtrar pelo local destino (CODVOL = destino da transferência em TGFITE)
            if codlocal_destino:
                expr += ' AND this.CODVOL = ?'
                plist.append({'value': codlocal_destino, 'type': 'I'})
            criteria = self.sankhya._make_criteria(expr, plist)
            resultado = self.sankhya.load_records(
                entity='ItemNota', fields='SEQUENCIA', criteria=criteria, limit=1
            )
            if resultado and resultado.get('status') == '1':
                total = int(resultado.get('responseBody', {}).get('entities', {}).get('total', '0'))
                return total > 0
            return False
        except Exception:
            return False

    # VALIDAR E SALVAR

    def validar_e_salvar(
        self,
        id_validacao: str,
        numnota_erp: str,
        nunota_erp: str,
        codemp: str,
        validado_por: str,
        observacao: str = '',
        forcar_status: str = '',   # 'Ignorado' para marcar sem validação técnica
        forcar_aprovado: bool = False,  # True em ambiente de teste (pula delta Sankhya)
    ) -> Dict:
        """
        Re-captura TGFEST, compara com snapshot_antes, salva resultado.

        Returns:
            { success, tudo_ok, resultado, mensagens, registro }
        """
        try:
            registro = self.buscar_por_id(id_validacao)
            if not registro:
                return {'success': False, 'error': f'Validação {id_validacao} não encontrada'}

            # Aprovação forçada (ambiente de teste — ignora delta Sankhya)
            if forcar_aprovado:
                reg_salvo = self._salvar_resultado(
                    registro, 'Aprovado', 'Aprovado', numnota_erp, nunota_erp,
                    validado_por, observacao, snap_depois={},
                )
                return {
                    'success':    True,
                    'tudo_ok':    True,
                    'resultado':  'Aprovado',
                    'msg_resumo': 'Aprovado automaticamente (ambiente de teste).',
                    'delta_locais': [],
                    'registro':   reg_salvo,
                }

            # Marcar como Ignorado sem análise técnica
            if forcar_status == 'Ignorado':
                return self._salvar_resultado(
                    registro, 'Ignorado', 'Ignorado', numnota_erp, nunota_erp,
                    validado_por, observacao, snap_depois={}
                )

            codprod      = registro.get('codprod', '')
            codlocal_orig = str(registro.get('codlocal_origem', '')).strip()
            quantidade   = float(registro.get('quantidade') or 0)
            codemp_reg   = codemp or registro.get('codemp', '')

            # Snapshot antes (salvo na criação)
            snap_antes_raw = registro.get('snapshot_antes_json', '{}')
            try:
                snap_antes_full = json.loads(snap_antes_raw) if snap_antes_raw else {}
            except Exception:
                snap_antes_full = {}
            # Compatibilidade: novo formato tem 'saldos'; legado é flat {codlocal: qtd}
            snap_antes = snap_antes_full.get('saldos', snap_antes_full)

            # Snapshot agora
            mapa             = self._mapa_locais()
            snap_depois_full = self._capturar_snapshot(
                codprod, codemp_reg, mapa,
                descrprod=registro.get('descrprod', ''),
            )
            snap_depois = snap_depois_full.get('saldos', snap_depois_full)

            # Comparar saldos por local
            resultados_locais = []
            tudo_ok           = True
            delta_origem      = None

            todos_locais = set(snap_antes.keys()) | set(snap_depois.keys())
            for local in todos_locais:
                qtd_a  = float(snap_antes.get(local, 0))
                qtd_d  = float(snap_depois.get(local, 0))
                delta  = qtd_d - qtd_a
                info   = mapa.get(str(local), {})

                resultados_locais.append({
                    'codlocal':   local,
                    'descrlocal': info.get('descrlocal', ''),
                    'prefixo':    self._prefixo(local, mapa),
                    'tipo':       'venda' if self._prefixo(local, mapa) == PREFIXO_VENDA else 'wms',
                    'qtd_antes':  qtd_a,
                    'qtd_depois': qtd_d,
                    'delta':      delta,
                    'eh_origem':  str(local) == codlocal_orig,
                })

                if str(local) == codlocal_orig:
                    delta_origem = delta

            # Ordenar: origem primeiro, depois destino, depois demais
            resultados_locais.sort(key=lambda x: (0 if x['eh_origem'] else 1, x['codlocal']))

            # Ponto 9: validar APENAS pelo aumento do local de DESTINO.
            # Validação pelo delta da origem foi removida — o produto pode ter
            # voltado ao local de origem em cenários de devolução, tornando a
            # comparação de origem não-confiável.
            codlocal_dest_reg = str(registro.get('codlocal_destino', '')).strip()
            qtd_dest_antes    = float(snap_antes.get(codlocal_dest_reg, 0))
            qtd_dest_depois   = float(snap_depois.get(codlocal_dest_reg, 0))
            delta_destino     = qtd_dest_depois - qtd_dest_antes

            prefixo_orig = self._prefixo(codlocal_orig, mapa)
            if prefixo_orig == PREFIXO_VENDA:
                resultado  = 'Aprovado'
                tudo_ok    = True
                msg_resumo = f'Local origem é estoque de venda (prefixo {PREFIXO_VENDA}) — não validado pelo WMS.'
            elif not codlocal_dest_reg:
                resultado  = 'Inconsistente'
                tudo_ok    = False
                msg_resumo = 'Local de destino não definido neste registro.'
            elif codlocal_orig == codlocal_dest_reg:
                # Origem e destino são o mesmo local (ex.: devolução para o próprio
                # "DISPONÍVEL"). Comparar o DELTA entre snapshot_antes e snapshot_depois
                # é pouco confiável aqui: em locais compartilhados de alto tráfego, outras
                # vendas/movimentações do mesmo produto no mesmo local — entre a criação
                # da pendência e a validação, às vezes semanas depois — mascaram o
                # aumento esperado mesmo quando o item chegou de fato. Em vez disso,
                # valida pelo saldo ATUAL absoluto: o produto está fisicamente no local
                # agora, na quantidade esperada?
                if qtd_dest_depois >= (quantidade - 0.001):
                    resultado  = 'Aprovado'
                    tudo_ok    = True
                    msg_resumo = (
                        f'Origem e destino são o mesmo local ({codlocal_dest_reg}) — validado '
                        f'pelo saldo atual: {qtd_dest_depois:.2f} disponível '
                        f'(esperado ≥ {quantidade:.2f}) '
                    )
                else:
                    resultado  = 'Inconsistente'
                    tudo_ok    = False
                    msg_resumo = (
                        f'Origem e destino são o mesmo local ({codlocal_dest_reg}) — saldo atual '
                        f'{qtd_dest_depois:.2f} é menor que o esperado ({quantidade:.2f}) 🔴'
                    )
            elif delta_destino >= (quantidade - 0.001):
                resultado  = 'Aprovado'
                tudo_ok    = True
                msg_resumo = (
                    f'Saldo do local destino {codlocal_dest_reg} aumentou {delta_destino:.2f} '
                    f'(esperado ≥ {quantidade:.2f}) '
                )
            else:
                resultado  = 'Inconsistente'
                tudo_ok    = False
                msg_resumo = (
                    f'Saldo do local destino {codlocal_dest_reg} aumentou apenas {delta_destino:.2f} '
                    f'(esperado ≥ {quantidade:.2f}) 🔴 — '
                    f'verifique se a transferência foi concluída no ERP.'
                )

            status_final = 'Aprovado' if tudo_ok else 'Inconsistente'

            registro_salvo = self._salvar_resultado(
                registro, status_final, resultado, numnota_erp, nunota_erp,
                validado_por, observacao,
                snap_depois=snap_depois_full,
            )

            return {
                'success':          True,
                'tudo_ok':          tudo_ok,
                'resultado':        resultado,
                'msg_resumo':       msg_resumo,
                'delta_locais':     resultados_locais,
                'registro':         registro_salvo,
            }

        except Exception as e:
            logger.error(f'Erro ao validar {id_validacao}: {e}', exc_info=True)
            return {'success': False, 'error': str(e)}

    # HELPERS PRIVADOS

    def _salvar_resultado(
        self, registro: Dict, status_validacao: str, resultado: str,
        numnota_erp: str, nunota_erp: str,
        validado_por: str, observacao: str,
        snap_depois: Dict,
    ) -> Dict:
        """Atualiza a linha do registro no Sheets."""
        try:
            ws      = self._get_ws()
            linha   = registro['_linha']
            headers = ws.row_values(1)

            atualizacoes = {
                'status_validacao':    status_validacao,
                'numnota_erp':         numnota_erp,
                'nunota_erp':          nunota_erp,
                'validado_por':        validado_por,
                'data_validacao':      datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
                'resultado':           resultado,
                'observacao':          observacao,
                'snapshot_depois_json': json.dumps(snap_depois, ensure_ascii=False),
            }

            for campo, valor in atualizacoes.items():
                try:
                    col = headers.index(campo) + 1
                    ws.update_cell(linha, col, valor)
                except ValueError:
                    logger.warning(f'Coluna {campo} não encontrada no Sheets')

            registro.update(atualizacoes)
            logger.info(
                f'Validação {registro["id_validacao"]} salva: {status_validacao}'
            )
            return registro

        except Exception as e:
            logger.error(f'Erro ao salvar resultado: {e}', exc_info=True)
            raise

    def _capturar_snapshot(
        self, codprod: str, codemp: str, mapa_locais: Dict,
        descrprod: str = '',
    ) -> Dict:
        """
        Consulta TGFEST e retorna snapshot enriquecido:
        {
          'meta':   { 'codemp', 'codprod', 'descrprod', 'capturado_em' },
          'saldos': { codlocal: qtd, ... }
        }
        Exclui locais de venda (prefixo 01).

        Não filtra por CODEMP: o mesmo codlocal físico pode ter estoque
        centralizado em mais de uma empresa (ex.: 6 e 7), então a validação
        é "tem estoque no local ou não", somando todas as empresas — nunca
        por empresa isolada, senão estoque migrado entre CODEMPs no ERP
        aparece como Inconsistente mesmo tendo chegado fisicamente no local.
        `codemp` é mantido só como metadado informativo do registro de origem.
        """
        meta = {
            'codemp':       str(codemp),
            'codprod':      str(codprod),
            'descrprod':    descrprod,
            'capturado_em': datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
        }

        if not self.sankhya or not self.sankhya.enabled:
            return {'meta': meta, 'saldos': {}}

        try:
            expr   = 'this.CODPROD = ?'
            params = [{'value': str(codprod), 'type': 'I'}]

            resultado = self.sankhya.load_records(
                entity=ENTITY_ESTOQUE,
                fields='CODEMP,CODPROD,CODLOCAL,ESTOQUE',
                criteria=self.sankhya._make_criteria(expr, params),
                limit=500,
            )

            if not resultado or resultado.get('status') != '1':
                return {'meta': meta, 'saldos': {}}

            entities    = resultado.get('responseBody', {}).get('entities', {})
            total       = int(entities.get('total', '0'))
            if total == 0:
                return {'meta': meta, 'saldos': {}}

            entity_list = entities.get('entity', [])
            if not isinstance(entity_list, list):
                entity_list = [entity_list]

            saldos = {}
            for ent in entity_list:
                # f0=CODEMP, f1=CODPROD, f2=CODLOCAL, f3=ESTOQUE
                codlocal  = self.sankhya._extract_value(ent.get('f2'))
                estoque_s = self.sankhya._extract_value(ent.get('f3'))
                try:
                    qtdatu = float(str(estoque_s).replace(',', '.'))
                except (ValueError, AttributeError):
                    qtdatu = 0.0

                # Incluir todos os locais (supervisor verá venda como informativo).
                # Somar entre empresas: sem filtro de CODEMP, o mesmo codlocal pode
                # vir em mais de uma linha (uma por empresa que tem saldo ali).
                chave = str(codlocal)
                saldos[chave] = saldos.get(chave, 0.0) + qtdatu

            return {'meta': meta, 'saldos': saldos}
        except Exception as e:
            logger.error(f'Erro ao capturar snapshot: {e}', exc_info=True)
            return {'meta': meta, 'saldos': {}}

    def _mapa_locais(self) -> Dict[str, Dict]:
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
        except Exception:
            return {}

    def _prefixo(self, codlocal: str, mapa: Dict) -> str:
        info = mapa.get(str(codlocal))
        if info and info.get('prefixo'):
            return str(info['prefixo']).zfill(2)
        try:
            return f'{int(codlocal) % 100:02d}'
        except (ValueError, TypeError):
            return '??'

    def _get_ws(self):
        """Retorna worksheet WMS_ValidacoesEstoque, criando se necessário."""
        if self._ws_cache:
            try:
                _ = self._ws_cache.title   # testa se ainda válido
                return self._ws_cache
            except Exception:
                self._ws_cache = None

        try:
            ws = self.sheets.sheet.worksheet(SHEET_VALIDACOES)
        except Exception:
            logger.info(f'Criando aba {SHEET_VALIDACOES}...')
            ws = self.sheets.sheet.add_worksheet(
                title=SHEET_VALIDACOES, rows=1000, cols=len(COLUNAS)
            )
            ws.append_row(COLUNAS, value_input_option='RAW')
            logger.info(f'Aba {SHEET_VALIDACOES} criada')

        self._ws_cache = ws
        return ws
    
    def _get_ws_lotes(self):
        """Retorna worksheet WMS_LotesValidacao, criando a aba se ainda não existir."""
        if self._ws_lotes_cache:
            try:
                _ = self._ws_lotes_cache.title
                return self._ws_lotes_cache
            except Exception:
                self._ws_lotes_cache = None

        try:
            ws = self.sheets.sheet.worksheet(SHEET_LOTES)
        except Exception:
            logger.info(f'Criando aba {SHEET_LOTES}...')
            try:
                ws = self.sheets.sheet.add_worksheet(
                    title=SHEET_LOTES,
                    rows=2000,
                    cols=len(COLUNAS_LOTES),
                )
                ws.append_row(COLUNAS_LOTES, value_input_option='RAW')
                logger.info(f'Aba {SHEET_LOTES} criada com {len(COLUNAS_LOTES)} colunas')
            except Exception as e_add:
                # Aba já existe mas estava fora do cache gspread — busca novamente
                logger.warning(
                    f'⚠️ add_worksheet falhou ({e_add}) — aba provavelmente já existe; '
                    f'buscando via worksheet()...'
                )
                ws = self.sheets.sheet.worksheet(SHEET_LOTES)

        self._ws_lotes_cache = ws
        return ws

    def estatisticas(self, codemp: str = '', dias: int = 30, empresa: str = '') -> Dict:
        """Contadores para o dashboard da tela.

        Args:
            codemp:  filtro de empresa ERP ('' = todas)
            dias:    janela de dias para trás a partir de hoje
            empresa: nome do tenant para isolamento multi-tenant ('' = todos)
        """
        try:
            todos = self.listar_pendencias(status='', codemp=codemp, dias=dias, empresa=empresa)
            return {
                'total':        len(todos),
                'pendente':     sum(1 for r in todos if r['status_validacao'] == 'Pendente'),
                'aprovado':     sum(1 for r in todos if r['status_validacao'] == 'Aprovado'),
                'inconsistente':sum(1 for r in todos if r['status_validacao'] == 'Inconsistente'),
                'ignorado':     sum(1 for r in todos if r['status_validacao'] == 'Ignorado'),
            }
        except Exception:
            return {'total': 0, 'pendente': 0, 'aprovado': 0, 'inconsistente': 0, 'ignorado': 0}

    # LOTES POR LOCAL

    def registrar_pendencias_e_lotes_iniciais(self, itens: List[Dict], operador: str, empresa: str = '') -> int:
        """
        Versão EM LOTE de registrar_pendencia_triagem + criar_lote_auto_etiqueta_inicial
        para as etiquetas iniciais de uma avaria. Número de chamadas ao Sheets fixo
        (1 append_rows de validações + 1 leitura de lotes + até 2 escritas de lotes),
        independente da quantidade de itens; snapshots TGFEST em paralelo.

        Cada item: id_etiqueta, avaria_id, codprod, descrprod, quantidade, codlocal_origem,
        codlocal_destino, codemp, numnota_venda, numnota_devolucao, codparc,
        descr_local_origem, descr_local_destino.
        Nunca levanta exceção. Retorna quantas pendências foram registradas.
        """
        if not itens:
            return 0
        try:
            mapa = self._mapa_locais()

            # ── Snapshots em paralelo (1 por produto) ────────────────────────
            from concurrent.futures import ThreadPoolExecutor
            chaves = {}
            for it in itens:
                chaves.setdefault((str(it['codprod']), str(it.get('codemp', ''))), it)
            snapshots = {}

            def _snap(cod_it):
                cod, it = cod_it
                return cod, self._capturar_snapshot(cod[0], cod[1], mapa, descrprod=it.get('descrprod', ''))

            with ThreadPoolExecutor(max_workers=min(6, len(chaves))) as pool:
                for cod, snap in pool.map(_snap, list(chaves.items())):
                    snapshots[cod] = snap

            agora = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
            linhas = []
            registrados = []  # (item, id_validacao)
            for it in itens:
                snap_raw = snapshots.get((str(it['codprod']), str(it.get('codemp', '')))) or {}
                id_val = str(uuid.uuid4())[:12].upper()
                registro = {
                    'id_validacao':        id_val,
                    'status_validacao':    'Pendente',
                    'id_etiqueta_origem':  it['id_etiqueta'],
                    'id_etiqueta_destino': '',
                    'avaria_id':           it.get('avaria_id', ''),
                    'codemp':              it.get('codemp', ''),
                    'codprod':             it['codprod'],
                    'descrprod':           it.get('descrprod', ''),
                    'quantidade':          it.get('quantidade', 1.0),
                    'codlocal_origem':     it.get('codlocal_origem', ''),
                    'descr_local_origem':  mapa.get(str(it.get('codlocal_origem', '')), {}).get('descrlocal', ''),
                    'prefixo_origem':      self._prefixo(it.get('codlocal_origem', ''), mapa),
                    'codlocal_destino':    it.get('codlocal_destino', ''),
                    'descr_local_destino': mapa.get(str(it.get('codlocal_destino', '')), {}).get('descrlocal', ''),
                    'prefixo_destino':     self._prefixo(it.get('codlocal_destino', ''), mapa),
                    'operador_movimento':  operador,
                    'data_movimento':      agora,
                    'numnota_erp':         '',
                    'nunota_erp':          '',
                    'validado_por':        '',
                    'data_validacao':      '',
                    'resultado':           '',
                    'observacao':          '',
                    'snapshot_antes_json': json.dumps(snap_raw, ensure_ascii=False),
                    'snapshot_depois_json': '',
                    'numnota_venda':       it.get('numnota_venda', ''),
                    'numnota_devolucao':   it.get('numnota_devolucao', ''),
                    'codparc':             it.get('codparc', ''),
                    'empresa':             empresa,
                }
                linhas.append([registro[col] for col in COLUNAS])
                registrados.append((it, id_val))

            self._get_ws().append_rows(linhas, value_input_option='USER_ENTERED')
            logger.info(f'{len(linhas)} pendência(s) de validação registradas em lote')
        except Exception as e:
            logger.error(f'Erro ao registrar pendências em lote (não-fatal): {e}', exc_info=True)
            return 0

        # ── Lotes automáticos: agrupa por avaria + local de destino ─────────
        try:
            grupos = {}
            for it, id_val in registrados:
                if not it.get('codlocal_destino'):
                    logger.warning(
                        f"⚠️ Lote auto NÃO criado para {it['id_etiqueta']}: codlocal_destino vazio "
                        f"(config ausente para empresa={empresa!r})"
                    )
                    continue
                av = it.get('avaria_id', '') or ''
                chave = (av, str(it['codlocal_destino'])) if av else (f"_sem_avaria_{id_val}", '')
                grupos.setdefault(chave, []).append((it, id_val))
            if not grupos:
                return len(registrados)

            lotes_pendentes = self.listar_lotes(
                status='Pendente', codemp='', dias=30, empresa=empresa, enriquecer_nfd=False
            )
            ws_lotes = self._get_ws_lotes()
            hdrs_lotes = None
            celulas = []
            novas_linhas = []

            for (av, local), membros in grupos.items():
                ids_novos = [iv for _, iv in membros]
                existente = None
                if av:
                    for lt in lotes_pendentes:
                        try:
                            pv = json.loads(lt.get('preview_json') or '{}')
                        except Exception:
                            pv = {}
                        if str(lt.get('codlocal', '')) == local and pv.get('avaria_id') == av:
                            existente = lt
                            break
                if existente:
                    if hdrs_lotes is None:
                        hdrs_lotes = ws_lotes.row_values(1)
                    ids_atuais = json.loads(existente.get('ids_validacoes', '[]') or '[]')
                    for iv in ids_novos:
                        if iv not in ids_atuais:
                            ids_atuais.append(iv)
                    col_ids = hdrs_lotes.index('ids_validacoes') + 1
                    col_qtd = hdrs_lotes.index('qtd_itens') + 1
                    linha_lt = existente['_linha']
                    celulas.append({
                        'range':  f'{ValidacaoEstoqueService._col_num_to_letter(col_ids)}{linha_lt}',
                        'values': [[json.dumps(ids_atuais, ensure_ascii=False)]],
                    })
                    celulas.append({
                        'range':  f'{ValidacaoEstoqueService._col_num_to_letter(col_qtd)}{linha_lt}',
                        'values': [[len(ids_atuais)]],
                    })
                    logger.info(f'Lote {existente["id_lote"]} agregado: +{len(ids_novos)} (avaria={av} local={local} total={len(ids_atuais)})')
                else:
                    it0 = membros[0][0]
                    descr_dest = it0.get('descr_local_destino', '')
                    id_lote = 'LOTE-' + str(uuid.uuid4())[:8].upper()
                    titulo_auto = f"{local} - {descr_dest}".strip(' -') if descr_dest else str(local)
                    preview = {
                        'avaria_id':           av if not av.startswith('_sem_avaria_') else '',
                        'codprod':             it0['codprod'],
                        'descrprod':           it0.get('descrprod', ''),
                        'codlocal_origem':     it0.get('codlocal_origem', ''),
                        'descr_local_origem':  it0.get('descr_local_origem', ''),
                        'numnota_venda':       it0.get('numnota_venda', ''),
                        'numnota_devolucao':   it0.get('numnota_devolucao', ''),
                        'data_triagem':        datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
                        'is_auto':             True,
                    }
                    novas_linhas.append([
                        id_lote, it0['codlocal_destino'], descr_dest, it0.get('codemp', ''), 'Pendente',
                        len(ids_novos), json.dumps(ids_novos, ensure_ascii=False),
                        datetime.now().strftime('%d/%m/%Y %H:%M:%S'), operador,
                        '', '', '', '', '', '', '',
                        titulo_auto, json.dumps(preview, ensure_ascii=False), '[]', empresa,
                    ])
                    logger.info(f'Lote auto criado: {id_lote} → avaria={av} local={local} titulo="{titulo_auto}" itens={len(ids_novos)}')

            if celulas:
                ws_lotes.batch_update(celulas)
            if novas_linhas:
                ws_lotes.append_rows(novas_linhas, value_input_option='USER_ENTERED')
        except Exception as e:
            logger.warning(f'Lotes automáticos em lote falharam (não-fatal): {e}')

        return len(registrados)

    def criar_lote_auto_etiqueta_inicial(
        self,
        id_validacao: str,
        codprod: str,
        descrprod: str,
        codlocal_origem: str,
        descr_local_origem: str,
        codlocal_destino: str,
        descr_local_destino: str,
        numnota_venda: str,
        numnota_devolucao: str,
        codemp: str,
        criado_por: str,
        avaria_id: str = '',  # chave de agrupamento: 1 lote por avaria_id + codlocal_destino
        empresa: str = '',    # nome do tenant (ex: 'Empresax') — isolamento multi-tenant
    ) -> bool:
        """
        Cria ou agrega a lote automático para etiqueta inicial.

        Regra de agrupamento:
          - Se já existir lote Pendente para a mesma avaria_id + codlocal_destino,
            agrega o id_validacao ao lote existente (atualiza ids_validacoes + qtd_itens).
          - Caso contrário, cria novo lote com título automático
            "{codlocal_destino} - {descr_local_destino}" (ex: "696 - AGUARDANDO ARMAZENAGEM").

        Resultado: avaria com 3 itens para o mesmo local → 1 lote com qtd_itens=3.
        Falha silenciosamente — nunca interrompe o fluxo principal.
        """
        try:
            ws_lotes = self._get_ws_lotes()

            # ── Tentar agregar a lote existente da mesma avaria + local ──────
            if avaria_id:
                lotes_pendentes = self.listar_lotes(status='Pendente', codemp='', dias=30, empresa=empresa)
                for lt in lotes_pendentes:
                    try:
                        pv = json.loads(lt.get('preview_json') or '{}')
                    except Exception:
                        pv = {}
                    mesmo_local  = str(lt.get('codlocal', '')) == str(codlocal_destino)
                    mesma_avaria = pv.get('avaria_id') == avaria_id
                    if mesmo_local and mesma_avaria:
                        linha_lt   = lt['_linha']
                        ids_atuais = json.loads(lt.get('ids_validacoes', '[]') or '[]')
                        if id_validacao not in ids_atuais:
                            ids_atuais.append(id_validacao)
                            qtd_nova   = len(ids_atuais)
                            hdrs_lotes = ws_lotes.row_values(1)
                            col_ids    = hdrs_lotes.index('ids_validacoes') + 1
                            col_qtd    = hdrs_lotes.index('qtd_itens')      + 1
                            ws_lotes.batch_update([
                                {
                                    'range':  f'{ValidacaoEstoqueService._col_num_to_letter(col_ids)}{linha_lt}',
                                    'values': [[json.dumps(ids_atuais, ensure_ascii=False)]],
                                },
                                {
                                    'range':  f'{ValidacaoEstoqueService._col_num_to_letter(col_qtd)}{linha_lt}',
                                    'values': [[qtd_nova]],
                                },
                            ])
                            logger.info(
                                f'📦 Lote {lt["id_lote"]} agregado: '
                                f'+{id_validacao} prod={codprod} '
                                f'(avaria={avaria_id} local={codlocal_destino} '
                                f'total={qtd_nova})'
                            )
                        return True

            # ── Criar novo lote ───────────────────────────────────────────────
            id_lote     = 'LOTE-' + str(uuid.uuid4())[:8].upper()
            titulo_auto = (
                f"{codlocal_destino} - {descr_local_destino}".strip(' -')
                if descr_local_destino else str(codlocal_destino)
            )

            preview = {
                'avaria_id':           avaria_id,
                'codprod':             codprod,
                'descrprod':           descrprod,
                'codlocal_origem':     codlocal_origem,
                'descr_local_origem':  descr_local_origem,
                'numnota_venda':       numnota_venda,
                'numnota_devolucao':   numnota_devolucao,
                'data_triagem':        datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
                'is_auto':             True,
            }

            row = [
                id_lote,
                codlocal_destino,
                descr_local_destino,
                codemp,
                'Pendente',
                1,
                json.dumps([id_validacao], ensure_ascii=False),
                datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
                criado_por,
                '', '', '', '', '', '', '',   # numnota_erp … snapshot_local_json (7 campos: índices 9-15)
                titulo_auto,                  # titulo: "696 - AGUARDANDO ARMAZENAGEM"
                json.dumps(preview, ensure_ascii=False),  # preview_json
                '[]',                         # numnotas_json
                empresa,                      # empresa: isolamento multi-tenant
            ]
            ws_lotes.append_row(row, value_input_option='USER_ENTERED')
            logger.info(
                f'📦 Lote auto criado: {id_lote} → avaria={avaria_id} '
                f'local={codlocal_destino} titulo="{titulo_auto}" '
                f'empresa={empresa} val={id_validacao}'
            )
            return True

        except Exception as e:
            logger.warning(f'criar_lote_auto_etiqueta_inicial falhou (não-fatal): {e}')
            return False

    def fechar_lote(self, id_lote: str, fechado_por: str, observacao: str = '') -> Dict:
        """Fecha o lote fisicamente sem executar validação técnica no ERP."""
        try:
            lote = self.buscar_lote_por_id(id_lote)
            if not lote:
                return {'success': False, 'error': f'Lote {id_lote} não encontrado'}

            ws      = self._get_ws_lotes()
            linha   = lote['_linha']
            headers = ws.row_values(1)

            updates = {
                'status':         'Fechado',
                'resultado':      'Fechado',
                'validado_por':   fechado_por,
                'data_validacao': datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
                'observacao':     observacao,
            }
            batch_cells = []
            for campo, valor in updates.items():
                try:
                    col = headers.index(campo) + 1
                    batch_cells.append({
                        'range':  f'{ValidacaoEstoqueService._col_num_to_letter(col)}{linha}',
                        'values': [[valor]],
                    })
                except ValueError:
                    logger.warning(f'Coluna {campo} não encontrada em WMS_LotesValidacao')
            if batch_cells:
                ws.batch_update(batch_cells)

            logger.info(f'Lote {id_lote} fechado por {fechado_por}')
            return {'success': True}
        except Exception as e:
            logger.error(f'Erro ao fechar lote {id_lote}: {e}', exc_info=True)
            return {'success': False, 'error': str(e)}

    def vincular_nf_lote(
        self,
        id_lote:       str,
        numnota_erp:   str,
        nunota_erp:    str,
        codemp:        str,
        vinculado_por: str,
        observacao:    str = '',
        numnotas:      list = None,  # [{numnota, nunota}, ...] — múltiplas NFs
    ) -> Dict:
        """
        Salva a(s) NF(s) de transferência vinculadas ao lote SEM executar a
        validação técnica no ERP (não altera status/resultado). Permite ao
        operador registrar a NF assim que ela é emitida, ficando visível para
        quem consultar o lote depois, e só disparar validar_lote() quando a
        entrada física no local de destino for confirmada.
        """
        try:
            lote = self.buscar_lote_por_id(id_lote)
            if not lote:
                return {'success': False, 'error': f'Lote {id_lote} não encontrado'}

            if not numnotas:
                numnotas = [{'numnota': numnota_erp, 'nunota': nunota_erp}] if numnota_erp else []
            if not numnotas:
                return {'success': False, 'error': 'Informe ao menos uma NF para vincular'}

            numnotas_json_str = json.dumps(numnotas, ensure_ascii=False)
            numnota_principal = numnotas[0].get('numnota', numnota_erp)
            nunota_principal  = numnotas[0].get('nunota',  nunota_erp)

            ws      = self._get_ws_lotes()
            linha   = lote['_linha']
            headers = ws.row_values(1)

            updates = {
                'numnota_erp':   numnota_principal,
                'nunota_erp':    nunota_principal,
                'numnotas_json': numnotas_json_str,
            }
            if codemp:
                updates['codemp'] = codemp
            if observacao:
                updates['observacao'] = observacao

            batch_cells = []
            for campo, valor in updates.items():
                try:
                    col = headers.index(campo) + 1
                    batch_cells.append({
                        'range':  f'{ValidacaoEstoqueService._col_num_to_letter(col)}{linha}',
                        'values': [[valor]],
                    })
                except ValueError:
                    logger.warning(f'Coluna {campo} não encontrada em WMS_LotesValidacao')
            if batch_cells:
                ws.batch_update(batch_cells)

            logger.info(
                f'📎 NF(s) vinculada(s) ao lote {id_lote} por {vinculado_por} '
                f'sem executar validação técnica: {[n.get("numnota") for n in numnotas]}'
            )
            return {'success': True, 'numnotas': numnotas}
        except Exception as e:
            logger.error(f'Erro ao vincular NF ao lote {id_lote}: {e}', exc_info=True)
            return {'success': False, 'error': str(e)}

    def gerar_lotes_por_local(
        self,
        codemp: str,
        criado_por: str,
        data_inicio: str = '',   # DD/MM/YYYY ou YYYY-MM-DD; vazio = sem corte
        data_fim: str = '',      # DD/MM/YYYY ou YYYY-MM-DD; vazio = até hoje
        titulo: str = '',        # Título livre para aparecer no card
        empresa: str = '',       # Nome do tenant — isolamento multi-tenant
        codlocal: str = '',      # Filtro por local específico (vazio = todos)
    ) -> Dict:
        """
        Agrupa validações Pendente sem lote por codlocal_destino e cria
        registros de lote em WMS_LotesValidacao.

        Regras:
        - Só considera movimentações a partir de data_inicio (inclusive).
        - Registros já vinculados a qualquer lote existente são ignorados.
        - Locais que já têm lote Pendente não recebem novo lote.
        - empresa filtra pendências e é gravada no lote para isolamento multi-tenant.
        """
        try:
            pendentes = self.listar_pendencias(status='Pendente', codemp='', dias=365, empresa=empresa)
            logger.info(
                f'📦 gerar_lotes: {len(pendentes)} pendência(s) Pendente '
                f'(empresa={empresa!r}, janela 365 dias)'
                + (f' — codlocal={codlocal!r}' if codlocal else '')
            )

            if not pendentes:
                todos = self.listar_pendencias(status='', codemp='', dias=365)
                logger.warning(
                    f'⚠️ Nenhuma pendência Pendente. '
                    f'Total (qualquer status, 365d): {len(todos)}. '
                    f'Verifique se validacao_service está injetado no EtiquetaService '
                    f'e se a constante COLUNAS está definida no service.'
                )
                return {
                    'success': True, 'lotes': [], 'total': 0,
                    'msg': (
                        'Nenhuma validação pendente encontrada. '
                        'Verifique se a integração de pendências está ativa '
                        '(validacao_service deve estar injetado no EtiquetaService).'
                    ),
                }

            # ── Filtro de período (campo: data_movimento = data da triagem) ────
            def _parse_data(s):
                for fmt in ('%Y-%m-%d', '%d/%m/%Y'):
                    try:
                        return datetime.strptime(s.strip()[:10], fmt)
                    except (ValueError, AttributeError):
                        pass
                return None

            dt_inicio = _parse_data(data_inicio) if data_inicio else None
            dt_fim    = _parse_data(data_fim)    if data_fim    else None

            if dt_inicio or dt_fim:
                antes = len(pendentes)
                def _no_periodo(rec):
                    try:
                        dt = datetime.strptime(rec.get('data_movimento', '')[:10], '%d/%m/%Y')
                        if dt_inicio and dt < dt_inicio:
                            return False
                        if dt_fim and dt > dt_fim:
                            return False
                        return True
                    except Exception:
                        return True
                pendentes = [p for p in pendentes if _no_periodo(p)]
                periodo_log = f'{data_inicio or "início"} → {data_fim or "hoje"}'
                logger.info(
                    f'Filtro período={periodo_log}: '
                    f'{antes} → {len(pendentes)} pendências (campo: data_movimento)'
                )

            if not pendentes:
                periodo_msg = f'{data_inicio or "início"} até {data_fim or "hoje"}'
                return {
                    'success': True, 'lotes': [], 'total': 0,
                    'msg': (
                        f'Nenhuma pendência no período {periodo_msg}. '
                        f'O campo comparado é data_movimento (data da triagem).'
                    ),
                }

            # ── IDs já vinculados a algum lote (qualquer status) ────────────
            lotes_todos = self.listar_lotes(status='', codemp='', dias=365, empresa=empresa)
            ids_com_lote: set = set()
            for lt in lotes_todos:
                try:
                    ids_com_lote.update(
                        json.loads(lt.get('ids_validacoes', '[]') or '[]')
                    )
                except Exception:
                    pass

            locais_com_lote_pendente = {
                str(l.get('codlocal', ''))
                for l in lotes_todos
                if l.get('status') == 'Pendente'
            }

            # ── Agrupar por local de DESTINO (onde o produto chegou) ─────────
            por_local: Dict[str, Dict] = {}
            codlocal_filtro = str(codlocal).strip() if codlocal else ''
            for p in pendentes:
                if p.get('id_validacao', '') in ids_com_lote:
                    continue
                local = str(p.get('codlocal_destino', '')).strip()
                if not local:
                    continue
                # Filtro por local específico quando solicitado
                if codlocal_filtro and local != codlocal_filtro:
                    continue
                if local not in por_local:
                    por_local[local] = {
                        'codlocal':   local,
                        'descrlocal': p.get('descr_local_destino', ''),
                        'items':      [],
                    }
                por_local[local]['items'].append(p)

            if not por_local:
                return {
                    'success': True, 'lotes': [], 'total': 0,
                    'msg': 'Todas as movimentações pendentes já possuem lote criado.',
                }

            # ── Pré-carregar nota/nfd da aba Avarias (1 leitura só) ──────────
            # numnota_venda e numnota_devolucao ficam em Avarias, não em
            # WMS_ValidacoesEstoque. Precisamos enriquecer o preview do lote.
            mapa_avaria_notas: Dict[str, Dict] = {}
            try:
                ws_av   = self.sheets.sheet.worksheet('Avarias')
                av_all  = ws_av.get_all_values()
                av_hdrs = av_all[0] if av_all else []
                idx_id  = av_hdrs.index('id')   if 'id'  in av_hdrs else None
                idx_nf  = av_hdrs.index('nota') if 'nota' in av_hdrs else None
                idx_nfd = av_hdrs.index('nfd')  if 'nfd'  in av_hdrs else None
                if idx_id is not None:
                    for av_row in (av_all[1:] if len(av_all) > 1 else []):
                        av_id = av_row[idx_id] if idx_id < len(av_row) else ''
                        if av_id:
                            mapa_avaria_notas[av_id] = {
                                'nota': av_row[idx_nf]  if idx_nf  is not None and idx_nf  < len(av_row) else '',
                                'nfd':  av_row[idx_nfd] if idx_nfd is not None and idx_nfd < len(av_row) else '',
                            }
                logger.debug(f'Mapa avaria→notas carregado: {len(mapa_avaria_notas)} avarias')
            except Exception as e_av:
                logger.warning(f'Leitura nota/nfd da aba Avarias (não-fatal): {e_av}')

            # ── Criar lotes no Sheets ────────────────────────────────────────
            ws_lotes      = self._get_ws_lotes()
            lotes_criados = []
            lotes_pulados = []

            for codlocal, info in por_local.items():
                if codlocal in locais_com_lote_pendente:
                    # Agregar novos itens ao lote Pendente existente do mesmo local
                    lote_existente = next(
                        (lt for lt in lotes_todos
                         if str(lt.get('codlocal', '')) == codlocal
                         and lt.get('status') == 'Pendente'),
                        None,
                    )
                    if lote_existente and info['items']:
                        try:
                            linha_lt   = lote_existente['_linha']
                            hdrs_lotes = ws_lotes.row_values(1)
                            ids_atuais = json.loads(lote_existente.get('ids_validacoes', '[]') or '[]')
                            novos_ids  = [i['id_validacao'] for i in info['items']
                                          if i['id_validacao'] not in ids_atuais]
                            if novos_ids:
                                ids_atualizados = ids_atuais + novos_ids
                                qtd_nova        = len(ids_atualizados)
                                col_ids = hdrs_lotes.index('ids_validacoes') + 1
                                col_qtd = hdrs_lotes.index('qtd_itens')      + 1
                                upd = [
                                    {'range': f'{ValidacaoEstoqueService._col_num_to_letter(col_ids)}{linha_lt}',
                                     'values': [[json.dumps(ids_atualizados, ensure_ascii=False)]]},
                                    {'range': f'{ValidacaoEstoqueService._col_num_to_letter(col_qtd)}{linha_lt}',
                                     'values': [[qtd_nova]]},
                                ]
                                if titulo and not (lote_existente.get('titulo') or '').strip():
                                    col_titulo = hdrs_lotes.index('titulo') + 1
                                    upd.append({'range': f'{ValidacaoEstoqueService._col_num_to_letter(col_titulo)}{linha_lt}',
                                                'values': [[titulo]]})
                                ws_lotes.batch_update(upd)
                                lotes_criados.append({
                                    'id_lote':    lote_existente['id_lote'],
                                    'codlocal':   codlocal,
                                    'descrlocal': info['descrlocal'],
                                    'qtd_itens':  qtd_nova,
                                    'agregado':   True,
                                })
                                logger.info(
                                    f'📦 Lote {lote_existente["id_lote"]} agregado: '
                                    f'+{len(novos_ids)} itens (total={qtd_nova})'
                                )
                            else:
                                # Todos os itens já estavam no lote — atualizar título se necessário
                                if titulo and not (lote_existente.get('titulo') or '').strip():
                                    col_titulo = ws_lotes.row_values(1).index('titulo') + 1
                                    ws_lotes.update_cell(lote_existente['_linha'], col_titulo, titulo)
                                lotes_pulados.append(codlocal)
                        except Exception as e_agg:
                            logger.warning(f'Falha ao agregar lote para {codlocal}: {e_agg}')
                            lotes_pulados.append(codlocal)
                    else:
                        lotes_pulados.append(codlocal)
                    continue

                id_lote  = 'LOTE-' + str(uuid.uuid4())[:8].upper()
                ids_json = json.dumps(
                    [i['id_validacao'] for i in info['items']],
                    ensure_ascii=False
                )

                # Preview: dados do primeiro item + nota/nfd enriquecidas da aba Avarias
                primeiro   = info['items'][0] if info['items'] else {}
                av_id_prim = primeiro.get('avaria_id', '')
                notas_av   = mapa_avaria_notas.get(av_id_prim, {})

                preview = {
                    'avaria_id':           av_id_prim,
                    'codprod':             primeiro.get('codprod', ''),
                    'descrprod':           primeiro.get('descrprod', ''),
                    'codlocal_origem':     primeiro.get('codlocal_origem', ''),
                    'descr_local_origem':  primeiro.get('descr_local_origem', ''),
                    # nota/nfd: prefer valor já gravado na pendência; fallback para aba Avarias
                    'numnota_venda':       primeiro.get('numnota_venda') or notas_av.get('nota', ''),
                    'numnota_devolucao':   primeiro.get('numnota_devolucao') or notas_av.get('nfd', ''),
                    # data da triagem do primeiro item (usada para filtro por período na listagem)
                    'data_triagem':        primeiro.get('data_movimento', ''),
                }
                preview_json_str = json.dumps(preview, ensure_ascii=False)

                row = [
                    id_lote,
                    codlocal,
                    info['descrlocal'],
                    codemp,
                    'Pendente',
                    len(info['items']),
                    ids_json,
                    datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
                    criado_por,
                    '', '', '', '', '', '', '',  # numnota_erp … snapshot_local_json (7 campos)
                    titulo,                      # titulo
                    preview_json_str,            # preview_json
                    '[]',                        # numnotas_json
                    empresa,                     # empresa: isolamento multi-tenant
                ]
                ws_lotes.append_row(row, value_input_option='USER_ENTERED')
                lotes_criados.append({
                    'id_lote':    id_lote,
                    'codlocal':   codlocal,
                    'descrlocal': info['descrlocal'],
                    'qtd_itens':  len(info['items']),
                })

            msg = f'{len(lotes_criados)} lote(s) criado(s)'
            if lotes_pulados:
                msg += f' | {len(lotes_pulados)} local(is) já tinham lote pendente (ignorados)'

            logger.info(f'Lotes gerados: {msg} por {criado_por}')
            return {
                'success': True,
                'lotes':   lotes_criados,
                'total':   len(lotes_criados),
                'pulados': len(lotes_pulados),
                'msg':     msg,
            }

        except Exception as e:
            logger.error(f'Erro ao gerar lotes: {e}', exc_info=True)
            return {'success': False, 'error': str(e)}

    def listar_lotes(self, status: str = 'Pendente', codemp: str = '',
                     dias: int = 90, empresa: str = '',
                     enriquecer_nfd: bool = True) -> List[Dict]:
        """Lista lotes com filtros opcionais.
        
        empresa: nome do tenant (ex: 'Empresax') — isolamento multi-tenant.
                 Obrigatório em chamadas de usuário final; vazio apenas para
                 operações internas entre serviços (ex: criar_lote_auto verifica
                 lotes existentes sem restrição de empresa).
        """
        try:
            ws   = self._get_ws_lotes()
            rows = ws.get_all_values()
            if len(rows) < 2:
                return []

            headers    = rows[0]
            data_corte = datetime.now() - timedelta(days=dias)
            resultado  = []

            for i, row in enumerate(rows[1:], start=2):
                rec = dict(zip(headers, row + [''] * max(0, len(headers) - len(row))))
                rec['_linha'] = i

                if status:
                    status_list = [s.strip() for s in status.split(',') if s.strip()]
                    if rec.get('status', '') not in status_list:
                        continue
                if codemp and rec.get('codemp', '') not in ('', codemp):
                    continue

                # Isolamento por empresa (tenant) — filtro estrito.
                # Lotes sem o campo 'empresa' preenchido são excluídos quando
                # empresa é fornecida, garantindo que uma empresa nunca veja
                # lotes de outra empresa (incluindo lotes sem atribuição).
                rec_empresa = rec.get('empresa', '').strip()
                if not empresa_visivel(rec_empresa, empresa, blank_visible_to_all=False):
                    continue

                # Filtro de período: usa data_triagem do preview_json (data da triagem
                # do primeiro item) quando disponível, senão usa data_criacao do lote.
                try:
                    data_ref = ''
                    try:
                        pv_tmp = json.loads(rec.get('preview_json') or '{}')
                        data_ref = pv_tmp.get('data_triagem', '')
                    except Exception:
                        pass
                    data_ref = data_ref or rec.get('data_criacao', '')
                    dt = datetime.strptime(data_ref[:16], '%d/%m/%Y %H:%M')
                    if dt < data_corte:
                        continue
                except Exception:
                    pass

                self._garantir_is_auto(rec)
                resultado.append(rec)

            resultado.sort(key=lambda r: r.get('data_criacao', ''), reverse=True)

            # Enriquecer preview_json com NFD atual da aba Avarias.
            # Garante que o card mostre a NFD correta mesmo quando foi inserida
            # após a criação do lote (preview_json é snapshot estático).
            try:
                ws_av   = self.sheets.sheet.worksheet('Avarias') if enriquecer_nfd else None
                av_all  = ws_av.get_all_values() if ws_av is not None else []
                if len(av_all) > 1:
                    av_hdrs = av_all[0]
                    idx_id  = av_hdrs.index('id')  if 'id'  in av_hdrs else None
                    idx_nfd = av_hdrs.index('nfd') if 'nfd' in av_hdrs else None
                    if idx_id is not None and idx_nfd is not None:
                        mapa_nfd = {
                            row[idx_id]: row[idx_nfd]
                            for row in av_all[1:]
                            if idx_id < len(row) and row[idx_id]
                        }
                        for lote in resultado:
                            try:
                                pv = json.loads(lote.get('preview_json') or '{}')
                                av_id = pv.get('avaria_id', '')
                                nfd_atual = mapa_nfd.get(av_id, '')
                                if nfd_atual:
                                    pv['numnota_devolucao'] = nfd_atual
                                    lote['preview_json'] = json.dumps(pv, ensure_ascii=False)
                            except Exception:
                                pass
            except Exception as e_av:
                logger.debug(f'ℹEnriquecimento NFD em listar_lotes (não-fatal): {e_av}')

            return resultado

        except Exception as e:
            logger.error(f'Erro ao listar lotes: {e}', exc_info=True)
            return []

    def buscar_lote_por_id(self, id_lote: str) -> Optional[Dict]:
        """Busca lote pelo id_lote."""
        try:
            ws   = self._get_ws_lotes()
            rows = ws.get_all_values()
            if len(rows) < 2:
                return None
            headers = rows[0]
            for i, row in enumerate(rows[1:], start=2):
                rec = dict(zip(headers, row + [''] * max(0, len(headers) - len(row))))
                if rec.get('id_lote') == id_lote:
                    rec['_linha'] = i
                    self._garantir_is_auto(rec)
                    return rec
            return None
        except Exception as e:
            logger.error(f'Erro ao buscar lote {id_lote}: {e}', exc_info=True)
            return None

    def _garantir_is_auto(self, lote: Dict) -> None:
        """
        Enriquece preview_json com is_auto=True para lotes automáticos de triagem
        que foram criados antes da adição do campo (retrocompatibilidade).

        Detecção: auto-lotes têm título gerado no formato "{codlocal} - {descrlocal}"
        ou apenas "{codlocal}", sem texto livre do operador.
        Opera em memória — não escreve no Sheets.
        """
        try:
            pv = json.loads(lote.get('preview_json') or '{}')
            if pv.get('is_auto'):          # já marcado → nada a fazer
                return
            titulo     = (lote.get('titulo') or '').strip()
            codlocal   = str(lote.get('codlocal', '')).strip()
            descrlocal = (lote.get('descrlocal') or '').strip()
            titulo_auto = f"{codlocal} - {descrlocal}".strip(' -') if descrlocal else codlocal
            if titulo in (titulo_auto, codlocal):
                pv['is_auto'] = True
                lote['preview_json'] = json.dumps(pv, ensure_ascii=False)
        except Exception:
            pass

    def detalhar_lote(self, id_lote: str, empresa: str = '') -> Dict:
        """
        Retorna lote + todos os registros de validação + snapshot atual do local.
        Usado pelo modal de validação.
        empresa: se fornecida, valida que o lote pertence ao mesmo tenant.
        """
        try:
            lote = self.buscar_lote_por_id(id_lote)
            if not lote:
                return {'success': False, 'error': f'Lote {id_lote} não encontrado'}

            # Isolamento estrito: empresa da sessão deve bater exatamente com empresa do lote
            lote_empresa = lote.get('empresa', '').strip()
            if not empresa_visivel(lote_empresa, empresa, blank_visible_to_all=False):
                logger.warning(
                    f'🔒 Acesso negado ao lote {id_lote}: '
                    f'lote.empresa={lote_empresa!r} != sessão={empresa!r}'
                )
                return {'success': False, 'error': 'Lote não encontrado'}

            # Buscar registros individuais do lote
            ids_val   = json.loads(lote.get('ids_validacoes', '[]') or '[]')
            registros = []
            for id_val in ids_val:
                reg = self.buscar_por_id(id_val)
                if reg:
                    registros.append(reg)

            # Enriquecer registros com dados atuais da aba Avarias (NFD, NF, parceiro)
            # Garante que informações preenchidas após a criação do lote apareçam
            try:
                ws_av   = self.sheets.sheet.worksheet('Avarias')
                av_all  = ws_av.get_all_values()
                if len(av_all) > 1:
                    av_hdrs    = av_all[0]
                    idx_av_id  = av_hdrs.index('id')      if 'id'      in av_hdrs else None
                    idx_nfd    = av_hdrs.index('nfd')     if 'nfd'     in av_hdrs else None
                    idx_nota   = av_hdrs.index('nota')    if 'nota'    in av_hdrs else None
                    idx_codpar = av_hdrs.index('codparc') if 'codparc' in av_hdrs else None
                    if idx_av_id is not None:
                        mapa_av: Dict[str, Dict] = {}
                        for av_row in av_all[1:]:
                            av_id = av_row[idx_av_id] if idx_av_id < len(av_row) else ''
                            if av_id:
                                mapa_av[av_id] = {
                                    'nfd':     av_row[idx_nfd]    if idx_nfd    is not None and idx_nfd    < len(av_row) else '',
                                    'nota':    av_row[idx_nota]   if idx_nota   is not None and idx_nota   < len(av_row) else '',
                                    'codparc': av_row[idx_codpar] if idx_codpar is not None and idx_codpar < len(av_row) else '',
                                }
                        for reg in registros:
                            av_id = reg.get('avaria_id', '')
                            if av_id and av_id in mapa_av:
                                av = mapa_av[av_id]
                                # Sempre usa o valor atual da avaria (pode ter sido preenchido depois)
                                if av.get('nfd'):
                                    reg['numnota_devolucao'] = av['nfd']
                                if av.get('nota'):
                                    reg['numnota_venda'] = av['nota']
                                if av.get('codparc'):
                                    reg['codparc'] = av['codparc']
            except Exception as e_av:
                logger.warning(f'Enriquecimento Avarias em detalhar_lote (não-fatal): {e_av}')

            # Snapshot atual do local completo
            codlocal   = lote.get('codlocal', '')
            codemp_lot = lote.get('codemp', '')
            snap_atual = self._capturar_snapshot_local(codlocal, codemp_lot, registros)

            return {
                'success':        True,
                'lote':           {k: v for k, v in lote.items() if k != '_linha'},
                'registros':      registros,
                'snapshot_atual': snap_atual,
            }

        except Exception as e:
            logger.error(f'Erro ao detalhar lote {id_lote}: {e}', exc_info=True)
            return {'success': False, 'error': str(e)}

    def _capturar_snapshot_local(self, codlocal: str, codemp: str,
                                  registros_ref: list = None) -> Dict:
        """
        Consulta TGFEST e retorna TODOS os produtos de um local específico.
        registros_ref é usado para enriquecer com descrprod já conhecida.

        Retorno:
            {
              'meta': { codlocal, codemp, capturado_em },
              'produtos': [{ codprod, descrprod, qtd }, ...]
            }
        """
        meta = {
            'codlocal':    str(codlocal),
            'codemp':      str(codemp),
            'capturado_em': datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
        }

        # Mapa codprod → descrprod a partir dos registros conhecidos
        desc_map: Dict[str, str] = {}
        if registros_ref:
            for r in registros_ref:
                cp = str(r.get('codprod', ''))
                if cp and r.get('descrprod'):
                    desc_map[cp] = r['descrprod']

        # Guarda: codlocal vazio causaria param[0]=null no PreparedStatement do Sankhya
        if not str(codlocal).strip():
            logger.warning(
                f'⚠️ _capturar_snapshot_local: codlocal vazio — snapshot ignorado '
                f'(lote sem codlocal gravado no Sheets)'
            )
            return {'meta': meta, 'produtos': []}

        if not self.sankhya or not self.sankhya.enabled:
            return {'meta': meta, 'produtos': []}

        try:
            expr   = 'this.CODLOCAL = ?'
            params = [{'value': str(codlocal), 'type': 'I'}]

            # codemp do lote pode ser nome do tenant ('Teste') — ignorar se não numérico
            try:
                if codemp and int(codemp):
                    expr  += ' AND this.CODEMP = ?'
                    params.append({'value': str(int(codemp)), 'type': 'I'})
            except (ValueError, TypeError):
                pass

            criteria = self.sankhya._make_criteria(expr, params)
            result   = self.sankhya.load_records(
                entity=ENTITY_ESTOQUE,
                fields='CODPROD,ESTOQUE',
                criteria=criteria,
                limit=500,
            )

            produtos = []
            if result and result.get('status') == '1':
                ent_list = (
                    result.get('responseBody', {})
                          .get('entities', {})
                          .get('entity', [])
                )
                if isinstance(ent_list, dict):
                    ent_list = [ent_list]

                for ent in ent_list:
                    f = ent.get('f', [])
                    if len(f) >= 2:
                        codprod = str(f[0].get('$', ''))
                        qtd     = float(str(f[1].get('$', '0')).replace(',', '.'))
                        if qtd != 0:
                            produtos.append({
                                'codprod':   codprod,
                                'descrprod': desc_map.get(codprod, ''),
                                'qtd':       qtd,
                            })

            logger.info(
                f'📸 Snapshot local {codlocal}: {len(produtos)} produto(s) com saldo'
            )
            return {'meta': meta, 'produtos': produtos}

        except Exception as e:
            logger.error(f'Erro snapshot local {codlocal}: {e}', exc_info=True)
            return {'meta': meta, 'produtos': []}

    def validar_lote(
        self,
        id_lote:      str,
        numnota_erp:  str,
        nunota_erp:   str,
        codemp:       str,
        validado_por: str,
        observacao:   str = '',
        numnotas:     list = None,  # [{numnota, nunota}, ...] — múltiplas NFs
    ) -> Dict:
        """
        Valida todos os registros de um lote de uma só vez.
        Aceita múltiplas NFs de transferência via numnotas.
        A soma das quantidades deve bater com o total do lote;
        estoque disponível pode ser igual ou superior ao snapshot anterior.
        """
        try:
            lote = self.buscar_lote_por_id(id_lote)
            if not lote:
                return {'success': False, 'error': f'Lote {id_lote} não encontrado'}

            # Lotes finais (manuais) só podem ser validados a partir de status=Fechado,
            # ou revalidados a partir de status=Inconsistente (ex.: correção de lógica
            # de validação, ou saldo do ERP que se regularizou desde a 1ª tentativa).
            # Auto-lotes (is_auto=True, criados na triagem) validam do Pendente, e
            # também podem ser revalidados a partir de Inconsistente.
            try:
                pv_guard     = json.loads(lote.get('preview_json') or '{}')
                is_auto_lote = pv_guard.get('is_auto', False)
            except Exception:
                is_auto_lote = False
            status_lote_atual = lote.get('status', '')
            if not is_auto_lote and status_lote_atual not in ('Fechado', 'Inconsistente'):
                return {
                    'success': False,
                    'error': (
                        'Este lote final precisa ser fechado antes de ser validado. '
                        'Clique em "Fechar Lote" para encerrar o recebimento de itens '
                        'e depois valide na aba Fechados.'
                    ),
                }

            # Normalizar lista de NFs — fallback para campos legados se lista vazia
            if not numnotas:
                numnotas = [{'numnota': numnota_erp, 'nunota': nunota_erp}] if numnota_erp else []
            numnotas_json_str = json.dumps(numnotas, ensure_ascii=False)

            # Usar a primeira NF como numnota/nunota principal (compatibilidade legada)
            numnota_principal = numnotas[0].get('numnota', numnota_erp) if numnotas else numnota_erp
            nunota_principal  = numnotas[0].get('nunota',  nunota_erp)  if numnotas else nunota_erp

            codemp_efetivo = codemp or lote.get('codemp', '')
            ids_val        = json.loads(lote.get('ids_validacoes', '[]') or '[]')
            resultados     = []
            erros          = []

            # Em empresa "Teste", aprovação automática sem verificação de delta Sankhya.
            empresa_lote  = lote.get('empresa', '')
            modo_teste    = empresa_lote.lower() == 'teste'

            for id_val in ids_val:
                try:
                    res = self.validar_e_salvar(
                        id_validacao=id_val,
                        numnota_erp=numnota_principal,
                        nunota_erp=nunota_principal,
                        codemp=codemp_efetivo,
                        validado_por=validado_por,
                        observacao=observacao,
                        forcar_aprovado=modo_teste,
                    )
                    resultados.append(res)
                except Exception as e:
                    erros.append({'id': id_val, 'erro': str(e)})
                    logger.warning(f'Erro ao validar {id_val} no lote {id_lote}: {e}')

            # Resultado consolidado
            tem_inconsistente = any(
                r.get('resultado') == 'Inconsistente' for r in resultados
            )
            status_lote   = 'Inconsistente' if tem_inconsistente else 'Aprovado'
            resultado_str = status_lote

            # Salvar resultado no lote
            ws      = self._get_ws_lotes()
            linha   = lote['_linha']
            headers = ws.row_values(1)

            updates = {
                'status':         status_lote,
                'numnota_erp':    numnota_principal,
                'nunota_erp':     nunota_principal,
                'validado_por':   validado_por,
                'data_validacao': datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
                'resultado':      resultado_str,
                'observacao':     observacao,
                'numnotas_json':  numnotas_json_str,
            }
            batch_cells = []
            for campo, valor in updates.items():
                try:
                    col = headers.index(campo) + 1
                    batch_cells.append({
                        'range':  f'{ValidacaoEstoqueService._col_num_to_letter(col)}{linha}',
                        'values': [[valor]],
                    })
                except ValueError:
                    logger.warning(f'Coluna {campo} não encontrada em WMS_LotesValidacao')
            if batch_cells:
                ws.batch_update(batch_cells)

            logger.info(
                f'Lote {id_lote} validado: {status_lote} '
                f'({len(resultados)} itens, {len(erros)} erro(s), {len(numnotas)} NF(s))'
            )

            # Atualizar lotes downstream em background — não bloqueia a resposta.
            # Quando este lote (ex: 696) é validado, lotes pendentes das mesmas
            # avarias passam a mostrar 696 como origem em vez do local original.
            avaria_ids_lote = set()
            for res in resultados:
                av = (res.get('registro') or {}).get('avaria_id', '')
                if av:
                    avaria_ids_lote.add(av)
            if avaria_ids_lote:
                codlocal_lote = lote.get('codlocal', '')
                threading.Thread(
                    target=self._atualizar_origem_lotes_relacionados,
                    kwargs={
                        'codlocal_confirmado':    codlocal_lote,
                        'avaria_ids_confirmados': avaria_ids_lote,
                        'lote_id_excluir':        id_lote,
                    },
                    daemon=True,
                ).start()

            # Quando o lote é aprovado ou inconsistente, atualizar localizacao em Itens_Avarias.
            # Inconsistente = movimento ocorreu fisicamente mas Sankhya não refletiu ainda.
            if status_lote in ('Aprovado', 'Inconsistente'):
                codlocal_lote  = lote.get('codlocal', '')
                descrlocal_lote = lote.get('descrlocal', '')
                if codlocal_lote:
                    localizacao_formatada = (
                        f"{codlocal_lote} - {descrlocal_lote}"
                        if descrlocal_lote else str(codlocal_lote)
                    )
                    threading.Thread(
                        target=self._atualizar_localizacao_itens_aprovados,
                        args=(localizacao_formatada, resultados),
                        daemon=True,
                    ).start()

            return {
                'success':     True,
                'status_lote': status_lote,
                'resultado':   resultado_str,
                'total':       len(ids_val),
                'processados': len(resultados),
                'erros':       erros,
                'registros':   [r.get('registro', {}) for r in resultados],
            }

        except Exception as e:
            logger.error(f'Erro ao validar lote {id_lote}: {e}', exc_info=True)
            return {'success': False, 'error': str(e)}

    def ignorar_lote(self, id_lote: str, validado_por: str,
                     observacao: str = '') -> Dict:
        """Marca o lote como Ignorado sem validação técnica."""
        try:
            lote = self.buscar_lote_por_id(id_lote)
            if not lote:
                return {'success': False, 'error': f'Lote {id_lote} não encontrado'}

            ws      = self._get_ws_lotes()
            linha   = lote['_linha']
            headers = ws.row_values(1)

            updates = {
                'status':         'Ignorado',
                'resultado':      'Ignorado',
                'validado_por':   validado_por,
                'data_validacao': datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
                'observacao':     observacao,
            }
            batch_cells = []
            for campo, valor in updates.items():
                try:
                    col = headers.index(campo) + 1
                    batch_cells.append({
                        'range':  f'{ValidacaoEstoqueService._col_num_to_letter(col)}{linha}',
                        'values': [[valor]],
                    })
                except ValueError:
                    pass
            if batch_cells:
                ws.batch_update(batch_cells)

            return {'success': True}
        except Exception as e:
            logger.error(f'Erro ao ignorar lote {id_lote}: {e}', exc_info=True)
            return {'success': False, 'error': str(e)}

    def cancelar_pendencias_de_etiqueta(
        self, id_etiqueta_origem: str, operador: str, motivo: str = ''
    ) -> Dict:
        """
        Marca como 'Ignorado' toda pendência Pendente em WMS_ValidacoesEstoque cujo
        id_etiqueta_origem seja a etiqueta informada, e remove essa pendência de
        qualquer lote em WMS_LotesValidacao que a contenha (apagando o lote se ele
        ficar vazio, ou só ajustando ids_validacoes/qtd_itens se o lote for
        compartilhado com outras avarias/etiquetas).

        Usado quando uma etiqueta é baixada por um motivo que torna a pendência de
        validação original obsoleta (ex: item recusado depois de já ter gerado
        pendência para o destino normal) — evita pendência órfã presa num lote.

        Returns:
            { success, canceladas: int, lotes_ajustados: int, lotes_removidos: int }
        """
        try:
            ws = self._get_ws()
            rows = ws.get_all_values()
            if len(rows) < 2:
                return {'success': True, 'canceladas': 0, 'lotes_ajustados': 0, 'lotes_removidos': 0}

            headers = rows[0]
            idx_id      = headers.index('id_validacao')
            idx_origem  = headers.index('id_etiqueta_origem')
            idx_status  = headers.index('status_validacao')

            ids_cancelados = []
            data_agora = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
            campos_para_limpar = ['status_validacao', 'resultado', 'validado_por', 'data_validacao', 'observacao']

            for i, row in enumerate(rows[1:], start=2):
                row = row + [''] * max(0, len(headers) - len(row))
                if row[idx_origem] != id_etiqueta_origem or row[idx_status] != 'Pendente':
                    continue

                valores = {
                    'status_validacao': 'Ignorado',
                    'resultado':        'Ignorado',
                    'validado_por':     operador,
                    'data_validacao':   data_agora,
                    'observacao':       motivo or 'Pendência cancelada automaticamente (etiqueta de origem baixada/recusada)',
                }
                batch_cells = []
                for campo, valor in valores.items():
                    if campo in headers:
                        col = headers.index(campo) + 1
                        batch_cells.append({
                            'range':  f'{ValidacaoEstoqueService._col_num_to_letter(col)}{i}',
                            'values': [[valor]],
                        })
                if batch_cells:
                    ws.batch_update(batch_cells)
                ids_cancelados.append(row[idx_id])

            if not ids_cancelados:
                return {'success': True, 'canceladas': 0, 'lotes_ajustados': 0, 'lotes_removidos': 0}

            lotes_ajustados = 0
            lotes_removidos = 0
            try:
                ws_lotes = self._get_ws_lotes()
                lote_rows = ws_lotes.get_all_values()
                if len(lote_rows) >= 2:
                    lote_headers = lote_rows[0]
                    idx_ids_val  = lote_headers.index('ids_validacoes')
                    idx_qtd      = lote_headers.index('qtd_itens') if 'qtd_itens' in lote_headers else None
                    ids_cancelados_set = set(ids_cancelados)

                    # 1ª passada: só ler e decidir — nenhuma escrita ainda, para não
                    # desalinhar os índices de linha (_linha) usados na 2ª passada.
                    linhas_para_apagar = []
                    linhas_para_ajustar = []
                    for j, lrow in enumerate(lote_rows[1:], start=2):
                        lrow = lrow + [''] * max(0, len(lote_headers) - len(lrow))
                        try:
                            ids_no_lote = set(json.loads(lrow[idx_ids_val] or '[]'))
                        except (json.JSONDecodeError, TypeError):
                            continue
                        if not (ids_no_lote & ids_cancelados_set):
                            continue

                        restantes = ids_no_lote - ids_cancelados_set
                        if not restantes:
                            linhas_para_apagar.append(j)
                        else:
                            linhas_para_ajustar.append((j, restantes))

                    # 2ª passada: aplicar. Ajustes primeiro (update_cell não desloca
                    # linhas); deletes por último, em ordem decrescente, para não
                    # invalidar o índice de nenhuma linha ainda não processada.
                    for j, restantes in linhas_para_ajustar:
                        ws_lotes.update_cell(j, idx_ids_val + 1, json.dumps(sorted(restantes)))
                        if idx_qtd is not None:
                            ws_lotes.update_cell(j, idx_qtd + 1, str(len(restantes)))
                        lotes_ajustados += 1
                    for j in sorted(linhas_para_apagar, reverse=True):
                        ws_lotes.delete_rows(j)
                        lotes_removidos += 1
            except Exception as e_lote:
                logger.warning(f'cancelar_pendencias_de_etiqueta: ajuste de lote falhou (não-fatal): {e_lote}')

            logger.info(
                f'🚫 {len(ids_cancelados)} pendência(s) canceladas para etiqueta {id_etiqueta_origem} '
                f'| lotes ajustados={lotes_ajustados} removidos={lotes_removidos}'
            )
            return {
                'success': True,
                'canceladas': len(ids_cancelados),
                'lotes_ajustados': lotes_ajustados,
                'lotes_removidos': lotes_removidos,
            }
        except Exception as e:
            logger.error(f'Erro ao cancelar pendências da etiqueta {id_etiqueta_origem}: {e}', exc_info=True)
            return {'success': False, 'error': str(e)}

    def gerar_lote_por_etiquetas(
        self,
        ids_etiquetas: List[str],
        titulo: str,
        codemp: str,
        criado_por: str,
        empresa: str = '',
    ) -> Dict:
        """
        Cria um único lote a partir de uma lista de ids de etiquetas escaneadas.

        Busca em WMS_ValidacoesEstoque registros Pendentes cujo
        id_etiqueta_destino (ou id_etiqueta_origem para triagem inicial)
        esteja na lista informada.

        Retorna:
            {
              success, id_lote, qtd_itens, ids_nao_encontrados,
              ids_ja_em_lote, msg
            }
        """
        try:
            if not ids_etiquetas:
                return {'success': False, 'error': 'Nenhuma etiqueta informada'}

            ids_set = set(str(e).strip().upper() for e in ids_etiquetas if str(e).strip())
            if not ids_set:
                return {'success': False, 'error': 'Nenhuma etiqueta válida informada'}

            # BLOQUEIO ETIQUETA INICIAL (só pode gerar lote para etiqueta final, ex: 06-, 07-, 08-)
            iniciais = [e for e in ids_set if e.startswith('00-')]
            if iniciais:
                return {
                    'success': False, 
                    'error': f'O lote só pode ser gerado a partir da etiqueta final. Não é permitido gerar lote para etiqueta inicial: {", ".join(iniciais)}'
                }

            # Carregar pendências (sem filtro de empresa para localizar por etiqueta)
            pendentes = self.listar_pendencias(status='Pendente', codemp='', dias=365, empresa=empresa)

            # IDs já em algum lote
            lotes_todos = self.listar_lotes(status='', codemp='', dias=365, empresa=empresa)
            ids_com_lote: set = set()
            for lt in lotes_todos:
                try:
                    ids_com_lote.update(json.loads(lt.get('ids_validacoes', '[]') or '[]'))
                except Exception:
                    pass

            # Mapear etiqueta → registro de validação
            mapa_etq: Dict[str, Dict] = {}
            for p in pendentes:
                etq_dest = str(p.get('id_etiqueta_destino', '')).strip().upper()
                etq_orig = str(p.get('id_etiqueta_origem', '')).strip().upper()
                # Etiqueta destino tem prioridade; origem usada quando destino vazio (triagem inicial)
                etq_ref = etq_dest if etq_dest else etq_orig
                if etq_ref and etq_ref in ids_set:
                    # Preferir o mais recente para cada etiqueta
                    if etq_ref not in mapa_etq:
                        mapa_etq[etq_ref] = p
                    else:
                        # Manter o mais recente (data_movimento)
                        dt_existente = mapa_etq[etq_ref].get('data_movimento', '')
                        dt_novo      = p.get('data_movimento', '')
                        if dt_novo > dt_existente:
                            mapa_etq[etq_ref] = p

            ids_encontrados    = set(mapa_etq.keys())
            ids_nao_encontrados = list(ids_set - ids_encontrados)
            ids_ja_em_lote     = []
            validacoes_selecionadas = []

            for etq, reg in mapa_etq.items():
                id_val = reg.get('id_validacao', '')
                if id_val in ids_com_lote:
                    ids_ja_em_lote.append(etq)
                else:
                    validacoes_selecionadas.append(reg)

            # Tentar gerar pendência para etiquetas não encontradas (se existirem em Itens_Avarias)
            if ids_nao_encontrados:
                try:
                    ws_avarias = self.sheets.sheet.worksheet('Itens_Avarias')
                    todas_etiquetas = ws_avarias.get_all_values()
                    if len(todas_etiquetas) > 1:
                        hdrs = todas_etiquetas[0]
                        idx_id = hdrs.index('id_etiqueta') if 'id_etiqueta' in hdrs else -1
                        idx_av = hdrs.index('avaria_id') if 'avaria_id' in hdrs else -1
                        idx_cp = hdrs.index('codprod') if 'codprod' in hdrs else -1
                        idx_dp = hdrs.index('descrprod') if 'descrprod' in hdrs else -1
                        idx_qtd = hdrs.index('qtd_avariada') if 'qtd_avariada' in hdrs else hdrs.index('qtdneg') if 'qtdneg' in hdrs else -1
                        idx_lo = hdrs.index('codlocalorig') if 'codlocalorig' in hdrs else hdrs.index('codlocal') if 'codlocal' in hdrs else -1
                        idx_ld = hdrs.index('codlocaldest') if 'codlocaldest' in hdrs else -1
                        idx_loc = hdrs.index('localizacao') if 'localizacao' in hdrs else -1

                        if idx_id >= 0:
                            encontrados_agora = []
                            for row in todas_etiquetas[1:]:
                                if len(row) > idx_id:
                                    id_etq = str(row[idx_id]).strip().upper()
                                    if id_etq in ids_nao_encontrados:
                                        av_id = str(row[idx_av]) if idx_av >= 0 and len(row) > idx_av else ''
                                        cp = str(row[idx_cp]) if idx_cp >= 0 and len(row) > idx_cp else ''
                                        dp = str(row[idx_dp]) if idx_dp >= 0 and len(row) > idx_dp else ''
                                        qtd_str = str(row[idx_qtd]).replace(',', '.') if idx_qtd >= 0 and len(row) > idx_qtd else '1'
                                        lo = str(row[idx_lo]) if idx_lo >= 0 and len(row) > idx_lo else ''
                                        ld = str(row[idx_ld]) if idx_ld >= 0 and len(row) > idx_ld else ''
                                        loc_str = str(row[idx_loc]) if idx_loc >= 0 and len(row) > idx_loc else ''
                                        
                                        # Baseia-se na coluna localizacao para o destino, pois é ela que é preenchida na movimentação
                                        if loc_str:
                                            if ' - ' in loc_str:
                                                ld_extract = loc_str.split(' - ')[0].strip()
                                                if ld_extract.isdigit():
                                                    ld = ld_extract
                                            elif loc_str.strip().isdigit():
                                                ld = loc_str.strip()

                                        
                                        try:
                                            qtd = float(qtd_str)
                                        except Exception:
                                            qtd = 1.0
                                            
                                        novo_id = self.registrar_pendencia_triagem(
                                            id_etiqueta_origem=id_etq,
                                            id_etiqueta_destino=id_etq,
                                            avaria_id=av_id,
                                            codprod=cp,
                                            descrprod=dp,
                                            quantidade=qtd,
                                            codlocal_origem=lo,
                                            codlocal_destino=ld,
                                            operador=criado_por,
                                            codemp=codemp,
                                            empresa=empresa
                                        )
                                        if novo_id:
                                            # Constrói o registro em memória pois o cache (buscar_por_id) ainda não o terá
                                            mapa_loc = self._mapa_locais()
                                            d_orig = mapa_loc.get(str(lo), {}).get('descrlocal', '')
                                            d_dest = mapa_loc.get(str(ld), {}).get('descrlocal', '')
                                            
                                            rec = {
                                                'id_validacao': novo_id,
                                                'id_etiqueta_origem': id_etq,
                                                'id_etiqueta_destino': id_etq,
                                                'avaria_id': av_id,
                                                'codprod': cp,
                                                'descrprod': dp,
                                                'quantidade': qtd,
                                                'codlocal_origem': lo,
                                                'descr_local_origem': d_orig,
                                                'codlocal_destino': ld,
                                                'descr_local_destino': d_dest,
                                                'operador_movimento': criado_por,
                                                'codemp': codemp,
                                                'empresa': empresa,
                                                'status_validacao': 'Pendente'
                                            }
                                            validacoes_selecionadas.append(rec)
                                            encontrados_agora.append(id_etq)
                                                
                            ids_nao_encontrados = [e for e in ids_nao_encontrados if e not in encontrados_agora]
                except Exception as e:
                    logger.warning(f'Erro ao tentar gerar pendências para etiquetas não encontradas: {e}')

            if not validacoes_selecionadas:
                msg_parts = []
                if ids_nao_encontrados:
                    msg_parts.append(f'{len(ids_nao_encontrados)} etiqueta(s) não localizada(s) ou inválida(s)')
                if ids_ja_em_lote:
                    msg_parts.append(f'{len(ids_ja_em_lote)} etiqueta(s) já possui(em) lote')
                return {
                    'success':              False,
                    'error':               'Etiqueta(s) inválida(s) ou não encontrada(s). É necessário gerar nova(s) etiqueta(s) para o(s) respectivo(s) item(ns). ' + '; '.join(msg_parts),
                    'ids_nao_encontrados': ids_nao_encontrados,
                    'ids_ja_em_lote':      ids_ja_em_lote,
                }

            # Pré-carregar notas da aba Avarias
            mapa_avaria_notas: Dict[str, Dict] = {}
            try:
                ws_av   = self.sheets.sheet.worksheet('Avarias')
                av_all  = ws_av.get_all_values()
                av_hdrs = av_all[0] if av_all else []
                idx_id  = av_hdrs.index('id')   if 'id'   in av_hdrs else None
                idx_nf  = av_hdrs.index('nota') if 'nota' in av_hdrs else None
                idx_nfd = av_hdrs.index('nfd')  if 'nfd'  in av_hdrs else None
                if idx_id is not None:
                    for av_row in (av_all[1:] if len(av_all) > 1 else []):
                        av_id = av_row[idx_id] if idx_id < len(av_row) else ''
                        if av_id:
                            mapa_avaria_notas[av_id] = {
                                'nota': av_row[idx_nf]  if idx_nf  is not None and idx_nf  < len(av_row) else '',
                                'nfd':  av_row[idx_nfd] if idx_nfd is not None and idx_nfd < len(av_row) else '',
                            }
            except Exception as e_av:
                logger.warning(f'Leitura nota/nfd da aba Avarias (não-fatal): {e_av}')

            # Agrupar por local de destino
            lotes_por_local = {}
            for reg in validacoes_selecionadas:
                ld = str(reg.get('codlocal_destino', '')).strip()
                if ld not in lotes_por_local:
                    lotes_por_local[ld] = []
                lotes_por_local[ld].append(reg)

            # Locais que já têm lote Pendente — novos itens são agregados nele
            # em vez de abrir um lote separado (mesmo comportamento de
            # gerar_lotes_por_local, para não duplicar lote do mesmo local).
            locais_com_lote_pendente = {
                str(lt.get('codlocal', ''))
                for lt in lotes_todos
                if lt.get('status') == 'Pendente'
            }

            lotes_criados_ids = []
            ws_lotes = self._get_ws_lotes()

            for codlocal_destino, validacoes_local in lotes_por_local.items():
                if codlocal_destino in locais_com_lote_pendente:
                    lote_existente = next(
                        (lt for lt in lotes_todos
                         if str(lt.get('codlocal', '')) == codlocal_destino
                         and lt.get('status') == 'Pendente'),
                        None,
                    )
                    if lote_existente:
                        try:
                            linha_lt   = lote_existente['_linha']
                            hdrs_lotes = ws_lotes.row_values(1)
                            ids_atuais = json.loads(lote_existente.get('ids_validacoes', '[]') or '[]')
                            ids_novos  = [r['id_validacao'] for r in validacoes_local
                                          if r['id_validacao'] not in ids_atuais]
                            if ids_novos:
                                ids_atualizados = ids_atuais + ids_novos
                                qtd_nova        = len(ids_atualizados)
                                col_ids = hdrs_lotes.index('ids_validacoes') + 1
                                col_qtd = hdrs_lotes.index('qtd_itens')      + 1
                                upd = [
                                    {'range': f'{ValidacaoEstoqueService._col_num_to_letter(col_ids)}{linha_lt}',
                                     'values': [[json.dumps(ids_atualizados, ensure_ascii=False)]]},
                                    {'range': f'{ValidacaoEstoqueService._col_num_to_letter(col_qtd)}{linha_lt}',
                                     'values': [[qtd_nova]]},
                                ]
                                if titulo and not (lote_existente.get('titulo') or '').strip():
                                    col_titulo = hdrs_lotes.index('titulo') + 1
                                    upd.append({'range': f'{ValidacaoEstoqueService._col_num_to_letter(col_titulo)}{linha_lt}',
                                                'values': [[titulo]]})
                                ws_lotes.batch_update(upd)
                                lotes_criados_ids.append(lote_existente['id_lote'])
                                logger.info(
                                    f'📦 Lote {lote_existente["id_lote"]} agregado (por etiquetas): '
                                    f'+{len(ids_novos)} itens (total={qtd_nova})'
                                )
                            continue
                        except Exception as e_agg:
                            logger.warning(f'Falha ao agregar lote por etiquetas para {codlocal_destino}: {e_agg}')
                            # cai para criar um lote novo abaixo

                id_lote  = 'LOTE-' + str(uuid.uuid4())[:8].upper()
                primeiro = validacoes_local[0]
                av_id_prim = primeiro.get('avaria_id', '')
                notas_av   = mapa_avaria_notas.get(av_id_prim, {})
                descrlocal = primeiro.get('descr_local_destino', '')
    
                preview = {
                    'avaria_id':           av_id_prim,
                    'codprod':             primeiro.get('codprod', ''),
                    'descrprod':           primeiro.get('descrprod', ''),
                    'codlocal_origem':     primeiro.get('codlocal_origem', ''),
                    'descr_local_origem':  primeiro.get('descr_local_origem', ''),
                    'numnota_venda':       primeiro.get('numnota_venda') or notas_av.get('nota', ''),
                    'numnota_devolucao':   primeiro.get('numnota_devolucao') or notas_av.get('nfd', ''),
                    'data_triagem':        primeiro.get('data_movimento', ''),
                    'gerado_por_etiquetas': True,
                    'qtd_etiquetas':       len(validacoes_local),
                }
    
                ids_json = json.dumps(
                    [r['id_validacao'] for r in validacoes_local],
                    ensure_ascii=False
                )
    
                row = [
                    id_lote,
                    codlocal_destino,
                    descrlocal,
                    codemp,
                    'Pendente',
                    len(validacoes_local),
                    ids_json,
                    datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
                    criado_por,
                    '', '', '', '', '', '', '',  # numnota_erp … snapshot_local_json
                    titulo or f'Etiquetas — {datetime.now().strftime("%d/%m/%Y %H:%M")}',
                    json.dumps(preview, ensure_ascii=False),
                    '[]',
                    empresa,
                ]
                ws_lotes.append_row(row, value_input_option='USER_ENTERED')
                lotes_criados_ids.append(id_lote)

            msg_parts = [f'{len(lotes_criados_ids)} lote(s) (novo(s) ou agregado(s)) com {len(validacoes_selecionadas)} etiqueta(s)']
            if ids_nao_encontrados:
                msg_parts.append(f'{len(ids_nao_encontrados)} etiqueta(s) não localizadas: {", ".join(ids_nao_encontrados[:5])}{"…" if len(ids_nao_encontrados) > 5 else ""}')
            if ids_ja_em_lote:
                msg_parts.append(f'{len(ids_ja_em_lote)} etiqueta(s) já em lote (ignoradas)')

            logger.info(f'Lote(s) por etiquetas: {", ".join(lotes_criados_ids)} | {len(validacoes_selecionadas)} itens | por {criado_por}')
            return {
                'success':              True,
                'id_lote':              lotes_criados_ids[0] if lotes_criados_ids else '',
                'qtd_itens':            len(validacoes_selecionadas),
                'ids_nao_encontrados':  ids_nao_encontrados,
                'ids_ja_em_lote':       ids_ja_em_lote,
                'msg':                  ' | '.join(msg_parts),
            }

        except Exception as e:
            logger.error(f'Erro ao gerar lote por etiquetas: {e}', exc_info=True)
            return {'success': False, 'error': str(e)}

    def estatisticas_lotes(self, codemp: str = '', dias: int = 90, empresa: str = '') -> Dict:
        """Contadores rápidos para o header da tela de lotes."""
        def _is_auto(lote):
            # Fast string check — avoids json.loads() on every lote
            pj = lote.get('preview_json') or ''
            return '"is_auto": true' in pj or '"is_auto":true' in pj

        try:
            todos     = self.listar_lotes(status='', codemp=codemp, dias=dias, empresa=empresa)
            pendentes = [l for l in todos if l.get('status') == 'Pendente']
            # Pendente = só auto-lotes (validam direto)
            # Aguardando = lotes manuais Pendente (precisam ser fechados antes)
            pendente_count  = sum(1 for l in pendentes if _is_auto(l))
            aguardando_count = sum(1 for l in pendentes if not _is_auto(l))
            return {
                'pendente':      pendente_count,
                'aguardando':    aguardando_count,
                'fechado':       sum(1 for l in todos if l.get('status') == 'Fechado'),
                'aprovado':      sum(1 for l in todos if l.get('status') == 'Aprovado'),
                'inconsistente': sum(1 for l in todos if l.get('status') == 'Inconsistente'),
                'ignorado':      sum(1 for l in todos if l.get('status') == 'Ignorado'),
                'total':         len(todos),
            }
        except Exception:
            return {'pendente': 0, 'aguardando': 0, 'fechado': 0, 'aprovado': 0, 'inconsistente': 0, 'ignorado': 0, 'total': 0}

    # ATUALIZAÇÃO DINÂMICA — origem de lotes downstream após validação

    def _atualizar_origem_lotes_relacionados(
        self,
        codlocal_confirmado: str,
        avaria_ids_confirmados: set,
        lote_id_excluir: str,
    ) -> None:
        """
        Após um lote ser validado (ex: lote 696 aprovado), atualiza todos os
        lotes PENDENTES que contenham itens das mesmas avarias para mostrar
        o local confirmado como nova origem.

        Exemplo: lote 696 validado → lotes pendentes para local 403 que
        mostravam '401 → 403' passam a mostrar '696 → 403'.

        Falha silenciosamente — nunca interrompe o fluxo de validação.
        """
        if not codlocal_confirmado or not avaria_ids_confirmados:
            return
        try:
            # Ler todos os registros de validação de uma vez (1 chamada à API)
            ws   = self._get_ws()
            rows = ws.get_all_values()
            if len(rows) < 2:
                return

            headers = rows[0]
            try:
                idx_id    = headers.index('id_validacao')
                idx_avid  = headers.index('avaria_id')
                idx_orig  = headers.index('codlocal_origem')
                idx_dorig = headers.index('descr_local_origem')
            except ValueError as e:
                logger.warning(f'_atualizar_origem: coluna não encontrada: {e}')
                return

            mapa             = self._mapa_locais()
            descr_confirmado = mapa.get(str(codlocal_confirmado), {}).get('descrlocal', '')

            # Mapear id_validacao → linha (para batch_update posterior)
            ids_candidatos: Dict[str, int] = {}  # vid → linha_sheet
            for i, row in enumerate(rows[1:], start=2):
                avaria_id  = row[idx_avid] if idx_avid < len(row) else ''
                orig_atual = row[idx_orig] if idx_orig  < len(row) else ''
                vid        = row[idx_id]   if idx_id    < len(row) else ''
                if (avaria_id in avaria_ids_confirmados
                        and str(orig_atual) != str(codlocal_confirmado)
                        and vid):
                    ids_candidatos[vid] = i

            if not ids_candidatos:
                return

            col_orig_n  = idx_orig  + 1
            col_dorig_n = idx_dorig + 1

            # 1. Atualizar TODOS os registros de validação pendentes para esta avaria
            batch_upd = []
            for vid, linha in ids_candidatos.items():
                batch_upd.extend([
                    {'range': f'{ValidacaoEstoqueService._col_num_to_letter(col_orig_n)}{linha}',
                     'values': [[str(codlocal_confirmado)]]},
                    {'range': f'{ValidacaoEstoqueService._col_num_to_letter(col_dorig_n)}{linha}',
                     'values': [[descr_confirmado]]},
                ])
            if batch_upd:
                ws.batch_update(batch_upd)

            # 2. Encontrar lotes pendentes (exceto o que acabou de ser validado)
            # que contenham esses ids de validação e atualizar seus previews
            lotes_pendentes = self.listar_lotes(status='Pendente', codemp='', dias=365)
            lotes_afetados  = []
            for lt in lotes_pendentes:
                if lt.get('id_lote') == lote_id_excluir:
                    continue
                try:
                    lt_ids = set(json.loads(lt.get('ids_validacoes', '[]') or '[]'))
                except Exception:
                    lt_ids = set()
                ids_comuns = set(ids_candidatos.keys()) & lt_ids
                if ids_comuns:
                    lotes_afetados.append({'lote': lt, 'ids_comuns': ids_comuns})

            if not lotes_afetados:
                return

            for item in lotes_afetados:
                lt = item['lote']

                # Atualizar preview_json do lote com a nova origem
                try:
                    ws_lotes  = self._get_ws_lotes()
                    hdrs_lt   = ws_lotes.row_values(1)
                    col_prev  = hdrs_lt.index('preview_json') + 1
                    pv        = json.loads(lt.get('preview_json') or '{}')
                    pv['codlocal_origem']    = str(codlocal_confirmado)
                    pv['descr_local_origem'] = descr_confirmado
                    ws_lotes.update_cell(
                        lt['_linha'], col_prev,
                        json.dumps(pv, ensure_ascii=False)
                    )
                except Exception as e_pv:
                    logger.warning(
                        f'⚠️ preview_json lote {lt["id_lote"]} não atualizado: {e_pv}'
                    )

                logger.info(
                    f'🔄 Lote downstream {lt["id_lote"]}: '
                    f'{len(item["ids_comuns"])} registro(s) → origem={codlocal_confirmado}'
                )

        except Exception as e:
            logger.warning(f'_atualizar_origem_lotes_relacionados (não-fatal): {e}')

    def _atualizar_localizacao_itens_aprovados(
        self,
        codlocal: str,
        resultados: list,
    ) -> None:
        """
        Após aprovação do lote, atualiza o campo 'localizacao' em Itens_Avarias
        para cada item cujo resultado de validação é 'Aprovado'.

        Estratégia de busca da linha em Itens_Avarias:
        1. Se id_etiqueta_destino não vazio → busca por (avaria_id, id_etiqueta_destino)
        2. Se id_etiqueta_destino vazio (triagem inicial) → busca por (avaria_id, id_etiqueta_origem)
        Falha silenciosamente — nunca interrompe o fluxo.
        """
        if not codlocal or not resultados:
            return
        avaria_ids_para_atualizar = set()
        try:
            ws = self.sheets.sheet.worksheet('Itens_Avarias')
            all_data = ws.get_all_values()
            if len(all_data) < 2:
                return

            headers = all_data[0]
            try:
                col_avid = headers.index('avaria_id')
                col_etq  = headers.index('id_etiqueta')
                col_loc  = headers.index('localizacao')
            except ValueError as e:
                logger.warning(f'_atualizar_localizacao: coluna não encontrada: {e}')
                return

            # Montar mapa (avaria_id, id_etiqueta) → linha (1-based)
            mapa_linhas: dict = {}
            for i, row in enumerate(all_data[1:], start=2):
                av_id = row[col_avid] if col_avid < len(row) else ''
                etq   = row[col_etq]  if col_etq  < len(row) else ''
                if av_id and etq:
                    mapa_linhas[(av_id, etq)] = i

            col_loc_letter = ValidacaoEstoqueService._col_num_to_letter(col_loc + 1)
            batch_upd = []
            for res in resultados:
                if res.get('resultado') not in ('Aprovado', 'Inconsistente'):
                    continue
                reg        = res.get('registro') or {}
                av_id      = reg.get('avaria_id', '')
                etq_dest   = reg.get('id_etiqueta_destino', '')
                etq_orig   = reg.get('id_etiqueta_origem', '')
                if not av_id:
                    continue
                # Triagem inicial salva id_etiqueta_destino vazio;
                # nesses casos a etiqueta em Itens_Avarias é a de origem.
                etq_busca = etq_dest if etq_dest else etq_orig
                if not etq_busca:
                    continue
                linha = mapa_linhas.get((av_id, etq_busca))
                if linha:
                    batch_upd.append({
                        'range':  f'{col_loc_letter}{linha}',
                        'values': [[str(codlocal)]],
                    })
                if av_id not in avaria_ids_para_atualizar:
                    avaria_ids_para_atualizar.add(av_id)

            if batch_upd:
                ws.batch_update(batch_upd)
                
            # Atualizar aba Avarias (coluna 'local') para refletir o novo local
            if avaria_ids_para_atualizar:
                try:
                    ws_av = self.sheets.sheet.worksheet('Avarias')
                    av_data = ws_av.get_all_values()
                    if len(av_data) >= 2:
                        av_headers = av_data[0]
                        if 'local' in av_headers and 'id' in av_headers:
                            col_local_idx = av_headers.index('local')
                            col_id_idx = av_headers.index('id')
                            
                            def _col_num_to_letter(n):
                                string = ""
                                while n > 0:
                                    n, remainder = divmod(n - 1, 26)
                                    string = chr(65 + remainder) + string
                                return string
                                
                            col_local_letter = _col_num_to_letter(col_local_idx + 1)
                            
                            av_batch_upd = []
                            for i, row in enumerate(av_data[1:], start=2):
                                r_id = row[col_id_idx] if col_id_idx < len(row) else ''
                                if r_id in avaria_ids_para_atualizar:
                                    # Formatar de volta para extrair o codlocal apenas, se houver ' - '
                                    novo_local = str(codlocal).split(' - ')[0].strip()
                                    av_batch_upd.append({
                                        'range': f'{col_local_letter}{i}',
                                        'values': [[novo_local]]
                                    })
                                    
                            if av_batch_upd:
                                ws_av.batch_update(av_batch_upd)
                                logger.info(f'local={novo_local} atualizado em {len(av_batch_upd)} registro(s) de Avarias')
                except Exception as e_av:
                    logger.warning(f'Erro ao atualizar aba Avarias: {e_av}')

            if batch_upd or avaria_ids_para_atualizar:
                self.sheets.invalidate_wms_cache()
                logger.info(
                    f'localizacao={codlocal} atualizada em {len(batch_upd)} item(ns) de Itens_Avarias'
                )

        except Exception as e:
            logger.warning(f'_atualizar_localizacao_itens_aprovados (não-fatal): {e}')

