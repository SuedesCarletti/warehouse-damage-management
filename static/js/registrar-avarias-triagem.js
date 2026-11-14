// Continuação de registrar-avarias.js — seções: triagem, movimentação/baixa, bipagem e validação de etiquetas.
// Ordem de carregamento importa: deve vir logo depois de registrar-avarias-etiquetas.js.

// TRIAGEM DE PRODUTOS

function abrirModalTriagem(idEtiqueta, produto, avariaId, sequencia) {
    console.log('Abrindo modal de triagem:', {idEtiqueta, produto, avariaId, sequencia});
    
    triagemAtual = {idEtiqueta, produto, avariaId, sequencia};
    
    const produtoEl = document.getElementById('triagemProduto');
    const etiquetaEl = document.getElementById('triagemEtiqueta');
    const obsEl = document.getElementById('triagemObservacao');
    const localEl = document.getElementById('triagemLocalDestino');
    
    if (produtoEl) produtoEl.textContent = produto;
    if (etiquetaEl) etiquetaEl.textContent = idEtiqueta;
    if (obsEl) obsEl.value = '';
    if (localEl) localEl.value = '';
    
    if (modalTriagem) modalTriagem.show();
}

async function confirmarTriagem() {
    const localDestino = document.getElementById('triagemLocalDestino')?.value;
    const observacao = document.getElementById('triagemObservacao')?.value || '';
    
    if (!localDestino) {
        await _alertaCritico('Local obrigatório', 'Selecione o local destino!');
        return;
    }

    const confirmouTriagem = await _confirmarAcaoCritica(
        'Confirmar triagem',
        `Etiqueta atual: ${triagemAtual.idEtiqueta}<br>Produto: ${triagemAtual.produto}<br>Local destino: ${localDestino}<br><br>Uma nova etiqueta será gerada.`,
        'Confirmar', 'Cancelar'
    );
    if (!confirmouTriagem) {
        return;
    }
    
    try {
        const response = await fetch('/wms/sankhya_avarias/api/etiquetas/triar', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                id_etiqueta_00: triagemAtual.idEtiqueta,
                codlocal_destino: localDestino,
                observacao: observacao
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            await _alertaCritico('Triagem confirmada', `${data.message}<br><br>Nova etiqueta: ${data.novo_id_etiqueta}<br><br>A página será recarregada.`);
            if (modalTriagem) modalTriagem.hide();
            _reloadComCache();
        } else {
            await _alertaCritico('Erro', traduzirErroTecnico(data.error));
        }
    } catch (error) {
        console.error('Erro na triagem:', error);
        await _alertaCritico('Erro na triagem', traduzirErroTecnico(error.message));
    }
}

// MOVIMENTAÇÃO E BAIXA

/**
 * Abre o modal de movimentação
 */
/**
 * Abre o modal de movimentação
 */
function abrirModalMovimentacao(idEtiqueta, produto, avariaId, sequencia, quantidade, codprod) {  // quantidade, codprod
    console.log('Abrindo modal de movimentação:', {
        idEtiqueta,
        produto,
        avariaId,
        sequencia,
        quantidade,
        codprod
    });

    // ARMAZENAR quantidade e codprod (usados na simulação/impressão da etiqueta final)
    movimentacaoAtual = {
        idEtiqueta,
        produto,
        avariaId,
        sequencia,
        quantidade: quantidade || 1,  // default 1
        codprod: codprod || ''
    };
    
    // Preencher informações do produto
    document.getElementById('movProduto').textContent = produto;
    document.getElementById('movEtiqueta').textContent = idEtiqueta;
    
  // Exibir quantidade no modal (opcional, mas recomendado)
    const qtdInfo = document.getElementById('movQuantidade');
    if (qtdInfo) {
        qtdInfo.textContent = quantidade || 1;
    }
    
    // Reset do modal
    resetarModal();
    
    // Carregar locais disponíveis
    carregarLocaisFinanceiros();
    
    // Abrir modal
    const modal = new bootstrap.Modal(document.getElementById('modalMovimentacao'));
    modal.show();
}

/**
 * Reseta o modal para estado inicial
 */
