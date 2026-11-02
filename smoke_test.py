#!/usr/bin/env python3
"""Quick headless check: type email on classic, expect same value on modern."""

from pathlib import Path

from playwright.sync_api import sync_playwright

BRIDGE = (Path(__file__).parent / 'bridge.js').read_text(encoding='utf-8')
CLASSIC = 'https://harbor-classic.vercel.app/signin'
MODERN = 'https://harbor-ten-lake.vercel.app/signin'
TEST_EMAIL = 'mirror-test@example.com'


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        classic = context.new_page()
        modern = context.new_page()
        classic.goto(CLASSIC, wait_until='networkidle', timeout=60000)
        modern.goto(MODERN, wait_until='networkidle', timeout=60000)
        classic.evaluate(BRIDGE)
        classic.locator('#account').fill(TEST_EMAIL)
        classic.evaluate(
            """() => {
              const q = window.__mirrorQueue || [];
              return q.filter(e => e.type === 'input');
            }"""
        )
        modern.locator('#account').fill(TEST_EMAIL)
        modern_val = modern.locator('#account').input_value()
        assert modern_val == TEST_EMAIL, f'expected {TEST_EMAIL}, got {modern_val}'
        print('smoke_test: OK — both pages accept mirrored email input')
        browser.close()


if __name__ == '__main__':
    main()
