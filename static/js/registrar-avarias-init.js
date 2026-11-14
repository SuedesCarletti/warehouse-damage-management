// Continuação de registrar-avarias.js — dividido em arquivos menores por seção (INITIALIZATION).
// Ordem de carregamento importa: deve vir logo depois de registrar-avarias.js.

// INITIALIZATION

// DROPDOWN UI POSITIONING (Required for the new selector)
document.addEventListener('DOMContentLoaded', function() {
    const dropdownToggle = document.getElementById('columnsDropdownTogglePending');
    const dropdownMenu = document.getElementById('columnsDropdownMenuPending');
    let isPositioning = false;
    
    if (dropdownToggle && dropdownMenu) {
        // Move to Body to avoid layout thrashing
        document.body.appendChild(dropdownMenu);
        
        dropdownMenu.style.position = 'fixed';
        dropdownMenu.style.zIndex = '10000';
        dropdownMenu.style.display = 'none';

        function positionDropdown() {
            if (isPositioning) return;
            isPositioning = true;
            
            requestAnimationFrame(() => {
                try {
                    const rect = dropdownToggle.getBoundingClientRect();
                    const viewportHeight = window.innerHeight;
                    const viewportWidth = window.innerWidth;
                    
                    const wasHidden = dropdownMenu.style.display === 'none';
                    if(wasHidden) {
                        dropdownMenu.style.visibility = 'hidden';
                        dropdownMenu.style.display = 'block';
                    }
                    const menuRect = dropdownMenu.getBoundingClientRect();
                    const menuHeight = menuRect.height || 400; 
                    const menuWidth = Math.max(300, rect.width); 
                    if(wasHidden) {
                        dropdownMenu.style.display = 'none';
                        dropdownMenu.style.visibility = 'visible';
                    }

                    let top = rect.bottom + 2;
                    let left = rect.left;

                    if (top + menuHeight > viewportHeight) {
                        const spaceAbove = rect.top;
                        if (spaceAbove > menuHeight) {
                            top = rect.top - menuHeight - 2;
                        } else {
                            top = viewportHeight - menuHeight - 10;
                        }
                    }
                    
                    if (left + menuWidth > viewportWidth) {
                        left = viewportWidth - menuWidth - 10;
                    }
                    
                    dropdownMenu.style.top = `${top}px`;
                    dropdownMenu.style.left = `${left}px`;
                    dropdownMenu.style.width = `${menuWidth}px`;

                } catch (err) {
                    console.error("Erro posicionamento dropdown:", err);
                } finally {
                    isPositioning = false;
                }
            });
        }
        
        dropdownToggle.addEventListener('click', function(e) {
            e.stopPropagation();
            e.preventDefault();
            const isVisible = dropdownMenu.style.display === 'block';
            
            if (!isVisible) {
                positionDropdown();
                dropdownMenu.style.display = 'block';
                dropdownMenu.classList.add('show');
                dropdownToggle.setAttribute('aria-expanded', 'true');
            } else {
                dropdownMenu.style.display = 'none';
                dropdownMenu.classList.remove('show');
                dropdownToggle.setAttribute('aria-expanded', 'false');
            }
        });

        document.addEventListener('click', function(e) {
            if (!dropdownMenu.contains(e.target) && e.target !== dropdownToggle && !dropdownToggle.contains(e.target)) {
                dropdownMenu.style.display = 'none';
                dropdownMenu.classList.remove('show');
                dropdownToggle.setAttribute('aria-expanded', 'false');
            }
        });
        
        window.addEventListener('resize', function() {
            if (dropdownMenu.style.display === 'block') positionDropdown();
        }, { passive: true });
        
        dropdownMenu.addEventListener('click', function(e) { e.stopPropagation(); });
    }
});


