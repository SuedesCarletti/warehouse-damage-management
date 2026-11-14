// Continuação de registrar-avarias.js — seção: quebra de produtos.
// Ordem de carregamento importa: deve vir logo depois de registrar-avarias-triagem.js.

// QUEBRA DE PRODUTOS

/**
 * Abre modal de quebra
 */
function abrirModalQuebra(idEtiqueta, produto, avariaId, sequencia, quantidade) {
    console.log('Abrindo modal de quebra:', {idEtiqueta, produto, avariaId, sequencia, quantidade});
    
    quebraAtual = {idEtiqueta, produto, avariaId, sequencia, quantidade: quantidade || 1};
    quebraLinhasCount = 0;
    
    document.getElementById('quebraProduto').textContent = produto;
    document.getElementById('quebraEtiqueta').textContent = idEtiqueta;
    
    // Exibir quantidade total
    document.getElementById('quebraQuantidadeTotal').textContent = quantidade || 1;
    
    document.getElementById('quebraLinhas').innerHTML = '';
    
    // Adicionar 2 linhas iniciais
    adicionarLinhaQuebra();
    adicionarLinhaQuebra();
    
    // Carregar estoques
    carregarEstoquesParaQuebra();
    
    const modal = new bootstrap.Modal(document.getElementById('modalQuebra'));
    modal.show();
}

/**
 * Carrega estoques disponíveis para quebra
 */
async function carregarEstoquesParaQuebra() {
    try {
        const response = await fetch('/wms/sankhya_avarias/api/locais-financeiros');
        const data = await response.json();
        
        if (data.success && data.locais) {
            window.locaisParaQuebra = data.locais;
        }
    } catch (error) {
        console.error('Erro ao carregar estoques:', error);
    }
}

/**
 * Confirma quebra de produto
 */
async function confirmarQuebra() {
    const tbody = document.getElementById('quebraLinhas');
    const linhas = tbody.querySelectorAll('tr');
    
    if (linhas.length < 2) {
        await _alertaCritico('Destinos insuficientes', 'Adicione pelo menos 2 destinos para quebrar o produto!');
        return;
    }
    
    // Coletar dados das linhas com quantidade
    const quebras = [];
    let valido = true;
    let somaQuantidades = 0;
    
    linhas.forEach(linha => {
        const id = linha.id.replace('quebraLinha', '');
        const estoque = document.getElementById(`quebraEstoque${id}`).value;
        const qtdValue = document.getElementById(`quebraQtd${id}`).value;
        const qtd = parseFloat(qtdValue.replace(',', '.')) || 0;
        const obs = document.getElementById(`quebraObs${id}`).value;
        
        if (!estoque) {
            _alertaCritico('Estoque obrigatório', 'Selecione um estoque para todas as linhas!');
            valido = false;
            return;
        }

        if (qtd <= 0) {
            _alertaCritico('Quantidade inválida', 'Informe uma quantidade válida (maior que zero)!');
            valido = false;
            return;
        }
        
        somaQuantidades += qtd;
        
        quebras.push({
            codlocal_destino: parseInt(estoque),
            quantidade: qtd,
            observacao: obs
        });
    });
    
    if (!valido || quebras.length === 0) return;
    
    // Validar soma de quantidades
    const qtdOriginal = quebraAtual.quantidade || 1;
    
    if (Math.abs(somaQuantidades - qtdOriginal) > 0.01) {
        await _alertaCritico('Soma inválida', `Soma (${somaQuantidades.toFixed(2)}) ≠ Original (${qtdOriginal})!`);
        return;
    }

    // Confirmação
    const confirmouQuebra2 = await _confirmarAcaoCritica(
        'Confirmar quebra',
        `Etiqueta: ${quebraAtual.idEtiqueta}<br>Qtd original: ${qtdOriginal}<br>Novas etiquetas: ${quebras.length}`,
        'Continuar', 'Cancelar'
    );
    if (!confirmouQuebra2) {
        return;
    }
    
    try {
        const response = await fetch('/wms/sankhya_avarias/api/etiquetas/quebrar', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                id_etiqueta_origem: quebraAtual.idEtiqueta,
                quebras: quebras
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            const modal = bootstrap.Modal.getInstance(document.getElementById('modalMovimentacao'));
            modal.hide();

            showNotification('success', `${data.message} — ${data.novas_etiquetas.length} etiqueta(s) gerada(s). Imprimindo...`);

            const avariaResp = await fetch(`/wms/api/avaria/${quebraAtual.avariaId}`);
            const avariaHeader = (await avariaResp.json()).avaria;

            if (avariaHeader && typeof printMultipleLabels === 'function') {
                for (const idEtiqueta of data.novas_etiquetas) {
                    const printData = {
                        id_etiqueta: idEtiqueta,
                        id: idEtiqueta,
                        nota: avariaHeader.nota,
                        nfd: avariaHeader.nfd || 'N/A',
                        reembolso_pct: avariaHeader.reembolso_pct || 0,
                        codprod: quebraAtual.codprod || '',
                        volume: '1',
                        localizacao: '',
                        transportadora: avariaHeader.transportadora,
                        cte: avariaHeader.cte
                    };
                    await printMultipleLabels('final', avariaId, printData, parseInt(printData.volume) || 1);
                }
            }

            _reloadComCache(1500);
        } else {
            await _alertaCritico('Erro', traduzirErroTecnico(data.error));
        }
    } catch (error) {
        console.error('Erro na quebra:', error);
        await _alertaCritico('Erro na quebra', traduzirErroTecnico(error.message));
    }
}

