"""
modules/wms/services/dashboard_service.py
Serviço de agregação de dados para o Dashboard Analítico WMS.
"""

import logging
from datetime import datetime, timedelta, date
from typing import List, Optional, Dict, Any
from collections import defaultdict

logger = logging.getLogger(__name__)


# HELPERS

def _parse_date(s: str) -> Optional[date]:
    if not s:
        return None
    s = str(s).strip()[:10]
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _to_float(val) -> float:
    """
    Converte valor para float de forma segura.
    Suporta:
      - Inglês / Sheets : 152.01    →  152.01
      - Brasileiro      : 15.201,00 → 15201.00
      - BR sem milhar   : 201,00    →   201.00
    """
    if val is None or val == '':
        return 0.0
    try:
        s = str(val).replace('R$', '').replace(' ', '').strip()
        if not s:
            return 0.0
        tem_virgula = ',' in s
        tem_ponto   = '.' in s
        if tem_virgula and tem_ponto:
            # BR com milhar: 15.201,00
            s = s.replace('.', '').replace(',', '.')
        elif tem_virgula and not tem_ponto:
            # BR simples: 201,00
            s = s.replace(',', '.')
        elif tem_ponto and not tem_virgula:
            partes = s.split('.')
            if len(partes) == 2:
                pass          # decimal inglês: 152.01 — não mexer
            else:
                s = s.replace('.', '')   # milhar BR: 1.500.000
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _get(avaria, field: str, default='') -> str:
    if isinstance(avaria, dict):
        return str(avaria.get(field, default) or default)
    return str(getattr(avaria, field, default) or default)


def _get_float(avaria, field: str) -> float:
    return _to_float(_get(avaria, field))


def _norm(s) -> str:
    return (s or '').strip().lower()


# FILTROS

def aplicar_filtros(
    avarias: list,
    cod_empresa: Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    campo_data: str = 'data_registro',
    status_indenizacao: Optional[list] = None,
    status_estoque: Optional[list] = None,
    transportadora: Optional[list] = None,
    motivo: Optional[list] = None,
    operador: Optional[list] = None,
    codprod: Optional[list] = None,
    nota: Optional[list] = None,
    codlocal: Optional[list] = None,
) -> list:
    resultado = avarias
    if cod_empresa and cod_empresa != '__todos__':
        resultado = [a for a in resultado if _get(a, 'cod_empresa') == cod_empresa]
    dt_inicio = _parse_date(data_inicio)
    dt_fim    = _parse_date(data_fim)
    if dt_inicio or dt_fim:
        filtradas = []
        for a in resultado:
            dt = _parse_date(_get(a, campo_data))
            if dt is None:
                continue
            if dt_inicio and dt < dt_inicio:
                continue
            if dt_fim and dt > dt_fim:
                continue
            filtradas.append(a)
        resultado = filtradas
    if status_indenizacao:
        si_set = {s.strip().lower() for s in status_indenizacao if s.strip()}
        
        # Intercepta os "status virtuais" do clique do grafico
        tem_virtual = any(k in si_set for k in [
            'em atraso', 'indenização em atraso', 'indenizacao em atraso',
            'pendente de pagamento', 'indenização pendente', 'indenizacao pendente', 'todos os pendentes',
            'pago', 'indenização paga', 'indenizacao paga'
        ])
        
        if tem_virtual:
            try:
                from flask import session
                from modules.wms.routes.parametrizacao_routes import config_service
                empresa = session.get('user_empresa', '')
                cfg = config_service.get_all_configs(empresa) if config_service else {}
            except Exception:
                cfg = {}

            def _get_cfg_set(key, default):
                v = cfg.get(key, '')
                return {_norm(s) for s in v.split(',') if s.strip()} if v.strip() else set(default)

            _pago_set = _get_cfg_set('dash_status_pago', ['pago', 'pago total', 'quitado', 'pago parcial', 'pagto ok - total', 'pagto ok'])
            _atraso_status_set = _get_cfg_set('dash_status_em_atraso', [])

            filtradas = []
            hoje = date.today()
            for a in resultado:
                st_norm = _norm(_get(a, 'status_indenizacao'))
                
                # Identifica a qual "bucket" virtual a avaria pertence (exatamente igual ao calcular_financeiro)
                if st_norm in _pago_set:
                    label = 'pago'
                else:
                    dt_prazo = _parse_date(_get(a, 'prazo_pagamento'))
                    if (dt_prazo and dt_prazo < hoje) or (st_norm in _atraso_status_set):
                        label = 'em atraso'
                    else:
                        label = 'pendente de pagamento'
                        
                match = False
                if label == 'pago' and any(k in si_set for k in ['pago', 'indenização paga', 'indenizacao paga']):
                    match = True
                elif label == 'em atraso' and any(k in si_set for k in ['em atraso', 'indenização em atraso', 'indenizacao em atraso', 'todos os pendentes']):
                    match = True
                elif label == 'pendente de pagamento' and any(k in si_set for k in ['pendente de pagamento', 'indenização pendente', 'indenizacao pendente', 'todos os pendentes']):
                    match = True
                elif st_norm in si_set:
                    match = True
                    
                if match:
                    filtradas.append(a)
            resultado = filtradas
        else:
            resultado = [a for a in resultado if _norm(_get(a, 'status_indenizacao')) in si_set]

    if status_estoque:
        se_set = {s.strip().lower() for s in status_estoque if s.strip()}
        
        tem_virtual = any(k in se_set for k in ['em atraso', 'estoque em atraso', 'no prazo', 'pendente de entrega'])
        
        if tem_virtual:
            _fin_set = {'finalizado', 'baixado', 'finalizada'}
            _nc_set  = {'em transito', 'aguardando chegada', 'nao recebido'}
            
            filtradas = []
            hoje = date.today()
            for a in resultado:
                st_est_norm = _norm(_get(a, 'status_estoque'))
                if st_est_norm in _fin_set:
                    if st_est_norm in se_set:
                        filtradas.append(a)
                    continue
                
                dt_entrega = _parse_date(_get(a, 'data_entrega'))
                match = False
                
                if dt_entrega or st_est_norm in _nc_set:
                    atrasado = bool(dt_entrega and dt_entrega < hoje)
                    if atrasado and any(k in se_set for k in ['em atraso', 'estoque em atraso']):
                        match = True
                    elif not atrasado and any(k in se_set for k in ['no prazo', 'pendente de entrega']):
                        match = True
                        
                if not match and st_est_norm in se_set:
                    match = True
                    
                if match:
                    filtradas.append(a)
            resultado = filtradas
        else:
            resultado = [a for a in resultado if _norm(_get(a, 'status_estoque')) in se_set]
    if transportadora:
        tr_set = {t.strip().lower() for t in transportadora if t.strip()}
        resultado = [a for a in resultado if _norm(_get(a, 'transportadora')) in tr_set]
    if motivo:
        mt_set = {m.strip().lower() for m in motivo if m.strip()}
        resultado = [a for a in resultado if _norm(_get(a, 'motivo_devolucao')) in mt_set]
    if operador:
        op_set = {o.strip().lower() for o in operador if o.strip()}
        resultado = [a for a in resultado if (
            _norm(_get(a, 'registrado_por')) in op_set or 
            _norm(_get(a, 'finalizado_por')) in op_set or
            _norm(_get(a, 'alterado_por')) in op_set
        )]
    if codprod:
        cp_set = {str(c).strip().lower() for c in codprod if str(c).strip()}
        resultado = [a for a in resultado if _norm(_get(a, 'cod_produto')) in cp_set]
    if nota:
        nt_set = {str(n).strip().lower() for n in nota if str(n).strip()}
        resultado = [a for a in resultado if _norm(_get(a, 'nota')) in nt_set]
    if codlocal:
        cl_set = {str(l).strip().lower() for l in codlocal if str(l).strip()}
        resultado = [a for a in resultado if _norm(_get(a, 'local')) in cl_set or _norm(_get(a, 'codlocal')) in cl_set]
    return resultado


