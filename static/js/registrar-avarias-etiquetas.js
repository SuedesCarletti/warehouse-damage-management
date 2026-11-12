// Continuação de registrar-avarias.js — seções: gerenciamento de etiquetas, gerar etiquetas, locais financeiros, motivos de devolução.
// Ordem de carregamento importa: deve vir logo depois de registrar-avarias-init.js.

// SISTEMA DE GERENCIAMENTO DE ETIQUETAS WMS


// Inicialização adicional para modals de etiquetas
(function() {
    console.log('Inicializando sistema de etiquetas...');
    
    // Aguardar DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initEtiquetas);
    } else {
        initEtiquetas();
    }
    
    function initEtiquetas() {
        const modalTriagemEl = document.getElementById('modalTriagem');
        const modalMovimentacaoEl = document.getElementById('modalMovimentacao');
        const modalFotosEl = document.getElementById('modalFotos');
        const modalQuebraEl = document.getElementById('modalQuebra');
        const modalBipagemEl = document.getElementById('modalBipagem');
        
        if (modalTriagemEl) modalTriagem = new bootstrap.Modal(modalTriagemEl);
        if (modalMovimentacaoEl) modalMovimentacao = new bootstrap.Modal(modalMovimentacaoEl);
        if (modalFotosEl) modalFotos = new bootstrap.Modal(modalFotosEl);
        if (modalQuebraEl) modalQuebra = new bootstrap.Modal(modalQuebraEl);
        if (modalBipagemEl) modalBipagem = new bootstrap.Modal(modalBipagemEl);
        
        carregarLocaisFinanceiros();
        
        // NOVO: Garante carga de locais e motivos ao abrir modal de registro
        // (necessário em consultar_avarias.html onde tabelaAvariasPendentes não existe)
        const modalRegistrarEl = document.getElementById('modalRegistrarAvaria');
        if (modalRegistrarEl) {
            modalRegistrarEl.addEventListener('show.bs.modal', function() {
                console.log('[modalRegistrarAvaria] Carregando locais e motivos...');
                carregarLocaisFinanceiros();
                carregarMotivosDevolucao();
            });
        }
        
        console.log('Sistema de etiquetas inicializado');
    }
})();

// GERAR ETIQUETAS

