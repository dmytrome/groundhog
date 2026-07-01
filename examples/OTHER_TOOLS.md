# Other CDP-capable tools

Any tool that speaks the Chrome DevTools Protocol can drive this container by
pointing at `http://127.0.0.1:9222` (or `ws://...`). The runnable, verified
examples live in the [examples table](README.md); the entries below are
pointers — check each project's current docs for exact option names, and
remember to **set a User-Agent yourself** in every case.

## crawl4ai (Python)

crawl4ai can attach to an already-running browser over CDP instead of launching
its own, via the `cdp_url` browser option. Point it at
`http://127.0.0.1:9222`. See the crawl4ai docs for the option name in your
version.

## Scrapy + scrapy-playwright (Python)

scrapy-playwright connects over CDP when you set `PLAYWRIGHT_CDP_URL` to
`http://127.0.0.1:9222`. Set the User-Agent via the request/context options.

## go-rod (Go)

go-rod connects to an existing browser with
`rod.New().ControlURL("ws://...").MustConnect()` — resolve the `ws` URL from
`/json/version`. Set the User-Agent with `page.SetUserAgent(...)`.

## Crawlee (Node)

Crawlee's `PlaywrightCrawler` / `PuppeteerCrawler` manage their own browser
lifecycle (launch + fingerprint injection) and do not have a first-class option
to attach to an external CDP endpoint. To use this container with Crawlee-style
crawling, drive it directly with the [Playwright](playwright-node) or
[Puppeteer](puppeteer) examples instead.

## nodriver (Python)

nodriver normally launches its own Chrome. It can attach to an existing
instance; consult its docs for the current connect API and set the User-Agent
over CDP after attaching.
