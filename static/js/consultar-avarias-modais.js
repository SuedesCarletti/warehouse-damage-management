// Continuação de consultar-avarias.js — seções: funções de modal de dados, correção crítica de modais, fotos da avaria.
// Ordem de carregamento importa: deve vir logo depois de consultar-avarias-dropdown.js.

// 5. FUNÇÕES DE MODAL DE DADOS (Preservadas)

async function verDetalhes(avariaId) {
    console.log('Buscando detalhes da avaria:', avariaId);

    const modal = new bootstrap.Modal(document.getElementById('modalDetalhes'));
    const conteudo = document.getElementById('detalhesConteudo');

    modal.show();

    // Loading
    conteudo.innerHTML = `
        <div class="text-center py-4">
            <div class="spinner-border text-success" role="status">
                <span class="visually-hidden">Carregando...</span>
            </div>
        </div>
    `;

    try {
        const response = await fetch(`/wms/api/avaria/${avariaId}`);
        const data = await response.json();

        if (!data.success || !data.avaria) {
            throw new Error('Avaria não encontrada: ' + (data.message || 'Erro desconhecido'));
        }

        const av = data.avaria;
        const itens = data.itens || [];

        // ─── Helper: formata valor monetário ─────────────────────────────────
        const fmtMoeda = (val) => {
            const s = String(val ?? '0').replace(/[^\d,.-]/g, '');
            let n;
            if (s.includes(',') && s.includes('.') && s.indexOf('.') < s.lastIndexOf(',')) {
                n = parseFloat(s.replace(/\./g, '').replace(',', '.'));  // BR: 1.234,56
            } else if (s.includes(',')) {
                n = parseFloat(s.replace(',', '.'));  // BR simples: 532,31
            } else {
                n = parseFloat(s);  // US/plain: 532.31
            }
            return isNaN(n) ? 'R$ 0,00' : n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
        };

        // ─── Helper: valor ou N/A ─────────────────────────────────────────────
        const val = (v, fallback = '<em class="text-muted">N/A</em>') =>
            (v && String(v).trim() && String(v).trim() !== '-') ? v : fallback;

        // ─── Helper: badge colorido ───────────────────────────────────────────
        const badge = (v, cor = 'secondary') =>
            `<span class="badge bg-${cor} text-white">${val(v, 'N/A')}</span>`;

        // ─── Badge dinâmico de etiqueta ───────────────────────────────────────
        const etqBadge = av.etiqueta_inicial_impressa === 'sim'
            ? '<span class="badge bg-success">Sim</span>'
            : '<span class="badge bg-danger">Não</span>';

        // ─── Itens da avaria ─────────────────────────────────────────────────
        const itensHtml = itens.length > 0
            ? (() => {
                // Status etiqueta → badge color
                const etqCor = (s) => ({ ativa: 'success', baixada: 'danger', recusada: 'dark', pendente: 'warning' }[s] || 'secondary');
                const etqLabel = (s) => ({ ativa: 'Ativa', baixada: 'Baixada', recusada: 'Recusada', pendente: 'Pendente' }[s] || (s || 'N/D'));

                // Prefixo → descrição legível
                const prefixoLabel = (p) => {
                    if (!p || p === '00') return '<span class="badge bg-secondary">00 — Temporário</span>';
                    return `<span class="badge bg-info text-dark">${p} — Estoque</span>`;
                };

                // Formatar valor monetário
                const fmtVal = (v) => {
                    const s = String(v ?? '0').replace(/[^\d,.-]/g, '');
                    let n;
                    if (s.includes(',') && s.includes('.') && s.indexOf('.') < s.lastIndexOf(',')) {
                        n = parseFloat(s.replace(/\./g, '').replace(',', '.'));  // BR: 1.234,56
                    } else if (s.includes(',')) {
                        n = parseFloat(s.replace(',', '.'));  // BR simples: 532,31
                    } else {
                        n = parseFloat(s);  // US/plain: 532.31
                    }
                    return isNaN(n) ? 'R$ 0,00' : n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
                };

                // Gerar cards de itens
                const cardsHtml = itens.map((item, idx) => {
                    const seq = item.sequencia || (idx + 1);
                    const temEtiqueta = item.id_etiqueta && item.id_etiqueta.trim();
                    const temTriagem = item.data_triagem && item.data_triagem.trim();

                    return `
                    <!-- ── ITEM ${seq} ─────────────────────────────────────── -->
                    <div class="border rounded mb-3 overflow-hidden">

                        <!-- Cabeçalho do item -->
                        <div class="px-3 py-2 d-flex justify-content-between align-items-center"
                             style="background:rgba(0,128,0,0.1); border-bottom:1px solid rgba(0,0,0,.1);">
                            <span class="fw-bold">
                                <i class="fas fa-box me-2 text-success"></i>
                                #${seq} — <code class="text-success">${item.codprod || 'N/A'}</code>
                                <small class="ms-2 text-muted fw-normal">${item.descrprod || 'Sem descrição'}</small>
                            </span>
                            <span class="badge bg-${etqCor(item.status_etiqueta)} ms-2">
                                ${etqLabel(item.status_etiqueta)}
                            </span>
                        </div>

                        <div class="p-3">
                            <div class="row g-3">

                                <!-- ── BLOCO 1: QUANTIDADES E VALORES ─────── -->
                                <div class="col-md-4">
                                    <p class="text-muted small mb-1 fw-semibold text-uppercase">
                                        <i class="fas fa-boxes me-1"></i>Quantidades e Valor
                                    </p>
                                    <table class="table table-sm table-bordered mb-0">
                                        <tr>
                                            <th width="55%">Qtd Nota</th>
                                            <td>${item.qtdneg ?? '-'}</td>
                                        </tr>
                                        <tr>
                                            <th>Qtd Avariada</th>
                                            <td><strong class="text-danger">${item.qtd_avariada ?? '-'}</strong></td>
                                        </tr>
                                        <tr>
                                            <th>Valor Item</th>
                                            <td class="fw-bold">${fmtVal(item.valor_item)}</td>
                                        </tr>
                                        <tr>
                                            <th>Cód. Grupo</th>
                                            <td><small>${item.codgrupoprod || '-'}</small></td>
                                        </tr>
                                        <tr>
                                            <th>Cód. Empresa</th>
                                            <td><small>${item.codemp || '-'}</small></td>
                                        </tr>
                                    </table>
                                </div>

                                <!-- ── BLOCO 2: LOCALIZAÇÃO ───────────────── -->
                                <div class="col-md-4">
                                    <p class="text-muted small mb-1 fw-semibold text-uppercase">
                                        <i class="fas fa-map-marker-alt me-1"></i>Localização
                                    </p>
                                    <table class="table table-sm table-bordered mb-0">
                                        <tr>
                                            <th width="55%">Localização Atual</th>
                                            <td>
                                                <span class="badge bg-secondary text-white">
                                                    ${item.localizacao || item.codlocaldest || 'N/A'}
                                                </span>
                                            </td>
                                        </tr>
                                        <tr>
                                            <th>Cód. Local Orig.</th>
                                            <td><small>${item.codlocalorig || '-'}</small></td>
                                        </tr>
                                        <tr>
                                            <th>Cód. Local Dest.</th>
                                            <td><small>${item.codlocaldest || '-'}</small></td>
                                        </tr>
                                        <tr>
                                            <th>Prefixo Estoque</th>
                                            <td>${prefixoLabel(item.prefixo_estoque)}</td>
                                        </tr>
                                    </table>
                                </div>

                                <!-- ── BLOCO 3: ETIQUETA ──────────────────── -->
                                <div class="col-md-4">
                                    <p class="text-muted small mb-1 fw-semibold text-uppercase">
                                        <i class="fas fa-tag me-1"></i>Etiqueta
                                    </p>
                                    <table class="table table-sm table-bordered mb-0">
                                        <tr>
                                            <th width="55%">ID Etiqueta</th>
                                            <td>
                                                ${temEtiqueta
                                                    ? `<code class="small text-primary">${item.id_etiqueta}</code>`
                                                    : '<em class="text-muted small">Não gerada</em>'}
                                            </td>
                                        </tr>
                                        <tr>
                                            <th>Etiq. Anterior</th>
                                            <td>
                                                ${item.etiqueta_anterior
                                                    ? `<code class="small text-secondary">${item.etiqueta_anterior}</code>`
                                                    : '<em class="text-muted small">—</em>'}
                                            </td>
                                        </tr>
                                        <tr>
                                            <th>Data Identificação</th>
                                            <td><small>${formatDate(item.data_identificacao) || '-'}</small></td>
                                        </tr>
                                        <tr>
                                            <th>Operador Ident.</th>
                                            <td><small>${item.operador_identificacao || '-'}</small></td>
                                        </tr>
                                    </table>
                                </div>
                            </div>

                            ${/* Triagem e observação — linha extra se tiver dados */
                            (temTriagem || item.observacao_produto)
                            ? `<div class="row g-3 mt-1">
                                ${temTriagem ? `
                                    <div class="col-md-6">
                                        <p class="text-muted small mb-1 fw-semibold text-uppercase">
                                            <i class="fas fa-exchange-alt me-1"></i>Triagem
                                        </p>
                                        <table class="table table-sm table-bordered mb-0">
                                            <tr><th width="45%">Data Triagem</th><td><small>${formatDate(item.data_triagem)}</small></td></tr>
                                            <tr><th>Operador Triagem</th><td><small>${item.operador_triagem || '-'}</small></td></tr>
                                        </table>
                                    </div>` : ''}
                                ${item.observacao_produto ? `
                                    <div class="col-md-6">
                                        <p class="text-muted small mb-1 fw-semibold text-uppercase">
                                            <i class="fas fa-comment me-1"></i>Observação do Produto
                                        </p>
                                        <div class="border rounded p-2 obs-dark-safe small">
                                            ${item.observacao_produto.replace(/\n/g, '<br>')}
                                        </div>
                                    </div>` : ''}
                               </div>`
                            : ''}

                        </div><!-- /p-3 -->
                    </div><!-- /border rounded -->
                    `;
                }).join('');

                return `
                    <div id="itens-avaria-lista" style="max-height:420px; overflow-y:auto; padding-right:4px;">
                        ${cardsHtml}
                    </div>
                `;
            })()
            : '<p class="text-muted fst-italic mb-0"><i class="fas fa-info-circle me-1"></i>Nenhum item avariado detalhado.</p>';

        // ─── Status Indenização (cor dinâmica) ───────────────────────────────
        const indCor = {
            'Pendente': 'warning',
            'Aprovado': 'success',
            'Recusado': 'danger',
            'Em Análise': 'info',
            'Finalizado': 'success',
            'Indenização Finalizada': 'success'
        }[av.status_indenizacao] || 'secondary';

        const estkCor = {
            'Pendente': 'warning',
            'Finalizado': 'success',
            'Estoque Disponível': 'success',
            'Estoque Descarte': 'danger'
        }[av.status_estoque] || 'secondary';

        // ─── Seção de finalização (só exibe se tiver dados) ──────────────────
        const temFinalizacao = av.data_finalizacao || av.finalizado_por || av.observacao_final;
        const secaoFinalizacao = temFinalizacao ? `
            <hr class="my-3">
            <h6 class="text-success mb-2">
                <i class="fas fa-check-circle me-2"></i>Finalização
            </h6>
            <div class="row g-2">
                <div class="col-md-4">
                    <small class="text-muted d-block">Data Finalização</small>
                    <strong>${formatDate(av.data_finalizacao)}</strong>
                </div>
                <div class="col-md-4">
                    <small class="text-muted d-block">Finalizado Por</small>
                    <strong>${val(av.finalizado_por)}</strong>
                </div>
            </div>
            ${av.observacao_final ? `
            <div class="mt-2">
                <small class="text-muted d-block mb-1">Observação Final</small>
                <div class="border rounded p-2 obs-dark-safe small" style="max-height:80px; overflow-y:auto;">
                    ${av.observacao_final.replace(/\n/g, '<br>')}
                </div>
            </div>` : ''}
        ` : '';

        // ─── HTML COMPLETO ───────────────────────────────────────────────────
        const html = `

            <!-- ═══════════════════════════════════════════════════════════ -->
            <!-- LINHA 1: IDENTIFICAÇÃO + LOGÍSTICA                         -->
            <!-- ═══════════════════════════════════════════════════════════ -->
            <div class="row g-3">

                <!-- COLUNA 1: IDENTIFICAÇÃO -->
                <div class="col-md-6">
                    <h6 class="text-success border-bottom pb-1 mb-2">
                        <i class="fas fa-id-card me-2"></i>Identificação
                    </h6>
                    <table class="table table-sm table-bordered mb-0">
                        <tr><th width="42%">ID Avaria</th><td><code class="text-success fw-bold">${av.id}</code></td></tr>
                        <tr><th>Empresa</th><td>${val(av.empresa)} <small class="text-muted">(${val(av.cod_empresa)})</small></td></tr>
                        <tr><th>Nota Fiscal (Venda)</th><td><strong>${val(av.nota)}</strong></td></tr>
                        <tr><th>NFD (Devolução)</th><td>${val(av.nfd)}</td></tr>
                        <tr><th>Quantidade</th><td>${val(av.quantidade)}</td></tr>
                        <tr><th>Volume</th><td>${val(av.volume)}</td></tr>
                        <tr><th>Etiqueta Impressa</th><td>${etqBadge}</td></tr>
                        <tr><th>Registrado Por</th><td>${val(av.registrado_por)}</td></tr>
                    </table>
                </div>

                <!-- COLUNA 2: TRANSPORTE E LOGÍSTICA -->
                <div class="col-md-6">
                    <h6 class="text-info border-bottom pb-1 mb-2">
                        <i class="fas fa-truck me-2"></i>Transporte e Logística
                    </h6>
                    <table class="table table-sm table-bordered mb-0">
                        <tr><th width="42%">Cód. Parceiro</th><td>${val(av.codparc)}</td></tr>                    
                        <tr><th width="42%">Transportadora</th><td>${val(av.transportadora)}</td></tr>
                        <tr><th>CTE</th><td>${val(av.cte)}</td></tr>
                        <tr><th>Data Coleta</th><td>${formatDate(av.data_coleta)}</td></tr>
                        <tr><th>Data Entrega</th><td>${formatDate(av.data_entrega)}</td></tr>
                        <tr><th>Rastreio Correios</th><td>${val(av.cod_rastreio_correios)}</td></tr>
                        <tr><th>Data Protocolo MKT</th><td>${formatDate(av.data_protocolo_mkt)}</td></tr>
                        <tr><th>Data Registro</th><td>${formatDate(av.data_registro)}</td></tr>
                    </table>
                </div>
            </div>

            <!-- ═══════════════════════════════════════════════════════════ -->
            <!-- LINHA 2: FINANCEIRO + STATUS                                -->
            <!-- ═══════════════════════════════════════════════════════════ -->
            <div class="row g-3 mt-1">

                <!-- COLUNA 3: FINANCEIRO -->
                <div class="col-md-6">
                    <h6 class="text-primary border-bottom pb-1 mb-2">
                        <i class="fas fa-dollar-sign me-2"></i>Financeiro
                    </h6>
                    <table class="table table-sm table-bordered mb-0">
                        <tr><th width="42%">Valor NF Venda</th><td class="fw-bold">${av.valor_nf_formatado || fmtMoeda(av.valor_nf)}</td></tr>
                        <tr><th>Valor NFD</th><td>${fmtMoeda(av.valor_nfd)}</td></tr>
                        <tr><th>NFP</th><td>${val(av.nfp)}</td></tr>
                        <tr><th>Valor NFP</th><td>${fmtMoeda(av.valor_nfp)}</td></tr>
                        <tr><th>% Reembolso</th><td><span class="text-success fw-bold">${av.reembolso_pct || 0}%</span></td></tr>
                        <tr><th>Valor Indenização</th><td class="text-primary fw-bold">${av.valor_indenizacao_formatado || fmtMoeda(av.valor_indenizacao)}</td></tr>
                        <tr><th>Prazo Pagamento</th><td>${formatDate(av.prazo_pagamento)}</td></tr>
                        <tr><th>Data Pagamento</th><td>${formatDate(av.data_pagamento)}</td></tr>
                    </table>
                </div>

                <!-- COLUNA 4: STATUS E INDENIZAÇÃO -->
                <div class="col-md-6">
                    <h6 class="text-warning border-bottom pb-1 mb-2">
                        <i class="fas fa-tasks me-2"></i>Status e Indenização
                    </h6>
                    <table class="table table-sm table-bordered mb-0">
                        <tr><th width="42%">Status Estoque</th><td>${badge(av.status_estoque, estkCor)}</td></tr>
                        <tr><th>Status Indenização</th><td>${badge(av.status_indenizacao, indCor)}</td></tr>
                        <tr><th>Tipo Indenização</th><td>${val(av.indenizacao)}</td></tr>
                        <tr><th>Motivo Devolução</th><td>${val(av.motivo_devolucao)}</td></tr>
                        <tr><th>Cód. Motivo</th><td>${val(av.cod_motivo_devolucao)}</td></tr>
                        <tr><th>Motivo Inicial</th><td>${val(av.motivo_inicial)}</td></tr>
                        <tr><th>Cód. Motivo Inicial</th><td>${val(av.cod_motivo_inicial)}</td></tr>
                    </table>
                </div>
            </div>

            <!-- ═══════════════════════════════════════════════════════════ -->
            <!-- LINHA 3: ITENS                                              -->
            <!-- ═══════════════════════════════════════════════════════════ -->
            <hr class="my-3">
            <h6 class="text-secondary mb-2">
                <i class="fas fa-list-check me-2"></i>Itens Avariados
                <span class="badge bg-secondary ms-1">${itens.length}</span>
            </h6>
            ${itensHtml}

            <!-- ═══════════════════════════════════════════════════════════ -->
            <!-- LINHA 4: OBSERVAÇÃO INICIAL                                 -->
            <!-- ═══════════════════════════════════════════════════════════ -->
            <div class="mt-3">
                <h6 class="text-secondary mb-2">
                    <i class="fas fa-comment me-2"></i>Observação Inicial
                </h6>
                <div class="border rounded p-2 obs-dark-safe small" style="max-height:100px; overflow-y:auto;">
                    ${av.observacao
                        ? av.observacao.replace(/\n/g, '<br>')
                        : '<em class="text-muted">Nenhuma observação registrada.</em>'}
                </div>
            </div>

            <!-- ═══════════════════════════════════════════════════════════ -->
            <!-- LINHA 5: FINALIZAÇÃO (condicional)                          -->
            <!-- ═══════════════════════════════════════════════════════════ -->
            ${secaoFinalizacao}
        `;

        conteudo.innerHTML = html;

    } catch (error) {
        console.error('Erro ao buscar detalhes:', error);
        conteudo.innerHTML = `
            <div class="alert alert-danger">
                <i class="fas fa-exclamation-triangle me-2"></i>
                Erro ao carregar detalhes: ${error.message}
            </div>
        `;
    }
}
    
