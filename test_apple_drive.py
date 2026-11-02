#!/usr/bin/env python3
"""End-to-end Apple email drive (matches production mirror launch)."""
import time

from playwright.sync_api import sync_playwright

import apple_drive as ad
import phish_mirror as pm

EMAIL = "gutbits@gmail.com"

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
    print("account visible:", ad.account_field_visible(frame))
    print("password visible:", ad.password_field_visible(frame))
    print("password_only:", ad.password_only_step(frame))
    ok, frame = ad.drive_email(page, frame, EMAIL, pm.find_apple_frame)
    print("drive_email:", ok)
    print("password_only after:", ad.password_only_step(frame))
    print("account on apple:", ad.read_account_email(frame))
    time.sleep(8)
    browser.close()
