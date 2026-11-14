"""
modules/wms/models/asn.py
Modelo de dados para ASN (Advanced Shipping Notice) — Portal de Transportadoras.

Um ASN representa o pré-agendamento que a transportadora faz antes de entregar
uma devolução/avaria no CD. Contém os dados que serão usados para pré-popular
o formulário de registro de avaria quando a carga chegar.

Ciclo de vida:
  agendado → recebido (vinculado a avaria)
             cancelado
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ASN:
    """Advanced Shipping Notice — pré-agendamento de devolução pela transportadora."""

    # ── Identificação ─────────────────────────────────────────────────────
    id: str = ''                     # ASN-YYYYMMDD-NNNN
    token: str = ''                  # UUID para link direto (reservado para futuros links mágicos)

    # ── Dados da Transportadora ───────────────────────────────────────────
    transportadora: str = ''         # Nome da transportadora
    email_transportadora: str = ''   # Email do usuário que criou o ASN
    empresa_dest: str = ''           # Empresa destinatária (Matriz, Empresax, EmpresaY)

    # ── Dados da Carga ────────────────────────────────────────────────────
    nota: str = ''                   # NF ou NFD (nota principal)
    tipo_nota: str = 'NF'            # 'NF' | 'NFD'
    notas_adicionais: str = ''       # Notas extras separadas por vírgula (ex: "NFD:789,NF:456")
    cte: str = ''                    # Número do CT-e
    volumes: str = ''                # Quantidade de volumes
    descricao_avaria: str = ''       # Descrição prévia do dano/motivo da devolução

    # ── Logística ─────────────────────────────────────────────────────────
    data_prevista: str = ''          # Data/hora prevista de entrega (YYYY-MM-DD HH:MM)
    nome_motorista: str = ''         # Nome do motorista
    placa_veiculo: str = ''          # Placa do veículo

    # ── Status ────────────────────────────────────────────────────────────
    status: str = 'agendado'         # agendado | processado | recebido | cancelado

    # ── Vinculação com Avaria ─────────────────────────────────────────────
    avaria_id: str = ''              # ID(s) da(s) avaria(s) criadas (separadas por vírgula)
    cod_empresa: str = ''            # Código da empresa no Sankhya (preenchido na Etapa 2)
    recebido_por: str = ''           # Email do operador que recebeu
    recebido_em: str = ''            # Timestamp do recebimento
    notas_recebidas_parcial: str = ''  # Notas já recebidas em recebimento parcial anterior

    # ── Auditoria ─────────────────────────────────────────────────────────
    criado_em: str = ''
    criado_por: str = ''             # Email do usuário transportadora
    observacao_operador: str = ''    # Observação do operador ao receber
    link_fotos_drive: str = ''       # Link da pasta no Google Drive com fotos do recebimento

    # ── Campos calculados (não persistidos) ───────────────────────────────
    dias_ate_entrega: Optional[int] = field(default=None, repr=False)
    atrasado: bool = field(default=False, repr=False)

    # MÉTODOS

    def __post_init__(self):
        """Calcula campos derivados após inicialização."""
        self._calcular_prazo()

    def _calcular_prazo(self):
        """Calcula dias até a entrega e se está atrasado."""
        if not self.data_prevista:
            return
        try:
            for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
                try:
                    dt_prev = datetime.strptime(self.data_prevista[:16], fmt[:len(fmt)])
                    hoje = datetime.now()
                    diff = (dt_prev.date() - hoje.date()).days
                    self.dias_ate_entrega = diff
                    self.atrasado = diff < 0 and self.status == 'agendado'
                    break
                except ValueError:
                    continue
        except Exception:
            pass

    @property
    def status_badge(self) -> str:
        """Retorna classe Bootstrap para o badge de status."""
        return {
            'agendado':         'warning',
            'processado':       'primary',
            'recebido':         'success',
            'recebido_parcial': 'info',
            'cancelado':        'secondary',
            'recusado':         'danger',
        }.get(self.status, 'secondary')

    @property
    def status_label(self) -> str:
        return {
            'agendado':         'Agendado',
            'processado':       'Processado',
            'recebido':         'Recebido',
            'recebido_parcial': 'Receb. Parcial',
            'cancelado':        'Cancelado',
            'recusado':         'Recusado',
        }.get(self.status, self.status.title())

    @property
    def tipo_nota_badge(self) -> str:
        return 'info' if self.tipo_nota == 'NFD' else 'primary'

    def to_dict(self) -> dict:
        return {
            'id':                   self.id,
            'token':                self.token,
            'transportadora':       self.transportadora,
            'email_transportadora': self.email_transportadora,
            'empresa_dest':         self.empresa_dest,
            'nota':                 self.nota,
            'tipo_nota':            self.tipo_nota,
            'notas_adicionais':     self.notas_adicionais,
            'cte':                  self.cte,
            'cod_empresa':          self.cod_empresa,
            'volumes':              self.volumes,
            'descricao_avaria':     self.descricao_avaria,
            'data_prevista':        self.data_prevista,
            'nome_motorista':       self.nome_motorista,
            'placa_veiculo':        self.placa_veiculo,
            'status':                  self.status,
            'avaria_id':               self.avaria_id,
            'recebido_por':            self.recebido_por,
            'recebido_em':             self.recebido_em,
            'notas_recebidas_parcial': self.notas_recebidas_parcial,
            'criado_em':               self.criado_em,
            'criado_por':              self.criado_por,
            'observacao_operador':     self.observacao_operador,
            'link_fotos_drive':        self.link_fotos_drive,
            # calculados (apenas para API)
            'status_badge':         self.status_badge,
            'status_label':         self.status_label,
            'dias_ate_entrega':     self.dias_ate_entrega,
            'atrasado':             self.atrasado,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'ASN':
        """Cria instância a partir de dict (linha do Sheets)."""
        return cls(
            id=str(data.get('id', '')).strip(),
            token=str(data.get('token', '')).strip(),
            transportadora=str(data.get('transportadora', '')).strip(),
            email_transportadora=str(data.get('email_transportadora', '')).strip(),
            empresa_dest=str(data.get('empresa_dest', '')).strip(),
            nota=str(data.get('nota', '')).strip(),
            tipo_nota=str(data.get('tipo_nota', 'NF')).strip() or 'NF',
            notas_adicionais=str(data.get('notas_adicionais', '')).strip(),
            cte=str(data.get('cte', '')).strip(),
            cod_empresa=str(data.get('cod_empresa', '')).strip(),
            volumes=str(data.get('volumes', '')).strip(),
            descricao_avaria=str(data.get('descricao_avaria', '')).strip(),
            data_prevista=str(data.get('data_prevista', '')).strip(),
            nome_motorista=str(data.get('nome_motorista', '')).strip(),
            placa_veiculo=str(data.get('placa_veiculo', '')).strip(),
            status=str(data.get('status', 'agendado')).strip() or 'agendado',
            avaria_id=str(data.get('avaria_id', '')).strip(),
            recebido_por=str(data.get('recebido_por', '')).strip(),
            recebido_em=str(data.get('recebido_em', '')).strip(),
            notas_recebidas_parcial=str(data.get('notas_recebidas_parcial', '')).strip(),
            criado_em=str(data.get('criado_em', '')).strip(),
            criado_por=str(data.get('criado_por', '')).strip(),
            observacao_operador=str(data.get('observacao_operador', '')).strip(),
            link_fotos_drive=str(data.get('link_fotos_drive', '')).strip(),
        )
