"""Playwright (Python) over CDP, hardened to pass bot detectors.

Two library tells to clear when driving with Playwright:
  1. the CDP Runtime.enable leak (isAutomatedWithCDP) — use the rebrowser-patched
     client and set REBROWSER_PATCHES_RUNTIME_FIX_MODE=addBinding;
  2. Playwright's window globals (isPlaywright) — delete them in an init script.
The container already provides the fingerprint stealth (UA, WebGL, timezone).

    pip install -r requirements.txt
    REBROWSER_PATCHES_RUNTIME_FIX_MODE=addBinding python main.py

CDP_URL defaults to http://127.0.0.1:9222.
"""

import os

from rebrowser_playwright.sync_api import sync_playwright

CDP_URL = os.environ.get("CDP_URL", "http://127.0.0.1:9222")

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(CDP_URL)
    try:
        context = browser.new_context()
        # The globals deviceandbrowserinfo checks for `isPlaywright`.
        context.add_init_script(
            "try{delete window.__pwInitScripts}catch(e){}"
            "try{delete window.__playwright__binding__}catch(e){}"
        )
        page = context.new_page()
        page.goto(
            "https://deviceandbrowserinfo.com/are_you_a_bot",
            wait_until="domcontentloaded",
        )
        page.wait_for_timeout(6000)
        page.screenshot(path="result.png", full_page=True)
        print("saved result.png")
    finally:
        browser.close()
