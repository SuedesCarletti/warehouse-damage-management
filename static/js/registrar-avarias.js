/**
 * REGISTRAR AVARIAS - JavaScript
 * Extraído de: templates/wms/registrar_avarias.html (Fase 2)
 * 
 * NOTA: Este arquivo NÃO depende de variáveis Jinja2.
 *       Todas as URLs e dados são obtidos via API ou elementos DOM.
 */

// VARIÁVEIS GLOBAIS
let itensNotaGlobal = [];

// Mapeamento de colunas para filtros e busca global (16 colunas, 0-15)
const COLUMN_MAP_PENDING = {
    0:  { name: 'Ações',           headerIndex: 0,  itemIndex: null, filterable: false },
    1:  { name: 'Ações Item',      headerIndex: 1,  itemIndex: 0,    filterable: false },
    2:  { name: 'Cód. Empresa',    headerIndex: 2,  itemIndex: null, filterable: true,  key: 'cod_empresa'        },
    3:  { name: 'Nota',            headerIndex: 3,  itemIndex: null, filterable: true,  key: 'nota'               },
    4:  { name: 'NFD',             headerIndex: 4,  itemIndex: null, filterable: true,  key: 'nfd'                },
    5:  { name: 'Cód. Produto',    headerIndex: 5,  itemIndex: 1,    filterable: true,  key: 'cod_produto'        },
    6:  { name: 'Produto',         headerIndex: 6,  itemIndex: 2,    filterable: true,  key: 'produto'            },
    7:  { name: 'Qtd Avariada',    headerIndex: 7,  itemIndex: 3,    filterable: true,  key: 'qtd_avariada'       },
    8:  { name: 'Localização',     headerIndex: 8,  itemIndex: 4,    filterable: true,  key: 'localizacao'        },
    9:  { name: 'Etiqueta ID',     headerIndex: 9,  itemIndex: 5,    filterable: true,  key: 'id_etiqueta'        },
    10: { name: 'Status Etiq.',    headerIndex: 10, itemIndex: 6,    filterable: true,  key: 'status_etiqueta'    },
    11: { name: 'Qtd Nota (Total)',headerIndex: 11, itemIndex: null, filterable: true,  key: 'qtd_nota_total'     },
    12: { name: 'Transportadora',  headerIndex: 12, itemIndex: null, filterable: true,  key: 'transportadora'     },
    13: { name: 'Estoque',         headerIndex: 13, itemIndex: null, filterable: true,  key: 'status_estoque'     },
    14: { name: 'Indenização',     headerIndex: 14, itemIndex: null, filterable: true,  key: 'status_indenizacao' },
    15: { name: 'Data Registro',   headerIndex: 15, itemIndex: null, filterable: true,  key: 'data_registro'      },
    16: { name: 'Data Triagem',    headerIndex: 16, itemIndex: 7,    filterable: true,  key: 'data_triagem'       },
    17: { name: 'Data Finalização',headerIndex: 17, itemIndex: null, filterable: true,  key: 'data_finalizacao'   }
};

let selectedGlobalFilterColumns = []; // Store physical indices (1-17)
let columnFilterValuesPending = {};

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

/**
 * Helper: salva cache do estado atual e recarrega a página.
 * Centraliza todos os location.reload() do registrar para garantir
 * que filtros/busca nunca sejam perdidos após ações como editar etiqueta,
 * mover, quebrar, adicionar fotos, etc.
 * @param {number} delay - ms de espera antes de recarregar (padrão: 0)
 */
function _reloadComCache(delay = 0) {
    if (typeof CacheEstado !== 'undefined') {
        CacheEstado.salvarEstadoAtual('registrar');
    }
    if (delay > 0) {
        setTimeout(() => location.reload(), delay);
    } else {
        location.reload();
    }
}

let lastSortedColPending = -1;
let lastSortDirPending = 'asc';
let sortDirectionPending = {};

let resizingColumnPending = null;
let startXPending = 0;
let startWidthPending = 0;
let columnIndexPending = -1;
let galeriaFotosCache = null; // Cache para evitar recarregar
let fotosAtual = {};
// Variáveis globais para modals de etiquetas
let modalTriagem, modalMovimentacao, modalFotos, modalQuebra, modalBipagem;
let triagemAtual = {};
let fotosSelecionadas = []; 
let quebraAtual = null;
let quebraLinhasCount = 0;
let movimentacaoAtual = null; // FIX: Declaração única (resolve SyntaxError)
let locaisDisponiveis = [];

// Usar window.debounce se avarias-common.js já estiver carregado, ou definir aqui
var _debounce = (typeof window.debounce === 'function') ? window.debounce : function(fn, delay) {
    let t;
    return function(...args) { clearTimeout(t); t = setTimeout(() => fn.apply(this, args), delay); };
};

// ÍNDICE DE GRUPOS — evita leitura de DOM a cada keystroke no filtro
// Cada entrada: { header, headerSearch, headerCells, items: [{row, search, cells}] }
let _pendingGroupIndex = [];

function _buildPendingIndex() {
    _pendingGroupIndex = [];
    const tbody = document.querySelector('#tabelaAvariasPendentes tbody');
    if (!tbody) return;

    const allRows = Array.from(tbody.querySelectorAll('tr'));
    let currentGroup = null;

    allRows.forEach(row => {
        const avariaId = row.getAttribute('data-avaria-id');
        if (!avariaId) return;

        const isHeader = row.cells.length > 10;

        if (isHeader) {
            currentGroup = {
                header: row,
                headerSearch: (row.getAttribute('data-searchable-content') || '').toLowerCase(),
                headerCells: Array.from(row.cells).map(td => td.textContent.trim().toLowerCase()),
                items: []
            };
            _pendingGroupIndex.push(currentGroup);
        } else if (currentGroup && row.cells.length <= 10) {
            currentGroup.items.push({
                row,
                search: (row.getAttribute('data-searchable-content') || '').toLowerCase(),
                cells: Array.from(row.cells).map(td => td.textContent.trim().toLowerCase())
            });
        }
    });
}

