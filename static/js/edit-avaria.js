// ==================== edit-avaria.js v6.1 CORRIGIDA ====================
// Correções implementadas:
// 1. Usa data.status_list (não data.status) - formato correto da API
// 2. Usa status.status_type (não status.tipo) - campo correto do modelo
// 3. Prevenção de reload ao editar item (type="button")
// 4. Cálculo de valor de indenização baseado no tipo e percentual
// 5. Função checkMotivoChanged para alertar mudanças de motivo
// 6. Compatível com código original que funciona

console.log('edit-avaria.js v6.1 CARREGADO - Versão Final Corrigida');

// ==================== VARIÁVEIS GLOBAIS ====================

let avariaAtual = null;
let motivosDisponiveis = [];

// ==================== FUNÇÕES AUXILIARES ====================

function setFieldValue(id, value) {
    const el = document.getElementById(id);
    if (el) el.value = value || '';
}

function setTextContent(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text || '';
}

function getFieldValue(id) {
    const element = document.getElementById(id);
    return element ? element.value : '';
}

/**
 * Converte valor para decimal, tratando formato brasileiro
 * @param {string|number} value - Valor a converter
 * @returns {number} - Número decimal
 */
function parseDecimalValue(value) {
    if (value === null || value === undefined || value === '') {
        return 0;
    }
    
    // Se já é número, retornar
    if (typeof value === 'number') {
        return value;
    }
    
    // Converter para string e limpar
    let valueStr = String(value).trim();
    
    // Remover R$, espaços
    valueStr = valueStr.replace('R$', '').replace(/\s/g, '');
    
    // Se tem ponto E vírgula, é formato brasileiro (1.234,56)
    if (valueStr.indexOf('.') !== -1 && valueStr.indexOf(',') !== -1) {
        // Remover pontos de milhar e trocar vírgula por ponto
        valueStr = valueStr.replace(/\./g, '').replace(',', '.');
    } 
    // Se tem apenas vírgula, trocar por ponto
    else if (valueStr.indexOf(',') !== -1) {
        valueStr = valueStr.replace(',', '.');
    }
    
    // Converter para float
    const result = parseFloat(valueStr);
    return isNaN(result) ? 0 : result;
}

// ==================== CARREGAR STATUS NO MODAL ====================

async function loadStatusOptions() {
    console.log('Carregando status do Google Sheets via API...');
    
    try {
        // Buscar status via API
        const response = await fetch('/wms/status/api/list');
        const data = await response.json();
        
        if (!data.success) {
            console.error('Erro ao carregar status:', data.error);
            await _alertaCritico('Erro', traduzirErroTecnico(data.error));
            return;
        }

        // CORREÇÃO: Usar data.status_list (não data.status)
        const allStatus = data.status_list || [];
        console.log(`${allStatus.length} status carregados do servidor`);
        
        // Agrupar status por tipo
        const statusMap = {
            'estoque': [],
            'indenizacao': [],
            'geral': []
        };
        
        // CORREÇÃO: Usar status.status_type (não status.tipo)
        allStatus.forEach(status => {
            const type = status.status_type || 'geral';
            if (statusMap[type]) {
                statusMap[type].push(status);
            } else {
                console.warn(`Status type desconhecido: ${type}`);
            }
        });

        // Ordenar status por campo 'ordem'
        statusMap['estoque'].sort((a, b) => (a.ordem || 999) - (b.ordem || 999));
        statusMap['indenizacao'].sort((a, b) => (a.ordem || 999) - (b.ordem || 999));

        console.log(`Status Indenização: ${statusMap['indenizacao'].length}`);
        console.log(`Status Estoque: ${statusMap['estoque'].length}`);
        
        // Preencher dropdowns
        populateStatusSelect('editStatusIndenizacao', statusMap['indenizacao']);
        populateStatusSelect('editStatusEstoque', statusMap['estoque']);
        
        // CRÍTICO: Re-selecionar valores após popular dropdowns usando avariaAtual
        if (avariaAtual) {
            console.log('Re-selecionando valores de status após popular dropdowns...');
            setFieldValue('editStatusEstoque', avariaAtual.status_estoque);
            setFieldValue('editStatusIndenizacao', avariaAtual.status_indenizacao);
            console.log(`Status selecionados: Estoque="${avariaAtual.status_estoque}", Indenização="${avariaAtual.status_indenizacao}"`);
        }

        // Carregar motivos
        await carregarMotivosNoModal();
        
    } catch (error) {
        console.error('Erro ao carregar status:', error);
        await _alertaCritico('Erro', traduzirErroTecnico(error.message));
    }
}

