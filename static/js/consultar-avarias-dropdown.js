// Continuação de consultar-avarias.js — seções: dropdown flutuante, busca/filtro unificado, ordenação/redimensionamento.
// Ordem de carregamento importa: deve vir logo depois de consultar-avarias-baixas.js.

// 2. DROPDOWN FLUTUANTE (Posicionamento)

document.addEventListener('DOMContentLoaded', function() {
    const dropdownToggle = document.getElementById('columnsDropdownToggle');
    const dropdownMenu = document.getElementById('columnsDropdownMenu');
    let isPositioning = false;
    
    if (!dropdownToggle || !dropdownMenu) return;

    // Move para o Body para evitar layout thrashing
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
                
                // Medir menu (trick de visibilidade)
                const wasHidden = dropdownMenu.style.display === 'none';
                if(wasHidden) {
                    dropdownMenu.style.visibility = 'hidden';
                    dropdownMenu.style.display = 'flex'; // Use 'flex' for the flex container
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

                // Lógica inteligente de posicionamento
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
        const isVisible = dropdownMenu.style.display !== 'none';
        
        if (!isVisible) {
            positionDropdown();
            dropdownMenu.style.display = 'flex';
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
        if (dropdownMenu.style.display !== 'none') positionDropdown();
    }, { passive: true });
    
    dropdownMenu.addEventListener('click', function(e) { e.stopPropagation(); });
});

// 3. BUSCA E FILTRO UNIFICADO

function updateFilterInfo(visible, total, term) {
    const countEl = document.getElementById('resultsCount');
    const infoEl = document.getElementById('filterInfo');
    if (countEl) countEl.textContent = `${visible} registro(s)`;
    if (infoEl) infoEl.innerHTML = (term && term !== '') ? `<i class="fas fa-filter me-1"></i>Filtrando por "${term}"` : '';
}

let _rowIndex = [];   // Array<{ row: HTMLElement, search: string, cells: string[] }>
 
function _buildRowIndex() {
    _rowIndex = [];
    const tbody = document.querySelector('#tabelaAvarias tbody');
    if (!tbody) return;
 
    Array.from(tbody.querySelectorAll('tr')).forEach(row => {
        if (row.cells.length < 2) return;
 
        // Texto global: usa data-searchable-content quando disponível (gerado pelo Jinja)
        const search = (row.getAttribute('data-searchable-content') || '').toLowerCase();
 
        // Texto por célula: lido UMA VEZ aqui, nunca mais no loop de filtragem
        const cells = Array.from(row.cells).map(td => td.textContent.trim().toLowerCase());
 
        _rowIndex.push({ row, search, cells });
    });
}
 
function _invalidateRowIndex() {
    _rowIndex = [];
}

function applyFilters() {
    // Garante que o índice existe (lazy build na primeira chamada)
    if (_rowIndex.length === 0) _buildRowIndex();
 
    const globalSearchRaw = (document.getElementById('globalSearch')?.value || '').toLowerCase().trim();
 
    // Suporte a múltiplos termos OR separados por vírgula: "Aguardando,Em triagem"
    const globalTerms = globalSearchRaw
        ? globalSearchRaw.split(',').map(t => t.trim()).filter(t => t.length > 0)
        : [];
 
    // Coletar filtros de coluna ativos (somente os não-vazios)
    const activeColFilters = [];   // Array<{ colIndex: number, term: string }>
    for (const [colStr, val] of Object.entries(columnFilterValues)) {
        const term = String(val).toLowerCase().trim();
        if (term) activeColFilters.push({ colIndex: parseInt(colStr), term });
    }
 
    const individualFiltersActive = activeColFilters.length > 0;
    let visibleCount = 0;
 
    for (const entry of _rowIndex) {
        let show = true;
 
        if (individualFiltersActive) {
            // Filtros de coluna: AND entre colunas
            for (const { colIndex, term } of activeColFilters) {
                // colIndex == physicalIndex (0-based). Usa o array pre-extraído.
                if (!(entry.cells[colIndex] || '').includes(term)) {
                    show = false;
                    break;
                }
            }
        }
        
        if (show && globalTerms.length > 0) {
            // Busca global: OR entre termos, testa primeiro no searchable-content
            let match = globalTerms.some(t => entry.search.includes(t));
 
            if (!match && selectedGlobalFilterPhysicalIndices?.length > 0) {
                match = selectedGlobalFilterPhysicalIndices.some(colIndex =>
                    globalTerms.some(t => (entry.cells[colIndex] || '').includes(t))
                );
            }
 
            show = match;
        }
 
        entry.row.style.display = show ? '' : 'none';
        if (show) visibleCount++;
    }

    // Atualizar contador e label de filtro
    const totalRows = _rowIndex.length;
    const filterTerm = individualFiltersActive
        ? 'Filtro de Coluna Ativo'
        : (globalTerms.length > 1 ? globalTerms.join(' | ') : globalSearchRaw);

    updateFilterInfo(visibleCount, totalRows, filterTerm);
    calcularTotalizadores();
}

function clearSearch() {
    const global = document.getElementById('globalSearch');
    if (global) global.value = '';

    // Clear column filters
    document.querySelectorAll('.column-filter-individual').forEach(i => i.value = '');
    columnFilterValues = {};

    applyFilters();
}

// 4. ORDENAÇÃO E REDIMENSIONAMENTO

document.addEventListener('DOMContentLoaded', function() {
    // Sorting listeners are already present in the HTML (data-column attributes)
    document.querySelectorAll('#tabelaAvarias th.sortable').forEach(th => {
        th.addEventListener('click', function(e) {
            if (e.target.classList.contains('column-resizer')) return;
            
            // data-column is the logical index (1 to 17)
            const dataColIndex = parseInt(this.getAttribute('data-column')); 
            sortTable(dataColIndex);
        });
    });
});

