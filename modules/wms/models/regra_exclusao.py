"""
modules/wms/models/regra_exclusao.py
Modelo de dados para regras de exclusão de avarias pendentes.

Cada regra define um conjunto de condições (local, etiqueta, status_indenizacao, status_estoque).
- Campos vazios funcionam como curinga (qualquer valor).
- TODAS as condições definidas devem ser satisfeitas para a regra aplicar (AND interno).
- Regras diferentes são avaliadas independentemente (OR entre regras).
"""

from dataclasses import dataclass

@dataclass
class RegraExclusao:
    """Representa uma regra de exclusão para avarias pendentes"""
    id: str = ''
    empresa: str = ''                # ← NOVO: isolamento por empresa
    local: str = ''                  # → ItemAvaria.localizacao
    etiqueta: str = ''               # → ItemAvaria.status_etiqueta
    status_indenizacao: str = ''     # → Avaria.status_indenizacao
    status_estoque: str = ''         # → Avaria.status_estoque
    ativo: str = 'sim'
    criado_por: str = ''
    criado_em: str = ''

    # Propriedades

    @property
    def has_conditions(self) -> bool:
        """Verifica se a regra possui pelo menos uma condição definida"""
        return bool(self.local or self.etiqueta or self.status_indenizacao or self.status_estoque)

    # Matching

    def matches(self, avaria, item) -> bool:
        """
        Verifica se um par avaria / item corresponde a esta regra.

        Lógica AND: todas as condições definidas devem ser satisfeitas.
        Campos vazios não filtram (curinga).

        Args:
            avaria: Objeto Avaria (nunca None)
            item  : Objeto ItemAvaria ou None (avaria sem itens)

        Returns:
            True  → item deve ser EXCLUÍDO da lista de pendentes
            False → item permanece na lista
        """
        if not self.has_conditions:
            return False  # Regra sem condições nunca exclui

        # --- local (campo do ItemAvaria) ---
        if self.local:
            item_local = str(getattr(item, 'localizacao', '') or '') if item else ''
            if item_local.strip().lower() != self.local.strip().lower():
                return False

        # --- etiqueta (campo do ItemAvaria) ---
        if self.etiqueta:
            item_etiqueta = str(getattr(item, 'status_etiqueta', '') or '') if item else ''
            if item_etiqueta.strip().lower() != self.etiqueta.strip().lower():
                return False

        # --- status_indenizacao (campo da Avaria) ---
        if self.status_indenizacao:
            val = str(getattr(avaria, 'status_indenizacao', '') or '')
            if val.strip().lower() != self.status_indenizacao.strip().lower():
                return False

        # --- status_estoque (campo da Avaria) ---
        if self.status_estoque:
            val = str(getattr(avaria, 'status_estoque', '') or '')
            if val.strip().lower() != self.status_estoque.strip().lower():
                return False

        return True  # Todas as condições satisfeitas → excluir

    # Serialização

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'empresa': self.empresa,           # ← NOVO
            'local': self.local,
            'etiqueta': self.etiqueta,
            'status_indenizacao': self.status_indenizacao,
            'status_estoque': self.status_estoque,
            'ativo': self.ativo,
            'criado_por': self.criado_por,
            'criado_em': self.criado_em,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'RegraExclusao':
        return cls(
            id=str(data.get('id', '')).strip(),
            empresa=str(data.get('empresa', '')).strip(),  # ← NOVO
            local=str(data.get('local', '')).strip(),
            etiqueta=str(data.get('etiqueta', '')).strip(),
            status_indenizacao=str(data.get('status_indenizacao', '')).strip(),
            status_estoque=str(data.get('status_estoque', '')).strip(),
            ativo=str(data.get('ativo', 'sim')).strip(),
            criado_por=str(data.get('criado_por', '')).strip(),
            criado_em=str(data.get('criado_em', '')).strip(),
        )