function _invalidatePendingIndex() { _pendingGroupIndex = []; }

// rAF throttle para filtros pending — declarado em escopo de módulo (evita TDZ dentro de generateFilterRowPending)
let _filterPendingRafId = null;
function _applyFiltersPendingThrottled() {
    if (_filterPendingRafId) cancelAnimationFrame(_filterPendingRafId);
    _filterPendingRafId = requestAnimationFrame(function() {
        applyComplexFiltersPending();
        _filterPendingRafId = null;
    });
}

// DROPDOWN UI FUNCTIONS

function generateColumnCheckboxesPending() {
    const container = document.getElementById('columnsCheckboxListPending');
    if (!container) return;
    
    container.innerHTML = '';
    
    // Default to searching all filterable columns
    selectedGlobalFilterColumns = [];

    Object.entries(COLUMN_MAP_PENDING).forEach(([index, col]) => {
        if (!col.filterable) return;
        
        selectedGlobalFilterColumns.push(parseInt(index));

        const div = document.createElement('div');
        div.className = 'column-checkbox';
        
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.id = `col_pending_${index}`;
        checkbox.value = index; 
        checkbox.checked = true;
        checkbox.addEventListener('change', updateSelectedColumnsTextPending);
        
        const label = document.createElement('label');
        label.htmlFor = `col_pending_${index}`;
        label.textContent = col.name;
        
        div.appendChild(checkbox);
        div.appendChild(label);
        container.appendChild(div);
    });
    
    updateSelectedColumnsTextPending();
}

function updateSelectedColumnsTextPending() {
    const checkboxes = document.querySelectorAll('#columnsCheckboxListPending input[type="checkbox"]');
    const textElement = document.getElementById('columnsSelectedTextPending');
    if (!textElement) return;
    
    selectedGlobalFilterColumns = Array.from(checkboxes).filter(cb => cb.checked).map(cb => parseInt(cb.value));

    const totalFilterable = Object.values(COLUMN_MAP_PENDING).filter(c => c.filterable).length;

    if (selectedGlobalFilterColumns.length === 0) textElement.textContent = 'Nenhuma coluna selecionada';
    else if (selectedGlobalFilterColumns.length === totalFilterable) textElement.textContent = 'Todas as colunas';
    else textElement.textContent = `${selectedGlobalFilterColumns.length} de ${totalFilterable} colunas`;

    // Trigger filtering immediately
    applyComplexFiltersPending();
}

function selectAllColumnsPending() {
    document.querySelectorAll('#columnsCheckboxListPending input[type="checkbox"]').forEach(cb => cb.checked = true);
    updateSelectedColumnsTextPending();
}

function deselectAllColumnsPending() {
    document.querySelectorAll('#columnsCheckboxListPending input[type="checkbox"]').forEach(cb => cb.checked = false);
    updateSelectedColumnsTextPending();
}

// INDIVIDUAL FILTER ROW GENERATION (REIMPLEMENTED)

function generateFilterRowPending() {
    const filterRow = document.getElementById('filterRowPending');
    if (!filterRow) return;

    filterRow.innerHTML = '';

    Object.entries(COLUMN_MAP_PENDING).forEach(([index, col]) => {
        const th = document.createElement('th');

        if (col.filterable) {
            const input = document.createElement('input');
            input.type = 'text';
            input.className = 'form-control form-control-sm column-filter-pending';
            input.setAttribute('data-column', index);
            input.placeholder = 'Filtrar...';
            input.value = columnFilterValuesPending[index] || '';

            // _applyFiltersPendingThrottled declarado em escopo de módulo (linha ~83)
            const debouncedApply = _debounce(_applyFiltersPendingThrottled, 300);
            input.addEventListener('input', function() {
                columnFilterValuesPending[parseInt(index)] = this.value;
                debouncedApply();
            });
            th.appendChild(input);
        } else {
            th.innerHTML = '<small class="text-muted"></small>';
        }
        filterRow.appendChild(th);
    });
}

// COMPLEX FILTER LOGIC (REFACTORED CORE)

function getColumnFiltersPending() {
    // Collect values from the filter row inputs
    const filters = {};
    document.querySelectorAll('.column-filter-pending').forEach(input => {
        const index = parseInt(input.getAttribute('data-column'));
        const value = input.value.trim().toLowerCase();
        if (value) {
            filters[index] = value;
        }
    });
    return filters;
}

function checkRowAgainstIndividualFilters(row, filters, isHeader) {
    // OPCIONAL: Permitir que filtros de coluna também busquem em data-searchable-content
    // (Descomente se quiser que filtros individuais busquem em TODOS os campos)
    /*
    const searchableContent = row.getAttribute('data-searchable-content') || '';
    for (const [colIndexStr, filterValue] of Object.entries(filters)) {
        if (filterValue && searchableContent.includes(filterValue)) {
            continue; // Este filtro passou
        }
        // Se não encontrou no searchable-content, verificar nas células
    }
    */
    
    const cells = row.cells;

    for (const [colIndexStr, filterValue] of Object.entries(filters)) {
        const colIndex = parseInt(colIndexStr); 
        const colDef = COLUMN_MAP_PENDING[colIndex];
    
        if (!colDef || !colDef.filterable) continue;

        let cellIndexToCheck = colDef.headerIndex;
        
        // Ajuste de índice se for linha de item e coluna for item-specific (4-7)
        if (!isHeader && colDef.itemIndex !== null) {
             cellIndexToCheck = colDef.itemIndex;
        } else if (!isHeader && colDef.itemIndex === null && colDef.headerIndex > 0) {
             continue;
        }

        if (cells[cellIndexToCheck]) {
            const cellValue = cells[cellIndexToCheck].textContent.toLowerCase();
            if (!cellValue.includes(filterValue)) {
                return false;
            }
        }
    }
    return true;
}

