// Helpers de UI compartilhados entre as telas do WMS (e HelpDesk) para substituir
// os diálogos nativos alert()/confirm() do navegador por modais Bootstrap consistentes.
// _confirmarAcaoCritica() foi originalmente escrito em validacao_estoque.html;
// extraído para cá para ser reaproveitado em vez de duplicado por tela.

// Modal de confirmação customizado (2 botões) — retorna Promise<boolean>.
function _confirmarAcaoCritica(titulo, corpo, txtConfirm, txtCancel) {
  return new Promise(resolve => {
    let el = document.getElementById('_modalConfirmCritico');
    if (!el) {
      el = document.createElement('div');
      el.id = '_modalConfirmCritico';
      el.className = 'modal fade';
      el.tabIndex = -1;
      el.innerHTML = `
        <div class="modal-dialog modal-sm">
          <div class="modal-content border-danger">
            <div class="modal-header bg-danger text-white py-2">
              <h6 class="modal-title mb-0" id="_mcc-titulo"></h6>
              <button type="button" class="btn-close btn-close-white btn-sm" id="_mcc-x"></button>
            </div>
            <div class="modal-body small" id="_mcc-corpo"></div>
            <div class="modal-footer py-2">
              <button class="btn btn-secondary btn-sm" id="_mcc-cancel"></button>
              <button class="btn btn-danger btn-sm" id="_mcc-confirm"></button>
            </div>
          </div>
        </div>`;
      document.body.appendChild(el);
    }

    let resolved = false;
    function _resolve(val) {
      if (resolved) return;
      resolved = true;
      inst.hide();
      resolve(val);
    }

    document.getElementById('_mcc-titulo').textContent  = titulo;
    document.getElementById('_mcc-corpo').innerHTML     = corpo;
    document.getElementById('_mcc-confirm').textContent = txtConfirm || 'Confirmar';
    document.getElementById('_mcc-cancel').textContent  = txtCancel || 'Cancelar';

    const inst = new bootstrap.Modal(el);
    document.getElementById('_mcc-confirm').onclick = () => _resolve(true);
    document.getElementById('_mcc-cancel').onclick  = () => _resolve(false);
    document.getElementById('_mcc-x').onclick       = () => _resolve(false);
    el.addEventListener('hidden.bs.modal', () => _resolve(false), { once: true });
    inst.show();
  });
}

// Modal de aviso simples (1 botão) — substitui alert(). Retorna Promise<void>,
// resolvida quando o usuário fecha o modal.
function _alertaCritico(titulo, corpo, txtOk) {
  return new Promise(resolve => {
    let el = document.getElementById('_modalAlertaCritico');
    if (!el) {
      el = document.createElement('div');
      el.id = '_modalAlertaCritico';
      el.className = 'modal fade';
      el.tabIndex = -1;
      el.innerHTML = `
        <div class="modal-dialog modal-sm">
          <div class="modal-content">
            <div class="modal-header py-2">
              <h6 class="modal-title mb-0" id="_mac-titulo"></h6>
              <button type="button" class="btn-close" id="_mac-x"></button>
            </div>
            <div class="modal-body small" id="_mac-corpo"></div>
            <div class="modal-footer py-2">
              <button class="btn btn-primary btn-sm" id="_mac-ok"></button>
            </div>
          </div>
        </div>`;
      document.body.appendChild(el);
    }

    let resolved = false;
    function _resolve() {
      if (resolved) return;
      resolved = true;
      inst.hide();
      resolve();
    }

    document.getElementById('_mac-titulo').textContent = titulo || 'Aviso';
    document.getElementById('_mac-corpo').innerHTML     = corpo;
    document.getElementById('_mac-ok').textContent      = txtOk || 'OK';

    const inst = new bootstrap.Modal(el);
    document.getElementById('_mac-ok').onclick = () => _resolve();
    document.getElementById('_mac-x').onclick  = () => _resolve();
    el.addEventListener('hidden.bs.modal', () => _resolve(), { once: true });
    inst.show();
  });
}

// Traduz mensagens técnicas de erro (ERP/Sheets/rede) para algo que o operador
// consiga entender, seguindo o mesmo padrão já usado em validacao_estoque.html.
// Mensagens desconhecidas que "parecem" técnicas (stack trace, exceção crua,
// muito longas) caem num fallback genérico em vez de aparecer cruas na tela;
// mensagens desconhecidas mas curtas/legíveis (geralmente escritas pelo próprio
// backend para o usuário) são mantidas como estão.
function traduzirErroTecnico(mensagemBruta) {
  const msg = (mensagemBruta || '').toString().trim();
  if (!msg) return 'Ocorreu um problema ao processar sua solicitação. Tente novamente ou contate o suporte.';

  if (/ERP|Sankhya|timeout|ECONNRE|connect/i.test(msg)) {
    return 'Não foi possível conectar ao ERP (Sankhya). Verifique a integração e tente novamente.';
  }
  if (/429|quota|rate.?limit/i.test(msg)) {
    return 'O Google Sheets está temporariamente sobrecarregado (limite de requisições). Aguarde alguns segundos e tente novamente.';
  }
  if (/codemp|empresa/i.test(msg)) {
    return 'Código de empresa inválido. Verifique o CODEMP informado.';
  }
  if (/lote/i.test(msg)) {
    return 'Lote não encontrado ou já validado. Recarregue a página e tente novamente.';
  }

  const pareceTecnica = /Traceback|Exception|NoneType|NameError|KeyError|TypeError|at \S+\.js:\d+|^\{.*\}$/i.test(msg) || msg.length > 180;
  if (pareceTecnica) {
    console.error('Erro técnico não traduzido:', msg);
    return 'Ocorreu um problema ao processar sua solicitação. Tente novamente ou contate o suporte.';
  }
  return msg;
}
