/**
 * AVARIAS COMMON JS — Utilitários compartilhados WMS
 * 
 * Funções reutilizáveis entre Registrar e Consultar Avarias.
 * DEVE ser carregado ANTES de qualquer outro JS do WMS.
 * 
 * USO no template:
 *   <script src="{{ url_for('static', filename='js/wms/avarias-common.js') }}"></script>
 */

// NAMESPACE GLOBAL — Evita poluição do escopo global
window.WMS = window.WMS || {};

function _debounce(fn, delay) {
    let t;
    return function(...args) {
        clearTimeout(t);
        t = setTimeout(() => fn.apply(this, args), delay);
    };
}

// 1. NOTIFICAÇÕES

/**
 * Exibe notificação toast no canto superior direito.
 * Fallback para alert() se Bootstrap não estiver disponível.
 * 
 * @param {string} type - 'success' | 'error' | 'warning' | 'info'
 * @param {string} message - Texto da mensagem
 * @param {number} [duration=4000] - Duração em ms (0 = não fecha automaticamente)
 */
function showNotification(type, message, duration = 4000) {
    // Prevenir recursão
    if (window._showingNotification) return;
    window._showingNotification = true;

    const typeConfig = {
        success: { icon: 'fa-check-circle', bg: 'bg-success', title: 'Sucesso' },
        error:   { icon: 'fa-exclamation-triangle', bg: 'bg-danger', title: 'Erro' },
        warning: { icon: 'fa-exclamation-circle', bg: 'bg-warning', title: 'Atenção' },
        info:    { icon: 'fa-info-circle', bg: 'bg-info', title: 'Info' }
    };

    const config = typeConfig[type] || typeConfig.info;

    // Criar container se não existir
    let container = document.getElementById('wms-toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'wms-toast-container';
        container.className = 'toast-container position-fixed top-0 end-0 p-3';
        container.style.zIndex = '11000';
        document.body.appendChild(container);
    }

    const toastId = 'toast-' + Date.now();
    const toastHTML = `
        <div id="${toastId}" class="toast align-items-center text-white ${config.bg} border-0" 
             role="alert" aria-live="assertive" aria-atomic="true"
             data-bs-delay="${duration}" data-bs-autohide="${duration > 0}">
            <div class="d-flex">
                <div class="toast-body">
                    <i class="fas ${config.icon} me-2"></i>${message}
                </div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" 
                        data-bs-dismiss="toast" aria-label="Close"></button>
            </div>
        </div>
    `;

    container.insertAdjacentHTML('beforeend', toastHTML);

    const toastEl = document.getElementById(toastId);

    try {
        if (typeof bootstrap !== 'undefined' && bootstrap.Toast) {
            const toast = new bootstrap.Toast(toastEl);
            toast.show();

            toastEl.addEventListener('hidden.bs.toast', () => {
                toastEl.remove();
            });
        } else {
            // Fallback sem Bootstrap
            toastEl.style.display = 'block';
            if (duration > 0) {
                setTimeout(() => toastEl.remove(), duration);
            }
        }
    } catch (e) {
        console.warn('Toast fallback:', e);
        alert(`${config.title}: ${message}`);
        toastEl.remove();
    }

    window._showingNotification = false;
}

// Registrar globalmente
window.showNotification = showNotification;
window.WMS.showNotification = showNotification;

// 2. FORMATAÇÃO DE VALORES

/**
 * Formata número como moeda brasileira (R$)
 * @param {number|string} valor - Valor numérico
 * @returns {string} Valor formatado (ex: "R$ 1.234,56")
 */
