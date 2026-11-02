"""Open icloud.com home in Chrome with forced light appearance."""
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

URL = "https://www.icloud.com/"

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=False)
    context = browser.new_context(
        color_scheme="light",
        viewport={"width": 1280, "height": 900},
    )
    page = context.new_page()
    page.goto(URL, wait_until="domcontentloaded", timeout=120_000)
    try:
        page.wait_for_timeout(3_600_000)
    except (KeyboardInterrupt, PlaywrightError):
        pass
    finally:
        try:
            browser.close()
        except PlaywrightError:
            pass