def get_cod_empresas_disponiveis(avarias: list) -> List[str]:
    valores = set()
    for a in avarias:
        v = _get(a, 'cod_empresa')
        if v:
            valores.add(v)
    return sorted(valores)


# PAINEL: FINANCEIRO

def calcular_financeiro(
    avarias: list,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    status_indenizaveis: Optional[List[str]] = None,
    status_pago: Optional[List[str]] = None,
    status_pago_parcial: Optional[List[str]] = None,
    status_pendente: Optional[List[str]] = None,
    status_em_atraso: Optional[List[str]] = None,
    historico_estoque: Optional[List[Dict]] = None,
) -> Dict[str, Any]:

    # Unificamos a regra de 'Pagos' para usar estritamente o que foi parametrizado no painel
    _pago_set    = [_norm(s) for s in (status_pago or ['pago', 'pago total', 'quitado', 'pago parcial'])]
    _pend_set    = [_norm(s) for s in (status_pendente or [
        'pendente de pagamento', 'em negociacao', 'em negociacao',
        'aprovado', 'indenizacao aprovada', 'indenizacao aprovada',
    ])]
    _atraso_status_set = [_norm(s) for s in (status_em_atraso or [])]
    _todos_pagos = set(_pago_set)

    if status_indenizaveis:
        _indeni_set  = set([_norm(s) for s in status_indenizaveis])
        _indeni_set.update(_todos_pagos)
        indenizaveis = [a for a in avarias if _norm(_get(a, 'status_indenizacao')) in _indeni_set]
    else:
        _fallback = _todos_pagos | set(_pend_set) | set(_atraso_status_set)
        indenizaveis = [
            a for a in avarias
            if _get_float(a, 'valor_indenizacao') > 0
            or _norm(_get(a, 'status_indenizacao')) in _fallback
        ]

    hoje = date.today()

    pagos         = [a for a in indenizaveis if _norm(_get(a, 'status_indenizacao')) in _pago_set]
    ids_pagos     = {_get(a, 'id') for a in pagos}
    nao_pagos     = [a for a in indenizaveis if _get(a, 'id') not in ids_pagos]

    em_atraso, pendente_no_prazo = [], []
    for a in nao_pagos:
        dt_prazo = _parse_date(_get(a, 'prazo_pagamento'))
        if (dt_prazo and dt_prazo < hoje) or (_norm(_get(a, 'status_indenizacao')) in _atraso_status_set):
            em_atraso.append(a)
        else:
            pendente_no_prazo.append(a)

    # Cálculos corrigidos somando estritamente valor_indenizacao
    valor_total      = sum(_get_float(a, 'valor_indenizacao') for a in indenizaveis)
    valor_pago       = sum(_get_float(a, 'valor_indenizacao') for a in pagos)
    valor_atraso     = sum(_get_float(a, 'valor_indenizacao') for a in em_atraso)
    valor_pendente   = sum(_get_float(a, 'valor_indenizacao') for a in pendente_no_prazo)

    kpis = {
        'total_indenizaveis':           len(indenizaveis),
        'valor_total_indenizacoes':     round(valor_total, 2),
        'qtd_pago':                     len(pagos),
        'valor_pago':                   round(valor_pago, 2),
        'qtd_em_atraso':                len(em_atraso),
        'valor_em_atraso':              round(valor_atraso, 2),
        'qtd_pendente':                 len(pendente_no_prazo),
        'valor_pendente':               round(valor_pendente, 2),
        'qtd_vencendo_7d': sum(
            1 for a in pendente_no_prazo
            if _parse_date(_get(a, 'prazo_pagamento'))
            and hoje <= _parse_date(_get(a, 'prazo_pagamento')) <= hoje + timedelta(days=7)
        ),
    }

    status_grupos: Dict[str, Dict] = {}
    for a in indenizaveis:
        st_raw  = (_get(a, 'status_indenizacao') or 'Sem Status').strip().upper()
        st_norm = _norm(st_raw)
        if st_norm in _pago_set:
            label = 'Pago'
        else:
            dt_prazo = _parse_date(_get(a, 'prazo_pagamento'))
            if (dt_prazo and dt_prazo < hoje) or (st_norm in _atraso_status_set):
                label = 'Em Atraso'
            elif st_norm in _pend_set:
                label = 'Pendente de Pagamento'
            else:
                label = st_raw
        if label not in status_grupos:
            status_grupos[label] = {'qtd': 0, 'valor': 0.0}
        status_grupos[label]['qtd']   += 1
        status_grupos[label]['valor'] += _get_float(a, 'valor_indenizacao')

    # Ordenar status por valor para o gráfico de barras
    status_sorted = sorted(status_grupos.items(), key=lambda item: item[1]['valor'], reverse=True)

    # Cores para o gráfico de rosca de status
    color_map = {
        'Pago': '#198754',                   # success green
        'Em Atraso': '#dc3545',              # danger red
        'Pendente de Pagamento': '#ffc107',  # warning yellow
        'PAGO': '#198754',
        'EM ATRASO': '#dc3545',
        'PENDENTE DE PAGAMENTO': '#ffc107',
    }
    default_colors = ['#0d6efd', '#6f42c1', '#0dcaf0', '#fd7e14', '#20c997', '#6c757d']

    colors = []
    default_idx = 0
    for label, _ in status_sorted:
        if label.upper() in color_map:
            colors.append(color_map[label.upper()])
        else:
            colors.append(default_colors[default_idx % len(default_colors)])
            default_idx += 1

    # Ajuste: Transportadoras divididas por status para gráfico de barras empilhadas
    transp_status: Dict[str, Dict[str, float]] = defaultdict(lambda: {'pago': 0.0, 'pendente': 0.0, 'atraso': 0.0})
    for a in indenizaveis:
        t = (_get(a, 'transportadora') or 'Sem info').strip().upper()
        st_norm = _norm(_get(a, 'status_indenizacao'))
        val = _get_float(a, 'valor_indenizacao')
        if st_norm in _pago_set:
            transp_status[t]['pago'] += val
        else:
            dt_prazo = _parse_date(_get(a, 'prazo_pagamento'))
            if (dt_prazo and dt_prazo < hoje) or (st_norm in _atraso_status_set):
                transp_status[t]['atraso'] += val
            else:
                transp_status[t]['pendente'] += val
                
    transp_totais = {t: sum(v.values()) for t, v in transp_status.items()}
    top_transp = sorted(transp_totais.items(), key=lambda x: x[1], reverse=True)[:10]

    # Novo: Dados detalhados para o gráfico de fluxo de caixa
    fluxo_detalhado = []
    # Pagos (usando data_pagamento)
    for a in pagos:
        dt_pgto = _parse_date(_get(a, 'data_pagamento'))
        if dt_pgto:
            fluxo_detalhado.append({
                'date': dt_pgto.strftime('%Y-%m-%d'),
                'valor': _get_float(a, 'valor_indenizacao'),
                'status': 'pago'
            })
    # Pendentes (usando prazo_pagamento)
    for a in pendente_no_prazo:
        dt_prazo = _parse_date(_get(a, 'prazo_pagamento'))
        if dt_prazo:
            fluxo_detalhado.append({
                'date': dt_prazo.strftime('%Y-%m-%d'),
                'valor': _get_float(a, 'valor_indenizacao'),
                'status': 'pendente'
            })
    # Atrasados (usando prazo_pagamento)
    for a in em_atraso:
        dt_prazo = _parse_date(_get(a, 'prazo_pagamento'))
        if dt_prazo:
            fluxo_detalhado.append({
                'date': dt_prazo.strftime('%Y-%m-%d'),
                'valor': _get_float(a, 'valor_indenizacao'),
                'status': 'atrasado'
            })

    # Evolução de Estoque (Snapshot Diário via Sheets vs Calculado por Avarias)
    labels_evolucao = []
    saldos_evolucao = []
    entradas_evolucao = []
    
    # Detecta modo (diario vs mensal) a partir das datas de filtro
    hoje = date.today()
    mes_atual = hoje.strftime('%Y-%m')
    modo_evolucao = 'mensal'
    if data_inicio or data_fim:
        d_fim_chk = _parse_date(data_fim) if data_fim else hoje
        d_ini_chk = _parse_date(data_inicio) if data_inicio else None
        if d_fim_chk and d_fim_chk.strftime('%Y-%m') == mes_atual:
            ini_mes = d_ini_chk.strftime('%Y-%m') if d_ini_chk else mes_atual
            if ini_mes == mes_atual:
                modo_evolucao = 'diario'

    if historico_estoque:
        # Agrupa por data: soma qtd_total de todos os produtos/locais
        por_data: Dict[str, float] = defaultdict(float)
        for he in historico_estoque:
            dt_pos = _get(he, 'data_posicao')
            qtd    = _get_float(he, 'qtd_total') or _get_float(he, 'valor_total')
            if dt_pos:
                por_data[dt_pos] += qtd

        if modo_evolucao == 'diario':
            # Preenche todos os dias do período (gaps = 0)
            d_ini_obj = _parse_date(data_inicio) if data_inicio else hoje.replace(day=1)
            d_fim_obj = _parse_date(data_fim) if data_fim else hoje
            if d_ini_obj is None:
                d_ini_obj = hoje.replace(day=1)
            if d_fim_obj is None:
                d_fim_obj = hoje
            current_day = d_ini_obj
            while current_day <= d_fim_obj:
                d_key = current_day.strftime('%d/%m/%Y')
                labels_evolucao.append(d_key[:5])  # DD/MM
                saldos_evolucao.append(round(por_data.get(d_key, 0), 2))
                current_day += timedelta(days=1)
        else:
            datas_ordenadas = []
            for d in por_data:
                dt_obj = _parse_date(d)
                if dt_obj:
                    datas_ordenadas.append((dt_obj, d))
            datas_ordenadas.sort(key=lambda x: x[0])
            for dt_obj, d_str in datas_ordenadas[-30:]:
                labels_evolucao.append(d_str[:5])  # DD/MM
                saldos_evolucao.append(round(por_data[d_str], 2))

    else:
        # FALLBACK (MODELO ANTIGO): Calculado mensalmente por entradas/saídas das avarias
        meses_mov: Dict[str, Dict[str, float]] = defaultdict(lambda: {'in': 0.0, 'out': 0.0})
        _finalizados = ['finalizado', 'baixado', 'finalizada']
        for a in avarias:
            dt_reg = _parse_date(_get(a, 'data_registro'))
            val_nf = _get_float(a, 'valor_nf')
            if dt_reg:
                meses_mov[dt_reg.strftime('%Y-%m')]['in'] += val_nf
                
            st_est = _norm(_get(a, 'status_estoque'))
            if st_est in _finalizados:
                dt_fin = _parse_date(_get(a, 'data_finalizacao'))
                if dt_fin:
                    meses_mov[dt_fin.strftime('%Y-%m')]['out'] += val_nf
                    
        meses_pt = {'01':'Jan', '02':'Fev', '03':'Mar', '04':'Abr', '05':'Mai', '06':'Jun', '07':'Jul', '08':'Ago', '09':'Set', '10':'Out', '11':'Nov', '12':'Dez'}
        evolucao_sorted = sorted(meses_mov.items(), key=lambda x: x[0])
        
        saldo_acumulado = 0.0
        
        for mes_ano, mov in evolucao_sorted:
            ano, mes = mes_ano.split('-')
            labels_evolucao.append(f"{meses_pt[mes]}/{ano}")
            saldo_acumulado += (mov['in'] - mov['out'])
            saldos_evolucao.append(round(saldo_acumulado, 2))
            entradas_evolucao.append(round(mov['in'], 2))
            
        labels_evolucao = labels_evolucao[-12:]
        saldos_evolucao = saldos_evolucao[-12:]
        entradas_evolucao = entradas_evolucao[-12:]

    return {
        'kpis': kpis,
        'grafico_transp': {
            'labels': [t[0] for t in top_transp],
            'pago': [round(transp_status[t[0]]['pago'], 2) for t in top_transp],
            'pendente': [round(transp_status[t[0]]['pendente'], 2) for t in top_transp],
            'atraso': [round(transp_status[t[0]]['atraso'], 2) for t in top_transp],
        },
        'grafico_status': {
            'labels': [item[0] for item in status_sorted],
            'qtds':   [item[1]['qtd'] for item in status_sorted],
            'valores':[round(item[1]['valor'], 2) for item in status_sorted],
            'colors': colors
        },
        # Substituído pela versão detalhada
        'grafico_fluxo_detalhado': fluxo_detalhado,
        'grafico_evolucao': {
            'labels': labels_evolucao,
            'saldos': saldos_evolucao,
            'entradas': entradas_evolucao,
            'modo': modo_evolucao,
        }
    }


