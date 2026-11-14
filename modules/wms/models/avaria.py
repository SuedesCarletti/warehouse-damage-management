from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import random
import math

@dataclass
class Avaria:
    # --- Dados Iniciais ---
    id: str = field(default_factory=lambda: f"00-{datetime.now().strftime('%y%m%d')}-{random.randint(1000, 9999)}")
    cod_empresa: str = ""
    nota: str = ""  # NF de Venda
    nfd: str = ""   # NFD Específica
    quantidade: str = ""
    volume: str = ""
    transportadora: str = ""
    cte: str = ""
    
    # Campos financeiros
    valor_nf: str = ""  # Valor da NF original
    valor_nfd: str = "" # Valor da NFD
    nfp: str = ""  # Nota Fiscal de Produto
    valor_nfp: str = ""  # Valor da NFP
    
    # Campos financeiros e devolução
    motivo_devolucao: str = ""
    cod_motivo_devolucao: str = ""
    motivo_inicial: str = ""
    cod_motivo_inicial: str = ""
    data_protocolo_mkt: str = ""
    indenizacao: str = "" # Status/Tipo: Sim/Não/Pendente
    
    valor_indenizacao: Optional[str] = None
    
    prazo_pagamento: str = ""
    data_pagamento: str = ""
    data_entrega: str = ""

    # Campos logísticos (Registro)
    data_coleta: str = ""
    cod_rastreio_correios: str = ""
    protocolo_insucesso: str = ""
    
    observacao: str = ""
    data_registro: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # Campos de status
    status_estoque: str = "Pendente"
    status_indenizacao: str = "Pendente"
    
    registrado_por: str = ""
    empresa: str = ""  # Empresa do usuário (Matriz, Empresax, EmpresaY)
    codparc: str = ""  # Código do parceiro (CODPARC do Sankhya)
    
    etiqueta_inicial_impressa: str = 'nao'
    local: str = ""  # Codlocal do local atual do item (definido na triagem)

    # Dados pós-triagem
    cod_triagem: Optional[str] = None
    reembolso_pct: Optional[float] = None
    observacao_final: Optional[str] = None
    data_finalizacao: Optional[str] = None
    finalizado_por: Optional[str] = None
    data_alteracao: str = ""
    alterado_por: str = ""

    def __post_init__(self):
        """
        Valida e ajusta dados após inicialização
        CORRIGIDO: Trata corretamente valores float/int vindos de Excel/CSV
        """
        # Função auxiliar para converter valores com segurança
        def safe_string_convert(valor) -> str:
            """Converte valor para string tratando NaN, None, etc"""
            if valor is None:
                return ""
            if isinstance(valor, float):
                # Verificar se é NaN
                if math.isnan(valor):
                    return ""
                # Se for inteiro (ex: 123.0), retornar sem decimais
                if valor == int(valor):
                    return str(int(valor))
                return str(valor)
            if isinstance(valor, int):
                return str(valor)
            # String
            valor_str = str(valor).strip()
            if valor_str.lower() in ['nan', 'none', '']: 
                return ""
            return valor_str
        
        # 1. Validar e ajustar volume (usar quantidade se vazio)
        volume_str = safe_string_convert(self.volume)
        if not volume_str:
            self.volume = safe_string_convert(self.quantidade)
        else:
            self.volume = volume_str
        
        # 2. Normalizar todos os campos de texto
        # Garantir que campos sejam strings vazias em vez de None, NaN, etc
        self.cod_empresa = safe_string_convert(self.cod_empresa)
        self.nota = safe_string_convert(self.nota)
        self.nfd = safe_string_convert(self.nfd)
        self.quantidade = safe_string_convert(self.quantidade)
        self.transportadora = safe_string_convert(self.transportadora)
        self.cte = safe_string_convert(self.cte)

        # Campos financeiros
        self.valor_nf = safe_string_convert(self.valor_nf)
        self.valor_nfd = safe_string_convert(self.valor_nfd)
        self.nfp = safe_string_convert(self.nfp)
        self.valor_nfp = safe_string_convert(self.valor_nfp)
        
        # Campos de devolução
        self.motivo_devolucao = safe_string_convert(self.motivo_devolucao)
        self.cod_motivo_devolucao = safe_string_convert(self.cod_motivo_devolucao)
        self.motivo_inicial = safe_string_convert(self.motivo_inicial)
        self.cod_motivo_inicial = safe_string_convert(self.cod_motivo_inicial)        
        # Normalizar
        self.data_protocolo_mkt = safe_string_convert(self.data_protocolo_mkt)
        
        # Campos de indenização
        self.indenizacao = Avaria._normalizar_indenizacao(
            safe_string_convert(self.indenizacao)
        )
        self.valor_indenizacao = safe_string_convert(self.valor_indenizacao)
        self.prazo_pagamento = safe_string_convert(self.prazo_pagamento)
        self.data_pagamento = safe_string_convert(self.data_pagamento)
        self.data_entrega = safe_string_convert(self.data_entrega)
        
        # Campos logísticos
        self.data_coleta = safe_string_convert(self.data_coleta)
        self.cod_rastreio_correios = safe_string_convert(self.cod_rastreio_correios)
        self.protocolo_insucesso = safe_string_convert(self.protocolo_insucesso)
        
        # Outros campos
        self.observacao = safe_string_convert(self.observacao)
        self.registrado_por = safe_string_convert(self.registrado_por)
        self.empresa = safe_string_convert(self.empresa)
        self.codparc = safe_string_convert(self.codparc)
        self.local = safe_string_convert(self.local)

    @property
    def quantidade_formatada(self) -> str:
        """Retorna a quantidade formatada com vírgula se necessário"""
        if not self.quantidade:
            return "0"
        return self.quantidade.replace('.', ',')

    @property
    def volume_formatada(self) -> str:
        """Retorna o volume formatado com vírgula se necessário"""
        if not self.volume:
            return "0"
        return self.volume.replace('.', ',')

    @property
    def is_finalizada(self) -> bool:
        """Verifica se avaria está finalizada"""
        return self.status_estoque in ["Finalizado", "Estoque Disponível", "Estoque Descarte"] and \
               self.status_indenizacao in ["Finalizado", "Indenização Finalizada"]
    
    @property
    def valor_nf_formatado(self) -> str:
        """Retorna o valor da NF formatado"""
        if not self.valor_nf:
            return "R$ 0,00"
        try:
            valor = float(self.valor_nf.replace(',', '.'))
            return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except (ValueError, TypeError):
            return "R$ 0,00"

    @property
    def valor_nfd_formatado(self) -> str:
        """Retorna o valor da NFD formatado"""
        if not self.valor_nfd:
            return "R$ 0,00"
        try:
            valor = float(self.valor_nfd.replace(',', '.'))
            return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except (ValueError, TypeError):
            return "R$ 0,00"
    
    @property
    def valor_indenizacao_formatado(self) -> str:
        """Retorna o Valor Indenização formatado"""
        if not self.valor_indenizacao:
            return "R$ 0,00"
        try:
            valor = float(str(self.valor_indenizacao).replace(',', '.'))
            return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except (ValueError, TypeError):
            return "R$ 0,00"

    @property
    def reembolso_pct_str(self) -> str:
        """Retorna o percentual de reembolso como string formatada para templates."""
        if self.reembolso_pct is None:
            return ""
        # Retorna o float como string com vírgula (ex: "5,0")
        return str(self.reembolso_pct).replace('.', ',')

    def to_dict(self) -> dict:
        """
        Converte Avaria para dicionário (ORDEM EXATA conforme Google Sheets)
        
        ⚠️ IMPORTANTE: A ordem dos campos DEVE corresponder às colunas da planilha
        """
        def _clean_float(v):
            if v is None or v == '':
                return 0.0
            if isinstance(v, (int, float)):
                return float(v)
            # String handling (reusing logic from _parse_valor_item effectively)
            s = str(v).replace('R$', '').replace(' ', '').strip()
            if not s:
                return 0.0
            
            # Se já é formato US com ponto e sem vírgula
            if ',' not in s:
                try: return float(s)
                except: pass

            tem_virgula = ',' in s
            tem_ponto = '.' in s
            if tem_virgula and tem_ponto:
                if s.rfind(',') > s.rfind('.'): # BR: 1.234,56
                    s = s.replace('.', '').replace(',', '.')
                else: # US: 1,234.56
                    s = s.replace(',', '')
            elif tem_virgula: # BR simples: 529,52
                s = s.replace(',', '.')
            
            try:
                return float(s)
            except:
                return 0.0

        return {
            'id': self.id,
            'cod_empresa': self.cod_empresa,
            'nota': self.nota,
            'nfd': self.nfd,
            'quantidade': _clean_float(self.quantidade),
            'volume': _clean_float(self.volume),
            'transportadora': self.transportadora,
            'cte': self.cte,
            'valor_nf': _clean_float(self.valor_nf),
            'valor_nfd': _clean_float(self.valor_nfd),
            'nfp': self.nfp,
            'valor_nfp': _clean_float(self.valor_nfp),
            'motivo_devolucao': self.motivo_devolucao,
            'cod_motivo_devolucao': self.cod_motivo_devolucao,
            'motivo_inicial': self.motivo_inicial,
            'data_protocolo_mkt': self.data_protocolo_mkt,
            'cod_motivo_inicial': self.cod_motivo_inicial,
            'indenizacao': self.indenizacao,
            'valor_indenizacao': _clean_float(self.valor_indenizacao),
            'prazo_pagamento': self.prazo_pagamento,
            'data_pagamento': self.data_pagamento,
            'data_entrega': self.data_entrega,
            'data_coleta': self.data_coleta,
            'cod_rastreio_correios': self.cod_rastreio_correios,
            'observacao': self.observacao,
            'data_registro': self.data_registro,
            'status_estoque': self.status_estoque,
            'status_indenizacao': self.status_indenizacao,
            'registrado_por': self.registrado_por,
            'empresa': self.empresa,
            'codparc': self.codparc or '',
            'etiqueta_inicial_impressa': self.etiqueta_inicial_impressa,
            'local': self.local,
            'cod_triagem': self.cod_triagem or '',
            'reembolso_pct': _clean_float(self.reembolso_pct),
            'observacao_final': self.observacao_final or '',
            'data_finalizacao': self.data_finalizacao or '',
            'finalizado_por': self.finalizado_por or '',
            'protocolo_insucesso': self.protocolo_insucesso or '',
            'data_alteracao': self.data_alteracao or '',
            'alterado_por': self.alterado_por or '',
        }

    def to_row(self) -> list:
        """
        Converte para lista para o Google Sheets.
        Campos monetários são emitidos como string '532.31' (ponto decimal)
        para que o Sheets armazene como texto e não reformate via locale PT-BR.
        """
        d = self.to_dict()
        for campo in ('valor_nf', 'valor_nfd', 'valor_nfp', 'valor_indenizacao', 'reembolso_pct'):
            v = d.get(campo)
            if v is not None and v != '' and v != 0.0:
                try:
                    d[campo] = f"{float(v):.2f}"
                except (ValueError, TypeError):
                    pass
        return list(d.values())

    @classmethod
    def from_dict(cls, data: dict) -> 'Avaria':
        """
        Cria avaria a partir de dicionário
        CORRIGIDO: Trata valores float/int do pandas/Excel corretamente
        """
        
        def safe_str(key, default=''):
            """
            Extrai valor do dicionário e converte para string com segurança
            CORRIGIDO: Trata float/int/NaN corretamente
            """
            val = data.get(key)
            
            # Tratar None
            if val is None:
                return default
            
            # Tratar float
            if isinstance(val, float):
                # Verificar NaN
                if math.isnan(val):
                    return default
                # Se for inteiro (ex: 123.0), retornar sem decimais
                if val == int(val):
                    return str(int(val))
                return str(val)
            
            # Tratar int
            if isinstance(val, int):
                return str(val)
            
            # Tratar string
            val_str = str(val).strip()
            if val_str.lower() in ['nan', 'none', '']: 
                return default
            
            return val_str
            
        safe_data = {
            'id': safe_str('id') or safe_str('avaria_id', ''),  # COMPATIBILIDADE
            'cod_empresa': safe_str('cod_empresa', ''),
            'nota': safe_str('nota') or safe_str('nf_venda', ''),
            'nfd': safe_str('nfd', ''),
            'quantidade': safe_str('quantidade', ''),
            'volume': safe_str('volume', ''),
            'transportadora': safe_str('transportadora', ''),
            'cte': safe_str('cte', ''),
            'valor_nf': safe_str('valor_nf', ''),
            'valor_nfd': safe_str('valor_nfd', ''),
            'nfp': safe_str('nfp', ''),
            'valor_nfp': safe_str('valor_nfp', ''),
            'motivo_devolucao': safe_str('motivo_devolucao', ''),
            'cod_motivo_devolucao': safe_str('cod_motivo_devolucao', ''),
            'motivo_inicial': safe_str('motivo_inicial', ''),
            'cod_motivo_inicial': safe_str('cod_motivo_inicial', ''),
            'data_protocolo_mkt': safe_str('data_protocolo_mkt', ''),
            'indenizacao': safe_str('indenizacao', ''),
            'valor_indenizacao': safe_str('valor_indenizacao', ''),
            'prazo_pagamento': safe_str('prazo_pagamento', ''),
            'data_pagamento': safe_str('data_pagamento', ''),
            'data_entrega': safe_str('data_entrega', ''), 
            'data_coleta': safe_str('data_coleta', ''), 
            'cod_rastreio_correios': safe_str('cod_rastreio_correios', ''),
            'observacao': safe_str('observacao') or safe_str('observacao_inicial', ''),
            'data_registro': safe_str('data_registro', ''),
            'status_estoque': safe_str('status_estoque') or 'Pendente',
            'status_indenizacao': safe_str('status_indenizacao') or 'Pendente',
            'registrado_por': safe_str('registrado_por', ''),
            'empresa': safe_str('empresa', ''),
            'codparc': safe_str('codparc', ''),
            'etiqueta_inicial_impressa': safe_str('etiqueta_inicial_impressa', 'nao'),
            'local': safe_str('local', ''),
            'cod_triagem': safe_str('cod_triagem') or None,
            'reembolso_pct': data.get('reembolso_pct'),
            'observacao_final': safe_str('observacao_final') or None,
            'data_finalizacao': safe_str('data_finalizacao') or None,
            'finalizado_por': safe_str('finalizado_por', ''),
            'data_alteracao': safe_str('data_alteracao', ''),
            'alterado_por': safe_str('alterado_por', ''),
            'protocolo_insucesso': safe_str('protocolo_insucesso', ''),
        }
        
        # Converter reembolso_pct para float
        pct_val = safe_data['reembolso_pct']
        if pct_val is not None:
            try:
                if isinstance(pct_val, str):
                    pct_val = pct_val.strip()
                    if pct_val:
                        safe_data['reembolso_pct'] = float(pct_val)
                    else:
                        safe_data['reembolso_pct'] = None
                elif isinstance(pct_val, (int, float)) and not math.isnan(pct_val):
                    safe_data['reembolso_pct'] = float(pct_val)
                else:
                    safe_data['reembolso_pct'] = None
            except (ValueError, TypeError):
                safe_data['reembolso_pct'] = None
        
        return cls(**safe_data)
        
    @classmethod
    def from_sheet_row(cls, headers: list, row: list) -> 'Avaria':
        """Cria um objeto Avaria a partir de uma linha do Google Sheets"""
        try:
            data = {}
            for i, header in enumerate(headers):
                if i < len(row):
                    data[header] = str(row[i]) if row[i] is not None else ''
                else:
                    data[header] = ''
            
            return cls.from_dict(data)
            
        except Exception as e:
            print(f"Erro ao criar Avaria da linha: {e}")
            print(f"   Headers: {headers}")
            print(f"   Row: {row}")
            return None

    @staticmethod
    def _normalizar_indenizacao(valor: str) -> str:
        """
        Normaliza o campo indenizacao para os valores canônicos:
          'total'           ← Total, Indenização Total, indenizacao total, 1
          'parcial'         ← Parcial, Indenização Parcial, indenizacao parcial
          'nao_indenizavel' ← Não Indenizável, nao indenizavel, 0
          ''                ← vazio / não informado

        Garante consistência independente de qual script salvou o dado.
        """
        if not valor:
            return ''

        v = valor.strip().lower()

        # Variantes de TOTAL
        if v in ('total', 'indenização total', 'indenizacao total',
                 'indenizacaototal', '1', 'sim'):
            return 'total'

        # Variantes de PARCIAL
        if v in ('parcial', 'indenização parcial', 'indenizacao parcial',
                 'indenizacaoparcial'):
            return 'parcial'

        # Variantes de NÃO INDENIZÁVEL
        if v in ('não indenizável', 'nao indenizavel', 'nao_indenizavel',
                 'não indenizavel', 'naoindenizavel', '0', 'nao'):
            return 'nao_indenizavel'

        # Manter valor original se não reconhecido (evitar perda de dados)
        return valor.strip()
