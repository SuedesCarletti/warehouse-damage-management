/**
 * print_direct.js v3.0 - Formatação de Dados
 * JavaScript para impressão com dados PRÉ-FORMATADOS
 * Envia texto completo ao invés de valores separados
 */

console.log('print_direct.js v3.0 carregado!');

// Configuração do servidor local
const PRINT_SERVER = {
    url: 'http://localhost:8765',
    timeout: 30000 // 30 segundos
};

let currentPrintContext = {
    tipo: null,
    avariaId: null,
    campos: null,
    volume: null
};

/**
 * Garante um valor não-vazio para um campo da etiqueta (codprod, descrprod, ...).
 * Se o item preferido (ativo/triado) veio com o campo em branco na planilha,
 * busca o primeiro item da avaria que tenha esse campo preenchido antes
 * de desistir e deixar a etiqueta sair sem a informação.
 * Isso corrige etiquetas saindo com cód/descrição em branco mesmo quando a nota
 * tem o produto identificado em outro item/linha.
 */
function resolverCampoComFallback(itens, valorPreferido, campo) {
    if (valorPreferido) return valorPreferido;

    if (!Array.isArray(itens)) return valorPreferido;

    const itemComCampo = itens.find(i => i && i[campo]);
    if (itemComCampo) {
        console.warn(`${campo} do item selecionado veio vazio. Usando ${campo} de outro item da mesma avaria como fallback:`, itemComCampo[campo]);
        return itemComCampo[campo];
    }

    console.warn(`Nenhum item da avaria possui ${campo} preenchido na planilha. Etiqueta sairá sem essa informação.`);
    return valorPreferido;
}

/**
 * Alias mantido por compatibilidade — fallback específico de codprod.
 */
function resolverCodprodComFallback(itens, codprodPreferido) {
    return resolverCampoComFallback(itens, codprodPreferido, 'codprod');
}

/**
 * Verifica se o servidor de impressão local está online
 */
async function checkPrintServer() {
    try {
        const response = await fetch(`${PRINT_SERVER.url}/status`, {
            method: 'GET',
            mode: 'cors',
            signal: AbortSignal.timeout(5000)
        });
        
        if (response.ok) {
            const data = await response.json();
            console.log('Servidor conectado:', data);
            return { online: true, data };
        }
        
        return { online: false, error: `Status HTTP ${response.status}` };
    } catch (error) {
        console.warn('Servidor offline:', error.message);
        return { online: false, error: error.message };
    }
}

/**
 * Mostra notificação para o usuário
 */
function showNotification(type, message) {
    console.log(`[${type.toUpperCase()}] ${message}`);
    
    // Tentar usar Bootstrap Toast
    if (typeof bootstrap !== 'undefined' && typeof bootstrap.Toast !== 'undefined') {
        const toastContainer = document.getElementById('toastContainer');
        if (!toastContainer) {
            const container = document.createElement('div');
            container.id = 'toastContainer';
            container.className = 'toast-container position-fixed top-0 end-0 p-3';
            container.style.zIndex = '9999';
            document.body.appendChild(container);
        }
        
        const toast = document.createElement('div');
        toast.className = `toast align-items-center text-white bg-${type === 'success' ? 'success' : type === 'error' ? 'danger' : 'warning'} border-0`;
        toast.setAttribute('role', 'alert');
        toast.innerHTML = `
            <div class="d-flex">
                <div class="toast-body">${message}</div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        `;
        
        document.getElementById('toastContainer').appendChild(toast);
        const bsToast = new bootstrap.Toast(toast, { delay: 5000 });
        bsToast.show();
        
        toast.addEventListener('hidden.bs.toast', () => toast.remove());
    } else {
        alert(message);
    }
}

/**
 * Formata dados da avaria para impressão
 * NOVO: Retorna texto completo formatado para cada linha
 *
 * REVISÃO v3.1: Ajustado layout das etiquetas Inicial e Final conforme especificação.
 */
