// Evaluated on icloud.com top-level page (trust / post-auth UI).
() => {
  const body = (document.body && document.body.innerText) || '';
  const trust =
    /trust this browser/i.test(body) ||
    document.querySelector('[class*="trust"]')?.innerText?.toLowerCase().includes('trust');
  const signedIn =
    /signed in/i.test(body) ||
    document.querySelector('.app-icon, .apps-list, [data-app]') !== null;
  const twoFactor = /two-factor|verification code|enter the code/i.test(body);
  return {
    trust: Boolean(trust),
    signedIn: Boolean(signedIn),
    twoFactorOnPage: Boolean(twoFactor),
    snippet: body.slice(0, 400),
  };
};
