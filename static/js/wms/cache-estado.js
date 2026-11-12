/**
 * CACHE ESTADO JS — Persistência de filtros e estado via sessionStorage
 * 
 * Preserva filtros, busca, colunas visíveis e scroll ao recarregar a página.
 * Usa sessionStorage (limpa ao fechar aba) com TTL configurável.
 * 
 * DEPENDÊNCIAS: Nenhuma (standalone)
 * 
 * USO no template:
 *   <script src="{{ url_for('static', filename='js/wms/cache-estado.js') }}"></script>
 *   <script>
 *     // Na inicialização da página:
 *     CacheEstado.restaurar('registrar'); // ou 'consultar'
 *     
 *     // Antes de qualquer ação que recarrega:
 *     CacheEstado.salvarEstadoAtual('registrar');
 *   </script>
 */

const CacheEstado = (function() {
    'use strict';

    // CONFIGURAÇÃO
    
    const CONFIG = {
        PREFIX: 'wms_cache_',
        TTL_MS: 30 * 60 * 1000,  // 30 minutos
        VERSION: '1.0'
    };

    const KEYS = {
        // Registrar Avarias
        REGISTRAR_FILTROS: CONFIG.PREFIX + 'registrar_filtros',
        REGISTRAR_BUSCA: CONFIG.PREFIX + 'registrar_busca',
        REGISTRAR_SCROLL: CONFIG.PREFIX + 'registrar_scroll',
        REGISTRAR_NOTA: CONFIG.PREFIX + 'registrar_nota',
        REGISTRAR_COLUNAS: CONFIG.PREFIX + 'registrar_colunas',      // NOVO
        REGISTRAR_ORDENACAO: CONFIG.PREFIX + 'registrar_ordenacao',  // NOVO
        
        // Consultar Avarias
        CONSULTAR_FILTROS: CONFIG.PREFIX + 'consultar_filtros',
        CONSULTAR_BUSCA: CONFIG.PREFIX + 'consultar_busca',
        CONSULTAR_COLUNAS: CONFIG.PREFIX + 'consultar_colunas',
        CONSULTAR_ORDENACAO: CONFIG.PREFIX + 'consultar_ordenacao',
        CONSULTAR_SCROLL: CONFIG.PREFIX + 'consultar_scroll',
        
        // Flag de restauração pendente
        RESTAURAR_PENDENTE: CONFIG.PREFIX + 'restaurar_pendente'
    };

    // STORAGE CORE
    
    let _storageAvailable = null;
    function isStorageAvailable() {
        if (_storageAvailable !== null) return _storageAvailable;
        try {
            const testKey = '__wms_test__';
            sessionStorage.setItem(testKey, '1');
            sessionStorage.removeItem(testKey);
            _storageAvailable = true;
        } catch (e) {
            _storageAvailable = false;
        }
        return _storageAvailable;
    }

    /**
     * Salva dados no sessionStorage com timestamp e TTL
     * @param {string} chave - Chave de armazenamento
     * @param {*} dados - Dados a persistir (serão serializados como JSON)
     * @param {number} [ttl] - TTL customizado em ms (opcional)
     */
    function salvar(chave, dados, ttl) {
        if (!isStorageAvailable()) return;
        try {
            const payload = {
                dados: dados,
                timestamp: Date.now(),
                ttl: ttl || CONFIG.TTL_MS,
                version: CONFIG.VERSION
            };
            sessionStorage.setItem(chave, JSON.stringify(payload));
        } catch (e) {
            console.warn('[CacheEstado] Erro ao salvar:', chave, e);
        }
    }

    /**
     * Recupera dados do sessionStorage com verificação de TTL
     * @param {string} chave - Chave de armazenamento
     * @returns {*|null} Dados armazenados ou null se expirado/inexistente
     */
    function recuperar(chave) {
        if (!isStorageAvailable()) return null;
        try {
            const raw = sessionStorage.getItem(chave);
            if (!raw) return null;

            const payload = JSON.parse(raw);
            
            // Verificar versão
            if (payload.version !== CONFIG.VERSION) {
                sessionStorage.removeItem(chave);
                return null;
            }
            
            // Verificar TTL
            const expirado = (Date.now() - payload.timestamp) > payload.ttl;
            if (expirado) {
                sessionStorage.removeItem(chave);
                return null;
            }

            return payload.dados;
        } catch (e) {
            console.warn('[CacheEstado] Erro ao recuperar:', chave, e);
            return null;
        }
    }

    /**
     * Remove chave do cache
     */
    function limpar(chave) {
        if (!isStorageAvailable()) return;
        try {
            sessionStorage.removeItem(chave);
        } catch (e) { /* ignore */ }
    }

    /**
     * Limpa todo cache WMS
     */
    function limparTudo() {
        if (!isStorageAvailable()) return;
        try {
            Object.values(KEYS).forEach(k => sessionStorage.removeItem(k));
            // Limpar qualquer chave com prefixo WMS
            Object.keys(sessionStorage).forEach(k => {
                if (k.startsWith(CONFIG.PREFIX)) sessionStorage.removeItem(k);
            });
        } catch (e) { /* ignore */ }
    }

    // SALVAR ESTADO COMPLETO DA TELA

    /**
     * Captura e salva o estado atual da tela antes de um reload
     * @param {string} tela - 'registrar' | 'consultar'
     */
    function _salvarEstadoRegistrar() {
        // Busca global
        const buscaGlobal = document.getElementById('globalSearchAvarias');
        if (buscaGlobal) {
            salvar(KEYS.REGISTRAR_BUSCA, buscaGlobal.value);
        }
        
        // Filtros por coluna + colunas selecionadas para busca global
        const filtros = {};
        document.querySelectorAll('.column-filter-pending').forEach(input => {
            if (input.value) {
                const colIndex = input.dataset.column || input.closest('th')?.dataset?.column;
                if (colIndex) filtros[colIndex] = input.value;
            }
        });
    
        // NOVO: Persistir array de colunas ativas na busca global
        if (typeof selectedGlobalFilterColumns !== 'undefined') {
            filtros._selectedGlobalFilterColumns = [...selectedGlobalFilterColumns];
        }
        // NOVO: Persistir estado dos checkboxes de coluna
        if (typeof columnFilterValuesPending !== 'undefined') {
            filtros._columnFilterValuesPending = Object.assign({}, columnFilterValuesPending);
        }
        salvar(KEYS.REGISTRAR_FILTROS, filtros);
        
        // NOVO: Ordenação ativa
        const sortedTh = document.querySelector('#tabelaAvariasPendentes th.sorted');
        if (sortedTh) {
            const colIdx = sortedTh.dataset.column;
            const sortIcon = sortedTh.querySelector('.sort-icon');
            const direction = sortIcon?.classList.contains('fa-sort-up') ? 'asc' : 'desc';
            salvar(KEYS.REGISTRAR_ORDENACAO, { coluna: colIdx, direcao: direction });
        }
    
        // Posição do scroll
        const tableContainer = document.querySelector('.table-container');
        if (tableContainer) {
            salvar(KEYS.REGISTRAR_SCROLL, {
                x: tableContainer.scrollLeft,
                y: tableContainer.scrollTop
            });
        }
        
        // Última nota pesquisada
        const codEmpresa = document.getElementById('cod_empresa');
        const nota = document.getElementById('nota');
        if (codEmpresa && nota && (codEmpresa.value || nota.value)) {
            salvar(KEYS.REGISTRAR_NOTA, {
                cod_empresa: codEmpresa.value,
                nota: nota.value
            });
        }
    
        if (typeof WMSLog !== 'undefined') {
            WMSLog.info('💾 Estado do Registrar salvo no cache');
        }
    }

    function _salvarEstadoConsultar() {
        // Busca global
        const busca = document.getElementById('globalSearch') || document.getElementById('searchInput');
        if (busca) {
            salvar(KEYS.CONSULTAR_BUSCA, busca.value);
        }
        
        // Filtros por coluna (dropdowns/inputs)
        const filtros = {};
        document.querySelectorAll('.column-filter-individual').forEach(input => {
            if (input.value) {
                const colIndex = input.dataset.column || input.getAttribute('data-column');
                if (colIndex !== null && colIndex !== undefined) filtros[colIndex] = input.value;
            }
        });
        
        // Também capturar filtros globais por coluna (selectedGlobalFilterPhysicalIndices)
        if (typeof selectedGlobalFilterPhysicalIndices !== 'undefined') {
            filtros._selectedPhysical = selectedGlobalFilterPhysicalIndices;
        }
        if (typeof columnFilterValues !== 'undefined') {
            filtros._columnFilterValues = columnFilterValues;
        }
        
        salvar(KEYS.CONSULTAR_FILTROS, filtros);
        
        // Colunas visíveis
        const colunasVisiveis = [];
        document.querySelectorAll('.column-toggle-check:checked').forEach(cb => {
            colunasVisiveis.push(parseInt(cb.value));
        });
        if (colunasVisiveis.length > 0) {
            salvar(KEYS.CONSULTAR_COLUNAS, colunasVisiveis);
        }
        
        // Ordenação ativa
        const sortedTh = document.querySelector('th.sorted');
        if (sortedTh) {
            const colIdx = sortedTh.dataset.column;
            const sortIcon = sortedTh.querySelector('.sort-icon');
            const direction = sortIcon?.classList.contains('fa-sort-up') ? 'asc' : 'desc';
            salvar(KEYS.CONSULTAR_ORDENACAO, { coluna: colIdx, direcao: direction });
        }
        
        // Posição do scroll
        const tableContainer = document.querySelector('.table-container');
        if (tableContainer) {
            salvar(KEYS.CONSULTAR_SCROLL, {
                x: tableContainer.scrollLeft,
                y: tableContainer.scrollTop
            });
        }

        if (typeof WMSLog !== 'undefined') {
            WMSLog.info('💾 Estado do Consultar salvo no cache');
        }
    }

    /**
     * Ponto de entrada público para salvar o estado da tela
     * @param {string} tela - 'registrar' | 'consultar'
     */
    function salvarEstadoAtual(tela) {
        if (tela === 'registrar') {
            _salvarEstadoRegistrar();
        } else if (tela === 'consultar') {
            _salvarEstadoConsultar();
        }
    }

    // RESTAURAR ESTADO

    /**
     * Restaura o estado da tela a partir do cache.
     * Deve ser chamado no DOMContentLoaded APÓS a tabela estar renderizada.
     * 
     * @param {string} tela - 'registrar' | 'consultar'
     * @returns {boolean} true se havia estado para restaurar
     */
    function restaurar(tela) {
        const pendente = recuperar(KEYS.RESTAURAR_PENDENTE);
        
        // Só restaura se a flag pendente corresponde à tela atual
        // OU se há dados de cache mesmo sem flag (ex: F5 manual)
        if (tela === 'registrar') {
            return _restaurarEstadoRegistrar();
        } else if (tela === 'consultar') {
            return _restaurarEstadoConsultar();
        }
        
        return false;
    }

    function _restaurarEstadoRegistrar() {
        let restaurou = false;
        
        // Busca global
        const busca = recuperar(KEYS.REGISTRAR_BUSCA);
        if (busca) {
            const buscaEl = document.getElementById('globalSearchAvarias');
            if (buscaEl) {
                buscaEl.value = busca;
                buscaEl.dispatchEvent(new Event('input', { bubbles: true }));
                restaurou = true;
            }
        }
        
        // Filtros por coluna + colunas selecionadas para busca global
        const filtros = recuperar(KEYS.REGISTRAR_FILTROS);
        if (filtros && Object.keys(filtros).length > 0) {
            // Filtros individuais de coluna
            Object.entries(filtros).forEach(([colIndex, valor]) => {
                if (colIndex.startsWith('_')) return; // pular metadados
                const input = document.querySelector(`.column-filter-pending[data-column="${colIndex}"]`);
                if (input) {
                    input.value = valor;
                    if (typeof columnFilterValuesPending !== 'undefined') {
                        columnFilterValuesPending[parseInt(colIndex)] = valor;
                    }
                }
            });
    
            // NOVO: Restaurar colunas selecionadas para busca global
            if (filtros._selectedGlobalFilterColumns && typeof selectedGlobalFilterColumns !== 'undefined') {
                selectedGlobalFilterColumns.length = 0;
                filtros._selectedGlobalFilterColumns.forEach(v => selectedGlobalFilterColumns.push(v));
                // Atualizar checkboxes DOM
                document.querySelectorAll('#columnsCheckboxListPending input[type="checkbox"]').forEach(cb => {
                    cb.checked = selectedGlobalFilterColumns.includes(parseInt(cb.value));
                });
                // Atualizar texto do seletor
                if (typeof updateSelectedColumnsTextPending === 'function') {
                    updateSelectedColumnsTextPending();
                }
            }
    
            // NOVO: Restaurar estado do columnFilterValuesPending
            if (filtros._columnFilterValuesPending && typeof columnFilterValuesPending !== 'undefined') {
                Object.assign(columnFilterValuesPending, filtros._columnFilterValuesPending);
            }
    
            restaurou = true;
        }
    
        // NOVO: Restaurar ordenação
        const ordenacao = recuperar(KEYS.REGISTRAR_ORDENACAO);
        if (ordenacao && ordenacao.coluna) {
            if (typeof sortTablePending === 'function') {
                setTimeout(() => sortTablePending(parseInt(ordenacao.coluna)), 100);
            }
            restaurou = true;
        }
    
        // Scroll (com delay)
        const scroll = recuperar(KEYS.REGISTRAR_SCROLL);
        if (scroll) {
            setTimeout(() => {
                const container = document.querySelector('.table-container');
                if (container) {
                    container.scrollLeft = scroll.x || 0;
                    container.scrollTop = scroll.y || 0;
                }
            }, 200);
            restaurou = true;
        }
        
        // Limpar flag pendente
        limpar(KEYS.RESTAURAR_PENDENTE);
    
        if (restaurou && typeof WMSLog !== 'undefined') {
            WMSLog.info('🔄 Estado do Registrar restaurado do cache');
        }
        
        return restaurou;
    }

    function _restaurarEstadoConsultar() {
        let restaurou = false;
        
        // Busca global
        const busca = recuperar(KEYS.CONSULTAR_BUSCA);
        if (busca) {
            const buscaEl = document.getElementById('globalSearch') || document.getElementById('searchInput');
            if (buscaEl) {
                buscaEl.value = busca;
                buscaEl.dispatchEvent(new Event('input', { bubbles: true }));
                restaurou = true;
            }
        }
        
        // Filtros por coluna
        const filtros = recuperar(KEYS.CONSULTAR_FILTROS);
        if (filtros) {
            // Restaurar filtros de coluna
            if (filtros._columnFilterValues && typeof columnFilterValues !== 'undefined') {
                Object.assign(columnFilterValues, filtros._columnFilterValues);
                // Popular os inputs DOM visíveis gerados por generateFilterRow()
                Object.entries(filtros._columnFilterValues).forEach(([colIndex, valor]) => {
                    if (!valor) return;
                    const input = document.querySelector(`.column-filter-individual[data-column="${colIndex}"]`);
                    if (input) input.value = valor;
                });
            }
            if (filtros._selectedPhysical && typeof selectedGlobalFilterPhysicalIndices !== 'undefined') {
                selectedGlobalFilterPhysicalIndices.length = 0;
                filtros._selectedPhysical.forEach(v => selectedGlobalFilterPhysicalIndices.push(v));
            }
            restaurou = true;
        }
        // Nota: applyFilters() é chamado pelo consultar-avarias.js APÓS esta função retornar
        
        // Colunas visíveis
        const colunas = recuperar(KEYS.CONSULTAR_COLUNAS);
        if (colunas && colunas.length > 0) {
            // Desmarcar todas primeiro
            document.querySelectorAll('.column-toggle-check').forEach(cb => {
                cb.checked = colunas.includes(parseInt(cb.value));
            });
            // Aplicar visibilidade
            if (typeof applyColumnVisibility === 'function') {
                applyColumnVisibility();
            }
            restaurou = true;
        }
        
        // Ordenação
        const ordenacao = recuperar(KEYS.CONSULTAR_ORDENACAO);
        if (ordenacao && ordenacao.coluna) {
            // Aplicar ordenação programaticamente
            if (typeof sortTable === 'function') {
                setTimeout(() => sortTable(parseInt(ordenacao.coluna)), 100);
            }
            restaurou = true;
        }
        
        // Scroll (com delay)
        const scroll = recuperar(KEYS.CONSULTAR_SCROLL);
        if (scroll) {
            setTimeout(() => {
                const container = document.querySelector('.table-container');
                if (container) {
                    container.scrollLeft = scroll.x || 0;
                    container.scrollTop = scroll.y || 0;
                }
            }, 200);
            restaurou = true;
        }
        
        // Limpar flag pendente
        limpar(KEYS.RESTAURAR_PENDENTE);
        
        if (restaurou && typeof WMSLog !== 'undefined') {
            WMSLog.info('🔄 Estado do Consultar restaurado do cache');
        }
        
        return restaurou;
    }

    // HOOKS PARA INTERCEPTAR RELOADS

    /**
     * Registra interceptadores para salvar estado automaticamente antes de
     * ações que recarregam a página (forms, redirects, etc.)
     * 
     * @param {string} tela - 'registrar' | 'consultar'
     */
    function registrarAutoSave(tela) {
        // Interceptar submissão de formulários
        document.querySelectorAll('form').forEach(form => {
            form.addEventListener('submit', () => {
                salvarEstadoAtual(tela);
            });
        });

        // Interceptar beforeunload (F5, navegação)
        window.addEventListener('beforeunload', () => {
            salvarEstadoAtual(tela);
        });

        // Interceptar cliques em links que recarregam
        document.querySelectorAll('a[href]').forEach(link => {
            const href = link.getAttribute('href');
            // Só interceptar links internos WMS
            if (href && (href.includes('/wms/') || href === '#')) {
                link.addEventListener('click', () => {
                    salvarEstadoAtual(tela);
                });
            }
        });

        if (typeof WMSLog !== 'undefined') {
            WMSLog.info(`🔗 Auto-save registrado para tela: ${tela}`);
        }
    }

    // API PÚBLICA

    return {
        KEYS,
        CONFIG,
        
        // Core
        salvar,
        recuperar,
        limpar,
        limparTudo,
        
        // Alto nível
        salvarEstadoAtual,
        restaurar,
        registrarAutoSave
    };
})();

// Exportar globalmente
window.CacheEstado = CacheEstado;
window.WMS = window.WMS || {};
window.WMS.CacheEstado = CacheEstado;

if (typeof WMSLog !== 'undefined') {
    WMSLog.info('✅ cache-estado.js carregado');
}