// --- FIX 2: Implementação de adicionarFotos e helpers (faltando) ---
function updateImagePreview(files) {
    const previewContainer = document.getElementById('fotosPreview');
    if (!previewContainer) return;
    previewContainer.innerHTML = '';
    
    Array.from(files).slice(0, 10).forEach(file => {
        const reader = new FileReader();
        reader.onload = function(e) {
            const div = document.createElement('div');
            div.className = 'col-6 col-md-4 col-lg-3';
            div.innerHTML = `
                <img src="${e.target.result}" class="img-fluid rounded shadow" 
                     style="object-fit: cover; height: 100px; width: 100%;">
                <small class="d-block text-truncate mt-1">${file.name}</small>
            `;
            previewContainer.appendChild(div);
        };
        reader.readAsDataURL(file);
    });
}

/* 
   A função adicionarFotos deve ser modificada para incluir uma verificação de nulidade
   para o elemento do formulário que recebe o ID da etiqueta, evitando o TypeError.
*/

/**
 * Abre modal de fotos e configura upload
 * @param {string} idEtiqueta - ID da etiqueta
 * @param {string} avariaId - ID da avaria
 * @param {number} sequencia - Sequência do item
 */
function adicionarFotos(idEtiqueta, avariaId, sequencia) {
    // Armazenar contexto global
    fotosAtual = {
        idEtiqueta: idEtiqueta,
        avariaId: avariaId,
        sequencia: sequencia
    };
    
    // Limpar cache de galeria (forçar recarregar após upload)
    galeriaFotosCache = null;
    
    // Atualizar título do modal
    document.getElementById('fotosEtiqueta').textContent = idEtiqueta;
    
    // Limpar input de fotos
    const fotosInput = document.getElementById('fotosInput');
    if (fotosInput) {
        fotosInput.value = '';
    }
    
    // Limpar preview de fotos
    const preview = document.getElementById('fotosPreview');
    if (preview) {
        preview.innerHTML = '';
    }
    
  // Verificar se já existem fotos e exibir link do Drive
    verificarFotosExistentes(idEtiqueta);
    
    // Ir para aba de upload (não galeria)
    const uploadTab = document.getElementById('upload-tab');
    if (uploadTab) {
        uploadTab.click();
    }
    
    // Abrir o modal
    if (modalFotos) {
        modalFotos.show();
    } else {
        // Criar modal se não existir
        const modalElement = document.getElementById('modalFotos');
        if (modalElement) {
            modalFotos = new bootstrap.Modal(modalElement);
            modalFotos.show();
        }
    }
}

/**
 * NOVA FUNÇÃO: Verifica se já existem fotos para a etiqueta
 * e exibe o link do Drive se houver
 */