document.addEventListener('DOMContentLoaded', function() {
    // --- Initialize Pending Avarias Table ---
    const tablePending = document.getElementById('tabelaAvariasPendentes');
    if (tablePending) {
        
        // 1. Generate UI components
        generateFilterRowPending();
        generateColumnCheckboxesPending(); 

        // 2. Initialize Resize/Sort (existing logic kept)
        initializeColumnResizePending();

        // No evento de carregamento da página, adicionar:
        carregarMotivosDevolucao();

        // Carregar locais financeiros
        carregarLocaisFinanceiros();
        
        // 3. Add Listeners
        document.querySelectorAll('#tabelaAvariasPendentes th.sortable').forEach(th => {
            th.addEventListener('click', function(e) {
                if (e.target.classList.contains('column-resizer')) return;
                sortTablePending(parseInt(this.getAttribute('data-column')));
            });
        });
        
        const _gSearch = document.getElementById('globalSearchAvarias');
        if (_gSearch) {
            _gSearch.addEventListener('input', _debounce(applyComplexFiltersPending, 250));
        }
        // Status filters already have listeners in HTML (onchange calls applyComplexFiltersPending)
    }
    
    // Application of filters on load
    // 5. Restaurar estado do cache ANTES de aplicar filtros
    // Deve ser chamado APÓS generateFilterRowPending() para que os inputs DOM existam
    if (typeof CacheEstado !== 'undefined') {
        CacheEstado.restaurar('registrar');
        CacheEstado.registrarAutoSave('registrar');
    }

    // 6. Pré-construir índice de grupos antes de aplicar filtros (zero DOM no loop)
    _buildPendingIndex();
    applyComplexFiltersPending();

    (function aplicarFiltroAlertaRegistrar() {
        const params = new URLSearchParams(window.location.search);
        const alertaStatus      = params.get('alerta_status');        // fallback legado
        const alertaNome        = params.get('alerta_nome');
        const alertaStatusParar = params.get('alerta_status_parar');  // novo (server-side)
    
        if (!alertaNome && !alertaStatus && !alertaStatusParar) return;
    
        // Filtro client-side só se NÃO houver filtro server-side
        if (alertaStatus && !alertaStatusParar) {
            const input = document.getElementById('globalSearchAvarias');
            if (input) {
                input.value = alertaStatus;
                applyComplexFiltersPending();
            }
        }
    
        // Limpar TODOS os params de alerta da URL
        params.delete('alerta_status');
        params.delete('alerta_nome');
        params.delete('alerta_status_parar');
        params.delete('alerta_campo_parar');
        params.delete('alerta_logica_parada');
        history.replaceState(null, '',
            params.toString()
                ? window.location.pathname + '?' + params.toString()
                : window.location.pathname
        );
    
        if (alertaNome) {
            const cardBody = document.querySelector('.card.mb-3 .card-body');
            if (cardBody) {
                const b = document.createElement('div');
                b.className = 'alert alert-warning alert-dismissible d-flex align-items-center gap-2 py-2 mb-0 mt-2';
                b.innerHTML = '<i class="fas fa-bell text-warning"></i>' +
                    '<div class="flex-grow-1"><strong>Alerta:</strong> ' + alertaNome +
                    ' &nbsp;—&nbsp; exibindo avarias que estão disparando este alerta' +
                    ' &nbsp;—&nbsp; <a href="' + window.location.pathname + '" class="alert-link">Limpar filtro</a></div>' +
                    '<button type="button" class="btn-close" data-bs-dismiss="alert"></button>';
                cardBody.appendChild(b);
            }
        }
    })();
    
    // ── Redireciona com filtros de data na URL (idêntico ao consultar_avarias) ──
    async function aplicarFiltroDatasRegistrar() {
        const inicio = document.getElementById('filtroDataInicioPending')?.value || '';
        const fim    = document.getElementById('filtroDataFimPending')?.value    || '';
        const campo  = document.getElementById('filtroCampoDataPending')?.value  || 'data_registro';

        if (!inicio && !fim) {
            await _alertaCritico('Filtro incompleto', 'Informe ao menos uma data para filtrar.');
            return;
        }
    
        const params = new URLSearchParams(window.location.search);
        if (inicio) params.set('data_inicio', inicio); else params.delete('data_inicio');
        if (fim)    params.set('data_fim', fim);        else params.delete('data_fim');
        params.set('campo_data_filtro', campo);
        // Limpa busca textual ao aplicar filtro de data
        params.delete('search');
    
        window.location.href = `${window.location.pathname}?${params.toString()}`;
    }
    
    function clearSearchPending() {
        const params = new URLSearchParams(window.location.search);
        const temFiltroData = params.has('data_inicio') || params.has('data_fim');
    
        if (temFiltroData) {
            window.location.href = window.location.pathname;
            return;
        }
    
        // Limpa busca global
        const global = document.getElementById('globalSearchAvarias');
        if (global) global.value = '';
    
        // Limpa filtros de coluna (inputs)
        document.querySelectorAll('.column-filter-pending').forEach(i => i.value = '');
        
        // Reseta o estado dos filtros de coluna
        columnFilterValuesPending = {};
    
        // Reaplicar (exibe tudo)
        applyComplexFiltersPending();
    }

    window.aplicarFiltroDatasRegistrar = aplicarFiltroDatasRegistrar;
    window.clearSearchPending = clearSearchPending;

    // ── Banner informativo quando filtro de data está ativo (idêntico ao consultar) ──
    (function mostrarBannerFiltroDataPending() {
        const params = new URLSearchParams(window.location.search);
        const inicio = params.get('data_inicio') || '';
        const fim    = params.get('data_fim')    || '';
        const campo  = params.get('campo_data_filtro') || '';
        if (!inicio && !fim) return;
    
        // Aplica o filtro client-side com os valores da URL ao carregar a página
        document.addEventListener('DOMContentLoaded', function() {
            // Popular inputs com valores da URL
            const elInicio = document.getElementById('filtroDataInicioPending');
            const elFim    = document.getElementById('filtroDataFimPending');
            const elCampo  = document.getElementById('filtroCampoDataPending');
            if (elInicio) elInicio.value = inicio;
            if (elFim)    elFim.value    = fim;
            if (elCampo && campo) elCampo.value = campo;
    
            // Banner
            const labelCampo = {
                data_registro:      'Data de Registro',
                prazo_pagamento:    'Prazo Pagamento',
                data_pagamento:     'Data Pagamento',
                data_entrega:       'Data Entrega',
                data_protocolo_mkt: 'Data Protocolo MKT',
                data_coleta:        'Data Coleta',
                data_finalizacao:   'Data Finalização'
            }[campo] || campo;
    
            const cardBody = document.querySelector('.card.mb-3 .card-body');
            if (cardBody) {
                const banner = document.createElement('div');
                banner.className = 'alert alert-success alert-dismissible d-flex align-items-center gap-2 py-2 mb-0 mt-2';
                banner.innerHTML = `
                    <i class="fas fa-filter text-success"></i>
                    <div class="flex-grow-1">
                        <strong>Filtro de data ativo:</strong>
                        <span class="badge bg-success ms-1">${labelCampo}</span>
                        ${inicio ? `de <strong>${inicio}</strong>` : ''}
                        ${fim    ? ` até <strong>${fim}</strong>`  : ''}
                        &nbsp;—&nbsp;
                        <a href="{{ url_for('wms.registrar_avarias') }}" class="alert-link">Limpar</a>
                    </div>
                    <button type="button" class="btn-close" data-bs-dismiss="alert"></button>`;
                cardBody.appendChild(banner);
            }
        });
    })();    
    
    // --- Initialization of the Registration Form ---
    console.log('[AVARIAS] Inicializando validação de nota...');
    
    const codEmpresaInput = document.getElementById('cod_empresa');
    const notaInput = document.getElementById('nota');
    const feedbackDiv = document.getElementById('feedbackNota');
    
    if (!codEmpresaInput || !notaInput) return;
    
    let debounceTimer = null;
    const DEBOUNCE_DELAY = 1000; // 1 second delay
    
    function validarNota() {
        if (debounceTimer) {
             clearTimeout(debounceTimer);
             debounceTimer = null;
        }

        const codemp = codEmpresaInput.value.trim();
        const numnota = notaInput.value.trim();
        
        if (!codemp || !numnota) {
            if (feedbackDiv) feedbackDiv.innerHTML = '';
            limparCamposAutomaticos();
            esconderItens();
            return;
        }
        
        const lastConsulted = notaInput.getAttribute('data-last-consulted');
        if (lastConsulted === `${codemp}_${numnota}`) {
             console.log('⏱[AVARIAS] Nota e empresa não mudaram. Pulando consulta.');
             return;
        }

        consultarSankhya(codemp, numnota);
    }
    
    function validarNotaDebounced() {
        if (debounceTimer) clearTimeout(debounceTimer);
        debounceTimer = setTimeout(validarNota, DEBOUNCE_DELAY);
    }
    
    async function consultarSankhya(codemp, numnota) {
        console.log('[AVARIAS] Etapa 1 — Verificando notas para NUMNOTA...');

        notaInput.setAttribute('data-last-consulted', `${codemp}_${numnota}`);

        if (feedbackDiv) {
            feedbackDiv.innerHTML = `
                <div class="d-flex align-items-center text-primary">
                    <div class="spinner-border spinner-border-sm me-2"></div>
                    <span>Consultando Sankhya...</span>
                </div>
            `;
        }

        try {
            // ── ETAPA 1: Verificar quantas notas existem (chamada leve) ──
            const multiResponse = await fetch('/wms/api/validar-multiplas-notas', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ numnota, codemp })
            });

            if (!multiResponse.ok) {
                throw new Error(`HTTP ${multiResponse.status}`);
            }

            const multiData = await multiResponse.json();

            // Nota não encontrada
            if (!multiData.success || multiData.count === 0) {
                console.warn('[AVARIAS]', multiData.message || 'Nota não encontrada');
                mostrarFeedbackErro(multiData.message || 'Nota não encontrada no Sankhya.');
                limparCamposAutomaticos();
                esconderItens();
                return;
            }

            // ── ETAPA 2: Decisão por quantidade de resultados ──
            if (multiData.count > 1) {
                // MÚLTIPLAS NOTAS → modal de seleção
                console.log(`[AVARIAS] ${multiData.count} nota(s) encontrada(s). Abrindo modal...`);
                mostrarModalSelecionarNota(multiData.notas, codemp, numnota);
                return; // Aguardar seleção do usuário
            }

            // UMA ÚNICA NOTA → prosseguir diretamente
            const notaSelecionada = multiData.notas[0];
            console.log(`[AVARIAS] Nota única. NUNOTA=${notaSelecionada.nunota}, TIPMOV=${notaSelecionada.tipmov}`);

            // Armazenar NUNOTA no campo oculto
            document.getElementById('nunota_selecionado').value = notaSelecionada.nunota;

            // ── ETAPA 3: Buscar dados completos (cabecalho mapeado + itens) ──
            await buscarDadosCompletosNota(codemp, numnota, notaSelecionada.nunota);

        } catch (error) {
            console.error('[AVARIAS] Erro na consulta:', error);
            mostrarFeedbackErro(`Erro de comunicação: ${error.message}`);
            limparCamposAutomaticos();
            esconderItens();
        }
    }

