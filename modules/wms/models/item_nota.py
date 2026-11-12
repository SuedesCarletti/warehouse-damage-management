"""
modules/wms/models/item_nota.py
Modelo para representar itens de notas fiscais (TGFITE do Sankhya)
"""

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class ItemNota:
    """
    Representa um item de nota fiscal do ERP Sankhya (tabela TGFITE)
    """
    
    # Campos obrigatórios
    nunota: str = ''                    # Número único da nota
    sequencia: str = ''                 # Sequência do item
    codprod: str = ''                   # Código do produto
    descrprod: str = ''                 # Descrição do produto
    qtdneg: str = ''                    # Quantidade negociada
    vlrunit: str = ''                   # Valor unitário
    vlrtot: str = ''                    # Valor total do item
    
    # Campos opcionais
    codvol: str = ''                    # Código volume/unidade
    codlocalorig: str = ''              # Código local origem
    controle: str = ''                  # Controle (lote, série)
    reserva: str = ''                   # Reserva
    atualestoque: str = ''              # Atualiza estoque (S/N)
    
    # Metadados
    origem: str = 'sankhya'             # Origem dos dados
    data_consulta: str = field(default_factory=lambda: datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    
    def __post_init__(self):
        """Validações e conversões após inicialização"""
        # Garantir que campos numéricos são strings
        self.nunota = str(self.nunota) if self.nunota else ''
        self.sequencia = str(self.sequencia) if self.sequencia else ''
        self.codprod = str(self.codprod) if self.codprod else ''
    
    @property
    def qtdneg_float(self) -> float:
        """Retorna quantidade como float"""
        try:
            return float(self.qtdneg.replace(',', '.'))
        except:
            return 0.0
    
    @property
    def vlrunit_float(self) -> float:
        """Retorna valor unitário como float"""
        try:
            return float(self.vlrunit.replace(',', '.'))
        except:
            return 0.0
    
    @property
    def vlrtot_float(self) -> float:
        """Retorna valor total como float"""
        try:
            return float(self.vlrtot.replace(',', '.'))
        except:
            return 0.0
    
    @property
    def vlrunit_formatado(self) -> str:
        """Retorna valor unitário formatado em reais"""
        try:
            valor = self.vlrunit_float
            return f"R$ {valor:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
        except:
            return "R$ 0,00"
    
    @property
    def vlrtot_formatado(self) -> str:
        """Retorna valor total formatado em reais"""
        try:
            valor = self.vlrtot_float
            return f"R$ {valor:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
        except:
            return "R$ 0,00"
    
    @property
    def qtdneg_formatada(self) -> str:
        """Retorna quantidade formatada com até 2 casas decimais"""
        try:
            qtd = self.qtdneg_float
            # Remove zeros desnecessários
            if qtd == int(qtd):
                return str(int(qtd))
            return f"{qtd:.2f}".rstrip('0').rstrip('.')
        except:
            return "0"
    
    def to_dict(self) -> dict:
        """Converte item para dicionário"""
        return {
            'nunota': self.nunota,
            'sequencia': self.sequencia,
            'codprod': self.codprod,
            'descrprod': self.descrprod,
            'qtdneg': self.qtdneg,
            'vlrunit': self.vlrunit,
            'vlrtot': self.vlrtot,
            'codvol': self.codvol,
            'codlocalorig': self.codlocalorig,
            'controle': self.controle,
            'reserva': self.reserva,
            'atualestoque': self.atualestoque,
            'origem': self.origem,
            'data_consulta': self.data_consulta,
            # Campos calculados
            'qtdneg_formatada': self.qtdneg_formatada,
            'vlrunit_formatado': self.vlrunit_formatado,
            'vlrtot_formatado': self.vlrtot_formatado
        }
    
    @classmethod
    def from_sankhya_dict(cls, data: dict) -> 'ItemNota':
        """
        Cria ItemNota a partir de dicionário retornado do Sankhya
        
        Args:
            data: Dicionário com campos do Sankhya (uppercase)
            
        Returns:
            ItemNota: Objeto criado
        """
        return cls(
            nunota=str(data.get('NUNOTA', '')),
            sequencia=str(data.get('SEQUENCIA', '')),
            codprod=str(data.get('CODPROD', '')),
            descrprod=str(data.get('DESCRPROD', '')),
            qtdneg=str(data.get('QTDNEG', '')),
            vlrunit=str(data.get('VLRUNIT', '')),
            vlrtot=str(data.get('VLRTOT', '')),
            codvol=str(data.get('CODVOL', '')),
            codlocalorig=str(data.get('CODLOCALORIG', '')),
            controle=str(data.get('CONTROLE', '')),
            reserva=str(data.get('RESERVA', '')),
            atualestoque=str(data.get('ATUALESTOQUE', ''))
        )
    
    def __repr__(self) -> str:
        return (f"ItemNota(nunota={self.nunota}, seq={self.sequencia}, "
                f"prod={self.codprod}, qtd={self.qtdneg_formatada})")


@dataclass
class CabecalhoNota:
    """
    Representa o cabeçalho de uma nota fiscal do ERP Sankhya (tabela TGFCAB)
    Classe auxiliar para complementar visualização de itens
    """
    
    nunota: str = ''                    # Número único da nota
    numnota: str = ''                   # Número da nota fiscal
    dtneg: str = ''                     # Data de negociação
    codparc: str = ''                   # Código do parceiro
    nomeparc: str = ''                  # Nome do parceiro
    codtipoper: str = ''                # Código tipo operação
    dhtipoper: str = ''                 # Descrição tipo operação
    vlrnota: str = ''                   # Valor da nota
    observacao: str = ''                # Observações
    statusnota: str = ''                # Status da nota
    codemp: str = ''                    # Código empresa
    
    # Metadados
    origem: str = 'sankhya'
    data_consulta: str = field(default_factory=lambda: datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    
    @property
    def vlrnota_float(self) -> float:
        """Retorna valor da nota como float"""
        try:
            return float(str(self.vlrnota).replace(',', '.'))
        except:
            return 0.0
    
    @property
    def vlrnota_formatado(self) -> str:
        """Retorna valor da nota formatado em reais"""
        try:
            valor = self.vlrnota_float
            return f"R$ {valor:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
        except:
            return "R$ 0,00"
    
    @property
    def dtneg_formatada(self) -> str:
        """Retorna data de negociação formatada DD/MM/YYYY"""
        try:
            if not self.dtneg or len(self.dtneg) < 10:
                return ""
            # Assumindo formato ISO YYYY-MM-DD
            return f"{self.dtneg[8:10]}/{self.dtneg[5:7]}/{self.dtneg[0:4]}"
        except:
            return self.dtneg
    
    def to_dict(self) -> dict:
        """Converte cabeçalho para dicionário"""
        return {
            'nunota': self.nunota,
            'numnota': self.numnota,
            'dtneg': self.dtneg,
            'codparc': self.codparc,
            'nomeparc': self.nomeparc,
            'codtipoper': self.codtipoper,
            'dhtipoper': self.dhtipoper,
            'vlrnota': self.vlrnota,
            'observacao': self.observacao,
            'statusnota': self.statusnota,
            'codemp': self.codemp,
            'origem': self.origem,
            'data_consulta': self.data_consulta,
            # Campos formatados
            'vlrnota_formatado': self.vlrnota_formatado,
            'dtneg_formatada': self.dtneg_formatada
        }
    
    @classmethod
    def from_sankhya_dict(cls, data: dict) -> 'CabecalhoNota':
        """Cria CabecalhoNota a partir de dicionário do Sankhya"""
        return cls(
            nunota=str(data.get('NUNOTA', '')),
            numnota=str(data.get('NUMNOTA', '')),
            dtneg=str(data.get('DTNEG', '')),
            codparc=str(data.get('CODPARC', '')),
            nomeparc=str(data.get('NOMEPARC', '')),
            codtipoper=str(data.get('CODTIPOPER', '')),
            dhtipoper=str(data.get('DHTIPOPER', '')),
            vlrnota=str(data.get('VLRNOTA', '')),
            observacao=str(data.get('OBSERVACAO', '')),
            statusnota=str(data.get('STATUSNOTA', '')),
            codemp=str(data.get('CODEMP', ''))
        )
    
    def __repr__(self) -> str:
        return (f"CabecalhoNota(nunota={self.nunota}, numnota={self.numnota}, "
                f"parceiro={self.nomeparc}, valor={self.vlrnota_formatado})")