function resetarModal() {
    // Esconder todas as seções
    document.getElementById('secaoTriagem').style.display = 'none';
    document.getElementById('secaoRecusa').style.display = 'none';
    document.getElementById('btnConfirmarMovimentacao').style.display = 'none';
    
    // Limpar campos
    document.getElementById('triagemTipo').value = '';
    document.getElementById('triagemSimples').style.display = 'none';
    document.getElementById('triagemQuebra').style.display = 'none';
    document.getElementById('triagemLocalSimples').value = '';
    document.getElementById('triagemObsSimples').value = '';
    document.getElementById('triagemValidacao').value = '';
    document.getElementById('recusaObservacao').value = '';
    document.getElementById('recusaValidacao').value = '';
    const recusaQtdEl = document.getElementById('recusaQuantidade');
    if (recusaQtdEl) recusaQtdEl.value = '';
    const recusaFotosEl = document.getElementById('recusaFotosInput');
    if (recusaFotosEl) recusaFotosEl.value = '';
    const recusaFotosStatus = document.getElementById('recusaFotosStatus');
    if (recusaFotosStatus) recusaFotosStatus.textContent = '';

    // Limpar linhas de quebra
    document.getElementById('quebrasContainer').innerHTML = '';
}

/**
 * Seleciona a ação (Triagem ou Recusa)
 */
function selecionarAcao(acao) {
    console.log('Ação selecionada:', acao);
    
    // Esconder ambas as seções
    document.getElementById('secaoTriagem').style.display = 'none';
    document.getElementById('secaoRecusa').style.display = 'none';
    
    // Mostrar seção correspondente
    if (acao === 'TRIAGEM') {
        document.getElementById('secaoTriagem').style.display = 'block';
    } else if (acao === 'RECUSA') {
        document.getElementById('secaoRecusa').style.display = 'block';
    }
    
    // Mostrar botão de confirmar
    document.getElementById('btnConfirmarMovimentacao').style.display = 'inline-block';
    
    // Armazenar ação selecionada
    movimentacaoAtual.acao = acao;
}

/**
 * Toggle entre transferência simples e quebra
 */
function toggleQuebra() {
    const tipo = document.getElementById('triagemTipo').value;
    
    document.getElementById('triagemSimples').style.display = 'none';
    document.getElementById('triagemQuebra').style.display = 'none';
    
    if (tipo === 'SIMPLES') {
        document.getElementById('triagemSimples').style.display = 'block';
    } else if (tipo === 'QUEBRA') {
        document.getElementById('triagemQuebra').style.display = 'block';
        
        // Adicionar 2 linhas iniciais para quebra
        const container = document.getElementById('quebrasContainer');
        if (container.children.length === 0) {
            adicionarLinhaQuebra();
            adicionarLinhaQuebra();
        }
    }
}

/**
 * Carrega locais financeiros disponíveis
 * CORRIGIDO v2: Null check no select + notificação só se realmente necessário
 */
async function carregarLocaisFinanceiros() {
    try {
        console.log('Carregando locais financeiros...');
        
        const response = await fetch('/wms/sankhya_avarias/api/locais-financeiros');
        
        // FIX: Verificar HTTP status antes de fazer .json()
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        const data = await response.json();
        
        if (data.success) {
            locaisDisponiveis = data.locais;
            
            // FIX: Verificar elemento antes de manipular DOM
            // Na página de consultar, triagemLocalSimples não existe — sem esse
            // check, o TypeError cai no catch e mostra erro falso na tela.
            const selectSimples = document.getElementById('triagemLocalSimples');
            if (selectSimples) {
                selectSimples.innerHTML = '<option value="">Selecione o local...</option>';
                data.locais.forEach(local => {
                    const option = document.createElement('option');
                    option.value = local.value;
                    option.textContent = local.label;
                    selectSimples.appendChild(option);
                });
                console.log(`Select triagemLocalSimples preenchido com ${data.locais.length} locais`);
            } else {
                console.warn('triagemLocalSimples não encontrado (normal fora da página de triagem)');
            }
            
            // Preencher também locaisParaQuebra (se usado)
            window.locaisParaQuebra = data.locais;
            
            console.log(`Locais financeiros carregados: ${data.locais.length}`);
        } else {
            // FIX: Só notificar se estivermos numa página que realmente usa locais
            const precisaDeLocais = document.getElementById('triagemLocalSimples') !== null
                                 || document.querySelector('.select-local-quebra') !== null;
            
            console.error('Erro ao carregar locais:', data.error);
            if (precisaDeLocais) {
                showNotification('error', '✖️ Erro ao carregar locais financeiros');
            }
        }
    } catch (error) {
        // FIX: Mesma lógica — só notificar se a página realmente precisa
        const precisaDeLocais = document.getElementById('triagemLocalSimples') !== null
                             || document.querySelector('.select-local-quebra') !== null;
        
        console.error('Erro ao carregar locais:', error);
        if (precisaDeLocais) {
            showNotification('error', '✖️ Erro ao carregar locais financeiros');
        }
    }
}

