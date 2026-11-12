// Continuação de consultar-avarias.js — seções: inicialização, sistema de baixas múltiplas, totalizadores.
// Ordem de carregamento importa: deve vir logo depois de consultar-avarias.js.

// INICIALIZAÇÃO

document.addEventListener('DOMContentLoaded', function() {
    console.log('Inicializando sistema de cache de filtros');
    
    // Restaurar filtros ao carregar a página
    restaurarFiltrosCache();
    
    // Salvar filtros quando o usuário pesquisar
    const btnFiltrar = document.getElementById('btnFiltrar'); // Adaptar ao seu botão
    if (btnFiltrar) {
        btnFiltrar.addEventListener('click', function() {
            salvarFiltrosCache();
        });
    }
    
    // Salvar filtros ao pressionar Enter nos campos
    ['filtroNota', 'filtroCodEmpresa', 'globalSearch'].forEach(id => {
        const campo = document.getElementById(id);
        if (campo) {
            campo.addEventListener('keypress', function(e) {
                if (e.key === 'Enter') {
                    salvarFiltrosCache();
                    aplicarFiltros();
                }
            });
        }
    });
    
    // Limpar cache quando o botão "Limpar Busca" for clicado
    const btnLimpar = document.querySelector('[onclick="clearSearch()"]');
    if (btnLimpar) {
        btnLimpar.addEventListener('click', function() {
            limparCacheFiltros();
        });
    }
});

// Salvar filtros antes de sair da página (ao clicar em ações)
window.addEventListener('beforeunload', function() {
    salvarFiltrosCache();
});

// SISTEMA DE BAIXAS MÚLTIPLAS

/**
 * Atualiza a contagem de registros selecionados
 */
function atualizarContagemSelecionados() {
    const checkboxes = document.querySelectorAll('.checkbox-avaria:checked');
    const count = checkboxes.length;
    
    // Atualizar contador no botão
    document.getElementById('countSelecionados').textContent = count;
    
    // Mostrar/ocultar botão de baixas múltiplas
    const btnBaixas = document.getElementById('btnBaixasMultiplas');
    if (btnBaixas) {
        btnBaixas.style.display = count > 0 ? 'block' : 'none';
    }
    
    console.log(`${count} avaria(s) selecionada(s)`);
}

/**
 * Seleciona ou desmarca checkboxes da tabela.
 * CORRIGIDO: Ao MARCAR (checked=true), seleciona apenas linhas VISÍVEIS.
 * Ao DESMARCAR (checked=false), limpa TODAS (inclusive ocultas) para evitar
 * seleções fantasma que persistem ao remover filtros.
 */
function selecionarTodos(checked) {
    document.querySelectorAll('.checkbox-avaria').forEach(checkbox => {
        const row = checkbox.closest('tr');
        const rowVisible = row && row.style.display !== 'none';

        if (checked) {
            // Só marca se a linha estiver visível
            if (rowVisible) {
                checkbox.checked = true;
            }
        } else {
            // Desmarca TUDO (inclusive ocultos) ao desmarcar o "selecionar todos"
            checkbox.checked = false;
        }
    });

    atualizarContagemSelecionados();
}

/**
 * Abre o modal de baixas múltiplas.
 * CORRIGIDO: Detecta avarias já baixadas (com data_pagamento) e exibe aviso.
 *              Somente as SEM data serão enviadas à API.
 */
