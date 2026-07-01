# Examples

Each example connects to the container's CDP endpoint, **sets a realistic
User-Agent and viewport** (the container does not — that is the caller's job),
opens a detector page, and saves a screenshot.

Start the container first:

```bash
docker compose up --build -d   # from the repo root
```

> Use `127.0.0.1`, not `localhost`. Some clients (Playwright) resolve
> `localhost` to IPv6 `::1`, which the container does not listen on.

| Example | Language | Connect API |
| --- | --- | --- |
| [puppeteer](puppeteer) | Node | `puppeteer.connect({ browserURL })` |
| [playwright-node](playwright-node) | Node | `chromium.connectOverCDP(url)` |
| [playwright-python](playwright-python) | Python | `chromium.connect_over_cdp(url)` |
| [selenium-python](selenium-python) | Python | `debuggerAddress` |
| [python-raw-cdp](python-raw-cdp) | Python | raw DevTools over WebSocket |
| [go-chromedp](go-chromedp) | Go | `chromedp.NewRemoteAllocator` |

Other CDP-capable crawlers and frameworks: see [OTHER_TOOLS.md](OTHER_TOOLS.md).
