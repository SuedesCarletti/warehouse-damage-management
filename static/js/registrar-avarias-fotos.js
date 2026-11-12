// Continuação de registrar-avarias.js — seções: fotos/galeria e recusa de item sem etiqueta.
// Ordem de carregamento importa: deve vir logo depois de registrar-avarias-quebra.js.

// FIM DAS FUNÇÕES ETIQUETA

async function carregarGaleriaFotos() {
    // Se já carregou e tem cache, não recarregar
    if (galeriaFotosCache) {
        exibirGaleriaFotos(galeriaFotosCache);
        return;
    }
    
    const idEtiqueta = fotosAtual?.idEtiqueta;
    
    if (!idEtiqueta) {
        console.warn('ID da etiqueta não disponível');
        return;
    }
    
    // Mostrar loading
    mostrarEstadoGaleria('loading');
    
    try {
        console.log(`Buscando fotos da etiqueta: ${idEtiqueta}`);
        
        const response = await fetch(`/wms/sankhya_avarias/api/produto/${idEtiqueta}/fotos`);
        const data = await response.json();
        
        if (data.success) {
            galeriaFotosCache = data; // Salvar em cache
            exibirGaleriaFotos(data);
        } else {
            // Sem fotos ou erro
            if (data.error.includes('Nenhuma pasta encontrada')) {
                mostrarEstadoGaleria('empty');
            } else {
                mostrarEstadoGaleria('error', data.error);
            }
        }
        
    } catch (error) {
        console.error('Erro ao carregar fotos:', error);
        mostrarEstadoGaleria('error', `Erro de comunicação: ${error.message}`);
    }
}

// NOVA FUNÇÃO: Controla estados da galeria (loading, empty, error, fotos)
function mostrarEstadoGaleria(estado, mensagemErro = '') {
    const loading = document.getElementById('galeriaLoading');
    const empty = document.getElementById('galeriaEmpty');
    const error = document.getElementById('galeriaError');
    const galeria = document.getElementById('galeriaFotos');
    
    // Esconder tudo primeiro
    loading.style.display = 'none';
    empty.style.display = 'none';
    error.style.display = 'none';
    galeria.style.display = 'none';
    
    // Mostrar estado específico
    switch(estado) {
        case 'loading':
            loading.style.display = 'block';
            break;
        case 'empty':
            empty.style.display = 'block';
            break;
        case 'error':
            error.style.display = 'block';
            document.getElementById('galeriaErrorMsg').textContent = mensagemErro;
            break;
        case 'fotos':
            galeria.style.display = 'block';
            break;
    }
}

// NOVA FUNÇÃO: Exibe galeria de fotos no DOM
function exibirGaleriaFotos(data) {
    const { fotos, total_fotos, link_pasta } = data;
    
    // Atualizar contador
    document.getElementById('galeriaTotalFotos').textContent = total_fotos;
    
    // Atualizar link do Drive
    const linkDrive = document.getElementById('galeriaLinkDrive');
    linkDrive.href = link_pasta || '#';
    
    // Container da galeria
    const container = document.getElementById('galeriaContainer');
    container.innerHTML = ''; // Limpar conteúdo anterior
    
    // Se não tem fotos, mostrar empty
    if (!fotos || fotos.length === 0) {
        mostrarEstadoGaleria('empty');
        return;
    }
    
    // Criar cards para cada foto
    fotos.forEach((foto, index) => {
        const colDiv = document.createElement('div');
        colDiv.className = 'col-md-4 col-sm-6';
        
        colDiv.innerHTML = `
            <div class="card h-100 shadow-sm hover-shadow" style="cursor: pointer;" 
                 onclick="abrirFotoFull('${foto.view_url}', '${foto.filename}')">
                <div class="position-relative">
                    <img src="${foto.thumbnail_url}" 
                         class="card-img-top" 
                         alt="${foto.filename}"
                         loading="lazy"
                         style="height: 200px; object-fit: cover;">
                    <div class="position-absolute top-0 end-0 m-2">
                        <span class="badge bg-dark bg-opacity-75">
                            <i class="fas fa-search-plus"></i>
                        </span>
                    </div>
                </div>
                <div class="card-body p-2">
                    <p class="card-text small text-muted mb-0 text-truncate" title="${foto.filename}">
                        <i class="fas fa-image"></i> ${foto.filename}
                    </p>
                </div>
            </div>
        `;
        
        container.appendChild(colDiv);
    });
    
    // Mostrar galeria
    mostrarEstadoGaleria('fotos');
    
    console.log(`${total_fotos} foto(s) exibida(s)`);
}

