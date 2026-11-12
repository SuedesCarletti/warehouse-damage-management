/**
 * SPLIT SIMPLIFICADO DE ITENS - REGISTRO DE AVARIAS
 * Versão simplificada que pergunta quantas divisões fazer e distribui
 * automaticamente as quantidades.
 * 
 * Depende de: itensNotaGlobal (array global)
 */

/**
 * Divide um item em múltiplas linhas com distribuição automática
 * @param {number} index - Índice do item no array
 */
async function splitItemRegistro(index) {
    // Validar índice
    if (!itensNotaGlobal || index < 0 || index >= itensNotaGlobal.length) {
        console.error('[SPLIT-REGISTRO] Índice inválido:', index);
        return;
    }

    const itemOriginal = itensNotaGlobal[index];
    const qtdTotal = parseFloat(itemOriginal.qtd_avariada || itemOriginal.qtdneg || 0);

    // Validação: Quantidade deve ser maior que 1
    if (qtdTotal <= 1) {
        await _alertaCritico('Divisão inválida', 'A quantidade deve ser maior que 1 para realizar a divisão.');
        return;
    }

    console.log(`[SPLIT-REGISTRO] Dividindo item ${index}:`, itemOriginal);

    // PERGUNTAR QUANTAS DIVISÕES
    const numDivisoes = prompt(
        `🔢 DIVIDIR ITEM EM MÚLTIPLAS LINHAS\n\n` +
        `Produto: ${itemOriginal.descrprod}\n` +
        `Quantidade Total: ${qtdTotal}\n\n` +
        `Em quantas linhas deseja dividir este item?\n` +
        `(Digite um número entre 2 e ${Math.floor(qtdTotal)})`,
        "2" // valor padrão
    );

    // Cancelou ou não digitou nada
    if (!numDivisoes) {
        console.log('[SPLIT-REGISTRO] Usuário cancelou');
        return;
    }

    // Converter para número
    const numLinhas = parseInt(numDivisoes);

    // Validações
    if (isNaN(numLinhas) || numLinhas < 2) {
        await _alertaCritico('Divisão inválida', 'Você deve dividir em pelo menos 2 linhas!');
        return;
    }

    if (numLinhas > qtdTotal) {
        await _alertaCritico(
            'Divisão inválida',
            `Não é possível dividir ${qtdTotal} unidades em ${numLinhas} linhas!<br>Cada linha precisa ter pelo menos 1 unidade.`
        );
        return;
    }

    // CALCULAR DISTRIBUIÇÃO AUTOMÁTICA
    const distribuicao = calcularDistribuicao(qtdTotal, numLinhas);

    console.log(`[SPLIT-REGISTRO] Distribuição calculada:`, distribuicao);

    // Confirmar com usuário
    const confirmacao = await _confirmarAcaoCritica(
        'Confirmar divisão',
        `Produto: ${itemOriginal.descrprod}<br>` +
        `Quantidade Total: ${qtdTotal}<br>` +
        `Número de Linhas: ${numLinhas}<br><br>` +
        `Distribuição:<br>` +
        distribuicao.map((qtd, i) => `Linha ${i + 1}: ${qtd} unidade(s)`).join('<br>'),
        'Confirmar', 'Cancelar'
    );

    if (!confirmacao) {
        console.log('[SPLIT-REGISTRO] Usuário cancelou confirmação');
        return;
    }

    // EXECUTAR SPLIT
    executarSplitRegistro(index, distribuicao);
}

/**
 * Calcula a distribuição automática de quantidades
 * @param {number} total - Quantidade total
 * @param {number} numLinhas - Número de linhas a criar
 * @returns {Array<number>} - Array com as quantidades para cada linha
 */
function calcularDistribuicao(total, numLinhas) {
    const distribuicao = [];
    
    // Quantidade base para cada linha
    const qtdBase = Math.floor(total / numLinhas);
    
    // Resto a distribuir
    let resto = total - (qtdBase * numLinhas);
    
    // Distribuir
    for (let i = 0; i < numLinhas; i++) {
        let qtdLinha = qtdBase;
        
        // Adicionar 1 unidade do resto nas primeiras linhas
        if (resto > 0) {
            qtdLinha += 1;
            resto -= 1;
        }
        
        distribuicao.push(qtdLinha);
    }
    
    return distribuicao;
}

/**
 * Executa o split criando as linhas conforme distribuição
 * @param {number} index - Índice do item original
 * @param {Array<number>} distribuicao - Array com quantidades
 */
function executarSplitRegistro(index, distribuicao) {
    const itemOriginal = itensNotaGlobal[index];
    
    // Atualizar item original com primeira quantidade
    itemOriginal.qtd_avariada = distribuicao[0].toString();
    
    // Criar novas linhas para as demais quantidades
    for (let i = 1; i < distribuicao.length; i++) {
        const novoItem = {
            sequencia: itemOriginal.sequencia, // Backend ajustará
            codprod: itemOriginal.codprod,
            descrprod: itemOriginal.descrprod,
            qtdneg: itemOriginal.qtdneg,
            qtd_avariada: distribuicao[i].toString(),
            localizacao: itemOriginal.localizacao || itemOriginal.codlocalorig || "", // Herdar o local do item original
            valor_item: itemOriginal.valor_item,
            codlocalorig: itemOriginal.codlocalorig,
            codgrupoprod: itemOriginal.codgrupoprod,
            _isSplit: true,
            _splitNumero: i + 1 // Número da divisão
        };

        // Inserir após o item atual
        itensNotaGlobal.splice(index + i, 0, novoItem);
    }

    console.log(`[SPLIT-REGISTRO] ${distribuicao.length} linhas criadas`);

    // Atualizar UI
    renderizarTabelaItensRegistro();

    // Atualizar campo oculto para POST
    if (typeof coletarItensSelecionados === 'function') {
        coletarItensSelecionados();
    }

    // Notificar usuário
    mostrarNotificacaoSplit(
        `${itemOriginal.descrprod} dividido em ${distribuicao.length} linhas!`,
        distribuicao
    );
}