/**
 * NOVA FUNÇÃO: Verifica se já existem fotos para a etiqueta
 * e exibe o link do Drive se houver
 */
async function verificarFotosExistentes(idEtiqueta) {
    try {
        // Ocultar link inicialmente (pode ter ficado visível de outro modal)
        const driveLink = document.getElementById('driveLink');
        if (driveLink) {
            driveLink.style.display = 'none';
        }
        
        console.log(`Verificando fotos existentes para: ${idEtiqueta}`);
        
        // Buscar informações de fotos da API
        const response = await fetch(`/wms/sankhya_avarias/api/produto/${idEtiqueta}/fotos`);
        const data = await response.json();
        
        if (data.success && data.tem_fotos && data.link_pasta) {
            // Tem fotos! Exibir o link do Drive
            const btnAbrirDrive = document.getElementById('btnAbrirDrive');
            const totalFotos = document.getElementById('totalFotos');
            
            if (driveLink && btnAbrirDrive) {
                // Configurar link e contador
                btnAbrirDrive.href = data.link_pasta;
                
                if (totalFotos && data.total_fotos > 0) {
                    totalFotos.textContent = data.total_fotos;
                }
                
                // Exibir elemento
                driveLink.style.display = 'block';
                
                console.log(`Link do Drive exibido: ${data.total_fotos} foto(s)`);
            }
        } else {
            console.log(`Nenhuma foto encontrada para ${idEtiqueta}`);
        }
        
    } catch (error) {
        console.error('Erro ao verificar fotos existentes:', error);
        // Não mostrar erro para o usuário, apenas ocultar link
        const driveLink = document.getElementById('driveLink');
        if (driveLink) {
            driveLink.style.display = 'none';
        }
    }
}

async function salvarFotos() {
    await handleFileUpload();
}

async function buscarLinkDrive(idEtiqueta) {
    try {
        const response = await fetch(`/wms/sankhya_avarias/api/produto/${idEtiqueta}/fotos`);
        const data = await response.json();
        
        if (data.success && data.link_pasta) {
            // Mostrar link do Drive
            const driveLink = document.getElementById('driveLink');
            const btnAbrirDrive = document.getElementById('btnAbrirDrive');
            const totalFotos = document.getElementById('totalFotos');
            
            if (driveLink && btnAbrirDrive) {
                btnAbrirDrive.href = data.link_pasta;
                driveLink.style.display = 'block';
                
                // Atualizar contador (precisaria listar fotos para ter número exato)
                // Por ora, mostrar apenas "Fotos disponíveis"
                if (totalFotos) {
                    totalFotos.textContent = ''; // Esconder contador
                    totalFotos.parentElement.innerHTML = 'Fotos disponíveis no Drive';
                }
            }
        }
    } catch (error) {
        console.error('Erro ao buscar link do Drive:', error);
    }
}