function populateStatusSelect(selectId, statusList) {
    const selectElement = document.getElementById(selectId);
    if (!selectElement) {
        console.warn(`Elemento ${selectId} não encontrado`);
        return;
    }
    
    // Limpar dropdown
    selectElement.innerHTML = '';
    
    // Adicionar opção vazia
    const emptyOption = document.createElement('option');
    emptyOption.value = '';
    emptyOption.textContent = 'Selecione...';
    selectElement.appendChild(emptyOption);
    
    // Adicionar opções de status
    statusList.forEach(status => {
        const option = document.createElement('option');
        option.value = status.status;
        option.textContent = status.status;
        option.setAttribute('data-ordem', status.ordem || '999');
        option.setAttribute('data-cor', status.cor || '');
        selectElement.appendChild(option);
    });
    
    console.log(`${selectId}: ${statusList.length} status adicionados`);
}

// ==================== CARREGAR MOTIVOS NO MODAL DE EDIÇÃO ====================

async function carregarMotivosNoModal() {
    try {
        // Se já carregou antes, reusar
        if (motivosDisponiveis.length > 0) {
            preencherSelectMotivosModal(motivosDisponiveis);
            
            // CORREÇÃO: Re-selecionar motivo após popular
            if (avariaAtual && avariaAtual.cod_motivo_devolucao) {
                setFieldValue('editCodMotivoDevolucao', avariaAtual.cod_motivo_devolucao);
                console.log(`Motivo selecionado: ${avariaAtual.cod_motivo_devolucao}`);
                checkMotivoChanged();
            }
            return;
        }
        
        // Buscar motivos da API
        const response = await fetch('/wms/sankhya_avarias/api/motivos-devolucao');
        const data = await response.json();
        
        if (data.success) {
            motivosDisponiveis = data.motivos;
            preencherSelectMotivosModal(data.motivos);
            console.log(`${data.motivos.length} motivos carregados no modal`);
            
            // CORREÇÃO: Re-selecionar motivo após popular
            if (avariaAtual && avariaAtual.cod_motivo_devolucao) {
                setFieldValue('editCodMotivoDevolucao', avariaAtual.cod_motivo_devolucao);
                console.log(`Motivo selecionado: ${avariaAtual.cod_motivo_devolucao}`);
                checkMotivoChanged();
            }
        } else {
            console.error('Erro ao carregar motivos:', data.error);
        }
    } catch (error) {
        console.error('Erro ao carregar motivos:', error);
    }
}

function preencherSelectMotivosModal(motivos) {
    const select = document.getElementById('editCodMotivoDevolucao');
    if (!select) return;
    
    select.innerHTML = '<option value="">Selecione o motivo...</option>';
    
    motivos.forEach(motivo => {
        const option = document.createElement('option');
        option.value = motivo.value;
        option.textContent = motivo.label;
        option.setAttribute('data-descricao', motivo.label.split(' - ')[1] || motivo.label);
        select.appendChild(option);
    });
}

// ==================== VERIFICAR MUDANÇA DE MOTIVO ====================

function checkMotivoChanged() {
    const motivoAtual = getFieldValue('editCodMotivoDevolucao');
    const motivoInicial = getFieldValue('editCodMotivoInicial');
    const alertDiv = document.getElementById('alertMotivoAlterado');
    
    if (!alertDiv) return;
    
    // Mostrar alerta se motivo mudou
    if (motivoInicial && motivoAtual && motivoInicial !== motivoAtual) {
        alertDiv.style.display = 'block';
    } else {
        alertDiv.style.display = 'none';
    }
}

// ==================== ABRIR MODAL DE EDIÇÃO ====================