function formatarDadosEtiqueta(avaria, tipo = 'inicial') {
    console.log('Formatando dados para etiqueta:', tipo);
    
    if (tipo === 'inicial') {
        // ETIQUETA INICIAL - Sequência: Nota, NFD, Volume, CTE, Transp.
        return {
            // Linha 1: Nota Fiscal
            txtLinha1: `Nota: ${avaria.nota || 'N/A'}`, 
            
            // Linha 2: NFD (Adicionado)
            txtLinha2: `NFD: ${avaria.nfd || 'N/A'}`, 
            
            // Linha 3: Volume
            txtLinha3: `Volume: ${avaria.volume || 'N/A'}`, 
            
            // Linha 4: CTE
            txtLinha4: `CTE: ${avaria.cte || 'N/A'}`, 
            
            // Linha 5: Transportadora (Abreviada, conforme solicitado)
            txtLinha5: `${avaria.transportadora || 'N/A'}`, 
            
            // Linha 6: Vazia (ID explícito removido)
            txtLinha6: '',
            
            // Linha 7
            txtLinha7: '', 
            
            // Linha 8
            txtLinha8: '', 
            
            // Código de barras (apenas ID)
            barcodeID: String(avaria.id_etiqueta || avaria.id || '')
        };
    } else if (tipo === 'final') {
        // ETIQUETA FINAL - Sequência solicitada: Nota, NFD, Cód. Produto, Volume, Local, % Reembolso
        return {
            // Linha 1: Nota Fiscal
            txtLinha1: `Nota: ${avaria.nota || 'N/A'}`, 
            
            // Linha 2: NFD
            txtLinha2: `NFD: ${avaria.nfd || 'N/A'}`, 
            
            // Linha 3: Código do Produto
            txtLinha3: `Cód.: ${avaria.codprod || 'N/A'}`,

            // Linha 4: Volume/Quantidade
            txtLinha4: `Qtd/Vol: ${avaria.volume || 'N/A'}`,

            // Linha 5: Localização
            txtLinha5: `Local: ${avaria.localizacao || 'N/A'}`,

            // Linha 6: Reembolso
            txtLinha6: `Reembolso: ${avaria.reembolso_pct || 0}%`,

            // Linha 7: Nome/descrição do produto (identificação nominal)
            txtLinha7: avaria.descrprod ? `Produto: ${avaria.descrprod}` : '',
            
            // Linha 8 (Clearing previous content: Empty)
            txtLinha8: '', 
            
            // Código de barras
            barcodeID: String(avaria.id_etiqueta || avaria.id || '')
        };
    }
}

/**
 * Imprime etiqueta inicial (busca dados da avaria primeiro)
 */
async function printEtiquetaInicial(avariaId) {
    console.log('printEtiquetaInicial chamada!', avariaId);
    
    const btn = event?.target?.closest('button');
    const originalText = btn?.innerHTML;
    
    // Mostrar loading no botão
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin me-1"></i>Imprimindo...';
    }
    
    try {
        // PASSO 1: Buscar dados da avaria
        console.log('Buscando dados da avaria...');
        const avariaResponse = await fetch(`/wms/api/avaria/${avariaId}`);
        
        if (!avariaResponse.ok) {
            throw new Error(`Erro ao buscar avaria: HTTP ${avariaResponse.status}`);
        }
        
        const data = await avariaResponse.json();
        console.log('Dados recebidos:', data);
        
        if (!data.success || !data.avaria || !data.avaria.id) { // Verificação da estrutura JSON da API
            throw new Error('Avaria não encontrada ou dados inválidos');
        }
        
        // PASSO 2: Armazenar dados e abrir modal
        console.log('Abrindo modal de seleção de quantidade...');
        
        // ========== MUDANÇA: Chamar modal em vez de imprimir direto ==========/ 
        const printAvaria = { ...data.avaria };

        // FIX: Usar id_etiqueta SOMENTE do item com status 'ativa'.
        // Fallback inseguro removido — pegar qualquer item com id_etiqueta
        // causava uso de etiqueta já substituída/baixada.
        if (data.itens && data.itens.length > 0) {
            const itemAtivo = data.itens.find(i => i.id_etiqueta && i.status_etiqueta === 'ativa');
            if (itemAtivo) {
                // Se a etiqueta ativa não for uma inicial (00), redireciona para a impressão final.
                // Isso garante que se a triagem já ocorreu, sempre imprimimos a final, mesmo que 
                // o usuário tenha tentado reimprimir a inicial.
                if (!itemAtivo.id_etiqueta.startsWith('00')) {
                    console.log('Etiqueta ativa não é inicial (00). Redirecionando para impressão final...');
                    return printEtiquetaFinal(avariaId);
                }
                printAvaria.id_etiqueta = itemAtivo.id_etiqueta;
                printAvaria.codprod = resolverCodprodComFallback(data.itens, itemAtivo.codprod); // <-- Garante codprod na inicial também
            } else {
                console.warn('Nenhum item com status_etiqueta=ativa encontrado. Usando id da avaria como fallback.');
                // Fallback de segurança para não perder os dados do produto na reimpressão
                const firstItem = data.itens.find(i => i.id_etiqueta) || data.itens[0];
                if (firstItem) {
                    printAvaria.codprod = resolverCodprodComFallback(data.itens, firstItem.codprod);
                    console.log('Dados do produto recuperados pelo fallback.');
                }
            }
        }
        
        showPrintQuantityModal('inicial', avariaId, printAvaria);
        
        // FIX: Remover a lógica de impressão que estava aqui, pois o fluxo continua no confirmPrint()
        
    } catch (error) {
        console.error('ERRO COMPLETO:', error);
        
        // Mensagens de erro específicas
        let userMessage = 'Erro ao imprimir etiqueta';
        
        if (error.name === 'TimeoutError' || error.name === 'AbortError') {
            userMessage = '⏱️ Tempo esgotado. Verifique se a impressora está conectada e o servidor está rodando.';
        } else if (error.message.includes('Failed to fetch') || error.message.includes('NetworkError')) {
            userMessage = '🔌 Servidor de impressão offline. Execute o "Brother Print Server" no Windows.';
        } else if (error.message.includes('HTTP 404')) {
            userMessage = '❌ Template não encontrado no servidor de impressão.';
        } else if (error.message.includes('HTTP 500')) {
            userMessage = '❌ Erro no servidor de impressão. Verifique os logs.';
        } else {
            userMessage = `❌ ${error.message}`;
        }
        
        showNotification('error', userMessage);
        
    } finally {
        // Restaurar botão
        if (btn && originalText) {
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    }
}

