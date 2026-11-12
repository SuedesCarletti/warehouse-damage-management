"""
modules/wms/models/item_avaria.py - VERSÃO CORRIGIDA
Data: 28/01/2026
Correção: Ordem EXATA conforme planilha (sem link_fotos_drive)
"""

from dataclasses import dataclass
from typing import Optional
from datetime import datetime

def _parse_valor_item(v) -> float:
    """Converte valor monetário para float tolerando formatos BR (1.234,56) e US (1,234.56)."""
    from core.services.sankhya.service import parse_valor_sankhya
    return parse_valor_sankhya(v)

@dataclass
class ItemAvaria:
    """Representa um item de uma avaria com controle de etiquetas"""
    
    # Campos originais
    avaria_id: str
    sequencia: int
    codprod: str
    descrprod: str
    qtdneg: float
    qtd_avariada: float
    codlocaldest: str
    codlocalorig: str
    localizacao: str = ""
    valor_item: float = 0.0
    codgrupoprod: str = ""
    codemp: str = ""
    
    # Campos de etiquetas
    id_etiqueta: str = ""
    prefixo_estoque: str = "00"
    etiqueta_anterior: str = ""
    status_etiqueta: str = "pendente"
    observacao_produto: str = ""
    link_fotos_drive: str = ""
    data_identificacao: str = ""
    data_triagem: str = ""
    operador_identificacao: str = ""
    operador_triagem: str = ""
    data_alteracao: str = ""
    alterado_por: str = ""

    def to_dict(self) -> dict:
        """
        Converte para dicionário (ORDEM EXATA para Google Sheets)
        """
        return {
            'avaria_id': self.avaria_id,
            'sequencia': self.sequencia,
            'codprod': self.codprod,
            'descrprod': self.descrprod,
            'qtdneg': _parse_valor_item(self.qtdneg),
            'qtd_avariada': _parse_valor_item(self.qtd_avariada),
            'codlocaldest': self.codlocaldest,
            'codlocalorig': self.codlocalorig,
            'localizacao': self.localizacao,
            'valor_item': round(_parse_valor_item(self.valor_item), 2),
            'codgrupoprod': self.codgrupoprod,
            'codemp': self.codemp,
            'id_etiqueta': self.id_etiqueta,
            'prefixo_estoque': self.prefixo_estoque,
            'etiqueta_anterior': self.etiqueta_anterior,
            'status_etiqueta': self.status_etiqueta,
            'observacao_produto': self.observacao_produto,
            'link_fotos_drive': self.link_fotos_drive,
            'data_identificacao': self.data_identificacao,
            'data_triagem': self.data_triagem,
            'operador_identificacao': self.operador_identificacao,
            'operador_triagem': self.operador_triagem,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'ItemAvaria':
        """Cria item a partir de dicionário"""
        def _safe_int(v, default=0) -> int:
            try:
                return int(float(v)) if v not in (None, '', 'None') else default
            except (ValueError, TypeError):
                return default

        return cls(
            avaria_id=str(data.get('avaria_id', '')).strip(),
            sequencia=_safe_int(data.get('sequencia', 0)),
            codprod=str(data.get('codprod', '')).strip(),
            descrprod=data.get('descrprod', ''),
            qtdneg=_parse_valor_item(data.get('qtdneg', 0)),
            qtd_avariada=_parse_valor_item(data.get('qtd_avariada', 0)),
            codlocaldest=data.get('codlocaldest', ''),
            codlocalorig=data.get('codlocalorig', ''),
            localizacao=data.get('localizacao', ''),
            valor_item=_parse_valor_item(data.get('valor_item', 0)),
            codgrupoprod=data.get('codgrupoprod', ''),
            codemp=data.get('codemp', ''),
            id_etiqueta=data.get('id_etiqueta', ''),
            prefixo_estoque=data.get('prefixo_estoque', '00'),
            etiqueta_anterior=data.get('etiqueta_anterior', ''),
            status_etiqueta=data.get('status_etiqueta', 'pendente'),
            observacao_produto=data.get('observacao_produto', ''),
            link_fotos_drive=data.get('link_fotos_drive', ''),
            data_identificacao=data.get('data_identificacao', ''),
            data_triagem=data.get('data_triagem', ''),
            operador_identificacao=data.get('operador_identificacao', ''),
            operador_triagem=data.get('operador_triagem', ''),
            data_alteracao=data.get('data_alteracao', ''),
            alterado_por=data.get('alterado_por', ''),
        )
    
    @property
    def tem_etiqueta(self) -> bool:
        """Verifica se produto já tem etiqueta gerada"""
        return bool(self.id_etiqueta and self.id_etiqueta.strip())
    
    @property
    def etiqueta_ativa(self) -> bool:
        """Verifica se etiqueta está ativa"""
        return self.status_etiqueta == 'ativa'
    
    @property
    def pode_triar(self) -> bool:
        """Verifica se produto pode ser triado"""
        return self.tem_etiqueta and self.etiqueta_ativa and self.prefixo_estoque == '00'