/**
 * Adiciona uma nova linha de quebra
 * VERSÃO COM DIAGNÓSTICO E TRATAMENTO DE ERROS
 */
async function adicionarLinhaQuebra() {
    try {
        console.log('adicionarLinhaQuebra() INICIADA');

        // 1. Verificar container
        const container = document.getElementById('quebrasContainer');
        if (!container) {
            console.error('ERRO: quebrasContainer não encontrado!');
            await _alertaCritico('Erro', 'Container de quebras não encontrado. Recarregue a página.');
            return;
        }
        console.log('Container encontrado:', container);

        // 2. Verificar template
        const template = document.getElementById('templateLinhaQuebra');
        if (!template) {
            console.error('ERRO: templateLinhaQuebra não encontrado!');
            await _alertaCritico('Erro', 'Template de linha não encontrado. Recarregue a página.');
            return;
        }
        console.log('Template encontrado:', template);

        // 3. Verificar locais disponíveis
        if (!locaisDisponiveis) {
            console.error('ERRO: locaisDisponiveis não está definido!');
            await _alertaCritico('Aguarde', 'Lista de locais não carregada. Aguarde e tente novamente.');
            // Tentar carregar
            carregarLocaisFinanceiros();
            return;
        }

        if (locaisDisponiveis.length === 0) {
            console.warn('AVISO: locaisDisponiveis está vazio!');
            await _alertaCritico('Aguarde', 'Carregando locais disponíveis.');
            // Tentar carregar
            carregarLocaisFinanceiros();
            return;
        }
        console.log('Locais disponíveis:', locaisDisponiveis.length, locaisDisponiveis);

        // 4. Clonar template
        const clone = template.content.cloneNode(true);
        if (!clone) {
            console.error('ERRO: Falha ao clonar template!');
            await _alertaCritico('Erro', 'Erro ao criar nova linha. Tente novamente.');
            return;
        }
        console.log('Template clonado:', clone);

        // 5. Preencher select de locais
        const select = clone.querySelector('.select-local-quebra');
        if (!select) {
            console.error('ERRO: Select não encontrado no template!');
            await _alertaCritico('Erro', 'Select de locais não encontrado no template.');
            return;
        }
        console.log('Select encontrado:', select);
        
        // 6. Popular select com locais
        let optionsAdded = 0;
        locaisDisponiveis.forEach(local => {
            try {
                const option = document.createElement('option');
                option.value = local.value;
                option.textContent = local.label;
                select.appendChild(option);
                optionsAdded++;
            } catch (err) {
                console.error('Erro ao adicionar opção:', local, err);
            }
        });
        console.log(`${optionsAdded} opções adicionadas ao select`);
        
        // 7. Adicionar ao container
        container.appendChild(clone);
        
        // 8. Confirmar sucesso
        const totalLinhas = container.children.length;
        console.log(`Linha de quebra adicionada com SUCESSO! Total de linhas: ${totalLinhas}`);
        
        // Feedback visual opcional
        // showNotification('success', 'Destino adicionado!');
        
    } catch (error) {
        console.error('ERRO CRÍTICO em adicionarLinhaQuebra():', error);
        console.error('Stack trace:', error.stack);
        await _alertaCritico('Erro', traduzirErroTecnico(error.message));
    }
}

/**
 * Remove uma linha de quebra
 */
function removerLinhaQuebra(btn) {
    const linha = btn.closest('.linha-quebra');
    const container = document.getElementById('quebrasContainer');
    
    // Não permitir remover se só tiver 1 linha
    if (container.children.length <= 1) {
        showNotification('warning', '⚠️ É necessário ter pelo menos 1 destino!');
        return;
    }
    
    linha.remove();
    console.log('Linha de quebra removida. Total:', container.children.length);
}

/**
 * Confirma a movimentação
 */
async function confirmarMovimentacao() {
    if (!movimentacaoAtual || !movimentacaoAtual.acao) {
        showNotification('error', '✖️ Erro: ação não selecionada!');
        return;
    }
    
    if (movimentacaoAtual.acao === 'TRIAGEM') {
        await confirmarTriagemMovimentacao();
    } else if (movimentacaoAtual.acao === 'RECUSA') {
        await confirmarRecusa();
    }
}