/**
 * Imprime etiqueta final (busca dados da avaria primeiro)
 */
async function printEtiquetaFinal(avariaId) {
    console.log('printEtiquetaFinal chamada!', avariaId);
    
    const btn = event?.target?.closest('button');
    const originalText = btn?.innerHTML;
    
    // Mostrar loading no botão
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin me-1"></i>Finalizando...';
    }
    
    try {
        // PASSO 1: Buscar dados da avaria
        console.log('Buscando dados da avaria...');
        const avariaResponse = await fetch(`/wms/api/avaria/${avariaId}`);
        
        if (!avariaResponse.ok) {
            throw new Error(`Erro ao buscar avaria: HTTP ${avariaResponse.status}`);
        }
        
        const data = await avariaResponse.json();
        console.log('Dados recebidos:', data);
        
        if (!data.success || !data.avaria || !data.avaria.id) {
            throw new Error('Avaria não encontrada ou dados inválidos');
        }
        
        // PASSO 2: Formatar dados para impressão
        console.log('Abrindo modal de seleção de quantidade...');
        
        const printAvaria = { ...data.avaria };

        if (data.itens && data.itens.length > 0) {
            // FIX: Usar id_etiqueta SOMENTE do item com status 'ativa'.
            // Fallback inseguro removido — pegar qualquer item com id_etiqueta
            // causava uso de etiqueta já substituída/baixada na etiqueta final.
            let itemAtivo = data.itens.find(i => i.id_etiqueta && i.status_etiqueta === 'ativa');

            // FIX: itens recusados (recusar_item) ficam com status 'recusada' —
            // estado terminal que nunca volta a 'ativa'. Sem este fallback, o find()
            // acima não acha nada, o bloco abaixo é pulado e a etiqueta final sai
            // sem codprod/localizacao (Avaria não tem campo codprod). Usa o último
            // item já triado (prefixo != '00') como próximo melhor candidato.
            if (!itemAtivo) {
                const jaTriados = data.itens.filter(i => i.id_etiqueta && !i.id_etiqueta.startsWith('00'));
                itemAtivo = jaTriados[jaTriados.length - 1] || null;
                if (itemAtivo) {
                    console.warn(`Nenhum item 'ativa' — usando último item já triado (status=${itemAtivo.status_etiqueta}) como fallback.`);
                }
            }

            if (itemAtivo) {
                printAvaria.id_etiqueta = itemAtivo.id_etiqueta;
                printAvaria.codprod = resolverCodprodComFallback(data.itens, itemAtivo.codprod);
                // Nome/descrição do produto — sem isso a etiqueta final sai só com o código,
                // sem a identificação nominal do produto.
                printAvaria.descrprod = resolverCampoComFallback(data.itens, itemAtivo.descrprod, 'descrprod');
                printAvaria.volume = itemAtivo.qtd_avariada || '1';

                // Prioriza localizacao (descrição completa, ex: "8 - NOME DO LOCAL")
                // gerada pelo etiqueta_service após triagem/transferência.
                // Fallback: codlocaldest (código numérico) → local do cabeçalho da avaria.
                let localizacaoApresentacao = itemAtivo.localizacao || itemAtivo.codlocaldest || data.avaria.local || '—';
                printAvaria.localizacao = localizacaoApresentacao;
            } else {
                console.warn('Nenhum item com status_etiqueta=ativa encontrado para etiqueta final. Usando dados da avaria como fallback.');
                // Fallback de segurança para não perder o código do produto se a etiqueta não for mais 'ativa' (ex: baixada, reimpressão)
                const fallbackItem = data.itens.find(i => i.id_etiqueta) || data.itens[0];
                if (fallbackItem) {
                    printAvaria.codprod = resolverCodprodComFallback(data.itens, fallbackItem.codprod);
                    printAvaria.descrprod = resolverCampoComFallback(data.itens, fallbackItem.descrprod, 'descrprod');
                    printAvaria.volume = fallbackItem.qtd_avariada || '1';
                    let localizacaoApresentacao = fallbackItem.localizacao || fallbackItem.codlocaldest || data.avaria.local || '—';
                    printAvaria.localizacao = localizacaoApresentacao;
                    console.log('Dados do produto recuperados pelo fallback de segurança.');
                }
            }
        }
        
        // ========== MUDANÇA: Chamar modal em vez de imprimir direto ==========/ 
        showPrintQuantityModal('final', avariaId, printAvaria);

    } catch (error) {
        console.error('ERRO COMPLETO:', error);
        
        // Mensagens de erro específicas
        let userMessage = 'Erro ao finalizar e imprimir etiqueta';
        
        if (error.name === 'TimeoutError' || error.name === 'AbortError') {
            userMessage = '⏱️ Tempo esgotado. Verifique se a impressora está conectada e o servidor está rodando.';
        } else if (error.message.includes('Failed to fetch') || error.message.includes('NetworkError')) {
            userMessage = '🔌 Servidor de impressão offline. Execute o "Brother Print Server" no Windows.';
        } else if (error.message.includes('HTTP 404')) {
            userMessage = '❌ Template não encontrado no servidor de impressão.';
        } else if (error.message.includes('HTTP 500')) {
            userMessage = '❌ Erro no servidor de impressão. Verifique os logs.';
        } else {
            userMessage = `❌ ${error.message}`;
        }
        
        showNotification('error', userMessage);
        
    } finally {
        // Restaurar botão
        if (btn && originalText) {
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    }
}

