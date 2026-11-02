#!/usr/bin/env python3
"""
iCloud (you drive) + Harbor (follows). Watches Apple's sign-in iframe and icloud.com
for steps, field values, and errors; updates Harbor to match — no API between sites.

  Tab 1: iCloud — use your real Apple ID here.
  Tab 2: Harbor — routes, inputs, and errors mirror Apple for side-by-side polish.

Usage:
  python apple_harbor_mirror.py --channel chrome
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

ICLOUD_HOME = 'https://www.icloud.com/'
ROOT = Path(__file__).parent
HARBOR_DRIVER = (ROOT / 'harbor_driver.js').read_text(encoding='utf-8')
APPLE_PROBE = (ROOT / 'apple_probe.js').read_text(encoding='utf-8')
APPLE_PAGE_PROBE = (ROOT / 'apple_page_probe.js').read_text(encoding='utf-8')


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Mirror iCloud sign-in state → Harbor (Playwright).')
    p.add_argument('--harbor', default='https://harbor-ten-lake.vercel.app')
    p.add_argument('--poll-ms', type=int, default=120)
    p.add_argument('--channel', default='chrome')
    return p.parse_args()


def path_of(url: str) -> str:
    path = urlparse(url).path or '/'
    if path != '/' and path.endswith('/'):
        path = path.rstrip('/')
    return path or '/'


def apple_auth_frame(page: Page):
    for frame in page.frames:
        if 'idmsa.apple.com' in (frame.url or ''):
            return frame
    return None


def open_icloud_sign_in(apple: Page) -> None:
    apple.goto(ICLOUD_HOME, wait_until='domcontentloaded', timeout=90000)
    apple.wait_for_timeout(2500)
    if apple_auth_frame(apple):
        return
    sign_in = apple.locator('text=Sign In').first
    sign_in.wait_for(state='visible', timeout=45000)
    sign_in.click()
    for _ in range(4):
        apple.wait_for_timeout(3000)
        if apple_auth_frame(apple):
            return
        sign_in.click()


def probe_apple_frame(frame) -> dict | None:
    try:
        return frame.evaluate(APPLE_PROBE)
    except PlaywrightError:
        return None


def probe_apple_page(page: Page) -> dict:
    try:
        return page.evaluate(APPLE_PAGE_PROBE)
    except PlaywrightError:
        return {'trust': False, 'signedIn': False, 'twoFactorOnPage': False}


def read_apple_password(frame) -> str:
    try:
        return frame.evaluate(
            """() => {
              const el = document.getElementById('password_text_field');
              if (!el || el.offsetParent === null) return '';
              return el.value || '';
            }"""
        )
    except PlaywrightError:
        return ''


def invalidate_for_message(message: str) -> str:
    m = (message or '').lower()
    if 'password' in m and ('account' in m or 'apple id' in m):
        return 'both'
    if 'password' in m or 'incorrect' in m:
        return 'password'
    if 'verification' in m or 'code' in m:
        return 'otp'
    if 'account' in m or 'apple id' in m or 'email' in m:
        return 'account'
    return 'both'


def ensure_harbor_driver(harbor: Page) -> None:
    try:
        harbor.evaluate(HARBOR_DRIVER)
    except PlaywrightError:
        pass


def harbor_goto(harbor: Page, base: str, path: str) -> None:
    target = base.rstrip('/') + path
    if harbor.url.split('?')[0].rstrip('/') == target.rstrip('/'):
        return
    try:
        harbor.goto(target, wait_until='domcontentloaded', timeout=45000)
        ensure_harbor_driver(harbor)
    except PlaywrightError:
        pass


def harbor_route_for_apple(apple_step: str, page_info: dict) -> str:
    if page_info.get('trust'):
        return '/session'
    if page_info.get('signedIn') and not page_info.get('trust'):
        return '/success'
    if apple_step == 'otp' or page_info.get('twoFactorOnPage'):
        return '/verify'
    return '/signin'


def sync_fields(
    harbor: Page,
    apple_state: dict | None,
    frame,
    last_email: str,
    last_pw_len: int,
) -> tuple[str, int]:
    if not apple_state:
        return last_email, last_pw_len
    email = apple_state.get('email') or ''
    pw_len = int(apple_state.get('passwordLen') or 0)
    password = read_apple_password(frame) if frame and pw_len else ''

    if email != last_email or pw_len != last_pw_len:
        harbor.evaluate(
            """({ email, password }) => window.HarborDriver?.fillSignIn(email, password)""",
            {'email': email, 'password': password},
        )

    otp = apple_state.get('otpDigits') or []
    if len(otp) == 6 and any(otp):
        harbor.evaluate(
            """(digits) => window.HarborDriver?.fillOtp(digits)""",
            otp,
        )

    return email, pw_len


def run() -> int:
    args = parse_args()
    harbor_base = args.harbor.rstrip('/')

    launch_opts: dict = {'headless': False, 'slow_mo': 40}
    if args.channel:
        launch_opts['channel'] = args.channel

    print('Apple → Harbor mirror', flush=True)
    print('  Tab 1: iCloud (you sign in here)', flush=True)
    print(f'  Tab 2: Harbor ({harbor_base})', flush=True)
    print('  Errors and steps on Apple should appear on Harbor. Ctrl+C to stop.\n', flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_opts)
        context = browser.new_context(viewport={'width': 980, 'height': 920})
        apple = context.new_page()
        harbor = context.new_page()

        open_icloud_sign_in(apple)
        harbor_goto(harbor, harbor_base, '/signin')
        ensure_harbor_driver(harbor)

        last_error_key = ''
        last_email = ''
        last_pw_len = -1
        last_apple_route = ''
        last_trust = False

        try:
            while True:
                frame = apple_auth_frame(apple)
                apple_state = probe_apple_frame(frame) if frame else None
                page_info = probe_apple_page(apple)

                route = harbor_route_for_apple(
                    (apple_state or {}).get('step') or 'email', page_info
                )
                if route != last_apple_route:
                    print(f'  → Apple phase: {route}', flush=True)
                    last_apple_route = route
                    if route == '/verify' and apple_state:
                        email = apple_state.get('email') or ''
                        harbor.evaluate(
                            """(email) => {
                              window.HarborDriver?.setAppleMirrorPending(email);
                            }""",
                            email,
                        )
                    harbor_goto(harbor, harbor_base, route)
                    last_error_key = ''

                if page_info.get('trust') and not last_trust:
                    print('  → Trust browser (Harbor /session)', flush=True)
                    harbor.evaluate(
                        """() => {
                          window.HarborDriver?.markVerified();
                          if (!location.pathname.includes('session')) location.href = '/session';
                        }"""
                    )
                    last_trust = True

                if page_info.get('signedIn') and last_trust:
                    harbor_goto(harbor, harbor_base, '/success')

                errors = (apple_state or {}).get('errors') or []
                if route == '/signin':
                    if errors:
                        msg = errors[-1]
                        err_key = hashlib.sha256(msg.encode()).hexdigest()[:16]
                        if err_key != last_error_key:
                            inv = invalidate_for_message(msg)
                            print(f'  ! Apple error → Harbor: {msg[:80]}', flush=True)
                            ensure_harbor_driver(harbor)
                            harbor.evaluate(
                                """({ msg, inv }) => {
                                  window.HarborDriver?.showSignInError(msg, inv === 'otp' ? 'password' : inv);
                                }""",
                                {'msg': msg, 'inv': inv},
                            )
                            last_error_key = err_key
                    elif last_error_key and not last_error_key.startswith('v:'):
                        ensure_harbor_driver(harbor)
                        harbor.evaluate('() => window.HarborDriver?.clearSignInErrors()')
                        last_error_key = ''

                if route == '/verify':
                    if errors:
                        msg = errors[-1]
                        err_key = 'v:' + hashlib.sha256(msg.encode()).hexdigest()[:16]
                        if err_key != last_error_key:
                            print(f'  ! Apple OTP error → Harbor: {msg[:80]}', flush=True)
                            ensure_harbor_driver(harbor)
                            harbor.evaluate(
                                """(msg) => window.HarborDriver?.showVerifyError(msg)""",
                                msg,
                            )
                            last_error_key = err_key
                    elif last_error_key.startswith('v:') and not errors:
                        harbor.evaluate('() => window.HarborDriver?.clearVerifyError()')
                        last_error_key = ''

                if apple_state and route == '/signin':
                    step = apple_state.get('step')
                    if step == 'password':
                        harbor.evaluate('() => window.HarborDriver?.ensurePasswordStep()')

                last_email, last_pw_len = sync_fields(
                    harbor, apple_state, frame, last_email, last_pw_len
                )

                time.sleep(max(args.poll_ms, 50) / 1000.0)
        except KeyboardInterrupt:
            print('\nStopped.', flush=True)
        finally:
            context.close()
            browser.close()

    return 0


if __name__ == '__main__':
    sys.exit(run())