function checkRowAgainstGlobalSearch(row, searchTerm, isHeader) {
    // Suporte a múltiplos termos OR separados por vírgula
    const terms = searchTerm
        ? searchTerm.split(',').map(t => t.trim()).filter(t => t.length > 0)
        : [searchTerm];

    const searchableContent = row.getAttribute('data-searchable-content');
    if (searchableContent) {
        const sc = searchableContent.toLowerCase();
        if (terms.some(t => sc.includes(t))) return true;
    }

    const cells = row.cells;
    for (const colIndex of selectedGlobalFilterColumns) {
        const colDef = COLUMN_MAP_PENDING[colIndex];
        if (!colDef || !colDef.filterable) continue;
        let cellIndexToCheck = colDef.headerIndex;
        if (!isHeader && colDef.itemIndex !== null) {
            cellIndexToCheck = colDef.itemIndex;
        } else if (!isHeader && colDef.itemIndex === null && colDef.headerIndex > 0) {
            continue;
        }
        if (cells[cellIndexToCheck]) {
            const cellValue = cells[cellIndexToCheck].textContent.toLowerCase();
            if (terms.some(t => cellValue.includes(t))) return true;
        }
    }
    return false;
}

function applyComplexFiltersPending() {
    // Lazy-build: constrói índice na primeira chamada (ou após invalidação)
    if (_pendingGroupIndex.length === 0) _buildPendingIndex();

    const globalSearchRaw = (document.getElementById('globalSearchAvarias')?.value || '').toLowerCase().trim();
    const globalTerms = globalSearchRaw
        ? globalSearchRaw.split(',').map(t => t.trim()).filter(t => t.length > 0)
        : [];

    // Coletar filtros de coluna ativos (lê DOM uma única vez, fora do loop)
    const activeColFilters = [];
    document.querySelectorAll('.column-filter-pending').forEach(input => {
        const term = input.value.trim().toLowerCase();
        if (term) {
            const colIndex = parseInt(input.getAttribute('data-column'));
            activeColFilters.push({ colIndex, term, colDef: COLUMN_MAP_PENDING[colIndex] });
        }
    });

    // ── Ler filtro de data da UI (simultâneo aos demais filtros) ─────────
    function _parseDateUI(s) {
        if (!s) return null;
        const parts = s.includes('-') ? s.split('-') : s.split('/').reverse();
        if (parts.length === 3) return new Date(parts[0], parts[1] - 1, parts[2]);
        return null;
    }
    const _dtInicioUI = _parseDateUI(document.getElementById('filtroDataInicioPending')?.value || '');
    const _dtFimUI    = _parseDateUI(document.getElementById('filtroDataFimPending')?.value    || '');
    const _campoUI    = (document.getElementById('filtroCampoDataPending')?.value || 'data_registro');
    const _attrDateUI = 'data-' + _campoUI.replace(/_/g, '-');
    const hasDateFilter = !!_dtInicioUI || !!_dtFimUI;

    const hasFilters = globalTerms.length > 0 || activeColFilters.length > 0 || hasDateFilter;
    let visibleCount = 0;

    for (const group of _pendingGroupIndex) {
        if (!hasFilters) {
            // Sem filtro: mostrar tudo diretamente, sem tocar no DOM de leitura
            group.header.style.display = '';
            group.items.forEach(it => { it.row.style.display = ''; });
            
            // FIX: Re-estabelecer rowspan original
            const totalVisible = 1 + group.items.length;
            Array.from(group.header.cells).forEach(cell => {
                if (cell.hasAttribute('rowspan')) {
                    cell.setAttribute('rowspan', totalVisible);
                }
            });
            
            visibleCount++;
            continue;
        }

        // ── 1. Verificar filtro de DATA no header ─────────────────────────
        let dateMatch = true;
        if (hasDateFilter) {
            const dataStr = group.header.getAttribute(_attrDateUI) || '';
            const dt = _parseDateUI(dataStr.substring(0, 10));
            if (dt) {
                if (_dtInicioUI && dt < _dtInicioUI) dateMatch = false;
                if (_dtFimUI    && dt > _dtFimUI)    dateMatch = false;
            } else {
                dateMatch = false; // sem valor no campo = oculta quando filtro ativo
            }
        }

        // ── 2. Verificar filtros de coluna no header (AND entre filtros) ──
        let headerMatch = true;
        if (activeColFilters.length > 0) {
            for (const { colIndex, term, colDef } of activeColFilters) {
                if (!colDef || !colDef.filterable) continue;
                const cellText = group.headerCells[colDef.headerIndex] || '';
                if (!cellText.includes(term)) { headerMatch = false; break; }
            }
        }

        // ── 3. Verificar busca global no header (OR entre termos, AND com os demais) ──────
        let globalMatchHeader = true;
        if (globalTerms.length > 0) {
            // Testa searchable-content primeiro (mais abrangente)
            globalMatchHeader = globalTerms.some(t => group.headerSearch.includes(t));
            if (!globalMatchHeader) {
                // Fallback: colunas selecionadas no filtro global
                globalMatchHeader = selectedGlobalFilterColumns.some(colIndex => {
                    const colDef = COLUMN_MAP_PENDING[colIndex];
                    if (!colDef) return false;
                    const cellText = group.headerCells[colDef.headerIndex] || '';
                    return globalTerms.some(t => cellText.includes(t));
                });
            }
        }

        const groupVisible = dateMatch && headerMatch && globalMatchHeader;
        group.header.style.display = groupVisible ? '' : 'none';

        let visibleItemsCount = 0;

        // ── Processar linhas filho ─────────────────────────────────────────
        group.items.forEach(it => {
            if (!groupVisible) { it.row.style.display = 'none'; return; }

            // Filtro por coluna no item (AND)
            let colMatchItem = true;
            for (const { term, colDef } of activeColFilters) {
                if (!colDef || !colDef.filterable || colDef.itemIndex === null) continue;
                const cellText = it.cells[colDef.itemIndex] || '';
                if (!cellText.includes(term)) { colMatchItem = false; break; }
            }

            // Busca global no item (OR)
            let globalMatchItem = true;
            if (globalTerms.length > 0) {
                let match = globalTerms.some(t => it.search.includes(t));
                if (!match) {
                    match = selectedGlobalFilterColumns.some(colIndex => {
                        const colDef = COLUMN_MAP_PENDING[colIndex];
                        if (!colDef || colDef.itemIndex === null) return false;
                        return globalTerms.some(t => (it.cells[colDef.itemIndex] || '').includes(t));
                    });
                }
                globalMatchItem = match;
            }

            const itemMatch = colMatchItem && globalMatchItem;
            it.row.style.display = itemMatch ? '' : 'none';
            if (itemMatch) visibleItemsCount++;
        });

        // FIX: AJUSTE DINÂMICO DE ROWSPAN
        if (groupVisible) {
            const newRowspan = 1 + visibleItemsCount;
            Array.from(group.header.cells).forEach(cell => {
                if (cell.hasAttribute('rowspan')) {
                    cell.setAttribute('rowspan', newRowspan);
                }
            });
            visibleCount++;
        }
    }

    // Atualizar contador e label
    const countEl = document.getElementById('pendingAvariaCount');
    if (countEl) countEl.textContent = `Exibindo ${visibleCount} registros`;

    const infoParts = [];
    if (hasDateFilter) infoParts.push('Data');
    if (activeColFilters.length > 0) infoParts.push('Coluna');
    if (globalTerms.length > 0) infoParts.push(globalTerms.length > 1 ? globalTerms.join(' | ') : globalSearchRaw);
    const filterTerm = infoParts.join(' + ');
    const infoEl = document.getElementById('filterInfoPending');
    if (infoEl) {
        infoEl.innerHTML = filterTerm
            ? `<i class="fas fa-filter me-1"></i>Filtrando por "${filterTerm}"`
            : '';
    }
}