// ADIÇÃO 4: FUNÇÕES DO MODAL DE QUANTIDADE

/**
 * Mostra modal de seleção de quantidade
 */
function showPrintQuantityModal(tipo, avariaId, avaria) {
    console.log('Preparando modal para', tipo);
    
    // Armazenar contexto
    currentPrintContext = {
        tipo: tipo,
        avariaId: avariaId,
        avaria: avaria,
        volume: parseInt(avaria.volume) || 1
    };
    
    // Atualizar interface do modal
    document.getElementById('printModalTitle').textContent = 
        `Imprimir Etiqueta ${tipo === 'inicial' ? 'Inicial' : 'Final'}`;
    document.getElementById('volumeValue').textContent = currentPrintContext.volume;
    document.getElementById('manualQuantity').value = currentPrintContext.volume;
    document.getElementById('autoQuantity').checked = true;
    
    // Habilitar/desabilitar input manual
    toggleManualInput();
    
    // Preview dinâmico da etiqueta
    let previewEl = document.getElementById('labelPreviewArea');
    if (!previewEl) {
        const modalBody = document.querySelector('#printQuantityModal .modal-body');
        if (modalBody) {
            const previewContainer = document.createElement('div');
            previewContainer.innerHTML = `
                <hr>
                <h6><i class="fas fa-eye"></i> Pré-visualização da Etiqueta:</h6>
                <div id="labelPreviewArea" class="p-2 border rounded text-start shadow-sm" style="font-family: 'Courier New', Courier, monospace; font-size: 12px; white-space: pre-wrap; line-height: 1.4; opacity: 0.85;"></div>
            `;
            modalBody.appendChild(previewContainer);
            previewEl = document.getElementById('labelPreviewArea');
        }
    }
    
    if (previewEl) {
        const campos = formatarDadosEtiqueta(avaria, tipo);
        let previewText = '';
        for (let i = 1; i <= 8; i++) {
            if (campos[`txtLinha${i}`]) previewText += campos[`txtLinha${i}`] + '\n';
        }
        previewText += `\n||||||||||||||||||||||||||||||\n[ Cód. Barras: ${campos.barcodeID} ]`;
        previewEl.textContent = previewText;
    }
    
    // Mostrar modal
    const modalEl = document.getElementById('printQuantityModal');
    const modal = new bootstrap.Modal(modalEl);
    modal.show();
    
    console.log('Modal aberto');
}