/**
 * Confirma TRIAGEM dentro do fluxo de Movimentação (transferência simples ou quebra).
 *
 * BUG CORRIGIDO: esta função tinha o mesmo nome (`confirmarTriagem`) da função do
 * modal #modalTriagem (linha ~2328, usado pelo botão "Triar" de cada item — ver
 * onclick="abrirModalTriagem(...)" em registrar_avarias.html). Como as duas eram
 * globais e esta era declarada depois, ela sobrescrevia a outra — o botão
 * "Confirmar" do #modalTriagem executava ESTA lógica (que lê #triagemTipo,
 * inexistente nesse modal) e quebrava com TypeError sempre que usado. Renomeada
 * para eliminar a colisão; chamada em confirmarMovimentacao() atualizada.
 */
async function confirmarTriagemMovimentacao() {
    const tipo = document.getElementById('triagemTipo').value;
    
    if (!tipo) {
        showNotification('error', '⚠️ Selecione o tipo de transferência!');
        return;
    }
    
    // Validar etiqueta
    const etiquetaValidacao = document.getElementById('triagemValidacao').value;
    if (!etiquetaValidacao) {
        showNotification('error', '⚠️ Bipe ou digite a etiqueta para confirmar!');
        return;
    }
    
    if (etiquetaValidacao !== movimentacaoAtual.idEtiqueta) {
        showNotification('error', `✖️ Etiqueta incorreta! Esperado: ${movimentacaoAtual.idEtiqueta}`);
        return;
    }
    
    if (tipo === 'SIMPLES') {
        await executarTransferenciaSimples();
    } else if (tipo === 'QUEBRA') {
        await executarQuebra();
    }
}

/**
 * Executa transferência simples
 */
async function executarTransferenciaSimples() {
    const codlocalDestino = document.getElementById('triagemLocalSimples').value;
    const observacao = document.getElementById('triagemObsSimples').value || '';
    
    if (!codlocalDestino) {
        showNotification('error', '⚠️ Selecione o local de destino!');
        return;
    }
    
    // Confirmação final
    const confirmouTransferencia = await _confirmarAcaoCritica(
        'Confirmar transferência',
        `Etiqueta atual: ${movimentacaoAtual.idEtiqueta}<br>Produto: ${movimentacaoAtual.produto}<br>Local destino: ${codlocalDestino}<br><br>` +
        `Esta etiqueta será BAIXADA e uma NOVA etiqueta será gerada no local de destino.`,
        'Continuar', 'Cancelar'
    );
    if (!confirmouTransferencia) {
        return;
    }
    
    try {
        showNotification('info', '⟳ Processando transferência...');
        
        const response = await fetch('/wms/sankhya_avarias/api/etiquetas/transferir', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                id_etiqueta_origem: movimentacaoAtual.idEtiqueta,
                codlocal_destino: parseInt(codlocalDestino),
                observacao: observacao
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            const modal = bootstrap.Modal.getInstance(document.getElementById('modalMovimentacao'));
            modal.hide();

            showNotification('success', `${data.message} — Imprimindo etiqueta ${data.nova_etiqueta}...`);

            // Buscar dados da avaria para impressão da etiqueta final
            const avariaResp = await fetch(`/wms/api/avaria/${movimentacaoAtual.avariaId}`);
            const avariaHeader = (await avariaResp.json()).avaria;

            if (avariaHeader && typeof showPrintQuantityModal === 'function') {
                const printData = {
                    id_etiqueta: data.nova_etiqueta,
                    id: data.nova_etiqueta,
                    nota: avariaHeader.nota,
                    nfd: avariaHeader.nfd || 'N/A',
                    reembolso_pct: avariaHeader.reembolso_pct || 0,
                    codprod: movimentacaoAtual.codprod || '',
                    volume: movimentacaoAtual.quantidade || '1',
                    localizacao: codlocalDestino,
                    transportadora: avariaHeader.transportadora,
                    cte: avariaHeader.cte
                };
                showPrintQuantityModal('final', movimentacaoAtual.avariaId, printData);
            }

            _reloadComCache(8000);
        } else {
            showNotification('error', `✖️ Erro: ${data.error}`);
        }
    } catch (error) {
        console.error('Erro na transferência:', error);
        showNotification('error', `✖️ Erro na transferência: ${error.message}`);
    }
}

/**
 * Executa quebra de quantidade em múltiplos locais
 */
