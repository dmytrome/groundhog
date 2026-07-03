// Puppeteer over CDP, hardened to pass bot detectors.
//
// deviceandbrowserinfo has no isPuppeteer check, so Puppeteer only needs the CDP
// Runtime.enable leak cleared (the rebrowser-patched client + fix mode). The init
// script also drops any stray automation globals to be safe. The container
// provides the fingerprint stealth (UA, WebGL, timezone).
//
//   npm install
//   REBROWSER_PATCHES_RUNTIME_FIX_MODE=addBinding node index.mjs
//
// CDP_URL defaults to http://127.0.0.1:9222.

import puppeteer from 'rebrowser-puppeteer-core';

const CDP_URL = process.env.CDP_URL || 'http://127.0.0.1:9222';

const browser = await puppeteer.connect({ browserURL: CDP_URL });
try {
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => {
    for (const k of Object.keys(window)) {
      if (/^__pw|__playwright|__puppeteer|^cdc_|\$cdc_/.test(k)) {
        try { delete window[k]; } catch {}
      }
    }
  });

  await page.goto('https://deviceandbrowserinfo.com/are_you_a_bot', {
    waitUntil: 'domcontentloaded',
  });
  await new Promise((r) => setTimeout(r, 6000));
  await page.screenshot({ path: 'result.png', fullPage: true });
  console.log('saved result.png');
} finally {
  await browser.disconnect();
}