async function handleFileUpload() {
    const fileInput = document.getElementById('fotosInput');
    const files = fileInput.files;
    
    if (files.length === 0) {
        showNotification('error', '⚠️ Selecione pelo menos uma foto');
        return;
    }

    const btn = document.querySelector('#modalFotos .modal-footer .btn-primary');
    const originalText = btn.innerHTML;
    
    try {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Enviando...';
        showNotification('info', `📤 Enviando ${files.length} fotos...`);

        const formData = new FormData();
        formData.append('id_etiqueta', fotosAtual.idEtiqueta || '');
        formData.append('avaria_id', fotosAtual.avariaId || '');
        formData.append('sequencia', fotosAtual.sequencia || '');
        Array.from(files).forEach(file => {
            formData.append('fotos', file);
        });

        const response = await fetch('/wms/sankhya_avarias/api/produto/upload-fotos', {
            method: 'POST',
            body: formData 
        });

        const data = await response.json();
        
        if (data.success) {
  // Mostrar link do Drive
            const driveLink = document.getElementById('driveLink');
            const btnAbrirDrive = document.getElementById('btnAbrirDrive');
            const totalFotos = document.getElementById('totalFotos');
            
            if (driveLink && btnAbrirDrive && totalFotos) {
                btnAbrirDrive.href = data.link_drive;
                totalFotos.textContent = data.total_fotos || files.length;
                driveLink.style.display = 'block';
            }
            
            showNotification('success', data.message);
            
            // Limpar input
            fileInput.value = '';
            document.getElementById('fotosPreview').innerHTML = '';

        } else {
            showNotification('error', `✖️ Falha: ${data.error}`);
        }

    } catch (error) {
        console.error('Upload error:', error);
        showNotification('error', `✖️ Erro: ${error.message}`);
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}

async function handleManualLinkUpdate() {
    const link = document.getElementById('fotosLinkDrive').value.trim();
    
    if (!link) {
        showNotification('error', '⚠️ Digite o link do Google Drive.');
        return;
    }
    
    if (!link.startsWith('https://drive.google.com')) {
         showNotification('error', '⚠️ O link deve ser do Google Drive.');
        return;
    }
    
    try {
        const response = await fetch('/wms/sankhya_avarias/api/produto/atualizar-fotos', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                id_etiqueta: fotosAtual.idEtiqueta,
                link_fotos_drive: link
            })
        });
        
        const data = await response.json();

        if (data.success) {
            showNotification('success', 'Link atualizado com sucesso!');
            const modal = bootstrap.Modal.getInstance(document.getElementById('modalFotos'));
            modal.hide();
            _reloadComCache(1000);
        } else {
            showNotification('error', `✖️ Erro ao salvar link: ${data.error}`);
        }
    } catch (error) {
        console.error('Link update error:', error);
        showNotification('error', `✖️ Erro de comunicação: ${error.message}`);
    }
}

// --- FIX 3: Implementação de imprimirEtiqueta (FALHAVA EM PEGAR NF/NFD E CODPROD) ---
async function imprimirEtiqueta(idEtiqueta, prefixoEstoque = null) {
    console.log('imprimirEtiqueta chamada:', idEtiqueta, 'prefixo:', prefixoEstoque);
    
    if (!idEtiqueta || idEtiqueta === 'Não gerada' || idEtiqueta === 'undefined') {
        showNotification('error', '✖️ Etiqueta não gerada ou inválida.');
        return;
    }

    const btn = event?.target?.closest('button');
    const originalText = btn?.innerHTML;
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin me-1"></i>Buscando...';
    }

    try {
        const response = await fetch('/wms/sankhya_avarias/api/etiquetas/validar', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ id_etiqueta: idEtiqueta })
        });
        
        const data = await response.json();
        
        if (!data.valida) {
            throw new Error(`Etiqueta ${idEtiqueta} inválida: ${data.erro}`);
        }

        const itemData = data.dados;
        const avariaId = itemData.avaria_id;

        const avariaResponse = await fetch(`/wms/api/avaria/${avariaId}`);
        const avariaHeader = (await avariaResponse.json()).avaria;
        
        if (!avariaHeader) throw new Error("Não foi possível carregar dados do cabeçalho da avaria.");

        let localizacaoApresentacao = itemData.codlocaldest || itemData.localizacao || '—';

        const printData = {
            id_etiqueta: itemData.id_etiqueta,
            id: itemData.id_etiqueta,
            nota: avariaHeader.nota,
            nfd: avariaHeader.nfd || 'N/A',
            reembolso_pct: avariaHeader.reembolso_pct || 0,
            codprod: itemData.codprod,
            volume: itemData.qtd_avariada || '1',
            localizacao: localizacaoApresentacao,
            transportadora: avariaHeader.transportadora,
            cte: avariaHeader.cte
        };
        // Tipo determinado pela triagem: se data_triagem preenchida → final; senão → inicial
        // Não usar prefixo_estoque pois automações podem alterá-lo antes da triagem real ocorrer
        const tipoEtiqueta = (itemData.data_triagem && String(itemData.data_triagem).trim() !== '')
            ? 'final'
            : 'inicial';

        if (typeof showPrintQuantityModal === 'function') {
            showPrintQuantityModal(tipoEtiqueta, avariaId, printData);
        } else {
            showNotification('error', '✖️ Erro interno: Sistema de impressão descarregado.');
        }

    } catch (error) {
        console.error('Erro ao preparar impressão:', error);
        showNotification('error', `✖️ Falha ao preparar impressão: ${error.message}`);
    } finally {
        if (btn && originalText) {
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    }
}