// IIFE movida para dentro do DOMContentLoaded — ver bloco abaixo

/**
 * Lógica de ordenação (existente e preservada)
 */
function sortTablePending(dataColIndex) {
    const tbody = document.querySelector('#tabelaAvariasPendentes tbody');
    if (!tbody) return;

    // Coleta apenas header rows para ordenar grupos
    const headerRows = Array.from(tbody.querySelectorAll('tr')).filter(r => {
        return r.getAttribute('data-avaria-id') && r.cells.length > 10;
    });

    sortDirectionPending[dataColIndex] = (sortDirectionPending[dataColIndex] === 'desc') ? 'asc' : 'desc';
    const isAsc = sortDirectionPending[dataColIndex] === 'asc';

    // Mapear cada header com seus itens filhos
    const grupos = headerRows.map(header => {
        const avariaId = header.getAttribute('data-avaria-id');
        const items = Array.from(tbody.querySelectorAll(`tr[data-avaria-id="${avariaId}"]`))
            .filter(r => r.cells.length <= 10);
        return { header, items };
    });

    grupos.sort((a, b) => {
        const valA = a.header.cells[dataColIndex]?.textContent.trim() || '';
        const valB = b.header.cells[dataColIndex]?.textContent.trim() || '';

        // Data
        if (valA.match(/^\d{2}\/\d{2}\/\d{4}/) || valA.match(/^\d{4}-\d{2}-\d{2}/)) {
            const toSort = v => v.includes('/') ? v.split('/').reverse().join('') : v.replace(/[^\d]/g, '').substring(0, 8);
            return isAsc ? toSort(valA).localeCompare(toSort(valB)) : toSort(valB).localeCompare(toSort(valA));
        }

        // Numérico
        const nA = parseFloat(valA.replace(/[R$.\s]/g, '').replace(',', '.'));
        const nB = parseFloat(valB.replace(/[R$.\s]/g, '').replace(',', '.'));
        if (!isNaN(nA) && !isNaN(nB)) return isAsc ? nA - nB : nB - nA;

        return isAsc ? valA.localeCompare(valB, 'pt-BR') : valB.localeCompare(valA, 'pt-BR');
    });

    // Reinserir na ordem correta (header + seus items)
    grupos.forEach(({ header, items }) => {
        tbody.appendChild(header);
        items.forEach(item => tbody.appendChild(item));
    });

    // Atualizar ícones
    document.querySelectorAll('#tabelaAvariasPendentes th .sort-icon').forEach(icon => {
        icon.className = 'fas fa-sort sort-icon float-end';
    });
    const activeTh = document.querySelector(`#tabelaAvariasPendentes th[data-column="${dataColIndex}"] .sort-icon`);
    if (activeTh) activeTh.className = `fas fa-sort-${isAsc ? 'up' : 'down'} sort-icon float-end`;

    // Reaplica filtros após reordenar — sem isso linhas ocultas ficam em posições erradas.
    // O índice _pendingGroupIndex continua válido (objetos <tr> são os mesmos).
    applyComplexFiltersPending();
}


/**
 * Inicialização do Redimensionamento de Colunas
 */
