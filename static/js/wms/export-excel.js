/**
 * EXPORT EXCEL - JavaScript (VERSÃO CORRIGIDA)
 * Correções aplicadas:
 * 1. Identificação correta de linhas header/item em Registrar Avarias
 * 2. Coleta adequada de dados de produtos independente de mesclagem
 * 3. Melhoria na formatação de filtros ativos
 */

const ExportExcel = (function() {
    'use strict';

    // CONFIGURAÇÃO
    
    let config = {
        tela: null,              // 'consultar' ou 'registrar'
        urlExport: null,         // URL do endpoint de exportação
        tabelaId: null,          // ID da tabela HTML
        columnDefs: null,        // Definições das colunas (COLUMN_DEFS)
        totalizadoresCallback: null  // Função para obter totalizadores (opcional)
    };
    
    // CONFIGURAÇÃO PÚBLICA
    
    /**
     * Configura o módulo de exportação
     * @param {Object} opcoes - Opções de configuração
     */
    function configurar(opcoes) {
        config = { ...config, ...opcoes };
        
        if (typeof WMSLog !== 'undefined') {
            WMSLog.info(`📊 ExportExcel configurado para tela: ${config.tela}`);
        }
    }
    
    // COLETA DE DADOS - CONSULTAR AVARIAS
    
    /**
     * Coleta dados filtrados da tabela de Consultar Avarias
     * @returns {Object} {dados, colunas_visiveis, filtros_ativos, totalizadores}
     */
    function coletarDadosConsultar() {
        const tabela = document.getElementById(config.tabelaId);
        if (!tabela) {
            throw new Error('Tabela não encontrada');
        }
        
        const tbody = tabela.querySelector('tbody');
        const rows = Array.from(tbody.querySelectorAll('tr'));
        
        // 1. Identificar colunas visíveis
        const colunasVisiveis = [];
        const indicesVisiveis = [];
        
        const headerThs = Array.from(tabela.querySelectorAll('thead tr:first-child th'));
        config.columnDefs.forEach((col, idx) => {
            const th = headerThs[col.physicalIndex]; // posição 0 = checkbox, 1 = ações, 2 = Cód. Empresa...
            if (th && th.style.display !== 'none') {
                colunasVisiveis.push({ name: col.name, key: col.key || `col_${idx}` });
                indicesVisiveis.push(col.physicalIndex);
            }
        });
        
        // 2. Coletar dados das linhas VISÍVEIS
        const dados = [];

        rows.forEach(row => {
            // Pular linhas ocultas (filtradas)
            if (row.style.display === 'none') {
                return;
            }

            // Incluir avaria_id de cada linha para enriquecimento com itens no backend
            const avariaId = row.getAttribute('data-avaria-id') || '';

            const registro = { avaria_id: avariaId };

            indicesVisiveis.forEach((colIdx, arrayIdx) => {
                const cell = row.cells[colIdx];
                const colKey = colunasVisiveis[arrayIdx].key;

                if (cell) {
                    registro[colKey] = extrairValorCelula(cell);
                }
            });

            // Coletar campos de data dos atributos data-* da linha (mais confiáveis que o texto exibido)
            registro['data_registro']    = row.getAttribute('data-data-registro')    || registro['data_registro']    || '';
            registro['data_pagamento']   = row.getAttribute('data-data-pagamento')   || registro['data_pagamento']   || '';
            registro['prazo_pagamento']  = row.getAttribute('data-prazo-pagamento')  || registro['prazo_pagamento']  || '';
            registro['data_finalizacao'] = row.getAttribute('data-data-finalizacao') || registro['data_finalizacao'] || '';

            dados.push(registro);
        });

        // 3. Coletar totalizadores (se disponível)
        let totalizadores = {};
        if (typeof calcularTotalizadoresConsultar === 'function') {
            try {
                totalizadores = calcularTotalizadoresConsultar();
            } catch (e) {
                console.warn('Erro ao calcular totalizadores:', e);
            }
        }

        // 4. Coletar filtros ativos com nomes de colunas
        const filtrosAtivos = coletarFiltrosAtivosMelhorados();

        return {
            dados,
            colunas_visiveis: colunasVisiveis,
            filtros_ativos: filtrosAtivos,
            totalizadores: totalizadores
        };
    }
    
    // COLETA DE DADOS - REGISTRAR AVARIAS
    
    /**
     * Coleta dados filtrados da tabela de Registrar Avarias (formato agrupado)
     * CORRIGIDO: Identifica header/item corretamente independente de mesclagem
     * @returns {Object} {dados, colunas_visiveis, totalizadores, filtros_ativos}
     */
    function coletarDadosRegistrar() {
        const tabela = document.getElementById(config.tabelaId);
        if (!tabela) {
            throw new Error('Tabela não encontrada');
        }
        
        const tbody = tabela.querySelector('tbody');
        const rows = Array.from(tbody.querySelectorAll('tr'));
        
        // 1. Identificar colunas visíveis e seus tipos
        const colunasVisiveis = [];
        const indicesVisiveis = [];
        
        config.columnDefs.forEach((col, idx) => {
            const th = tabela.querySelector(`thead th[data-column="${col.physicalIndex}"]`);
            if (th && th.style.display !== 'none') {
                colunasVisiveis.push({
                    name: col.name,
                    key: col.key || `col_${idx}`,
                    type: col.type || 'both',
                    physicalIndex: col.physicalIndex
                });
                indicesVisiveis.push(col.physicalIndex);
            }
        });
        
        // Separar colunas por tipo para facilitar
        const colunasHeader = colunasVisiveis.filter(c => c.type === 'header');
        const colunasBoth = colunasVisiveis.filter(c => c.type === 'both');
        const colunasItem = colunasVisiveis.filter(c => c.type === 'item');
        
        // 2. Agrupar dados
        const grupos = [];
        let grupoAtual = null;
        let ultimoAvariaId = null;
        let valoresHeaderAtual = {}; // ← Guardar valores do header
        
        rows.forEach(row => {
            // Pular linhas ocultas e linha de totais
            if (row.style.display === 'none' || row.id === 'totalRow') {
                return;
            }
            
            const avariaId = row.getAttribute('data-avaria-id');
            const isNovaAvaria = avariaId && avariaId !== ultimoAvariaId;
            
            if (isNovaAvaria) {
                // NOVA AVARIA - Primeira linha do grupo
                grupoAtual = {
                    avaria: {},
                    itens: []
                };
                
                valoresHeaderAtual = {}; // Reset
                
                // Coletar dados do header E both (nesta linha têm todas as células)
                indicesVisiveis.forEach((colIdx, arrayIdx) => {
                    const cell = row.cells[colIdx];
                    const colInfo = colunasVisiveis[arrayIdx];
                    
                    if (cell && (colInfo.type === 'header' || colInfo.type === 'both')) {
                        const valor = extrairValorCelula(cell);
                        grupoAtual.avaria[colInfo.key] = valor;
                        valoresHeaderAtual[colInfo.key] = valor; // ← Guardar
                    }
                });
                
                grupos.push(grupoAtual);
                ultimoAvariaId = avariaId;
            }
            
            // COLETAR ITEM (TODAS as linhas são itens, incluindo a primeira)
            if (grupoAtual) {
                const item = {};
                
                // ESTRATÉGIA PARA LINHAS SUBSEQUENTES (com rowspan):
                // As células mescladas NÃO existem, então row.cells tem menos elementos.
                // Precisamos identificar quais células realmente existem nesta linha.
                
                if (isNovaAvaria) {
                    // PRIMEIRA LINHA: usar índices normais
                    indicesVisiveis.forEach((colIdx, arrayIdx) => {
                        const cell = row.cells[colIdx];
                        const colInfo = colunasVisiveis[arrayIdx];
                        
                        if (colInfo.type === 'item' || colInfo.type === 'both') {
                            item[colInfo.key] = cell ? extrairValorCelula(cell) : '';
                        }
                    });
                    
                } else {
                    // LINHAS SUBSEQUENTES: 
                    // Reutilizar valores do header, pegar apenas células 'item'
                    
                    // 1. Copiar valores do header guardados
                    Object.keys(valoresHeaderAtual).forEach(key => {
                        item[key] = valoresHeaderAtual[key];
                    });
                    
                    // 2. Pegar valores das células 'item' que realmente existem
                    // Nessas linhas, row.cells só contém as células SEM rowspan
                    // que são as colunas tipo 'item'
                    
                    let cellIndex = 0; // Índice no array cells[] real
                    
                    colunasVisiveis.forEach(colInfo => {
                        if (colInfo.type === 'item') {
                            // Esta célula existe nesta linha
                            const cell = row.cells[cellIndex];
                            item[colInfo.key] = cell ? extrairValorCelula(cell) : '';
                            cellIndex++;
                        }
                        // Colunas 'header' e 'both' não existem nesta linha (rowspan)
                        // Não incrementamos cellIndex
                    });
                }
                
                grupoAtual.itens.push(item);
            }
        });
        
        // 3. Coletar totalizadores
        let totalizadores = {};
        if (config.totalizadoresCallback && typeof config.totalizadoresCallback === 'function') {
            totalizadores = config.totalizadoresCallback();
        }
        
        // 4. Coletar filtros ativos
        const filtrosAtivos = coletarFiltrosAtivosMelhorados();
        
        return {
            dados: grupos,
            colunas_visiveis: colunasVisiveis,
            totalizadores: totalizadores,
            filtros_ativos: filtrosAtivos
        };
    }
    
    // UTILITÁRIOS - COLETA DE FILTROS (MELHORADO)
    
    /**
     * Coleta filtros ativos com nome da coluna
     * Retorna objeto com formato "Nome da Coluna": "valor"
     */
    function coletarFiltrosAtivosMelhorados() {
        const filtros = {};
        
        // Buscar pelo global search
        const globalSearch = document.getElementById('globalSearchAvarias');
        if (globalSearch && globalSearch.value.trim()) {
            filtros['Busca Global'] = globalSearch.value.trim();
        }
        
        // Buscar filtros individuais por coluna
        const filterInputs = document.querySelectorAll('.column-filter, .column-filter-pending');
        filterInputs.forEach(input => {
            const valor = input.value.trim();
            if (valor) {
                const colIndex = input.getAttribute('data-column');
                
                // Buscar nome da coluna no columnDefs
                let nomeColuna = 'Filtro'; // Fallback
                
                if (config.columnDefs) {
                    const colDef = config.columnDefs.find(c => c.physicalIndex == colIndex);
                    if (colDef && colDef.name) {
                        nomeColuna = colDef.name;
                    }
                }
                
                filtros[nomeColuna] = valor;
            }
        });
        
        // Buscar filtros de empresa (se existir)
        const filtroCodEmpresa = document.getElementById('filtroCodEmpresa');
        if (filtroCodEmpresa && filtroCodEmpresa.value && filtroCodEmpresa.value !== 'TODAS') {
            filtros['Empresa'] = filtroCodEmpresa.value;
        }
        
        return filtros;
    }
    
    /**
     * Extrai valor limpo de uma célula
     */
    function extrairValorCelula(cell) {
        if (!cell) return '';
        
        // Priorizar badges
        const badge = cell.querySelector('.badge');
        if (badge) {
            return badge.textContent.trim();
        }
        
        // Priorizar links
        const link = cell.querySelector('a');
        if (link) {
            return link.textContent.trim();
        }
        
        // Texto limpo
        return cell.textContent.trim();
    }
    
    // EXPORTAÇÃO
    
    /**
     * Executa a exportação para Excel
     * @param {Function} onSuccess - Callback de sucesso (opcional)
     * @param {Function} onError - Callback de erro (opcional)
     */
    async function exportar(onSuccess, onError) {
        try {
            // Validar configuração
            if (!config.tela || !config.urlExport || !config.tabelaId) {
                throw new Error('ExportExcel não configurado corretamente');
            }
            
            // Mostrar loading
            mostrarLoading(true);
            
            // Coletar dados baseado no tipo de tela
            let payload;
            if (config.tela === 'consultar') {
                payload = coletarDadosConsultar();
            } else if (config.tela === 'registrar') {
                payload = coletarDadosRegistrar();
            } else {
                throw new Error(`Tipo de tela inválido: ${config.tela}`);
            }
            
            if (typeof WMSLog !== 'undefined') {
                if (config.tela === 'consultar') {
                    WMSLog.info(`📊 Exportando ${payload.dados.length} registros`);
                } else {
                    const totalItens = payload.dados.reduce((sum, g) => sum + g.itens.length, 0);
                    WMSLog.info(`📊 Exportando ${payload.dados.length} grupos com ${totalItens} itens`);
                }
            }
            
            // Fazer requisição
            const response = await fetch(config.urlExport, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            });
            
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || 'Erro ao exportar');
            }
            
            // Baixar arquivo
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            
            // Nome do arquivo a partir do header ou padrão
            const contentDisposition = response.headers.get('content-disposition');
            let filename = 'relatorio.xlsx';
            if (contentDisposition) {
                const filenameMatch = contentDisposition.match(/filename="?([^"]+)"?/);
                if (filenameMatch) {
                    filename = filenameMatch[1];
                }
            }
            
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
            
            // Esconder loading
            mostrarLoading(false);
            
            // Callback de sucesso
            if (onSuccess && typeof onSuccess === 'function') {
                onSuccess(filename);
            }
            
        } catch (error) {
            // Esconder loading
            mostrarLoading(false);
            
            // Callback de erro
            if (onError && typeof onError === 'function') {
                onError(error);
            } else {
                alert(`Erro ao exportar: ${error.message}`);
            }
            
            console.error('Erro na exportação:', error);
        }
    }
    
    // UI - LOADING
    
    function mostrarLoading(show) {
        let loader = document.getElementById('exportLoadingOverlay');
        
        if (show) {
            if (!loader) {
                loader = document.createElement('div');
                loader.id = 'exportLoadingOverlay';
                loader.innerHTML = `
                    <div style="
                        position: fixed;
                        top: 0;
                        left: 0;
                        width: 100%;
                        height: 100%;
                        background: rgba(0,0,0,0.7);
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        z-index: 9999;
                    ">
                        <div style="
                            background: white;
                            padding: 30px;
                            border-radius: 10px;
                            text-align: center;
                        ">
                            <div class="spinner-border text-success" role="status">
                                <span class="visually-hidden">Carregando...</span>
                            </div>
                            <p class="mt-3 mb-0">Gerando arquivo Excel...</p>
                        </div>
                    </div>
                `;
                document.body.appendChild(loader);
            }
            loader.style.display = 'block';
        } else {
            if (loader) {
                loader.style.display = 'none';
            }
        }
    }
    
    // API PÚBLICA
    
    return {
        configurar: configurar,
        exportar: exportar
    };
    
})();
