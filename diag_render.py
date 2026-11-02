from playwright.sync_api import sync_playwright

p = sync_playwright().start()
b = p.chromium.launch(channel='chrome', headless=True)
pg = b.new_page()
pg.goto('http://127.0.0.1:8099/signin.html', wait_until='networkidle', timeout=30000)
print('title:', pg.title())
print('heading:', pg.locator('#sign-in-title').inner_text())
print('account input:', pg.locator('#account').count(), '| password input:', pg.locator('#password').count())
print('login.css linked:', pg.evaluate("() => !!document.querySelector('link[href=\"login.css\"]')"))
print('login.css applied (body bg):', pg.evaluate("() => getComputedStyle(document.body).backgroundColor"))
b.close()
p.stop()