function initializeColumnResizePending() {
    const table = document.getElementById('tabelaAvariasPendentes');
    if (!table) return;
    
    // Seleciona resizers na primeira linha TH
    document.querySelectorAll('#tabelaAvariasPendentes thead tr:first-child .column-resizer').forEach((resizer) => {
        // Encontrar o índice físico do TH pai
        const th = resizer.parentElement;
        const thIndex = Array.from(th.parentNode.children).indexOf(th);
        
        resizer.addEventListener('mousedown', function(e) {
            e.preventDefault(); e.stopPropagation();
            resizingColumnPending = th;
            columnIndexPending = thIndex; // Índice físico
            startXPending = e.pageX;
            startWidthPending = resizingColumnPending.offsetWidth;
            resizer.classList.add('resizing');
            
            // Adicionar listeners globais
            document.addEventListener('mousemove', handleResizeMovePending);
            document.addEventListener('mouseup', handleResizeUpPending);
        });
    });
}

function handleResizeMovePending(e) {
    if (!resizingColumnPending) return;
    const minWidth = 80;
    const w = startWidthPending + (e.pageX - startXPending);
    
    if (w > minWidth) {
        resizingColumnPending.style.width = w + 'px';
        
        // Aplicar largura à célula de filtro correspondente
        const filterRow = document.getElementById('filterRowPending');
        if (filterRow && filterRow.cells[columnIndexPending]) {
            filterRow.cells[columnIndexPending].style.width = w + 'px';
        }
    }
}

function handleResizeUpPending() {
    if(resizingColumnPending) resizingColumnPending.querySelector('.column-resizer').classList.remove('resizing');
    resizingColumnPending = null;
    document.removeEventListener('mousemove', handleResizeMovePending);
    document.removeEventListener('mouseup', handleResizeUpPending);
}

// FUNÇÕES REMANESCENTES (do código original - showDetalhesModal e formulário)

/**
 * Exibe detalhes de uma avaria em modal. (Wrapper para showDetalhesModal)
 */
async function verDetalhesAvaria(avariaId) {
    if (typeof showDetalhesModal === 'function') { 
        showDetalhesModal(avariaId); 
    } else {
        console.error('Função showDetalhesModal não está carregada no escopo global.');
    }
}