function toggleManualInput() {
    const el = document.getElementById('manualQuantity');
    const radio = document.getElementById('manualQuantityRadio');
    if (el && radio) {
        el.disabled = !radio.checked;
        if(radio.checked) el.focus(); else el.value = '';
    }
}

// 6. CORREÇÃO CRÍTICA DE MODAIS E INICIALIZAÇÃO

function fixModalsPosition() {
    const modals = document.querySelectorAll('.modal');
    modals.forEach(modal => {
        if (modal.parentNode !== document.body) {
            document.body.appendChild(modal);
            // console.log(`Modal ${modal.id} movido para o body.`);
        }
    });
}

document.addEventListener('DOMContentLoaded', function() {
    // 1. Move modais imediatamente para evitar o bug do scroll
    fixModalsPosition();

    // 2. Inicializar filtros/busca UI
    generateColumnCheckboxes();
    generateFilterRow();

    // 3. Restaurar filtros de coluna APÓS generateFilterRow() criar os inputs DOM
    _restaurarFiltrosColuna();

    // 4. Busca global com debounce de 250ms (escaneia data-searchable-content)
    const globalSearchInput = document.getElementById('globalSearch');
    if (globalSearchInput) {
        const debouncedGlobal = _debounce(applyFilters, 300);
        globalSearchInput.addEventListener('input', debouncedGlobal);
    }

    let _filterRafId = null;
    const _applyFiltersThrottled = function() {
        if (_filterRafId) cancelAnimationFrame(_filterRafId);
        _filterRafId = requestAnimationFrame(function() {
            applyFilters();
            _filterRafId = null;
        });
    };    

    // 5. Inicializar redimensionamento
    initializeColumnResize();

    // 6. Restaurar estado do cache CacheEstado (scroll, colunas visíveis, ordenação)
    if (typeof CacheEstado !== 'undefined') {
        CacheEstado.restaurar('consultar');
        CacheEstado.registrarAutoSave('consultar');
    }

    // 7. Pré-construir índice de busca, depois aplicar filtros (zero DOM no loop)
    _buildRowIndex();
    applyFilters();

    // 8. Detectar ?alerta_status= vindo do clique no sino (sem reload)
    // URLSearchParams.get() já decodifica automaticamente — não usar decodeURIComponent
    (function aplicarFiltroAlertaConsultar() {
        const params = new URLSearchParams(window.location.search);
        const alertaStatus      = params.get('alerta_status');       // fallback legado
        const alertaNome        = params.get('alerta_nome');
        const alertaStatusParar = params.get('alerta_status_parar'); // novo (server-side)
    
        if (!alertaNome && !alertaStatus && !alertaStatusParar) return;
    
        // Filtro client-side apenas se NÃO houver filtro server-side (alerta_status_parar)
        // Quando server-side está ativo, o backend já retornou somente as avarias corretas
        if (alertaStatus && !alertaStatusParar) {
            const input = document.getElementById('globalSearch');
            if (input) {
                input.value = alertaStatus;
                applyFilters();
            }
        }
    
        // Limpar TODOS os params de alerta da URL
        params.delete('alerta_status');
        params.delete('alerta_nome');
        params.delete('alerta_status_parar'); // novo
        params.delete('alerta_campo_parar');  // novo
        params.delete('alerta_logica_parada'); // novo
        history.replaceState(null, '',
            params.toString()
                ? window.location.pathname + '?' + params.toString()
                : window.location.pathname
        );
    
        if (alertaNome) {
            const cardBody = document.querySelector('.card.mb-4 .card-body');
            if (cardBody) {
                const b = document.createElement('div');
                b.className = 'alert alert-warning alert-dismissible d-flex align-items-center gap-2 py-2 mb-0 mt-2';
                b.innerHTML = '<i class="fas fa-bell text-warning"></i>' +
                    '<div class="flex-grow-1"><strong>Alerta:</strong> ' + alertaNome +
                    ' &nbsp;—&nbsp; exibindo avarias que estão disparando este alerta' +
                    ' &nbsp;—&nbsp; <a href="' + window.location.pathname + '" class="alert-link">Limpar filtro</a></div>' +
                    '<button type="button" class="btn-close" data-bs-dismiss="alert"></button>';
                cardBody.appendChild(b);
            }
        }
    })();

    console.log('Página de Consulta Avarias inicializada com filtragem avançada!');
});