async function abrirModalEditarAvaria(avariaId) {
    console.log('Abrindo modal de edição para avaria:', avariaId);
    
    try {
        // Buscar dados da avaria
        const response = await fetch(`/wms/api/avaria/${avariaId}`);
        const data = await response.json();
        
        if (!data.success) {
            await _alertaCritico('Erro', traduzirErroTecnico(data.error));
            return;
        }
        
        const avaria = data.avaria;
        const itens = data.itens || [];
        
        // CRÍTICO: Armazenar globalmente ANTES de abrir modal
        avariaAtual = avaria;
        
        console.log('Dados da avaria carregados:', avaria);
        console.log('Status Estoque:', avaria.status_estoque);
        console.log('Status Indenização:', avaria.status_indenizacao);
        console.log('Itens:', itens.length);
        
        // Preencher campos do modal (exceto dropdowns)
        preencherModalEdicao(avaria, itens);
        
        // Abrir modal (isso vai disparar loadStatusOptions via evento shown.bs.modal)
        const modalElement = document.getElementById('modalEditarAvaria');
        const modal = new bootstrap.Modal(modalElement);
        modal.show();
        
    } catch (error) {
        console.error('Erro ao abrir modal:', error);
        await _alertaCritico('Erro', traduzirErroTecnico(error.message));
    }
}

// ==================== PREENCHER MODAL COM DADOS ====================