async function abrirModalBaixasMultiplas() {
    const checkboxes = document.querySelectorAll('.checkbox-avaria:checked');
    if (checkboxes.length === 0) {
        await _alertaCritico('Nenhuma avaria selecionada', 'Selecione pelo menos uma avaria para baixar.');
        return;
    }

    // ── Separar em "sem data" e "já baixadas" ─────────────────────────────
    const semData = [];
    const jaBaixadas = [];

    checkboxes.forEach(cb => {
        const row = cb.closest('tr');
        const avariaId = cb.dataset.avariaId;
        const dataPag = row ? (row.dataset.dataPagamento || '').trim() : '';

        if (dataPag) {
            jaBaixadas.push({ id: avariaId, data: dataPag });
        } else {
            semData.push(avariaId);
        }
    });

    // ── Atualizar totalizador: mostra apenas as que serão baixadas ─────────
    const qtdParaBaixar = semData.length || checkboxes.length;
    document.getElementById('totalSelecionadasModal').textContent = qtdParaBaixar;
    document.getElementById('idsDisplay').textContent = semData.join(', ') || 'Nenhuma nova';

    // ── Exibir / ocultar aviso de já-baixadas ─────────────────────────────
    const containerAviso = document.getElementById('alertaJaBaixadas');
    if (containerAviso) {
        if (jaBaixadas.length > 0) {
            const lista = jaBaixadas.map(j => {
                const p = j.data.split('-');
                const dataFmt = p.length === 3 ? `${p[2]}/${p[1]}/${p[0]}` : j.data;
                return `<li><code>${j.id}</code> — pago em <strong class="text-danger">${dataFmt}</strong></li>`;
            }).join('');

            containerAviso.innerHTML = `
                <div class="alert alert-warning d-flex gap-2 mb-3">
                    <i class="fas fa-exclamation-triangle fa-lg text-warning mt-1 flex-shrink-0"></i>
                    <div>
                        <strong>${jaBaixadas.length} avaria(s) já possui(em) data de pagamento e NÃO serão alteradas.</strong>
                        <p class="mb-1 mt-1 small">A data registrada anteriormente será preservada. Verifique a seleção:</p>
                        <ul class="mb-0 small">${lista}</ul>
                    </div>
                </div>`;
            containerAviso.style.display = 'block';
        } else {
            containerAviso.innerHTML = '';
            containerAviso.style.display = 'none';
        }
    }

    // ── Definir data padrão como hoje ─────────────────────────────────────
    const hoje = new Date().toISOString().split('T')[0];
    document.getElementById('dataPagamentoMultipla').value = hoje;

    // ── Abrir modal ───────────────────────────────────────────────────────
    const modal = new bootstrap.Modal(document.getElementById('modalBaixasMultiplas'));
    modal.show();
}

/**
 * Executa as baixas múltiplas.
 * CORRIGIDO: Envia à API APENAS avarias sem data_pagamento existente.
 *              Avarias já baixadas são ignoradas (data preservada).
 */
async function executarBaixasMultiplas() {
    // ── Separar IDs: sem data (enviar) vs já baixadas (ignorar) ───────────
    const checkboxes = document.querySelectorAll('.checkbox-avaria:checked');
    const idsSemData = [];
    const idsJaBaixadas = [];

    checkboxes.forEach(cb => {
        const row = cb.closest('tr');
        const avariaId = cb.dataset.avariaId;
        const dataPag = row ? (row.dataset.dataPagamento || '').trim() : '';
        (dataPag ? idsJaBaixadas : idsSemData).push(avariaId);
    });

    if (idsSemData.length === 0) {
        await _alertaCritico('Nada a atualizar', 'Todas as avarias selecionadas já possuem data de pagamento.');
        return;
    }

    const dataPagamento = document.getElementById('dataPagamentoMultipla').value;
    const observacao = document.getElementById('observacaoMultipla').value;

    if (!dataPagamento) {
        await _alertaCritico('Data obrigatória', 'Por favor, informe a data de pagamento.');
        return;
    }

    // ── Confirmação com aviso das ignoradas ────────────────────────────────
    const p = dataPagamento.split('-');
    const dataFmt = p.length === 3 ? `${p[2]}/${p[1]}/${p[0]}` : dataPagamento;
    let msgConfirm = `Confirma a baixa de ${idsSemData.length} avaria(s) com data ${dataFmt}?`;
    if (idsJaBaixadas.length > 0) {
        msgConfirm += `<br><br>${idsJaBaixadas.length} avaria(s) já baixada(s) serão ignoradas (data preservada).`;
    }
    const confirmouBaixa = await _confirmarAcaoCritica('Confirmar baixa múltipla', msgConfirm, 'Confirmar', 'Cancelar');
    if (!confirmouBaixa) return;

    try {
        const btnConfirmar = event.target;
        const textoOriginal = btnConfirmar.innerHTML;
        btnConfirmar.disabled = true;
        btnConfirmar.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i>Processando...';

        // ── Enviar APENAS os IDs sem data ──────────────────────────────────
        const response = await fetch(WMS_CONFIG.urls.baixasMultiplas, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ids: idsSemData,
                data_pagamento: dataPagamento,
                observacao: observacao
            })
        });

        const result = await response.json();

        if (result.success) {
            let msgOk = `${result.atualizados} avaria(s) atualizada(s) com sucesso!`;
            if (idsJaBaixadas.length > 0) {
                msgOk += `<br><br>${idsJaBaixadas.length} avaria(s) já possuíam data e foram ignoradas (data preservada).`;
            }
            await _alertaCritico('Baixa concluída', msgOk);

            bootstrap.Modal.getInstance(document.getElementById('modalBaixasMultiplas')).hide();
            salvarFiltrosCache();
            window.location.reload();
        } else {
            await _alertaCritico('Erro', traduzirErroTecnico(result.message || result.error));
            btnConfirmar.disabled = false;
            btnConfirmar.innerHTML = textoOriginal;
        }

    } catch (error) {
        console.error('Erro ao executar baixas:', error);
        await _alertaCritico('Erro', traduzirErroTecnico(error.message));
    }
}