async function gerarTodasEtiquetasAvaria(avariaId) {
    if (!avariaId || avariaId === '' || avariaId === 'undefined') {
        showNotification('error', '✖️ Erro: ID da avaria não fornecido');
        return;
    }

    showNotification('info', '🔄 Gerando etiquetas...');

    try {
        const response = await fetch('/wms/sankhya_avarias/api/etiquetas/gerar-iniciais', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({avaria_id: avariaId})
        });

        const data = await response.json();

        if (!data.success) {
            showNotification('error', `✖️ Erro: ${data.error}`);
            return;
        }

        const etiquetas = data.etiquetas || [];
        if (etiquetas.length === 0) {
            showNotification('warning', '⚠️ Todos os produtos já possuem etiqueta.');
            _reloadComCache(1000);
            return;
        }

        showNotification('success', `✅ ${etiquetas.length} etiqueta(s) gerada(s).`);

        // Buscar dados da avaria para exibir no modal
        const avariaResp = await fetch(`/wms/api/avaria/${avariaId}`);
        const avariaHeader = (await avariaResp.json()).avaria;

        // Abre modal de confirmação de impressão (igual ao individual)
        // Reutiliza o modal de bipagem/etiqueta para mostrar as etiquetas geradas
        // e deixa o usuário escolher se imprime ou apenas fecha.
        const modalId = 'modalEtiquetasGeradas';
        let modalEl = document.getElementById(modalId);

        if (!modalEl) {
            modalEl = document.createElement('div');
            modalEl.id = modalId;
            modalEl.className = 'modal fade';
            modalEl.tabIndex = -1;
            modalEl.innerHTML = `
              <div class="modal-dialog">
                <div class="modal-content">
                  <div class="modal-header bg-success text-white">
                    <h5 class="modal-title"><i class="fas fa-tags me-2"></i>Etiquetas Geradas</h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                  </div>
                  <div class="modal-body" id="${modalId}-body"></div>
                  <div class="modal-footer">
                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">
                      <i class="fas fa-times me-1"></i>Fechar sem imprimir
                    </button>
                    <button type="button" class="btn btn-primary" id="${modalId}-btn-print">
                      <i class="fas fa-print me-1"></i>Imprimir todas
                    </button>
                  </div>
                </div>
              </div>`;
            document.body.appendChild(modalEl);
        }

        // Montar corpo do modal
        const listaEtqs = etiquetas.map(id => `<li class="list-group-item"><code>${id}</code></li>`).join('');
        document.getElementById(`${modalId}-body`).innerHTML = `
          <p class="mb-2">${etiquetas.length} etiqueta(s) inicial(is) gerada(s) para a avaria <strong>${avariaId}</strong>:</p>
          <ul class="list-group list-group-flush mb-2">${listaEtqs}</ul>
          <div class="alert alert-info mb-0 py-2">
            <i class="fas fa-info-circle me-1"></i>Você pode fechar sem imprimir agora e imprimir depois pela leitura rápida.
          </div>`;

        // Botão imprimir
        const btnPrint = document.getElementById(`${modalId}-btn-print`);
        btnPrint.onclick = async function () {
            btnPrint.disabled = true;
            btnPrint.innerHTML = '<i class="fas fa-spinner fa-spin me-1"></i>Imprimindo...';
            if (avariaHeader && typeof printMultipleLabels === 'function') {
                for (const idEtiqueta of etiquetas) {
                    const printData = {
                        id_etiqueta: idEtiqueta, id: idEtiqueta,
                        nota: avariaHeader.nota, nfd: avariaHeader.nfd || 'N/A',
                        volume: avariaHeader.volume || '1',
                        cte: avariaHeader.cte || 'N/A',
                        transportadora: avariaHeader.transportadora || 'N/A'
                    };
                    await printMultipleLabels('inicial', avariaId, printData, parseInt(printData.volume) || 1);
                }
            }
            // Countdown no botão antes do reload
            const RELOAD_SEG = 10;
            let restante = RELOAD_SEG;
            btnPrint.innerHTML = `<i class="fas fa-sync-alt fa-spin me-1"></i>Atualizando em <span id="countdownTimerGerar">${restante}</span>s`;
            const interval = setInterval(() => {
                restante--;
                const el = document.getElementById('countdownTimerGerar');
                if (el) el.textContent = restante;
                if (restante <= 0) {
                    clearInterval(interval);
                    _reloadComCache();
                }
            }, 1000);
        };

        // Recarregar ao fechar sem imprimir (caso feche sem clicar em imprimir)
        modalEl.addEventListener('hidden.bs.modal', () => _reloadComCache(500), {once: true});

        new bootstrap.Modal(modalEl).show();

    } catch (error) {
        console.error('Erro ao gerar etiquetas:', error);
        showNotification('error', `✖️ Erro: ${error.message}`);
    }
}
    
async function gerarEtiquetaIndividual(avariaId, sequencia) {
    if (!avariaId || sequencia === undefined) {
        showNotification('error', '✖️ Dados inválidos para geração de etiqueta.');
        return;
    }

    showNotification('info', '🔄 Gerando etiqueta...');

    try {
        const response = await fetch('/wms/sankhya_avarias/api/etiquetas/gerar-iniciais', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({avaria_id: avariaId})
        });

        const data = await response.json();

        if (!data.success) {
            showNotification('error', `✖️ Erro: ${data.error}`);
            return;
        }

        const etiquetas = data.etiquetas || [];
        if (etiquetas.length === 0) {
            showNotification('warning', '⚠️ Este produto já possui etiqueta.');
            _reloadComCache(1000);
            return;
        }

        showNotification('success', `✅ Etiqueta gerada. Imprimindo...`);

        const avariaResp = await fetch(`/wms/api/avaria/${avariaId}`);
        const avariaHeader = (await avariaResp.json()).avaria;

        if (avariaHeader && typeof showPrintQuantityModal === 'function') {
            const idEtiqueta = etiquetas[0];
            const printData = {
                id_etiqueta: idEtiqueta,
                id: idEtiqueta,
                nota: avariaHeader.nota,
                nfd: avariaHeader.nfd || 'N/A',
                volume: avariaHeader.volume || '1',
                cte: avariaHeader.cte || 'N/A',
                transportadora: avariaHeader.transportadora || 'N/A'
            };
            showPrintQuantityModal('inicial', avariaId, printData);
        }

        _reloadComCache(8000);

    } catch (error) {
        console.error('Erro ao gerar etiqueta individual:', error);
        showNotification('error', `✖️ Erro: ${error.message}`);
    }
}