async function executarQuebra() {
    const linhas = document.querySelectorAll('.linha-quebra');
    
    if (linhas.length < 2) {
        showNotification('error', '⚠️ É necessário ter pelo menos 2 destinos para quebra!');
        return;
    }
    
    // CORREÇÃO: Coletar dados das quebras COM quantidade
    const quebras = [];
    let valido = true;
    let somaQuantidades = 0;
    
    linhas.forEach((linha, index) => {
        const select = linha.querySelector('.select-local-quebra');
        const inputQtd = linha.querySelector('.input-qtd-quebra');
        const inputObs = linha.querySelector('.input-obs-quebra');
        
        // Validar se elementos existem
        if (!select || !inputQtd) {
            console.error(`Linha ${index}: elementos não encontrados`, {select, inputQtd});
            showNotification('error', `Erro na linha ${index + 1}: elementos não encontrados!`);
            valido = false;
            return;
        }
        
        const codlocal = select.value;
        const qtd = parseFloat(inputQtd.value.replace(',', '.')) || 0;
        const obs = inputObs?.value || '';
        
        // Validar local
        if (!codlocal) {
            showNotification('error', '⚠️ Selecione um local para todas as linhas!');
            valido = false;
            return;
        }
        
        // Validar quantidade
        if (qtd <= 0) {
            showNotification('error', '⚠️ Informe uma quantidade válida (maior que zero) para todas as linhas!');
            valido = false;
            return;
        }
        
        somaQuantidades += qtd;
        
        quebras.push({
            codlocal_destino: parseInt(codlocal),
            quantidade: qtd,
            observacao: obs
        });
    });
    
    if (!valido || quebras.length === 0) {
        return;
    }
    
    // Validar soma das quantidades
    const qtdOriginal = movimentacaoAtual.quantidade || 1;
    
    if (Math.abs(somaQuantidades - qtdOriginal) > 0.01) {
        showNotification('error', 
            `⚠️ ERRO: Soma das quantidades (${somaQuantidades.toFixed(2)}) ` +
            `diferente da quantidade original (${qtdOriginal})!`
        );
        return;
    }
    
    // Confirmação final
    const confirmouQuebra1 = await _confirmarAcaoCritica(
        'Confirmar quebra',
        `Etiqueta original: ${movimentacaoAtual.idEtiqueta}<br>` +
        `Produto: ${movimentacaoAtual.produto}<br>` +
        `Qtd original: ${qtdOriginal}<br>` +
        `Novas etiquetas: ${quebras.length}<br><br>` +
        `Detalhes das quebras:<br>` +
        quebras.map((q, i) => `${i + 1}. Local ${q.codlocal_destino}: ${q.quantidade} unidades`).join('<br>'),
        'Continuar', 'Cancelar'
    );
    if (!confirmouQuebra1) {
        return;
    }
    
    try {
        showNotification('info', '⟳ Processando quebra...');
        
        const response = await fetch('/wms/sankhya_avarias/api/etiquetas/quebrar', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                id_etiqueta_origem: movimentacaoAtual.idEtiqueta,
                quebras: quebras
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            const modal = bootstrap.Modal.getInstance(document.getElementById('modalMovimentacao'));
            modal.hide();

            showNotification('success', `${data.message} — ${data.novas_etiquetas.length} etiqueta(s) gerada(s). Imprimindo...`);

            // Buscar dados da avaria para impressão das etiquetas finais
            const avariaResp = await fetch(`/wms/api/avaria/${movimentacaoAtual.avariaId}`);
            const avariaHeader = (await avariaResp.json()).avaria;

            if (avariaHeader && typeof printMultipleLabels === 'function') {
                for (const idEtiqueta of data.novas_etiquetas) {
                    const printData = {
                        id_etiqueta: idEtiqueta,
                        id: idEtiqueta,
                        nota: avariaHeader.nota,
                        nfd: avariaHeader.nfd || 'N/A',
                        reembolso_pct: avariaHeader.reembolso_pct || 0,
                        codprod: movimentacaoAtual.codprod || '',
                        volume: '1',
                        localizacao: '',
                        transportadora: avariaHeader.transportadora,
                        cte: avariaHeader.cte
                    };
                    await printMultipleLabels('final', movimentacaoAtual.avariaId, printData, 1);
                }
            }

            _reloadComCache(1500);
        } else {
            showNotification('error', `✖️ Erro: ${data.error}`);
        }
    } catch (error) {
        console.error('Erro na quebra:', error);
        showNotification('error', `✖️ Erro na quebra: ${error.message}`);
    }
}
    
/**
 * Confirma RECUSA (descarte/baixa)
 */
