// Live integration test: connect to the running container over CDP and assert
// it passes real anti-bot detectors, saving a full-page screenshot of each as
// proof. Requires the container to be up (`docker compose up -d`) and network
// access to the detection sites.
//
//   CDP_URL=http://127.0.0.1:9222 npm test
//
// This mirrors real usage: a client connects, sets a realistic User-Agent and
// viewport, then browses. The container alone does not override the UA — that
// is the caller's job (see the project README).
//
// Implemented as a plain script with an explicit exit code rather than
// `node --test`: Playwright keeps a persistent driver connection that makes the
// node test runner drop its buffered output when stdout is a pipe (e.g. in CI).

import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { chromium } from 'playwright-core';

const CDP_URL = process.env.CDP_URL || 'http://127.0.0.1:9222';
const REAL_UA =
  'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) ' +
  'Chrome/149.0.0.0 Safari/537.36';
const SHOT_DIR = join(dirname(fileURLToPath(import.meta.url)), 'screenshots');

async function withPage(browser, fn) {
  const context = await browser.newContext({
    userAgent: REAL_UA,
    viewport: { width: 1920, height: 1080 },
  });
  try {
    return await fn(await context.newPage());
  } finally {
    await context.close();
  }
}

const tests = {
  async 'bot.sannysoft.com: every check passes'(browser) {
    await withPage(browser, async (page) => {
      await page.goto('https://bot.sannysoft.com/', { waitUntil: 'networkidle', timeout: 60000 });
      const fail = await page.evaluate(() => {
        const out = [];
        for (const row of document.querySelectorAll('table tr')) {
          const cells = row.querySelectorAll('td');
          if (cells.length < 2) continue;
          const label = cells[0].innerText.trim().replace(/\s+/g, ' ');
          if (label && (cells[1].className || '').toLowerCase().includes('fail')) out.push(label);
        }
        return out;
      });
      await page.screenshot({ path: join(SHOT_DIR, 'sannysoft.png'), fullPage: true });
      assert.deepEqual(fail, [], `sannysoft reported failing checks: ${fail.join(', ')}`);
    });
  },

  async 'areyouheadless: not detected as headless'(browser) {
    await withPage(browser, async (page) => {
      await page.goto('https://arh.antoinevastel.com/bots/areyouheadless', {
        waitUntil: 'networkidle',
        timeout: 60000,
      });
      const verdict = await page.evaluate(() => {
        const m = document.body.innerText.match(/You are (not )?(a )?chrome headless/i);
        return m ? m[0] : '(verdict not found)';
      });
      await page.screenshot({ path: join(SHOT_DIR, 'areyouheadless.png'), fullPage: true });
      assert.match(verdict, /not chrome headless/i, `unexpected verdict: ${verdict}`);
    });
  },

  // The stealth content script matches <all_urls>, which excludes about:blank;
  // crawl targets are always real http(s) pages, so assert there.
  async 'deviceMemory is present on a real page'(browser) {
    await withPage(browser, async (page) => {
      await page.goto('https://example.com/', { waitUntil: 'domcontentloaded', timeout: 60000 });
      const deviceMemory = await page.evaluate(() => navigator.deviceMemory);
      assert.equal(typeof deviceMemory, 'number', 'navigator.deviceMemory should be a number');
    });
  },
};

const browser = await chromium.connectOverCDP(CDP_URL);
let failed = 0;
try {
  await mkdir(SHOT_DIR, { recursive: true });
  for (const [name, fn] of Object.entries(tests)) {
    try {
      await fn(browser);
      console.log(`✔ ${name}`);
    } catch (err) {
      failed++;
      console.error(`✘ ${name}\n  ${err.message}`);
    }
  }
} finally {
  await browser.close();
}

console.log(`\n${Object.keys(tests).length - failed} passed, ${failed} failed`);
// Set exitCode rather than calling process.exit(), which would truncate the
// buffered stdout above when it is a pipe (e.g. CI). browser.close() already
// drains the CDP connection so the event loop empties and the process exits.
process.exitCode = failed ? 1 : 0;