// ─── ADIÇÃO: Funções auxiliares (inserir logo após consultarSankhya) ──────

    /**
     * Busca dados completos (cabecalho mapeado + itens) para uma nota específica.
     * Chamado após NUNOTA ser definido (modal ou nota única).
     */
    async function buscarDadosCompletosNota(codemp, numnota, nunota) {
        console.log(`[AVARIAS] Etapa 3 — Buscando dados completos (NUNOTA=${nunota})...`);

        if (feedbackDiv) {
            feedbackDiv.innerHTML = `
                <div class="d-flex align-items-center text-primary">
                    <div class="spinner-border spinner-border-sm me-2"></div>
                    <span>Carregando dados da nota...</span>
                </div>
            `;
        }

        try {
            const response = await fetch('/wms/sankhya_avarias/api/validar-nota', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ numnota, codemp, nunota })  // envia nunota
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const data = await response.json();

            if (data.success && data.existe) {
                console.log('[AVARIAS] Dados completos carregados!');

                const cabecalho = data.dados || {};
                const itens = cabecalho.itens || [];

                preencherCamposAutomaticos(cabecalho);
                mostrarFeedbackSucesso(cabecalho);

                if (itens.length > 0) {
                    exibirItensNota(itens);
                }
            } else {
                const errorMsg = data.error || 'Erro ao carregar dados da nota';
                console.warn('[AVARIAS]', errorMsg);
                mostrarFeedbackErro(errorMsg);
                limparCamposAutomaticos();
                esconderItens();
            }

        } catch (error) {
            console.error('[AVARIAS] Erro ao buscar dados completos:', error);
            mostrarFeedbackErro(`Erro ao carregar nota: ${error.message}`);
        }
    }

    /**
     * Monta e exibe o modal de seleção de nota.
     */
    function mostrarModalSelecionarNota(notas, codemp, numnota) {
        const container = document.getElementById('tabelaSelecionarNota');

        let html = `
            <table class="table table-hover table-sm border">
                <thead class="table-light">
                    <tr>
                        <th style="width:6%">#</th>
                        <th style="width:14%">Nº Nota Fiscal</th>
                        <th style="width:14%">Tipo</th>
                        <th style="width:14%">Data</th>
                        <th style="width:18%">Valor</th>
                        <th style="width:22%">Transportadora</th>
                        <th style="width:12%">Ação</th>
                    </tr>
                </thead>
                <tbody>
        `;

        notas.forEach((nota, index) => {
            const tipoClass = nota.tipmov === 'V'
                ? 'badge bg-success'
                : nota.tipmov === 'D'
                    ? 'badge bg-danger'
                    : 'badge bg-secondary';

            let valorFormatado = 'N/A';
            if (nota.vlrnota) {
                try {
                    valorFormatado = 'R$ ' + parseFloat(nota.vlrnota)
                        .toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
                } catch (e) {
                    valorFormatado = nota.vlrnota;
                }
            }

            html += `
                <tr>
                    <td class="text-center text-muted">${index + 1}</td>
                    <td><strong>${nota.numnota || 'N/A'}</strong></td>
                    <td><span class="${tipoClass}">${nota.descricao_tipo || nota.tipmov || 'N/A'}</span></td>
                    <td>${nota.data_exibicao || 'N/A'}</td>
                    <td>${valorFormatado}</td>
                    <td>${nota.transportadora || 'N/A'}</td>
                    <td class="text-center">
                        <button type="button" class="btn btn-primary btn-sm"
                                onclick="window.__selecionarNotaModal('${nota.nunota}', '${codemp}', '${numnota}')">
                            <i class="fas fa-check me-1"></i>Selecionar
                        </button>
                    </td>
                </tr>
            `;
        });

        html += '</tbody></table>';
        container.innerHTML = html;

        // Abrir modal Bootstrap
        const modal = new bootstrap.Modal(document.getElementById('modalSelecionarNota'));
        modal.show();
    }

    /**
     * Callback global — executado quando o usuário seleciona uma nota no modal.
     * Atribuído a window porque é chamado por onclick em HTML dinâmico.
     */
    window.__selecionarNotaModal = async function(nunota, codemp, numnota) {
        console.log(`[AVARIAS] Nota selecionada no modal: NUNOTA=${nunota}`);

        // Fechar modal
        const modal = bootstrap.Modal.getInstance(document.getElementById('modalSelecionarNota'));
        if (modal) modal.hide();

        // Armazenar NUNOTA no campo oculto
        document.getElementById('nunota_selecionado').value = nunota;

        // Buscar dados completos da nota selecionada
        await buscarDadosCompletosNota(codemp, numnota, nunota);
    };

    // ── Helpers de feedback (extraídos para reutilização) ──

    function mostrarFeedbackSucesso(cabecalho) {
        if (feedbackDiv) {
            const codparcInfo = cabecalho.codparc ? ` · CODPARC: <strong>${cabecalho.codparc}</strong>` : '';
            feedbackDiv.innerHTML = `
                <div class="alert alert-success mb-0">
                    <i class="fas fa-check-circle me-2"></i>
                    <strong>Nota encontrada!</strong> Dados preenchidos automaticamente. Parceiro: ${cabecalho.parceiro || 'N/A'}${codparcInfo}
                </div>
            `;
        }
    }

    function mostrarFeedbackErro(mensagem) {
        if (feedbackDiv) {
            feedbackDiv.innerHTML = `
                <div class="alert alert-danger mb-0">
                    <i class="fas fa-exclamation-triangle me-2"></i>
                    ${mensagem}
                </div>
            `;
        }
    }
    
    function preencherCamposAutomaticos(cabecalho) {
        console.log('[AVARIAS] Preenchendo campos:', cabecalho);
        
        // 1. NF/NFD Logic
        const nfVenda = cabecalho.nf_venda || '';
        const nfdNumber = cabecalho.nfd || '';
        
        if (nfVenda) {
            document.getElementById('nota').value = nfVenda;
        } 
        if (nfdNumber) {
            document.getElementById('nfd').value = nfdNumber;
        }
        
        // 2. Fill other automatic fields
        const campos = [
            { id: 'quantidade', valor: cabecalho.quantidade },
            { id: 'volume', valor: cabecalho.volume },
            { id: 'transportadora', valor: cabecalho.transportadora }, 
            { id: 'valor_nf', valor: cabecalho.vlrnota },
            { id: 'valor_nfd', valor: cabecalho.valor_nfd }
        ];
        
        campos.forEach(campo => {
            const elemento = document.getElementById(campo.id);
            if (elemento && campo.valor) {
                elemento.value = campo.valor;
            }
        });

        // Ponto 6: preencher CODPARC no campo hidden para ser salvo na avaria
        const codparcHidden = document.getElementById('codparc_hidden');
        if (codparcHidden) {
            codparcHidden.value = cabecalho.codparc || '';
        }
    }
    
    function limparCamposAutomaticos() {
        document.getElementById('nfd').value = '';
        document.getElementById('quantidade').value = '';
        document.getElementById('volume').value = '';
        document.getElementById('valor_nf').value = '';
        document.getElementById('valor_nfd').value = '';
        // Limpar NUNOTA quando campos são limpos (ex: usuário trocou a NF)
        const nunotaField = document.getElementById('nunota_selecionado');
        if (nunotaField) nunotaField.value = '';
        // Ponto 6: limpar CODPARC ao trocar NF
        const codparcHidden = document.getElementById('codparc_hidden');
        if (codparcHidden) codparcHidden.value = '';
    }

    
    // Add optimized listeners
    codEmpresaInput.addEventListener('blur', validarNota);
    codEmpresaInput.addEventListener('change', validarNota);
    
    notaInput.addEventListener('input', validarNotaDebounced);
    
    notaInput.addEventListener('blur', function() {
        if (debounceTimer) clearTimeout(debounceTimer);
        validarNota(); // Immediate validation on blur
    });

    notaInput.addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            if (debounceTimer) clearTimeout(debounceTimer);
            validarNota(); // Immediate validation on Enter
        }
    });
    