// FIX: Implementação de showDetalhesModal para garantir que o modal funcione
async function showDetalhesModal(avariaId) {
    console.log('Buscando detalhes da avaria:', avariaId);

    const modalEl = document.getElementById('modalDetalhes');
    let modal = bootstrap.Modal.getInstance(modalEl);
    if (!modal) {
        modal = new bootstrap.Modal(modalEl);
    }

    const conteudo = document.getElementById('detalhesConteudo');

    modal.show();

    // Loading
    conteudo.innerHTML = `
        <div class="text-center py-4">
            <div class="spinner-border text-success" role="status">
                <span class="visually-hidden">Carregando...</span>
            </div>
        </div>
    `;

    try {
        const response = await fetch(`/wms/api/avaria/${avariaId}`);
        const data = await response.json();

        if (!data.success || !data.avaria) {
            throw new Error('Avaria não encontrada: ' + (data.message || data.error || 'Erro desconhecido'));
        }

        const av = data.avaria;
        const itens = data.itens || [];

        // ─── Helpers ─────────────────────────────────────────────────────────
        const fmtMoeda = (v) => {
            const n = parseFloat(String(v || '0').replace(/[^\d,.-]/g, '').replace(',', '.'));
            return isNaN(n) ? 'R$ 0,00' : n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
        };
        const val = (v, fallback = '<em class="text-muted">N/A</em>') =>
            (v && String(v).trim() && String(v).trim() !== '-') ? v : fallback;
        const badge = (v, cor = 'secondary') =>
            `<span class="badge bg-${cor} text-white">${val(v, 'N/A')}</span>`;

        // Badge etiqueta inicial impressa
        const etqBadge = av.etiqueta_inicial_impressa === 'sim'
            ? '<span class="badge bg-success">Sim</span>'
            : '<span class="badge bg-danger">Não</span>';

        // ─── Itens — layout completo (cards) ─────────────────────────────────
        const itensHtml = itens.length > 0
            ? (() => {
                const etqCor   = (s) => ({ ativa: 'success', baixada: 'danger', pendente: 'warning' }[s] || 'secondary');
                const etqLabel = (s) => ({ ativa: 'Ativa',  baixada: 'Baixada', pendente: 'Pendente'}[s] || (s || 'N/D'));
                const prefixoLabel = (p) => {
                    if (!p || p === '00') return '<span class="badge bg-secondary">00 — Temporário</span>';
                    return `<span class="badge bg-info text-dark">${p} — Estoque</span>`;
                };
                const fmtVal = (v) => {
                    const s = String(v ?? '0').replace(/[^\d,.-]/g, '');
                    let n;
                    if (s.includes(',') && s.includes('.') && s.indexOf('.') < s.lastIndexOf(',')) {
                        n = parseFloat(s.replace(/\./g, '').replace(',', '.'));  // BR: 1.234,56
                    } else if (s.includes(',')) {
                        n = parseFloat(s.replace(',', '.'));  // BR simples: 532,31
                    } else {
                        n = parseFloat(s);  // US/plain: 532.31
                    }
                    return isNaN(n) ? 'R$ 0,00' : n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
                };

                const cardsHtml = itens.map((item, idx) => {
                    const seq = item.sequencia || (idx + 1);
                    const temEtiqueta = item.id_etiqueta && item.id_etiqueta.trim();
                    const temTriagem  = item.data_triagem && item.data_triagem.trim();

                    return `
                    <div class="border rounded mb-3 overflow-hidden">
                        <div class="px-3 py-2 d-flex justify-content-between align-items-center"
                             style="background:rgba(0,128,0,0.1); border-bottom:1px solid rgba(0,0,0,.1);">
                            <span class="fw-bold">
                                <i class="fas fa-box me-2 text-success"></i>
                                #${seq} — <code class="text-success">${item.codprod || 'N/A'}</code>
                                <small class="ms-2 text-muted fw-normal">${item.descrprod || 'Sem descrição'}</small>
                            </span>
                            <span class="badge bg-${etqCor(item.status_etiqueta)} ms-2">
                                ${etqLabel(item.status_etiqueta)}
                            </span>
                        </div>
                        <div class="p-3">
                            <div class="row g-3">
                                <!-- BLOCO 1: Quantidades e Valor -->
                                <div class="col-md-4">
                                    <p class="text-muted small mb-1 fw-semibold text-uppercase">
                                        <i class="fas fa-boxes me-1"></i>Quantidades e Valor
                                    </p>
                                    <table class="table table-sm table-bordered mb-0">
                                        <tr><th width="55%">Qtd Nota</th><td>${item.qtdneg ?? '-'}</td></tr>
                                        <tr><th>Qtd Avariada</th><td><strong class="text-danger">${item.qtd_avariada ?? '-'}</strong></td></tr>
                                        <tr><th>Valor Item</th><td class="fw-bold">${fmtVal(item.valor_item)}</td></tr>
                                        <tr><th>Cód. Grupo</th><td><small>${item.codgrupoprod || '-'}</small></td></tr>
                                        <tr><th>Cód. Empresa</th><td><small>${item.codemp || '-'}</small></td></tr>
                                    </table>
                                </div>
                                <!-- BLOCO 2: Localização -->
                                <div class="col-md-4">
                                    <p class="text-muted small mb-1 fw-semibold text-uppercase">
                                        <i class="fas fa-map-marker-alt me-1"></i>Localização
                                    </p>
                                    <table class="table table-sm table-bordered mb-0">
                                        <tr>
                                            <th width="55%">Localização Atual</th>
                                            <td><span class="badge bg-secondary">${item.localizacao || 'N/A'}</span></td>
                                        </tr>
                                        <tr><th>Cód. Local Orig.</th><td><small>${item.codlocalorig || '-'}</small></td></tr>
                                        <tr><th>Cód. Local Dest.</th><td><small>${item.codlocaldest || '-'}</small></td></tr>
                                        <tr><th>Prefixo Estoque</th><td>${prefixoLabel(item.prefixo_estoque)}</td></tr>
                                    </table>
                                </div>
                                <!-- BLOCO 3: Etiqueta -->
                                <div class="col-md-4">
                                    <p class="text-muted small mb-1 fw-semibold text-uppercase">
                                        <i class="fas fa-tag me-1"></i>Etiqueta
                                    </p>
                                    <table class="table table-sm table-bordered mb-0">
                                        <tr>
                                            <th width="55%">ID Etiqueta</th>
                                            <td>${temEtiqueta
                                                ? `<code class="small text-primary">${item.id_etiqueta}</code>`
                                                : '<em class="text-muted small">Não gerada</em>'}</td>
                                        </tr>
                                        <tr>
                                            <th>Etiq. Anterior</th>
                                            <td>${item.etiqueta_anterior
                                                ? `<code class="small text-secondary">${item.etiqueta_anterior}</code>`
                                                : '<em class="text-muted small">—</em>'}</td>
                                        </tr>
                                        <tr><th>Data Identificação</th><td><small>${formatDate(item.data_identificacao) || '-'}</small></td></tr>
                                        <tr><th>Operador Ident.</th><td><small>${item.operador_identificacao || '-'}</small></td></tr>
                                    </table>
                                </div>
                            </div>
                            ${(temTriagem || item.observacao_produto) ? `
                            <div class="row g-3 mt-1">
                                ${temTriagem ? `
                                <div class="col-md-6">
                                    <p class="text-muted small mb-1 fw-semibold text-uppercase">
                                        <i class="fas fa-exchange-alt me-1"></i>Triagem
                                    </p>
                                    <table class="table table-sm table-bordered mb-0">
                                        <tr><th width="45%">Data Triagem</th><td><small>${formatDate(item.data_triagem)}</small></td></tr>
                                        <tr><th>Operador Triagem</th><td><small>${item.operador_triagem || '-'}</small></td></tr>
                                    </table>
                                </div>` : ''}
                                ${item.observacao_produto ? `
                                <div class="col-md-6">
                                    <p class="text-muted small mb-1 fw-semibold text-uppercase">
                                        <i class="fas fa-comment me-1"></i>Observação do Produto
                                    </p>
                                    <div class="border rounded p-2 obs-dark-safe small">
                                        ${item.observacao_produto.replace(/\n/g, '<br>')}
                                    </div>
                                </div>` : ''}
                            </div>` : ''}
                        </div>
                    </div>`;
                }).join('');

                return `
                    <div id="itens-avaria-lista" style="max-height:420px; overflow-y:auto; padding-right:4px;">
                        ${cardsHtml}
                    </div>`;
            })()
            : '<p class="text-muted fst-italic mb-0"><i class="fas fa-info-circle me-1"></i>Nenhum item avariado detalhado.</p>';

        // ─── HTML Principal ───────────────────────────────────────────────────
        const html = `
            <!-- SEÇÃO 1: Identificação -->
            <h6 class="text-success mb-2"><i class="fas fa-id-card me-2"></i>Identificação</h6>
            <div class="row g-3 mb-3">
                <div class="col-md-6">
                    <table class="table table-sm table-bordered mb-0">
                        <tr><th width="40%">ID</th><td><strong>${val(av.id)}</strong></td></tr>
                        <tr><th>Cód. Empresa</th><td>${val(av.cod_empresa)}</td></tr>
                        <tr><th>Nota Fiscal</th><td>${val(av.nota)}</td></tr>
                        <tr><th>NFD</th><td>${val(av.nfd)}</td></tr>
                        <tr><th>NFP</th><td>${val(av.nfp)}</td></tr>
                        <tr><th>Quantidade</th><td>${val(av.quantidade)}</td></tr>
                        <tr><th>Volume</th><td>${val(av.volume)}</td></tr>
                        <tr><th>Etiqueta Impressa</th><td>${etqBadge}</td></tr>
                    </table>
                </div>
                <div class="col-md-6">
                    <table class="table table-sm table-bordered mb-0">
                        <tr><th width="45%">Cód. Parceiro</th><td>${val(av.codparc)}</td></tr>                    
                        <tr><th width="45%">Transportadora</th><td>${val(av.transportadora)}</td></tr>
                        <tr><th>CTE</th><td>${val(av.cte)}</td></tr>
                        <tr><th>Motivo Devolução</th><td>${val(av.motivo_devolucao)}</td></tr>
                        <tr><th>Cód. Motivo</th><td>${val(av.cod_motivo_devolucao)}</td></tr>
                        <tr><th>Motivo Inicial</th><td>${val(av.motivo_inicial)}</td></tr>
                        <tr><th>Data Protocolo MKT</th><td>${formatDate(av.data_protocolo_mkt)}</td></tr>
                        <tr><th>Registrado Por</th><td>${val(av.registrado_por)}</td></tr>
                        <tr><th>Data Registro</th><td>${formatDate(av.data_registro)}</td></tr>
                    </table>
                </div>
            </div>

            <!-- SEÇÃO 2: Financeiro e Status -->
            <h6 class="text-info mb-2 mt-3"><i class="fas fa-dollar-sign me-2"></i>Financeiro e Status</h6>
            <div class="row g-3 mb-3">
                <div class="col-md-6">
                    <table class="table table-sm table-bordered mb-0">
                        <tr><th width="45%">Valor NF</th><td>${fmtMoeda(av.valor_nf)}</td></tr>
                        <tr><th>Valor NFD</th><td>${fmtMoeda(av.valor_nfd)}</td></tr>
                        <tr><th>Valor NFP</th><td>${fmtMoeda(av.valor_nfp)}</td></tr>
                        <tr><th>Valor Indenização</th><td>${fmtMoeda(av.valor_indenizacao)}</td></tr>
                        <tr><th>Reembolso %</th><td>${val(av.reembolso_pct)}</td></tr>
                    </table>
                </div>
                <div class="col-md-6">
                    <table class="table table-sm table-bordered mb-0">
                        <tr><th width="45%">Status Indenização</th><td>${badge(av.status_indenizacao, 'primary')}</td></tr>
                        <tr><th>Status Estoque</th><td>${badge(av.status_estoque, 'secondary')}</td></tr>
                        <tr><th>Indenização</th><td>${val(av.indenizacao)}</td></tr>
                        <tr><th>Prazo Pagamento</th><td>${formatDate(av.prazo_pagamento)}</td></tr>
                        <tr><th>Data Pagamento</th><td>${formatDate(av.data_pagamento)}</td></tr>
                    </table>
                </div>
            </div>

            <!-- SEÇÃO 3: Logística -->
            <h6 class="text-warning mb-2 mt-3"><i class="fas fa-truck me-2"></i>Logística</h6>
            <div class="row g-3 mb-3">
                <div class="col-md-12">
                    <table class="table table-sm table-bordered mb-0">
                        <tr>
                            <th width="20%">Data Coleta</th><td width="30%">${formatDate(av.data_coleta)}</td>
                            <th width="20%">Data Entrega</th><td>${formatDate(av.data_entrega)}</td>
                        </tr>
                        <tr>
                            <th>Cód. Rastreio Correios</th><td colspan="3">${val(av.cod_rastreio_correios)}</td>
                        </tr>
                    </table>
                </div>
            </div>

            <!-- SEÇÃO 4: Observações -->
            ${av.observacao ? `
            <h6 class="text-secondary mb-2 mt-3"><i class="fas fa-comment me-2"></i>Observação</h6>
            <div class="border rounded p-2 obs-dark-safe small mb-3">
                ${av.observacao.replace(/\n/g, '<br>')}
            </div>` : ''}

            ${av.data_finalizacao ? `
            <h6 class="text-danger mb-2 mt-3"><i class="fas fa-check-circle me-2"></i>Finalização</h6>
            <div class="row g-3 mb-3">
                <div class="col-md-12">
                    <table class="table table-sm table-bordered mb-0">
                        <tr>
                            <th width="20%">Data Finalização</th><td width="30%">${formatDate(av.data_finalizacao)}</td>
                            <th width="20%">Finalizado Por</th><td>${val(av.finalizado_por)}</td>
                        </tr>
                        ${av.observacao_final ? `
                        <tr>
                            <th>Obs. Final</th>
                            <td colspan="3"><div class="obs-dark-safe p-1 rounded small">${av.observacao_final.replace(/\n/g, '<br>')}</div></td>
                        </tr>` : ''}
                    </table>
                </div>
            </div>` : ''}

            <!-- SEÇÃO 5: Itens da Avaria -->
            <hr>
            <h6 class="text-success mb-3 mt-2">
                <i class="fas fa-boxes me-2"></i>Itens da Avaria
                <span class="badge bg-secondary ms-1">${itens.length}</span>
            </h6>
            ${itensHtml}
        `;

        conteudo.innerHTML = html;

    } catch (error) {
        console.error('Erro ao buscar detalhes:', error);
        conteudo.innerHTML = `
            <div class="alert alert-danger">
                <i class="fas fa-exclamation-circle me-2"></i>
                Erro ao carregar detalhes: ${error.message}
            </div>`;
    }
}