// NOVA FUNÇÃO: Abre foto em tamanho real
function abrirFotoFull(viewUrl, filename) {
    document.getElementById('modalFotoFullNome').textContent = filename;
    document.getElementById('modalFotoFullImg').src = viewUrl;
    
    const modal = new bootstrap.Modal(document.getElementById('modalFotoFull'));
    modal.show();
}

// MODIFICAÇÃO: Limpar cache ao fechar modal principal
document.getElementById('modalFotos')?.addEventListener('hidden.bs.modal', function () {
    console.log('Modal fechado - limpando cache');
    
    // Limpar cache ao fechar modal
    galeriaFotosCache = null;
    
    // Voltar para aba de upload
    const uploadTab = document.getElementById('upload-tab');
    if (uploadTab) {
        uploadTab.click();
    }
});

// MODIFICAÇÃO: Carregar fotos ao abrir aba Galeria
document.getElementById('galeria-tab')?.addEventListener('shown.bs.tab', function () {
    console.log('Aba Galeria aberta - carregando fotos...');
    
    // Esconder botão de salvar na aba de visualização
    const btnSalvar = document.getElementById('btnSalvarFotos');
    if (btnSalvar) {
        btnSalvar.style.display = 'none';
    }
    
    // NOVO: Carregar fotos automaticamente
    carregarGaleriaFotos();
});

document.getElementById('upload-tab')?.addEventListener('shown.bs.tab', function () {
    console.log('Aba Upload aberta');
    
    // Mostrar botão de salvar na aba de upload
    const btnSalvar = document.getElementById('btnSalvarFotos');
    if (btnSalvar) {
        btnSalvar.style.display = 'block';
    }
});

// NOVO: Abrir aba Galeria quando modal for aberto E já existirem fotos
document.getElementById('modalFotos')?.addEventListener('shown.bs.modal', async function () {
    console.log('Modal de fotos aberto');
    
    // Se fotosAtual está definido, verificar se já existem fotos
    if (fotosAtual && fotosAtual.idEtiqueta) {
        console.log(`Verificando fotos existentes para: ${fotosAtual.idEtiqueta}`);
        
        try {
            const response = await fetch(`/wms/sankhya_avarias/api/produto/${fotosAtual.idEtiqueta}/fotos`);
            const data = await response.json();
            
            if (data.success && data.total_fotos > 0) {
                console.log(`Encontradas ${data.total_fotos} foto(s) - abrindo aba Galeria`);
                
                // Se já tem fotos, abrir aba de galeria automaticamente
                const galeriaTab = document.getElementById('galeria-tab');
                if (galeriaTab) {
                    galeriaTab.click();
                }
            } else {
                console.log('Nenhuma foto encontrada - mantendo aba Upload');
            }
        } catch (error) {
            console.error('Erro ao verificar fotos:', error);
            // Em caso de erro, manter na aba de upload
        }
    }
});

// CONFIGURAÇÃO PARA EXPORTAÇÃO EXCEL
const COLUMN_MAP_EXPORT = [
    { physicalIndex: 0,  name: 'Ações',           key: 'acoes',              type: 'header', filterable: false },
    { physicalIndex: 1,  name: 'Ações Item',      key: 'acoes_item',         type: 'item',   filterable: false },
    { physicalIndex: 2,  name: 'Cód. Empresa',     key: 'cod_empresa',        type: 'header', filterable: true  },
    { physicalIndex: 3,  name: 'Nota',             key: 'nota',               type: 'header', filterable: true  },
    { physicalIndex: 4,  name: 'NFD',              key: 'nfd',                type: 'header', filterable: true  },
    { physicalIndex: 5,  name: 'Cód. Produto',     key: 'cod_produto',        type: 'item',   filterable: true  },
    { physicalIndex: 6,  name: 'Produto',          key: 'produto',            type: 'item',   filterable: true  },
    { physicalIndex: 7,  name: 'Qtd Avariada',     key: 'qtd_avariada',       type: 'item',   filterable: true  },
    { physicalIndex: 8,  name: 'Localização',      key: 'localizacao',        type: 'item',   filterable: true  },
    { physicalIndex: 9,  name: 'Etiqueta ID',      key: 'id_etiqueta',        type: 'item',   filterable: true  },
    { physicalIndex: 10, name: 'Status Etiq.',     key: 'status_etiqueta',    type: 'item',   filterable: true  },
    { physicalIndex: 11, name: 'Qtd Nota',         key: 'qtd_nota',           type: 'header', filterable: true  },
    { physicalIndex: 12, name: 'Transportadora',   key: 'transportadora',     type: 'header', filterable: true  },
    { physicalIndex: 13, name: 'Estoque',          key: 'status_estoque',     type: 'header', filterable: true  },
    { physicalIndex: 14, name: 'Indenização',      key: 'status_indenizacao', type: 'header', filterable: true  },
    { physicalIndex: 15, name: 'Data Registro',    key: 'data_registro',      type: 'header', filterable: true  },
    { physicalIndex: 16, name: 'Data Triagem',     key: 'data_triagem',       type: 'item',   filterable: true  },
    { physicalIndex: 17, name: 'Data Finalização', key: 'data_finalizacao',   type: 'header', filterable: true  }
];