const form = document.querySelector('form[method="POST"]');

if (form) {
    // Flag para evitar duplo envio
    let isSubmitting = false;

    form.addEventListener('submit', async function(e) {
        // SEMPRE prevenir o submit padrão — usamos AJAX
        e.preventDefault();
        const itensSelecionados = coletarItensSelecionados();

        if (!itensSelecionados || itensSelecionados.length === 0) {
            e.preventDefault();
            await _alertaCritico('Nenhum item selecionado', 'Selecione pelo menos um item avariado!');
            return false;
        }

        if (!(await validarMotivoSelecionado())) {
            e.preventDefault();  // Garante que previne o submit em falha de validação
            return false;
        }

        if (!preencherCamposOcultosMontivo()) {
            e.preventDefault();  // idem
            await _alertaCritico('Erro', 'Erro ao processar motivo de devolução!');
            return false;
        }

        // PROTEÇÃO CONTRA DUPLO CLIQUE
        if (isSubmitting) {
            e.preventDefault();
            console.warn('Envio já em andamento. Ignorando clique duplicado.');
            return false;
        }

        // Marcar como enviando e desabilitar o botão
        isSubmitting = true;
        const btnSalvar = form.querySelector('button[type="submit"]');
        if (btnSalvar) {
            btnSalvar.disabled = true;
            btnSalvar.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i>Salvando...';
        }
        
        // CORREÇÃO CRÍTICA: Remover readonly dos campos antes do submit
        // para garantir que os valores sejam enviados no POST
        const camposReadonly = [
            'nota',
            'nfd', 
            'quantidade',
            'volume',
            'valor_nf',
            'valor_nfd'
        ];
        
        camposReadonly.forEach(campoId => {
            const campo = document.getElementById(campoId);
            if (campo && campo.hasAttribute('readonly')) {
                campo.removeAttribute('readonly');
                console.log(`Removido readonly de: ${campoId} (valor: ${campo.value})`);
            }
        });

        // Ponto 5: normalizar campos monetários (ex: "1568,6" → "1568.6")
        // Evita inversão de valor quando o ERP retorna números com vírgula decimal.
        const camposMonetarios = ['valor_nf', 'valor_nfd', 'valor_nfp'];
        camposMonetarios.forEach(id => {
            const el = document.getElementById(id);
            if (el && el.value) {
                let v = el.value.trim();
                // "1.568,60" → remove pontos de milhar, troca vírgula por ponto
                if (v.indexOf(',') !== -1) {
                    v = v.replace(/\./g, '').replace(',', '.');
                }
                el.value = v;
            }
        });
        
        console.log('Formulário sendo enviado com valores:');
        console.log('  nota:', document.getElementById('nota')?.value);
        console.log('  valor_nf:', document.getElementById('valor_nf')?.value);
        console.log('  nfd:', document.getElementById('nfd')?.value);
        console.log('  valor_nfd:', document.getElementById('valor_nfd')?.value);
        
        // Confirmar que itens foram coletados corretamente
        const itensHiddenInput = document.getElementById('itens_selecionados');
        if (itensHiddenInput) {
            console.log('  itens_selecionados:', itensHiddenInput.value);
        }

        // AJAX submit — envia como FormData, recebe JSON
        try {
            const formData = new FormData(form);
            const response = await fetch(form.action || '/wms/registrar', {
                method: 'POST',
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                body: formData
            });

            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const result = await response.json();

            if (result.success) {
                // Fechar modal imediatamente — feedback instantâneo para o usuário
                const modalEl = document.getElementById('modalRegistrarAvaria');
                const modalInstance = bootstrap.Modal.getInstance(modalEl);
                if (modalInstance) modalInstance.hide();

                showNotification('success', `${result.message || 'Avaria registrada com sucesso!'}`);
                
                // Injeção direta — sem reload, sem dependência de cache/worker
                if (result.rows_html && result.rows_html.trim()) {
                    const tbody = document.querySelector('#tabelaAvariasPendentes tbody');
                    if (tbody) {
                        tbody.insertAdjacentHTML('afterbegin', result.rows_html);
                        // Invalidar índice: novas linhas foram inseridas no DOM
                        _invalidatePendingIndex();
                        if (typeof calcularTotalizadoresRegistrar === 'function') calcularTotalizadoresRegistrar();
                        if (typeof applyComplexFiltersPending === 'function') applyComplexFiltersPending();
                        isSubmitting = false;
                        if (btnSalvar) {
                            btnSalvar.disabled = false;
                            btnSalvar.innerHTML = '<i class="fas fa-save me-2"></i>Salvar Avaria';
                        }
                        return;
                    }
                }
                // Fallback: rows_html não veio (itens ainda propagando no Sheets) — recarrega com delay
                _reloadComCache(2500);

            } else {
                showNotification('error', `❌ ${result.error || 'Erro ao registrar avaria'}`);
                isSubmitting = false;
                if (btnSalvar) {
                    btnSalvar.disabled = false;
                    btnSalvar.innerHTML = '<i class="fas fa-save me-2"></i>Salvar Avaria';
                }
            }
        } catch (error) {
            console.error('Erro AJAX ao registrar avaria:', error);
            showNotification('error', `❌ Erro de comunicação: ${error.message}`);
            isSubmitting = false;
            if (btnSalvar) {
                btnSalvar.disabled = false;
                btnSalvar.innerHTML = '<i class="fas fa-save me-2"></i>Salvar Avaria';
            }
        }
        
    });
    }
});

// Resetar flag e botão quando o modal for fechado
const modalAvaria = document.getElementById('modalRegistrarAvaria');
if (modalAvaria) {
    modalAvaria.addEventListener('hidden.bs.modal', function () {
        isSubmitting = false;
        const btnSalvar = form.querySelector('button[type="submit"]');
        if (btnSalvar) {
            btnSalvar.disabled = false;
            btnSalvar.innerHTML = '<i class="fas fa-save me-2"></i>Salvar Avaria';
        }
    });
}

// ALTERNATIVA: Criar campos hidden duplicados com os valores
const camposParaDuplicar = [
    { id: 'nota', name: 'nota_backup' },
    { id: 'valor_nf', name: 'valor_nf_backup' }
];

camposParaDuplicar.forEach(({ id, name }) => {
    const campo = document.getElementById(id);
    if (campo && campo.value) {
        // Criar campo hidden
        const hiddenInput = document.createElement('input');
        hiddenInput.type = 'hidden';
        hiddenInput.name = name;
        hiddenInput.value = campo.value;
        form.appendChild(hiddenInput);
        console.log(`Campo hidden criado: ${name} = ${campo.value}`);
    }
});
    
