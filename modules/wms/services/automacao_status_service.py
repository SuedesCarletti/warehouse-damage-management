"""
modules/wms/services/automacao_status_service.py
Serviço de Automações de Status — parametrizáveis por empresa.

Responsabilidades:
  - CRUD de regras na aba WMS_AUTOMACOES do Google Sheets
  - Aplicar automações quando um trigger é disparado
  - Suportar status_origem como pré-condição bloqueante (retorna erro visível)
  - Suportar múltiplas condições extras com operador AND/OR

Aba no Sheets: WMS_AUTOMACOES
Cabeçalhos (ordem exata):
  id | empresa | trigger |
  status_origem_estoque | status_origem_indenizacao |
  condicoes_extras | operador_condicoes |
  target_type | novo_status |
  descricao | ativo | criado_em | criado_por

Triggers disponíveis:
  baixa_pagamento        → baixa de pagamento registrada
  asn_recebido           → ASN recebido no CD
  asn_recusado           → ASN recusado pelo operador
  avaria_criada          → nova avaria registrada manualmente
  etiqueta_inicial_gerada → etiqueta inicial impressa com sucesso
  etiqueta_final_gerada  → etiqueta final impressa com sucesso
  item_localizado        → item triado para um local (saiu do 00)
  item_transferido       → item transferido entre locais
  triagem_iniciada       → operador inicia triagem
  triagem_finalizada     → triagem concluída
  pi_aberta              → PI de indenização aberta
  reversa_solicitada     → reversa solicitada
  processo_judicial      → enviado para ajuizamento
  validacao_estoque_aprovada → validação de estoque aprovada (delta destino OK)

Formato de condicoes_extras (JSON salvo na coluna):
  [
    {"campo": "indenizacao",    "valor": "total"},
    {"campo": "transportadora", "valor": "Jadlog"}
  ]
  Campos válidos (da avaria): status_estoque, status_indenizacao, indenizacao,
                               local, transportadora, empresa
  Campos de contexto: tipo_baixa, prefixo, codlocal, local_completo, descr_local_destino
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# Resultado da aplicação de uma automação

class ResultadoAutomacao:
    """
    Encapsula o resultado de uma automação individual.

    status:
      'aplicado'  → mudança de status realizada com sucesso
      'bloqueado' → status_origem não satisfeito — erro visível ao usuário
      'ignorado'  → condição extra não satisfeita — silencioso
      'erro'      → falha técnica ao atualizar
    """
    def __init__(self, auto_id: str, trigger: str, status: str,
                 campo: str = '', novo_status: str = '', msg: str = ''):
        self.auto_id     = auto_id
        self.trigger     = trigger
        self.status      = status   # 'aplicado' | 'bloqueado' | 'ignorado' | 'erro'
        self.campo       = campo
        self.novo_status = novo_status
        self.msg         = msg

    @property
    def bloqueado(self) -> bool:
        return self.status == 'bloqueado'

    @property
    def sucesso(self) -> bool:
        return self.status == 'aplicado'

    def to_dict(self) -> dict:
        return {
            'auto_id':    self.auto_id,
            'trigger':    self.trigger,
            'status':     self.status,
            'campo':      self.campo,
            'novo_status': self.novo_status,
            'msg':        self.msg,
            'bloqueado':  self.bloqueado,
            'sucesso':    self.sucesso,
        }


# Serviço principal

class AutomacaoStatusService:

    SHEET_NAME = 'WMS_AUTOMACOES'

    # Ordem exata das colunas na planilha
    HEADERS = [
        'id', 'empresa', 'trigger',
        'status_origem_estoque', 'status_origem_indenizacao',
        'condicoes_extras', 'operador_condicoes',
        'target_type', 'novo_status',
        'descricao', 'ativo', 'criado_em', 'criado_por',
        'prioridade',
    ]

    # Prioridade intrínseca por trigger (maior = vence conflito no mesmo campo).
    # Regras de maior prioridade sobrescrevem resultados de menor prioridade
    # quando ambas alteram o mesmo target_type na mesma chamada aplicar().
    TRIGGER_PRIORIDADE_PADRAO: Dict[str, int] = {
        'asn_recusado':   10,
        'item_recusado':  10,
        'asn_recebido':    5,
    }

    # Triggers disponíveis para exibição na UI
    TRIGGERS_DISPONIVEIS = [
        ('baixa_pagamento',        'Baixa de Pagamento',                '💰'),
        ('etiqueta_inicial_gerada','Etiqueta Inicial Gerada',           '🏷️'),
        ('etiqueta_final_gerada',  'Etiqueta Final Gerada',             '🏷️'),
        ('asn_recebido',           'ASN Recebido no CD',                '🚛'),
        ('asn_recusado',           'ASN Recusado pelo Operador',        '❌'),
        ('avaria_criada',          'Avaria Registrada (Manual)',         '📋'),
        ('item_localizado',        'Item Localizado (Triagem)',          '📦'),
        ('item_transferido',       'Item Transferido Entre Locais',      '↔️'),
        ('triagem_iniciada',       'Triagem Iniciada',                   '🔍'),
        ('triagem_finalizada',     'Triagem Finalizada',                 '✅'),
        ('pi_aberta',              'PI de Indenização Aberta',           '📄'),
        ('reversa_solicitada',     'Reversa Solicitada',                 '↩️'),
        ('processo_judicial',      'Enviado para Ajuizamento',           '⚖️'),
        ('item_recusado',          'Item Recusado (Descarte)',           '🚫'),
        ('validacao_estoque_aprovada', 'Validação de Estoque Aprovada',  '✅'),
    ]
    TARGET_TYPES_ESPECIAIS = ['finalizacao']  

    # Campos da avaria que podem ser usados em condições extras
    CAMPOS_VALIDOS = [
        'status_estoque', 'status_indenizacao', 'indenizacao',
        'local', 'transportadora', 'empresa', 'cod_empresa',
        # Campos de contexto (passados pelo chamador)
        'tipo_baixa', 'prefixo', 'codlocal', 'local_completo', 'descr_local_destino',
    ]

    def __init__(self, sheets_service, wms_avaria_service=None, config_service=None):
        self.sheets = sheets_service
        self.wms    = wms_avaria_service
        self.config = config_service
        self._ensure_sheet()
        logger.info("AutomacaoStatusService inicializado")

    # SETUP — garante que a aba existe com o cabeçalho correto

    def _ensure_sheet(self):
        """Cria a aba WMS_AUTOMACOES se não existir, e adiciona colunas novas."""
        try:
            ws = self._worksheet(create=True)
            existing = ws.row_values(1)

            if not existing:
                ws.append_row(self.HEADERS)
                logger.info(f"Aba {self.SHEET_NAME} criada com cabeçalhos")
                return

            # Adicionar colunas novas sem apagar as existentes
            missing = [h for h in self.HEADERS if h not in existing]
            if missing:
                for col_name in missing:
                    existing.append(col_name)
                ws.update('1:1', [existing])
                logger.info(f"Colunas adicionadas em {self.SHEET_NAME}: {missing}")

        except Exception as e:
            logger.error(f"_ensure_sheet: {e}")

    def _worksheet(self, create=False):
        """Retorna worksheet, criando se solicitado."""
        try:
            return self.sheets.sheet.worksheet(self.SHEET_NAME)
        except Exception:
            if create:
                return self.sheets.sheet.add_worksheet(
                    title=self.SHEET_NAME, rows=500, cols=len(self.HEADERS)
                )
            raise

    # HELPERS

    @staticmethod
    def _gen_id() -> str:
        return f"AUTO-{uuid.uuid4().hex[:8].upper()}"

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    @staticmethod
    def _parse_condicoes(raw: str) -> List[Dict]:
        """Deserializa condicoes_extras do JSON salvo na planilha."""
        if not raw or not raw.strip():
            return []
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, ValueError):
            pass
        return []

    @staticmethod
    def _serialize_condicoes(lista: List[Dict]) -> str:
        """Serializa condições para salvar na planilha."""
        if not lista:
            return ''
        return json.dumps(lista, ensure_ascii=False)

    def _row_to_dict(self, headers: list, row: list) -> dict:
        """Converte linha do Sheets para dicionário."""
        d = {}
        for i, h in enumerate(headers):
            d[h] = str(row[i]).strip() if i < len(row) else ''
        # Deserializar JSON de condições
        d['condicoes_extras'] = self._parse_condicoes(d.get('condicoes_extras', ''))
        return d

    # LEITURA

    def get_all(self, empresa: str = '', apenas_ativas: bool = False) -> List[dict]:
        """Retorna todas as automações, opcionalmente filtrando por empresa e ativo."""
        try:
            ws = self._worksheet()
            all_values = ws.get_all_values()
            if not all_values or len(all_values) < 2:
                return []

            headers = all_values[0]
            resultado = []
            for row in all_values[1:]:
                if not any(str(v).strip() for v in row):
                    continue
                rec = self._row_to_dict(headers, row)
                if not rec.get('id'):
                    continue
                if empresa and rec.get('empresa') and rec.get('empresa').lower() != empresa.lower():
                    continue
                if apenas_ativas and rec.get('ativo', 'sim') != 'sim':
                    continue
                resultado.append(rec)
            return resultado
        except Exception as e:
            logger.error(f"get_all: {e}")
            return []

    def get_by_id(self, auto_id: str) -> Optional[dict]:
        """Busca automação por ID."""
        for rec in self.get_all():
            if rec.get('id') == auto_id:
                return rec
        return None

    # ESCRITA — CRUD

    def criar(self, empresa: str, dados: dict, criado_por: str = 'sistema') -> Tuple[bool, str]:
        """
        Cria nova automação.

        dados deve conter: trigger, target_type, novo_status
        Opcionais: status_origem_estoque, status_origem_indenizacao,
                   condicoes_extras (list), operador_condicoes, descricao
        """
        try:
            trigger     = dados.get('trigger', '').strip()
            target_type = dados.get('target_type', '').strip()
            novo_status = dados.get('novo_status', '').strip()

            if not trigger or not target_type or not novo_status:
                return False, 'trigger, target_type e novo_status são obrigatórios'

            auto_id = self._gen_id()
            condicoes_raw = self._serialize_condicoes(
                dados.get('condicoes_extras', [])
            )

            prioridade_default = self.TRIGGER_PRIORIDADE_PADRAO.get(trigger, 0)
            row = [
                auto_id,
                empresa,
                trigger,
                dados.get('status_origem_estoque', ''),
                dados.get('status_origem_indenizacao', ''),
                condicoes_raw,
                dados.get('operador_condicoes', 'AND').upper(),
                target_type,
                novo_status,
                dados.get('descricao', ''),
                'sim',
                self._now(),
                criado_por,
                str(dados.get('prioridade', prioridade_default)),
            ]
            ws = self._worksheet()
            ws.append_row(row)
            logger.info(f"Automação {auto_id} criada: {trigger} → {target_type}/{novo_status}")
            return True, f"Automação {auto_id} criada com sucesso"
        except Exception as e:
            logger.error(f"criar: {e}")
            return False, str(e)

    def atualizar(self, auto_id: str, dados: dict) -> Tuple[bool, str]:
        """Atualiza campos de uma automação existente."""
        try:
            ws = self._worksheet()
            all_values = ws.get_all_values()
            if not all_values:
                return False, 'Planilha vazia'

            headers = all_values[0]

            # Mapeamento campo → índice (1-based)
            col_map = {h: headers.index(h) + 1 for h in headers if h in self.HEADERS}

            for row_idx, row in enumerate(all_values[1:], start=2):
                if row and str(row[0]).strip() == auto_id:
                    for campo, valor in dados.items():
                        if campo not in col_map:
                            continue
                        # Serializar condições se necessário
                        if campo == 'condicoes_extras' and isinstance(valor, list):
                            valor = self._serialize_condicoes(valor)
                        elif campo == 'operador_condicoes' and valor:
                            valor = str(valor).upper()
                        ws.update_cell(row_idx, col_map[campo], valor)
                    logger.info(f"Automação {auto_id} atualizada")
                    return True, f"Automação {auto_id} atualizada"

            return False, f"Automação {auto_id} não encontrada"
        except Exception as e:
            logger.error(f"atualizar({auto_id}): {e}")
            return False, str(e)

    def toggle_ativo(self, auto_id: str) -> Tuple[bool, str, str]:
        """Alterna ativo/inativo. Retorna (success, msg, novo_valor)."""
        try:
            atual = self.get_by_id(auto_id)
            if not atual:
                return False, 'Automação não encontrada', ''
            novo = 'nao' if atual.get('ativo') == 'sim' else 'sim'
            success, msg = self.atualizar(auto_id, {'ativo': novo})
            return success, msg, novo
        except Exception as e:
            return False, str(e), ''

    def excluir(self, auto_id: str) -> Tuple[bool, str]:
        """Remove uma automação da planilha."""
        try:
            ws = self._worksheet()
            all_values = ws.get_all_values()
            for row_idx, row in enumerate(all_values[1:], start=2):
                if row and str(row[0]).strip() == auto_id:
                    ws.delete_rows(row_idx)
                    return True, f"Automação {auto_id} removida"
            return False, f"Automação {auto_id} não encontrada"
        except Exception as e:
            logger.error(f"excluir({auto_id}): {e}")
            return False, str(e)

    # AVALIAÇÃO DE CONDIÇÕES

    # Campos de localização que refletem a operação atual — contexto tem
    # prioridade sobre o dado (potencialmente defasado) da avaria.
    _CAMPOS_CONTEXTO_PRIORITARIO = {'local', 'codlocal', 'codlocal_destino', 'codlocaldest', 'cod_triagem', 'localizacao'}

    # Mapeamento de sinônimos para campos comuns
    _SINONIMOS = {
        'local':         ['codlocal', 'cod_triagem', 'localizacao', 'local_item', 'codlocalorig'],
        'codlocal':      ['local', 'cod_triagem', 'localizacao', 'local_item', 'codlocalorig'],
        'cod_triagem':   ['local', 'codlocal', 'localizacao', 'local_item', 'codlocalorig'],
        'localizacao':   ['local', 'codlocal', 'cod_triagem', 'local_item', 'codlocalorig'],
        'codlocal_destino': ['codlocaldest', 'local_destino', 'destino'],
        'codlocaldest':     ['codlocal_destino', 'local_destino', 'destino'],
        'empresa':       ['cod_empresa', 'nome_empresa'],
        'cod_empresa':   ['empresa'],
        'transportadora': ['transp', 'nome_transp'],
    }

    def _valor_campo(self, campo: str, avaria_obj, contexto: dict) -> str:
        """
        Retorna o valor de um campo buscando em duas fontes principais:
          1. Contexto (operação atual)
          2. Atributos do objeto Avaria (dados persistidos)

        Suporta sinônimos (ex: se pede 'local', busca também 'codlocal').
        """
        campo_lower = campo.lower()
        
        def _normalize(v):
            if v is None: return ''
            s = str(v).strip()
            # Remover .0 de valores numéricos vindos do Sheets (ex: "403.0" -> "403")
            if s.endswith('.0'): s = s[:-2]
            return s.lower()

        # 1. Tentar contexto primeiro para campos de localização (prioridade operacional)
        if campo_lower in self._CAMPOS_CONTEXTO_PRIORITARIO:
            # Tenta o campo exato
            v = _normalize(contexto.get(campo_lower, '') or contexto.get(campo, ''))
            if v: return v
            
            # Tenta sinônimos no contexto
            for sin in self._SINONIMOS.get(campo_lower, []):
                v = _normalize(contexto.get(sin, ''))
                if v: return v

        # 2. Tentar no objeto Avaria
        valor = ''
        if avaria_obj:
            # Tenta o campo exato (atributo do objeto ou chave do dict)
            if hasattr(avaria_obj, campo_lower):
                valor = _normalize(getattr(avaria_obj, campo_lower, ''))
            elif hasattr(avaria_obj, campo):
                valor = _normalize(getattr(avaria_obj, campo, ''))
            elif isinstance(avaria_obj, dict):
                valor = _normalize(avaria_obj.get(campo_lower) or avaria_obj.get(campo) or '')

            # Se vazio, tenta sinônimos no objeto
            if not valor:
                for sin in self._SINONIMOS.get(campo_lower, []):
                    if hasattr(avaria_obj, sin):
                        valor = _normalize(getattr(avaria_obj, sin, ''))
                        if valor: break
                    elif isinstance(avaria_obj, dict) and avaria_obj.get(sin):
                        valor = _normalize(avaria_obj.get(sin))
                        if valor: break

        # 3. Fallback: Contexto para demais campos ou se avaria não tinha o valor
        if not valor:
            valor = _normalize(contexto.get(campo_lower, '') or contexto.get(campo, ''))
            if not valor:
                for sin in self._SINONIMOS.get(campo_lower, []):
                    valor = _normalize(contexto.get(sin, ''))
                    if valor: break

        return valor

    def _avaliar_status_origem(
        self, regra: dict, avaria_obj
    ) -> Tuple[bool, str]:
        """
        Verifica se a avaria está no status de origem exigido pela regra.

        Retorna (passou, msg_erro).
        Se a regra não exige status de origem, retorna (True, '').
        """
        erros = []

        orig_estoque = str(regra.get('status_origem_estoque', '')).strip()
        orig_indeniz = str(regra.get('status_origem_indenizacao', '')).strip()

        # Ignorar valores falsos vindos de planilhas (ex: células vazias lidas como 0)
        if orig_estoque.lower() in ('0', 'none', 'null', 'false'):
            orig_estoque = ''
        if orig_indeniz.lower() in ('0', 'none', 'null', 'false'):
            orig_indeniz = ''

        if orig_estoque and avaria_obj:
            atual = str(getattr(avaria_obj, 'status_estoque', '') or '').strip()
            if atual.lower() != orig_estoque.lower():
                erros.append(
                    f"Status de Estoque deve ser '{orig_estoque}' "
                    f"(atual: '{atual}')"
                )

        if orig_indeniz and avaria_obj:
            atual = str(getattr(avaria_obj, 'status_indenizacao', '') or '').strip()
            if atual.lower() != orig_indeniz.lower():
                erros.append(
                    f"Status de Indenização deve ser '{orig_indeniz}' "
                    f"(atual: '{atual}')"
                )

        if erros:
            return False, ' | '.join(erros)
        return True, ''

    def _avaliar_condicoes_extras(
        self, regra: dict, avaria_obj, contexto: dict
    ) -> Tuple[bool, str]:
        """
        Avalia a lista de condições extras com operador AND ou OR.

        Retorna (passou, motivo_falha).
        Se não há condições extras, retorna (True, '').
        """
        condicoes = regra.get('condicoes_extras', [])
        if not condicoes:
            return True, ''

        operador = str(regra.get('operador_condicoes', 'AND')).upper()
        resultados = []

        for cond in condicoes:
            campo = str(cond.get('campo', '')).strip()
            valor_esperado = str(cond.get('valor', '')).strip().lower()

            if not campo or not valor_esperado:
                continue

            valor_real = self._valor_campo(campo, avaria_obj, contexto)
            passou = (valor_real == valor_esperado)
            resultados.append((passou, campo, valor_esperado, valor_real))

        if not resultados:
            return True, ''

        if operador == 'AND':
            falhas = [(c, e, r) for ok, c, e, r in resultados if not ok]
            if falhas:
                detalhes = ', '.join(f"{c}={e!r} (atual: {r!r})" for c, e, r in falhas)
                return False, f"Condições não satisfeitas: {detalhes}"
            return True, ''
        else:  # OR
            if any(ok for ok, *_ in resultados):
                return True, ''
            detalhes = ', '.join(
                f"{c}={e!r} (atual: {r!r})" for _, c, e, r in resultados
            )
            return False, f"Nenhuma condição satisfeita: {detalhes}"

    # APLICAÇÃO — ponto de entrada principal

    def aplicar(
        self,
        trigger: str,
        avaria_id: str,
        empresa: str,
        contexto: Optional[Dict[str, Any]] = None,
        operador_email: str = 'automacao',
    ) -> List[ResultadoAutomacao]:
        """
        Aplica todas as automações ativas correspondentes ao trigger.

        Fluxo por regra:
          1. Filtra por trigger e ordena por prioridade (maior primeiro)
          2. Verifica status_origem (bloqueante — retorna erro visível)
          3. Avalia condicoes_extras (ignorante — pula silenciosamente)
          4. Aplica mudança de status, respeitando prioridade por campo:
             se um campo já foi alterado por regra de maior prioridade,
             regras de menor prioridade para o mesmo campo são ignoradas.

        Prioridade: campo 'prioridade' da regra (int). Fallback para
        TRIGGER_PRIORIDADE_PADRAO quando ausente na regra.
        Maior número = maior peso. Recusa (10) > Recebimento (5) > padrão (0).

        Retorna lista de ResultadoAutomacao.
        Chamadores devem checar .bloqueado para exibir erro ao usuário.
        """
        if not self.wms:
            logger.warning("aplicar(): wms_service não disponível, automações ignoradas")
            return []

        contexto = contexto or {}

        # Buscar automações ativas para o trigger
        regras = self.get_all(empresa=empresa, apenas_ativas=True)
        regras_trigger = [r for r in regras if r.get('trigger') == trigger]

        # Compatibilidade: triggers legados de baixa
        if trigger == 'baixa_pagamento':
            regras_trigger += [
                r for r in regras
                if r.get('trigger') in ('baixa_total', 'baixa_parcial')
                and r.get('ativo') == 'sim'
            ]

        if not regras_trigger:
            logger.debug(f"ℹNenhuma automação ativa para trigger '{trigger}' (empresa='{empresa}')")
            return []

        # Override priorities from config if available
        prioridades_config = {}
        if self.config and empresa:
            try:
                prioridades_config = self.config.get_automacao_prioridades(empresa)
            except Exception:
                pass

        # Ordenar por prioridade decrescente (maior peso aplicado primeiro)
        prioridade_trigger = prioridades_config.get(trigger, self.TRIGGER_PRIORIDADE_PADRAO.get(trigger, 0))

        def _prioridade_regra(r: dict) -> int:
            raw = r.get('prioridade', '')
            trigger_r = r.get('trigger', trigger)
            fallback = prioridades_config.get(trigger_r, self.TRIGGER_PRIORIDADE_PADRAO.get(trigger_r, 0))
            try:
                return int(raw) if raw != '' else fallback
            except (ValueError, TypeError):
                return fallback

        regras_trigger.sort(key=_prioridade_regra, reverse=True)

        # Carregar avaria UMA VEZ para todas as regras
        avaria_obj = None
        try:
            avaria_obj = self.wms.get_avaria_by_id(avaria_id)
        except Exception as e:
            logger.warning(f"Não foi possível carregar avaria {avaria_id}: {e}")

        resultados: List[ResultadoAutomacao] = []
        updates_acumulados: Dict[str, Any] = {}
        fila_resultados: List[ResultadoAutomacao] = []
        # Rastreia campos já alterados com a prioridade aplicada (campo → prioridade usada)
        campos_aplicados: Dict[str, int] = {}

        for regra in regras_trigger:
            regra_id     = regra.get('id', '?')
            prio_regra   = _prioridade_regra(regra)

            # ── 1. Verificar status_origem (BLOQUEANTE) ──────────────────
            passou_origem, msg_origem = self._avaliar_status_origem(regra, avaria_obj)
            if not passou_origem:
                msg = (
                    f"Esta avaria não pode avançar: {msg_origem}. "
                    f"Verifique o status antes de prosseguir."
                )
                logger.info(f"Regra {regra_id} bloqueada para {avaria_id}: {msg_origem}")
                resultados.append(ResultadoAutomacao(
                    auto_id=regra_id, trigger=trigger,
                    status='bloqueado', msg=msg,
                ))
                continue

            # ── 2. Avaliar condições extras (IGNORANTE) ──────────────────
            passou_extras, msg_extras = self._avaliar_condicoes_extras(
                regra, avaria_obj, contexto
            )
            if not passou_extras:
                logger.info(f"↩Regra {regra_id} ignorada (condições não satisfeitas): {msg_extras}")
                resultados.append(ResultadoAutomacao(
                    auto_id=regra_id, trigger=trigger,
                    status='ignorado', msg=msg_extras,
                ))
                continue

            # ── 3. Aplicar mudança de status ─────────────────────────────
            target_type = regra.get('target_type', '').strip()
            novo_status = regra.get('novo_status', '').strip()

            if target_type == 'finalizacao':
                # ── Marcar processo como finalizado ──────────────────────
                campo_fin = 'data_finalizacao'
                if campos_aplicados.get(campo_fin, -1) > prio_regra:
                    logger.debug(
                        f"↩️ Regra {regra_id} ignorada por prioridade: campo '{campo_fin}' "
                        f"já aplicado com prioridade {campos_aplicados[campo_fin]}"
                    )
                    resultados.append(ResultadoAutomacao(
                        auto_id=regra_id, trigger=trigger,
                        status='ignorado',
                        msg=f"Campo '{campo_fin}' já definido por regra de maior prioridade",
                    ))
                    continue
                from datetime import datetime as _dt
                _now_str = _dt.now().strftime('%d/%m/%Y %H:%M:%S')
                campos_aplicados[campo_fin] = prio_regra
                updates_acumulados['data_finalizacao'] = _now_str
                updates_acumulados['finalizado_por'] = operador_email
                res_fin = ResultadoAutomacao(
                    auto_id=regra_id, trigger=trigger, status='aplicado',
                    campo=campo_fin, novo_status='finalizado',
                )
                resultados.append(res_fin)
                fila_resultados.append(res_fin)
                continue

            if target_type not in ('estoque', 'indenizacao'):
                logger.warning(f"target_type inválido na regra {regra_id}: '{target_type}'")
                continue

            campo_status = f"status_{target_type}"

            # ── Prioridade: ignorar se campo já foi definido com maior peso ──
            if campos_aplicados.get(campo_status, -1) > prio_regra:
                logger.info(
                    f"↩️ Regra {regra_id} ignorada por prioridade: campo '{campo_status}' "
                    f"já aplicado (prioridade {campos_aplicados[campo_status]} > {prio_regra})"
                )
                resultados.append(ResultadoAutomacao(
                    auto_id=regra_id, trigger=trigger,
                    status='ignorado',
                    msg=f"Campo '{campo_status}' já definido por regra de maior prioridade",
                ))
                continue

            campos_aplicados[campo_status] = prio_regra
            updates_acumulados[campo_status] = novo_status
            res_st = ResultadoAutomacao(
                auto_id=regra_id, trigger=trigger, status='aplicado',
                campo=campo_status, novo_status=novo_status,
            )
            resultados.append(res_st)
            fila_resultados.append(res_st)
            logger.info(
                f"🤖 Automação {regra_id}: {avaria_id}.{campo_status} → '{novo_status}' "
                f"(prioridade={prio_regra})"
            )

        # ── Gravação única: todas as regras aplicadas viram UM update_avaria ──
        if updates_acumulados:
            try:
                success, msg = self.wms.update_avaria(
                    avaria_id, alterado_por=operador_email, **updates_acumulados
                )
            except Exception as e:
                logger.error(f"Aplicar automações em {avaria_id}: {e}")
                success, msg = False, str(e)
            for res in fila_resultados:
                res.status = 'aplicado' if success else 'erro'
                res.msg = msg
            if not success:
                logger.warning(f"Automações de {avaria_id} falharam: {msg}")

        return resultados

    def extrair_erros_visiveis(self, resultados: List[ResultadoAutomacao]) -> List[str]:
        """
        Retorna lista de mensagens de erro para exibir ao usuário.
        Apenas resultados com status='bloqueado'.
        """
        return [r.msg for r in resultados if r.bloqueado]

    # HELPERS PARA A UI

    def get_triggers_info(self) -> List[dict]:
        """Retorna lista de triggers com metadados para dropdowns, incluindo prioridade padrão."""
        return [
            {
                'value':             v,
                'label':             l,
                'icon':              i,
                'prioridade_padrao': self.TRIGGER_PRIORIDADE_PADRAO.get(v, 0),
            }
            for v, l, i in self.TRIGGERS_DISPONIVEIS
        ]

    def get_campos_validos(self) -> List[str]:
        """Retorna campos disponíveis para condições extras."""
        return self.CAMPOS_VALIDOS