function sortTable(dataColIndex) {
    const tbody = document.querySelector('#tabelaAvarias tbody');
    if (!tbody) return;
 
    // Pega TODAS as linhas — sem filtrar por cells.length para não perder nada
    const allRows = Array.from(tbody.querySelectorAll('tr'));
 
    // Separa linhas sortáveis (dados reais) de linhas auxiliares (empty state, etc.)
    const sortableRows = allRows.filter(r => r.cells.length > 2);
    const auxiliaryRows = allRows.filter(r => r.cells.length <= 2);
 
    // Alternar direção: primeiro clique = desc (maior→menor), segundo = asc
    sortDirection[dataColIndex] = (sortDirection[dataColIndex] === 'desc') ? 'asc' : 'desc';
    const isAsc = sortDirection[dataColIndex] === 'asc';
 
    // ── CORREÇÃO DO OFF-BY-ONE ───────────────────────────────────────────────
    // data-column no HTML começa em 1 para Cód.Empresa (physicalIndex 2 no DOM).
    // Checkbox está em cells[0], Ações em cells[1], dados a partir de cells[2].
    // Logo: cellIndex correto = dataColIndex + 1
    const cellIndex = dataColIndex + 1;
 
    sortableRows.sort((a, b) => {
        const valA = a.cells[cellIndex]?.textContent.trim() || '';
        const valB = b.cells[cellIndex]?.textContent.trim() || '';
 
        // Ordenação por data (DD/MM/YYYY ou YYYY-MM-DD)
        const isDateA = valA.match(/^\d{2}\/\d{2}\/\d{4}/) || valA.match(/^\d{4}-\d{2}-\d{2}/);
        if (isDateA) {
            const toSortable = v => v.includes('/')
                ? v.split('/').reverse().join('')
                : v.replace(/[^\d]/g, '').substring(0, 8);
            return isAsc
                ? toSortable(valA).localeCompare(toSortable(valB))
                : toSortable(valB).localeCompare(toSortable(valA));
        }
 
        // Ordenação numérica (inclui moeda BR: "R$ 1.234,56")
        const numA = parseFloat(valA.replace('R$', '').replace(/\./g, '').replace(',', '.').trim());
        const numB = parseFloat(valB.replace('R$', '').replace(/\./g, '').replace(',', '.').trim());
        if (!isNaN(numA) && !isNaN(numB)) {
            return isAsc ? numA - numB : numB - numA;
        }
 
        // Ordenação textual
        return isAsc
            ? valA.localeCompare(valB, 'pt-BR')
            : valB.localeCompare(valA, 'pt-BR');
    });
 
    // Reinserir: primeiro as linhas auxiliares (empty state), depois as sortáveis
    // Isso garante que NENHUMA linha fica fora da ordem no tbody
    auxiliaryRows.forEach(r => tbody.appendChild(r));
    sortableRows.forEach(r => tbody.appendChild(r));
 
    // Atualizar ícones visuais
    document.querySelectorAll('#tabelaAvarias th .sort-icon').forEach(icon => {
        icon.className = 'fas fa-sort sort-icon';
    });
    const activeTh = document.querySelector(`#tabelaAvarias th[data-column="${dataColIndex}"] .sort-icon`);
    if (activeTh) {
        activeTh.className = `fas fa-sort-${isAsc ? 'up' : 'down'} sort-icon`;
    }
 
    // ── CORREÇÃO CRÍTICA: reaplica filtros após reordenar o DOM ─────────────
    // Sem isso, linhas com display:none ficam intercaladas entre as visíveis
    // após o reorder, fazendo o sort parecer errado visualmente.
    // O índice (_rowIndex) continua válido pois os objetos <tr> são os mesmos.
    applyFilters();
}

function updateSortIcons() {
    const headers = document.querySelectorAll('#tabelaAvarias th');
    headers.forEach((th, index) => {
        const icon = th.querySelector('.sort-icon');
        if (icon) {
            icon.className = 'fas fa-sort sort-icon float-end';
            if (index === sortDirection.col) {
                icon.className = `fas fa-sort-${sortDirection.dir === 'asc' ? 'up' : 'down'} sort-icon float-end`;
            }
        }
    });
}

function initializeColumnResize() {
    document.querySelectorAll('.column-resizer').forEach((resizer) => {
        const th = resizer.parentElement;
        const thIndex = Array.from(th.parentNode.children).indexOf(th); // Physical index
        
        resizer.addEventListener('mousedown', function(e) {
            e.preventDefault(); e.stopPropagation();
            resizingColumn = th;
            columnIndex = thIndex; 
            startX = e.pageX;
            startWidth = resizingColumn.offsetWidth;
            resizer.classList.add('resizing');
            document.addEventListener('mousemove', handleResizeMove);
            document.addEventListener('mouseup', handleResizeUp);
        });
    });
}

function handleResizeMove(e) {
    if (!resizingColumn) return;
    const minWidth = 80;
    const w = startWidth + (e.pageX - startX);
    if (w > minWidth) {
        resizingColumn.style.width = w + 'px';
        const table = document.getElementById('tabelaAvarias');
        // Apply width to the filter cell too
        const filterRow = document.getElementById('filterRow');
        if (filterRow && filterRow.cells[columnIndex]) {
            filterRow.cells[columnIndex].style.width = w + 'px';
        }
        // Optional: Apply to all body cells if table-layout: fixed is insufficient
    }
}

function handleResizeUp() {
    if(resizingColumn) resizingColumn.querySelector('.column-resizer').classList.remove('resizing');
    resizingColumn = null;
    document.removeEventListener('mousemove', handleResizeMove);
    document.removeEventListener('mouseup', handleResizeUp);
}

