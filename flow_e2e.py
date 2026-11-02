#!/usr/bin/env python3
"""Harbor modern production: signin → verify → session → success → home."""

from playwright.sync_api import sync_playwright

BASE = 'https://harbor-ten-lake.vercel.app'
TEST_EMAIL = 'flow-e2e@example.com'


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f'{BASE}/signin', wait_until='networkidle', timeout=60000)

        page.locator('#account').fill(TEST_EMAIL)
        page.locator('#continue-btn').click()
        page.wait_for_selector('#step-password:not([hidden])', timeout=5000)

        page.locator('#password').fill('demo-password')
        page.locator('#continue-btn').click()
        page.wait_for_url('**/verify**', timeout=10000)

        for i in range(6):
            page.locator(f'#otp-{i}').fill(str(i + 1))

        page.wait_for_url('**/session**', timeout=15000)
        page.locator('#trust-yes').click()
        page.wait_for_url('**/success**', timeout=5000)

        assert 'Signed in' in page.locator('#success-title').inner_text()
        assert TEST_EMAIL in page.locator('#success-account').inner_text()

        page.locator('a.trust-btn--fill').click()
        page.wait_for_url(
            lambda url: url.rstrip('/').endswith('harbor-ten-lake.vercel.app'),
            timeout=10000,
        )

        print('flow_e2e: OK — modern full path on production')
        browser.close()


if __name__ == '__main__':
    main()
