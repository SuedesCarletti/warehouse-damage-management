/**
 * sidebar-toggle.js - Sistema de Toggle da Sidebar
 * 
 * Adicione este arquivo ao base.html: <script src="{{ url_for('static', filename='js/sidebar-toggle.js') }}"></script>
 */

(function() {
    'use strict';
    
    // ==================== INICIALIZAÇÃO ====================
    document.addEventListener('DOMContentLoaded', function() {
        initSidebarToggle();
        loadSidebarState();
    });
    
    /**
     * Inicializa o botão de toggle da sidebar
     */
    function initSidebarToggle() {
        // Criar botão de toggle se não existir
        if (!document.getElementById('sidebarToggleBtn')) {
            createToggleButton();
        }
        
        // Adicionar event listener
        const toggleBtn = document.getElementById('sidebarToggleBtn');
        if (toggleBtn) {
            toggleBtn.addEventListener('click', toggleSidebar);
        }
        
        // Adicionar atalho de teclado (Ctrl + B)
        document.addEventListener('keydown', function(e) {
            if (e.ctrlKey && e.key === 'b') {
                e.preventDefault();
                toggleSidebar();
            }
        });
    }
    
    /**
     * Cria o botão de toggle da sidebar
     */
    function createToggleButton() {
        const button = document.createElement('button');
        button.id = 'sidebarToggleBtn';
        button.className = 'sidebar-toggle-btn';
        button.title = 'Ocultar/Mostrar Sidebar (Ctrl + B)';
        button.innerHTML = '<i class="fas fa-chevron-left"></i>';
        button.setAttribute('aria-label', 'Toggle Sidebar');
        
        document.body.appendChild(button);
    }
    
    let _storageAvailable = null;
    function isStorageAvailable() {
        if (_storageAvailable !== null) return _storageAvailable;
        try {
            const test = '__wms_test__';
            localStorage.setItem(test, test);
            localStorage.removeItem(test);
            _storageAvailable = true;
        } catch (e) {
            _storageAvailable = false;
        }
        return _storageAvailable;
    }

    /**
     * Alterna a visibilidade da sidebar
     */
    function toggleSidebar() {
        const body = document.body;
        const isHidden = body.classList.toggle('sidebar-hidden');
        
        // Salvar estado no localStorage
        if (isStorageAvailable()) {
            localStorage.setItem('sidebarHidden', isHidden ? 'true' : 'false');
        }

        // Log para debug
        console.log(`Sidebar ${isHidden ? 'ocultada' : 'exibida'}`);
        
        // Disparar evento customizado para outras partes do sistema
        const event = new CustomEvent('sidebarToggle', { 
            detail: { hidden: isHidden } 
        });
        document.dispatchEvent(event);
        
        // Ajustar foco para melhor acessibilidade
        if (!isHidden) {
            const firstLink = document.querySelector('.sidebar .nav-link');
            if (firstLink) firstLink.focus();
        }
    }
    
    /**
     * Carrega o estado da sidebar do localStorage
     */
    function loadSidebarState() {
        if (isStorageAvailable()) {
            const sidebarHidden = localStorage.getItem('sidebarHidden');
            
            if (sidebarHidden === 'true') {
                document.body.classList.add('sidebar-hidden');
                console.log('Estado da sidebar restaurado: oculta');
            } else {
                console.log('Estado da sidebar restaurado: visível');
            }
        }
    }
    
    // ==================== EXPOR FUNÇÕES GLOBAIS ====================
    window.sidebarToggle = {
        toggle: toggleSidebar,
        hide: function() {
            if (!document.body.classList.contains('sidebar-hidden')) {
                toggleSidebar();
            }
        },
        show: function() {
            if (document.body.classList.contains('sidebar-hidden')) {
                toggleSidebar();
            }
        },
        isHidden: function() {
            return document.body.classList.contains('sidebar-hidden');
        }
    };
    
})();
