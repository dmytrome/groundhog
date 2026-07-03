# Examples

Each example connects to the container's CDP endpoint, opens a detector page, and
saves a screenshot. The container sets a clean User-Agent at launch, so clients no
longer need to override it (and for stealth, shouldn't).

Start the container first:

```bash
docker compose up --build -d          # from the repo root
```

> Use `127.0.0.1`, not `localhost`. Some clients (Playwright) resolve
> `localhost` to IPv6 `::1`, which the container does not listen on.

## Passing bot detectors

The container provides the *fingerprint* stealth (UA, WebGL, timezone) for every
client. But automation **libraries** add their own tells that you clear client-side:

- **CDP `Runtime.enable` leak** (`isAutomatedWithCDP`) — Puppeteer/Playwright/Selenium
  enable the Runtime domain. Clear it with the rebrowser-patched client
  (`REBROWSER_PATCHES_RUNTIME_FIX_MODE=addBinding`). chromedriver and chromedp enable
  Runtime with no supported way to stop it — those cannot clear this tell.
- **Library globals** (`isPlaywright` = `__pwInitScripts` / `__playwright__binding__`;
  `isSeleniumChromeDefault` = chromedriver's `cdc_*`) — delete them in an init script.

deviceandbrowserinfo has **no** `isPuppeteer` check, so Puppeteer only needs the CDP fix.

| Example | Fully passes `deviceandbrowserinfo`? | Recipe |
| --- | --- | --- |
| [python-raw-cdp](python-raw-cdp) | ✅ yes | raw CDP, never enables Runtime — the reference |
| [playwright-node](playwright-node) | ✅ yes | rebrowser-playwright-core + delete `__pw*` globals |
| [playwright-python](playwright-python) | ✅ yes | rebrowser-playwright + delete `__pw*` globals |
| [puppeteer](puppeteer) | ✅ yes | rebrowser-puppeteer-core (no isPuppeteer check) |
| [selenium-python](selenium-python) | ◐ partial | strips `cdc_`, but chromedriver's `isAutomatedWithCDP` remains — use raw CDP / SeleniumBase CDP Mode for a full pass |
| [go-chromedp](go-chromedp) | ◐ partial | chromedp enables Runtime → `isAutomatedWithCDP`; use raw CDP in Go for a full pass |

The rebrowser examples need `REBROWSER_PATCHES_RUNTIME_FIX_MODE=addBinding` in the
environment (see each example's header).

Other CDP-capable crawlers and frameworks: see [OTHER_TOOLS.md](OTHER_TOOLS.md).
