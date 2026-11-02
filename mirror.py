#!/usr/bin/env python3
"""
Mirror user actions from Harbor Classic (tab 1) to Harbor Modern (tab 2) via Selenium.
No server/API between sites — only browser automation reading the classic DOM and replaying on modern.
"""

from __future__ import annotations

import argparse
import sys
import time
from urllib.parse import urlparse

from selenium import webdriver
from selenium.common.exceptions import (
    ElementNotInteractableException,
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException,
)
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

INJECT_BRIDGE = r"""
(function () {
  if (window.__harborMirrorInstalled) return;
  window.__harborMirrorInstalled = true;
  window.__mirrorQueue = window.__mirrorQueue || [];

  function push(type, payload) {
    window.__mirrorQueue.push({ type: type, payload: payload, t: Date.now() });
  }

  function selectorFor(el) {
    if (!el || !el.tagName) return null;
    if (el.id) return { kind: 'id', value: el.id };
    if (el.name) return { kind: 'name', value: el.name };
    if (el.dataset && el.dataset.otpIndex !== undefined) {
      return { kind: 'css', value: '[data-otp-index="' + el.dataset.otpIndex + '"]' };
    }
    if (el.classList && el.classList.length) {
      return { kind: 'css', value: el.tagName.toLowerCase() + '.' + Array.from(el.classList).slice(0, 3).join('.') };
    }
    return null;
  }

  document.addEventListener(
    'input',
    function (e) {
      var t = e.target;
      if (!t || !/^(INPUT|TEXTAREA|SELECT)$/i.test(t.tagName)) return;
      push('input', {
        selector: selectorFor(t),
        value: t.value,
        checked: t.checked,
        inputType: t.type || ''
      });
    },
    true
  );

  document.addEventListener(
    'change',
    function (e) {
      var t = e.target;
      if (!t || !/^(INPUT|SELECT|TEXTAREA)$/i.test(t.tagName)) return;
      push('input', {
        selector: selectorFor(t),
        value: t.value,
        checked: t.checked,
        inputType: t.type || ''
      });
    },
    true
  );

  document.addEventListener(
    'click',
    function (e) {
      var t = e.target.closest(
        'button, a.trust-btn, a.primary, a.sign-in-link, .quick-access-btn, .help-menu-button, .verify-link'
      );
      if (!t) return;
      push('click', {
        selector: selectorFor(t),
        tag: t.tagName.toLowerCase(),
        text: (t.innerText || t.textContent || '').trim().slice(0, 80),
        href: t.getAttribute('href') || ''
      });
    },
    true
  );
})();
"""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Mirror Harbor Classic → Modern in a second browser tab (Selenium).')
    p.add_argument(
        '--classic',
        default='https://harbor-classic.vercel.app',
        help='Classic (dark-only) base URL',
    )
    p.add_argument(
        '--modern',
        default='https://harbor-ten-lake.vercel.app',
        help='Modern (themed) base URL',
    )
    p.add_argument('--path', default='/signin', help='Initial path on both sites (e.g. /, /signin)')
    p.add_argument('--poll-ms', type=int, default=120, help='DOM poll interval in ms')
    p.add_argument('--headless', action='store_true', help='Run Chrome headless (not recommended for manual use)')
    return p.parse_args()


def modern_url_for_classic(classic_url: str, classic_base: str, modern_base: str) -> str:
    if not classic_url.startswith(classic_base):
        return modern_base + '/'
    suffix = classic_url[len(classic_base) :] or '/'
    if not suffix.startswith('/'):
        suffix = '/' + suffix
    return modern_base.rstrip('/') + suffix.split('?')[0].split('#')[0]


def find_element(driver: webdriver.Chrome, spec: dict | None):
    if not spec:
        return None
    kind = spec.get('kind')
    value = spec.get('value')
    if not value:
        return None
    if kind == 'id':
        return driver.find_element(By.ID, value)
    if kind == 'name':
        return driver.find_element(By.NAME, value)
    if kind == 'css':
        return driver.find_element(By.CSS_SELECTOR, value)
    return None