/**
 * Renderiza a tabela de itens no modal de registro
 */
function renderizarTabelaItensRegistro() {
    const tbody = document.getElementById('itensNotaLista');
    
    if (!tbody || !itensNotaGlobal || itensNotaGlobal.length === 0) {
        console.warn('[SPLIT-REGISTRO] Nenhum item para renderizar');
        return;
    }

    tbody.innerHTML = '';

    itensNotaGlobal.forEach((item, index) => {
        const qtdAvariada = parseFloat(item.qtd_avariada || item.qtdneg || 0);
        const valorUnitario = parseFloat(item.valor_item || 0);
        const podeSerDividido = qtdAvariada > 1;

        const row = document.createElement('tr');
        
        // Destacar itens splitados
        if (item._isSplit) {
            row.classList.add('table-info');
        }
        
        row.innerHTML = `
            <!-- Checkbox -->
            <td class="text-center">
                <input type="checkbox" 
                       class="form-check-input item-checkbox" 
                       data-index="${index}" 
                       ${item._isChecked ? 'checked' : ''}>
            </td>
            
            <!-- Sequência -->
            <td class="text-center">
                ${item.sequencia || index + 1}
                ${item._isSplit ? `<span class="badge bg-info ms-1" title="Divisão #${item._splitNumero}">#${item._splitNumero}</span>` : ''}
            </td>
            
            <!-- Código -->
            <td>${item.codprod}</td>
            
            <!-- Produto -->
            <td class="text-truncate" style="max-width: 200px;" title="${item.descrprod}">
                ${item.descrprod}
                ${item._isSplit ? '<i class="fas fa-columns text-info ms-2" title="Item dividido"></i>' : ''}
            </td>
            
            <!-- Qtd Nota -->
            <td class="text-center">${item.qtdneg || 0}</td>
            
            <!-- Qtd Avariada (Input) -->
            <td>
                <input type="number" 
                       class="form-control form-control-sm qtd-avariada" 
                       data-index="${index}" 
                       value="${item.qtd_avariada || ''}" 
                       min="0" 
                       max="${item.qtdneg || 999}"
                       step="1">
            </td>
            
            <!-- Localização -->
            <td>
                <input type="text" 
                       class="form-control form-control-sm localizacao" 
                       data-index="${index}" 
                       value="${item.localizacao || ''}" 
                       placeholder="Digite localização">
            </td>
            
            <!-- Valor Unitário -->
            <td class="text-end">R$ ${valorUnitario.toFixed(2)}</td>
            
            <!-- Ações (Botão Split) -->
            <td class="text-center">
                ${podeSerDividido ? `
                    <button type="button" 
                            class="btn btn-sm btn-outline-info" 
                            onclick="splitItemRegistro(${index})" 
                            title="Dividir item em múltiplas linhas">
                        <i class="fas fa-columns"></i>
                    </button>
                ` : `
                    <button type="button" 
                            class="btn btn-sm btn-outline-secondary" 
                            disabled 
                            title="Quantidade insuficiente para dividir">
                        <i class="fas fa-columns"></i>
                    </button>
                `}
            </td>
        `;
        
        tbody.appendChild(row);
    });

    // Reativar listeners
    document.querySelectorAll('.item-checkbox').forEach(checkbox => {
        checkbox.addEventListener('change', function() {
            const idx = parseInt(this.dataset.index);
            itensNotaGlobal[idx]._isChecked = this.checked;
        });
    });

    document.querySelectorAll('.qtd-avariada').forEach(input => {
        input.addEventListener('change', function() {
            const idx = parseInt(this.dataset.index);
            itensNotaGlobal[idx].qtd_avariada = this.value;
            if (typeof coletarItensSelecionados === 'function') {
                coletarItensSelecionados();
            }
        });
    });

    document.querySelectorAll('.localizacao').forEach(input => {
        input.addEventListener('change', function() {
            const idx = parseInt(this.dataset.index);
            itensNotaGlobal[idx].localizacao = this.value;
        });
    });

    console.log(`[SPLIT-REGISTRO] ${itensNotaGlobal.length} itens renderizados`);
}

/**
 * Mostra notificação de sucesso ao dividir item
 */
function mostrarNotificacaoSplit(mensagem, distribuicao) {
    const detalhes = distribuicao 
        ? `<br><small class="mt-1 d-block">Distribuição: ${distribuicao.join(', ')} unidades</small>`
        : '';
        
    const notification = document.createElement('div');
    notification.className = 'alert alert-success alert-dismissible fade show position-fixed';
    notification.style.cssText = 'top: 80px; right: 20px; z-index: 9999; min-width: 350px;';
    notification.innerHTML = `
        <i class="fas fa-check-circle me-2"></i>
        <strong>Split concluído!</strong><br>
        <small>${mensagem}</small>
        ${detalhes}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    
    document.body.appendChild(notification);
    
    setTimeout(() => {
        notification.remove();
    }, 4000);
}

/**
 * Inicializar módulo
 */
function inicializarSplitRegistro() {
    console.log('[SPLIT-REGISTRO] Módulo inicializado (versão simplificada)');
}

// Inicializar ao carregar o DOM
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', inicializarSplitRegistro);
} else {
    inicializarSplitRegistro();
}

console.log('[SPLIT-REGISTRO] Módulo carregado (versão simplificada com prompt)');
