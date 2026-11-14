"""
modules/wms/services/asn_service.py
Serviço de ASN (Advanced Shipping Notice) — Portal de Transportadoras.

Responsabilidades:
  - CRUD de ASNs na aba WMS_ASN do Google Sheets
  - Geração de IDs sequenciais (ASN-YYYYMMDD-NNNN)
  - Geração de token UUID para auditoria
  - Envio de email de confirmação para a transportadora
  - Envio de notificação aos gestores WMS
  - Criação automática de avaria com dados pré-populados ao receber
  - Geração automática de etiqueta inicial (prefixo 00-)

Aba no Sheets: WMS_ASN
Cabeçalhos:
  id | token | transportadora | email_transportadora | empresa_dest |
  nota | tipo_nota | cte | volumes | descricao_avaria |
  data_prevista | nome_motorista | placa_veiculo |
  status | avaria_id | recebido_por | recebido_em |
  criado_em | criado_por | observacao_operador
"""

import uuid
import logging
import traceback
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from ..models.asn import ASN
from .config_service import WmsConfigService
from core.services.tenant_scope import empresa_visivel

logger = logging.getLogger(__name__)

NOMES_DIAS_SEMANA = {
    0: 'segunda-feira', 1: 'terça-feira', 2: 'quarta-feira',
    3: 'quinta-feira', 4: 'sexta-feira', 5: 'sábado', 6: 'domingo',
}