/**
 * Exibe os itens da nota para seleção
 * CORRIGIDO: Template alinhado com os headers da tabela
 * Colunas: Sel | Seq | Código | Produto | Qtd Nota | Qtd Avariada | Localização | Vlr Unit. | Ações
 */
function exibirItensNota(itens) {
    if (!itens || itens.length === 0) {
        console.warn('[AVARIAS] Nenhum item para exibir');
        esconderItens();
        return;
    }

    const tbody = document.getElementById('itensNotaLista');
    const secaoItens = document.getElementById('secaoItens');

    if (!tbody || !secaoItens) {
        console.error('[AVARIAS] Elementos de itens não encontrados');
        return;
    }

    console.log(`[AVARIAS] Exibindo ${itens.length} itens...`);
    itensNotaGlobal = itens;
    tbody.innerHTML = '';

    itens.forEach((item, index) => {
        const initialQtd       = item.qtdneg || 0;
        // Localização pode ser pré-preenchida com o local de origem se disponível
        const initialLoc       = item.codlocalorig || '';
        // FIX: normaliza separador antes de parseFloat (evita "532,31" → 532)
        const _rawVal = item.valor_item ?? 0;
        const _strVal = String(_rawVal).replace(/[^\d,.-]/g, '');
        const valorUnitario = _strVal.includes(',')
            ? (_strVal.includes('.') && _strVal.indexOf('.') < _strVal.lastIndexOf(',')
                ? parseFloat(_strVal.replace(/\./g, '').replace(',', '.'))  // BR: 1.234,56
                : parseFloat(_strVal.replace(',', '.')))                    // BR simples: 532,31
            : parseFloat(_strVal) || 0;
        const valorFormatado   = valorUnitario > 0
            ? `R$ ${valorUnitario.toFixed(2).replace('.', ',')}`
            : '-';

        const tr = document.createElement('tr');
        tr.innerHTML = `
            <!-- Col 1: Seleção -->
            <td class="text-center">
                <input type="checkbox"
                       class="form-check-input item-checkbox"
                       data-index="${index}"
                       checked>
            </td>

            <!-- Col 2: Sequência -->
            <td class="text-center">${item.sequencia || index + 1}</td>

            <!-- Col 3: Código -->
            <td>${item.codprod}</td>

            <!-- Col 4: Produto -->
            <td class="text-truncate" style="max-width: 200px;" title="${item.descrprod}">
                ${item.descrprod}
            </td>

            <!-- Col 5: Qtd Nota (somente leitura) -->
            <td class="text-end">${item.qtdneg || 0}</td>

            <!-- Col 6: Qtd Avariada (editável) -->
            <td>
                <input type="number"
                       class="form-control form-control-sm text-end qtd-avariada"
                       data-index="${index}"
                       value="${initialQtd}"
                       min="0.01"
                       step="0.01"
                       required>
            </td>

            <!-- Col 7: Localização (editável) -->
            <td>
                <input type="text"
                       class="form-control form-control-sm localizacao-input localizacao-item"
                       data-index="${index}"
                       value="${initialLoc}"
                       placeholder="Ex: A1-02-03">
            </td>

            <!-- Col 8: Vlr Unit. (somente leitura) -->
            <td class="text-end text-success fw-semibold">
                ${valorFormatado}
            </td>

            <!-- Col 9: Ações -->
            <td class="text-center">
                <button type="button"
                        class="btn btn-sm btn-info"
                        onclick="splitItemRegistro(${index})"
                        title="Dividir item em múltiplas linhas">
                    <i class="fas fa-columns"></i>
                </button>
            </td>
        `;
        tbody.appendChild(tr);
    });

    console.log(`[AVARIAS] ${itens.length} itens exibidos na tabela.`);
    secaoItens.style.display = 'block';
}