async function confirmarRecusa() {
    const observacao = document.getElementById('recusaObservacao').value;
    
    if (!observacao || observacao.trim().length < 10) {
        showNotification('error', '⚠️ Informe o motivo da recusa (mínimo 10 caracteres)!');
        return;
    }
    
    // Validar etiqueta
    const etiquetaValidacao = document.getElementById('recusaValidacao').value;
    if (!etiquetaValidacao) {
        showNotification('error', '⚠️ Bipe ou digite a etiqueta para confirmar!');
        return;
    }
    
    if (etiquetaValidacao !== movimentacaoAtual.idEtiqueta) {
        showNotification('error', `✖️ Etiqueta incorreta! Esperado: ${movimentacaoAtual.idEtiqueta}`);
        return;
    }
    
    // Confirmação final
    const confirmouBaixaEtiqueta = await _confirmarAcaoCritica(
        'Atenção — ação permanente',
        'Esta ação irá BAIXAR a etiqueta PERMANENTEMENTE!<br><br>A etiqueta não poderá mais ser utilizada após esta operação.<br><br>Confirma a baixa da etiqueta?',
        'Confirmar baixa', 'Cancelar'
    );
    if (!confirmouBaixaEtiqueta) {
        return;
    }
    
    try {
        showNotification('info', '⟳ Processando baixa...');
        
        const qtdRecusadaRaw = document.getElementById('recusaQuantidade')?.value;
        const qtdRecusada = qtdRecusadaRaw ? parseFloat(qtdRecusadaRaw.replace(',', '.')) : 0;

        const response = await fetch('/wms/sankhya_avarias/api/etiquetas/recusar', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                id_etiqueta: movimentacaoAtual.idEtiqueta,
                observacao: observacao,
                quantidade_recusada: qtdRecusada || 0
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            showNotification('success', `${data.message}`);

            // Upload de fotos da recusa (opcional, não-bloqueante)
            const fotosInput = document.getElementById('recusaFotosInput');
            const idEtiquetaFinal = data.id_etiqueta_final;
            if (fotosInput && fotosInput.files.length > 0 && idEtiquetaFinal) {
                const statusEl = document.getElementById('recusaFotosStatus');
                if (statusEl) statusEl.textContent = 'Enviando fotos...';
                try {
                    const fd = new FormData();
                    fd.append('id_etiqueta', idEtiquetaFinal);
                    for (const f of fotosInput.files) fd.append('fotos', f);
                    const upRes = await fetch('/wms/sankhya_avarias/api/produto/upload-fotos', {
                        method: 'POST', body: fd
                    });
                    const upData = await upRes.json();
                    if (statusEl) {
                        statusEl.textContent = upData.success
                            ? `✓ ${upData.total_fotos} foto(s) enviada(s)`
                            : `Fotos não enviadas: ${upData.error || 'erro'}`;
                    }
                } catch (upErr) {
                    console.warn('Upload de fotos da recusa falhou (não-fatal):', upErr);
                }
            }

            const modal = bootstrap.Modal.getInstance(document.getElementById('modalMovimentacao'));
            modal.hide();

            setTimeout(() => location.reload(), 1500);
        } else {
            showNotification('error', `✖️ Erro: ${data.error}`);
        }
    } catch (error) {
        console.error('Erro na baixa:', error);
        showNotification('error', `✖️ Erro na baixa: ${error.message}`);
    }
}

/**
 * Função helper para mostrar notificações.
 *
 * ANTES: caía num alert() nativo bloqueante, porque window.globalNotificationHandler
 * nunca é definido em lugar nenhum do app — ou seja, TODO showNotification() nesta
 * tela (dezenas de chamadas) sempre executava esse "fallback". Substituído por um
 * toast Bootstrap não-bloqueante (mesmo padrão usado em print_direct.js), mantendo
 * a função síncrona e sem valor de retorno para não exigir mudança nos chamadores.
 */
function showNotification(type, message) {
    if (typeof window.globalNotificationHandler === 'function') {
        window.globalNotificationHandler(type, message);
        return;
    }

    if (typeof bootstrap === 'undefined' || typeof bootstrap.Toast === 'undefined') {
        console.warn(`[${type}] ${message}`);
        return;
    }

    let toastContainer = document.getElementById('toastContainer');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.id = 'toastContainer';
        toastContainer.className = 'toast-container position-fixed top-0 end-0 p-3';
        toastContainer.style.zIndex = '9999';
        document.body.appendChild(toastContainer);
    }

    const bg = type === 'success' ? 'success' : type === 'error' ? 'danger' : type === 'warning' ? 'warning' : 'info';
    const toast = document.createElement('div');
    toast.className = `toast align-items-center text-white bg-${bg} border-0`;
    toast.setAttribute('role', 'alert');
    toast.innerHTML = `
        <div class="d-flex">
            <div class="toast-body">${message}</div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
        </div>`;

    toastContainer.appendChild(toast);
    const bsToast = new bootstrap.Toast(toast, { delay: 5000 });
    bsToast.show();
    toast.addEventListener('hidden.bs.toast', () => toast.remove());
}

