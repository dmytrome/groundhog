"""Playwright (Python) over CDP.

The container does not set a User-Agent, so we set it (and the viewport) on the
context.

    pip install -r requirements.txt
    python main.py

CDP_URL defaults to http://127.0.0.1:9222.
"""

import os

from playwright.sync_api import sync_playwright

CDP_URL = os.environ.get("CDP_URL", "http://127.0.0.1:9222")
REAL_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36"
)

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(CDP_URL)
    try:
        context = browser.new_context(
            user_agent=REAL_UA,
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()
        page.goto("https://bot.sannysoft.com/", wait_until="networkidle")
        page.screenshot(path="sannysoft.png", full_page=True)
        print("saved sannysoft.png — UA:", page.evaluate("() => navigator.userAgent"))
    finally:
        browser.close()
