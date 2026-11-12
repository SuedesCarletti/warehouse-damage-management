/**
 * BAIXA MÚLTIPLA JS — Atualização em lote de Data de Pagamento + Status
 * 
 * Permite selecionar múltiplas avarias e aplicar a mesma data de pagamento,
 * status de indenização e observação a todos os registros selecionados.
 * 
 * DEPENDÊNCIAS: 
 *   - avarias-common.js (showNotification, formatCurrency, parseDecimalValue)
 *   - cache-estado.js (CacheEstado — para preservar filtros após reload)
 *   - Bootstrap 5 (Modal)
 * 
 * USO no template consultar_avarias.html:
 *   1. Adicionar checkbox na coluna de cada linha (ver HTML abaixo)
 *   2. Incluir o modal HTML (ver _getModalHTML())
 *   3. Incluir barra de ações flutuante
 *   4. Carregar este script
 * 
 *   <script src="{{ url_for('static', filename='js/wms/baixa-multipla.js') }}"></script>
 */

const BaixaMultipla = (function() {
    'use strict';

    // ESTADO
    
    let selecionados = new Map(); // Map<avariaId, { nota, cod_empresa, valor_nf }>
    let modalInstance = null;

    // CONFIGURAÇÃO

    const CONFIG = {
        // IDs dos elementos no DOM
        CHECKBOX_CLASS: 'avaria-check-lote',
        CHECKBOX_ALL_ID: 'selecionarTodosLote',
        BARRA_ID: 'barraAcoesLote',
        BARRA_QTD_ID: 'qtdSelecionadosLote',
        MODAL_ID: 'modalBaixaMultipla',
        FORM_DATA_PGTO_ID: 'dataPagamentoLote',
        FORM_STATUS_ID: 'statusIndenizacaoLote',
        FORM_OBS_ID: 'observacaoLote',
        LISTA_SELECIONADOS_ID: 'listaBaixaMultipla',
        QTD_MODAL_ID: 'qtdBaixaModal',
        BTN_CONFIRMAR_ID: 'btnConfirmarBaixa',
        
        // API
        API_URL: '/wms/api/avarias/baixa-multipla',
        
        // Limites
        MAX_SELECAO: 100
    };

    // SELEÇÃO DE CHECKBOXES

    /**
     * Toggle de checkbox individual
     * @param {HTMLInputElement} checkbox - O checkbox clicado
     */
    function toggleCheckbox(checkbox) {
        const avariaId = checkbox.value;
        const row = checkbox.closest('tr');
        
        if (checkbox.checked) {
            if (selecionados.size >= CONFIG.MAX_SELECAO) {
                checkbox.checked = false;
                showNotification('warning', `Máximo de ${CONFIG.MAX_SELECAO} avarias por vez.`);
                return;
            }
            
            // Extrair dados da linha para exibir no modal
            const dados = _extrairDadosLinha(row, avariaId);
            selecionados.set(avariaId, dados);
            row.classList.add('table-warning');
        } else {
            selecionados.delete(avariaId);
            row.classList.remove('table-warning');
        }

        _atualizarUI();
    }

    /**
     * Toggle "Selecionar Todos" (apenas linhas visíveis)
     * @param {HTMLInputElement} checkboxAll
     */
    function toggleSelecionarTodos(checkboxAll) {
        const checkboxes = document.querySelectorAll(`.${CONFIG.CHECKBOX_CLASS}`);
        
        checkboxes.forEach(cb => {
            const row = cb.closest('tr');
            // Só selecionar linhas visíveis
            if (row && row.style.display !== 'none') {
                if (checkboxAll.checked && selecionados.size < CONFIG.MAX_SELECAO) {
                    cb.checked = true;
                    const dados = _extrairDadosLinha(row, cb.value);
                    selecionados.set(cb.value, dados);
                    row.classList.add('table-warning');
                } else if (!checkboxAll.checked) {
                    cb.checked = false;
                    selecionados.delete(cb.value);
                    row.classList.remove('table-warning');
                }
            }
        });

        _atualizarUI();
    }

    /**
     * Extrair dados relevantes da linha da tabela
     */
    function _extrairDadosLinha(row, avariaId) {
        if (!row) return { id: avariaId, nota: '-', cod_empresa: '-', valor_nf: 0, data_pagamento: '' };

        const nota = row.dataset.nota || _getTextByColumn(row, 'nota') || '-';
        const codEmpresa = row.dataset.codEmpresa || _getTextByColumn(row, 'cod_empresa') || '-';
        const valorNf = parseDecimalValue(
            row.dataset.valorNf || _getTextByColumn(row, 'valor_nf') || '0'
        );
        const valorNfd = parseDecimalValue(
            row.dataset.valorNfd || _getTextByColumn(row, 'valor_nfd') || '0'
        );

        // NOVO: captura data_pagamento do atributo adicionado no template
        const dataPagamento = row.dataset.dataPagamento || '';

        return {
            id: avariaId,
            nota: nota,
            cod_empresa: codEmpresa,
            valor_nf: valorNf,
            valor_nfd: valorNfd,
            data_pagamento: dataPagamento  // ← NOVO
        };
    }

    /**
     * Busca texto de uma coluna pelo nome
     */
    function _getTextByColumn(row, columnKey) {
        // Tentar por data-field
        const cell = row.querySelector(`td[data-field="${columnKey}"]`);
        if (cell) return cell.textContent.trim();

        // Tentar por data-search
        const cellSearch = row.querySelector(`td[data-search]`);
        if (cellSearch) return cellSearch.dataset.search;

        return null;
    }

    /**
     * Limpar todas as seleções
     */
    function limparSelecao() {
        selecionados.clear();
        
        document.querySelectorAll(`.${CONFIG.CHECKBOX_CLASS}`).forEach(cb => {
            cb.checked = false;
            const row = cb.closest('tr');
            if (row) row.classList.remove('table-warning');
        });
        
        const checkAll = document.getElementById(CONFIG.CHECKBOX_ALL_ID);
        if (checkAll) checkAll.checked = false;

        _atualizarUI();
    }

    // UI

    /**
     * Atualiza visibilidade da barra de ações e contadores
     */
    function _atualizarUI() {
        const qtd = selecionados.size;
        
        // Barra flutuante
        const barra = document.getElementById(CONFIG.BARRA_ID);
        if (barra) {
            barra.style.display = qtd > 0 ? 'block' : 'none';
        }

        // Contador na barra
        const contador = document.getElementById(CONFIG.BARRA_QTD_ID);
        if (contador) {
            contador.textContent = qtd;
        }

        // Atualizar estado do checkbox "Selecionar Todos"
        const checkAll = document.getElementById(CONFIG.CHECKBOX_ALL_ID);
        if (checkAll) {
            const totalVisiveis = document.querySelectorAll(
                `.${CONFIG.CHECKBOX_CLASS}:not([style*="display: none"])`
            ).length;
            checkAll.indeterminate = qtd > 0 && qtd < totalVisiveis;
            checkAll.checked = qtd > 0 && qtd === totalVisiveis;
        }
    }

    // MODAL

    /**
     * Abre o modal de baixa múltipla
     */
    function abrirModal() {
        if (selecionados.size === 0) {
            showNotification('warning', 'Selecione pelo menos uma avaria.');
            return;
        }

        // Atualizar contador no modal
        const qtdEl = document.getElementById(CONFIG.QTD_MODAL_ID);
        if (qtdEl) qtdEl.textContent = selecionados.size;

        // Preencher tabela de resumo
        _preencherListaSelecionados();

        // Limpar campos do formulário
        const dataPgto = document.getElementById(CONFIG.FORM_DATA_PGTO_ID);
        if (dataPgto) {
            // Default: data de hoje
            dataPgto.value = new Date().toISOString().split('T')[0];
        }
        
        const statusSelect = document.getElementById(CONFIG.FORM_STATUS_ID);
        if (statusSelect) statusSelect.value = '';
        
        const obsField = document.getElementById(CONFIG.FORM_OBS_ID);
        if (obsField) obsField.value = '';

        // Abrir modal
        const modalEl = document.getElementById(CONFIG.MODAL_ID);
        if (modalEl) {
            modalInstance = new bootstrap.Modal(modalEl);
            modalInstance.show();
        }
    }

    /**
     * Preenche a tabela de resumo no modal
     */
    function _preencherListaSelecionados() {
        const tbody = document.getElementById(CONFIG.LISTA_SELECIONADOS_ID);
        if (!tbody) return;

        let html = '';
        let totalValorNf = 0;
        let totalValorNfd = 0;
        const jasBaixadas = [];  // coleta as que já têm data_pagamento

        selecionados.forEach((dados, id) => {
            totalValorNf += dados.valor_nf || 0;
            totalValorNfd += dados.valor_nfd || 0;

            const jaBaixada = dados.data_pagamento && dados.data_pagamento.trim() !== '';
            if (jaBaixada) jasBaixadas.push({ id, dados });

            // Linha normal ou destacada (já baixada = fundo laranja claro)
            html += `
                <tr class="${jaBaixada ? 'table-warning' : ''}">
                    <td>
                        <code>${id}</code>
                        ${jaBaixada
                            ? `<span class="badge bg-warning text-dark ms-1" title="Já possui data de pagamento">
                                   <i class="fas fa-exclamation-triangle me-1"></i>Já baixada
                               </span>`
                            : ''}
                    </td>
                    <td>${dados.nota}</td>
                    <td class="text-center">${dados.cod_empresa}</td>
                    <td class="text-end">${formatCurrency(dados.valor_nf)}</td>
                    <td class="text-center">
                        <button type="button" class="btn btn-sm btn-outline-danger"
                                onclick="BaixaMultipla.removerDoModal('${id}')"
                                title="Remover">
                            <i class="fas fa-times"></i>
                        </button>
                    </td>
                </tr>
            `;
        });

        // Linha de total
        html += `
            <tr class="table-success fw-bold">
                <td colspan="3" class="text-end">Total:</td>
                <td class="text-end">${formatCurrency(totalValorNf)}</td>
                <td></td>
            </tr>
        `;

        tbody.innerHTML = html;

        // ── Exibir (ou ocultar) o alerta de já-baixadas no modal ────────────
        _renderizarAlerteJaBaixadas(jasBaixadas);
    }

    /**
     * Exibe ou oculta o banner de aviso de avarias já baixadas no modal.
     * Cria o elemento #alerteJaBaixadas na primeira chamada.
     *
     * @param {Array} jasBaixadas - [{ id, dados }] com data_pagamento preenchida
     */
    function _renderizarAlerteJaBaixadas(jasBaixadas) {
        // Encontrar o container do modal-body onde inseriremos o alerta
        const modalEl = document.getElementById(CONFIG.MODAL_ID);
        if (!modalEl) return;

        const modalBody = modalEl.querySelector('.modal-body');
        if (!modalBody) return;

        // Remover alerta anterior, se existir
        const alerteExistente = document.getElementById('alerteJaBaixadas');
        if (alerteExistente) alerteExistente.remove();

        // Se não há já-baixadas, não renderiza nada
        if (jasBaixadas.length === 0) return;

        // Montar lista dos IDs afetados com suas datas
        const listaHtml = jasBaixadas.map(({ id, dados }) => {
            const dataFmt = _formatDateBR(dados.data_pagamento) || dados.data_pagamento;
            return `<li>
                        <code>${id}</code>
                        — NF <strong>${dados.nota}</strong>
                        — Pago em: <strong class="text-danger">${dataFmt}</strong>
                    </li>`;
        }).join('');

        // Criar o bloco de alerta
        const alerte = document.createElement('div');
        alerte.id = 'alerteJaBaixadas';
        alerte.className = 'alert alert-warning d-flex gap-2 mt-0 mb-3';
        alerte.setAttribute('role', 'alert');
        alerte.innerHTML = `
            <div class="flex-shrink-0 pt-1">
                <i class="fas fa-exclamation-triangle fa-lg text-warning"></i>
            </div>
            <div>
                <strong>Atenção — ${jasBaixadas.length} avaria(s) já possui(em) data de pagamento!</strong>
                <p class="mb-1 mt-1 small">
                    As avarias listadas abaixo <strong>não terão suas datas alteradas</strong>
                    pois já foram baixadas anteriormente. Verifique se a seleção está correta
                    antes de confirmar.
                </p>
                <ul class="mb-0 small">${listaHtml}</ul>
            </div>
        `;

        // Inserir NO TOPO do modal-body (antes da tabela de selecionados)
        const primeiroFilho = modalBody.firstChild;
        modalBody.insertBefore(alerte, primeiroFilho);
    }

    /**
     * Remove uma avaria da seleção a partir do modal
     */
    function removerDoModal(avariaId) {
        selecionados.delete(avariaId);

        // Desmarcar checkbox na tabela
        const cb = document.querySelector(`.${CONFIG.CHECKBOX_CLASS}[value="${avariaId}"]`);
        if (cb) {
            cb.checked = false;
            const row = cb.closest('tr');
            if (row) row.classList.remove('table-warning');
        }

        // Se não sobrou nenhum, fechar modal
        if (selecionados.size === 0) {
            if (modalInstance) modalInstance.hide();
            showNotification('info', 'Nenhuma avaria selecionada.');
        } else {
            // Atualizar modal
            const qtdEl = document.getElementById(CONFIG.QTD_MODAL_ID);
            if (qtdEl) qtdEl.textContent = selecionados.size;
            _preencherListaSelecionados();
        }

        _atualizarUI();
    }

    // CONFIRMAR E ENVIAR

    /**
     * Confirma e envia a baixa múltipla para a API
     */
    async function confirmar() {
        const dataPgto = document.getElementById(CONFIG.FORM_DATA_PGTO_ID)?.value;
        const statusIndenizacao = document.getElementById(CONFIG.FORM_STATUS_ID)?.value;
        const observacao = document.getElementById(CONFIG.FORM_OBS_ID)?.value || '';

        // Validações
        if (!dataPgto) {
            showNotification('warning', 'Informe a data de pagamento.');
            return;
        }

        if (selecionados.size === 0) {
            showNotification('warning', 'Nenhuma avaria selecionada.');
            return;
        }

        // Confirmação
        const ids = Array.from(selecionados.keys());
        if (!confirm(`Confirma a baixa de ${ids.length} avaria(s) com data de pagamento ${_formatDateBR(dataPgto)}?`)) {
            return;
        }

        // Desabilitar botão
        const btnConfirmar = document.getElementById(CONFIG.BTN_CONFIRMAR_ID);
        if (btnConfirmar) {
            btnConfirmar.disabled = true;
            btnConfirmar.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i>Processando...';
        }

        try {
            const payload = {
                avaria_ids: [...selecionados],
                data_pagamento: dataPgto,
                status_indenizacao: statusIndenizacao,
                tipo_baixa: document.getElementById('inputTipoBaixa')?.value || 'total',
                observacao: observacao,
            };

            // Incluir status se selecionado
            if (statusIndenizacao) {
                payload.status_indenizacao = statusIndenizacao;
            }

            // Incluir observação se preenchida
            if (observacao.trim()) {
                payload.observacao = observacao.trim();
            }

            const response = await fetch(CONFIG.API_URL, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            const result = await response.json();

            if (result.success) {
                showNotification('success', result.message || `${result.atualizados} avaria(s) atualizada(s)`);

                // Fechar modal
                if (modalInstance) modalInstance.hide();

                // Salvar estado do cache antes de recarregar
                if (typeof CacheEstado !== 'undefined') {
                    CacheEstado.salvarEstadoAtual('consultar');
                }

                // Reportar erros individuais
                if (result.erros && result.erros.length > 0) {
                    console.warn('[BaixaMultipla] Erros parciais:', result.erros);
                    showNotification('warning', 
                        `${result.erros.length} avaria(s) com erro. Verifique o console.`,
                        6000
                    );
                }

                // Recarregar página (cache preservará filtros)
                setTimeout(() => window.location.reload(), 1500);
            } else {
                showNotification('error', result.error || 'Erro ao processar baixa múltipla.');
            }

        } catch (error) {
            console.error('[BaixaMultipla] Erro:', error);
            showNotification('error', `Erro de comunicação: ${error.message}`);
        } finally {
            // Restaurar botão
            if (btnConfirmar) {
                btnConfirmar.disabled = false;
                btnConfirmar.innerHTML = '<i class="fas fa-check me-2"></i>Confirmar Baixa';
            }
        }
    }

    /**
     * Formata data ISO para DD/MM/YYYY
     */
    function _formatDateBR(isoDate) {
        if (!isoDate) return '-';
        const parts = isoDate.split('-');
        if (parts.length === 3) return `${parts[2]}/${parts[1]}/${parts[0]}`;
        return isoDate;
    }

    // INICIALIZAÇÃO

    /**
     * Inicializa o módulo: registra listeners nos checkboxes.
     * Deve ser chamado após o DOM estar pronto.
     */
    function init() {
        // Registrar listener no "Selecionar Todos"
        const checkAll = document.getElementById(CONFIG.CHECKBOX_ALL_ID);
        if (checkAll) {
            checkAll.addEventListener('change', function() {
                toggleSelecionarTodos(this);
            });
        }

        // Registrar listeners nos checkboxes individuais (delegation)
        document.addEventListener('change', function(e) {
            if (e.target.classList.contains(CONFIG.CHECKBOX_CLASS)) {
                toggleCheckbox(e.target);
            }
        });

        if (typeof WMSLog !== 'undefined') {
            WMSLog.info('✅ BaixaMultipla inicializado');
        }
    }

    // API PÚBLICA

    return {
        CONFIG,
        init,
        toggleCheckbox,
        toggleSelecionarTodos,
        limparSelecao,
        abrirModal,
        removerDoModal,
        confirmar,
        
        // Acesso ao estado (para debug)
        getSelecionados: () => new Map(selecionados),
        getQuantidade: () => selecionados.size
    };
})();

// Exportar globalmente
window.BaixaMultipla = BaixaMultipla;
window.WMS = window.WMS || {};
window.WMS.BaixaMultipla = BaixaMultipla;

if (typeof WMSLog !== 'undefined') {
    WMSLog.info('✅ baixa-multipla.js carregado');
}