class AsnService:

    SHEET_NAME = 'WMS_ASN'

    # Janela de atendimento padrão (usada quando a empresa não tem config própria
    # em WMS_Config): terça(1), quarta(2), quinta(3) — slots de 40 min, 14h-17h.
    # dt.weekday(): segunda=0 ... domingo=6
    DIAS_ATENDIMENTO_PADRAO = (1, 2, 3)
    HORA_INICIO_PADRAO = '14:00'
    HORA_FIM_PADRAO = '17:00'
    INTERVALO_MIN_PADRAO = 40

    # Chaves em WMS_Config (via WmsConfigService) onde a configuração editável
    # pelo operador é persistida, por empresa.
    CONFIG_KEY_DIAS      = 'asn_dias_atendimento'
    CONFIG_KEY_HORA_INICIO = 'asn_hora_inicio'
    CONFIG_KEY_HORA_FIM    = 'asn_hora_fim'
    CONFIG_KEY_INTERVALO   = 'asn_intervalo_min'

    HEADERS = [
        'id', 'token', 'transportadora', 'email_transportadora', 'empresa_dest',
        'nota', 'tipo_nota', 'cte', 'volumes', 'descricao_avaria',
        'data_prevista', 'nome_motorista', 'placa_veiculo',
        'status', 'avaria_id',
        'recebido_por', 'recebido_em',
        'recusado_por', 'recusado_em', 'motivo_recusa',
        'criado_em', 'criado_por', 'observacao_operador',
        'notas_adicionais', 'cod_empresa', 'link_fotos_drive',
        'notas_recebidas_parcial',
    ]

    def __init__(self, sheets_service, email_service=None, wms_service=None,
                 base_url: str = '', automacao_service=None, sankhya_avaria_service=None,
                 etiqueta_service=None, config_service=None):
        self.sheets = sheets_service
        self.email = email_service
        self.wms = wms_service
        self.automacao = automacao_service
        self.sankhya_avaria = sankhya_avaria_service
        self.etiqueta = etiqueta_service
        self.config = config_service or WmsConfigService(sheets_service)
        self.base_url = base_url.rstrip('/')
        self._ensure_sheet()
        logger.info("AsnService inicializado")

    # SETUP

    def _ensure_sheet(self):
        """Garante que a aba WMS_ASN existe e tem todos os cabeçalhos esperados."""
        if not self.sheets or not self.sheets.connected:
            return
        try:
            ws = self.sheets.sheet.worksheet(self.SHEET_NAME)
            existing = ws.row_values(1)
            for col in self.HEADERS:
                if col not in existing:
                    new_col_idx = len(existing) + 1
                    # Expande o grid se necessário antes de escrever
                    if new_col_idx > ws.col_count:
                        ws.resize(rows=ws.row_count, cols=new_col_idx + 4)
                    ws.update_cell(1, new_col_idx, col)
                    existing.append(col)
                    logger.info(f"Coluna '{col}' adicionada à aba {self.SHEET_NAME}")
        except Exception:
            try:
                ws = self.sheets.sheet.add_worksheet(
                    title=self.SHEET_NAME, rows=5000, cols=len(self.HEADERS)
                )
                ws.update('A1', [self.HEADERS])
                logger.info(f"Aba '{self.SHEET_NAME}' criada")
            except Exception as e:
                logger.error(f"Erro ao criar aba {self.SHEET_NAME}: {e}")

    def _worksheet(self):
        if not self.sheets or not self.sheets.connected:
            return None
        try:
            return self.sheets.sheet.worksheet(self.SHEET_NAME)
        except Exception as e:
            logger.error(f"Aba {self.SHEET_NAME} não encontrada: {e}")
            return None

    # GERAÇÃO DE ID

    def _gerar_id(self) -> str:
        """
        Gera ID sequencial no formato ASN-YYYYMMDD-NNNN.
        Busca o maior número do dia atual e incrementa.
        """
        hoje = datetime.now().strftime('%Y%m%d')
        prefixo = f'ASN-{hoje}-'

        ws = self._worksheet()
        if not ws:
            return f'{prefixo}0001'

        try:
            all_ids = ws.col_values(1)[1:]  # Coluna A, pula cabeçalho
            ids_hoje = [v for v in all_ids if v.startswith(prefixo)]

            if not ids_hoje:
                return f'{prefixo}0001'

            maior = max(int(v.replace(prefixo, '')) for v in ids_hoje if v.replace(prefixo, '').isdigit())
            return f'{prefixo}{str(maior + 1).zfill(4)}'
        except Exception:
            return f'{prefixo}0001'

    def _now(self) -> str:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # LEITURA

    def _read_all(self) -> List[dict]:
        ws = self._worksheet()
        if not ws:
            return []
        try:
            all_values = ws.get_all_values()
            if not all_values or len(all_values) < 2:
                return []
            headers = all_values[0]
            rows = []
            for row in all_values[1:]:
                if not any(str(v).strip() for v in row):
                    continue
                # Em caso de cabeçalho duplicado, mantém a 1ª ocorrência — mesma
                # convenção usada nas escritas (headers.index(campo), que também
                # resolve para a 1ª ocorrência). Isso evita que leitura e escrita
                # apontem para colunas diferentes quando há um header repetido.
                record = {}
                for i, h in enumerate(headers):
                    if h in record:
                        continue
                    record[h] = str(row[i]).strip() if i < len(row) else ''
                rows.append(record)
            return rows
        except Exception as e:
            logger.error(f"_read_all ASN: {e}")
            return []

    @staticmethod
    def _gravar_campos_asn(ws, linha: int, campos: dict) -> None:
        """Grava vários campos de uma linha do ASN numa única chamada. campos: {coluna(1-based): valor}."""
        from gspread.utils import rowcol_to_a1
        ws.batch_update(
            [{'range': rowcol_to_a1(linha, col), 'values': [[val]]} for col, val in campos.items()],
            value_input_option='USER_ENTERED',
        )

    def _get_by_id_com_linha(self, asn_id: str):
        """
        Como get_by_id, mas devolve também (linha_na_planilha, ws, all_values) a partir de
        UMA única leitura — evita reler a aba do ASN para gravar o recebimento.
        Retorna (asn|None, linha|None, ws|None, all_values).
        """
        ws = self._worksheet()
        if not ws:
            return None, None, None, []
        try:
            all_values = ws.get_all_values()
        except Exception as e:
            logger.error(f"_get_by_id_com_linha ASN: {e}")
            return None, None, ws, []
        if not all_values or len(all_values) < 2:
            return None, None, ws, all_values or []
        headers = all_values[0]
        for row_idx, row in enumerate(all_values[1:], start=2):
            if not any(str(v).strip() for v in row):
                continue
            record = {}
            for i, h in enumerate(headers):
                if h in record:
                    continue
                record[h] = str(row[i]).strip() if i < len(row) else ''
            if str(record.get('id', '')).strip() == asn_id:
                return ASN.from_dict(record), row_idx, ws, all_values
        return None, None, ws, all_values

    def get_all(self, empresa: str = '', status: str = '') -> List[ASN]:
        """
        Retorna todos os ASNs, opcionalmente filtrados por empresa e/ou status.
        Ordenados por data de criação decrescente (mais recentes primeiro).
        """
        rows = self._read_all()
        asns = []
        for row in rows:
            if not empresa_visivel(row.get('empresa_dest', ''), empresa, blank_visible_to_all=False):
                continue
            if status and row.get('status', '') != status:
                continue
            try:
                asns.append(ASN.from_dict(row))
            except Exception as e:
                logger.warning(f"Erro ao parsear ASN: {e}")
        asns.sort(key=lambda a: a.criado_em, reverse=True)
        return asns

    def get_by_id(self, asn_id: str) -> Optional[ASN]:
        """Busca um ASN pelo ID."""
        for row in self._read_all():
            if str(row.get('id', '')).strip() == asn_id:
                return ASN.from_dict(row)
        return None

    def get_by_transportadora(self, email: str) -> List[ASN]:
        """Retorna ASNs criados por uma transportadora específica."""
        rows = self._read_all()
        asns = []
        for row in rows:
            if str(row.get('email_transportadora', '')).strip().lower() == email.lower():
                try:
                    asns.append(ASN.from_dict(row))
                except Exception:
                    pass
        asns.sort(key=lambda a: a.criado_em, reverse=True)
        return asns

    def get_agendados(self, empresa: str = '') -> List[ASN]:
        """Retorna ASNs pendentes (agendado ou processado) — para a fila do operador."""
        rows = self._read_all()
        result = []
        for row in rows:
            if not empresa_visivel(row.get('empresa_dest', ''), empresa, blank_visible_to_all=False):
                continue
            if row.get('status', '') in ('agendado', 'processado', 'recebido_parcial'):
                try:
                    result.append(ASN.from_dict(row))
                except Exception:
                    pass
        result.sort(key=lambda a: a.data_prevista)
        return result

    def get_slots_ocupados(self, data: str, empresa: str = '', excluir_asn_id: str = '') -> List[str]:
        """
        Retorna lista de slots de horário já reservados (agendado ou processado)
        para uma data específica no formato YYYY-MM-DD.

        excluir_asn_id: se informado, ignora o próprio ASN na contagem — usado ao
        reabrir o slot atual de um agendamento que está sendo editado.
        """
        STATUSES_ATIVOS = {'agendado', 'processado', 'recebido_parcial'}
        rows = self._read_all()
        ocupados = []
        for row in rows:
            if excluir_asn_id and str(row.get('id', '')).strip() == str(excluir_asn_id).strip():
                continue
            dp     = str(row.get('data_prevista', '')).strip()
            status = str(row.get('status', '')).strip()
            emp    = str(row.get('empresa_dest', '')).strip()
            if status not in STATUSES_ATIVOS:
                continue
            if not empresa_visivel(emp, empresa, blank_visible_to_all=False):
                continue
            if dp.startswith(data) and len(dp) >= 16:
                horario = dp[11:16]
                if horario:
                    ocupados.append(horario)
        return ocupados

    def _validar_data_prevista(self, data_prevista: str, empresa_dest: str, excluir_asn_id: str = '') -> Tuple[bool, str]:
        """
        Valida dia da semana, horário (slots configuráveis) e disponibilidade
        do slot para uma data_prevista "YYYY-MM-DD HH:MM", de acordo com a
        configuração de atendimento vigente para empresa_dest (ver
        get_config_atendimento).
        """
        try:
            dt = datetime.strptime(data_prevista[:10], '%Y-%m-%d')
        except ValueError:
            return False, "Data inválida"

        cfg = self.get_config_atendimento(empresa_dest)

        if dt.weekday() not in cfg['dias_permitidos']:
            dias_nomes = ', '.join(NOMES_DIAS_SEMANA[d] for d in sorted(cfg['dias_permitidos']))
            return False, f"Agendamentos permitidos apenas: {dias_nomes}"

        horario_str = data_prevista[11:16] if len(data_prevista) >= 16 else ''
        if horario_str not in cfg['slots']:
            return False, f"Horário inválido. Selecione um dos horários disponíveis: {', '.join(cfg['slots'])}"

        slots_ocupados = self.get_slots_ocupados(data_prevista[:10], empresa=empresa_dest, excluir_asn_id=excluir_asn_id)
        if horario_str in slots_ocupados:
            return False, f"O horário {horario_str} já está reservado para este dia. Escolha outro horário disponível."

        return True, ""

    @staticmethod
    def _gerar_slots(hora_inicio: str, hora_fim: str, intervalo_min: int) -> List[str]:
        """Gera a lista de horários (slots) entre hora_inicio e hora_fim, em passos de intervalo_min."""
        try:
            inicio = datetime.strptime(hora_inicio, '%H:%M')
            fim    = datetime.strptime(hora_fim, '%H:%M')
            intervalo_min = int(intervalo_min)
        except (ValueError, TypeError):
            return []
        if intervalo_min <= 0 or fim <= inicio:
            return []

        slots = []
        atual = inicio
        while atual < fim:
            slots.append(atual.strftime('%H:%M'))
            atual += timedelta(minutes=intervalo_min)
        return slots

    def get_config_atendimento(self, empresa: str) -> dict:
        """
        Retorna a configuração de dias/horários de atendimento para agendamento
        de ASN, editável pelo operador via WMS_Config (chaves CONFIG_KEY_*).
        Cai nos valores padrão (DIAS/HORA/INTERVALO_*_PADRAO) quando a empresa
        não tem configuração própria nem global ('*').

        Returns:
            {
              'dias_permitidos': [1, 2, 3],       # dt.weekday(): segunda=0 ... domingo=6
              'dias_permitidos_js': [2, 3, 4],    # Date.getDay(): domingo=0 ... sábado=6
              'hora_inicio': '14:00',
              'hora_fim': '17:00',
              'intervalo_min': 40,
              'slots': ['14:00', '14:40', ...],
            }
        """
        # force_refresh=True: cada worker gunicorn mantém seu próprio cache de
        # WMS_Config (10 min) e a invalidação de set_config_atendimento só
        # alcança o processo que atendeu a escrita — sem isso, outro worker
        # continuaria servindo a janela antiga por até 10 min após a troca.
        cfg = self.config.get_all_configs(empresa, force_refresh=True) if self.config else {}

        dias_raw = str(cfg.get(self.CONFIG_KEY_DIAS, '') or '').strip()
        dias: List[int] = []
        if dias_raw:
            try:
                dias = sorted({int(d) for d in dias_raw.split(',') if d.strip() != ''})
            except ValueError:
                dias = []
        if not dias or any(d < 0 or d > 6 for d in dias):
            dias = list(self.DIAS_ATENDIMENTO_PADRAO)

        hora_inicio = str(cfg.get(self.CONFIG_KEY_HORA_INICIO, '') or '').strip() or self.HORA_INICIO_PADRAO
        hora_fim    = str(cfg.get(self.CONFIG_KEY_HORA_FIM, '') or '').strip() or self.HORA_FIM_PADRAO

        try:
            intervalo_min = int(str(cfg.get(self.CONFIG_KEY_INTERVALO, '') or '').strip() or self.INTERVALO_MIN_PADRAO)
        except ValueError:
            intervalo_min = self.INTERVALO_MIN_PADRAO

        slots = self._gerar_slots(hora_inicio, hora_fim, intervalo_min)
        if not slots:
            # Configuração inconsistente (ex: hora_fim <= hora_inicio) — volta ao padrão.
            hora_inicio, hora_fim, intervalo_min = self.HORA_INICIO_PADRAO, self.HORA_FIM_PADRAO, self.INTERVALO_MIN_PADRAO
            slots = self._gerar_slots(hora_inicio, hora_fim, intervalo_min)

        return {
            'dias_permitidos':    dias,
            'dias_permitidos_js': [(d + 1) % 7 for d in dias],
            'hora_inicio':        hora_inicio,
            'hora_fim':           hora_fim,
            'intervalo_min':      intervalo_min,
            'slots':              slots,
        }

    def set_config_atendimento(self, empresa: str, data: dict, user_email: str) -> Tuple[bool, str]:
        """
        Atualiza a configuração de dias/horários de atendimento da empresa.

        Args:
            data: {dias_permitidos: [1,2,3], hora_inicio: 'HH:MM', hora_fim: 'HH:MM',
                   intervalo_min: int}  — dias_permitidos usa dt.weekday() (segunda=0).
        """
        if not self.config:
            return False, "Serviço de configuração não disponível"

        dias_in = data.get('dias_permitidos', [])
        try:
            dias_norm = sorted({int(d) for d in dias_in})
        except (ValueError, TypeError):
            return False, "Dias da semana inválidos"
        if not dias_norm or any(d < 0 or d > 6 for d in dias_norm):
            return False, "Selecione ao menos um dia válido de atendimento"

        hora_inicio = str(data.get('hora_inicio', '') or '').strip()
        hora_fim    = str(data.get('hora_fim', '') or '').strip()
        try:
            hi = datetime.strptime(hora_inicio, '%H:%M')
            hf = datetime.strptime(hora_fim, '%H:%M')
        except ValueError:
            return False, "Horário inválido. Use o formato HH:MM"
        if hf <= hi:
            return False, "O horário final deve ser depois do horário inicial"

        try:
            intervalo_min = int(data.get('intervalo_min', 0))
        except (ValueError, TypeError):
            return False, "Intervalo entre horários inválido"
        if intervalo_min < 5 or intervalo_min > 240:
            return False, "O intervalo entre horários deve estar entre 5 e 240 minutos"

        valores = {
            self.CONFIG_KEY_DIAS:        ','.join(str(d) for d in dias_norm),
            self.CONFIG_KEY_HORA_INICIO: hora_inicio,
            self.CONFIG_KEY_HORA_FIM:    hora_fim,
            self.CONFIG_KEY_INTERVALO:   str(intervalo_min),
        }
        _salvos, erros, lista_erros = self.config.batch_set_configs(empresa, valores, user_email)
        if erros:
            return False, f"Erro ao salvar configuração: {'; '.join(lista_erros)}"

        logger.info(f"Configuração de atendimento ASN atualizada para '{empresa}' por {user_email}")
        return True, "Configuração de atendimento atualizada com sucesso"

    # CRIAÇÃO

    def criar_asn(self, data: dict, user_email: str, transportadora_nome: str, empresa_dest: str) -> Tuple[bool, str, Optional[ASN]]:
        """
        Cria um novo ASN a partir do formulário da transportadora.

        Args:
            data: Dados do formulário {nota, tipo_nota, cte, volumes, ...}
            user_email: Email do usuário transportadora logado
            transportadora_nome: Nome da transportadora (campo 'departamento' ou 'cargo' do usuário)
            empresa_dest: Empresa destinatária

        Returns:
            (success, message, asn_criado)
        """
        if not self.sheets or not self.sheets.connected:
            return False, "Sheets não conectado", None

        # Validação mínima
        if not data.get('nota'):
            return False, "Número da NF/NFD é obrigatório", None
        if not data.get('data_prevista'):
            return False, "Data/hora prevista de entrega é obrigatória", None

        try:
            data_prevista = str(data.get('data_prevista', '')).strip()

            ok, msg = self._validar_data_prevista(data_prevista, empresa_dest)
            if not ok:
                return False, msg, None

            asn = ASN(
                id=self._gerar_id(),
                token=str(uuid.uuid4()),
                transportadora=transportadora_nome,
                email_transportadora=user_email,
                empresa_dest=empresa_dest,
                nota=str(data.get('nota', '')).strip(),
                tipo_nota=str(data.get('tipo_nota', 'NF')).strip(),
                notas_adicionais=str(data.get('notas_adicionais', '')).strip(),
                cte=str(data.get('cte', '')).strip(),
                volumes=str(data.get('volumes', '')).strip(),
                descricao_avaria=str(data.get('descricao_avaria', '')).strip(),
                data_prevista=data_prevista,
                nome_motorista=str(data.get('nome_motorista', '')).strip(),
                placa_veiculo=str(data.get('placa_veiculo', '')).strip(),
                status='agendado',
                criado_em=self._now(),
                criado_por=user_email,
            )

            ws = self._worksheet()
            headers = ws.row_values(1)
            # Garante colunas críticas existem antes de inserir linha
            for col in ('notas_adicionais', 'cod_empresa'):
                if col not in headers:
                    new_col_idx = len(headers) + 1
                    if new_col_idx > ws.col_count:
                        ws.resize(rows=ws.row_count, cols=new_col_idx + 4)
                    ws.update_cell(1, new_col_idx, col)
                    headers.append(col)
                    logger.info(f"Coluna '{col}' adicionada antes do append")
            values_dict = {
                'id':                   asn.id,
                'token':                asn.token,
                'transportadora':       asn.transportadora,
                'email_transportadora': asn.email_transportadora,
                'empresa_dest':         asn.empresa_dest,
                'nota':                 asn.nota,
                'tipo_nota':            asn.tipo_nota,
                'cte':                  asn.cte,
                'volumes':              asn.volumes,
                'descricao_avaria':     asn.descricao_avaria,
                'data_prevista':        asn.data_prevista,
                'nome_motorista':       asn.nome_motorista,
                'placa_veiculo':        asn.placa_veiculo,
                'status':               asn.status,
                'avaria_id':            '',
                'recebido_por':         '',
                'recebido_em':          '',
                'recusado_por':         '',
                'recusado_em':          '',
                'motivo_recusa':        '',
                'criado_em':            asn.criado_em,
                'criado_por':           asn.criado_por,
                'observacao_operador':  '',
                'notas_adicionais':     asn.notas_adicionais,
                'cod_empresa':          '',
            }
            row = [values_dict.get(h, '') for h in headers]
            ws.append_row(row)

            logger.info(f"ASN {asn.id} criado por {user_email}")

            # ── Notificações assíncronas (não bloqueantes) ──
            self._notificar_criacao(asn)

            return True, f"Agendamento {asn.id} criado com sucesso!", asn

        except Exception as e:
            logger.error(f"criar_asn: {e}")
            logger.debug(traceback.format_exc())
            return False, str(e), None

    def _listar_notas_asn(self, asn) -> List[tuple]:
        """Retorna lista de (tipo_nota, numnota) para todas as notas do ASN."""
        notas: List[tuple] = [(asn.tipo_nota or 'NF', asn.nota)]
        if asn.notas_adicionais:
            for extra in asn.notas_adicionais.split(','):
                extra = extra.strip().split('~')[0].strip()
                if not extra:
                    continue
                if ':' in extra:
                    tipo, num = extra.split(':', 1)
                    notas.append((tipo.strip(), num.strip()))
                else:
                    notas.append(('NF', extra))
        return [(t, n) for t, n in notas if n]

    def _carregar_avarias_existentes(self) -> Optional[List[dict]]:
        """
        Lê a aba Avarias uma única vez para alimentar a deduplicação.

        Retorna None quando a leitura falha. Quem chama DEVE abortar nesse caso:
        seguir em frente sem a lista significa criar avarias sem conseguir
        verificar duplicidade, que é exatamente o defeito que a dedup evita.
        """
        if not (self.sheets and self.sheets.connected):
            logger.error("Sheets indisponível — não é possível validar duplicidade de notas.")
            return None
        try:
            return self.sheets.get_all_records('Avarias')
        except Exception as e:
            logger.error(f"Erro ao pré-carregar Avarias para deduplicação: {e}")
            return None

    def _localizar_avaria_existente(
        self, asn, numnota: str, avarias_existentes: List[dict],
        nf_sankhya: str = '', nfd_sankhya: str = '', is_nfd: bool = False,
    ) -> Optional[str]:
        """
        Procura uma avaria já cadastrada no WMS para a nota informada.

        Reaproveitar o registro existente é o comportamento correto em todos os
        caminhos do ASN (processar, receber, recusar): o portal só deve criar uma
        avaria quando a nota realmente não existe no WMS. Vale para qualquer
        avaria, tenha ela nascido de um ASN ou de um lançamento manual.

        nf_sankhya/nfd_sankhya são opcionais — só o processar_asn consulta o
        Sankhya e consegue casar também pelo vínculo oficial NF↔NFD.
        """
        numnota = str(numnota or '').strip()
        for r in avarias_existentes:
            empresa_r = str(r.get('empresa', '') or '').strip()
            if asn.empresa_dest and empresa_r and empresa_r != asn.empresa_dest:
                continue
            nota_r = str(r.get('nota', '') or '').strip()
            nfd_r  = str(r.get('nfd',  '') or '').strip()
            id_r   = str(r.get('id',   '') or '').strip()
            if not id_r:
                continue

            # Número informado bate com a NF original ou com a NFD já gravada
            if numnota and (numnota == nota_r or (nfd_r and numnota == nfd_r)):
                return id_r

            # Vínculo oficial trazido do Sankhya (só no processar_asn).
            # Só exige o match exato da NFD quando a avaria candidata JÁ TEM um
            # nfd_r gravado: se o Sankhya trouxer uma NF de origem diferente da
            # que está gravada (ambiguidade de NUMNOTA, nota cancelada/reemitida),
            # isso não pode fazer o sistema deixar de reconhecer um vínculo de
            # NFD já existente e duplicar o lançamento.
            if nf_sankhya and nf_sankhya == nota_r:
                if is_nfd and nfd_sankhya and nfd_r:
                    if nfd_sankhya == nfd_r:
                        return id_r
                else:
                    return id_r
        return None

    def _criar_avarias_por_nota(self, asn, cod_empresa, operador_email: str) -> List[str]:
        """
        Cria UMA avaria para CADA nota do ASN (principal + adicionais), sem consulta ao
        Sankhya — usado no recebimento/recusa direto (fora da Etapa 2 de processamento).

        Cada nota precisa da sua própria avaria para que a etiqueta inicial e o lote
        automático (agrupados por avaria_id) NÃO misturem itens de notas diferentes.
        Ver processar_asn(), que aplica a mesma regra de 1 avaria por nota quando passa
        pela Etapa 2.

        Retorna a lista de avaria_id criados (pode ser menor que o total de notas se
        alguma falhar — a falha é logada e a nota seguinte continua sendo processada).
        """
        if not self.wms:
            return []
        avarias_existentes = self._carregar_avarias_existentes()
        if avarias_existentes is None:
            logger.error(
                f"❌ ASN {asn.id}: avarias NÃO criadas — falha ao ler a aba Avarias impede "
                f"validar duplicidade. Reprocesse o ASN após restabelecer o acesso ao Sheets."
            )
            return []

        avaria_ids: List[str] = []
        for tipo_nota, numnota in self._listar_notas_asn(asn):
            # A nota pode já estar cadastrada no WMS (lançamento manual ou ASN
            # anterior). Nesse caso reaproveita o registro em vez de duplicar.
            ja_existe = self._localizar_avaria_existente(asn, numnota, avarias_existentes)
            if ja_existe:
                if ja_existe not in avaria_ids:
                    avaria_ids.append(ja_existe)
                logger.info(
                    f"Nota {tipo_nota}:{numnota} já cadastrada no WMS "
                    f"(avaria {ja_existe}) — reaproveitando em vez de duplicar."
                )
                continue

            dados_avaria = {
                'nota':           numnota,
                'transportadora': asn.transportadora,
                'cte':            asn.cte,
                'quantidade':     asn.volumes,
                'observacao':     f"[ASN {asn.id}] {asn.descricao_avaria}".strip(),
                'cod_empresa':    str(cod_empresa or ''),
                'empresa':        asn.empresa_dest,
                'asn_id':         asn.id,
            }
            try:
                success, avaria_obj, msg = self.wms.registrar_avaria(dados_avaria, operador_email)
                if success and avaria_obj:
                    avaria_ids.append(avaria_obj.id)
                    avarias_existentes.append({
                        'id': avaria_obj.id, 'nota': numnota,
                        'nfd': '', 'empresa': asn.empresa_dest,
                    })
                    logger.info(f"Avaria {avaria_obj.id} criada (nota {tipo_nota}:{numnota}) a partir do ASN {asn.id}")
                else:
                    logger.warning(f"Avaria não criada para nota {tipo_nota}:{numnota} do ASN {asn.id}: {msg}")
            except Exception as e:
                logger.error(f"Erro ao criar avaria da nota {tipo_nota}:{numnota} do ASN {asn.id}: {e}")
        return avaria_ids

    def _determinar_trigger_avaria(
        self, av_id: str, notas_recusadas: set, empresa: str
    ) -> str:
        """
        Determina o trigger correto para uma avaria com base nas notas recusadas.

        Se a nota da avaria está em notas_recusadas → 'asn_recusado'.
        Se não está (nota sobrevivente) → 'asn_recebido'.
        Sem notas_recusadas (recusa total) → 'asn_recusado'.
        """
        if not notas_recusadas:
            return 'asn_recusado'
        if not self.wms:
            return 'asn_recusado'
        def _num(raw: str) -> str:
            s = str(raw).strip()
            return s.split(':', 1)[1].strip() if ':' in s else s

        try:
            avaria = self.wms.get_avaria_by_id(av_id)
            if not avaria:
                return 'asn_recusado'
            nota_av = _num(avaria.nota or '')
            nfd_av  = _num(getattr(avaria, 'nfd', '') or '')
            if nota_av in notas_recusadas or (nfd_av and nfd_av in notas_recusadas):
                return 'asn_recusado'
            return 'asn_recebido'
        except Exception as e:
            logger.warning(f"_determinar_trigger_avaria({av_id}): {e}")
            return 'asn_recusado'

    def _determinar_trigger_por_produtos_recusados(
        self, av_id: str, produtos_recusados: list
    ) -> str:
        """
        Determina o trigger correto para uma avaria com base nos produtos recusados.

        Se não há produtos_recusados → retorna 'asn_recebido'.
        Lê Itens_Avarias para encontrar itens da avaria.
        Se ALGUM item da avaria tem codprod nos recusados → 'asn_recusado'.
        Caso contrário → 'asn_recebido'.
        Falha silenciosamente (log warning, retorna 'asn_recebido' em erro).
        """
        if not produtos_recusados:
            return 'asn_recebido'
        try:
            codprods_recusados = {
                str(p.get('codprod', '')).strip()
                for p in produtos_recusados
                if p.get('codprod')
            }
            if not codprods_recusados:
                return 'asn_recebido'

            itens_raw = self.sheets.get_wms_itens_cached()
            for item in itens_raw:
                item_av_id = str(item.get('avaria_id', '') or '').strip()
                if item_av_id != av_id:
                    continue
                item_codprod = str(item.get('codprod', '') or '').strip()
                if item_codprod in codprods_recusados:
                    return 'asn_recusado'
            return 'asn_recebido'
        except Exception as e:
            logger.warning(f"_determinar_trigger_por_produtos_recusados({av_id}): {e}")
            return 'asn_recebido'

    def _processar_conferencia_itens(
        self, avaria_ids: list, itens_conferencia: list, produtos_recusados: list, operador_email: str
    ) -> tuple:
        """
        Processa a conferência dos itens (recebimento total, parcial, recusa ou saldo pendente).
        Retorna: (codprods_recusados, sequencias_estaticas, notas_nums_com_pendencia, recusa_linhas)
        """
        codprods_recusados = set()
        sequencias_estaticas = {av_id: set() for av_id in avaria_ids}
        notas_nums_com_pendencia = set()
        recusa_linhas = []

        if not self.wms:
            return codprods_recusados, sequencias_estaticas, notas_nums_com_pendencia, recusa_linhas

        def _clean_nf(raw: str) -> str:
            s = str(raw).strip()
            return s.split(':', 1)[1].strip() if ':' in s else s

        if itens_conferencia:
            # Regra de processo: não existe recebimento parcial de item. Tudo que não foi
            # recebido (qtd_orig - qtd_recebida) é recusado; não há saldo pendente.
            # Performance: Itens_Avarias é lida UMA vez e todas as alterações são gravadas
            # em lote (1 batch_update + 1 append_rows), independente do nº de itens.
            snapshot = self.wms.ler_itens_avarias_raw()
            if snapshot is None:
                return codprods_recusados, sequencias_estaticas, notas_nums_com_pendencia, recusa_linhas
            ws_itens, headers_it, rows_it = snapshot
            atualizacoes = []
            novos_itens = []

            for item in itens_conferencia:
                av_id = str(item.get('avaria_id', '')).strip()
                if av_id not in avaria_ids and len(avaria_ids) == 1:
                    av_id = avaria_ids[0]
                if not av_id or av_id not in avaria_ids:
                    continue
                try:
                    seq = item.get('sequencia')
                    if seq is None:
                        continue
                    seq_int = int(seq)
                    qtd_recb = float(item.get('qtd_recebida', 0) or 0)
                    codprod = str(item.get('codprod', '')).strip()
                    descr   = str(item.get('descrprod', '')).strip()

                    linha_item, item_data = self.wms.localizar_item_raw(headers_it, rows_it, av_id, seq_int)
                    if not item_data:
                        continue

                    try:
                        qtd_orig = float(
                            str(item_data.get('qtd_avariada') or item_data.get('qtdneg')
                                or item.get('qtd_orig_nf') or 0).replace(',', '.')
                        )
                    except (ValueError, TypeError):
                        qtd_orig = 0.0
                    if qtd_orig <= 0:
                        qtd_orig = qtd_recb + float(item.get('qtd_recusada', 0) or 0)

                    qtd_recb = max(0.0, min(qtd_recb, qtd_orig))
                    qtd_rec = qtd_orig - qtd_recb
                    if qtd_rec < 0.001:
                        qtd_rec = 0.0

                    if qtd_rec > 0:
                        recusa_linhas.append(f"{codprod} ({descr}): {qtd_rec:.0f} und recusada(s)")

                    if qtd_recb > 0 and qtd_rec > 0:
                        atualizacoes.append((linha_item, {'qtd_avariada': qtd_recb, 'qtdneg': qtd_recb}))
                        nova_seq_rec = seq_int * 100 + 99
                        novos_itens.append({
                            'avaria_id': av_id,
                            'sequencia': str(nova_seq_rec),
                            'codprod': item_data.get('codprod', codprod),
                            'descrprod': item_data.get('descrprod', descr),
                            'qtdneg': qtd_rec,
                            'qtd_avariada': qtd_rec,
                            'localizacao': '',
                            'valor_item': float(str(item_data.get('valor_item') or 0).replace(',', '.') or 0),
                            'codlocalorig': item_data.get('codlocalorig', ''),
                            'codlocaldest': '',
                            'codgrupoprod': item_data.get('codgrupoprod', ''),
                            'codemp': item_data.get('codemp', ''),
                            'id_etiqueta': '',
                            'status_etiqueta': 'recusado_asn',
                        })
                        logger.info(f"Split (recusa do restante): av={av_id} seq={seq_int} recebida={qtd_recb} -> nova_seq={nova_seq_rec} recusada={qtd_rec}")
                    elif qtd_rec > 0:
                        atualizacoes.append((linha_item, {
                            'qtd_avariada': qtd_rec, 'qtdneg': qtd_rec, 'status_etiqueta': 'recusado_asn',
                        }))
                        logger.info(f"Item 100% recusado: av={av_id} seq={seq_int} (qtd={qtd_rec})")
                    elif qtd_recb > 0:
                        atualizacoes.append((linha_item, {'qtd_avariada': qtd_recb, 'qtdneg': qtd_recb}))

                except Exception as e_item:
                    logger.warning(f"Erro no processamento de conferência do item {item.get('codprod')}: {e_item}")

            self.wms.gravar_itens_avarias_lote(
                ws_itens, headers_it, atualizacoes, novos_itens, alterado_por=operador_email
            )

        elif produtos_recusados:
            for prod in produtos_recusados:
                try:
                    qtd_rec = float(prod.get('qtd_recusada', 0) or 0)
                    if qtd_rec <= 0:
                        continue
                    codprod = str(prod.get('codprod', '')).strip()
                    descr   = str(prod.get('descrprod', '')).strip()
                    seq     = prod.get('sequencia')
                    recusa_linhas.append(f"{codprod} ({descr}): {qtd_rec:.0f} und recusada(s)")
                    if seq is not None:
                        for av_id in avaria_ids:
                            item_data = self.wms.get_item_avaria_por_seq(av_id, int(seq))
                            if item_data:
                                try:
                                    qtd_total = float(str(item_data.get('qtd_avariada') or item_data.get('qtdneg') or qtd_rec).replace(',', '.'))
                                except (ValueError, TypeError):
                                    qtd_total = qtd_rec
                                qtd_aceita = qtd_total - qtd_rec
                                if qtd_aceita > 0.001:
                                    self.wms.update_item_avaria(av_id, int(seq), operador_email, qtd_avariada=qtd_aceita, qtdneg=qtd_aceita)
                                    nova_seq = int(seq) * 100 + 99
                                    self.wms.inserir_item_avaria(
                                        avaria_id=av_id, sequencia=nova_seq,
                                        codprod=item_data.get('codprod', codprod), descrprod=item_data.get('descrprod', descr),
                                        qtdneg=qtd_rec, qtd_avariada=qtd_rec,
                                        codlocalorig=item_data.get('codlocalorig', ''), codgrupoprod=item_data.get('codgrupoprod', ''),
                                        codemp=item_data.get('codemp', ''),
                                        valor_item=float(str(item_data.get('valor_item') or 0).replace(',', '.') or 0),
                                        status_etiqueta='recusado_asn',
                                    )
                                else:
                                    self.wms.update_item_avaria(av_id, int(seq), operador_email, qtd_avariada=qtd_rec)
                                    codprods_recusados.add(codprod)
                            else:
                                self.wms.update_item_avaria(av_id, int(seq), operador_email, qtd_avariada=qtd_rec)
                                codprods_recusados.add(codprod)
                except Exception as prod_err:
                    logger.warning(f"Recusa parcial prod {prod.get('codprod')}: {prod_err}")

        return codprods_recusados, sequencias_estaticas, notas_nums_com_pendencia, recusa_linhas

    def _buscar_avaria_por_nota(self, numnota: str, empresa: str = ''):
        """
        Busca avaria existente pelo número da nota no Sheets.
        Verifica tanto o campo `nota` quanto `nfd` para cobrir NFDs/devoluções.
        Filtra por empresa quando informada para evitar colisão entre empresas.
        Retorna None se não encontrar.
        """
        if not self.sheets or not self.sheets.connected:
            return None
        try:
            numnota_clean = str(numnota).strip()
            registros = self.sheets.get_all_records('Avarias')
            for r in registros:
                nota_r    = str(r.get('nota',    '') or '').strip()
                nfd_r     = str(r.get('nfd',     '') or '').strip()
                empresa_r = str(r.get('empresa', '') or '').strip()
                if not empresa_visivel(empresa_r, empresa):
                    continue
                if nota_r == numnota_clean or nfd_r == numnota_clean:
                    from modules.wms.models.avaria import Avaria
                    return Avaria.from_dict(r)
        except Exception as e:
            logger.warning(f"_buscar_avaria_por_nota({numnota}): {e}")
        return None

    # ETAPA 2 — Operador insere empresa, consulta Sankhya, cria avaria(s)

    def processar_asn(self, asn_id: str, cod_empresa: str, operador_email: str,
                      notas_empresas: dict = None) -> Tuple[bool, str, List[str]]:
        """
        Etapa 2 do fluxo ASN:
          1. Operador informa cod_empresa (padrão) e, opcionalmente, CODEMP por nota
          2. Sistema consulta cada NF no Sankhya usando o CODEMP correspondente
          3. Cria registro de avaria para cada NF
          4. Atualiza ASN para status='processado'

        notas_empresas: dict {"TIPO:NUM" → "CODEMP"} para sobrescrever o cod_empresa padrão
        """
        asn = self.get_by_id(asn_id)
        if not asn:
            return False, f"ASN {asn_id} não encontrado", []
        if asn.status != 'agendado':
            return False, f"ASN já foi processado ou não está mais agendado (status: {asn.status})", []
        if not self.sankhya_avaria:
            return False, "Serviço Sankhya não disponível no momento", []
        if not self.wms:
            return False, "Serviço WMS não disponível", []

        notas_empresas = notas_empresas or {}

        # Monta lista completa de notas (principal + adicionais)
        notas: List[tuple] = [(asn.tipo_nota, asn.nota)]
        if asn.notas_adicionais:
            for extra in asn.notas_adicionais.split(','):
                extra = extra.strip().split('~')[0].strip()  # remove sufixo ~CTE~VOL
                if not extra:
                    continue
                if ':' in extra:
                    tipo, num = extra.split(':', 1)
                    notas.append((tipo.strip(), num.strip()))
                else:
                    notas.append(('NF', extra))

        avaria_ids: List[str] = []
        erros: List[str] = []

        # Pré-carrega registros do Sheets uma única vez em memória para alta performance (evita leituras lentas repetidas)
        avarias_existentes = self._carregar_avarias_existentes()
        if avarias_existentes is None:
            return False, ("Não foi possível ler as avarias já cadastradas para validar "
                           "duplicidade. Tente novamente em instantes."), []

        for tipo_nota, numnota in notas:
            if not numnota:
                continue
            # CODEMP por nota tem prioridade; fallback para o padrão informado
            codemp_nota = notas_empresas.get(f"{tipo_nota}:{numnota}") or cod_empresa
            try:
                # 1. Consulta Sankhya primeiro para extrair e validar o par oficial do vínculo (NF Original + NFD)
                # tipo_nota_hint filtra por TIPMOV na busca do cabeçalho: evita que uma NF e uma NFD
                # que coincidentemente compartilham o mesmo NUMNOTA sejam confundidas entre si.
                dados_sankhya, itens_sankhya, erro = self.sankhya_avaria.buscar_e_mapear_nota(
                    numnota, codemp_nota, tipo_nota_hint=tipo_nota
                )
                if erro or not dados_sankhya:
                    erros.append(f"Nota {numnota}: {erro or 'Não encontrada no Sankhya'}")
                    continue

                nf_sankhya  = str(dados_sankhya.get('nf_venda', '') or '').strip()
                nfd_sankhya = str(dados_sankhya.get('nfd',      '') or '').strip()
                tipmov      = str(dados_sankhya.get('tipmov_origem', '') or '').strip()

                is_nfd = (tipmov == 'D' or bool(nfd_sankhya) or str(tipo_nota).upper() == 'NFD')

                # 2. Validação obrigatória de vínculo entre NFD e NF de Venda original
                if is_nfd and not nf_sankhya:
                    erros.append(f"NFD {numnota}: Vínculo com a Nota Fiscal original de Venda não encontrado no Sankhya.")
                    continue

                # 3. Verifica em memória se a nota já está cadastrada em Avarias
                avaria_encontrada = self._localizar_avaria_existente(
                    asn, numnota, avarias_existentes,
                    nf_sankhya=nf_sankhya, nfd_sankhya=nfd_sankhya, is_nfd=is_nfd,
                )

                if avaria_encontrada:
                    if avaria_encontrada not in avaria_ids:
                        avaria_ids.append(avaria_encontrada)
                    logger.info(f"Vínculo validado (NF: {nf_sankhya} | NFD: {nfd_sankhya}) já existe no banco — reaproveitando.")
                    continue

                # Mapeia itens do Sankhya para o formato esperado por registrar_avaria
                itens_para_avaria = []
                for item in (itens_sankhya or []):
                    itens_para_avaria.append({
                        'codprod':         item.get('codprod', ''),
                        'descrprod':       item.get('descrprod', ''),
                        'qtdneg':          item.get('qtdneg', ''),
                        'qtd_avariada':    '',
                        'codlocaldest':    '',
                        'codlocalorig':    item.get('codlocalorig', ''),
                        'localizacao':     '',
                        'valor_item':      str(item.get('valor_item', '')),
                        'codgrupoprod':    item.get('codgrupoprod', ''),
                        'codemp':          codemp_nota,
                        'prefixo_estoque': '',
                    })

                dados_avaria = {
                    'cod_empresa':    codemp_nota,
                    'nota':           nf_sankhya or numnota,
                    'nfd':            nfd_sankhya,
                    'quantidade':     dados_sankhya.get('quantidade') or asn.volumes,
                    'volume':         dados_sankhya.get('volume', ''),
                    'transportadora': asn.transportadora,
                    'cte':            asn.cte,
                    'valor_nf':       str(dados_sankhya.get('vlrnota', '')),
                    'valor_nfd':      str(dados_sankhya.get('valor_nfd', '')),
                    'observacao':     f"[ASN {asn.id}] {asn.descricao_avaria}".strip(),
                    'empresa':        asn.empresa_dest,
                    'itens':          itens_para_avaria,
                }

                success, avaria_obj, msg = self.wms.registrar_avaria(dados_avaria, operador_email)
                if success and avaria_obj:
                    avaria_ids.append(avaria_obj.id)
                    avarias_existentes.append({
                        'id': avaria_obj.id,
                        'nota': dados_avaria['nota'],
                        'nfd': dados_avaria['nfd'],
                        'empresa': asn.empresa_dest
                    })
                    logger.info(f"Avaria {avaria_obj.id} criada (NF: {dados_avaria['nota']} | NFD: {dados_avaria['nfd']}) para ASN {asn_id}")
                else:
                    erros.append(f"Nota {numnota}: {msg}")

            except Exception as e:
                logger.error(f"processar_asn Nota {numnota}: {e}")
                erros.append(f"Nota {numnota}: {str(e)}")

        if erros:
            # Se houver erro de vínculo ou na consulta, aborta e avisa o operador sem marcar como processado
            return False, "Falha na validação das notas/vínculo: " + " | ".join(erros), []

        if not avaria_ids:
            return False, "Nenhuma nota foi processada ou vinculada com sucesso.", []

        # Atualiza ASN: status=processado, avaria_id(s), cod_empresa
        try:
            ws = self._worksheet()
            all_values = ws.get_all_values()
            headers = all_values[0]

            col_status   = headers.index('status') + 1
            col_avaria   = headers.index('avaria_id') + 1
            col_cod_emp  = headers.index('cod_empresa') + 1 if 'cod_empresa' in headers else None

            avarias_str = ','.join(avaria_ids)

            for row_idx, row in enumerate(all_values[1:], start=2):
                if str(row[0]).strip() == asn_id:
                    ws.update_cell(row_idx, col_status, 'processado')
                    ws.update_cell(row_idx, col_avaria, avarias_str)
                    if col_cod_emp:
                        ws.update_cell(row_idx, col_cod_emp, cod_empresa)
                    break

            logger.info(f"ASN {asn_id} → processado | avarias: {avarias_str}")

            msg_ok = f"Avaria(s) criada(s): {avarias_str}"
            if erros:
                msg_ok += f" | Atenção: {' | '.join(erros)}"
            return True, msg_ok, avaria_ids

        except Exception as e:
            logger.error(f"Erro ao atualizar ASN {asn_id} após processar: {e}")
            return True, f"Avaria(s) {','.join(avaria_ids)} criada(s), mas erro ao atualizar ASN: {e}", avaria_ids

    # ETAPA 3 — Recebimento físico

    def receber_asn(self, asn_id: str, operador_email: str, observacao: str = '',
                    cod_empresa: int = None, produtos_recusados: list = None,
                    itens_conferencia: list = None) -> Tuple[bool, str, Optional[str]]:
        """
        Marca o ASN como recebido e cria a avaria automaticamente com dados pré-populados.

        Fluxo:
          1. Valida que o ASN existe e está agendado
          2. Cria a avaria via WmsAvariaService (com dados do ASN)
          3. A avaria já nasce com etiqueta inicial (prefixo 00-)
          4. Atualiza o ASN: status=recebido, avaria_id, recebido_por, recebido_em
          5. Notifica a transportadora por email

        Returns:
            (success, message, avaria_id_criado)
        """
        asn, linha_asn, ws_asn, all_values_asn = self._get_by_id_com_linha(asn_id)
        if not asn:
            return False, f"ASN {asn_id} não encontrado", None
        if asn.status not in ('agendado', 'processado', 'recebido_parcial'):
            return False, f"ASN {asn_id} não pode ser recebido (status atual: {asn.status})", None

        try:
            # Se já foi processado (avaria criada na Etapa 2), não criar avaria novamente
            avaria_id = asn.avaria_id or None

            # ── Criar avaria(s) apenas se ainda não foi processado na Etapa 2 ──
            # Uma avaria POR NOTA (principal + adicionais) — ver _criar_avarias_por_nota().
            # Isso evita que múltiplas notas do mesmo ASN caiam na mesma avaria_id e,
            # consequentemente, no mesmo lote/etiqueta inicial.
            if asn.status == 'agendado' and not avaria_id and self.wms:
                avaria_ids_criadas = self._criar_avarias_por_nota(asn, cod_empresa, operador_email)
                if avaria_ids_criadas:
                    avaria_id = ','.join(avaria_ids_criadas)
                    logger.info(f"Avaria(s) {avaria_id} criada(s) a partir do ASN {asn_id}")
                else:
                    logger.warning(f"Nenhuma avaria criada para o ASN {asn_id} — ASN ainda será marcado como recebido")
            elif asn.status == 'processado':
                logger.info(f"ℹASN {asn_id} já processado — avaria(s) {avaria_id} mantidas, só marca recebido")

            av_ids_lista = [av.strip() for av in str(avaria_id).split(',') if av.strip()] if avaria_id else []
            codprods_recusados, sequencias_estaticas, notas_com_pendencia, recusa_linhas = self._processar_conferencia_itens(
                avaria_ids=av_ids_lista,
                itens_conferencia=itens_conferencia,
                produtos_recusados=produtos_recusados,
                operador_email=operador_email,
            )
            if recusa_linhas:
                obs_extra = "Itens recusados no recebimento: " + "; ".join(recusa_linhas)
                observacao = (observacao + "\n" + obs_extra).strip() if observacao else obs_extra

            # ── Atualizar ASN no Sheets (1 batch_update com todos os campos) ──
            headers = all_values_asn[0]

            col_status   = headers.index('status') + 1
            col_avaria   = headers.index('avaria_id') + 1
            col_rec_por  = headers.index('recebido_por') + 1
            col_rec_em   = headers.index('recebido_em') + 1
            col_obs      = headers.index('observacao_operador') + 1

            status_final = 'recebido_parcial' if notas_com_pendencia else 'recebido'
            campos_asn = {col_status: status_final, col_avaria: avaria_id or ''}
            if status_final == 'recebido':
                campos_asn[col_rec_por] = operador_email
                campos_asn[col_rec_em] = self._now()
            if observacao:
                campos_asn[col_obs] = observacao
            self._gravar_campos_asn(ws_asn, linha_asn, campos_asn)

            logger.info(f"ASN {asn_id} marcado como {status_final} por {operador_email}")

            # Triggers calculados ANTES das etiquetas (usa o cache de itens, ainda quente)
            triggers_por_avaria = {
                av_id: self._determinar_trigger_por_produtos_recusados(av_id, produtos_recusados)
                for av_id in av_ids_lista
            } if (av_ids_lista and self.automacao) else {}

            # ── Gerar etiquetas iniciais automaticamente ──
            if self.etiqueta and av_ids_lista:
                for av_id in av_ids_lista:
                    try:
                        self.etiqueta.criar_etiquetas_iniciais(
                            avaria_id=av_id,
                            operador=operador_email,
                            empresa=asn.empresa_dest,
                            codprods_recusados=codprods_recusados,
                            sequencias_estaticas=sequencias_estaticas.get(av_id, set()),
                        )
                    except Exception as etq_err:
                        logger.warning(f"Etiquetas iniciais para {av_id} ignoradas: {etq_err}")

            # ── Disparar automação de status (uma chamada por avaria, trigger por-avaria) ──
            if av_ids_lista and self.automacao:
                for av_id in av_ids_lista:
                    trigger = triggers_por_avaria[av_id]
                    try:
                        self.automacao.aplicar(
                            trigger=trigger,
                            avaria_id=av_id,
                            empresa=asn.empresa_dest,
                            operador_email=operador_email,
                        )
                    except Exception as auto_err:
                        logger.warning(
                            f"⚠️ Automação '{trigger}' para avaria {av_id} ignorada: {auto_err}"
                        )

            # ── Notificar transportadora ──
            self._notificar_recebimento(asn, avaria_id)

            msg_final = f"ASN {asn_id} recebido com sucesso"
            if avaria_id:
                msg_final += f" — Avaria {avaria_id} criada automaticamente"
            return True, msg_final, avaria_id

        except Exception as e:
            logger.error(f"receber_asn({asn_id}): {e}")
            logger.debug(traceback.format_exc())
            return False, str(e), None

    def _avarias_das_notas(self, av_ids: list, notas_alvo: set, itens_conferencia: list) -> list:
        """
        Subconjunto de av_ids que pertence às notas informadas (recebimento parcial).

        Sinal principal: avaria_id dos itens conferidos (o front só envia itens das
        notas marcadas). Complemento: nota/nfd gravadas na própria avaria — cobre
        nota marcada sem itens detalhados.
        """
        def _num(raw) -> str:
            s = str(raw or '').strip()
            return s.split(':', 1)[1].strip() if ':' in s else s

        alvo = {str(i.get('avaria_id', '')).strip() for i in (itens_conferencia or [])}
        for av_id in av_ids:
            if av_id in alvo or not self.wms:
                continue
            try:
                av = self.wms.get_avaria_by_id(av_id)
            except Exception:
                av = None
            if av and ({_num(av.nota), _num(getattr(av, 'nfd', ''))} - {''}) & notas_alvo:
                alvo.add(av_id)
        resultado = [a for a in av_ids if a in alvo]
        if not resultado and len(av_ids) == 1:
            return list(av_ids)
        return resultado

    def receber_parcial_asn(
        self,
        asn_id: str,
        notas_recebidas: list,
        operador_email: str,
        observacao: str = '',
        cod_empresa=None,
        produtos_recusados=None,
        itens_conferencia=None,
    ) -> Tuple[bool, str, Optional[str], Optional[str]]:
        """
        Registra o recebimento parcial em nível de nota fiscal inteira e permite recusal parcial de itens.
        O ASN muda para 'recebido_parcial' e permanece na fila até que todas as
        suas notas estejam concluídas (quando passa a 'recebido').
        """
        asn, linha_asn, ws_asn, all_values_asn = self._get_by_id_com_linha(asn_id)
        if not asn:
            return False, f"ASN {asn_id} não encontrado", None, None
        if asn.status not in ('agendado', 'processado', 'recebido_parcial'):
            return False, f"ASN {asn_id} não pode receber parcial (status atual: {asn.status})", None, None

        try:
            avaria_id = asn.avaria_id or None

            # Uma avaria POR NOTA (principal + adicionais) — evita agrupar notas
            # distintas na mesma avaria_id/lote. Ver _criar_avarias_por_nota().
            if asn.status == 'agendado' and not avaria_id and self.wms:
                avaria_ids_criadas = self._criar_avarias_por_nota(asn, cod_empresa, operador_email)
                if avaria_ids_criadas:
                    avaria_id = ','.join(avaria_ids_criadas)
                    logger.info(f"Avaria(s) {avaria_id} criada(s) a partir do ASN {asn_id} em recebimento parcial")
                else:
                    logger.warning(f"Nenhuma avaria criada para o ASN {asn_id} em recebimento parcial")

            av_ids = [av.strip() for av in str(avaria_id).split(',') if av.strip()] if avaria_id else []
            codprods_recusados, sequencias_estaticas, notas_com_pendencia, recusa_linhas = self._processar_conferencia_itens(
                avaria_ids=av_ids,
                itens_conferencia=itens_conferencia,
                produtos_recusados=produtos_recusados,
                operador_email=operador_email,
            )
            if recusa_linhas:
                obs_extra = "Itens recusados nesta remessa parcial: " + "; ".join(recusa_linhas)
                observacao = (observacao + "\n" + obs_extra).strip() if observacao else obs_extra

            def _num_nota(raw: str) -> str:
                s = str(raw).strip()
                return s.split(':', 1)[1].strip() if ':' in s else s

            novas_notas = {_num_nota(n) for n in (notas_recebidas or []) if n}
            # Se uma nota ainda possui saldo pendente ao nível de item, não marcamos ela como integralmente recebida
            novas_concluidas = novas_notas - notas_com_pendencia

            ja_recebidas = {_num_nota(n) for n in str(asn.notas_recebidas_parcial or '').split(',') if n.strip()}
            total_recebidas = ja_recebidas | novas_concluidas

            todas_notas = self._listar_notas_asn(asn)
            todas_notas_nums = {n for _, n in todas_notas}

            if todas_notas_nums and total_recebidas >= todas_notas_nums and not notas_com_pendencia:
                novo_status = 'recebido'
            else:
                novo_status = 'recebido_parcial'

            notas_str = ', '.join(sorted(novas_notas))
            obs_add = f"[Receb. Parcial em {self._now()[:16]} por {operador_email}: Notas conferidas: {notas_str}]"
            if observacao:
                obs_add += f" {observacao}"

            ws = ws_asn
            all_values = all_values_asn
            headers = all_values[0]

            if 'notas_recebidas_parcial' not in headers:
                self._ensure_sheet()
                all_values = ws.get_all_values()
                headers = all_values[0]

            col_status  = headers.index('status') + 1
            col_avaria  = headers.index('avaria_id') + 1
            col_obs     = headers.index('observacao_operador') + 1
            col_parcial = headers.index('notas_recebidas_parcial') + 1 if 'notas_recebidas_parcial' in headers else None
            col_rec_por = headers.index('recebido_por') + 1 if 'recebido_por' in headers else None
            col_rec_em  = headers.index('recebido_em')  + 1 if 'recebido_em' in headers else None

            val_parcial = ','.join(sorted(total_recebidas))

            row_asn = all_values[linha_asn - 1] if linha_asn and linha_asn - 1 < len(all_values) else []
            current_obs = str(row_asn[col_obs - 1]).strip() if len(row_asn) >= col_obs else ''
            nova_obs = (current_obs + "\n" + obs_add).strip() if current_obs else obs_add
            campos_asn = {col_status: novo_status, col_avaria: avaria_id or ''}
            if col_parcial:
                campos_asn[col_parcial] = val_parcial
            campos_asn[col_obs] = nova_obs
            if novo_status == 'recebido':
                if col_rec_por:
                    campos_asn[col_rec_por] = operador_email
                if col_rec_em:
                    campos_asn[col_rec_em] = self._now()
            self._gravar_campos_asn(ws, linha_asn, campos_asn)

            logger.info(f"ASN {asn_id} recebimento parcial registrado (status: {novo_status}) por {operador_email}")

            # Só as avarias das notas conferidas AGORA recebem etiqueta/automação; as demais
            # notas do ASN continuam intactas até serem recebidas.
            av_ids_alvo = self._avarias_das_notas(av_ids, novas_notas, itens_conferencia)
            logger.info(f"Receb. parcial ASN {asn_id}: avarias alvo {av_ids_alvo} de {av_ids} (notas {sorted(novas_notas)})")

            triggers_por_avaria = {
                av_id: self._determinar_trigger_por_produtos_recusados(av_id, produtos_recusados)
                for av_id in av_ids_alvo
            } if (av_ids_alvo and self.automacao) else {}

            if self.etiqueta and av_ids_alvo:
                for av_id in av_ids_alvo:
                    try:
                        self.etiqueta.criar_etiquetas_iniciais(
                            avaria_id=av_id,
                            operador=operador_email,
                            empresa=asn.empresa_dest,
                            codprods_recusados=codprods_recusados,
                            sequencias_estaticas=sequencias_estaticas.get(av_id, set()),
                        )
                    except Exception as etq_err:
                        logger.warning(f"Etiquetas iniciais para {av_id} ignoradas: {etq_err}")

            # ── Disparar automação de status (uma chamada por avaria, trigger por-avaria) ──
            if av_ids_alvo and self.automacao:
                for av_id in av_ids_alvo:
                    trigger = triggers_por_avaria[av_id]
                    try:
                        self.automacao.aplicar(
                            trigger=trigger,
                            avaria_id=av_id,
                            empresa=asn.empresa_dest,
                            operador_email=operador_email,
                        )
                    except Exception as auto_err:
                        logger.warning(
                            f"⚠️ Automação '{trigger}' (parcial) para avaria {av_id} ignorada: {auto_err}"
                        )

            if novo_status == 'recebido':
                self._notificar_recebimento(asn, avaria_id)

            msg_final = f"Recebimento parcial registrado: {len(novas_notas)} nota(s) marcada(s) como entregue(s)."
            if novo_status == 'recebido':
                msg_final += " Todas as notas foram entregues e o ASN foi concluído!"
            return True, msg_final, avaria_id, novo_status

        except Exception as e:
            logger.error(f"receber_parcial_asn({asn_id}): {e}")
            logger.debug(traceback.format_exc())
            return False, str(e), None, None

    # FOTOS

    def update_link_fotos(self, asn_id: str, link_fotos: str) -> Tuple[bool, str]:
        """
        Atualiza o campo link_fotos_drive para o ASN indicado.
        Adiciona a coluna na planilha se ainda não existir.

        Returns:
            (success, message)
        """
        try:
            ws = self._worksheet()
            if not ws:
                return False, "Planilha não disponível"

            all_values = ws.get_all_values()
            if not all_values:
                return False, "Planilha vazia"

            headers = all_values[0]

            # Garante que a coluna existe
            if 'link_fotos_drive' not in headers:
                new_col_idx = len(headers) + 1
                if new_col_idx > ws.col_count:
                    ws.resize(rows=ws.row_count, cols=new_col_idx + 4)
                ws.update_cell(1, new_col_idx, 'link_fotos_drive')
                headers.append('link_fotos_drive')
                logger.info("Coluna 'link_fotos_drive' adicionada à aba WMS_ASN")

            col_fotos = headers.index('link_fotos_drive') + 1

            for row_idx, row in enumerate(all_values[1:], start=2):
                if str(row[0]).strip() == asn_id:
                    ws.update_cell(row_idx, col_fotos, link_fotos)
                    logger.info(f"link_fotos_drive atualizado para ASN {asn_id}")
                    return True, "Link de fotos salvo com sucesso"

            return False, f"ASN {asn_id} não encontrado na planilha"

        except Exception as e:
            logger.error(f"update_link_fotos({asn_id}): {e}")
            return False, str(e)

    # EDIÇÃO (antes do processamento)

    def editar_asn(self, asn_id: str, data: dict, user_email: str) -> Tuple[bool, str]:
        """
        Edita campos da NF/NFD e dados complementares de um ASN ainda não processado.

        Campos editáveis: nota, tipo_nota, notas_adicionais, cte, volumes, descricao_avaria,
        data_prevista (dia e horário de atendimento).
        Restrito a ASNs com status 'agendado'.

        Returns:
            (success, message)
        """
        asn = self.get_by_id(asn_id)
        if not asn:
            return False, f"ASN {asn_id} não encontrado"
        if asn.status != 'agendado':
            return False, f"Apenas ASNs com status 'agendado' podem ser editados (status atual: {asn.status})"

        nota = str(data.get('nota', '') or '').strip()
        if not nota:
            return False, "Número da NF/NFD é obrigatório"

        if 'data_prevista' in data:
            data_prevista = str(data.get('data_prevista', '') or '').strip()
            if not data_prevista:
                return False, "Data/hora prevista de entrega é obrigatória"
            ok, msg = self._validar_data_prevista(data_prevista, asn.empresa_dest, excluir_asn_id=asn_id)
            if not ok:
                return False, msg

        CAMPOS_EDITAVEIS = ('nota', 'tipo_nota', 'notas_adicionais', 'cte', 'volumes', 'descricao_avaria', 'data_prevista')

        try:
            ws = self._worksheet()
            all_values = ws.get_all_values()
            if not all_values:
                return False, "Planilha vazia"
            headers = all_values[0]

            for row_idx, row in enumerate(all_values[1:], start=2):
                if str(row[0]).strip() != asn_id:
                    continue

                for campo in CAMPOS_EDITAVEIS:
                    if campo not in data:
                        continue
                    if campo not in headers:
                        continue
                    col = headers.index(campo) + 1
                    ws.update_cell(row_idx, col, str(data.get(campo, '') or '').strip())

                logger.info(f"ASN {asn_id} editado por {user_email}: {list(data.keys())}")
                return True, f"ASN {asn_id} atualizado com sucesso"

            return False, f"ASN {asn_id} não encontrado na planilha"

        except Exception as e:
            logger.error(f"editar_asn({asn_id}): {e}")
            return False, str(e)

    # CANCELAMENTO

    def cancelar_asn(self, asn_id: str, motivo: str, user_email: str) -> Tuple[bool, str]:
        """Cancela um ASN agendado."""
        asn = self.get_by_id(asn_id)
        if not asn:
            return False, f"ASN {asn_id} não encontrado"
        if asn.status == 'recebido':
            return False, "Não é possível cancelar um ASN já recebido"

        try:
            ws = self._worksheet()
            all_values = ws.get_all_values()
            headers = all_values[0]
            col_status = headers.index('status') + 1
            col_obs    = headers.index('observacao_operador') + 1

            for row_idx, row in enumerate(all_values[1:], start=2):
                if str(row[0]).strip() == asn_id:
                    ws.update_cell(row_idx, col_status, 'cancelado')
                    ws.update_cell(row_idx, col_obs, f"CANCELADO por {user_email}: {motivo}")
                    break

            logger.info(f"ASN {asn_id} cancelado por {user_email}")
            return True, f"ASN {asn_id} cancelado"
        except Exception as e:
            logger.error(f"cancelar_asn({asn_id}): {e}")
            return False, str(e)

    # ESTATÍSTICAS (para cards do dashboard do operador)

    def get_stats(self, empresa: str = '') -> dict:
        """Retorna contadores para os cards do dashboard."""
        todos = self.get_all(empresa=empresa)
        agendados   = [a for a in todos if a.status == 'agendado']
        processados = [a for a in todos if a.status == 'processado']
        parciais    = [a for a in todos if a.status == 'recebido_parcial']
        atrasados   = [a for a in agendados if a.atrasado]
        hoje_str    = datetime.now().strftime('%Y-%m-%d')
        hoje        = [a for a in agendados + processados + parciais if a.data_prevista.startswith(hoje_str)]
        recebidos   = [a for a in todos if a.status == 'recebido']
        return {
            'total':       len(todos),
            'agendados':   len(agendados),
            'processados': len(processados),
            'atrasados':   len(atrasados),
            'hoje':        len(hoje),
            'recebidos':   len(recebidos),
            'cancelados':  len([a for a in todos if a.status in ('cancelado', 'recusado')]),
        }

    # EMAILS

    def _notificar_criacao(self, asn: ASN):
        """
        Notifica gestores WMS sobre novo ASN criado.
        Chamado de forma síncrona mas erros são suprimidos (não crítico).
        """
        if not self.email or not self.email.enabled:
            return

        try:
            # Busca emails dos gestores via config (se disponível)
            gestores = self._get_emails_gestores(asn.empresa_dest)
            if not gestores:
                logger.warning("Nenhum gestor configurado para notificação de ASN")
                return

            data_fmt = asn.data_prevista[:16].replace('T', ' ') if asn.data_prevista else 'Não informada'
            html = f'''<!DOCTYPE html>
<html>
<head><meta charset="UTF-8">
<style>
  body {{ font-family: 'Segoe UI', sans-serif; background: #f4f4f4; margin: 0; padding: 0; }}
  .container {{ max-width: 620px; margin: 20px auto; background: white; border-radius: 8px; overflow: hidden; }}
  .header {{ background: linear-gradient(135deg, #28a745, #1a7a32); color: white; padding: 24px; text-align: center; }}
  .header h1 {{ margin: 0; font-size: 1.4rem; }}
  .header p {{ margin: 6px 0 0; opacity: .85; font-size: .9rem; }}
  .content {{ padding: 24px; }}
  .asn-card {{ background: #f8fff9; border-left: 4px solid #28a745; border-radius: 4px; padding: 16px 20px; margin: 16px 0; }}
  .asn-card table {{ width: 100%; border-collapse: collapse; }}
  .asn-card td {{ padding: 5px 0; font-size: .9rem; }}
  .asn-card td:first-child {{ color: #666; width: 45%; font-weight: 500; }}
  .btn {{ display: inline-block; padding: 12px 28px; background: #28a745; color: white;
          text-decoration: none; border-radius: 6px; font-weight: 600; margin-top: 16px; }}
  .badge {{ display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: .8rem;
            background: #d4edda; color: #155724; font-weight: 600; }}
  .footer {{ padding: 16px 24px; background: #f8f9fa; font-size: .8rem; color: #666; text-align: center; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>🚛 Novo Agendamento ASN</h1>
    <p><span class="badge" style="background:rgba(255,255,255,.2);color:white">{asn.id}</span></p>
  </div>
  <div class="content">
    <p>Um novo agendamento de devolução/avaria foi criado pela transportadora:</p>
    <div class="asn-card">
      <table>
        <tr><td>Transportadora</td><td><strong>{asn.transportadora}</strong></td></tr>
        <tr><td>Tipo / Nota</td><td><strong>{asn.tipo_nota} {asn.nota}</strong></td></tr>
        <tr><td>CT-e</td><td>{asn.cte or '—'}</td></tr>
        <tr><td>Volumes</td><td>{asn.volumes or '—'}</td></tr>
        <tr><td>Motorista</td><td>{asn.nome_motorista or '—'}</td></tr>
        <tr><td>Placa</td><td>{asn.placa_veiculo or '—'}</td></tr>
        <tr><td>Previsão de Entrega</td><td><strong>{data_fmt}</strong></td></tr>
        <tr><td>Observação</td><td>{asn.descricao_avaria or '—'}</td></tr>
      </table>
    </div>
    <a class="btn" href="{self.base_url}/wms/asn/fila">Ver Fila de ASNs</a>
  </div>
  <div class="footer">WMS MultiSoluções — Notificação automática</div>
</div>
</body>
</html>'''

            self.email.send_email(
                to_addresses=gestores,
                subject=f'🚛 ASN {asn.id} — {asn.transportadora} | Previsão: {data_fmt}',
                html_body=html,
            )
            logger.info(f"Email de criação de ASN enviado para {gestores}")
        except Exception as e:
            logger.error(f"_notificar_criacao: {e}")

    def _notificar_recebimento(self, asn: ASN, avaria_id: Optional[str]):
        """Notifica a transportadora em segundo plano (SMTP não deve atrasar a resposta ao operador)."""
        if not self.email or not self.email.enabled or not asn.email_transportadora:
            return
        import threading
        threading.Thread(
            target=self._notificar_recebimento_sync, args=(asn, avaria_id), daemon=True,
        ).start()

    def _notificar_recebimento_sync(self, asn: ASN, avaria_id: Optional[str]):
        """Notifica a transportadora que o ASN foi recebido."""
        if not self.email or not self.email.enabled:
            return
        if not asn.email_transportadora:
            return
        try:
            avaria_info = f'Avaria gerada: <strong>{avaria_id}</strong>' if avaria_id else 'Avaria será registrada manualmente.'
            html = f'''<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
  body {{ font-family: 'Segoe UI', sans-serif; background: #f4f4f4; }}
  .container {{ max-width: 580px; margin: 20px auto; background: white; border-radius: 8px; overflow: hidden; }}
  .header {{ background: linear-gradient(135deg, #28a745, #1a7a32); color: white; padding: 24px; text-align: center; }}
  .content {{ padding: 24px; }}
  .success-box {{ background: #d4edda; border: 1px solid #c3e6cb; border-radius: 6px; padding: 16px; margin: 16px 0; text-align: center; }}
  .success-box .check {{ font-size: 2.5rem; }}
  .detail-row {{ display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #f0f0f0; font-size: .9rem; }}
  .footer {{ padding: 16px; background: #f8f9fa; font-size: .8rem; color: #666; text-align: center; }}
</style>
</head>
<body>
<div class="container">
  <div class="header"><h1>Entrega Recebida</h1><p>{asn.id}</p></div>
  <div class="content">
    <div class="success-box">
      <div class="check"></div>
      <strong>Sua entrega foi recebida com sucesso!</strong>
    </div>
    <div class="detail-row"><span>Agendamento</span><strong>{asn.id}</strong></div>
    <div class="detail-row"><span>Nota/NFD</span><strong>{asn.tipo_nota} {asn.nota}</strong></div>
    <div class="detail-row"><span>CT-e</span><span>{asn.cte or '—'}</span></div>
    <div class="detail-row"><span>Recebido em</span><span>{self._now()[:16]}</span></div>
    <p style="margin-top:16px;font-size:.9rem;color:#555">{avaria_info}</p>
  </div>
  <div class="footer">WMS MultiSoluções</div>
</div>
</body>
</html>'''
            self.email.send_email(
                to_addresses=[asn.email_transportadora],
                subject=f'Entrega recebida — {asn.id} ({asn.tipo_nota} {asn.nota})',
                html_body=html,
            )
        except Exception as e:
            logger.error(f"_notificar_recebimento: {e}")

    def _get_emails_gestores(self, empresa: str) -> List[str]:
        """
        Busca emails dos gestores WMS para notificação.
        Tenta: (1) config WMS_Config, (2) usuários com nivel=gestor na empresa.
        """
        gestores = []
        try:
            if self.sheets and self.sheets.connected:
                users = self.sheets.get_all_users()
                gestores = [
                    u.email for u in users
                    if u.ativo == 'sim'
                    and u.empresa == empresa
                    and u.nivel in ('gestor', 'admin_empresa', 'admin_global', 'admin')
                    and 'wms' in (u.permissions.get('modules', []) if u.permissions else [])
                ]
        except Exception as e:
            logger.warning(f"_get_emails_gestores: {e}")
        return gestores

    def recusar_asn(
        self,
        asn_id: str,
        motivo: str,
        user_email: str,
        avaria_id: str = '',
        empresa: str = '',
        notas_recusadas: list = None,
    ) -> Tuple[bool, str]:
        """\"\"\"
        Recusa um ASN agendado pelo operador WMS.

        Diferente do cancelamento (que pode ser feito pela transportadora),
        a recusa é uma ação do operador que indica que a mercadoria
        chegou mas foi rejeitada no recebimento.

        Status resultante: 'recusado'
        Trigger automação: 'asn_recusado'
        \"\"\""""
        asn = self.get_by_id(asn_id)
        if not asn:
            return False, f"ASN {asn_id} não encontrado"
        if asn.status == 'recebido':
            return False, "Não é possível recusar um ASN já recebido"
        if asn.status == 'cancelado':
            return False, "ASN já cancelado — use recusar somente para ASNs agendados"

        # Normalizar notas recusadas: remover prefixo "TIPO:" se presente (ex: "NF:12345" → "12345")
        def _num_nota(raw: str) -> str:
            s = str(raw).strip()
            return s.split(':', 1)[1].strip() if ':' in s else s

        notas_recusadas_set = {_num_nota(n) for n in (notas_recusadas or []) if n}
        todas_notas = self._listar_notas_asn(asn)
        todas_notas_nums = {n for _, n in todas_notas}

        eh_recusa_parcial = (
            bool(notas_recusadas_set)
            and len(todas_notas) > 1
            and notas_recusadas_set != todas_notas_nums
        )

        # Notas que NÃO foram recusadas (serão consideradas recebidas)
        notas_recebidas = todas_notas_nums - notas_recusadas_set if eh_recusa_parcial else set()

        # Status resultante do ASN
        novo_status_asn = 'recebido' if eh_recusa_parcial else 'recusado'

        # Incorporar notas recusadas no motivo para rastreabilidade
        motivo_completo = motivo
        if notas_recusadas_set:
            notas_str = ', '.join(sorted(notas_recusadas_set))
            motivo_completo = f"[Notas recusadas: {notas_str}] {motivo}"
            if notas_recebidas:
                recebidas_str = ', '.join(sorted(notas_recebidas))
                motivo_completo += f" [Notas recebidas: {recebidas_str}]"

        # ── Se o ASN ainda não passou pela Etapa 2 nem foi recebido antes, ainda não
        # existe avaria — cria uma POR NOTA agora. Sem isso, a recusa "direta" (primeiro
        # contato com a nota, sem Processar/Receber antes) não tem avaria_id para
        # etiquetar/rotear e o trigger de automação abaixo é pulado silenciosamente.
        avaria_id_efetivo = (avaria_id or asn.avaria_id or '').strip()
        avarias_criadas_agora = False
        if not avaria_id_efetivo and self.wms:
            avaria_ids_criadas = self._criar_avarias_por_nota(asn, asn.cod_empresa, user_email)
            if avaria_ids_criadas:
                avaria_id_efetivo = ','.join(avaria_ids_criadas)
                avarias_criadas_agora = True
                logger.info(f"Avaria(s) {avaria_id_efetivo} criada(s) a partir do ASN {asn_id} na recusa")
            else:
                logger.warning(f"Nenhuma avaria criada para o ASN {asn_id} na recusa")

        try:
            ws = self._worksheet()
            all_values = ws.get_all_values()
            headers = all_values[0]

            col_status       = headers.index('status') + 1
            col_recusado_por = headers.index('recusado_por') + 1 if 'recusado_por' in headers else None
            col_recusado_em  = headers.index('recusado_em')  + 1 if 'recusado_em'  in headers else None
            col_motivo       = headers.index('motivo_recusa') + 1 if 'motivo_recusa' in headers else None
            col_obs          = headers.index('observacao_operador') + 1
            col_rec_por      = headers.index('recebido_por') + 1 if 'recebido_por' in headers else None
            col_rec_em       = headers.index('recebido_em')  + 1 if 'recebido_em'  in headers else None
            col_avaria       = headers.index('avaria_id') + 1 if 'avaria_id' in headers else None

            obs_txt = (
                f"RECUSA PARCIAL por {user_email}: {motivo_completo}"
                if eh_recusa_parcial
                else f"RECUSADO por {user_email}: {motivo_completo}"
            )

            for row_idx, row in enumerate(all_values[1:], start=2):
                if str(row[0]).strip() == asn_id:
                    ws.update_cell(row_idx, col_status, novo_status_asn)
                    if col_recusado_por:
                        ws.update_cell(row_idx, col_recusado_por, user_email)
                    if col_recusado_em:
                        ws.update_cell(row_idx, col_recusado_em, self._now())
                    if col_motivo:
                        ws.update_cell(row_idx, col_motivo, motivo_completo)
                    ws.update_cell(row_idx, col_obs, obs_txt)
                    # Para recusa parcial, registrar também como recebido
                    if eh_recusa_parcial:
                        if col_rec_por:
                            ws.update_cell(row_idx, col_rec_por, user_email)
                        if col_rec_em:
                            ws.update_cell(row_idx, col_rec_em, self._now())
                    if avarias_criadas_agora and col_avaria:
                        ws.update_cell(row_idx, col_avaria, avaria_id_efetivo)
                    break

            if eh_recusa_parcial:
                logger.info(
                    f"⚠️ ASN {asn_id} recusa parcial por {user_email}: "
                    f"recusadas={notas_recusadas_set}, recebidas={notas_recebidas}"
                )
            else:
                logger.info(f"ASN {asn_id} recusado por {user_email}: {motivo_completo}")

            # ── Gerar etiquetas iniciais por avaria ────────────────────────────
            # Avarias recusadas → roteamento 497 (todos_recusados=True)
            # Avarias recebidas (recusa parcial de notas) → linha padrão (403)
            if self.etiqueta and avaria_id_efetivo:
                empresa_real_etq = empresa or asn.empresa_dest
                av_ids_etq = [av.strip() for av in str(avaria_id_efetivo).split(',') if av.strip()]
                for av_id in av_ids_etq:
                    try:
                        trigger_av = self._determinar_trigger_avaria(
                            av_id, notas_recusadas_set, empresa_real_etq
                        )
                        av_recusada = (trigger_av == 'asn_recusado')
                        sucesso_etq, etqs, msg_etq, _ = self.etiqueta.criar_etiquetas_iniciais(
                            avaria_id=av_id,
                            operador=user_email,
                            empresa=empresa_real_etq,
                            todos_recusados=av_recusada,
                        )
                        if sucesso_etq:
                            destino = '497' if av_recusada else 'padrão'
                            logger.info(
                                f"🏷️ {len(etqs)} etiqueta(s) gerada(s) para avaria {av_id} "
                                f"({'recusada → ' + destino if av_recusada else 'recebida → ' + destino})"
                            )
                        else:
                            logger.warning(f"Etiquetas não geradas para avaria {av_id}: {msg_etq}")
                    except Exception as etq_err:
                        logger.warning(
                            f"⚠️ Etiquetas para avaria {av_id} na recusa ignoradas: {etq_err}"
                        )

            # ── Disparar automação correta por avaria ──────────────────────────
            # Cada avaria recebe seu trigger conforme a nota: asn_recusado ou asn_recebido
            if self.automacao and avaria_id_efetivo:
                empresa_real = empresa or asn.empresa_dest
                av_ids = [av.strip() for av in str(avaria_id_efetivo).split(',') if av.strip()]
                for av_id in av_ids:
                    trigger = self._determinar_trigger_avaria(
                        av_id, notas_recusadas_set, empresa_real
                    )
                    try:
                        self.automacao.aplicar(
                            trigger=trigger,
                            avaria_id=av_id,
                            empresa=empresa_real,
                            operador_email=user_email,
                        )
                        logger.info(f"Trigger '{trigger}' disparado para avaria {av_id}")
                    except Exception as auto_err:
                        logger.warning(
                            f"⚠️ Automação '{trigger}' para avaria {av_id} ignorada: {auto_err}"
                        )

            # ── Notificar transportadora sobre recusa ──
            self._notificar_recusa(asn, motivo)

            msg = (
                f"ASN {asn_id} processado: {len(notas_recusadas_set)} nota(s) recusada(s), "
                f"{len(notas_recebidas)} nota(s) recebida(s)"
                if eh_recusa_parcial
                else f"ASN {asn_id} recusado"
            )
            return True, msg

        except Exception as e:
            logger.error(f"recusar_asn({asn_id}): {e}")
            return False, str(e)

    def _notificar_recusa(self, asn, motivo: str):
        """\"\"\"Notifica a transportadora que o ASN foi recusado.\"\"\""""
        if not self.email or not self.email.enabled:
            return
        try:
            html = f'''<!DOCTYPE html>
<html>
<head><meta charset="UTF-8">
<style>
  body {{ font-family: 'Segoe UI', sans-serif; background: #f4f4f4; }}
  .container {{ max-width: 620px; margin: 20px auto; background: white; border-radius: 8px; overflow: hidden; }}
  .header {{ background: linear-gradient(135deg, #dc3545, #a71d2a); color: white; padding: 24px; text-align: center; }}
  .header h1 {{ margin: 0; font-size: 1.4rem; }}
  .content {{ padding: 24px; }}
  .card {{ background: #fff5f5; border-left: 4px solid #dc3545; border-radius: 4px; padding: 16px; margin: 16px 0; }}
  .footer {{ padding: 16px 24px; background: #f8f9fa; font-size: .8rem; color: #666; text-align: center; }}
</style>
</head>
<body>
<div class="container">
  <div class="header"><h1>❌ ASN Recusado no CD</h1></div>
  <div class="content">
    <p>O seu agendamento <strong>{asn.id}</strong> foi recusado pelo operador do CD.</p>
    <div class="card">
      <table style="width:100%;border-collapse:collapse;font-size:.9rem">
        <tr><td style="color:#666;width:40%">ASN</td><td><strong>{asn.id}</strong></td></tr>
        <tr><td style="color:#666">Nota</td><td>{asn.nota}</td></tr>
        <tr><td style="color:#666">Motivo da recusa</td><td><strong>{motivo}</strong></td></tr>
      </table>
    </div>
    <p>Entre em contato com a equipe do CD para mais informações.</p>
  </div>
  <div class="footer">MultiSoluções WMS — Notificação automática</div>
</div>
</body></html>'''

            self.email.send(
                to=asn.email_transportadora,
                subject=f'[ASN Recusado] {asn.id} — {asn.nota}',
                html_body=html,
            )
        except Exception as e:
            logger.warning(f"_notificar_recusa: {e}")

