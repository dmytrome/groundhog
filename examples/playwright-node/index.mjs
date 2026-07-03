// Playwright (Node) over CDP, hardened to pass bot detectors.
//
// Two library tells to clear when driving with Playwright:
//   1. the CDP Runtime.enable leak (isAutomatedWithCDP) — use the rebrowser-patched
//      client and set REBROWSER_PATCHES_RUNTIME_FIX_MODE=addBinding;
//   2. Playwright's window globals (isPlaywright) — delete them in an init script.
// The container already provides the fingerprint stealth (UA, WebGL, timezone).
//
//   npm install
//   REBROWSER_PATCHES_RUNTIME_FIX_MODE=addBinding node index.mjs
//
// CDP_URL defaults to http://127.0.0.1:9222.

import { chromium } from 'rebrowser-playwright-core';

const CDP_URL = process.env.CDP_URL || 'http://127.0.0.1:9222';

const browser = await chromium.connectOverCDP(CDP_URL);
try {
  const context = await browser.newContext();
  // The globals deviceandbrowserinfo checks for `isPlaywright`.
  await context.addInitScript(() => {
    try { delete window.__pwInitScripts; } catch {}
    try { delete window.__playwright__binding__; } catch {}
  });
  const page = await context.newPage();

  await page.goto('https://deviceandbrowserinfo.com/are_you_a_bot', {
    waitUntil: 'domcontentloaded',
  });
  await page.waitForTimeout(6000);
  await page.screenshot({ path: 'result.png', fullPage: true });
  console.log('saved result.png');
} finally {
  await browser.close();
}
