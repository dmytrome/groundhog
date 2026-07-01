// Playwright (Node) over CDP. The container does not set a User-Agent, so we
// set it (and the viewport) on the context.
//
//   npm install && node index.mjs
//
// CDP_URL defaults to http://127.0.0.1:9222.

import { chromium } from 'playwright-core';

const CDP_URL = process.env.CDP_URL || 'http://127.0.0.1:9222';
const REAL_UA =
  'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) ' +
  'Chrome/149.0.0.0 Safari/537.36';

const browser = await chromium.connectOverCDP(CDP_URL);
try {
  const context = await browser.newContext({
    userAgent: REAL_UA,
    viewport: { width: 1920, height: 1080 },
  });
  const page = await context.newPage();

  await page.goto('https://bot.sannysoft.com/', { waitUntil: 'networkidle' });
  await page.screenshot({ path: 'sannysoft.png', fullPage: true });
  console.log('saved sannysoft.png — UA:', await page.evaluate(() => navigator.userAgent));
} finally {
  await browser.close();
}
