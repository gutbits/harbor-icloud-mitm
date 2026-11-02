#!/usr/bin/env python3
"""Email then password on real iCloud (no real creds required for length check)."""
import os
import time

from playwright.sync_api import sync_playwright

import apple_drive as ad
import phish_mirror as pm

EMAIL = os.environ.get("TEST_APPLE_EMAIL", "gutbits@gmail.com")
PASSWORD = os.environ.get("TEST_APPLE_PASSWORD", "TestMirrorPw9!")


with sync_playwright() as p:
    browser = p.chromium.launch(
        channel="chrome",
        headless=False,
        ignore_default_args=["--enable-automation"],
        args=["--disable-blink-features=AutomationControlled"],
    )
    ctx = browser.new_context(viewport={"width": 1000, "height": 940})
    ctx.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    page = ctx.new_page()
    frame = pm.open_icloud_sign_in(page)
    ok1, frame = ad.drive_email(page, frame, EMAIL, pm.find_apple_frame)
    print("drive_email:", ok1)
    ok2, frame = ad.drive_password(
        page,
        frame,
        PASSWORD,
        EMAIL,
        pm.find_apple_frame,
        password_submit_ok=pm._apple_password_submit_ok,
    )
    print("drive_password:", ok2)
    print("password len on apple:", ad.read_password_length(frame))
    time.sleep(6)
    browser.close()