function formatCurrency(valor) {
    const num = parseFloat(valor);
    if (isNaN(num)) return 'R$ 0,00';
    return num.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

/**
 * Formata número como inteiro com separador de milhares
 * @param {number|string} valor
 * @returns {string}
 */
function formatInteger(valor) {
    const num = parseInt(valor);
    if (isNaN(num)) return '0';
    return num.toLocaleString('pt-BR');
}

/**
 * Parse de valor decimal (aceita formatos BR e US)
 * @param {string|number} valor - "1.234,56" ou "1234.56" ou 1234.56
 * @returns {number}
 */
function parseDecimalValue(valor) {
    if (typeof valor === 'number') return valor;
    if (!valor || valor === '' || valor === null || valor === undefined) return 0;
    
    const str = String(valor).trim();
    
    // Remover R$ e espaços
    let clean = str.replace(/R\$\s*/g, '').trim();
    
    // Detectar formato brasileiro (1.234,56) vs americano (1,234.56)
    const lastComma = clean.lastIndexOf(',');
    const lastDot = clean.lastIndexOf('.');
    
    if (lastComma > lastDot) {
        // Formato BR: 1.234,56 → remover pontos, trocar vírgula por ponto
        clean = clean.replace(/\./g, '').replace(',', '.');
    } else if (lastDot > lastComma && lastComma > -1) {
        // Formato US: 1,234.56 → remover vírgulas
        clean = clean.replace(/,/g, '');
    } else if (lastComma > -1 && lastDot === -1) {
        // Só vírgula: 1234,56 → trocar por ponto
        clean = clean.replace(',', '.');
    }
    
    const result = parseFloat(clean);
    return isNaN(result) ? 0 : result;
}

/**
 * Formata data para exibição (DD/MM/YYYY)
 * @param {string} dateStr - Data em formato ISO ou DD/MM/YYYY
 * @returns {string}
 */
function formatDate(dateStr) {
    if (!dateStr) return '-';
    
    // Se já está no formato DD/MM/YYYY
    if (/^\d{2}\/\d{2}\/\d{4}$/.test(dateStr)) return dateStr;
    
    // Se está em ISO (YYYY-MM-DD ou YYYY-MM-DDTHH:MM:SS)
    const match = dateStr.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (match) return `${match[3]}/${match[2]}/${match[1]}`;
    
    // Tentar cortar nos primeiros 10 chars
    if (dateStr.length >= 10) {
        const sub = dateStr.substring(0, 10);
        const parts = sub.split('-');
        if (parts.length === 3) return `${parts[2]}/${parts[1]}/${parts[0]}`;
    }
    
    return dateStr;
}

// Exportar globalmente
window.formatCurrency = formatCurrency;
window.formatInteger = formatInteger;
window.parseDecimalValue = parseDecimalValue;
window.formatDate = formatDate;
window.WMS.formatCurrency = formatCurrency;
window.WMS.formatInteger = formatInteger;
window.WMS.parseDecimalValue = parseDecimalValue;
window.WMS.formatDate = formatDate;

// 3. UTILITÁRIOS DE DOM

/**
 * Obtém valor de campo do formulário (trim)
 * @param {string} id - ID do elemento
 * @returns {string}
 */
function getFieldValue(id) {
    const el = document.getElementById(id);
    return el ? (el.value || '').trim() : '';
}

/**
 * Define valor de campo do formulário
 * @param {string} id - ID do elemento
 * @param {string} value - Valor a definir
 */
function setFieldValue(id, value) {
    const el = document.getElementById(id);
    if (el) el.value = value || '';
}

/**
 * Define textContent de um elemento
 * @param {string} id - ID do elemento
 * @param {string} text - Texto
 */
function setTextContent(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text || '';
}

window.getFieldValue = getFieldValue;
window.setFieldValue = setFieldValue;
window.setTextContent = setTextContent;

// 4. UTILITÁRIOS DE TABELA

/**
 * Converte índice de coluna (1-based) para letra do Excel
 * @param {number} index - Índice da coluna (1 = A, 26 = Z, 27 = AA)
 * @returns {string}
 */
function colIndexToLetter(index) {
    let letter = '';
    while (index > 0) {
        const remainder = (index - 1) % 26;
        letter = String.fromCharCode(65 + remainder) + letter;
        index = Math.floor((index - 1) / 26);
    }
    return letter;
}

/**
 * Debounce genérico
 * @param {Function} func - Função a executar
 * @param {number} wait - Delay em ms
 * @returns {Function}
 */
function debounce(func, wait = 300) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func.apply(this, args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

window.colIndexToLetter = colIndexToLetter;
window.debounce = debounce;
window.WMS.debounce = debounce;

// 5. STATUS COLOR MAP HELPER

/**
 * Aplica cores de status aos badges da tabela.
 * Chamado após renderização da tabela.
 * 
 * @param {Object} colorMap - Mapa {statusName: corHex} vindo do Jinja2
 * @param {string} tableId - ID da tabela
 */
function applyStatusColors(colorMap, tableId) {
    if (!colorMap || Object.keys(colorMap).length === 0) return;
    
    const table = document.getElementById(tableId);
    if (!table) return;
    
    table.querySelectorAll('.status-badge').forEach(badge => {
        const status = badge.textContent.trim();
        const cor = colorMap[status];
        if (cor) {
            badge.style.backgroundColor = cor;
            badge.style.color = isLightColor(cor) ? '#212529' : '#ffffff';
            badge.style.border = `1px solid ${cor}`;
        }
    });
}

/**
 * Verifica se uma cor hex é clara (para determinar cor do texto)
 * @param {string} hexColor - Cor em hex (#RRGGBB)
 * @returns {boolean}
 */
function isLightColor(hexColor) {
    const hex = hexColor.replace('#', '');
    const r = parseInt(hex.substr(0, 2), 16);
    const g = parseInt(hex.substr(2, 2), 16);
    const b = parseInt(hex.substr(4, 2), 16);
    const brightness = (r * 299 + g * 587 + b * 114) / 1000;
    return brightness > 155;
}

window.applyStatusColors = applyStatusColors;
window.isLightColor = isLightColor;
window.WMS.applyStatusColors = applyStatusColors;

// 6. LOG HELPER

/**
 * Log com prefixo WMS colorido (facilita debug no console)
 */
const WMSLog = {
    info: (msg, ...args) => console.log(`%c[WMS]%c ${msg}`, 'color:#28a745;font-weight:bold', 'color:inherit', ...args),
    warn: (msg, ...args) => console.warn(`%c[WMS]%c ${msg}`, 'color:#ffc107;font-weight:bold', 'color:inherit', ...args),
    error: (msg, ...args) => console.error(`%c[WMS]%c ${msg}`, 'color:#dc3545;font-weight:bold', 'color:inherit', ...args),
    debug: (msg, ...args) => console.log(`%c[WMS]%c ${msg}`, 'color:#17a2b8;font-weight:bold', 'color:inherit', ...args)
};

window.WMSLog = WMSLog;
window.WMS.log = WMSLog;

// INIT LOG
WMSLog.info('✅ avarias-common.js carregado');
