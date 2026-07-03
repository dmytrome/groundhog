# Examples

Each example connects to the container's CDP endpoint, opens a detector page, and
saves a screenshot. The container sets a clean User-Agent at launch, so clients no
longer need to override it (and for stealth, shouldn't).

Start the container first:

```bash
docker compose up --build -d   # from the repo root
```

> Use `127.0.0.1`, not `localhost`. Some clients (Playwright) resolve
> `localhost` to IPv6 `::1`, which the container does not listen on.

> **On the CDP-automation tell:** Puppeteer/Playwright/Selenium enable the CDP
> `Runtime` domain, which anti-bots detect (`isAutomatedWithCDP`) even against this
> container. `python-raw-cdp` avoids it by never enabling `Runtime` (as Groundhog's
> MCP server does); for the library clients, use a patched variant
> ([rebrowser-patches](https://github.com/rebrowser/rebrowser-patches)) to clear that
> signal. The container's *fingerprint* stealth (UA, WebGL, timezone) applies to every
> client regardless.

| Example | Language | Connect API |
| --- | --- | --- |
| [puppeteer](puppeteer) | Node | `puppeteer.connect({ browserURL })` |
| [playwright-node](playwright-node) | Node | `chromium.connectOverCDP(url)` |
| [playwright-python](playwright-python) | Python | `chromium.connect_over_cdp(url)` |
| [selenium-python](selenium-python) | Python | `debuggerAddress` |
| [python-raw-cdp](python-raw-cdp) | Python | raw DevTools over WebSocket |
| [go-chromedp](go-chromedp) | Go | `chromedp.NewRemoteAllocator` |

Other CDP-capable crawlers and frameworks: see [OTHER_TOOLS.md](OTHER_TOOLS.md).