// Verificação de sanidade
console.log(`COLUMN_MAP_EXPORT definido com ${COLUMN_MAP_EXPORT.length} colunas`);

// FIM DA CONFIGURAÇÃO PARA EXPORTAÇÃO

console.log('Correção de galeria de fotos aplicada');
    
console.log('Sistema de etiquetas WMS carregado');

// LÓGICA PARA RECUSAR ITEM DIRETAMENTE (SEM ETIQUETA / PARCIAL)
function abrirModalRecusarItemAvaria(avariaId, sequencia, qtdDisponivel, descrProd) {
    document.getElementById('recusarItemAvariaId').value = avariaId;
    document.getElementById('recusarItemSeq').value = sequencia;
    document.getElementById('recusarItemQtdMaxTxt').innerText = qtdDisponivel;
    document.getElementById('recusarItemDescr').innerText = descrProd;
    
    const inputQtd = document.getElementById('recusarItemQtd');
    inputQtd.value = qtdDisponivel;
    inputQtd.max = qtdDisponivel;
    
    const modal = new bootstrap.Modal(document.getElementById('modalRecusarItemAvaria'));
    modal.show();
}

async function confirmarRecusaItemAvaria() {
    const avariaId = document.getElementById('recusarItemAvariaId').value;
    const sequencia = document.getElementById('recusarItemSeq').value;
    const qtdStr = document.getElementById('recusarItemQtd').value;
    const qtd = parseInt(qtdStr, 10);
    const maxQtd = parseInt(document.getElementById('recusarItemQtdMaxTxt').innerText, 10);

    if (!qtd || isNaN(qtd) || qtd <= 0) {
        await _alertaCritico('Quantidade inválida', 'Informe uma quantidade válida e inteira para recusar.');
        return;
    }

    if (qtd > maxQtd) {
        await _alertaCritico('Quantidade inválida', 'A quantidade não pode ser maior que o disponível (' + maxQtd + ').');
        return;
    }

    const confirmouRecusa = await _confirmarAcaoCritica(
        'Confirmar recusa',
        'Tem certeza que deseja recusar ' + qtd + ' und deste item? Esta ação irá retirá-lo da conferência ativa.',
        'Recusar', 'Cancelar'
    );
    if (!confirmouRecusa) {
        return;
    }

    const btn = document.querySelector('#modalRecusarItemAvaria .btn-danger');
    const originalText = btn.innerHTML;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin me-1"></i>Processando...';
    btn.disabled = true;

    try {
        const response = await fetch('/wms/avaria/api/recusar-item', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                avaria_id: avariaId,
                sequencia: sequencia,
                qtd_recusada: qtd
            })
        });
        const data = await response.json();

        if (data.success) {
            // Atualiza linha
            if (data.rows_html) {
                const tbody = document.getElementById('avariasTbody');
                if (tbody) {
                    const existingRows = Array.from(tbody.querySelectorAll(`tr[data-avaria-id="${avariaId}"]`));
                    if (existingRows.length > 0) {
                        const firstRow = existingRows[0];
                        existingRows.forEach(r => r.remove());
                        firstRow.insertAdjacentHTML('beforebegin', data.rows_html);

                        // Opcional: Reaplicar filtros
                        if (typeof applyComplexFiltersPending === 'function') {
                            applyComplexFiltersPending();
                        }
                    } else {
                        location.reload();
                    }
                } else {
                    location.reload();
                }
            } else {
                location.reload();
            }

            const modalEl = document.getElementById('modalRecusarItemAvaria');
            const modal = bootstrap.Modal.getInstance(modalEl);
            if (modal) modal.hide();
        } else {
            await _alertaCritico('Erro', traduzirErroTecnico(data.message));
        }
    } catch (err) {
        console.error(err);
        await _alertaCritico('Erro', 'Erro ao processar requisição.');
    } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
}
