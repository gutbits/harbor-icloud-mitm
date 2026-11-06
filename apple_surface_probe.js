// Evaluated on icloud.com top-level page (modal / post-auth UI outside auth iframe).
() => {
  const body = (document.body && document.body.innerText) || '';
  const payment =
    /verify your payment information|security code for the(?: credit)? card|not be charged/i.test(
      body
    );
  const trust =
    /trust this browser/i.test(body) ||
    document.querySelector('[class*="trust"]')?.innerText?.toLowerCase().includes('trust');
  const signedIn =
    /signed in/i.test(body) ||
    document.querySelector('.app-icon, .apps-list, [data-app]') !== null;
  const twoFactor =
    /two-factor|verification code|enter the code/i.test(body) &&
    !payment;
  const loading =
    !/enter verification code|verify your payment information|security code for the(?: credit)? card/i.test(
      body
    ) &&
    ((/\bloading\b/i.test(body) && body.length < 400) ||
      document.querySelector('.loading-wrapper, .global-loading') !== null);
  return {
    payment: Boolean(payment),
    trust: Boolean(trust),
    signedIn: Boolean(signedIn),
    twoFactorOnPage: Boolean(twoFactor),
    loading: Boolean(loading),
    snippet: body.slice(0, 600),
  };
};