// CARREGAR LOCAIS FINANCEIROS

// New helper function
function populateLocalSelect(selectId) {
    const select = document.getElementById(selectId);
    if (!select || !window.locaisParaQuebra) return;
    
    select.innerHTML = '<option value="">Selecione o local...</option>';
    window.locaisParaQuebra.forEach(local => {
        const option = document.createElement('option');
        option.value = local.value;
        option.textContent = local.label;
        select.appendChild(option);
    });
}

// CARREGAR MOTIVOS DE DEVOLUÇÃO

/**
 * Carrega motivos de devolução do Sankhya
 * CORRIGIDO: Validação robusta do elemento DOM
 * Chamado automaticamente ao carregar a página
 */
async function carregarMotivosDevolucao() {
    try {
        console.log('Carregando motivos de devolução...');
        
        const response = await fetch('/wms/sankhya_avarias/api/motivos-devolucao');
        const data = await response.json();
        
        if (data.success) {
            motivosDisponiveis = data.motivos;
            
            // Preencher select de motivo de devolução
            const selectMotivo = document.getElementById('motivo_devolucao');
            
            if (!selectMotivo) {
                console.error('Select motivo_devolucao não encontrado!');
                console.log('Verificando se estamos na página correta...');
                
                // Verificar se estamos na página de registro
                const formulario = document.getElementById('formRegistrarAvaria');
                if (!formulario) {
                    console.warn('Não estamos na página de registro de avaria. Ignorando carregamento de motivos.');
                    return;
                }
                
                console.error('ERRO: Formulário existe mas select motivo_devolucao não foi encontrado!');
                showNotification('error', '❌ Erro: Campo de motivo não encontrado na página');
                return;
            }
            
            // Limpar opções existentes
            selectMotivo.innerHTML = '<option value="">Selecione o motivo...</option>';
            
            // Adicionar motivos como opções
            data.motivos.forEach(motivo => {
                const option = document.createElement('option');
                option.value = motivo.value;
                option.textContent = motivo.label;
                option.setAttribute('data-descricao', motivo.label.split(' - ')[1] || motivo.label);
                selectMotivo.appendChild(option);
            });
            
            console.log(`${data.motivos.length} motivos carregados com sucesso`);
        } else {
            console.error('Erro ao carregar motivos:', data.error);
            showNotification('error', '❌ Erro ao carregar motivos de devolução');
        }
    } catch (error) {
        console.error('Erro ao carregar motivos:', error);
        console.error('Stack trace:', error.stack);
        showNotification('error', '❌ Erro ao carregar motivos de devolução');
    }
}

/**
 * Valida se motivo foi selecionado
 */
async function validarMotivoSelecionado() {
    const selectMotivo = document.getElementById('motivo_devolucao');
    if (!selectMotivo || !selectMotivo.value) {
        await _alertaCritico('Motivo obrigatório', 'Por favor, selecione um motivo de devolução!');
        selectMotivo.focus();
        return false;
    }
    return true;
}

/**
 * Preenche campos ocultos com dados do motivo selecionado
 */
function preencherCamposOcultosMontivo() {
    const selectMotivo = document.getElementById('motivo_devolucao');
    
    if (selectMotivo && selectMotivo.value) {
        const codigoMotivo = selectMotivo.value;
        const opcaoSelecionada = selectMotivo.options[selectMotivo.selectedIndex];
        const descricaoMotivo = opcaoSelecionada.getAttribute('data-descricao') || '';
        
        document.getElementById('cod_motivo_devolucao').value = codigoMotivo;
        document.getElementById('cod_motivo_inicial').value = codigoMotivo;
        document.getElementById('motivo_inicial').value = descricaoMotivo;
        
        return true;
    }
    
    return false;
}

