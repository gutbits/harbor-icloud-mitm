#!/usr/bin/env python3
"""Test trusted mouse click on Apple Continue after sequential typing."""
import time

from playwright.sync_api import sync_playwright

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
    apple = ctx.new_page()
    frame = pm.open_icloud_sign_in(apple)
    field = frame.locator("#account_name_text_field")
    field.click()
    field.press("Control+a")
    field.press_sequentially(EMAIL, delay=55)
    time.sleep(0.8)
    btn = frame.locator("#sign-in")
    box = btn.bounding_box()
    print("button box:", box)
    if box:
        apple.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    else:
        btn.click()
    for wait in (2, 5, 10, 20, 30):
        time.sleep(2 if wait == 2 else 2)
        frame = pm.find_apple_frame(apple, frame)
        st = pm.probe_apple_frame(frame) or {}
        print(
            f"@{wait}s step={st.get('step')} pw={pm.apple_password_step_ready(frame)} "
            f"loading={st.get('loading')} err={(st.get('errors') or [])[:1]}"
        )
        if pm.apple_password_step_ready(frame):
            print("SUCCESS password step")
            break
    time.sleep(5)
    browser.close()