console.log('Script de movimentação simplificada carregado!');

// BIPAGEM E VALIDAÇÃO DE ETIQUETAS

/**
 * Abre modal de bipagem
 */
function abrirModalBipagem() {
    document.getElementById('bipagemInput').value = '';
    document.getElementById('bipagemResultado').style.display = 'none';
    
    // FIX: Verificar se modalBipagem foi inicializado (quebra de sintaxe)
    if (!modalBipagem) {
        modalBipagem = new bootstrap.Modal(document.getElementById('modalBipagem'));
    }

    modalBipagem.show();
    
    // Auto-focus no input
    setTimeout(() => {
        document.getElementById('bipagemInput').focus();
    }, 500);
}

/**
 * Valida etiqueta bipada
 */
async function validarEtiquetaBipada() {
    const etiqueta = document.getElementById('bipagemInput').value.trim();
    
    if (!etiqueta) {
        await _alertaCritico('Etiqueta obrigatória', 'Digite ou bipe uma etiqueta!');
        return;
    }
    
    try {
        const response = await fetch('/wms/sankhya_avarias/api/etiquetas/validar', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({id_etiqueta: etiqueta})
        });
        
        const data = await response.json();
        
        const resultado = document.getElementById('bipagemResultado');
        resultado.style.display = 'block';


    if (data.valida) {
        const et = data.dados;
        
        // Determinar status e ações
        let badgeStatus = '';
        let acoesHTML = '';
        
        if (et.status_etiqueta === 'ativa') {
            badgeStatus = '<span class="badge bg-success fs-6">✔ ATIVA</span>';
        
        // Armazena dados no objeto global para evitar quebra por aspas no nome do produto
        window._bipagemEtiquetaAtual = {
            id_etiqueta: et.id_etiqueta,
            descrprod:   et.descrprod,
            avaria_id:   et.avaria_id,
            sequencia:   et.sequencia,
            quantidade:  et.quantidade || 1,
            codprod:     et.codprod || ''
        };
        
        acoesHTML = `
            <div class="d-grid gap-2">
                <button class="btn btn-warning btn-lg" 
                        onclick="_fecharBipagemEMovimentar()">
                    <i class="fas fa-exchange-alt me-2"></i>Movimentar Produto
                    <small class="d-block mt-1 opacity-75">Transferir, dar baixa ou descartar</small>
                </button>
        
                <div class="btn-group" role="group">
                    <button class="btn btn-outline-secondary" 
                            onclick="fecharModalEImprimir('${et.id_etiqueta}')">
                        <i class="fas fa-print me-1"></i>Reimprimir
                    </button>
                    <button class="btn btn-outline-info" 
                            onclick="localizarNaTabela('${et.avaria_id}')">
                        <i class="fas fa-search me-1"></i>Localizar na Lista
                    </button>
                </div>
            </div>
        `;
        } else if (et.status_etiqueta === 'baixada') {
            badgeStatus = '<span class="badge bg-danger fs-6">✘ BAIXADA</span>';
            acoesHTML = `
                <div class="alert alert-warning mb-0">
                    <i class="fas fa-exclamation-triangle me-2"></i>
                    Esta etiqueta já foi <strong>BAIXADA</strong> e não pode mais ser movimentada.
                </div>
            `;
        } else if (et.status_etiqueta === 'recusada') {
            badgeStatus = '<span class="badge bg-dark fs-6">✘ RECUSADA</span>';
            acoesHTML = `
                <div class="alert alert-dark mb-0">
                    <i class="fas fa-ban me-2"></i>
                    Esta etiqueta foi <strong>RECUSADA</strong> e não pode mais ser movimentada.
                </div>
            `;
        } else {
            badgeStatus = '<span class="badge bg-secondary fs-6">◣️ PENDENTE</span>';
            acoesHTML = `
                <div class="alert alert-info mb-0">
                    <i class="fas fa-info-circle me-2"></i>
                    Etiqueta ainda não foi gerada completamente. Finalize o registro primeiro.
                </div>
            `;
        }
        
        resultado.innerHTML = `
            <div class="card border-success">
                <div class="card-header bg-success text-white">
                    <h6 class="mb-0"><i class="fas fa-check-circle me-2"></i>Etiqueta Encontrada</h6>
                </div>
                <div class="card-body">
                    <table class="table table-sm table-borderless mb-3">
                        <tr>
                            <th style="width: 120px;">Etiqueta:</th>
                            <td><strong class="text-primary fs-5">${et.id_etiqueta}</strong> ${badgeStatus}</td>
                        </tr>
                        <tr>
                            <th>Produto:</th>
                            <td><strong>${et.descrprod}</strong></td>
                        </tr>
                        <tr>
                            <th>Estoque Atual:</th>
                            <td><span class="badge bg-info">${et.prefixo_estoque}</span></td>
                        </tr>
                        <tr>
                            <th>Avaria ID:</th>
                            <td><code>${et.avaria_id}</code></td>
                        </tr>
                        ${et.observacao_produto ? `
                        <tr>
                            <th>Observação:</th>
                            <td class="text-muted"><small>${et.observacao_produto}</small></td>
                        </tr>
                        ` : ''}
                    </table>
                    
                    <hr>
                    
                    <div class="mt-3">
                        <h6 class="text-muted mb-3">O que deseja fazer?</h6>
                        ${acoesHTML}
                    </div>
                </div>
            </div>
        `;
        
    } else {
        // Etiqueta não encontrada
        resultado.innerHTML = `
            <div class="alert alert-danger">
                <h6><i class="fas fa-times-circle me-2"></i>Etiqueta Não Encontrada</h6>
                <p class="mb-2">${data.erro || 'Etiqueta não existe no sistema'}</p>
                <hr>
                <small class="text-muted">
                    <i class="fas fa-lightbulb me-1"></i>
                    <strong>Dica:</strong> Verifique se digitou corretamente o código da etiqueta (formato: XX-XXXX-XXXX)
                </small>
            </div>
        `;
    }
    }
        
    catch (error) {
        console.error('Erro ao validar:', error);
        const resultado = document.getElementById('bipagemResultado');
        resultado.style.display = 'block';
        resultado.innerHTML = `
            <div class="alert alert-danger">
                <h6><i class="fas fa-exclamation-triangle me-2"></i>Erro na Validação</h6>
                <p class="mb-0">${error.message}</p>
            </div>
        `;
    }
}

