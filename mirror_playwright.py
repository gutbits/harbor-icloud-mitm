#!/usr/bin/env python3
"""Mirror Harbor Classic → Modern using Playwright (two tabs, no API)."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, Playwright, sync_playwright

BRIDGE = (Path(__file__).parent / 'bridge.js').read_text(encoding='utf-8')


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Mirror Harbor Classic → Modern (Playwright).')
    p.add_argument('--classic', default='https://harbor-classic.vercel.app')
    p.add_argument('--modern', default='https://harbor-ten-lake.vercel.app')
    p.add_argument('--path', default='/signin')
    p.add_argument('--poll-ms', type=int, default=100)
    p.add_argument('--channel', default='chrome', help='Playwright browser channel: chrome, chromium, msedge')
    return p.parse_args()


def path_of(url: str) -> str:
    path = urlparse(url).path or '/'
    if path != '/' and path.endswith('/'):
        path = path.rstrip('/')
    return path or '/'


def modern_url_for(classic_url: str, classic_base: str, modern_base: str) -> str:
    classic_base = classic_base.rstrip('/')
    modern_base = modern_base.rstrip('/')
    if not classic_url.startswith(classic_base):
        return modern_base + '/'
    suffix = classic_url[len(classic_base) :] or '/'
    if not suffix.startswith('/'):
        suffix = '/' + suffix
    return modern_base + suffix.split('?')[0].split('#')[0]


def find_locator(page: Page, spec: dict | None):
    if not spec:
        return None
    kind = spec.get('kind')
    value = spec.get('value')
    if not value:
        return None
    if kind == 'id':
        return page.locator(f'#{value}')
    if kind == 'name':
        return page.locator(f'[name="{value}"]')
    if kind == 'css':
        return page.locator(value)
    return None


def apply_input(page: Page, payload: dict) -> None:
    loc = find_locator(page, payload.get('selector'))
    if not loc or loc.count() == 0:
        return
    target = loc.first
    input_type = (payload.get('inputType') or '').lower()
    value = payload.get('value') or ''
    spec = payload.get('selector') or {}
    is_otp = (
        spec.get('kind') == 'id'
        and str(spec.get('value', '')).startswith('otp-')
    ) or (
        spec.get('kind') == 'css'
        and 'otp-digit' in str(spec.get('value', ''))
    )
    try:
        if input_type in ('checkbox', 'radio'):
            if payload.get('checked'):
                target.check()
            else:
                target.uncheck()
            return
        if is_otp:
            target.fill(value)
            target.dispatch_event('input')
            target.dispatch_event('change')
            return
        target.fill(value)
    except Exception:
        pass


def apply_click(page: Page, payload: dict, *, skip_submit: bool = False) -> None:
    spec = payload.get('selector') or {}
    if skip_submit and spec.get('kind') == 'id' and spec.get('value') in (
        'continue-btn',
        'sign-in',
    ):
        return
    href = (payload.get('href') or '').strip()
    if href and not href.startswith('#') and not href.startswith('javascript:'):
        return
    loc = find_locator(page, payload.get('selector'))
    if loc and loc.count() > 0:
        try:
            loc.first.click(timeout=2000)
            return
        except Exception:
            pass
    text = (payload.get('text') or '').strip()
    if text:
        try:
            page.get_by_role('button', name=text[:48]).first.click(timeout=1500)
        except Exception:
            try:
                page.get_by_role('link', name=text[:48]).first.click(timeout=1500)
            except Exception:
                pass


def ensure_bridge(page: Page) -> None:
    try:
        page.evaluate(BRIDGE)
    except PlaywrightError:
        pass


def clear_mirror_queue(page: Page) -> None:
    try:
        page.evaluate('() => { window.__mirrorQueue = []; }')
    except PlaywrightError:
        pass


def signin_step(page: Page) -> str:
    try:
        return page.evaluate(
            """() => {
              const pw = document.getElementById('step-password');
              if (pw && !pw.hidden) return 'password';
              return 'email';
            }"""
        )
    except PlaywrightError:
        return 'unknown'


def sync_otp_fields(classic: Page, modern: Page) -> None:
    try:
        digits = classic.evaluate(
            """() => Array.from(document.querySelectorAll('.otp-digit')).map((el) => el.value)"""
        )
        if not digits or len(digits) != 6:
            return
        for i, digit in enumerate(digits):
            if not digit:
                continue
            loc = modern.locator(f'#otp-{i}')
            if loc.input_value() != digit:
                loc.fill(digit)
                loc.dispatch_event('input')
    except PlaywrightError:
        pass


def reconcile_signin(classic: Page, modern: Page) -> None:
    """Same URL (/signin) but classic may be on password step before modern."""
    try:
        if signin_step(classic) != 'password' or signin_step(modern) == 'password':
            return
        account = classic.locator('#account').input_value()
        if account:
            modern.locator('#account').fill(account)
        btn = modern.locator('#continue-btn')
        if btn.is_enabled():
            btn.click(timeout=5000)
    except PlaywrightError:
        pass


def sync_modern_to_classic(
    classic: Page,
    modern: Page,
    classic_url: str,
    classic_base: str,
    modern_base: str,
    last_modern_url: str,
) -> str:
    """Navigate modern to match classic path. Returns updated last_modern_url."""
    target = modern_url_for(classic_url, classic_base, modern_base)
    if target == last_modern_url and path_of(classic_url) == path_of(modern.url):
        return last_modern_url
    try:
        modern.goto(target, wait_until='domcontentloaded', timeout=45000)
        try:
            modern.wait_for_load_state('networkidle', timeout=8000)
        except PlaywrightError:
            pass
        return target
    except PlaywrightError:
        return last_modern_url


def run(playwright: Playwright, args: argparse.Namespace) -> None:
    classic_base = args.classic.rstrip('/')
    modern_base = args.modern.rstrip('/')
    start = args.path if args.path.startswith('/') else '/' + args.path

    launch_opts = {'headless': False}
    if args.channel:
        launch_opts['channel'] = args.channel

    browser = playwright.chromium.launch(**launch_opts)
    context = browser.new_context(viewport={'width': 720, 'height': 900})
    classic = context.new_page()
    modern = context.new_page()

    classic.goto(classic_base + start, wait_until='domcontentloaded')
    modern.goto(modern_base + start, wait_until='domcontentloaded')
    ensure_bridge(classic)

    print('Harbor mirror running (Playwright).', flush=True)
    print(f'  Tab 1 — Classic: {classic_base}', flush=True)
    print(f'  Tab 2 — Modern:  {modern_base}', flush=True)
    print('Use the Classic tab; Modern follows. Ctrl+C to stop.\n', flush=True)

    last_classic_path = ''
    last_modern_url = ''
    nav_settle_until = 0.0

    try:
        while True:
            try:
                classic_url = classic.url
                modern_url = modern.url
            except PlaywrightError:
                time.sleep(0.15)
                continue

            classic_path = path_of(classic_url)
            modern_path = path_of(modern_url)
            path_changed = classic_path != last_classic_path

            if path_changed:
                last_classic_path = classic_path
                nav_settle_until = time.time() + 1.25
                try:
                    classic.wait_for_load_state('domcontentloaded', timeout=20000)
                except PlaywrightError:
                    pass
                clear_mirror_queue(classic)
                ensure_bridge(classic)
                last_modern_url = sync_modern_to_classic(
                    classic, modern, classic_url, classic_base, modern_base, last_modern_url
                )
                print(f'  → synced modern to {classic_path}', flush=True)

            elif classic_path != modern_path:
                last_modern_url = sync_modern_to_classic(
                    classic, modern, classic_url, classic_base, modern_base, last_modern_url
                )

            if classic_path == '/signin' and modern_path == '/signin':
                reconcile_signin(classic, modern)

            if classic_path == '/verify' and modern_path == '/verify':
                sync_otp_fields(classic, modern)

            settling = time.time() < nav_settle_until
            events: list = []
            if not settling:
                try:
                    events = classic.evaluate('() => (window.__mirrorQueue || []).splice(0)')
                except PlaywrightError:
                    try:
                        classic.wait_for_load_state('domcontentloaded', timeout=20000)
                        ensure_bridge(classic)
                    except PlaywrightError:
                        pass

            for ev in events:
                etype = ev.get('type')
                payload = ev.get('payload') or {}
                try:
                    if etype == 'input':
                        apply_input(modern, payload)
                    elif etype == 'click':
                        apply_click(modern, payload, skip_submit=settling)
                except PlaywrightError:
                    pass

            time.sleep(max(args.poll_ms, 40) / 1000.0)
    except KeyboardInterrupt:
        print('\nStopped.', flush=True)
    except PlaywrightError as err:
        print(f'\nMirror stopped: {err}', flush=True)
    finally:
        context.close()
        browser.close()


def main() -> int:
    args = parse_args()
    with sync_playwright() as p:
        run(p, args)
    return 0


if __name__ == '__main__':
    sys.exit(main())
