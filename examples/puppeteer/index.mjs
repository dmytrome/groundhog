// Puppeteer over CDP. The container does not set a User-Agent, so we must.
//
//   npm install && node index.mjs
//
// CDP_URL defaults to http://127.0.0.1:9222.

import puppeteer from 'puppeteer-core';

const CDP_URL = process.env.CDP_URL || 'http://127.0.0.1:9222';
const REAL_UA =
  'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) ' +
  'Chrome/149.0.0.0 Safari/537.36';

const browser = await puppeteer.connect({ browserURL: CDP_URL });
try {
  const page = await browser.newPage();
  await page.setUserAgent(REAL_UA);
  await page.setViewport({ width: 1920, height: 1080 });

  await page.goto('https://bot.sannysoft.com/', { waitUntil: 'networkidle2' });
  await page.screenshot({ path: 'sannysoft.png' });
  console.log('saved sannysoft.png — UA:', await page.evaluate(() => navigator.userAgent));
} finally {
  await browser.disconnect();
}
