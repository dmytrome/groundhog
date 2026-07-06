# Groundhog

[![Conformance](https://github.com/dmytrome/groundhog/actions/workflows/conformance.yml/badge.svg)](https://github.com/dmytrome/groundhog/actions/workflows/conformance.yml)

**Safe, self-hosted web grounding for AI agents and crawlers.** Groundhog is an
[MCP](https://modelcontextprotocol.io) server that fetches live web pages through a
**real, stealth-patched Chrome** (over CDP) and returns clean Markdown with provenance —
without the SSRF holes of plain fetchers and without getting blocked like plain HTTP
clients.

```text
agent / crawler  ──MCP──▶  Groundhog (read_url)  ──CDP──▶  stealth Chrome  ──▶  the web
```

## Quick start

Add Groundhog to your MCP client — that's it. On the first `read_url`, Groundhog pulls and
starts the stealth-browser container for you (Docker or Podman required); no repo checkout,
no manual steps.

Claude Desktop / Cursor / Windsurf (`claude_desktop_config.json` or equivalent):

```json
{
  "mcpServers": {
    "groundhog": {
      "command": "uvx",
      "args": ["groundhog-mcp"]
    }
  }
}
```

`uvx` fetches `groundhog-mcp` from PyPI on first run. The first fetch pulls the browser
image (once, a few minutes); later fetches are instant. No container runtime? The `status`
tool and any error say how to install one — or point `CDP_URL` at a hosted browser for
zero-install use.

**Prefer to manage the browser yourself?** Start it and Groundhog will just use it:

```bash
docker run -d --rm -p 127.0.0.1:9222:9222 ghcr.io/dmytrome/groundhog:latest
# or, from a repo checkout: docker compose up --build -d
curl -s http://localhost:9222/json/version    # CDP is live
```

Set `GROUNDHOG_AUTO_START_BROWSER=false` to disable auto-start. To run the MCP server from
source: `cd mcp && uv sync && uv run groundhog-mcp`.

## Tools

### `read_url(url, format="markdown", max_tokens=None, query=None, include_hidden=False)`

Fetches a page and returns clean content plus provenance.

| Key          | Meaning                                                                                          |
| ------------ | ------------------------------------------------------------------------------------------------ |
| `markdown`   | Extracted content (article-first, falls back to full text); `format` may be `markdown` or `text` |
| `title`      | Page title                                                                                       |
| `url`        | The URL you asked for                                                                            |
| `final_url`  | The URL after redirects (re-checked against the SSRF guard)                                      |
| `fetched_at` | UTC ISO-8601 timestamp                                                                           |
| `truncated`  | Whether the content was cut to fit the token budget                                              |
| `threats`    | Hidden-text signals detected (signal type + excerpt per node); empty list when none found        |
| `matches`    | When `query` is set: ranked passages with `heading`, `offset`, and `score` for citation          |
| `provenance` | Content hash, canonical URL, language, word count, and author/date metadata when present         |

Because Groundhog renders a real DOM, it can evaluate computed styles. Text invisible to
humans is **stripped by default** and each occurrence reported in `threats` with its signal
type and a short excerpt: `display:none`/`visibility:hidden`, `opacity ≤ 0.05`,
`font-size < 4 px`, zero-size elements, the sub-pixel box used by `.sr-only`/
`.visually-hidden` accessibility utility classes (a pattern attackers now mimic), the legacy
`clip: rect(...)` hiding technique, text-color transparency or matching the background color
(near-1:1 contrast), and elements positioned entirely outside the rendered page (e.g.
`left: -9999px`). Non-trivial HTML comments are reported too — they never reach the
extracted content either way, but a page embedding instructions this way is worth knowing
about. Pass `include_hidden=True` to keep the stripped text in the output; `threats` is
still populated so you know it was there. Pass `query` to
replace blunt head-truncation with relevance-ranked passage selection: content is chunked
on markdown structure, ranked by lexical (BM25) relevance, and the top passages within the
token budget are returned; `matches` gives each passage's heading, character offset, and
score for downstream citation. Ranking runs on sanitized content, so hidden-text injection
payloads cannot influence which passages surface.

### `status()`

Reports whether Groundhog can reach the stealth browser. Returns `browser_reachable`,
`cdp_url`, and a `hint` with remediation steps when it isn't reachable.

## Configuration

**MCP server** (`mcp/`):

| Env var                          | Default                 | Purpose                                                                                  |
| -------------------------------- | ----------------------- | ---------------------------------------------------------------------------------------- |
| `CDP_URL`                        | `http://127.0.0.1:9222` | CDP endpoint of the stealth browser                                                      |
| `GROUNDHOG_BLOCK_PRIVATE_IPS`    | `true`                  | Enforce the SSRF guard (resolve + block private ranges)                                  |
| `GROUNDHOG_MIN_DELAY_MS`         | `5000`                  | Minimum delay between requests to the same domain                                        |
| `GROUNDHOG_MAX_TOKENS`           | `20000`                 | Token budget before truncation                                                           |
| `GROUNDHOG_MAX_CONCURRENT_PAGES` | `4`                     | Cap on concurrent open tabs                                                              |
| `GROUNDHOG_AUTO_START_BROWSER`   | `true`                  | Auto-pull-and-run the browser container when it isn't reachable (needs Docker/Podman); `false` to manage it yourself |
| `GROUNDHOG_BROWSER_IMAGE`        | `ghcr.io/dmytrome/groundhog:latest` | Image used for auto-start                                                    |
| `GROUNDHOG_COMPOSE_FILE`         | _(none)_                | Use `docker compose -f <file> up -d` for auto-start instead of `docker run` (local repo) |

**Dependencies:** `py3langid` (which pulls in numpy) is used for language detection in the
`provenance` result. It is installed in the MCP server package only — not in the browser
container.

**Browser container:**

| Env var       | Default                       | Purpose                                                           |
| ------------- | ----------------------------- | ----------------------------------------------------------------- |
| `USER_AGENT`  | derived from installed Chrome | UA set at launch, so it is clean in every scope including workers        |
| `PROXY`       | _(none)_                      | Upstream proxy (`http://user:pass@host:port`); auth is relayed and timezone/locale auto-align to the exit IP |
| `TZ`          | `UTC`                         | Fallback timezone; auto-derived from the exit IP when `PROXY` is set     |
| `WINDOW_SIZE` | `1920,1080`                   | Initial Chrome window size                                               |
| `XVFB_WHD`    | `1920x1080x24`                | Virtual display geometry                                                 |

## Why Groundhog

- **Safe by default.** The SSRF guard resolves the host and blocks loopback, RFC-1918
  private, link-local (incl. `169.254.169.254`), reserved, multicast, unspecified,
  CGNAT `100.64.0.0/10`, and IPv4-mapped IPv6 — and re-checks the URL after redirects.
  Only `http`/`https`, no credentials in URLs. Read-only, per-domain rate limiting.
- **No automation tell.** Puppeteer/Playwright/Selenium enable the CDP `Runtime` domain,
  which anti-bots detect (`isAutomatedWithCDP`). Groundhog drives the browser over raw CDP
  and never enables `Runtime`/`Console`, so that signal is absent — a clean session that
  full automation libraries can't produce over `connect_over_cdp`.
- **A real fingerprint.** It's real Chrome, run headful under Xvfb (no `HeadlessChrome`
  token) — authentic TLS/HTTP2 fingerprint, real WebGL/canvas — not a Python HTTP client,
  so fingerprint-driven blocks go away and cheap proxies work where they otherwise wouldn't.
- **Self-hosted.** You run the container; the pages you fetch and the content extracted
  from them never leave your own infrastructure.

## Under the hood: the stealth Chrome container

A minimal Docker container running **headful Chrome under Xvfb** with a remote CDP
endpoint. Any CDP-speaking client (Puppeteer, Playwright, Selenium, chromedp, raw
DevTools) can drive it — Groundhog is one such client.

- **Headful under Xvfb**, not `--headless=new` — the browser reports `Chrome`, not
  `HeadlessChrome`, avoids headless-specific tells, and engages the real GPU path.
- **`--disable-blink-features=AutomationControlled`** — `navigator.webdriver` reads
  `false`.
- **UA set at launch** from the installed Chrome version (`USER_AGENT`), so it is clean
  in every scope — main frame, network, and Web/Service Worker globals.
- **Proxy geo-coherence.** When `PROXY` is set, the entrypoint geolocates the exit IP and
  aligns the browser timezone and locale to it — a timezone or locale that disagrees with
  the IP is itself a block signal. The country→locale table is CLDR likely-subtags. Chrome
  can't authenticate to a proxy over `--proxy-server`, so credentials are relayed through a
  local tinyproxy; WebRTC is pinned to the proxy path so the real IP can't leak.
- **GPU-aware WebGL.** The entrypoint auto-detects a GPU (NVIDIA via the Container
  Toolkit, or Intel/AMD via `/dev/dri`) and uses hardware acceleration; without one it
  runs Mesa `llvmpipe`, a coherent software renderer that VMs and servers legitimately
  emit. See the `gpus`/`devices` hints in [`docker-compose.yml`](docker-compose.yml).

### Verified results

Measured against a freshly built container (Chrome 149, headful under Xvfb, no proxy),
driven over raw CDP:

| Detector                                                               | Result                                  |
| ---------------------------------------------------------------------- | --------------------------------------- |
| [deviceandbrowserinfo](https://deviceandbrowserinfo.com/are_you_a_bot) | not a bot (`isBot: false`, zero flags)  |
| [browserscan](https://www.browserscan.net/bot-detection)               | Normal                                  |
| [bot.sannysoft.com](https://bot.sannysoft.com/)                        | 31 / 31 checks pass                     |

[iphey](https://iphey.com/) is tracked informationally, not pass/fail: its one recurring
flag is Location ("looks like you're trying to hide your location"), which fires on any
datacenter/hosting exit IP regardless of browser fingerprint or `TZ` correctness — it
passes on a residential IP and fails in CI (a cloud runner) and behind most proxies alike.

See [`RESULTS.md`](RESULTS.md) for the full live table (regenerated by
[`tests/antibot.py`](tests/antibot.py) and the Conformance workflow).

These reflect the raw-CDP client. Full automation libraries (Puppeteer/Playwright/Selenium)
enable the CDP `Runtime` domain and are flagged as automated even against this container —
see [`examples/`](examples/) for which need patched (rebrowser) variants.

### Examples

| Client              | Path                                                       |
| ------------------- | ---------------------------------------------------------- |
| Puppeteer (Node)    | [`examples/puppeteer`](examples/puppeteer)                 |
| Playwright (Node)   | [`examples/playwright-node`](examples/playwright-node)     |
| Playwright (Python) | [`examples/playwright-python`](examples/playwright-python) |
| Selenium (Python)   | [`examples/selenium-python`](examples/selenium-python)     |
| chromedp (Go)       | [`examples/go-chromedp`](examples/go-chromedp)             |
| Raw CDP (Python)    | [`examples/python-raw-cdp`](examples/python-raw-cdp)       |

See [`examples/OTHER_TOOLS.md`](examples/OTHER_TOOLS.md) for crawl4ai, Scrapy +
Playwright, go-rod, Crawlee, and nodriver pointers.

## Security

The CDP endpoint is **unauthenticated** — anyone who can reach the port has full control
of the browser. Bind it to localhost or a trusted private network; never expose it to the
public internet. `--no-sandbox` is used because Chrome's sandbox does not work in an
unprivileged container; keep the container isolated. To report a vulnerability, see
[`SECURITY.md`](SECURITY.md).

## A note on "stealth"

Best-effort, not a guarantee. It defeats common open-source detectors and lets cheap
proxies work on many mid-tier targets, but it does not beat sophisticated commercial
anti-bot systems that gate on IP reputation, TLS/HTTP2 fingerprints, and behavioral
analysis. Use it for legitimate, authorized automation and testing.

## License

[MIT](LICENSE)