# PAINEL: OPERACIONAL

def calcular_operacional(
    avarias: list,
    itens: list = None,
    status_pendente: Optional[List[str]] = None,
    status_nao_chegou: Optional[List[str]] = None,
    status_triagem: Optional[List[str]] = None,
    status_etiquetado: Optional[List[str]] = None,
    status_finalizado: Optional[List[str]] = None,
    asns_agendados: Optional[list] = None,
) -> Dict[str, Any]:

    _pend_set   = [_norm(s) for s in (status_pendente   or ['pendente', 'aguardando recebimento'])]
    _nc_set     = [_norm(s) for s in (status_nao_chegou or ['em transito', 'em transito', 'aguardando chegada', 'nao recebido', 'nao recebido'])]
    _triage_set = [_norm(s) for s in (status_triagem    or ['em triagem', 'aguardando triagem'])]
    _etiq_set   = [_norm(s) for s in (status_etiquetado or ['etiquetado', 'aguardando destino'])]
    _fin_set    = [_norm(s) for s in (status_finalizado or ['finalizado', 'baixado', 'finalizada'])]

    por_status_estoque: Dict[str, int] = defaultdict(int)
    por_status_valor: Dict[str, float] = defaultdict(float)
    for a in avarias:
        raw_st = _get(a, 'status_estoque') or 'Sem Status'
        st = raw_st.strip().upper()
        por_status_estoque[st] += 1
        por_status_valor[st] += _get_float(a, 'valor_nf')

    def _conta(st_set):
        return sum(v for k, v in por_status_estoque.items() if _norm(k) in st_set)

    def _valor(st_set):
        return round(sum(v for k, v in por_status_valor.items() if _norm(k) in st_set), 2)

    qtd_sem_etiqueta = sum(
        1 for a in avarias
        if not (_get(a, 'etiqueta_inicial_impressa') or '').lower() == 'sim'
    )
    valor_sem_etiqueta = round(sum(
        _get_float(a, 'valor_nf') for a in avarias
        if not (_get(a, 'etiqueta_inicial_impressa') or '').lower() == 'sim'
    ), 2)

    kpis = {
        'total':               len(avarias),
        'valor_total':         round(sum(_get_float(a, 'valor_nf') for a in avarias), 2),
        'nao_chegou':          _conta(_nc_set),
        'valor_nao_chegou':    _valor(_nc_set),
        'pendentes':           _conta(_pend_set),
        'valor_pendentes':     _valor(_pend_set),
        'em_triagem':          _conta(_triage_set),
        'valor_em_triagem':    _valor(_triage_set),
        'etiquetados':         _conta(_etiq_set),
        'valor_etiquetados':   _valor(_etiq_set),
        'finalizados':         _conta(_fin_set),
        'valor_finalizados':   _valor(_fin_set),
        'sem_etiqueta':        qtd_sem_etiqueta,
        'valor_sem_etiqueta':  valor_sem_etiqueta,
    }

    por_local: Dict[str, float] = defaultdict(float)
    if itens:
        avaria_by_id = {str(_get(a, 'id')).strip(): a for a in avarias}
        for item in itens:
            loc_val = (_get(item, 'localizacao') or _get(item, 'codlocaldest') or
                       _get(item, 'codlocalorig'))
            a = avaria_by_id.get(str(_get(item, 'avaria_id')).strip())
            if not loc_val and a:
                loc_val = _get(a, 'local') or _get(a, 'codlocal')
            loc_val = loc_val or 'Não Definido'
            loc = str(loc_val).strip().upper()
            valor = _get_float(item, 'valor_item') or (a and _get_float(a, 'valor_nf')) or 0.0
            por_local[loc] += valor
    else:
        for a in avarias:
            st_est = _norm(_get(a, 'status_estoque') or '')
            if st_est not in _fin_set:
                loc_val = _get(a, 'local') or _get(a, 'codlocal') or 'Não Definido'
                loc = str(loc_val).strip().upper()
                por_local[loc] += _get_float(a, 'valor_nf')
    local_sorted = sorted(por_local.items(), key=lambda x: x[1], reverse=True)[:15]

    vol_por_transp: Dict[str, int] = defaultdict(int)
    for a in avarias:
        tp = (_get(a, 'transportadora') or 'Sem info').strip().upper()
        vol_por_transp[tp] += 1
    vol_sorted = sorted(vol_por_transp.items(), key=lambda x: x[1], reverse=True)[:10]

    # Notas Pendentes de Entrega por Transportadora (Integrando Sheets + ASN Portal)
    hoje = date.today()
    transp_entrega: Dict[str, Dict[str, int]] = defaultdict(lambda: {'pendente': 0, 'atraso': 0})
    tabela_entrega = []
    
    # 1. Dados de Avarias (Sheets)
    for a in avarias:
        st_est_norm = _norm(_get(a, 'status_estoque') or '')
        if st_est_norm in _fin_set:
            continue
            
        dt_entrega = _parse_date(_get(a, 'data_entrega'))
        transp = (_get(a, 'transportadora') or 'N/D').strip().upper()
        
        # Só contabiliza se houver uma data de entrega ou se estiver em um status de "Não Chegou"
        if dt_entrega or st_est_norm in _nc_set:
            is_atraso = dt_entrega and dt_entrega < hoje
            
            if is_atraso:
                transp_entrega[transp]['atraso'] += 1
            else:
                transp_entrega[transp]['pendente'] += 1
                
            tabela_entrega.append({
                'id':             _get(a, 'id'),
                'nota':           _get(a, 'nota'),
                'transportadora': _get(a, 'transportadora'),
                'data_entrega':   _get(a, 'data_entrega'),
                'status_estoque': _get(a, 'status_estoque'),
                'valor_nf':       _get_float(a, 'valor_nf'),
                'atrasado':       is_atraso,
                'fonte':          'Sheets'
            })

    # 2. Dados de ASN (Portal Agendamentos)
    if asns_agendados:
        for asn in asns_agendados:
            # ASN agendado geralmente ainda não tem avaria_id vinculada
            if asn.avaria_id:
                continue
                
            dt_prev = _parse_date(asn.data_prevista)
            transp = (asn.transportadora or 'N/D').strip().upper()
            is_atraso = dt_prev and dt_prev < hoje
            
            if is_atraso:
                transp_entrega[transp]['atraso'] += 1
            else:
                transp_entrega[transp]['pendente'] += 1
                
            tabela_entrega.append({
                'nota':           asn.nota,
                'transportadora': asn.transportadora,
                'data_entrega':   asn.data_prevista,
                'status_estoque': 'Agendado (ASN)',
                'atrasado':       is_atraso,
                'fonte':          'ASN Portal'
            })

    top_transp_ent = sorted(transp_entrega.items(), key=lambda x: x[1]['pendente'] + x[1]['atraso'], reverse=True)[:10]
    
    _pend_e_triagem = _pend_set + _triage_set
    tabela_pendentes = []
    for a in avarias:
        if _norm(_get(a, 'status_estoque') or '') in _pend_e_triagem:
            dt_reg      = _parse_date(_get(a, 'data_registro'))
            dias_aberto = (hoje - dt_reg).days if dt_reg else None
            tabela_pendentes.append({
                'nota':           _get(a, 'nota'),
                'transportadora': _get(a, 'transportadora'),
                'status_estoque': _get(a, 'status_estoque'),
                'data_registro':  _get(a, 'data_registro'),
                'dias_aberto':    dias_aberto,
            })
    tabela_pendentes.sort(key=lambda x: x.get('dias_aberto') or 0, reverse=True)

    locais_map = {}
    try:
        from flask import current_app
        cache_svc = getattr(current_app, 'cache_sankhya_service', None)
        if cache_svc:
            locais_map = {str(item['value']): item['label'] for item in cache_svc.listar_locais_para_selecao()}
    except Exception:
        pass

    return {
        'kpis': kpis,
        'grafico_status_estoque': {
            'labels': list(por_status_estoque.keys()),
            'values': list(por_status_estoque.values()),
        },
        'grafico_vol_transp': {
            'labels': [x[0] for x in vol_sorted],
            'values': [x[1] for x in vol_sorted],
        },
        'grafico_local': {
            'labels': [locais_map.get(str(x[0]), str(x[0])) for x in local_sorted],
            'values': [x[1] for x in local_sorted],
            'keys': [str(x[0]) for x in local_sorted],
        },
        'grafico_entrega_transp': {
            'labels':   [t[0] for t in top_transp_ent],
            'pendente': [t[1]['pendente'] for t in top_transp_ent],
            'atraso':   [t[1]['atraso'] for t in top_transp_ent],
        },
        'tabela_pendentes': tabela_pendentes[:20],
        'tabela_entrega':   tabela_entrega[:20],
    }