def apply_input(driver: webdriver.Chrome, payload: dict) -> None:
    el = find_element(driver, payload.get('selector'))
    if not el:
        return
    value = payload.get('value', '')
    input_type = (payload.get('inputType') or '').lower()
    try:
        if input_type in ('checkbox', 'radio'):
            checked = bool(payload.get('checked'))
            if el.is_selected() != checked:
                el.click()
            return
        el.clear()
        el.send_keys(value)
    except (ElementNotInteractableException, StaleElementReferenceException):
        pass


def apply_click(driver: webdriver.Chrome, payload: dict) -> None:
    href = (payload.get('href') or '').strip()
    if href and not href.startswith('#') and not href.startswith('javascript:'):
        return
    el = find_element(driver, payload.get('selector'))
    if el:
        try:
            el.click()
            return
        except (ElementNotInteractableException, StaleElementReferenceException):
            pass
    text = (payload.get('text') or '').strip()
    if not text:
        return
    short = text[:48]
    try:
        for by, arg in (
            (By.PARTIAL_LINK_TEXT, short),
            (By.XPATH, f"//button[contains(normalize-space(.), '{short[:20]}')]"),
        ):
            try:
                el = driver.find_element(by, arg)
                el.click()
                return
            except NoSuchElementException:
                continue
    except (ElementNotInteractableException, StaleElementReferenceException):
        pass


def ensure_bridge(driver: webdriver.Chrome) -> None:
    driver.execute_script(INJECT_BRIDGE)


def main() -> int:
    args = parse_args()
    classic_base = args.classic.rstrip('/')
    modern_base = args.modern.rstrip('/')
    start_path = args.path if args.path.startswith('/') else '/' + args.path

    options = webdriver.ChromeOptions()
    options.add_argument('--window-size=1400,900')
    if args.headless:
        options.add_argument('--headless=new')

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    try:
        driver.get(classic_base + start_path)
        classic_handle = driver.current_window_handle
        driver.switch_to.new_window('tab')
        driver.get(modern_base + start_path)
        modern_handle = driver.current_window_handle

        driver.set_window_rect(x=20, y=20, width=700, height=900)
        driver.switch_to.window(classic_handle)
        driver.set_window_rect(x=740, y=20, width=700, height=900)

        last_classic_url = ''
        last_modern_synced = ''

        print('Harbor mirror running.')
        print(f'  Classic (you): {classic_base}')
        print(f'  Modern (mirror): {modern_base}')
        print('Interact with the Classic window; Modern tab stays in sync. Ctrl+C to stop.\n')

        while True:
            driver.switch_to.window(classic_handle)
            try:
                classic_url = driver.current_url
            except WebDriverException:
                time.sleep(0.2)
                continue

            if classic_url != last_classic_url:
                last_classic_url = classic_url
                ensure_bridge(driver)
                target = modern_url_for_classic(classic_url, classic_base, modern_base)
                if target != last_modern_synced:
                    driver.switch_to.window(modern_handle)
                    driver.get(target)
                    last_modern_synced = target
                    time.sleep(0.35)
                    driver.switch_to.window(classic_handle)
                    ensure_bridge(driver)

            try:
                events = driver.execute_script(
                    'return window.__mirrorQueue ? window.__mirrorQueue.splice(0) : [];'
                )
            except WebDriverException:
                events = []

            if events:
                driver.switch_to.window(modern_handle)
                for ev in events:
                    etype = ev.get('type')
                    payload = ev.get('payload') or {}
                    if etype == 'input':
                        apply_input(driver, payload)
                    elif etype == 'click':
                        apply_click(driver, payload)
                driver.switch_to.window(classic_handle)

            time.sleep(max(args.poll_ms, 50) / 1000.0)

    except KeyboardInterrupt:
        print('\nStopped.')
        return 0
    finally:
        driver.quit()


if __name__ == '__main__':
    sys.exit(main())
