/**
 * SPLIT SIMPLIFICADO DE ITENS - EDIÇÃO DE AVARIAS
 * Versão simplificada que pergunta quantas divisões fazer e distribui
 * automaticamente as quantidades.
 * 
 * IMPORTANTE: Itens novos (splitados) terão:
 * - sequencia: calculada como maxSeq + 1
 * - _status: "novo" (para backend identificar e inserir no fim da planilha)
 * 
 * Depende de: itensAvariaEdicao (array global do modal de edição)
 */

// Array global para armazenar itens da avaria em edição
let itensAvariaEdicao = [];

/**
 * Divide um item em múltiplas linhas com distribuição automática
 * @param {number} index - Índice do item no array
 */
async function splitItemEdicao(index) {
    // Validar índice
    if (!itensAvariaEdicao || index < 0 || index >= itensAvariaEdicao.length) {
        console.error('[SPLIT-EDICAO] Índice inválido:', index);
        return;
    }

    const itemOriginal = itensAvariaEdicao[index];
    const qtdTotal = parseFloat(itemOriginal.qtd_avariada || 0);

    // Validação: Quantidade deve ser maior que 1
    if (qtdTotal <= 1) {
        await _alertaCritico('Divisão inválida', 'A quantidade deve ser maior que 1 para realizar a divisão.');
        return;
    }

    // Validação: Item já possui etiqueta ativa?
    if (itemOriginal.status_etiqueta === 'ativa' && itemOriginal.id_etiqueta) {
        await _alertaCritico(
            'Divisão não permitida',
            'Não é possível dividir itens com etiqueta já impressa/ativa.<br>Motivo: a etiqueta impressa refere-se à quantidade original.'
        );
        return;
    }

    console.log(`[SPLIT-EDICAO] Dividindo item ${index}:`, itemOriginal);

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
        console.log('[SPLIT-EDICAO] Usuário cancelou');
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

    console.log(`[SPLIT-EDICAO] Distribuição calculada:`, distribuicao);

    // Confirmar com usuário
    const confirmacao = await _confirmarAcaoCritica(
        'Confirmar divisão',
        `Produto: ${itemOriginal.descrprod}<br>` +
        `Quantidade Total: ${qtdTotal}<br>` +
        `Número de Linhas: ${numLinhas}<br><br>` +
        `Distribuição:<br>` +
        distribuicao.map((qtd, i) => `Linha ${i + 1}: ${qtd} unidade(s)`).join('<br>') +
        `<br><br><strong>Lembre-se de salvar as alterações após o split!</strong>`,
        'Confirmar', 'Cancelar'
    );

    if (!confirmacao) {
        console.log('[SPLIT-EDICAO] Usuário cancelou confirmação');
        return;
    }

    // EXECUTAR SPLIT
    executarSplitEdicao(index, distribuicao);
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
function executarSplitEdicao(index, distribuicao) {
    const itemOriginal = itensAvariaEdicao[index];
    
    // Calcular maior sequência atual da avaria
    const maxSequencia = Math.max(...itensAvariaEdicao.map(i => parseInt(i.sequencia) || 0));
    
    // Atualizar item original com primeira quantidade
    itemOriginal.qtd_avariada = distribuicao[0].toString();
    itemOriginal._modificado = true; // Flag para backend saber que precisa UPDATE
    
    // Criar novas linhas para as demais quantidades
    for (let i = 1; i < distribuicao.length; i++) {
        const novaSequencia = maxSequencia + i;
        
        const novoItem = {
            avaria_id: itemOriginal.avaria_id,
            sequencia: novaSequencia,
            codprod: itemOriginal.codprod,
            descrprod: itemOriginal.descrprod,
            qtdneg: itemOriginal.qtdneg,
            qtd_avariada: distribuicao[i].toString(),
            localizacao: itemOriginal.localizacao || itemOriginal.codlocalorig || "", // Herdar o local do item original
            valor_item: itemOriginal.valor_item,
            codlocalorig: itemOriginal.codlocalorig,
            codlocaldest: itemOriginal.codlocaldest || "",
            codgrupoprod: itemOriginal.codgrupoprod,
            codemp: itemOriginal.codemp,
            id_etiqueta: "",
            status_etiqueta: "pendente",
            _status: "novo", // FLAG CRÍTICA: Backend sabe que é item novo (INSERT)
            _isSplit: true,
            _splitNumero: i + 1 // Número da divisão
        };

        // Inserir após o item atual
        itensAvariaEdicao.splice(index + i, 0, novoItem);
    }

    console.log(`[SPLIT-EDICAO] ${distribuicao.length} linhas criadas`);

    // Atualizar UI
    renderizarTabelaItensEdicao();

    // Notificar usuário
    mostrarNotificacaoSplitEdicao(
        `${itemOriginal.descrprod} dividido em ${distribuicao.length} linhas!`,
        distribuicao
    );
}

/**
 * Renderiza a tabela de itens no modal de edição
 */
function renderizarTabelaItensEdicao() {
    const tbody = document.getElementById('editItensTableBody');
    
    if (!tbody || !itensAvariaEdicao || itensAvariaEdicao.length === 0) {
        if (tbody) {
            tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted">Nenhum item cadastrado</td></tr>';
        }
        return;
    }

    tbody.innerHTML = '';

    itensAvariaEdicao.forEach((item, index) => {
        const qtdAvariada = parseFloat(item.qtd_avariada || 0);
        const podeSerDividido = qtdAvariada > 1 && item.status_etiqueta !== 'ativa';
        const isNovoItem = item._status === 'novo';
        const isModificado = item._modificado === true;

        const row = document.createElement('tr');
        
        // Aplicar classe visual para itens modificados/novos
        if (isNovoItem) {
            row.classList.add('table-success'); // Verde claro para novos
        } else if (isModificado) {
            row.classList.add('table-warning'); // Amarelo claro para modificados
        }

        row.innerHTML = `
            <!-- Sequência -->
            <td class="text-center">
                ${item.sequencia}
                ${isNovoItem ? '<span class="badge bg-success ms-1" title="Item novo (será inserido)">NOVO</span>' : ''}
                ${isModificado ? '<span class="badge bg-warning ms-1" title="Item modificado">MOD</span>' : ''}
                ${item._isSplit ? `<span class="badge bg-info ms-1" title="Divisão #${item._splitNumero}">#${item._splitNumero}</span>` : ''}
            </td>
            
            <!-- Código -->
            <td>${item.codprod}</td>
            
            <!-- Descrição -->
            <td class="text-truncate" style="max-width: 250px;" title="${item.descrprod}">
                ${item.descrprod}
                ${item._isSplit ? '<i class="fas fa-columns text-info ms-2" title="Item dividido"></i>' : ''}
            </td>
            
            <!-- Qtd Avariada -->
            <td class="text-center">
                <strong>${qtdAvariada}</strong>
            </td>
            
            <!-- Localização -->
            <td>
                <span class="badge ${item.localizacao ? 'bg-primary' : 'bg-secondary'}">
                    ${item.localizacao || 'Não definida'}
                </span>
            </td>

            <!-- ✅ NOVO: Valor Item -->
            <td class="text-end fw-semibold text-success">
                ${typeof formatCurrency === 'function'
                    ? formatCurrency(item.valor_item || 0)
                    : (parseFloat(item.valor_item || 0)
                        .toLocaleString('pt-BR', {style:'currency', currency:'BRL'}))}
            </td>
            
            <!-- Status Etiqueta -->
            <td class="text-center">
                ${item.id_etiqueta ? `
                    <span class="badge ${item.status_etiqueta === 'ativa' ? 'bg-success' : 'bg-warning'}">
                        ${item.id_etiqueta}
                    </span>
                ` : '<span class="text-muted">-</span>'}
            </td>
            
            <!-- Ações -->
            <td class="text-center">
                <div class="btn-group btn-group-sm" role="group">
                    <!-- Botão Editar -->
                    <button type="button" 
                            class="btn btn-outline-primary" 
                            onclick="editarItemAvariaModal(${index})"
                            title="Editar quantidade e localização">
                        <i class="fas fa-edit"></i>
                    </button>
                    
                    <!-- Botão Split -->
                    ${podeSerDividido ? `
                        <button type="button" 
                                class="btn btn-outline-info" 
                                onclick="splitItemEdicao(${index})" 
                                title="Dividir item em múltiplas linhas">
                            <i class="fas fa-columns"></i>
                        </button>
                    ` : `
                        <button type="button" 
                                class="btn btn-outline-secondary" 
                                disabled 
                                title="${item.status_etiqueta === 'ativa' ? 'Etiqueta já impressa' : 'Quantidade insuficiente'}">
                            <i class="fas fa-columns"></i>
                        </button>
                    `}
                </div>
            </td>
        `;
        
        tbody.appendChild(row);
    });

    console.log(`[SPLIT-EDICAO] ${itensAvariaEdicao.length} itens renderizados`);
}

/**
 * Modal simples para editar quantidade/localização de um item
 * @param {number} index - Índice do item no array
 */
async function editarItemAvariaModal(index) {
    const item = itensAvariaEdicao[index];

    if (!item) {
        await _alertaCritico('Erro', 'Item não encontrado');
        return;
    }

    // Criar modal dinâmico
    const modalHtml = `
        <div class="modal fade" id="modalEditarItemRapido" tabindex="-1">
            <div class="modal-dialog modal-sm">
                <div class="modal-content">
                    <div class="modal-header bg-warning text-dark">
                        <h5 class="modal-title">
                            <i class="fas fa-edit me-2"></i>Editar Item
                        </h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body">
                        <div class="mb-3">
                            <label class="form-label fw-bold">${item.codprod} - ${item.descrprod}</label>
                        </div>
                        <div class="mb-3">
                            <label class="form-label">Quantidade Avariada</label>
                            <input type="number" 
                                   class="form-control" 
                                   id="editQtdRapida" 
                                   value="${item.qtd_avariada}"
                                   min="1"
                                   step="1">
                        </div>
                        <div class="mb-3">
                            <label class="form-label">Localização</label>
                            <input type="text" 
                                   class="form-control" 
                                   id="editLocalizacaoRapida" 
                                   value="${item.localizacao || ''}"
                                   placeholder="Digite a localização">
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancelar</button>
                        <button type="button" class="btn btn-primary" onclick="salvarEdicaoRapida(${index})">
                            <i class="fas fa-save me-2"></i>Salvar
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `;

    // Remover modal existente (se houver)
    const modalExistente = document.getElementById('modalEditarItemRapido');
    if (modalExistente) {
        modalExistente.remove();
    }

    // Adicionar novo modal ao DOM
    document.body.insertAdjacentHTML('beforeend', modalHtml);

    // Abrir modal
    const modal = new bootstrap.Modal(document.getElementById('modalEditarItemRapido'));
    modal.show();
}

/**
 * Salva edição rápida de um item
 * @param {number} index - Índice do item no array
 */
async function salvarEdicaoRapida(index) {
    const item = itensAvariaEdicao[index];

    const novaQtd = parseFloat(document.getElementById('editQtdRapida').value);
    const novaLocalizacao = document.getElementById('editLocalizacaoRapida').value.trim();

    // Validações
    if (isNaN(novaQtd) || novaQtd < 1) {
        await _alertaCritico('Valor inválido', 'Quantidade deve ser maior que zero!');
        return;
    }

    // Atualizar item
    item.qtd_avariada = novaQtd.toString();
    item.localizacao = novaLocalizacao;
    
    // Marcar como modificado (se não for novo)
    if (item._status !== 'novo') {
        item._modificado = true;
    }

    console.log(`[SPLIT-EDICAO] Item ${index} atualizado:`, { qtd: novaQtd, local: novaLocalizacao });

    // Fechar modal
    bootstrap.Modal.getInstance(document.getElementById('modalEditarItemRapido')).hide();

    // Atualizar UI
    renderizarTabelaItensEdicao();
}

/**
 * Mostra notificação de sucesso ao dividir item
 */
function mostrarNotificacaoSplitEdicao(mensagem, distribuicao) {
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
        <br><small class="text-warning"><i class="fas fa-exclamation-triangle me-1"></i>Lembre-se de salvar as alterações!</small>
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    
    document.body.appendChild(notification);
    
    setTimeout(() => {
        notification.remove();
    }, 5000);
}

/**
 * Prepara dados para envio ao backend
 * Separa itens em: novos, modificados e inalterados
 */
function prepararDadosParaSalvarEdicao() {
    const itensNovos = [];
    const itensModificados = [];
    const itensInalterados = [];

    itensAvariaEdicao.forEach(item => {
        if (item._status === 'novo') {
            // Item novo (splitado) - será inserido no fim da planilha
            itensNovos.push({
                avaria_id: item.avaria_id,
                sequencia: item.sequencia,
                codprod: item.codprod,
                descrprod: item.descrprod,
                qtdneg: item.qtdneg,
                qtd_avariada: parseFloat(item.qtd_avariada),
                localizacao: item.localizacao || '',
                valor_item: parseFloat(item.valor_item || 0),
                codlocalorig: item.codlocalorig || '',
                codlocaldest: item.codlocaldest || '',
                codgrupoprod: item.codgrupoprod || '',
                codemp: item.codemp || ''
            });
        } else if (item._modificado) {
            // Item existente modificado - será atualizado
            itensModificados.push({
                avaria_id: item.avaria_id,
                sequencia: item.sequencia,
                qtd_avariada: parseFloat(item.qtd_avariada),
                localizacao: item.localizacao || ''
            });
        } else {
            // Item inalterado
            itensInalterados.push(item);
        }
    });

    console.log(`[SPLIT-EDICAO] Dados preparados:`, {
        novos: itensNovos.length,
        modificados: itensModificados.length,
        inalterados: itensInalterados.length
    });

    return {
        itens_novos: itensNovos,
        itens_modificados: itensModificados,
        itens_inalterados: itensInalterados
    };
}

/**
 * Inicializar módulo de split para edição
 */
function inicializarSplitEdicao() {
    console.log('[SPLIT-EDICAO] Módulo inicializado (versão simplificada)');
    
    // Interceptar a função preencherItensAvaria original
    if (typeof preencherItensAvaria === 'function') {
        const preencherItensAvariaOriginal = preencherItensAvaria;
        
        window.preencherItensAvaria = function(itens) {
            // Armazenar itens no array global
            itensAvariaEdicao = itens || [];
            
            // Renderizar com funcionalidade de split
            renderizarTabelaItensEdicao();
        };
        
        console.log('[SPLIT-EDICAO] Função preencherItensAvaria interceptada');
    }
}

// Inicializar ao carregar o DOM
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', inicializarSplitEdicao);
} else {
    inicializarSplitEdicao();
}

console.log('[SPLIT-EDICAO] Módulo carregado (versão simplificada com prompt)');