# PAINEL: QUALIDADE

def calcular_qualidade(avarias: list, itens: list = None) -> Dict[str, Any]:

    por_produto: Dict[str, Dict] = defaultdict(lambda: {'qtd': 0, 'valor': 0.0, 'qtd_avariada': 0.0})
    if itens:
        for item in itens:
            prod = (_get(item, 'descprod') or _get(item, 'descrprod') or _get(item, 'codprod') or 'N/D').strip().upper()
            por_produto[prod]['qtd']          += 1
            por_produto[prod]['qtd_avariada'] += _get_float(item, 'qtd_avariada')
            por_produto[prod]['valor']        += _get_float(item, 'valor_item')
    else:
        for a in avarias:
            # Tenta varrer os itens da avaria (caso seja um relacionamento do banco)
            itens_rel = getattr(a, 'itens_avarias', getattr(a, 'itens', getattr(a, 'itens_avaria', None)))
            if itens_rel and isinstance(itens_rel, list) and len(itens_rel) > 0:
                for item in itens_rel:
                    prod = (_get(item, 'descprod') or _get(item, 'descrprod') or _get(item, 'codprod') or 'N/D').strip().upper()
                    por_produto[prod]['qtd'] += 1
                    por_produto[prod]['valor'] += _get_float(item, 'valor_item')
            else:
                prod = 'N/D'
                for campo in ('descprod', 'descrprod', 'descricao_produto', 'produto', 'desc_produto', 'cod_produto', 'codprod'):
                    val = _get(a, campo)
                    if val and val not in ('', 'None'):
                        prod = val.strip().upper()
                        break
                por_produto[prod]['qtd']   += 1
                por_produto[prod]['valor'] += _get_float(a, 'valor_nf')
    top_produtos = sorted(por_produto.items(), key=lambda x: x[1]['qtd'], reverse=True)[:10]

    por_motivo: Dict[str, int] = defaultdict(int)
    for a in avarias:
        motivo = (_get(a, 'motivo_devolucao') or 'Nao informado').strip().upper()
        por_motivo[motivo] += 1
    motivos_sorted = sorted(por_motivo.items(), key=lambda x: x[1], reverse=True)[:10]

    divergencias = defaultdict(int)
    for a in avarias:
        mot_inicial = (_get(a, 'motivo_inicial') or _get(a, 'motivo_devolucao') or '').strip().upper()
        mot_final   = (_get(a, 'motivo_devolucao') or '').strip().upper()
        if mot_inicial and mot_final and mot_inicial != mot_final:
            chave = (mot_inicial[:40], mot_final[:40])
            divergencias[chave] += 1
    diverg_sorted = sorted(divergencias.items(), key=lambda x: x[1], reverse=True)[:10]

    motivos_por_transp: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for a in avarias:
        transp = (_get(a, 'transportadora') or 'Sem info').strip().upper()
        motivo = (_get(a, 'motivo_devolucao') or 'Nao informado').strip().upper()
        motivos_por_transp[transp][motivo] += 1
    top_motivos_transp = {}
    for transp, motivos in sorted(motivos_por_transp.items(), key=lambda x: sum(x[1].values()), reverse=True)[:8]:
        top3 = sorted(motivos.items(), key=lambda x: x[1], reverse=True)[:3]
        outros = sum(motivos.values()) - sum(q for _, q in top3)
        result = [{'motivo': m, 'qtd': q} for m, q in top3]
        if outros > 0:
            result.append({'motivo': 'Outros', 'qtd': outros})
        top_motivos_transp[transp] = result

    motivo_mais_freq = max(por_motivo, key=por_motivo.get) if por_motivo else 'N/D'
    transp_counts: Dict[str, int] = defaultdict(int)
    for a in avarias:
        transp_counts[_get(a, 'transportadora') or 'N/D'] += 1
    transp_mais_avaria = max(transp_counts, key=transp_counts.get) if transp_counts else ''

    kpis = {
        'total_avarias':                 len(avarias),
        'motivo_mais_frequente':         motivo_mais_freq,
        'pct_motivo_mais_freq':          round(por_motivo.get(motivo_mais_freq, 0) / len(avarias) * 100, 1) if avarias else 0,
        'transportadora_mais_incidente': transp_mais_avaria,
        'qtd_divergencias_motivo':       sum(divergencias.values()),
        'pct_divergencias':              round(sum(divergencias.values()) / len(avarias) * 100, 1) if avarias else 0,
    }

    return {
        'kpis': kpis,
        'grafico_top_produtos': {
            'labels': [x[0][:40] for x in top_produtos],
            'values': [x[1]['qtd'] for x in top_produtos],
            'valores': [round(x[1]['valor'], 2) for x in top_produtos],
            'nomes_completos': [x[0] for x in top_produtos]
        },
        'grafico_motivos': {
            'labels': [x[0] for x in motivos_sorted],
            'values': [x[1] for x in motivos_sorted],
        },
        'tabela_divergencias': [
            {'motivo_inicial': x[0][0], 'motivo_final': x[0][1], 'qtd': x[1]}
            for x in diverg_sorted
        ],
        'grafico_motivos_por_transp': {
            'transportadoras': list(top_motivos_transp.keys()),
            'dados': top_motivos_transp,
        },
    }