function preencherModalEdicao(avaria, itens) {
    console.log('Preenchendo modal com dados...');
    
    // Campos de identificação (não editáveis)
    setTextContent('editDisplayId', avaria.id);
    setTextContent('editDisplayRegistradoPor', avaria.registrado_por);
    setTextContent('editDisplayDataRegistro', formatDate(avaria.data_registro));
    
    // Campo oculto do ID
    setFieldValue('editAvariaId', avaria.id);
    
    // SEÇÃO 1: Identificação Básica
    setFieldValue('editCodEmpresa', avaria.cod_empresa);
    setFieldValue('editNota', avaria.nota);
    setFieldValue('editNfd', avaria.nfd);
    setFieldValue('editQuantidade', avaria.quantidade);
    setFieldValue('editVolume', avaria.volume);
    
    // SEÇÃO 2: Logística
    setFieldValue('editTransportadora', avaria.transportadora);
    setFieldValue('editCte', avaria.cte);
    setFieldValue('editProtocoloInsucesso', avaria.protocolo_insucesso || '');
    setFieldValue('editDataColeta', avaria.data_coleta);
    setFieldValue('editCodRastreioCorreios', avaria.cod_rastreio_correios);
    
    // Helper: formata valor (qualquer formato) para exibição BR (1.234,56)
    function formatarValorParaInput(val) {
        if (!val && val !== 0) return '';
        const s = String(val).replace(/[^\d,.-]/g, '');
        let n;
        if (s.includes(',') && s.includes('.') && s.indexOf('.') < s.lastIndexOf(',')) {
            n = parseFloat(s.replace(/\./g, '').replace(',', '.'));  // BR: 1.234,56
        } else if (s.includes(',')) {
            n = parseFloat(s.replace(',', '.'));  // BR simples: 532,31
        } else {
            n = parseFloat(s);
        }
        if (isNaN(n)) return val;
        return n.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

// Nos campos de valor (sempre em formato BR para exibição):
    setFieldValue('editValorNf',  formatarValorParaInput(avaria.valor_nf));
    setFieldValue('editValorNfd', formatarValorParaInput(avaria.valor_nfd));
    setFieldValue('editNfp',      avaria.nfp);
    setFieldValue('editValorNfp', formatarValorParaInput(avaria.valor_nfp));
    
    // SEÇÃO 4: Indenização e Reembolso
    setFieldValue('editIndenizacao', avaria.indenizacao);
    setFieldValue('editPrazoPagamento', avaria.prazo_pagamento);
    setFieldValue('editDataPagamento', avaria.data_pagamento);
    setFieldValue('editDataEntrega', avaria.data_entrega);
    setFieldValue('editReembolsoPct', avaria.reembolso_pct || '0');
    
    // Calcular valor de indenização inicial
    calculateIndenizacaoValue();
    
    // NOTA: Status e Motivo serão preenchidos DEPOIS via loadStatusOptions()
    // Isso garante que os dropdowns já estejam populados antes de selecionar
    
    // SEÇÃO 6: Observação
    setFieldValue('editObservacao', avaria.observacao);
    
    // SEÇÃO 7: Campos ocultos de motivo inicial
    setFieldValue('editCodMotivoInicial', avaria.cod_motivo_inicial || avaria.cod_motivo_devolucao || '');
    setFieldValue('editMotivoInicial', avaria.motivo_inicial || avaria.motivo_devolucao || '');
    
    // Exibir motivo inicial se existir
    const motivoInicial = avaria.motivo_inicial || avaria.motivo_devolucao || 'Não informado';
    const codMotivoInicial = avaria.cod_motivo_inicial || avaria.cod_motivo_devolucao || '-';
    
    setTextContent('editCodMotivoInicialDisplay', codMotivoInicial);
    setTextContent('editMotivoInicialTextoDisplay', motivoInicial);
    
    // SEÇÃO 8: Itens da Avaria
    preencherItensAvaria(itens);
    
    // Adicionar campo Data Protocolo MKT
    setFieldValue('editDataProtocoloMkt', avaria.data_protocolo_mkt);
}

function preencherItensAvaria(itens) {
    const itensBody = document.getElementById('itensAvariaTableBody');
    if (!itensBody) return;

    if (!itens || itens.length === 0) {
        // Subindo colspan para 8 para contemplar a nova coluna de valor
        itensBody.innerHTML = '<tr><td colspan="8" class="text-center text-muted">Nenhum item cadastrado</td></tr>';
        return;
    }

    itensBody.innerHTML = '';
    itens.forEach(item => {
        const statusEtiqueta = item.status_etiqueta || 'pendente';
        const idEtiqueta = item.id_etiqueta;
        const isAtiva = statusEtiqueta === 'ativa';

        let acoesHtml;
        if (isAtiva) {
            acoesHtml = '<span class="badge bg-success">Etiqueta Ativa</span>';
        } else {
            acoesHtml = `
                <button type="button" class="btn btn-sm btn-warning" 
                    onclick="editarItemAvaria('${item.avaria_id}', ${item.sequencia}, '${item.codprod}', ${item.qtd_avariada})">
                    <i class="fas fa-edit"></i>
                </button>`;
        }

        // ── PONTO 2: Adicionar célula de Valor Item ──
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${item.sequencia}</td>
            <td>${item.codprod}</td>
            <td class="text-truncate" style="max-width:200px" title="${item.descrprod}">${item.descrprod}</td>
            <td class="text-center"><strong>${item.qtd_avariada}</strong></td>
            <td><span class="badge bg-secondary">${item.localizacao || 'N/A'}</span></td>
            <td class="text-end fw-semibold text-success">${formatCurrency(item.valor_item)}</td>
            <td class="text-center">
                <span class="badge bg-${statusEtiqueta === 'ativa' ? 'success' : statusEtiqueta === 'baixada' ? 'danger' : 'warning'}">
                    ${statusEtiqueta}
                </span>
                ${idEtiqueta ? `<br><small class="text-muted">${idEtiqueta}</small>` : ''}
            </td>
            <td class="text-center">${acoesHtml}</td>
        `;
        itensBody.appendChild(tr);
    });
}

// ==================== EDITAR ITEM DE AVARIA ====================

function editarItemAvaria(avariaId, sequencia, produto, qtdAtual, qtdOriginal, localizacao) {
    console.log('Editando item:', {avariaId, sequencia, produto, qtdAtual, localizacao});
    
    setFieldValue('editItemAvariaId', avariaId);
    setFieldValue('editItemSequencia', sequencia);
    setFieldValue('editItemProduto', produto);
    setFieldValue('editItemQtd', qtdAtual);
    setTextContent('editItemOriginalQtd', qtdOriginal);
    setFieldValue('editItemLocalizacao', localizacao);
    
    const modal = new bootstrap.Modal(document.getElementById('modalEditarItem'));
    modal.show();
}

async function confirmarEdicaoItem() {
    const avariaId = document.getElementById('editItemAvariaId')?.value;
    const sequencia = document.getElementById('editItemSequencia')?.value;
    const qtdAvariada = document.getElementById('editItemQtd')?.value;
    const localizacao = document.getElementById('editItemLocalizacao')?.value;
    
    if (!avariaId || !sequencia) {
        await _alertaCritico('Dados inválidos', 'Não foi possível identificar o item a atualizar.');
        return;
    }
    
    console.log('Salvando item:', {avariaId, sequencia, qtdAvariada, localizacao});
    
    try {
        const response = await fetch('/wms/sankhya_avarias/api/avaria/item/update', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                avaria_id: avariaId,
                sequencia: parseInt(sequencia),
                qtd_avariada: parseDecimalValue(qtdAvariada),
                localizacao: localizacao
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            console.log('Item atualizado com sucesso');
            await _alertaCritico('Sucesso', 'Item atualizado com sucesso!');

            // Fechar modal de edição de item
            const modal = bootstrap.Modal.getInstance(document.getElementById('modalEditarItem'));
            if (modal) modal.hide();

            // Recarregar modal de edição da avaria
            abrirModalEditarAvaria(avariaId);
        } else {
            await _alertaCritico('Erro', traduzirErroTecnico(data.error));
        }
    } catch (error) {
        console.error('Erro na requisição:', error);
        await _alertaCritico('Erro', traduzirErroTecnico(error.message));
    }
}

// ==================== CÁLCULO DE INDENIZAÇÃO - PRIORIDADE NFD ====================

function calculateIndenizacaoValue() {
    const pctEl           = document.getElementById('editReembolsoPct');
    const displayEl       = document.getElementById('editReembolsoValor');
    const indenizacaoTipo = document.getElementById('editIndenizacao');

    if (!pctEl || !displayEl || !avariaAtual) return;

    try {
        const indenizacaoPct  = parseDecimalValue(pctEl.value);
        const tipoIndenizacao = indenizacaoTipo ? indenizacaoTipo.value : '';
        const tipoNorm        = tipoIndenizacao ? tipoIndenizacao.trim().toLowerCase() : '';

        // Não Indenizável → zero imediato
        if (tipoNorm === 'nao_indenizavel' || tipoNorm === 'não indenizável' || tipoNorm === 'nao indenizavel') {
            displayEl.value = 'R$ 0,00';
            displayEl.style.color = '#dc3545';
            return;
        }

        // ── Base = soma dos itens da avaria (valor_item × qtd_avariada) ──────
        let valorBase = 0;
        if (typeof itensAvariaEdicao !== 'undefined' && itensAvariaEdicao.length > 0) {
            itensAvariaEdicao.forEach(item => {
                const vi = parseFloat(item.valor_item || 0);
                const qa = parseFloat(item.qtd_avariada || 1);
                if (!isNaN(vi) && !isNaN(qa) && vi > 0 && qa > 0) {
                    valorBase += vi * qa;
                }
            });
            console.log('Base itens (valor_item × qtd_avariada):', valorBase.toFixed(2),
                        `(${itensAvariaEdicao.length} itens)`);
        }

        // ── Fallback → NFD/NF quando itens não têm valor ────────────────────
        if (valorBase <= 0) {
            const valorNf  = parseDecimalValue(avariaAtual.valor_nf);
            const valorNfd = parseDecimalValue(avariaAtual.valor_nfd);
            valorBase = valorNfd > 0 ? valorNfd : valorNf;
            console.log('   Itens sem valor — fallback NFD/NF:', valorBase);
        }

        if (valorBase <= 0) {
            displayEl.value = 'R$ 0,00';
            displayEl.style.color = '#6c757d';
            return;
        }

        // ── Aplicar tipo / percentual ────────────────────────────────────────
        let indenizacaoValor = 0;

        if (tipoNorm === 'total' || tipoNorm === 'indenização total' || tipoNorm === 'indenizacao total') {
            indenizacaoValor       = valorBase;
            displayEl.style.color = '#198754';
            console.log('   Total: R$', indenizacaoValor.toFixed(2));
        } else if (tipoNorm === 'parcial' || tipoNorm === 'indenização parcial' || tipoNorm === 'indenizacao parcial') {
            indenizacaoValor       = (valorBase * indenizacaoPct) / 100;
            displayEl.style.color = '#fd7e14';
            console.log(`   Parcial: ${indenizacaoPct}% de R$${valorBase.toFixed(2)} = R$${indenizacaoValor.toFixed(2)}`);
        } else {
            indenizacaoValor       = (valorBase * indenizacaoPct) / 100;
            displayEl.style.color = '#198754';
            console.log(`   Percentual: ${indenizacaoPct}%`);
        }

        displayEl.value = `R$ ${indenizacaoValor.toFixed(2)
            .replace('.', ',')
            .replace(/\B(?=(\d{3})+(?!\d))/g, '.')}`;
        console.log('   Exibindo:', displayEl.value);

    } catch (e) {
        console.error('Erro de cálculo:', e);
        displayEl.value = 'Erro';
        displayEl.style.color = '#dc3545';
    }
}

// Manter compatibilidade com código antigo que pode chamar esta função
function calculateReimbursementValue() {
    calculateIndenizacaoValue();
}

// PONTO 3: Normalizar valores monetários (aceita BR ou US)
function parseMonetarioBR(val) {
    if (!val && val !== 0) return '';
    let s = String(val).replace('R$', '').replace(/\s/g, '').trim();
    const temVirgula = s.includes(',');
    const temPonto   = s.includes('.');
    if (temVirgula && temPonto) {
        s = s.replace(/\./g, '').replace(',', '.'); // 1.234,56 → 1234.56
    } else if (temVirgula) {
        s = s.replace(',', '.');                    // 1234,56 → 1234.56
    }
    const n = parseFloat(s);
    return isNaN(n) ? '' : String(n.toFixed(2));    // salva como "1234.56"
}

// ==================== SALVAR EDIÇÃO ====================

async function salvarEdicaoAvaria() {
    const avariaId = document.getElementById('editAvariaId')?.value;
    
    console.log('Salvando edição da avaria:', avariaId);
    
    if (!avariaId) {
        await _alertaCritico('Erro', 'ID da avaria não encontrado.');
        return;
    }
    
    // Validar campos obrigatórios
    const camposObrigatorios = [
        'editCodEmpresa', 'editNota', 'editQuantidade', 
        'editTransportadora', 'editStatusEstoque', 
        'editStatusIndenizacao' 
    ];
    
    let campoInvalido = null;
    for (const campoId of camposObrigatorios) {
        const campo = document.getElementById(campoId);
        if (!campo) continue;
        
        if (!campo.value || String(campo.value).trim() === '') {
            campo.classList.add('is-invalid');
            if (!campoInvalido) campoInvalido = campo;
        } else {
            campo.classList.remove('is-invalid');
        }
    }
    
    if (campoInvalido) {
        await _alertaCritico('Campos obrigatórios', 'Por favor, preencha todos os campos obrigatórios!');
        campoInvalido.focus();
        return;
    }
    
    // Obter motivo selecionado
    const selectMotivo = document.getElementById('editCodMotivoDevolucao');
    let cod_motivo_atual = '';
    let descricao_motivo_atual = '';
    
    if (selectMotivo && selectMotivo.value) {
        cod_motivo_atual = selectMotivo.value;
        const opcaoSelecionada = selectMotivo.options[selectMotivo.selectedIndex];
        descricao_motivo_atual = opcaoSelecionada.getAttribute('data-descricao') || '';
    }
    
    const cod_motivo_inicial = getFieldValue('editCodMotivoInicial');
    const descricao_motivo_inicial = getFieldValue('editMotivoInicial');
    
    // CRÍTICO: Ler valores atuais para comparação
    const notaAtual = avariaAtual ? avariaAtual.nota : '';
    const nfdAtual = avariaAtual ? avariaAtual.nfd : '';
    const nfpAtual = avariaAtual ? avariaAtual.nfp : '';
    
    // CRÍTICO: Preparar dados APENAS com campos que realmente mudaram
    const dadosEdicao = {};
    
    // Função auxiliar para adicionar campo apenas se mudou
    function addIfChanged(fieldId, fieldName, currentValue = '') {
        const newValue = getFieldValue(fieldId);
        if (newValue !== currentValue) {
            dadosEdicao[fieldName] = newValue;
            console.log(`   ${fieldName}: '${currentValue}' → '${newValue}'`);
        }
    }
    
    // Identificação - CRÍTICO: Comparar com valores atuais
    addIfChanged('editCodEmpresa', 'cod_empresa', avariaAtual?.cod_empresa);
    addIfChanged('editNota', 'nota', notaAtual);
    addIfChanged('editNfd', 'nfd', nfdAtual);
    
    // Quantidade e Volume
    const qtdAtual = avariaAtual ? parseDecimalValue(avariaAtual.quantidade) : 0;
    const qtdNova = parseDecimalValue(getFieldValue('editQuantidade'));
    if (qtdNova !== qtdAtual) {
        dadosEdicao.quantidade = qtdNova;
    }
    
    const volAtual = avariaAtual ? parseDecimalValue(avariaAtual.volume) : 0;
    const volNovo = parseDecimalValue(getFieldValue('editVolume'));
    if (volNovo !== volAtual) {
        dadosEdicao.volume = volNovo;
    }
    
    // Logística
    addIfChanged('editTransportadora', 'transportadora', avariaAtual?.transportadora);
    addIfChanged('editCte', 'cte', avariaAtual?.cte);
    addIfChanged('editProtocoloInsucesso', 'protocolo_insucesso', avariaAtual?.protocolo_insucesso); 
    addIfChanged('editDataColeta', 'data_coleta', avariaAtual?.data_coleta);
    addIfChanged('editCodRastreioCorreios', 'cod_rastreio_correios', avariaAtual?.cod_rastreio_correios);
    
    // Financeiro — comparação float-to-float, envio como string US (parseMonetarioBR)
    const valorNfAtual = avariaAtual ? parseDecimalValue(avariaAtual.valor_nf) : 0;
    const valorNfNovo = parseDecimalValue(getFieldValue('editValorNf'));
    if (Math.abs(valorNfNovo - valorNfAtual) > 0.001) {
        dadosEdicao.valor_nf = parseMonetarioBR(getFieldValue('editValorNf'));
    }
    
    const valorNfdAtual = avariaAtual ? parseDecimalValue(avariaAtual.valor_nfd) : 0;
    const valorNfdNovo = parseDecimalValue(getFieldValue('editValorNfd'));
    if (Math.abs(valorNfdNovo - valorNfdAtual) > 0.001) {
        dadosEdicao.valor_nfd = parseMonetarioBR(getFieldValue('editValorNfd'));
    }
    
    addIfChanged('editNfp', 'nfp', nfpAtual);
    
    const valorNfpAtual = avariaAtual ? parseDecimalValue(avariaAtual.valor_nfp) : 0;
    const valorNfpNovo = parseDecimalValue(getFieldValue('editValorNfp'));
    if (Math.abs(valorNfpNovo - valorNfpAtual) > 0.001) {
        dadosEdicao.valor_nfp = parseMonetarioBR(getFieldValue('editValorNfp'));
    }
    
    addIfChanged('editPrazoPagamento', 'prazo_pagamento', avariaAtual?.prazo_pagamento);
    addIfChanged('editDataPagamento', 'data_pagamento', avariaAtual?.data_pagamento);
    
    // Indenização
    addIfChanged('editIndenizacao', 'indenizacao', avariaAtual?.indenizacao);
    
    const pctAtual = avariaAtual ? parseDecimalValue(avariaAtual.reembolso_pct) : 0;
    const pctNovo = parseDecimalValue(getFieldValue('editReembolsoPct'));
    if (pctNovo !== pctAtual) {
        dadosEdicao.reembolso_pct = pctNovo;
    }
    
    // Status
    addIfChanged('editStatusEstoque', 'status_estoque', avariaAtual?.status_estoque);
    addIfChanged('editStatusIndenizacao', 'status_indenizacao', avariaAtual?.status_indenizacao);
    
    // Motivo
    if (cod_motivo_atual !== (avariaAtual?.cod_motivo_devolucao || '')) {
        dadosEdicao.cod_motivo_devolucao = cod_motivo_atual;
        dadosEdicao.motivo_devolucao = descricao_motivo_atual;
    }
    
    dadosEdicao.cod_motivo_inicial = cod_motivo_inicial;
    dadosEdicao.motivo_inicial = descricao_motivo_inicial;
    
    // Outros
    addIfChanged('editDataEntrega', 'data_entrega', avariaAtual?.data_entrega);
    addIfChanged('editObservacao', 'observacao', avariaAtual?.observacao);
    addIfChanged('editDataProtocoloMkt', 'data_protocolo_mkt', avariaAtual?.data_protocolo_mkt);
    
  // Adicionar arrays de itens
    const dadosItens = prepararDadosParaSalvarEdicao(); // Função do split-item-edicao.js
    dadosEdicao.itens_novos = dadosItens.itens_novos || [];
    dadosEdicao.itens_modificados = dadosItens.itens_modificados || [];
    
    console.log('Dados a enviar:', dadosEdicao);
    console.log('   - Campos do cabeçalho:', Object.keys(dadosEdicao).filter(k => !['itens_novos', 'itens_modificados'].includes(k)).length);
    console.log('   - Itens novos:', dadosEdicao.itens_novos.length);
    console.log('   - Itens modificados:', dadosEdicao.itens_modificados.length);
    
    // Desabilitar botão de salvar durante o envio
    const btnSalvar = document.querySelector('#modalEditarAvaria .btn-primary');
    if (!btnSalvar) {
        console.error('Botão de salvar não encontrado');
        return;
    }
    
    const originalText = btnSalvar.innerHTML;
    btnSalvar.disabled = true;
    btnSalvar.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i>Salvando...';
    
    try {
        const response = await fetch(`/wms/api/avaria/${avariaId}/editar`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(dadosEdicao)
        });
        
        const data = await response.json();
        
        if (data.success) {
            console.log('Avaria atualizada com sucesso:', data.detalhes);
            await _alertaCritico('Sucesso',
                  `Avaria atualizada com sucesso!<br><br>` +
                  `Cabeçalho: ${data.detalhes.cabecalho_atualizado ? 'Atualizado' : 'Sem alterações'}<br>` +
                  `Itens modificados: ${data.detalhes.itens_modificados}<br>` +
                  `Itens novos: ${data.detalhes.itens_novos}`);

            // Salvar cache ANTES do reload — beforeunload não é confiável em async/await
            if (typeof CacheEstado !== 'undefined') {
                const _tela = document.getElementById('tabelaAvariasPendentes') ? 'registrar' : 'consultar';
                CacheEstado.salvarEstadoAtual(_tela);
            }
            // Salvar também o sistema legado de consultar (wms_filtros_consulta)
            if (typeof salvarFiltrosCache === 'function') salvarFiltrosCache();

            // Fechar modal e recarregar página
            const modal = bootstrap.Modal.getInstance(document.getElementById('modalEditarAvaria'));
            if (modal) modal.hide();
            location.reload();
        } else {
            console.error('Erro ao salvar:', data.error);
            await _alertaCritico('Erro ao salvar', traduzirErroTecnico(data.error));
        }

    } catch (error) {
        console.error('Erro na requisição:', error);
        await _alertaCritico('Erro ao salvar', traduzirErroTecnico(error.message));
    } finally {
        btnSalvar.disabled = false;
        btnSalvar.innerHTML = originalText;
    }
}

// ==================== INICIALIZAÇÃO ====================

document.addEventListener('DOMContentLoaded', function() {
    const modal = document.getElementById('modalEditarAvaria');
    if (modal) {
        // CRÍTICO: Carregar dropdowns quando modal abrir
        modal.addEventListener('shown.bs.modal', function() {
            loadStatusOptions();
        });
    }
    
    // Listener para o campo de percentual de indenização
    const reembolsoPctEl = document.getElementById('editReembolsoPct');
    if (reembolsoPctEl) {
        reembolsoPctEl.addEventListener('input', calculateIndenizacaoValue);
    }
    
    // Listener para o tipo de indenização
    const indenizacaoEl = document.getElementById('editIndenizacao');
    if (indenizacaoEl) {
        indenizacaoEl.addEventListener('change', calculateIndenizacaoValue);
    }
});

console.log('edit-avaria.js v6.1 CARREGADO - Versão Final Corrigida com API compatível');