/**
 * Habilita/desabilita input manual
 */
function toggleManualInput() {
    const manualInput = document.getElementById('manualQuantity');
    const selectedOption = document.querySelector('input[name="quantityOption"]:checked')?.value;
    
    if (selectedOption === 'manual') {
        manualInput.disabled = false;
        manualInput.focus();
        manualInput.select();
    } else {
        manualInput.disabled = true;
    }
}

/**
 * Confirma impressão (chamado pelo botão do modal)
 */
async function confirmPrint() {
    console.log('Confirmando impressão...');
    
    const quantityOption = document.querySelector('input[name="quantityOption"]:checked')?.value;
    let quantity;
    
    if (quantityOption === 'auto') {
        quantity = currentPrintContext.volume;
        console.log(`Quantidade automática: ${quantity}`);
    } else {
        quantity = parseInt(document.getElementById('manualQuantity').value);
        console.log(`Quantidade manual: ${quantity}`);
        
        // Validação
        if (isNaN(quantity) || quantity < 1) {
            showNotification('error', '❌ Quantidade inválida! Digite um número maior que zero.');
            return;
        }
        
        if (quantity > 500) {
            showNotification('error', '❌ Quantidade muito alta! Máximo: 500 etiquetas.');
            return;
        }
    }
    
    // Confirmação para grandes quantidades
    if (quantity > 50) {
        const confirmed = await _confirmarAcaoCritica(
            'Confirmar impressão em lote',
            `Você está prestes a imprimir <strong>${quantity} etiquetas</strong>.<br>` +
            `Isso pode levar alguns minutos e consumir bastante material.<br><br>` +
            `Deseja realmente continuar?`,
            'Imprimir', 'Cancelar'
        );

        if (!confirmed) {
            console.log('Usuário cancelou impressão de grande quantidade');
            return;
        }
    }
    
    // Fechar modal
    const modalEl = document.getElementById('printQuantityModal');
    const modal = bootstrap.Modal.getInstance(modalEl);
    modal.hide();
    
    // Iniciar impressão
    await printMultipleLabels(
        currentPrintContext.tipo,
        currentPrintContext.avariaId,
        currentPrintContext.avaria,
        quantity
    );

    // Após impressão: se for etiqueta final ou inicial, mostrar countdown no botão
    // e recarregar a página ao final da contagem
    const RELOAD_SEGUNDOS = 10;
    const btnConfirm = document.getElementById('btnConfirmPrint');
    if (btnConfirm) {
        btnConfirm.disabled = true;
        let restante = RELOAD_SEGUNDOS;
        btnConfirm.innerHTML = `<i class="fas fa-sync-alt fa-spin me-1"></i>Atualizando em <span id="countdownTimer">${restante}</span>s`;
        const interval = setInterval(() => {
            restante--;
            const el = document.getElementById('countdownTimer');
            if (el) el.textContent = restante;
            if (restante <= 0) {
                clearInterval(interval);
                if (typeof CacheEstado !== 'undefined') CacheEstado.salvarEstadoAtual('registrar');
                location.reload();
            }
        }, 1000);
    } else {
        // Fallback se o botão não for encontrado
        if (typeof CacheEstado !== 'undefined') CacheEstado.salvarEstadoAtual('registrar');
        setTimeout(() => location.reload(), RELOAD_SEGUNDOS * 1000);
    }
}

/**
 * Imprime múltiplas etiquetas
 */
