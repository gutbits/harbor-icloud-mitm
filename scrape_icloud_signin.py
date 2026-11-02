"""One-off: scrape icloud.com sign-in chrome for Harbor 1:1 styling."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent / "captured" / "icloud_signin_styles.json"


def main() -> int:
    data: dict = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto("https://www.icloud.com/", wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(3000)
        # Open sign in if needed
        for sel in [
            'button:has-text("Sign In")',
            'a:has-text("Sign In")',
            '[data-testid="sign-in"]',
            'ui-button:has-text("Sign In")',
        ]:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=2000):
                    loc.click(timeout=5000)
                    page.wait_for_timeout(4000)
                    break
            except Exception:
                continue

        frame = page.frame_locator('iframe[name="aid-auth-widget"], iframe#aid-auth-widget').first
        try:
            frame.locator("#account_name_text_field").wait_for(timeout=15000)
        except Exception:
            pass

        data["url"] = page.url
        data["title"] = page.title()

        def grab_css(page_or_frame, selector: str, label: str) -> None:
            try:
                loc = page_or_frame.locator(selector).first
                if loc.count() == 0:
                    return
                styles = loc.evaluate(
                    """(el) => {
                    const cs = getComputedStyle(el);
                    const keys = [
                      'backgroundColor','color','borderRadius','boxShadow','padding',
                      'margin','width','maxWidth','fontSize','fontWeight','letterSpacing',
                      'lineHeight','border','display','flexDirection','gap','height'
                    ];
                    const out = {};
                    for (const k of keys) out[k] = cs[k];
                    return out;
                }"""
                )
                data[label] = styles
            except Exception as e:
                data[label] = {"error": str(e)}

        grab_css(page, "body", "body")
        grab_css(page, ".home-login-component, .login-container, .widget-container, main", "main_candidates")

        # Try iframe auth widget
        for sel, label in [
            (".parent-container.is-visible, .parent-container", "page_card"),
            ("button#sign-in, #sign-in, button.signin-button", "submit_btn"),
            ("button.sign-in-with-iphone, .siwa, [id*='iphone']", "iphone_btn2"),
            (".create-link, a.create, #create-link", "create_link"),
        ]:
            grab_css(page, sel, label)

        for sel, label in [
            (".widget-container", "widget_container"),
            (".sign-in", "sign_in_root"),
            (".form-textbox", "form_textbox"),
            ("#account_name_text_field", "account_input"),
            ("button[type='submit']", "submit_btn"),
            (".sign-in-with-iphone, [class*='iphone']", "iphone_btn"),
        ]:
            try:
                grab_css(frame, sel, f"iframe_{label}")
            except Exception:
                pass

        # Full outer HTML snippet from page
        try:
            data["body_class"] = page.evaluate("document.body.className")
            data["html_snippet"] = page.evaluate(
                """() => {
                const m = document.querySelector('.home-login-component, .login, [class*="login"]');
                return m ? m.outerHTML.slice(0, 8000) : document.body.innerHTML.slice(0, 4000);
            }"""
            )
        except Exception as e:
            data["html_error"] = str(e)

        browser.close()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
