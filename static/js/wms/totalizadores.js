/**
 * TOTALIZADORES JS — Cálculo e exibição de totais nas tabelas WMS
 * 
 * Calcula somas de valores monetários e quantidades nas tabelas,
 * respeitando linhas filtradas/ocultas e agrupamentos com rowspan.
 * Atualiza automaticamente ao filtrar, ordenar ou alterar visibilidade.
 * 
 * DEPENDÊNCIAS: avarias-common.js (formatCurrency, formatInteger, parseDecimalValue)
 * 
 * USO no template:
 *   <!-- Container (inserir ABAIXO da tabela, dentro do card) -->
 *   <div id="totalizadoresContainer" class="totalizadores-container"></div>
 *   
 *   <script>
 *     // Após a tabela estar renderizada:
 *     Totalizadores.init('tabelaAvariasPendentes', 'totalizadoresContainer');
 *     // ou
 *     Totalizadores.init('tabelaAvarias', 'totalizadoresContainer');
 *   </script>
 */

const Totalizadores = (function() {
    'use strict';

    // CONFIGURAÇÃO DOS CAMPOS
    
    /**
     * Campos que devem ser somados.
     * `selector` indica como encontrar o valor na célula:
     *   - data-field: busca por atributo data-field na célula
     *   - data-column-name: busca por nome da coluna no header
     *   - index: busca por índice fixo da coluna
     */
    const CAMPOS_SOMA = [
        { 
            key: 'valor_nf', 
            label: 'Valor NF', 
            formato: 'moeda',
            selectors: ['data-field=valor_nf', 'column-name=Valor NF']
        },
        { 
            key: 'valor_nfd', 
            label: 'Valor NFD', 
            formato: 'moeda',
            selectors: ['data-field=valor_nfd', 'column-name=Valor NFD']
        },
        { 
            key: 'valor_nfp', 
            label: 'Valor NFP', 
            formato: 'moeda',
            selectors: ['data-field=valor_nfp', 'column-name=Valor NFP']
        },
        { 
            key: 'valor_indenizacao', 
            label: 'Indenização', 
            formato: 'moeda',
            selectors: ['data-field=valor_indenizacao', 'column-name=Valor Indenização', 'column-name=Valor Indeni']
        },
        { 
            key: 'quantidade', 
            label: 'Qtd Total', 
            formato: 'inteiro',
            selectors: ['data-field=quantidade', 'column-name=Quantidade', 'column-name=Qtd Avariada']
        }
    ];

    // Cache de mapeamento coluna → índice (evita recalcular a cada atualização)
    let _columnIndexCache = {};
    let _lastTableId = null;
    let _observer = null;
    let _updateTimer = null;

    // MAPEAMENTO DE COLUNAS

    /**
     * Mapeia os campos configurados para índices reais da tabela.
     * Suporta múltiplas estratégias de localização.
     */
    function mapearColunas(tableId) {
        const table = document.getElementById(tableId);
        if (!table) return {};

        if (_lastTableId === tableId && Object.keys(_columnIndexCache).length > 0) {
            return _columnIndexCache;
        }

        const headers = table.querySelectorAll('thead th');
        const mapa = {};

        CAMPOS_SOMA.forEach(campo => {
            let foundIndex = -1;

            for (const selector of campo.selectors) {
                if (foundIndex >= 0) break;

                if (selector.startsWith('data-field=')) {
                    // Estratégia 1: buscar por data-field no tbody
                    const fieldName = selector.split('=')[1];
                    const celula = table.querySelector(`tbody td[data-field="${fieldName}"]`);
                    if (celula) {
                        foundIndex = celula.cellIndex;
                    }
                } else if (selector.startsWith('column-name=')) {
                    // Estratégia 2: buscar pelo nome do header
                    const colName = selector.split('=')[1].toLowerCase();
                    headers.forEach((th, idx) => {
                        const headerText = (th.textContent || '').trim().toLowerCase();
                        const colDataName = (th.dataset.columnName || '').toLowerCase();
                        if (headerText.includes(colName) || colDataName.includes(colName)) {
                            foundIndex = idx;
                        }
                    });
                }
            }

            if (foundIndex >= 0) {
                mapa[campo.key] = foundIndex;
            }
        });

        _columnIndexCache = mapa;
        _lastTableId = tableId;

        return mapa;
    }

    // CÁLCULO DE TOTAIS

    /**
     * Calcula totais baseado nas linhas VISÍVEIS da tabela.
     * Lida com rowspan (evita duplicação de registros).
     * 
     * @param {string} tableId - ID da tabela
     * @returns {Object} { campo: valor, ..., _registros: N, _visiveis: N }
     */
    function calcular(tableId) {
        const table = document.getElementById(tableId);
        if (!table) return { _registros: 0, _visiveis: 0 };

        const mapa = mapearColunas(tableId);
        const tbody = table.querySelector('tbody');
        if (!tbody) return { _registros: 0, _visiveis: 0 };

        const linhas = tbody.querySelectorAll('tr');
        const totais = {};
        CAMPOS_SOMA.forEach(c => { totais[c.key] = 0; });

        let registrosVisiveis = 0;
        let totalLinhas = 0;
        const idsProcessados = new Set();

        linhas.forEach(tr => {
            // Pular linhas ocultas (filtradas)
            if (tr.style.display === 'none' || tr.classList.contains('d-none')) return;
            
            // Verificar visibilidade computada
            const computedStyle = window.getComputedStyle(tr);
            if (computedStyle.display === 'none') return;

            totalLinhas++;

            // Evitar duplicação em linhas com rowspan (mesmo avaria_id)
            const avariaId = tr.dataset.avariaId;
            if (avariaId) {
                if (idsProcessados.has(avariaId)) {
                    // Para quantidade de itens, ainda somar
                    _somarCampoEspecifico(tr, mapa, totais, 'quantidade');
                    return;
                }
                idsProcessados.add(avariaId);
            }

            registrosVisiveis++;

            // Somar cada campo
            CAMPOS_SOMA.forEach(campo => {
                const colIdx = mapa[campo.key];
                if (colIdx === undefined || colIdx < 0) return;

                const celula = _getCelulaValor(tr, colIdx, campo.key);
                if (!celula) return;

                const valor = _extrairValor(celula);
                totais[campo.key] += valor;
            });
        });

        totais._registros = registrosVisiveis;
        totais._visiveis = totalLinhas;

        return totais;
    }

    // DEPOIS (CORRETO):
    function atualizarTotalizadores() {
        const tabela = document.getElementById('tabelaAvarias');
        const linhas = tabela.querySelectorAll('tbody tr:not([style*="display: none"])');
        
        let totalQuantidade = 0;
        let totalValorNFD = 0;
        let totalValorIndenizacao = 0;
        
        linhas.forEach(linha => {
            // cells[8] = Quantidade (checkbox + ações + 6 colunas vazias = índice 8)
            const quantidade = parseFloat(
                linha.cells[8]?.textContent?.trim().replace(/[^\d,.-]/g, '').replace(',', '.')
            ) || 0;
            totalQuantidade += quantidade;
            
            // cells[9] = Valor NFD
            const valorNFD = parseValorMonetario(linha.cells[9]?.textContent || 'R$ 0,00');
            totalValorNFD += valorNFD;
            
            // cells[15] = Valor Indenização
            const valorIndenizacao = parseValorMonetario(linha.cells[15]?.textContent || 'R$ 0,00');
            totalValorIndenizacao += valorIndenizacao;
        });
        
        // Atualizar elementos do tfoot
        document.getElementById('totalQuantidade').textContent = Math.round(totalQuantidade);
        document.getElementById('totalValorNFD').textContent = formatarMoeda(totalValorNFD);
        document.getElementById('totalValorIndenizacao').textContent = formatarMoeda(totalValorIndenizacao);
    }
    
    // Índices corretos, cálculos precisos.

    /**
     * Obtém célula com o valor, considerando rowspan.
     * Se a célula com o índice desejado não existe na linha (por causa de rowspan),
     * busca na linha que "possui" o rowspan.
     */
    function _getCelulaValor(tr, colIdx, fieldKey) {
        // Primeiro: tentar por data-field (mais confiável)
        const byField = tr.querySelector(`td[data-field="${fieldKey}"]`);
        if (byField) return byField;

        // Segundo: tentar pelo índice
        const cells = tr.querySelectorAll('td');
        if (colIdx < cells.length) {
            return cells[colIdx];
        }

        return null;
    }

    /**
     * Somar apenas um campo específico (usado para itens em rowspan)
     */
    function _somarCampoEspecifico(tr, mapa, totais, campoKey) {
        const colIdx = mapa[campoKey];
        if (colIdx === undefined) return;

        const celula = _getCelulaValor(tr, colIdx, campoKey);
        if (!celula) return;

        totais[campoKey] += _extrairValor(celula);
    }

    /**
     * Extrai valor numérico de uma célula
     */
    function _extrairValor(celula) {
        // Priorizar data-value (mais preciso)
        if (celula.dataset.value) {
            return parseDecimalValue(celula.dataset.value);
        }

        // Fallback: texto da célula
        let texto = celula.textContent || '';
        
        // Ignorar badges, ícones, etc.
        const span = celula.querySelector('span:not(.badge)');
        if (span) texto = span.textContent || '';

        return parseDecimalValue(texto);
    }

    // RENDERIZAÇÃO

    /**
     * Renderiza barra de totais no container especificado
     * 
     * @param {string} containerId - ID do container
     * @param {Object} totais - Resultado de calcular()
     */
    function renderizar(containerId, totais) {
        const container = document.getElementById(containerId);
        if (!container) return;

        let html = '<div class="d-flex flex-wrap gap-3 justify-content-start align-items-center ms-0">';
        html += '<span class="text-muted me-2" style="font-size:0.85rem;"><i class="fas fa-calculator me-1"></i>Totais:</span>';

        CAMPOS_SOMA.forEach(campo => {
            const valor = totais[campo.key] || 0;
            
            // Pular campos zerados para não poluir
            // (mas sempre mostrar se houver pelo menos 1 registro)
            if (valor === 0 && (totais._registros || 0) === 0) return;

            let formatado;
            if (campo.formato === 'moeda') {
                formatado = typeof formatCurrency === 'function' 
                    ? formatCurrency(valor)
                    : valor.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
            } else {
                formatado = typeof formatInteger === 'function'
                    ? formatInteger(valor)
                    : valor.toLocaleString('pt-BR');
            }

            html += `
                <span class="totalizador-badge">
                    <span class="totalizador-label">${campo.label}:</span>
                    <span class="totalizador-valor">${formatado}</span>
                </span>
            `;
        });

        // Badge de registros (sempre)
        html += `
            <span class="totalizador-badge badge-registros">
                <i class="fas fa-list-ol me-1"></i>
                <span class="totalizador-label" style="color:rgba(255,255,255,0.8)">Registros:</span>
                <span class="totalizador-valor">${totais._registros || 0}</span>
            </span>
        `;

        html += '</div>';
        container.innerHTML = html;
    }

    // INICIALIZAÇÃO

    /**
     * Inicializa o módulo: calcula, renderiza e observa mudanças.
     * 
     * @param {string} tableId - ID da tabela
     * @param {string} containerId - ID do container dos totais
     * @returns {Function} Função de atualização manual
     */
    function init(tableId, containerId) {
        // Limpar cache ao re-inicializar
        _columnIndexCache = {};
        _lastTableId = null;

        const atualizar = debounce(function() {
            const totais = calcular(tableId);
            renderizar(containerId, totais);
        }, 150);

        // Calcular ao carregar
        atualizar();

        // Observar mudanças na tabela (filtros, ordenação, visibilidade)
        const table = document.getElementById(tableId);
        if (table) {
            // Desconectar observer anterior
            if (_observer) _observer.disconnect();

            _observer = new MutationObserver(() => {
                // Debounce para evitar recalculos excessivos
                if (_updateTimer) clearTimeout(_updateTimer);
                _updateTimer = setTimeout(atualizar, 200);
            });

            _observer.observe(table, {
                childList: true,
                subtree: true,
                attributes: true,
                attributeFilter: ['style', 'class']
            });
        }

        // Também recalcular ao redimensionar (scroll pode afetar visibilidade)
        window.addEventListener('resize', debounce(atualizar, 500));

        if (typeof WMSLog !== 'undefined') {
            WMSLog.info(`📊 Totalizadores inicializados para #${tableId}`);
        }

        // Retornar função de atualização para uso externo
        return atualizar;
    }

    /**
     * Força recalculo e re-renderização
     * Útil após operações que o MutationObserver pode não detectar
     */
    function forcarAtualizacao(tableId, containerId) {
        _columnIndexCache = {};
        _lastTableId = null;
        const totais = calcular(tableId);
        renderizar(containerId, totais);
    }

    // API PÚBLICA

    return {
        CAMPOS_SOMA,
        init,
        calcular,
        renderizar,
        forcarAtualizacao,
        mapearColunas
    };
})();

// Exportar globalmente
window.Totalizadores = Totalizadores;
window.WMS = window.WMS || {};
window.WMS.Totalizadores = Totalizadores;

if (typeof WMSLog !== 'undefined') {
    WMSLog.info('✅ totalizadores.js carregado');
}