/**
 * Esconde a seção de itens
 */
function esconderItens() {
    const secaoItens = document.getElementById('secaoItens');
    const tbody = document.getElementById('itensNotaLista');
    
    if (secaoItens) secaoItens.style.display = 'none';
    if (tbody) tbody.innerHTML = '';
    
    itensNotaGlobal = [];
    console.log('ḻ1[AVARIAS] Seção de itens ocultada');
}

/**
 * Coleta itens selecionados antes do submit
 */
function coletarItensSelecionados() {
    const itensSelecionados = [];
    
    document.querySelectorAll('.item-checkbox:checked').forEach(checkbox => {
        const index = parseInt(checkbox.dataset.index);
        const item = itensNotaGlobal[index];
        
        if (!item) return;
        
        const qtdAvariada = document.querySelector(`.qtd-avariada[data-index="${index}"]`)?.value || 0;
        const localizacao = document.querySelector(`.localizacao-input[data-index="${index}"]`)?.value || '';
        
        itensSelecionados.push({
            sequencia: item.sequencia,
            codprod: item.codprod,
            descrprod: item.descrprod,
            qtdneg: item.qtdneg, 
            qtd_avariada: qtdAvariada,
            localizacao: localizacao,
            valor_item: item.valor_item, 
            codlocalorig: item.codlocalorig, 
            codgrupoprod: item.codgrupoprod  
        });
    });
    
    console.log('[AVARIAS] Itens selecionados: ${itensSelecionados.length}');
    
    const hiddenInput = document.getElementById('itens_selecionados');
    if (hiddenInput) {
        hiddenInput.value = JSON.stringify(itensSelecionados);
    }
    
    return itensSelecionados;
}


// Continua em: registrar-avarias-init.js, registrar-avarias-etiquetas.js,
// registrar-avarias-triagem.js, registrar-avarias-quebra.js, registrar-avarias-fotos.js
// (arquivo original de 4254 linhas dividido por seção; ordem de carregamento importa).
