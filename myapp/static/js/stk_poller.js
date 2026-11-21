(function () {
  'use strict';

  var POLL_MS = 3000;
  var MAX_ATTEMPTS = 15; // 15 * 3s = 45s

  function csrfToken() {
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  function el(id) { return document.getElementById(id); }

  function showState(state) {
    ['stkStateIdle', 'stkStatePending', 'stkStateDone'].forEach(function (id) {
      var node = el(id);
      if (node) node.classList.toggle('d-none', id !== state);
    });
    var form = el('stkForm');
    if (form) {
      var submitBtn = form.querySelector('[type="submit"]');
      if (submitBtn) {
        submitBtn.disabled = state === 'stkStatePending' || state === 'stkStateDone';
      }
    }
  }

  function renderResult(data) {
    var ok = data.status === 'SUCCESS';
    var icon = el('stkResultIcon');
    var title = el('stkResultTitle');
    var detail = el('stkResultDetail');
    var receipt = el('stkResultReceipt');
    if (icon) {
      icon.className = ok ? 'bi bi-check-circle text-success' : 'bi bi-x-circle text-danger';
    }
    if (title) title.textContent = ok ? 'Payment confirmed' : 'Payment not completed';
    if (detail) {
      detail.textContent = ok
        ? 'The M-Pesa payment was received and recorded against this farmer.'
        : (data.result_desc || 'The payment failed or was cancelled on the handset.');
    }
    if (receipt) {
      receipt.textContent = data.mpesa_receipt_number ? ('Receipt: ' + data.mpesa_receipt_number) : '';
      receipt.classList.toggle('d-none', !data.mpesa_receipt_number);
    }
    showState('stkStateDone');
  }

  function poll(statusUrl, attempt) {
    fetch(statusUrl, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function (data) {
        if (data.status === 'PENDING' && attempt < MAX_ATTEMPTS) {
          setTimeout(function () { poll(statusUrl, attempt + 1); }, POLL_MS);
          return;
        }
        if (data.status === 'PENDING') {
          data.result_desc = 'Timed out waiting for the M-Pesa PIN prompt. Check the M-Pesa ledger before retrying.';
          data.status = 'FAILED';
        }
        renderResult(data);
      })
      .catch(function () {
        if (attempt < MAX_ATTEMPTS) {
          setTimeout(function () { poll(statusUrl, attempt + 1); }, POLL_MS);
        } else {
          renderResult({ status: 'FAILED', result_desc: 'Could not reach the status endpoint.' });
        }
      });
  }

  function submit(form) {
    var pushUrl = form.getAttribute('data-stk-push-url');
    var statusUrlTemplate = form.getAttribute('data-stk-status-url');
    var phone = (el('stkPhone') || {}).value || '';
    var amount = (el('stkAmount') || {}).value || '';
    var memberNumber = (el('stkMemberNumber') || {}).value || '';

    var errNode = el('stkError');
    var errTimer = null;
    function showError(message) {
      if (!errNode) return;
      errNode.textContent = message;
      errNode.classList.remove('d-none');
      if (errTimer) clearTimeout(errTimer);
      errTimer = setTimeout(function () {
        errNode.classList.add('d-none');
        errNode.textContent = '';
      }, 8000);
    }
    if (errNode) { errNode.classList.add('d-none'); errNode.textContent = ''; }
    showState('stkStatePending');

    fetch(pushUrl, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-CSRFToken': csrfToken()
      },
      body: JSON.stringify({ member_number: memberNumber, phone: phone, amount: amount })
    })
      .then(function (res) {
        return res.json().then(function (body) {
          return { ok: res.ok, status: res.status, body: body };
        });
      })
      .then(function (result) {
        if (!result.ok) {
          var message = (result.body && (result.body.detail || result.body.phone || result.body.amount)) ||
            'The payment request was rejected.';
          if (typeof message !== 'string') message = message.join(' ');
          showState('stkStateIdle');
          showError(message);
          return;
        }
        var checkout = result.body.checkout_request_id;
        poll(statusUrlTemplate.replace('CHECKOUT_ID', encodeURIComponent(checkout)), 1);
      })
      .catch(function () {
        showState('stkStateIdle');
        showError('Network error while starting the payment. Try again.');
      });
  }

  document.addEventListener('DOMContentLoaded', function () {
    var form = el('stkForm');
    if (!form) return;
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      submit(form);
    });

    var modal = el('stkModal');
    if (modal && window.bootstrap && bootstrap.Modal) {
      modal.addEventListener('hidden.bs.modal', function () {
        showState('stkStateIdle');
        var errNode = el('stkError');
        if (errNode) { errNode.classList.add('d-none'); errNode.textContent = ''; }
      });
    }
  });
})();
