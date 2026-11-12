class AdvancedTable {
    constructor(tableId, options = {}) {
        this.tableId = tableId;
        this.table = document.getElementById(tableId);
        if (!this.table) {
            console.error(`Tabela ${tableId} não encontrada`);
            return;
        }
        
        // NOVO: Tentar obter lista de colunas completa do atributo data-advanced-columns (JSON)
        const columnsData = this.table.getAttribute('data-advanced-columns');
        let externalColumns = null;
        if (columnsData) {
            try {
                // Tenta fazer o parse do JSON
                externalColumns = JSON.parse(columnsData);
            } catch (e) {
                console.error(`Erro ao parsear data-advanced-columns [${columnsData}]:`, e);
            }
        }
        
        this.options = {
            enableGlobalFilter: true,
            enableColumnFilters: true,
            enableSort: true,
            enableResize: true,
            enableReorder: true,
            saveState: true,
            minColumnWidth: 100,
            allColumns: options.allColumns || externalColumns, // Usa lista externa se fornecida
            ...options
        };
        
        this.state = {
            globalFilter: '',
            columnFilters: {},
            sortColumn: null,
            sortDirection: 'asc',
            columnWidths: {},
            columnOrder: [],
            selectedColumns: []
        };
        
        this.init();
    }
    
    isStorageAvailable() {
        if (this._storageAvailable !== undefined) return this._storageAvailable;
        try {
            const test = '__wms_test__';
            localStorage.setItem(test, test);
            localStorage.removeItem(test);
            this._storageAvailable = true;
        } catch (e) {
            this._storageAvailable = false;
        }
        return this._storageAvailable;
    }

    // ==================== INICIALIZAÇÃO ====================
    
    init() {
        console.log(`⎇ Inicializando AdvancedTable para #${this.tableId}`);
        
        // Carregar estado salvo
        if (this.options.saveState) {
            this.loadState();
        }
        
        // Inicializar funcionalidades que criam o DOM (Filtros)
        if (this.options.enableGlobalFilter) {
            this.initGlobalFilter();
        }
        
        if (this.options.enableColumnFilters) {
            this.initColumnFilters();
        }
        
        // Aplicar estado (larguras, filtros iniciais, etc.)
        this.applyState(); 
        
        if (this.options.enableSort) {
            this.initSort();
        }
        
        if (this.options.enableResize) {
            this.initResize();
        }
        
        if (this.options.enableReorder) {
            this.initReorder();
        }
        
        console.log('AdvancedTable inicializada');
    }
    
    // ==================== GESTÃO DE ESTADO (loadState/applyState/applyColumnWidths) ====================

    loadState() {
        if (!this.isStorageAvailable()) return;
        const stateKey = `advancedTable_${this.tableId}`;
        const savedState = localStorage.getItem(stateKey);
        if (savedState) {
            try {
                const parsedState = JSON.parse(savedState);
                this.state = { 
                    ...this.state, 
                    ...parsedState,
                    columnWidths: parsedState.columnWidths || {}
                };
                console.log('Estado carregado:', this.state);
            } catch (e) {
                console.error('Erro ao carregar estado:', e);
                localStorage.removeItem(stateKey);
            }
        }
    }
    
    applyState() {
        // Aplicar largura de colunas salvas (FIX CRÍTICO)
        if (Object.keys(this.state.columnWidths).length > 0) {
            this.applyColumnWidths();
        }
        
        // Aplica filtros e ordenação se houver estado salvo
        this.applyFilters(); 

        // Se filtro global existe na UI (criado em initGlobalFilter)
        const globalInput = document.getElementById('globalSearch');
        if (globalInput) {
            globalInput.value = this.state.globalFilter;
        }

        // Se filtros de coluna existem na UI (criado em initColumnFilters)
        const filterInputs = this.table.querySelectorAll('.column-filter');
        filterInputs.forEach(input => {
            const index = input.getAttribute('data-column');
            if (this.state.columnFilters[index]) {
                input.value = this.state.columnFilters[index];
            }
        });
        
        // Carregar seleção de colunas do filtro global
        const checkboxes = document.querySelectorAll(`#${this.tableId}-global-filter input[type="checkbox"]`);
        if (this.state.selectedColumns && checkboxes.length > 0) {
            this.loadColumnSelectionState(checkboxes);
        }
    }

    applyColumnWidths() {
        const thead = this.table.querySelector('thead');
        const headerRows = thead.querySelectorAll('tr'); 
        
        headerRows.forEach(row => {
            const ths = row.querySelectorAll('th');
            ths.forEach((th, index) => {
                const width = this.state.columnWidths[index];
                if (width) {
                    th.style.width = `${width}px`;
                    th.style.minWidth = `${width}px`;
                }
            });
        });
        console.log('◐ Larguras de colunas salvas aplicadas.');
    }

    saveState() {
        if (!this.options.saveState) return;
        
        const stateKey = `advancedTable_${this.tableId}`;
        
        // Capturar seleção atual de colunas
        const selectedCheckboxes = document.querySelectorAll(`#${this.tableId}-global-filter input[type="checkbox"]:checked`);
        this.state.selectedColumns = Array.from(selectedCheckboxes).map(chk => chk.value);

        if (this.isStorageAvailable()) {
            localStorage.setItem(stateKey, JSON.stringify(this.state));
        }
    }
    
    // ==================== REDIMENSIONAMENTO (Foco do FIX) ====================

    initResize() {
        // Injetar CSS básico necessário para o resizer funcionar visualmente
        const style = document.createElement('style');
        style.textContent = `
            .col-resizer {
                position: absolute;
                top: 0;
                right: -4px;
                width: 8px;
                height: 100%;
                cursor: col-resize;
                z-index: 10;
            }
            .advanced-table.resizing {
                user-select: none;
            }
        `;
        document.head.appendChild(style);
        
        const thead = this.table.querySelector('thead');
        // Apenas a primeira linha de headers para adicionar resizers
        const headers = Array.from(thead.querySelectorAll('tr:first-child th'));
        
        headers.forEach((th, index) => {
            th.style.position = 'relative'; // Necessário para posicionar o resizer
            
            const resizer = document.createElement('div');
            resizer.className = 'col-resizer';
            resizer.setAttribute('data-column-index', index);
            th.appendChild(resizer);
            
            resizer.addEventListener('mousedown', this.handleMouseDown.bind(this));
        });
        
        // Variáveis de estado do arrasto
        this.resizing = false;
        this.startX = 0;
        this.currentTh = null;
        this.columnIndex = null;
        this.startWidth = 0;
        
        // Eventos globais para o arrasto (devem ser adicionados ao document)
        document.addEventListener('mousemove', this.handleMouseMove.bind(this));
        document.addEventListener('mouseup', this.handleMouseUp.bind(this));
        
        console.log('Redimensionamento de colunas ativado');
    }

    handleMouseDown(e) {
        if (e.target.classList.contains('col-resizer')) {
            this.resizing = true;
            this.startX = e.clientX;
            this.currentTh = e.target.closest('th');
            this.columnIndex = parseInt(e.target.getAttribute('data-column-index'));
            this.startWidth = this.currentTh.offsetWidth;
            
            this.table.classList.add('resizing');
            e.preventDefault(); 
        }
    }
    
    handleMouseMove(e) {
        if (!this.resizing) return;
        
        const deltaX = e.clientX - this.startX;
        const newWidth = Math.max(this.startWidth + deltaX, this.options.minColumnWidth);
        
        // Aplicar largura ao TH da primeira linha
        this.currentTh.style.width = `${newWidth}px`;
        this.currentTh.style.minWidth = `${newWidth}px`;
        
        // Também aplicar nas células correspondentes da linha de filtro (se existir)
        const filterRow = this.table.querySelector('.filter-row');
        if (filterRow && filterRow.cells[this.columnIndex]) {
             filterRow.cells[this.columnIndex].style.width = `${newWidth}px`;
             filterRow.cells[this.columnIndex].style.minWidth = `${newWidth}px`;
        }
    }
    
    handleMouseUp() {
        if (!this.resizing) return;
        
        this.resizing = false;
        this.table.classList.remove('resizing');
        
        // PASSO CRÍTICO: SALVAR A LARGURA
        if (this.currentTh) {
            const newWidth = this.currentTh.offsetWidth;
            this.state.columnWidths[this.columnIndex] = newWidth;
            
            console.log(`Coluna ${this.columnIndex} redimensionada para ${newWidth}px. Salvando estado.`);
            this.saveState(); // Salva o estado atualizado (incluindo columnWidths)
        }
        
        this.currentTh = null;
        this.columnIndex = null;
    }
    
    // ==================== FILTRO GLOBAL ====================
    
    initGlobalFilter() {
        // Criar container de filtro global se não existir
        let container = document.getElementById(`${this.tableId}-global-filter`);
        if (!container) {
            container = this.createGlobalFilterUI();
        }
        
        if (!container) return; // Se a criação da UI falhar

        const input = container.querySelector('input[type="text"]');
        const columnSelect = container.querySelector('select');
        
        if (input) {
            input.addEventListener('input', (e) => {
                this.state.globalFilter = e.target.value;
                this.applyFilters();
                this.saveState();
            });
        }
        
        if (columnSelect) {
            columnSelect.addEventListener('change', () => {
                this.applyFilters();
                this.saveState(); // Salvar seleção de colunas
            });
        }
    }
    
    createGlobalFilterUI() {
        const thead = this.table.querySelector('thead');
        if (!thead) {
            console.error(`thead não encontrado na tabela #${this.tableId}`);
            return null;
        }
        
        const container = document.createElement('div');
        container.id = `${this.tableId}-global-filter`;
        container.className = 'advanced-table-global-filter mb-3';
        
        // Obter linha de cabeçalho principal
        let headerRow = thead.querySelector('tr:not(.filter-row)');
        if (!headerRow) {
             headerRow = thead.querySelector('tr');
             if (!headerRow) {
                 console.error(`Não foi possível encontrar a linha de cabeçalho principal na tabela #${this.tableId}`);
                 return null;
             }
        }
        
        let columnInfo;
        
        // NOVO: Priorizar lista externa se existir (para incluir colunas ocultas no filtro)
        if (this.options.allColumns && Array.isArray(this.options.allColumns)) {
            // Mapeia nomes fornecidos externamente (assumindo que a ordem corresponde ao índice da célula)
             columnInfo = this.options.allColumns.map((name, i) => ({
                name: String(name).trim() || `Coluna ${i + 1}`,
                index: i
            }));
            console.log(`ℹAdvancedTable: Usando lista externa de ${columnInfo.length} colunas para filtro.`);

        } else {
            // Lógica antiga: Usar headers do DOM
            const headers = Array.from(headerRow.querySelectorAll('th'));
            columnInfo = headers.map((th, i) => ({
                name: th.textContent.trim() || `Coluna ${i + 1}`,
                index: i
            })); 
        }

        const selectSize = Math.min(columnInfo.length + 1, 5); // Max 5 visible options
        
        container.innerHTML = `
            <div class="row g-3">
                <!-- Campo de Busca Global -->
                <div class="col-md-8">
                    <label for="globalSearch" class="form-label">
                        <i class="fas fa-search me-1"></i>Busca Rápida
                    </label>
                    <input type="text" class="form-control" id="globalSearch" placeholder="Digite para buscar em tempo real (ID, Nota, Status, Transportadora, etc)..." autocomplete="off">
                    <small class="form-text text-muted">
                        A busca é realizada nas colunas selecionadas abaixo
                    </small>
                </div>
                
                <!-- Seletor de Colunas para Busca -->
                <div class="col-md-4">
                    <label class="form-label">
                        <i class="fas fa-columns me-1"></i>Filtrar em Colunas
                    </label>
                    <div class="columns-dropdown">
                        <div class="form-control columns-dropdown-toggle d-flex justify-content-between align-items-center" id="columnsDropdownToggle" aria-expanded="false">
                            <span id="columnsSelectedText">Todas as colunas</span>
                            <i class="fas fa-chevron-down"></i>
                        </div>
                        
                    </div>
                    <small class="form-text text-muted">
                        Clique para selecionar colunas
                    </small>
                </div>
                
            <!-- Botão de Limpar Busca -->
            <div class="row mt-3">
                <div class="col-12">
                    <button type="button" class="btn btn-outline-secondary btn-sm" onclick="clearSearch()">
                        <i class="fas fa-times me-2"></i>Limpar Busca
                    </button>
                    <span id="filterInfo" class="ms-3 text-muted small"></span>
                </div>
            </div>
            </div>
        `;
        
        // Lógica de filtro global do novo UI (assumindo que a lógica de filtro é baseada no input #globalSearch)
        const globalInput = container.querySelector('#globalSearch');
        if (globalInput) {
            globalInput.addEventListener('input', (e) => {
                this.state.globalFilter = e.target.value;
                this.applyFilters();
                this.saveState();
            });
        }

        // Inicializar o dropdown customizado (assumindo que a lógica de toggle e checkbox está no escopo global)
        this.initializeCustomDropdown(columnInfo);
        
        this.table.parentNode.insertBefore(container, this.table);
        return container;
    }
    
    initializeCustomDropdown(columnInfo) {
        const toggle = document.getElementById('columnsDropdownToggle');
        const menu = document.getElementById('columnsDropdownMenu');
        const checkboxList = document.getElementById('columnsCheckboxList');
        
        if (!toggle || !menu || !checkboxList) return;

        // 1. Preencher checkboxes
        checkboxList.innerHTML = '';
        columnInfo.forEach(info => {
            const div = document.createElement('div');
            div.className = 'column-checkbox';
            div.innerHTML = `
                <input type="checkbox" id="col_physical_${info.index}" value="${info.index}">
                <label for="col_physical_${info.index}">${info.name}</label>
            `;
            checkboxList.appendChild(div);
        });
        
        // 2. Inicializar listeners de checkbox
        const checkboxes = checkboxList.querySelectorAll('input[type="checkbox"]');
        checkboxes.forEach(checkbox => {
            checkbox.addEventListener('change', () => {
                this.updateSelectedColumnsText();
                this.applyFilters();
                this.saveState();
            });
        });
        
        // 3. Toggle UI
        toggle.addEventListener('click', () => {
            const isExpanded = toggle.getAttribute('aria-expanded') === 'true' || false;
            toggle.setAttribute('aria-expanded', !isExpanded);
            menu.style.display = isExpanded ? 'none' : 'flex';
        });
        
        // 4. Carregar estado de colunas selecionadas
        this.loadColumnSelectionState(checkboxes);
    }

    updateSelectedColumnsText() {
        const selectedCheckboxes = document.querySelectorAll(`#${this.tableId}-global-filter input[type="checkbox"]:checked`);
        const allCheckboxes = document.querySelectorAll(`#${this.tableId}-global-filter input[type="checkbox"]`);
        const selectedText = document.getElementById('columnsSelectedText');
        
        if (!selectedText) return;

        if (selectedCheckboxes.length === allCheckboxes.length && allCheckboxes.length > 0) {
            selectedText.textContent = 'Todas as colunas';
        } else if (selectedCheckboxes.length === 0) {
            selectedText.textContent = 'Nenhuma coluna';
        } else {
            selectedText.textContent = `${selectedCheckboxes.length} coluna(s) selecionada(s)`;
        }
    }
    
    loadColumnSelectionState(checkboxes) {
        if (!this.state.selectedColumns || !checkboxes.length) return;
        
        checkboxes.forEach(chk => {
            if (this.state.selectedColumns.includes(chk.value)) {
                chk.checked = true;
            }
        });
        this.updateSelectedColumnsText();
    }

    // Sobrescreve o estado que é salvo e carregado para incluir a seleção de colunas
    saveState() {
        if (!this.options.saveState) return;
        
        const stateKey = `advancedTable_${this.tableId}`;
        
        // Capturar seleção atual de colunas
        const selectedCheckboxes = document.querySelectorAll(`#${this.tableId}-global-filter input[type="checkbox"]:checked`);
        this.state.selectedColumns = Array.from(selectedCheckboxes).map(chk => chk.value);

        if (this.isStorageAvailable()) {
            localStorage.setItem(stateKey, JSON.stringify(this.state));
        }
    }

    // ==================== FILTROS POR COLUNA ====================
    
    initColumnFilters() {
        const thead = this.table.querySelector('thead');
        const headerRow = thead.querySelector('tr');
        
        // Criar linha de filtros se não existir
        let filterRow = thead.querySelector('.filter-row');
        if (!filterRow) {
            filterRow = document.createElement('tr');
            filterRow.className = 'filter-row';
            headerRow.after(filterRow);
        }
        
        // Adicionar input de filtro para cada coluna
        const headers = headerRow.querySelectorAll('th');
        headers.forEach((th, index) => {
            const filterCell = document.createElement('th');
            filterCell.innerHTML = `
                <input type="text" 
                       class="form-control form-control-sm column-filter" 
                       data-column="${index}"
                       placeholder="Filtrar..."
                       value="${this.state.columnFilters[index] || ''}">
            `;
            
            const input = filterCell.querySelector('input');
            input.addEventListener('input', (e) => {
                this.state.columnFilters[index] = e.target.value;
                this.applyFilters();
                this.saveState();
            });
            
            filterRow.appendChild(filterCell);
        });
    }
    
    // ==================== APLICAR FILTROS ====================
    
    applyFilters() {
        const tbody = this.table.querySelector('tbody');
        const rows = Array.from(tbody.querySelectorAll('tr'));
        
        // Recuperar seleção de colunas do menu customizado
        const selectedColumnValues = Array.from(document.querySelectorAll(`#${this.tableId}-global-filter input[type="checkbox"]:checked`)).map(opt => opt.value);
        
        const globalFilterValue = this.state.globalFilter.toLowerCase();
        const useAllColumns = selectedColumnValues.includes('all') || selectedColumnValues.length === 0;
        
        rows.forEach(row => {
            let showRow = true;
            
            // Aplicar filtro global
            if (globalFilterValue) {
                const globalMatch = Array.from(row.cells).some((cell, index) => {
                    // Verifica se a coluna é selecionada ou se 'all' está selecionado
                    const columnIndexStr = String(index);
                    const isColumnSelected = selectedColumnValues.includes(columnIndexStr);

                    if (useAllColumns || isColumnSelected) {
                        const text = cell.textContent.toLowerCase();
                        return text.includes(globalFilterValue);
                    }
                    return false;
                });
                
                if (!globalMatch) {
                    showRow = false;
                }
            }
            
            // Aplicar filtros de coluna
            if (showRow && Object.keys(this.state.columnFilters).length > 0) {
                Object.entries(this.state.columnFilters).forEach(([colIndex, filterValue]) => {
                    if (filterValue && row.cells[colIndex]) {
                        const cellText = row.cells[colIndex].textContent.toLowerCase();
                        if (!cellText.includes(filterValue.toLowerCase())) {
                            showRow = false;
                        }
                    }
                });
            }
            
            row.style.display = showRow ? '' : 'none';
        });
        
        this.updateVisibleCount();
    }
    
    // ==================== REDIMENSIONAMENTO (O NOVO CÓDIGO) ====================

    initResize() {
        // Injetar CSS básico
        const style = document.createElement('style');
        style.textContent = `
            .col-resizer {
                position: absolute;
                top: 0;
                right: -4px;
                width: 8px;
                height: 100%;
                cursor: col-resize;
                z-index: 10;
            }
            .advanced-table.resizing {
                user-select: none;
            }
        `;
        document.head.appendChild(style);
        
        const thead = this.table.querySelector('thead');
        // Apenas a primeira linha de headers para adicionar resizers
        const headers = Array.from(thead.querySelectorAll('tr:first-child th'));
        
        headers.forEach((th, index) => {
            th.style.position = 'relative'; // Necessário para posicionar o resizer
            
            const resizer = document.createElement('div');
            resizer.className = 'col-resizer';
            resizer.setAttribute('data-column-index', index);
            th.appendChild(resizer);
            
            resizer.addEventListener('mousedown', this.handleMouseDown.bind(this));
        });
        
        // Variáveis de estado do arrasto
        this.resizing = false;
        this.startX = 0;
        this.currentTh = null;
        this.columnIndex = null;
        this.startWidth = 0;
        
        // Eventos globais para o arrasto (devem ser adicionados ao document)
        document.addEventListener('mousemove', this.handleMouseMove.bind(this));
        document.addEventListener('mouseup', this.handleMouseUp.bind(this));
        
        console.log('Redimensionamento de colunas ativado');
    }

    handleMouseDown(e) {
        if (e.target.classList.contains('col-resizer')) {
            this.resizing = true;
            this.startX = e.clientX;
            this.currentTh = e.target.closest('th');
            this.columnIndex = parseInt(e.target.getAttribute('data-column-index'));
            this.startWidth = this.currentTh.offsetWidth;
            
            this.table.classList.add('resizing');
            e.preventDefault(); 
        }
    }
    
    handleMouseMove(e) {
        if (!this.resizing) return;
        
        const deltaX = e.clientX - this.startX;
        const newWidth = Math.max(this.startWidth + deltaX, this.options.minColumnWidth);
        
        // Aplicar largura ao TH da primeira linha
        this.currentTh.style.width = `${newWidth}px`;
        this.currentTh.style.minWidth = `${newWidth}px`;
        
        // Também aplicar nas células correspondentes da linha de filtro (se existir)
        const filterRow = this.table.querySelector('.filter-row');
        if (filterRow && filterRow.cells[this.columnIndex]) {
             filterRow.cells[this.columnIndex].style.width = `${newWidth}px`;
             filterRow.cells[this.columnIndex].style.minWidth = `${newWidth}px`;
        }
    }
    
    handleMouseUp() {
        if (!this.resizing) return;
        
        this.resizing = false;
        this.table.classList.remove('resizing');
        
        // PASSO CRÍTICO: SALVAR A LARGURA
        if (this.currentTh) {
            const newWidth = this.currentTh.offsetWidth;
            this.state.columnWidths[this.columnIndex] = newWidth;
            
            console.log(`Coluna ${this.columnIndex} redimensionada para ${newWidth}px. Salvando estado.`);
            this.saveState(); // Salva o estado atualizado (incluindo columnWidths)
        }
        
        this.currentTh = null;
        this.columnIndex = null;
    }
    
    // ==================== FUNÇÕES DE RESET ====================
    
    clearSearch() {
        const input = document.getElementById('globalSearch');
        if (input) input.value = '';
        
        this.state.globalFilter = '';
        this.state.columnFilters = {};
        
        // Resetar checkboxes
        document.querySelectorAll(`#${this.tableId}-global-filter input[type="checkbox"]`).forEach(chk => {
            chk.checked = true; // Resetar para 'Todas as colunas'
        });
        
        this.updateSelectedColumnsText();
        this.applyFilters();
        this.saveState();
    }

    selectAllColumns() {
        document.querySelectorAll(`#${this.tableId}-global-filter input[type="checkbox"]`).forEach(chk => {
            chk.checked = true;
        });
        this.updateSelectedColumnsText();
        this.applyFilters();
        this.saveState();
    }

    deselectAllColumns() {
        document.querySelectorAll(`#${this.tableId}-global-filter input[type="checkbox"]`).forEach(chk => {
            chk.checked = false;
        });
        // Desmarcar o 'all' se ele existir
        const allCheckbox = document.querySelector(`#${this.tableId}-global-filter input[value="all"]`);
        if (allCheckbox) allCheckbox.checked = false;

        this.updateSelectedColumnsText();
        this.applyFilters();
        this.saveState();
    }

    // ==================== RESET GERAL ====================
    
    reset() {
        this.state = {
            globalFilter: '',
            columnFilters: {},
            sortColumn: null,
            sortDirection: 'asc',
            columnWidths: {},
            columnOrder: []
        };
        
        this.saveState();
        location.reload();
    }
}