# PAINEL: COBRANCA

def calcular_cobranca(
    avarias: list,
    status_indenizaveis: Optional[List[str]] = None,
    status_pago: Optional[List[str]] = None,
    status_pago_parcial: Optional[List[str]] = None,
    status_pendente: Optional[List[str]] = None,
    status_em_atraso: Optional[List[str]] = None,
    funil_etapas: Optional[List[Dict]] = None,
) -> Dict[str, Any]:

    _pago_set    = [_norm(s) for s in (status_pago or [
        'pago', 'pago total', 'quitado', 'pagto ok - total', 'pagto ok',
    ])]
    _parcial_set = [_norm(s) for s in (status_pago_parcial or [
        'pago parcial', 'indenizacao parcial paga', 'pagto ok - parcial',
    ])]
    _pend_set    = [_norm(s) for s in (status_pendente or [
        'pendente de pagamento', 'em negociacao', 'aprovado',
        'indenizacao aprovada',
    ])]
    _atraso_status_set = [_norm(s) for s in (status_em_atraso or [])]
    _todos_pagos = set(_pago_set) | set(_parcial_set)

    if status_indenizaveis:
        _indeni_set  = set(_norm(s) for s in status_indenizaveis)
        _indeni_set.update(_todos_pagos)   # sempre inclui pagos (consistência com painel financeiro)
        indenizaveis = [a for a in avarias if _norm(_get(a, 'status_indenizacao')) in _indeni_set]
    else:
        _fallback    = _todos_pagos | set(_pend_set) | set(_atraso_status_set)
        indenizaveis = [
            a for a in avarias
            if _get_float(a, 'valor_indenizacao') > 0
            or _norm(_get(a, 'status_indenizacao')) in _fallback
        ]

    hoje      = date.today()
    ids_pagos = {_get(a, 'id') for a in indenizaveis if _norm(_get(a, 'status_indenizacao')) in _todos_pagos}
    nao_pagos = [a for a in indenizaveis if _get(a, 'id') not in ids_pagos]

    pendentes = [a for a in nao_pagos if _norm(_get(a, 'status_indenizacao')) in _pend_set]

    em_atraso = [
        a for a in nao_pagos
        if (_parse_date(_get(a, 'prazo_pagamento')) and _parse_date(_get(a, 'prazo_pagamento')) < hoje)
        or _norm(_get(a, 'status_indenizacao')) in _atraso_status_set
    ]

    inadimp_por_transp: Dict[str, float] = defaultdict(float)
    for a in em_atraso:
        inadimp_por_transp[_get(a, 'transportadora') or 'N/D'] += _get_float(a, 'valor_indenizacao')
    inadimp_sorted = sorted(inadimp_por_transp.items(), key=lambda x: x[1], reverse=True)

    if not funil_etapas:
        funil_etapas = [
            {'label': 'Registradas',          'status': []},
            {'label': 'Em Negociacao',         'status': ['em negociacao', 'em negociacao']},
            {'label': 'Aprovadas',             'status': ['aprovado', 'indenizacao aprovada']},
            {'label': 'Pendente Pgto',         'status': list(_pend_set)},
            {'label': 'Em Atraso',             'status': list(_atraso_status_set)},
            {'label': 'Pago',                  'status': list(_todos_pagos)},
        ]

    funil = []
    _pagos_lista = [a for a in indenizaveis if _get(a, 'id') in ids_pagos]
    # Replicando a lógica estrita do financeiro para os baldes virtuais do funil
    _pendentes_estritos = [a for a in nao_pagos if _get(a, 'id') not in {_get(x, 'id') for x in em_atraso}]
    
    for etapa in funil_etapas:
        label = etapa.get('label', '')
        raw_status_list = [s.strip() for s in (etapa.get('status') or [])]
        st_set = [_norm(s) for s in raw_status_list]
        
        itens = indenizaveis if not st_set else [a for a in indenizaveis if _norm(_get(a, 'status_indenizacao')) in st_set]
            
        funil.append({
            'label': label,
            'status': raw_status_list,
            'qtd':   len(itens),
            'valor': round(sum(_get_float(a, 'valor_indenizacao') for a in itens), 2),
        })

    faixas_def = [('Ate 30d', 0, 30), ('31-60d', 31, 60), ('61-90d', 61, 90), ('91-180d', 91, 180), ('+180d', 181, 99999)]
    aging_qtds = []
    for _, minD, maxD in faixas_def:
        aging_qtds.append(sum(
            1 for a in em_atraso
            if _parse_date(_get(a, 'prazo_pagamento'))
            and minD <= (hoje - _parse_date(_get(a, 'prazo_pagamento'))).days <= maxD
        ))

    # All pending payment items: overdue + not yet due (pendentes with future prazo)
    _seen_tv = set()
    tabela_vencidas = []
    for a in em_atraso + pendentes:
        aid = _get(a, 'id')
        if aid in _seen_tv:
            continue
        _seen_tv.add(aid)
        dt_prazo    = _parse_date(_get(a, 'prazo_pagamento'))
        dias_atraso = (hoje - dt_prazo).days if dt_prazo else 0
        tabela_vencidas.append({
            'id':                aid,
            'transportadora':    _get(a, 'transportadora'),
            'nota':              _get(a, 'nota'),
            'valor_indenizacao': _get_float(a, 'valor_indenizacao'),
            'prazo_pagamento':   _get(a, 'prazo_pagamento'),
            'status_indenizacao':_get(a, 'status_indenizacao'),
            'dias_atraso':       dias_atraso,
        })
    tabela_vencidas.sort(key=lambda x: x.get('dias_atraso') or 0, reverse=True)

    kpis = {
        'total_indenizaveis':       len(indenizaveis),
        'valor_total_indenizaveis': round(sum(_get_float(a, 'valor_indenizacao') for a in indenizaveis), 2),
        'qtd_inadimplentes':        len(em_atraso),
        'valor_inadimplente':       round(sum(_get_float(a, 'valor_indenizacao') for a in em_atraso), 2),
        'taxa_inadimplencia':       round(len(em_atraso) / len(indenizaveis) * 100, 1) if indenizaveis else 0,
        'qtd_pendentes':            len(pendentes),
        'valor_pendentes':          round(sum(_get_float(a, 'valor_indenizacao') for a in pendentes), 2),
        'qtd_vencendo_7d': sum(
            1 for a in pendentes
            if _parse_date(_get(a, 'prazo_pagamento'))
            and hoje <= _parse_date(_get(a, 'prazo_pagamento')) <= hoje + timedelta(days=7)
        ),
    }

    return {
        'kpis': kpis,
        'grafico_funil': funil,
        'grafico_aging': {
            'faixas': [f[0] for f in faixas_def],
            'qtds':   aging_qtds,
        },
        'grafico_inadimplentes_transp': {
            'labels': [x[0] for x in inadimp_sorted[:10]],
            'values': [round(x[1], 2) for x in inadimp_sorted[:10]],
        },
        'tabela_vencidas': tabela_vencidas[:50],
    }


