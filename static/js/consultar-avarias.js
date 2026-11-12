/**
 * CONSULTAR AVARIAS - JavaScript
 * Extraído de: templates/wms/consultar_avarias.html (Fase 2)
 * 
 * DEPENDÊNCIA: Requer objeto WMS_CONFIG definido no template antes deste script.
 * Exemplo no template:
 *   <script>
 *     const WMS_CONFIG = {
 *       urls: {
 *         consultarAvarias: "{{ url_for('wms.consultar_avarias') }}",
 *         baixasMultiplas: "{{ url_for('wms.baixas_multiplas') }}"
 *       }
 *     };
 *   </script>
 */

// VARIÁVEIS GLOBAIS
let sortDirection = {};
let resizingColumn = null;
let startX = 0;
let startWidth = 0;
let columnIndex = -1;

// CORREÇÃO CRÍTICA APLICADA 
// PROBLEMA: O array COLUMN_DEFS tinha 17 elementos começando do índice 1,
// mas faltava a coluna "Ações" (índice 0) e incluía "CTE" que não existe no HTML.
// Isso causava desalinhamento nos filtros - apenas "Nota" funcionava.
//
// SOLUÇÃO: 
// 1. Adicionei a coluna "Ações" no índice 0 (não filtrável)
// 2. Removi a coluna "CTE" que não existia no HTML
// 3. Agora COLUMN_DEFS tem 18 elementos (0-17), alinhado com o HTML

// Column Definitions (Physical Index, Name, Filterable, Key)
// Total 17 columns (Index 0 to 16) - ALINHADO COM O HTML
const COLUMN_DEFS = [
    // Coluna 0: Checkbox (seleção múltipla) - NÃO FILTRÁVEL
    { physicalIndex: 0, name: 'Checkbox', filterable: false },
    
    // Coluna 1: Ações (botões) - NÃO FILTRÁVEL
    { physicalIndex: 1, name: 'Ações', filterable: false },
    
    // Coluna 2: Cód. Empresa (data-column="1" no HTML)
    { physicalIndex: 2, name: 'Cód. Empresa', filterable: true, key: 'cod_empresa' },
    
    // Coluna 3: Local (data-column="2") — oculta da visualização
    { physicalIndex: 3, name: 'Local', filterable: false, visible: false, key: 'local' },
    
    // Coluna 4: Status Indenização (data-column="3")
    { physicalIndex: 4, name: 'Status Indenização', filterable: true, key: 'status_indenizacao' },
    
    // Coluna 5: Nota (data-column="4")
    { physicalIndex: 5, name: 'Nota', filterable: true, key: 'nota' },
    
    // Coluna 6: NFD (data-column="5")
    { physicalIndex: 6, name: 'NFD', filterable: true, key: 'nfd' },
    
    // Coluna 7: Quantidade (data-column="6")
    { physicalIndex: 7, name: 'Quantidade', filterable: true, key: 'quantidade' },
    
    // Coluna 8: Valor NFD (data-column="7")
    { physicalIndex: 8, name: 'Valor NFD', filterable: true, key: 'valor_nfd' },
    
    // Coluna 9: Data Protocolo MKT (data-column="8")
    { physicalIndex: 9, name: 'Data Protocolo MKT', filterable: true, key: 'data_protocolo_mkt' },
    
    // Coluna 10: Data Coleta (data-column="9")
    { physicalIndex: 10, name: 'Data Coleta', filterable: true, key: 'data_coleta' },
    
    // Coluna 11: Data Entrega (data-column="10")
    { physicalIndex: 11, name: 'Previsão de Entrega', filterable: true, key: 'data_entrega' },
    
    // Coluna 12: Transportadora (data-column="11")
    { physicalIndex: 12, name: 'Transportadora', filterable: true, key: 'transportadora' },
    
    // Coluna 13: Reembolso % (data-column="12")
    { physicalIndex: 13, name: '% Indenização', filterable: true, key: 'reembolso_pct' },
    
    // Coluna 14: Valor Indenização (data-column="13")
    { physicalIndex: 14, name: 'Valor Indenização', filterable: true, key: 'valor_indenizacao' },
    
    // Coluna 15: Prazo Pgto (data-column="14")
    { physicalIndex: 15, name: 'Prazo Pgto', filterable: true, key: 'prazo_pagamento' },
    
    // Coluna 16: Data Baixa / Pgto (data-column="15")
    { physicalIndex: 16, name: 'Data Baixa / Pgto', filterable: true, key: 'data_pagamento' },
    
    // Coluna 17: Data Registro (data-column="16")
    { physicalIndex: 17, name: 'Data Registro', filterable: true, key: 'data_registro' },
    
    // Coluna 18: Data Triagem (data-column="17")
    { physicalIndex: 18, name: 'Data Triagem', filterable: true, key: 'data_triagem' },
    
    // Coluna 19: Data Finalização (data-column="18")
    { physicalIndex: 19, name: 'Data Finalização', filterable: true, key: 'data_finalizacao' }
];