// ==================== EXPOR GLOBALMENTE ====================
window.AdvancedTable = AdvancedTable;

// ==================== AUTO-INICIALIZAÇÃO ====================
document.addEventListener('DOMContentLoaded', function() {
    // Inicializar automaticamente tabelas com classe 'advanced-table'
    document.querySelectorAll('table.advanced-table').forEach(table => {
        // Definir uma variável global de fácil acesso para testes/debug
        window.advancedTableInstance = new AdvancedTable(table.id);
    });
});

// Função global para limpar busca (chamada pelo HTML de exemplo)
window.clearSearch = function() {
    const tableInstance = window.advancedTableInstance; // Assumindo que existe uma instância global para a tabela de avarias
    if (tableInstance && tableInstance.clearSearch) {
        tableInstance.clearSearch();
    } else {
        console.error('AdvancedTable instance not found globally.');
    }
};

window.selectAllColumns = function() {
    const tableInstance = window.advancedTableInstance; 
    if (tableInstance && tableInstance.selectAllColumns) {
        tableInstance.selectAllColumns();
    } 
};

window.deselectAllColumns = function() {
    const tableInstance = window.advancedTableInstance; 
    if (tableInstance && tableInstance.deselectAllColumns) {
        tableInstance.deselectAllColumns();
    } 
};