# PAINEL: PRODUTIVIDADE

def calcular_produtividade(avarias: list) -> Dict[str, Any]:

    hoje          = date.today()
    inicio_mes    = hoje.replace(day=1)
    inicio_semana = hoje - timedelta(days=hoje.weekday())

    por_operador_mes: Dict[str, Dict] = defaultdict(lambda: {
        'registros': 0, 'finalizacoes': 0, 'alteracoes': 0, 'etiquetas': 0
    })
    por_operador_semana: Dict[str, int] = defaultdict(int)

    for a in avarias:
        op_reg  = _get(a, 'registrado_por')        or 'N/D'
        op_fin  = _get(a, 'finalizado_por')         or ''
        op_alt  = _get(a, 'alterado_por')           or ''
        op_tria = _get(a, 'operador_triagem')       or ''
        op_iden = _get(a, 'operador_identificacao') or ''

        dt_reg = _parse_date(_get(a, 'data_registro'))
        dt_fin = _parse_date(_get(a, 'data_finalizacao'))

        if dt_reg:
            if dt_reg >= inicio_mes:
                por_operador_mes[op_reg]['registros'] += 1
            if dt_reg >= inicio_semana:
                por_operador_semana[op_reg] += 1

        if op_fin and dt_fin and dt_fin >= inicio_mes:
            por_operador_mes[op_fin]['finalizacoes'] += 1

        if op_alt:
            por_operador_mes[op_alt]['alteracoes'] += 1

        for op_etiq in [op_tria, op_iden]:
            if op_etiq and op_etiq not in ('', 'None', 'N/D'):
                por_operador_mes[op_etiq]['etiquetas'] += 1

    def total_acoes(data):
        return data['registros'] + data['finalizacoes'] + data['alteracoes'] + data['etiquetas']

    ranking = sorted(por_operador_mes.items(), key=lambda x: total_acoes(x[1]), reverse=True)

    por_dia_reg: Dict[str, int] = defaultdict(int)
    por_dia_fin: Dict[str, int] = defaultdict(int)
    limite_30 = hoje - timedelta(days=30)
    for a in avarias:
        dt_reg = _parse_date(_get(a, 'data_registro'))
        dt_fin = _parse_date(_get(a, 'data_finalizacao'))
        if dt_reg and dt_reg >= limite_30:
            por_dia_reg[dt_reg.strftime('%Y-%m-%d')] += 1
        if dt_fin and dt_fin >= limite_30:
            por_dia_fin[dt_fin.strftime('%Y-%m-%d')] += 1

    all_dates     = sorted(set(list(por_dia_reg.keys()) + list(por_dia_fin.keys())))
    timeline_prod = {
        'datas':        all_dates,
        'registros':    [por_dia_reg.get(d, 0) for d in all_dates],
        'finalizacoes': [por_dia_fin.get(d, 0) for d in all_dates],
    }

    kpis = {
        'total_registros_mes':     sum(v['registros']    for v in por_operador_mes.values()),
        'total_finalizacoes_mes':  sum(v['finalizacoes']  for v in por_operador_mes.values()),
        'total_alteracoes_mes':    sum(v['alteracoes']    for v in por_operador_mes.values()),
        'total_etiquetas_mes':     sum(v['etiquetas']     for v in por_operador_mes.values()),
        'registros_hoje':          por_dia_reg.get(hoje.strftime('%Y-%m-%d'), 0),
        'total_operadores_ativos': len([o for o in por_operador_semana if por_operador_semana[o] > 0]),
    }

    return {
        'kpis': kpis,
        'grafico_operadores_semana': {
            'labels': list(por_operador_semana.keys()),
            'values': list(por_operador_semana.values()),
        },
        'grafico_timeline': timeline_prod,
        'tabela_ranking': [
            {
                'posicao':      i + 1,
                'operador':     op,
                'registros':    data['registros'],
                'finalizacoes': data['finalizacoes'],
                'alteracoes':   data['alteracoes'],
                'etiquetas':    data['etiquetas'],
                'total_acoes':  total_acoes(data),
            }
            for i, (op, data) in enumerate(ranking[:15])
        ],
    }