let selectedGlobalFilterPhysicalIndices = [];
let columnFilterValues = {};

/**
 * Adicione esta função ao topo do arquivo ou certifique-se de que ela esteja disponível globalmente
 */
function formatDate(dateStr) {
    if (!dateStr || dateStr === 'None') return '—';

    try {
        // ── DD/MM/YYYY puro → já correto ─────────────────────────────
        if (/^\d{2}\/\d{2}\/\d{4}$/.test(dateStr)) return dateStr;

        // ── XX/XX/YYYY com ou sem hora → detectar MM/DD vs DD/MM ─────
        const slashMatch = dateStr.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})/);
        if (slashMatch) {
            const p1 = parseInt(slashMatch[1], 10);
            const p2 = parseInt(slashMatch[2], 10);
            const year = slashMatch[3];
            let day, month;
            // p2 > 12 → impossível ser mês → formato MM/DD/YYYY (Sheets US)
            if (p2 > 12) {
                day = p2; month = p1;
            } else {
                // DD/MM/YYYY ou ambíguo → mantém DD/MM
                day = p1; month = p2;
            }
            return `${String(day).padStart(2,'0')}/${String(month).padStart(2,'0')}/${year}`;
        }

        // ── ISO YYYY-MM-DD com ou sem hora → apenas data, sem timezone ─
        const isoMatch = dateStr.match(/^(\d{4})-(\d{2})-(\d{2})/);
        if (isoMatch) return `${isoMatch[3]}/${isoMatch[2]}/${isoMatch[1]}`;

        // ── Fallback: cortar 10 chars ─────────────────────────────────
        if (dateStr.length >= 10) {
            const sub = dateStr.substring(0, 10);
            const parts = sub.split('-');
            if (parts.length === 3) return `${parts[2]}/${parts[1]}/${parts[0]}`;
        }

        return dateStr;
    } catch (e) {
        return dateStr;
    }
}

// DEBOUNCE HELPER — definir ANTES de qualquer listener
function _debounce(fn, delay) {
    let t;
    return function(...args) {
        clearTimeout(t);
        t = setTimeout(() => fn.apply(this, args), delay);
    };
}

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

// 1. GERAÇÃO E UI (Colunas e Filtros)

function generateColumnCheckboxes() {
    const container = document.getElementById('columnsCheckboxList');
    if (!container) return;
    
    container.innerHTML = '';
    
    // Default to searching all filterable columns
    selectedGlobalFilterPhysicalIndices = [];

    COLUMN_DEFS.filter(col => col.filterable).forEach(col => {
        selectedGlobalFilterPhysicalIndices.push(col.physicalIndex); // Default to selected
        
        const div = document.createElement('div');
        div.className = 'column-checkbox';
        
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.id = `col_physical_${col.physicalIndex}`;
        checkbox.value = col.physicalIndex; 
        checkbox.checked = true;
        checkbox.addEventListener('change', updateSelectedColumnsText);
        
        const label = document.createElement('label');
        label.htmlFor = `col_physical_${col.physicalIndex}`;
        label.textContent = col.name;
        
        div.appendChild(checkbox);
        div.appendChild(label);
        container.appendChild(div);
    });
    
    updateSelectedColumnsText();
}

/**
 * Salva o estado atual dos filtros no sessionStorage
 */
function salvarFiltrosCache() {
    if (!isStorageAvailable()) return;
    const filtros = {
        nota: document.getElementById('filtroNota')?.value || '',
        codEmpresa: document.getElementById('filtroCodEmpresa')?.value || '',
        search: document.getElementById('globalSearch')?.value || '',
        timestamp: new Date().getTime(),
        columnFilters: typeof columnFilterValues !== 'undefined' ? Object.assign({}, columnFilterValues) : {},
    };
    
    sessionStorage.setItem('wms_filtros_consulta', JSON.stringify(filtros));
    console.log('Filtros salvos no cache:', filtros);
}

