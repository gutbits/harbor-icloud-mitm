#!/usr/bin/env python3
"""
Open two tabs: iCloud (Apple ID sign-in) + Harbor modern.
Run the same sign-in beats on both where possible, then finish the demo on Harbor only
(OTP → trust → success). Apple will not accept demo credentials — that is expected.

Usage:
  python two_tab_compare.py --channel chrome
"""

from __future__ import annotations

import argparse
import sys
import time

from playwright.sync_api import Page, sync_playwright

ICLOUD_HOME = 'https://www.icloud.com/'


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Side-by-side iCloud + Harbor sign-in automation.')
    p.add_argument('--harbor', default='https://harbor-ten-lake.vercel.app')
    p.add_argument('--email', default='compare-demo@example.com')
    p.add_argument('--harbor-password', default='harbor-demo-password')
    p.add_argument('--apple-password', default='not-a-real-apple-password')
    p.add_argument('--channel', default='chrome', help='Playwright channel (chrome, msedge, chromium)')
    p.add_argument('--step-ms', type=int, default=800, help='Pause between mirrored steps')
    p.add_argument('--pause-at-end', action='store_true', help='Open Playwright inspector and wait')
    p.add_argument('--hold-seconds', type=int, default=120, help='Keep browser open after run')
    return p.parse_args()


def apple_auth_frame(page: Page):
    for frame in page.frames:
        if 'idmsa.apple.com' in (frame.url or ''):
            return frame
    return None


def wait_apple_frame(page: Page, seconds: float = 25.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        frame = apple_auth_frame(page)
        if frame:
            return frame
        time.sleep(0.25)
    raise RuntimeError('Apple sign-in iframe did not appear — click Sign In on the iCloud tab if needed.')


def open_icloud_sign_in(apple: Page) -> None:
    apple.goto(ICLOUD_HOME, wait_until='domcontentloaded', timeout=90000)
    apple.wait_for_timeout(2500)
    if apple_auth_frame(apple):
        return
    sign_in = apple.locator('text=Sign In').first
    sign_in.wait_for(state='visible', timeout=45000)
    sign_in.click()
    for _ in range(3):
        apple.wait_for_timeout(3500)
        if apple_auth_frame(apple):
            return
        sign_in.click()
    wait_apple_frame(apple, seconds=45.0)


def beat(label: str, step_ms: int) -> None:
    print(f'  • {label}', flush=True)
    time.sleep(step_ms / 1000.0)


def mirror_email(apple: Page, harbor: Page, frame, email: str, step_ms: int) -> None:
    beat('Email on both tabs', step_ms)
    harbor.bring_to_front()
    harbor.locator('#account').fill(email)
    apple.bring_to_front()
    frame.locator('#account_name_text_field').fill(email)


def mirror_continue(apple: Page, harbor: Page, frame, step_ms: int) -> None:
    beat('Continue on both tabs', step_ms)
    harbor.bring_to_front()
    harbor.locator('#continue-btn').click()
    harbor.wait_for_selector('#step-password:not([hidden])', timeout=15000)
    apple.bring_to_front()
    frame = apple_auth_frame(apple) or frame
    frame.locator('#sign-in').click()
    frame.locator('#password_text_field').wait_for(state='visible', timeout=15000)


def mirror_password_and_submit(
    apple: Page, harbor: Page, frame, harbor_password: str, apple_password: str, step_ms: int
) -> None:
    beat('Password on both tabs', step_ms)
    harbor.bring_to_front()
    harbor.locator('#password').fill(harbor_password)
    apple.bring_to_front()
    frame = apple_auth_frame(apple) or frame
    frame.locator('#password_text_field').fill(apple_password)

    beat('Sign in on both tabs (Harbor continues; Apple will reject demo password)', step_ms)
    harbor.bring_to_front()
    harbor.locator('#continue-btn').click()
    apple.bring_to_front()
    frame = apple_auth_frame(apple) or frame
    frame.locator('#sign-in').click()


def harbor_finish_flow(harbor: Page, step_ms: int) -> None:
    harbor.bring_to_front()
    harbor.wait_for_url('**/verify**', timeout=20000)
    beat('OTP on Harbor only (any 6 digits)', step_ms)
    for i in range(6):
        harbor.locator(f'#otp-{i}').fill(str((i + 1) % 10))
    harbor.wait_for_url('**/session**', timeout=20000)
    beat('Trust browser on Harbor', step_ms)
    harbor.locator('#trust-yes').click()
    harbor.wait_for_url('**/success**', timeout=10000)
    beat('Success → home on Harbor', step_ms)
    harbor.locator('a.trust-btn--fill').click()
    harbor.wait_for_url(
        lambda url: '/success' not in url and '/session' not in url and '/verify' not in url,
        timeout=15000,
    )


def run() -> int:
    args = parse_args()
    harbor_base = args.harbor.rstrip('/')

    launch_opts: dict = {'headless': False, 'slow_mo': 60}
    if args.channel:
        launch_opts['channel'] = args.channel

    print('Opening Chrome: Tab 1 = iCloud, Tab 2 = Harbor', flush=True)
    print(f'  Harbor: {harbor_base}/signin', flush=True)
    print(f'  Email:  {args.email}\n', flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_opts)
        context = browser.new_context(viewport={'width': 960, 'height': 900})
        apple = context.new_page()
        harbor = context.new_page()

        open_icloud_sign_in(apple)
        frame = wait_apple_frame(apple)
        harbor.goto(f'{harbor_base}/signin', wait_until='domcontentloaded', timeout=90000)

        mirror_email(apple, harbor, frame, args.email, args.step_ms)
        mirror_continue(apple, harbor, frame, args.step_ms)
        mirror_password_and_submit(
            apple, harbor, frame, args.harbor_password, args.apple_password, args.step_ms
        )

        print('\nHarbor-only: verification and post-login screens…\n', flush=True)
        harbor_finish_flow(harbor, args.step_ms)

        print('\nDone.', flush=True)
        print('  Harbor finished the demo path.', flush=True)
        print('  iCloud tab: expect an auth error (demo password) — compare UI at each step.\n', flush=True)

        if args.pause_at_end:
            harbor.pause()
        else:
            if args.hold_seconds > 0:
                print(
                    f'Browser stays open {args.hold_seconds}s — compare tabs, then close the window.',
                    flush=True,
                )
                time.sleep(args.hold_seconds)

        context.close()
        browser.close()

    return 0


if __name__ == '__main__':
    sys.exit(run())
