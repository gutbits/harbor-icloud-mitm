(function () {
  if (window.__harborMirrorInstalled) return;
  window.__harborMirrorInstalled = true;
  window.__mirrorQueue = window.__mirrorQueue || [];

  function push(type, payload) {
    window.__mirrorQueue.push({ type: type, payload: payload, t: Date.now() });
  }

  function selectorFor(el) {
    if (!el || !el.tagName) return null;
    if (el.id) return { kind: 'id', value: el.id };
    if (el.name) return { kind: 'name', value: el.name };
    if (el.dataset && el.dataset.otpIndex !== undefined) {
      return { kind: 'css', value: '[data-otp-index="' + el.dataset.otpIndex + '"]' };
    }
    if (el.classList && el.classList.length) {
      return {
        kind: 'css',
        value: el.tagName.toLowerCase() + '.' + Array.from(el.classList).slice(0, 3).join('.'),
      };
    }
    return null;
  }

  document.addEventListener(
    'input',
    function (e) {
      var t = e.target;
      if (!t || !/^(INPUT|TEXTAREA|SELECT)$/i.test(t.tagName)) return;
      push('input', {
        selector: selectorFor(t),
        value: t.value,
        checked: t.checked,
        inputType: t.type || '',
      });
    },
    true
  );

  document.addEventListener(
    'change',
    function (e) {
      var t = e.target;
      if (!t || !/^(INPUT|SELECT|TEXTAREA)$/i.test(t.tagName)) return;
      push('input', {
        selector: selectorFor(t),
        value: t.value,
        checked: t.checked,
        inputType: t.type || '',
      });
    },
    true
  );

  document.addEventListener(
    'click',
    function (e) {
      var t = e.target.closest(
        'button, a.trust-btn, a.primary, a.sign-in-link, .quick-access-btn, .help-menu-button, .verify-link'
      );
      if (!t) return;
      push('click', {
        selector: selectorFor(t),
        tag: t.tagName.toLowerCase(),
        text: (t.innerText || t.textContent || '').trim().slice(0, 80),
        href: t.getAttribute('href') || '',
      });
    },
    true
  );
})();
