// Helpers de loading para botões do HelpDesk (Adicionar/Editar Usuário, Categorias, Novo Ticket).
// Antes deste arquivo, as telas chamavam showLoading()/hideLoading() sem essas funções
// existirem em nenhum script carregado pelo HelpDesk — o clique nos botões só lançava
// um ReferenceError silencioso e não fazia nada.

function showLoading(btn) {
    if (!btn) return;
    if (btn.dataset.originalText === undefined) {
        btn.dataset.originalText = btn.innerHTML;
    }
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Processando...';
}

function hideLoading(btn, originalText) {
    if (!btn) return;
    btn.disabled = false;
    btn.innerHTML = originalText !== undefined ? originalText : (btn.dataset.originalText || btn.innerHTML);
}