// FOTOS DA AVARIA (Consultar Avarias)

/**
 * Abre modal com fotos dos itens da avaria
 * @param {string} avariaId - ID da avaria
 */
async function abrirFotosAvaria(avariaId) {
    const modal = new bootstrap.Modal(document.getElementById('modalFotosConsultar'));
    const body  = document.getElementById('fotosConsultarBody');
    const title = document.getElementById('fotosConsultarAvariaId');

    title.textContent = `#${avariaId}`;
    body.innerHTML = `
        <div class="text-center py-4">
            <div class="spinner-border text-secondary" role="status"></div>
            <p class="mt-2 text-muted">Carregando itens...</p>
        </div>`;
    modal.show();

    try {
        const resp = await fetch(`/wms/api/avaria/${avariaId}`);
        const data = await resp.json();

        if (!data || !data.itens || data.itens.length === 0) {
            body.innerHTML = `<div class="alert alert-warning">
                <i class="fas fa-info-circle me-2"></i>
                Nenhum item encontrado para esta avaria.</div>`;
            return;
        }

        const itensComFoto = data.itens.filter(i => i.link_fotos_drive || i.id_etiqueta);
        const itensSemFoto = data.itens.filter(i => !i.link_fotos_drive && !i.id_etiqueta);

        // Buscar thumbnails de fotos para itens com etiqueta
        const fotosPromises = itensComFoto.map(async item => {
            if (!item.id_etiqueta) return { item, fotos: [] };
            try {
                const r = await fetch(`/wms/sankhya_avarias/api/produto/${item.id_etiqueta}/fotos`);
                const d = await r.json();
                return { item, fotos: (d.success && d.fotos) ? d.fotos : [], link_pasta: d.link_pasta };
            } catch {
                return { item, fotos: [], link_pasta: item.link_fotos_drive };
            }
        });

        const resultados = await Promise.all(fotosPromises);

        let html = '';

        if (resultados.length > 0) {
            const comFotosReais = resultados.filter(r => r.fotos && r.fotos.length > 0);
            const semFotosReais = resultados.filter(r => !r.fotos || r.fotos.length === 0);

            if (comFotosReais.length > 0) {
                html += `<h6 class="text-success mb-3">
                            <i class="fas fa-images me-2"></i>
                            ${comFotosReais.length} item(ns) com fotos
                         </h6>`;

                comFotosReais.forEach(({ item, fotos, link_pasta }) => {
                    const linkDrive = link_pasta || item.link_fotos_drive || '#';
                    html += `
                        <div class="card mb-3">
                            <div class="card-header d-flex justify-content-between align-items-center py-2">
                                <div>
                                    <small class="text-primary fw-semibold">${item.codprod || 'N/A'}</small>
                                    <span class="ms-2">${item.descrprod || 'Sem descrição'}</span>
                                    <small class="text-muted ms-2">Seq: ${item.sequencia}</small>
                                </div>
                                <a href="${linkDrive}" target="_blank" class="btn btn-sm btn-outline-primary">
                                    <i class="fas fa-external-link-alt me-1"></i>Drive
                                </a>
                            </div>
                            <div class="card-body p-2">
                                <div class="row g-2">`;
                    fotos.forEach(foto => {
                        const thumb = foto.thumbnail_url || '';
                        const view  = foto.view_url || linkDrive;
                        html += `
                                    <div class="col-4 col-md-3">
                                        <a href="${view}" target="_blank">
                                            <img src="${thumb}" alt="${foto.filename || 'foto'}"
                                                 class="img-thumbnail w-100"
                                                 style="height:100px;object-fit:cover;"
                                                 loading="lazy"
                                                 onerror="this.src='/static/img/no-image.png'">
                                        </a>
                                    </div>`;
                    });
                    html += `
                                </div>
                            </div>
                        </div>`;
                });
            }

            if (semFotosReais.length > 0) {
                html += `<h6 class="text-muted mb-2 mt-3">
                            <i class="fas fa-camera-slash me-2"></i>
                            ${semFotosReais.length} item(ns) sem fotos
                         </h6>
                         <div class="list-group">`;
                semFotosReais.forEach(({ item }) => {
                    html += `
                        <div class="list-group-item d-flex justify-content-between align-items-center text-muted">
                            <div>
                                <small class="text-primary fw-semibold">${item.codprod || 'N/A'}</small>
                                <span class="ms-2">${item.descrprod || 'Sem descrição'}</span>
                                <small class="text-muted ms-2">Seq: ${item.sequencia}</small>
                            </div>
                            <span class="badge bg-light text-muted border">Sem fotos</span>
                        </div>`;
                });
                html += `</div>`;
            }
        }

        if (itensSemFoto.length > 0) {
            html += `<div class="text-muted small mt-2">${itensSemFoto.length} item(ns) sem etiqueta ou foto.</div>`;
        }

        body.innerHTML = html || `<div class="alert alert-info">Nenhum item com fotos encontrado.</div>`;

    } catch (err) {
        console.error('Erro ao carregar fotos da avaria:', err);
        body.innerHTML = `<div class="alert alert-danger">
            <i class="fas fa-exclamation-circle me-2"></i>
            Erro ao carregar fotos: ${err.message}</div>`;
    }
}

window.abrirFotosAvaria = abrirFotosAvaria;
