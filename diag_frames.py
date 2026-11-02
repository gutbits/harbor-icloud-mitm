#!/usr/bin/env python3
"""Diagnostic: dump icloud.com frame structure + locate the real sign-in fields."""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch(channel='chrome', headless=True)
    ctx = b.new_context(viewport={'width': 1000, 'height': 940})
    page = ctx.new_page()
    page.goto('https://www.icloud.com/', wait_until='domcontentloaded', timeout=60000)
    page.wait_for_timeout(2500)
    try:
        page.locator('text=Sign In').first.click(timeout=10000)
        print('clicked Sign In')
    except Exception as e:
        print('sign-in click err:', repr(e))
    for _ in range(8):
        page.wait_for_timeout(2000)
        if any('idmsa' in (f.url or '') for f in page.frames):
            break

    print('=== FRAMES ===')
    for f in page.frames:
        print('  ', repr(f.url))

    print('=== FIELD SEARCH (per frame) ===')
    for f in page.frames:
        url = f.url or ''
        counts = {}
        for sel in ['#account_name_text_field', 'input[type="email"]', '#password_text_field',
                    '#sign-in', 'input[name*="account"]', 'input[name*="password"]']:
            try:
                counts[sel] = f.locator(sel).count()
            except Exception:
                counts[sel] = -1
        nonzero = {k: v for k, v in counts.items() if v}
        if nonzero:
            print('  frame', url, '->', nonzero)
        else:
            print('  frame', url, '-> (no known fields)')

    print('=== TOP-LEVEL INPUTS (main frame) ===')
    try:
        inputs = page.evaluate(
            "() => Array.from(document.querySelectorAll('input')).map(i => "
            "{id:i.id, name:i.name, type:i.type, ph:i.placeholder, auto:i.autocomplete})"
        )
        for i in inputs:
            print('  ', i)
    except Exception as e:
        print('  eval err:', repr(e))

    b.close()