// Inicializar ao carregar
document.addEventListener('DOMContentLoaded', function() {
    atualizarContagemSelecionados();
});
    
function generateFilterRow() {
    const filterRow = document.getElementById('filterRow');
    if (!filterRow) return;

    filterRow.innerHTML = '';

    COLUMN_DEFS.forEach(col => {
        const th = document.createElement('th');

        if (col.visible === false) {
            th.style.display = 'none';
        }

        if (col.filterable) {
            const input = document.createElement('input');
            input.type = 'text';
            input.className = 'form-control form-control-sm column-filter-individual';
            input.setAttribute('data-column', col.physicalIndex);
            input.placeholder = 'Filtrar...';

            // CADA input tem seu próprio debounce independente
            // Filtros de coluna usam delay curto (150ms) para responsividade
            const debouncedApply = _debounce(applyFilters, 300);

            input.addEventListener('input', function() {
                columnFilterValues[col.physicalIndex] = this.value;
                debouncedApply();
            });
            th.appendChild(input);
        } else {
            th.innerHTML = '<small class="text-muted"></small>';
        }
        filterRow.appendChild(th);
    });
}

function updateSelectedColumnsText() {
    const checkboxes = document.querySelectorAll('#columnsDropdownMenu input[type="checkbox"]');
    const textElement = document.getElementById('columnsSelectedText');
    if (!textElement) return;
    
    // Update the list of physical indices selected for global search
    selectedGlobalFilterPhysicalIndices = Array.from(checkboxes)
                                             .filter(cb => cb.checked)
                                             .map(cb => parseInt(cb.value));

    const totalFilterable = COLUMN_DEFS.filter(c => c.filterable).length;
    const checkedCount = selectedGlobalFilterPhysicalIndices.length;

    if (checkedCount === 0) textElement.textContent = 'Nenhuma coluna selecionada';
    else if (checkedCount === totalFilterable) textElement.textContent = 'Todas as colunas';
    else textElement.textContent = `${checkedCount} de ${totalFilterable} colunas`;

    // Trigger filtering immediately
    applyFilters();
}

function selectAllColumns() {
    document.querySelectorAll('#columnsDropdownMenu input[type="checkbox"]').forEach(cb => cb.checked = true);
    updateSelectedColumnsText();
}

function deselectAllColumns() {
    document.querySelectorAll('#columnsDropdownMenu input[type="checkbox"]').forEach(cb => cb.checked = false);
    updateSelectedColumnsText();
}

// SISTEMA DE TOTALIZADORES

/**
 * Calcula e atualiza os totalizadores da tabela
 * 
 * Mapeamento de colunas (com checkbox na coluna 0):
 *   Cell 7  = Quantidade
 *   Cell 8  = Valor NFD (R$ formatado)
 *   Cell 14 = Valor Indenização (R$ formatado)
 */