/**
 * Wrapper seguro para movimentação via leitura rápida.
 * Lê dados do objeto global para evitar quebra por aspas no nome do produto.
 */
function _fecharBipagemEMovimentar() {
    const d = window._bipagemEtiquetaAtual;
    if (!d) {
        console.error('_bipagemEtiquetaAtual não definido!');
        return;
    }
    fecharModalEMovimentar(d.id_etiqueta, d.descrprod, d.avaria_id, d.sequencia, d.quantidade, d.codprod);
}

/**
 * Localiza avaria na tabela pendentes
 */
function localizarNaTabela(avariaId) {
    const modal = bootstrap.Modal.getInstance(document.getElementById('modalBipagem'));
    modal.hide();
    
    setTimeout(() => {
        // Buscar elemento na tabela
        const row = document.querySelector(`tr[data-avaria-id="${avariaId}"]`);
        
        if (row) {
            // Scroll até o elemento
            row.scrollIntoView({ behavior: 'smooth', block: 'center' });
            
            // Highlight temporário
            row.style.backgroundColor = '#fff3cd';
            setTimeout(() => {
                row.style.backgroundColor = '';
            }, 2000);
            
            showNotification('success', `Avaria ${avariaId} localizada na lista`);
        } else {
            showNotification('info', 'Avaria não está na lista de pendentes. Pode já ter sido finalizada.');
        }
    }, 300);
}

/**
 * Funções auxiliares para fechar modal e executar ação
 */
function fecharModalETriar(idEtiqueta, produto, avariaId, sequencia) {
    const modal = bootstrap.Modal.getInstance(document.getElementById('modalBipagem'));
    modal.hide();
    setTimeout(() => {
        abrirModalTriagem(idEtiqueta, produto, avariaId, sequencia);
    }, 300);
}

function fecharModalEMovimentar(idEtiqueta, produto, avariaId, sequencia, quantidade, codprod) {  // quantidade, codprod
    const modal = bootstrap.Modal.getInstance(document.getElementById('modalBipagem'));
    modal.hide();
    setTimeout(() => {
        abrirModalMovimentacao(idEtiqueta, produto, avariaId, sequencia, quantidade, codprod);  // quantidade, codprod
    }, 300);
}

function fecharModalEImprimir(idEtiqueta) {
    const modal = bootstrap.Modal.getInstance(document.getElementById('modalBipagem'));
    modal.hide();
    setTimeout(() => {
        imprimirEtiqueta(idEtiqueta);
    }, 300);
}

// Auto-validar ao pressionar Enter
document.addEventListener('DOMContentLoaded', function() {
    const bipagemInput = document.getElementById('bipagemInput');
    if (bipagemInput) {
        bipagemInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                validarEtiquetaBipada();
            }
        });
    }
});