async function printMultipleLabels(tipo, avariaId, avaria, quantity) {
    console.log(`Iniciando impressão de ${quantity} etiqueta(s)...`);
    
    let successCount = 0;
    let errorCount = 0;
    
    for (let i = 1; i <= quantity; i++) {
        try {
            console.log(`[${i}/${quantity}] Processando etiqueta...`);
            
            // Formatar dados
            const camposFormatados = formatarDadosEtiqueta(avaria, tipo);
            
            // Preparar dados de impressão
            const printData = {
                template: tipo === 'inicial' ? 'layout_etiqueta_inicial.lbx' : 'layout_etiqueta_final.lbx',
                fields: camposFormatados
            };
            
            // Enviar para impressão
            const printResponse = await fetch(`${PRINT_SERVER.url}/print`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(printData),
                mode: 'cors',
                signal: AbortSignal.timeout(PRINT_SERVER.timeout)
            });
            
            if (!printResponse.ok) {
                throw new Error(`HTTP ${printResponse.status}`);
            }
            
            const result = await printResponse.json();
            const isSuccess = result.success === true || result.status === 'success';
            
            if (isSuccess) {
                successCount++;
                console.log(`[${i}/${quantity}] Impressa com sucesso`);
            } else {
                errorCount++;
                console.error(`[${i}/${quantity}] Erro:`, result.message || result.error);
            }
            
            // Delay entre impressões (exceto na última)
            if (i < quantity) {
                await new Promise(resolve => setTimeout(resolve, 500));
            }
            
        } catch (error) {
            errorCount++;
            console.error(`[${i}/${quantity}] Erro de impressão:`, error.message);
            
            // Se houver muitos erros consecutivos
            if (errorCount >= 3 && i < quantity) {
                const continuar = await _confirmarAcaoCritica(
                    'Erros na impressão',
                    `${errorCount} erros detectados.<br><br>` +
                    `Deseja continuar tentando as ${quantity - i} etiquetas restantes?`,
                    'Continuar', 'Parar'
                );

                if (!continuar) {
                    console.log('Impressão cancelada pelo usuário');
                    break;
                }
            }
        }
    }
    
    // Resultado final
    console.log(`Resultado: ${successCount} sucesso, ${errorCount} erros`);

    if (successCount > 0) {
        // ── Notificar backend para registrar e disparar automações ────────
        await registrarImpressaoBackend(avariaId, tipo);
    }

    if (errorCount === 0) {
        showNotification('success', `✅ ${successCount} etiqueta(s) impressa(s) com sucesso!`);
        // Reload controlado pelo countdown em confirmPrint — não recarregar aqui.
    } else if (successCount > 0) {
        showNotification('warning', `⚠️ Impressão parcial: ${successCount} sucesso, ${errorCount} erro(s).`);
    } else {
        showNotification('error', `❌ Nenhuma etiqueta foi impressa. Verifique o servidor e tente novamente.`);
    }
}

/**
 * Notifica o backend que a impressão foi bem-sucedida.
 * Dispara automações de status configuradas para etiqueta_inicial_gerada
 * ou etiqueta_final_gerada.
 *
 * Se uma automação estiver bloqueada (status de origem inválido),
 * exibe erro visível ao operador.
 *
 * @param {string} avariaId - ID da avaria
 * @param {string} tipo - 'inicial' ou 'final'
 */
async function registrarImpressaoBackend(avariaId, tipo) {
    // Apenas para avarias com ID (ignora impressões avulsas sem avaria)
    if (!avariaId) return;

    try {
        console.log(`Registrando impressão ${tipo} para avaria ${avariaId}...`);

        const response = await fetch('/wms/api/registrar_impressao', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ avaria_id: avariaId, tipo }),
        });

        const data = await response.json();

        if (response.status === 409 && data.bloqueado) {
            // Automação bloqueada — exibir erro visível ao operador
            showNotification(
                'error',
                `⚠️ Etiqueta impressa, mas atenção: ${data.message}`
            );
            console.warn(`Automação bloqueada: ${data.message}`);
            return;
        }

        if (!data.success) {
            // Erro técnico — logar mas não interromper o fluxo
            console.warn(`Erro ao registrar impressão no backend: ${data.error || data.message}`);
            return;
        }

        // Sucesso — automações foram aplicadas
        if (data.automacoes && data.automacoes.length > 0) {
            const statusAlterados = data.automacoes.map(a => a.novo_status).join(', ');
            console.log(`Automações aplicadas: ${statusAlterados}`);
            // Não exibir notificação extra para não poluir a UX — o reload vai mostrar o novo status
        }

    } catch (err) {
        // Falha de rede — registrar mas não bloquear o usuário
        console.warn(`Não foi possível registrar impressão no backend: ${err.message}`);
    }
}

