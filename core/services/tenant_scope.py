"""
Escopo multi-tenant (isolamento por empresa) para o módulo WMS.

Antes deste módulo, cada rota/serviço reimplementava seu próprio filtro
`if empresa and row.get('empresa') != empresa` — o que já causou pelo menos
3 vazamentos de dados entre empresas corrigidos pontualmente em subsistemas
diferentes (ASN, lotes de validação, itens). Este módulo centraliza a regra
para que ela só precise ser corrigida em um lugar.

Regra preservada (não alterada por este módulo): um registro sem empresa
definida (legado) continua visível para qualquer empresa — mudar isso exigiria
backfill dos dados antigos, fora do escopo desta consolidação.
"""

from flask import session, jsonify


def get_current_empresa() -> str:
    """Empresa do usuário logado (única leitura de session['user_empresa'])."""
    return session.get('user_empresa', '')


def empresa_visivel(record_empresa: str, current_empresa: str = None,
                     blank_visible_to_all: bool = True) -> bool:
    """
    True se um registro com `record_empresa` pode ser visto/mutado pelo usuário
    da `current_empresa` (ou o usuário logado, se não informado).

    - Sem empresa do usuário logado -> tudo visível (sem contexto de tenant).
    - Registro sem empresa (legado) -> segue `blank_visible_to_all`, porque os
      subsistemas hoje NÃO concordam nesse caso: os guards de rota do ASN e o
      `validacao_estoque_service` tratam empresa em branco como visível a
      todos (default aqui), mas os filtros de listagem do `AsnService`
      (`get_all`/`get_agendados`/`get_slots_ocupados`) hoje escondem
      registros em branco assim que o usuário tem empresa definida — passe
      `blank_visible_to_all=False` para preservar esse comportamento nesses
      pontos. Resolver essa divergência é uma decisão de produto separada,
      não algo para mudar silenciosamente aqui.
    - Caso contrário -> só visível se as empresas coincidirem.
    """
    if current_empresa is None:
        current_empresa = get_current_empresa()
    if not current_empresa:
        return True
    if not record_empresa:
        return blank_visible_to_all
    return record_empresa == current_empresa


def require_empresa_match_or_404(record_empresa: str, current_empresa: str = None,
                                  mensagem: str = 'ASN não encontrado'):
    """
    Guard para rotas Flask: retorna uma resposta 404 pronta se `record_empresa`
    não pertence à empresa do usuário logado, ou None se pode prosseguir.

    Uso: `_guard = require_empresa_match_or_404(registro.empresa_dest, empresa)`
         `if _guard: return _guard`
    """
    if not empresa_visivel(record_empresa, current_empresa):
        return jsonify({'success': False, 'error': mensagem}), 404
    return None