/**
 * Abre modal para visualizar fotos de um produto
 * @param {string} idEtiqueta - ID da etiqueta
 * @param {string} linkDrive - Link da pasta do Drive (opcional)
 */
function visualizarFotos(idEtiqueta, linkDrive = null) {
    // Se não tiver link, buscar da API
    if (!linkDrive || linkDrive === '') {
        showNotification('info', '🔍 Buscando informações da etiqueta...');
        
        // Buscar dados da etiqueta (deve incluir link_drive_fotos)
        fetch(`/wms/sankhya_avarias/api/etiqueta/${idEtiqueta}`)
            .then(response => response.json())
            .then(data => {
                if (data.success && data.etiqueta.link_drive_fotos) {
                    abrirModalVisualizacao(idEtiqueta, data.etiqueta.link_drive_fotos);
                } else {
                    showNotification('warning', '⚠️ Nenhuma foto disponível para esta etiqueta.');
                }
            })
            .catch(error => {
                console.error('Erro ao buscar etiqueta:', error);
                showNotification('error', '❌ Erro ao buscar informações da etiqueta.');
            });
    } else {
        abrirModalVisualizacao(idEtiqueta, linkDrive);
    }
}

/**
 * Abre modal de visualização com iframe do Google Drive
 */
function abrirModalVisualizacao(idEtiqueta, linkDrive) {
    // Verificar se modal existe, senão criar
    let modal = document.getElementById('modalVisualizarFotos');
    
    if (!modal) {
        // Criar modal dinamicamente
        const modalHTML = `
        <div class="modal fade" id="modalVisualizarFotos" tabindex="-1">
            <div class="modal-dialog modal-lg">
                <div class="modal-content">
                    <div class="modal-header bg-primary text-white">
                        <h5 class="modal-title">
                            <i class="fas fa-images"></i> Fotos do Produto
                        </h5>
                        <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body">
                        <div class="alert alert-info">
                            <i class="fas fa-tag"></i> Etiqueta: <strong id="visualizarEtiqueta"></strong>
                        </div>
                        
                        <div class="d-grid gap-2 mb-3">
                            <a id="visualizarLinkDrive" href="#" target="_blank" class="btn btn-primary">
                                <i class="fas fa-external-link-alt"></i> Abrir Pasta no Google Drive
                            </a>
                        </div>
                        
                        <div class="ratio ratio-16x9">
                            <iframe id="visualizarIframe" 
                                    src="" 
                                    allow="autoplay"
                                    allowfullscreen></iframe>
                        </div>
                        
                        <div class="alert alert-light mt-3">
                            <small class="text-muted">
                                <i class="fas fa-info-circle"></i> 
                                <strong>Dica:</strong> Para melhor visualização, clique no botão acima para abrir a pasta completa no Google Drive.
                            </small>
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Fechar</button>
                    </div>
                </div>
            </div>
        </div>
        `;
        
        document.body.insertAdjacentHTML('beforeend', modalHTML);
        modal = document.getElementById('modalVisualizarFotos');
    }
    
    // Atualizar conteúdo do modal
    document.getElementById('visualizarEtiqueta').textContent = idEtiqueta;
    document.getElementById('visualizarLinkDrive').href = linkDrive;
    
    // Converter link do Drive para embed
    // De: https://drive.google.com/drive/folders/FOLDER_ID
    // Para: https://drive.google.com/embeddedfolderview?id=FOLDER_ID
    let embedUrl = linkDrive;
    
    const folderMatch = linkDrive.match(/folders\/([^/?]+)/);
    if (folderMatch && folderMatch[1]) {
        const folderId = folderMatch[1];
        embedUrl = `https://drive.google.com/embeddedfolderview?id=${folderId}`;
    }
    
    document.getElementById('visualizarIframe').src = embedUrl;
    
    // Abrir modal
    const bsModal = new bootstrap.Modal(modal);
    bsModal.show();
}