// FIM DA ADIÇÃO 4

/**
 * Verificar conexão ao carregar a página
 */
document.addEventListener('DOMContentLoaded', async function() {
    console.log('Página carregada, verificando servidor...');
    
    const serverStatus = await checkPrintServer();
    
    if (!serverStatus.online) {
        console.warn('Servidor de impressão não está rodando!');
        console.info('Execute: Brother Print Server no Windows');
        
        // Mostrar aviso na página
        const warningBanner = document.createElement('div');
        warningBanner.className = 'alert alert-warning alert-dismissible fade show';
        warningBanner.style.position = 'fixed';
        warningBanner.style.top = '70px';
        warningBanner.style.right = '20px';
        warningBanner.style.zIndex = '9998';
        warningBanner.style.maxWidth = '400px';
        warningBanner.innerHTML = `
            <strong>⚠️ Servidor de Impressão Offline</strong><br>
            Para imprimir etiquetas, execute o <code>Brother Print Server</code> no Windows.<br>
            <small class="text-muted">Erro: ${serverStatus.error}</small>
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        `;
        
        document.body.appendChild(warningBanner);
        
        setTimeout(() => {
            warningBanner.classList.remove('show');
            setTimeout(() => warningBanner.remove(), 300);
        }, 10000);
    } else {
        console.log('Sistema de impressão pronto!');
        console.log(`   Versão: ${serverStatus.data.version}`);
        console.log(`   Porta: ${serverStatus.data.port}`);
        console.log(`   Threading: ${serverStatus.data.threading ? 'Ativado' : 'Desativado'}`);
    }
    
    // ========== ADIÇÃO 5: Event listeners do modal ==========/ 
    const radioButtons = document.querySelectorAll('input[name="quantityOption"]');
    radioButtons.forEach(radio => {
        radio.addEventListener('change', toggleManualInput);
    });
    
    // Enter no input manual = confirmar
    const manualInput = document.getElementById('manualQuantity');
    if (manualInput) {
        manualInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter' && !this.disabled) {
                confirmPrint();
            }
        });
    }
    // ========== FIM DA ADIÇÃO 5 ==========/ 
});
/**
 * ALIAS EM PORTUGUÊS: Imprime etiqueta inicial
 * Chama a função original printEtiquetaInicial()
 * 
 * @param {string} avariaId - ID da avaria
 */
function imprimirEtiquetaInicial(avariaId) {
    console.log('imprimirEtiquetaInicial() (alias PT-BR) -> printEtiquetaInicial()');
    return printEtiquetaInicial(avariaId);
}

/**
 * ALIAS EM PORTUGUÊS: Imprime etiqueta final
 * Chama a função original printEtiquetaFinal()
 * 
 * @param {string} avariaId - ID da avaria
 */
function imprimirEtiquetaFinal(avariaId) {
    console.log('imprimirEtiquetaFinal() (alias PT-BR) -> printEtiquetaFinal()');
    return printEtiquetaFinal(avariaId);
}

/**
 * ALIAS EM PORTUGUÊS: Ver detalhes da avaria
 * Chama a função original showDetalhesModal()
 * 
 * @param {string} avariaId - ID da avaria
 */
function verDetalhes(avariaId) {
    console.log('verDetalhes() (alias PT-BR) -> showDetalhesModal()');
    // Renomeado para a nova função verDetalhesAvaria no registrar_avarias.html
    if (typeof verDetalhesAvaria === 'function') {
        verDetalhesAvaria(avariaId);
    } else if (typeof showDetalhesModal === 'function') {
        showDetalhesModal(avariaId);
    } else {
        console.error("Função de detalhes não encontrada.");
    }
}

/**
 * ALIAS EM PORTUGUÊS: Mostrar modal de finalização
 * Chama a função original showFinalizationModal()
 * 
 * @param {string} avariaId - ID da avaria
 */
function mostrarFinalizacao(avariaId) {
    console.log('mostrarFinalizacao() (alias PT-BR) -> showFinalizationModal()');
    // Esta função não foi fornecida, mantendo o placeholder
}


console.log('print_direct.js v3.0 totalmente carregado!');