/**
 * Restaura os filtros do sessionStorage
 */
function restaurarFiltrosCache() {
    if (!isStorageAvailable()) return false;
    let filtrosStr;
    filtrosStr = sessionStorage.getItem('wms_filtros_consulta');
    
    if (!filtrosStr) {
        console.log('ℹNenhum filtro em cache');
        return false;
    }
    
    try {
        const filtros = JSON.parse(filtrosStr);
        
        // Verificar se o cache não está muito antigo (30 minutos)
        const agora = new Date().getTime();
        const tempoCacheMinutos = (agora - filtros.timestamp) / 1000 / 60;
        
        if (tempoCacheMinutos > 30) {
            console.log('⏰ Cache expirado, limpando...');
            sessionStorage.removeItem('wms_filtros_consulta');
            return false;
        }
        
        // NÃO remover o cache aqui — _restaurarFiltrosColuna() lê do mesmo item
        // depois de generateFilterRow() criar os inputs. A remoção acontece lá.
        // O loop infinito não ocorre pois aplicarFiltros() não recarrega a página.
        
        // Restaurar valores nos campos
        if (filtros.nota && document.getElementById('filtroNota')) {
            document.getElementById('filtroNota').value = filtros.nota;
        }
        
        if (filtros.codEmpresa && document.getElementById('filtroCodEmpresa')) {
            document.getElementById('filtroCodEmpresa').value = filtros.codEmpresa;
        }
        
        if (filtros.search && document.getElementById('globalSearch')) {
            document.getElementById('globalSearch').value = filtros.search;
        }

        console.log('Filtros restaurados (busca global):', filtros.search);

        return true;
        
    } catch (e) {
        console.error('Erro ao restaurar filtros:', e);
        sessionStorage.removeItem('wms_filtros_consulta');
        return false;
    }
}

/**
 * Limpa o cache de filtros
 */
function limparCacheFiltros() {
    if (isStorageAvailable()) sessionStorage.removeItem('wms_filtros_consulta');
    
    // Limpar também as chaves do CacheEstado (consultar + registrar)
    if (typeof CacheEstado !== 'undefined') CacheEstado.limparTudo();
    console.log('Cache de filtros limpo (ambos os sistemas)');
}

/**
 * Restaura os filtros individuais de coluna do cache.
 * Deve ser chamado APÓS generateFilterRow() para que os inputs DOM existam.
 */
function _restaurarFiltrosColuna() {
    if (!isStorageAvailable()) return;
    let filtrosStr;
    filtrosStr = sessionStorage.getItem('wms_filtros_consulta');
    if (!filtrosStr) return;
    
    try {
        const filtros = JSON.parse(filtrosStr);

        // Remover agora — todos os dados já foram lidos (busca global em restaurarFiltrosCache,
        // filtros de coluna aqui). Sem risco de loop pois applyFilters() é client-side.
        sessionStorage.removeItem('wms_filtros_consulta');

        if (!filtros.columnFilters || Object.keys(filtros.columnFilters).length === 0) return;
        
        // Restaurar objeto JS de estado
        if (typeof columnFilterValues !== 'undefined') {
            Object.assign(columnFilterValues, filtros.columnFilters);
        }
        
        // Popular os inputs DOM criados por generateFilterRow()
        Object.entries(filtros.columnFilters).forEach(([colIndex, valor]) => {
            if (!valor) return;
            const input = document.querySelector(`.column-filter-individual[data-column="${colIndex}"]`);
            if (input) input.value = valor;
        });
        
        console.log('Filtros de coluna restaurados:', filtros.columnFilters);
    } catch (e) {
        console.warn('Erro ao restaurar filtros de coluna:', e);
        sessionStorage.removeItem('wms_filtros_consulta');
    }
}

/**
 * Aplica os filtros (função existente ou nova)
 */
function aplicarFiltros() {
    applyFilters();
}


// Continua em: consultar-avarias-baixas.js, consultar-avarias-dropdown.js, consultar-avarias-modais.js
// (arquivo original de 1819 linhas dividido por seção; ordem de carregamento importa).