function calcularTotalizadores() {
    const tabela = document.getElementById('tabelaAvarias');
    if (!tabela) return;
    
    const linhas = tabela.querySelectorAll('tbody tr:not([style*="display: none"])');
    
    let totalValorNFD = 0;
    let totalQuantidade = 0;
    let totalValorIndenizacao = 0;
    
    linhas.forEach(linha => {
        // Pular linhas sem células suficientes (ex: linha "nenhum resultado")
        if (linha.cells.length < 15) return;
        
        // Quantidade (cell index 7)
        const qtdText = linha.cells[7]?.textContent || '0';
        const qtd = parseFloat(qtdText.trim()) || 0;
        totalQuantidade += qtd;
        
        // Valor NFD (cell index 8)
        const valorNFD = parseValorMonetario(linha.cells[8]?.textContent || '0');
        totalValorNFD += valorNFD;
        
        // Valor Indenização (cell index 14)
        const valorIndenizacao = parseValorMonetario(linha.cells[14]?.textContent || '0');
        totalValorIndenizacao += valorIndenizacao;
    });
    
    // Atualizar elementos de total
    const elementoTotalNFD = document.getElementById('totalValorNFD');
    const elementoTotalQtd = document.getElementById('totalQuantidade');
    const elementoTotalIndenizacao = document.getElementById('totalValorIndenizacao');
    
    if (elementoTotalNFD) {
        elementoTotalNFD.textContent = formatarMoeda(totalValorNFD);
    }
    
    if (elementoTotalQtd) {
        elementoTotalQtd.textContent = totalQuantidade.toFixed(0);
    }
    
    if (elementoTotalIndenizacao) {
        elementoTotalIndenizacao.textContent = formatarMoeda(totalValorIndenizacao);
    }
    
    console.log('Totais calculados:', {
        valorNFD: totalValorNFD,
        quantidade: totalQuantidade,
        valorIndenizacao: totalValorIndenizacao
    });
}

/**
 * Função wrapper para exportação Excel
 * Retorna totalizadores no formato esperado pelo ExportExcel
 * 
 * @returns {Object} Dicionário com totais formatados
 */
function calcularTotalizadoresConsultar() {
    const tabela = document.getElementById('tabelaAvarias');
    if (!tabela) return {};
    
    const linhas = tabela.querySelectorAll('tbody tr:not([style*="display: none"])');
    
    let totalValorNFD = 0;
    let totalQuantidade = 0;
    let totalValorIndenizacao = 0;
    
    linhas.forEach(linha => {
        // Pular linhas sem células suficientes
        if (linha.cells.length < 15) return;
        
        // Cell 7 = Quantidade
        const qtdText = linha.cells[7]?.textContent?.trim() || '0';
        const quantidade = parseFloat(qtdText.replace(/[^\d,.-]/g, '').replace(',', '.')) || 0;
        totalQuantidade += quantidade;
        
        // Cell 8 = Valor NFD
        const nfdText = linha.cells[8]?.textContent?.trim() || 'R$ 0,00';
        const valorNFD = parseFloat(
            nfdText.replace('R$', '').replace(/\./g, '').replace(',', '.').trim()
        ) || 0;
        totalValorNFD += valorNFD;
        
        // Cell 14 = Valor Indenização
        const indText = linha.cells[14]?.textContent?.trim() || 'R$ 0,00';
        const valorIndenizacao = parseFloat(
            indText.replace('R$', '').replace(/\./g, '').replace(',', '.').trim()
        ) || 0;
        totalValorIndenizacao += valorIndenizacao;
    });
    
    // Retornar no formato esperado pelo backend (chaves devem corresponder aos nomes das colunas)
    return {
        'quantidade': totalQuantidade.toFixed(0),
        'valor_nfd': totalValorNFD.toFixed(2),
        'valor_indenizacao': totalValorIndenizacao.toFixed(2)
    };
}

/**
 * FUNÇÃO AUXILIAR: Converte string monetária para número
 * Exemplos:
 *   "R$ 1.234,56" → 1234.56
 *   "R$ 0,00" → 0
 *   "123,45" → 123.45
 */
function parseValorMonetario(valor) {
    if (!valor) return 0;
    
    // Remover "R$", pontos e substituir vírgula por ponto
    const numero = valor
        .replace('R$', '')
        .replace(/\./g, '')
        .replace(',', '.')
        .trim();
    
    return parseFloat(numero) || 0;
}

/**
 * FUNÇÃO AUXILIAR: Formata número para moeda brasileira
 * Exemplos:
 *   1234.56 → "R$ 1.234,56"
 *   0 → "R$ 0,00"
 */
function formatarMoeda(valor) {
    return new Intl.NumberFormat('pt-BR', {
        style: 'currency',
        currency: 'BRL'
    }).format(valor);
}

// Calcular totais ao carregar a página.
// MutationObserver REMOVIDO — causava O(n²): 1 callback por linha × querySelectorAll completo.
// applyFilters() já chama calcularTotalizadores() ao final — zero necessidade do observer.
document.addEventListener('DOMContentLoaded', function() {
    calcularTotalizadores();
});

