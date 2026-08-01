# Groundhog

[![Conformance](https://github.com/dmytrome/groundhog/actions/workflows/conformance.yml/badge.svg)](https://github.com/dmytrome/groundhog/actions/workflows/conformance.yml)

**Web search, read and research for AI agents — through a real, stealth-patched Chrome.**
Groundhog is an [MCP](https://modelcontextprotocol.io) server that finds pages, reads them,
and researches across them, returning clean Markdown a model can trust: text no human could
see is **stripped by default before the model reads it**, every source comes back with a
**provenance receipt**, and a real browser reads pages that block plain fetchers — without
the SSRF holes of naive fetch tools.

```text
agent / crawler  ──MCP──▶  Groundhog (search, read_url, research)  ──CDP──▶  stealth Chrome  ──▶  the web
```

## Quick start

Add Groundhog to your MCP client — that's it. On the first fetch, Groundhog pulls and
starts the stealth-browser container for you (Docker or Podman required); no repo checkout,
no manual steps. When the default (non-compose) auto-start path has to run, any stale
container named `groundhog-browser` is removed first; a reachable browser is never touched.

Claude Code:

```bash
claude mcp add groundhog -- uvx groundhog-mcp
```

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
docker run -d --rm --name groundhog-browser --shm-size 512m \
  -p 127.0.0.1:9222:9222 -- ghcr.io/dmytrome/groundhog:latest
# or, from a repo checkout: docker compose up --build -d
curl -s http://localhost:9222/json/version    # CDP is live
```

Set `GROUNDHOG_AUTO_START_BROWSER=false` to disable auto-start. To run the MCP server from
source: `cd mcp && uv sync && uv run groundhog-mcp`.

## What makes it different

- **Hidden text is stripped before the model reads it.** Groundhog renders a real DOM, so it
  can judge what a *human* would actually see and strip what they could not, reporting each
  occurrence in `threats`. A strong heuristic, not a proof — see
  [the limits of hidden-text detection](#limits-of-hidden-text-detection). The ten signals,
  the `threats` caveat and the `include_hidden` exception are documented under `read_url`.
- **Every source carries a receipt.** SHA-256 hash of the extracted content, canonical URL,
  language, word count, and author/date when the page declares them — so a downstream claim
  traces back to exactly what was read. `read_url` returns the fetch time alongside it as
  `fetched_at`.
- **Safe by default.** The SSRF guard resolves each host before navigating and refuses to
  return content from a URL that redirects into a private address. Read-only, with per-domain
  rate limiting. This matters most in `research`, where a *third party* chooses the URLs.
  See [Security](#security) for the full blocklist and the guard's limits.
- **No automation tell.** Puppeteer/Playwright/Selenium enable the CDP `Runtime` domain,
  which anti-bots detect (`isAutomatedWithCDP`). Groundhog drives the browser over raw CDP
  and never enables `Runtime`/`Console`, so that signal is absent — a clean session that
  full automation libraries can't produce over `connect_over_cdp`.
- **A real fingerprint.** It's real Chrome, run headful under Xvfb (no `HeadlessChrome`
  token) — authentic TLS/HTTP2 fingerprint, real WebGL/canvas — not a Python HTTP client,
  so fingerprint-driven blocks go away and cheap proxies work where they otherwise wouldn't.
- **No model, no API key.** `research` returns extracts, not summaries; your agent does the
  synthesis. Self-hosted and MIT — the pages you fetch never leave your infrastructure.

## Tools

### `read_url(url, format="markdown", max_tokens=None, query=None, include_hidden=False)`

Fetches a page and returns clean content plus provenance.

| Key          | Meaning                                                                                          |
| ------------ | ------------------------------------------------------------------------------------------------ |
| `markdown`   | Extracted content (article-first, falls back to full text); `format` may be `markdown` or `text` |
| `title`      | Page title                                                                                       |
| `url`        | The URL you asked for                                                                            |
| `final_url`  | The URL after redirects (re-checked against the SSRF guard). Never rewritten: if the page's own final URL is unusable, the requested URL is reported and a `final_url_suppressed` threat says so |
| `fetched_at` | UTC ISO-8601 timestamp                                                                           |
| `truncated`  | Whether the content was cut to fit the token budget                                              |
| `threats`    | Signals detected: hidden-CSS nodes and invisible-character classes; empty when none found |
| `matches`    | When `query` is set: ranked passages with `heading`, `offset`, and `score` for citation          |
| `provenance` | Content hash, canonical URL, language, word count, and author/date metadata when present         |

Because Groundhog renders a real DOM, it can evaluate computed styles. Text invisible to
humans is **stripped by default** and each occurrence reported in `threats` with its signal
type and a short excerpt: `display:none`/`visibility:hidden`, `content-visibility: hidden`
(the subtree is skipped from layout while the element keeps an ordinary box, so no other
signal sees it), `opacity ≤ 0.05`, `font-size < 4 px`, zero-size elements, the sub-pixel box
used by `.sr-only`/`.visually-hidden` accessibility utility classes (a pattern attackers now
mimic), the legacy `clip: rect(...)` hiding technique, fully transparent text color, text
color matching the background color (near-1:1 contrast), and elements positioned entirely
outside the rendered page (e.g. `left: -9999px`). Non-trivial HTML comments are reported too — they never reach the
extracted content either way, but a page embedding instructions this way is worth knowing
about. A second, character-level class is stripped and reported alongside these: zero-width
characters, bidi marks and RTL overrides, and the Unicode Tag block — an invisible ASCII
mirror that is the canonical prompt-injection smuggling channel. Pass `include_hidden=True`
to keep the stripped text in the output; `threats` is still populated so you know it was
there.

**Treat `threats` as untrusted.** Entries come in six shapes (the character classes share one):

| `type` | Carries |
| ------ | ------- |
| `hidden_css` | The hiding `reason`, an 80-char `excerpt` of the removed text, and the DOM `location`. All three are page-authored, so all three are stripped of invisible characters and length-capped — but they remain attacker-*chosen* text |
| `zero_width` / `bidi` / `tag` | A codepoint and count in `reason`, no excerpt. Detected on the text the page actually served — the extractor removes these characters on its way to Markdown, so scanning the extracted output would report none of them |
| `report_truncated` | How many entries were dropped when the cap was hit. Its own type, so it cannot be miscounted as a finding |
| `final_url_suppressed` | The page's own final URL was unusable (over-long, or carrying invisible characters) and was not returned; `final_url` reports the URL you requested instead |
| `detection_degraded` | The collector had to run in the page's own JavaScript world, where the page can replace the DOM APIs it uses. A short list proves nothing on that page |
| `strip_incomplete` | A flagged node could not be removed outright — it won the cascade against the hiding stylesheet (an inline `!important` does), the page hid its own `<body>`, or its recorded position did not resolve. The text is then taken from the stripped markup rather than from layout. A weaker guarantee than a structural strip |

The value of stripping is that the payload is out of the content being reasoned over, not
that it is invisible to the model. At most **50 findings per page** are returned (**10 per
source** in `research`, since the fan-out multiplies the report); beyond that a
`report_truncated` entry is appended stating how many were dropped, rather than truncating
silently. The two classes are capped independently, so a page cannot bury the findings that
carry its injection excerpt by flooding the report with decoys of the other kind. Notices are
appended after the cap — so they can never themselves be dropped, and a capped list is up to
50 findings plus at most two notices.

Pass `query` to replace blunt head-truncation with relevance-ranked passage selection:
content is chunked on markdown structure, ranked by lexical (BM25) relevance, and the top
passages within the token budget are returned; `matches` gives each passage's heading,
character offset, and score for downstream citation. Ranking runs on the sanitized content,
so hidden-text injection payloads cannot influence which passages surface — with the one
exception of `include_hidden=True`, which leaves the hidden text in the document and ranks it
along with everything else.

### `search(query, limit=10)`

Finds pages for a query and returns ranked hits — `title`, `url`, `snippet`, `engine`,
`score`, `published` — plus the `backend` that answered. Hits are links only: nothing is
fetched until you pass a URL to `read_url`.

Two backends, chosen automatically. Set `SEARXNG_URL` to use your own
[SearXNG](https://docs.searxng.org) instance (best results; needs `formats: [html, json]`
in its `settings.yml`, since JSON is off by default upstream). With no instance configured,
Groundhog renders a search page through the stealth browser instead — no extra
infrastructure, at the cost of depending on that page's layout. Force one with
`GROUNDHOG_SEARCH_BACKEND=searxng|serp`.

Every text field of a hit is attacker-influenceable — a poisoned page controls how it describes
itself — so each passes through the same invisible-character stripping as page content, and
each is length-capped. The URL is treated differently: it is what a model cites, so it is
never rewritten. A hit is dropped outright if cleaning would change its URL at all, if that
URL is not `http`/`https`, if it carries credentials, or if it exceeds 2048 characters. Both matter on the DuckDuckGo path, which percent-decodes
the redirect wrapper and can therefore turn `%E2%80%8B` back into a real zero-width
character inside the link. A backend that is unreachable, has JSON disabled, or whose every upstream engine
is rate-limited raises an actionable error rather than reporting an empty web.

### `research(query, max_sources=5, max_tokens=None)`

One call for "find out about X": searches, reads the top sources through the stealth
browser, and returns the passages most relevant to `query` — ranked across **all** sources
in a single pass, so a passage from source 4 competes fairly with one from source 1.

Returns `passages` (each with `text`, `source_url`, `heading`, `score`) and `sources` (each
with `url`, `title`, `status`, `threats`, `provenance`). At most one page per registrable
domain, for source diversity. Passages are extracts, not summaries — nothing is generated,
and no model or API key is involved. When a passage isn't enough, `read_url` its
`source_url` for the whole page.

A source that fails doesn't fail the call: it appears in `sources` with a status of
`blocked` (SSRF guard), `timeout`, or `error`, so a partial answer is still usable and you
can see what was missed. Because search results are chosen by a third party — and
SEO-poisoned results are a documented in-the-wild attack — every fetched URL goes through
the same SSRF guard and hidden-text stripping as `read_url`, and each source reports what
was stripped from it. A source that failed carries `provenance: null` — only sources that
were actually read are hashed. `threats` is per-source here and capped at 10 entries per
source, lower than `read_url`'s 50, because the fan-out multiplies it. `max_sources` is
capped at 10.

It's slower than an API-backed research tool: a real browser renders every source. That's
the trade for reading pages that block plain fetchers, and for being able to tell you what
was hidden in them.

### `status()`

Reports whether Groundhog can reach the stealth browser. Returns `browser_reachable`,
`cdp_url` and a `hint` with remediation steps when it isn't reachable. The endpoint is
reported as scheme, host and port only — a hosted browser often carries a credential in
its URL, and this value reaches the model.

## Configuration

**MCP server** (`mcp/`):

| Env var                          | Default                 | Purpose                                                                                  |
| -------------------------------- | ----------------------- | ---------------------------------------------------------------------------------------- |
| `CDP_URL`                        | `http://127.0.0.1:9222` | CDP endpoint of the stealth browser. May be remote (a DNS name or IP); auto-start is skipped for non-local values. The endpoint is unauthenticated — keep it on a private network or a tunnel. |
| `GROUNDHOG_BLOCK_PRIVATE_IPS`    | `true`                  | Enforce the SSRF guard (resolve + block private ranges)                                  |
| `GROUNDHOG_MIN_DELAY_MS`         | `5000`                  | Minimum delay between requests to the same domain                                        |
| `GROUNDHOG_MAX_TOKENS`           | `20000`                 | Token budget before truncation                                                           |
| `GROUNDHOG_MAX_CONCURRENT_PAGES` | `4`                     | Cap on concurrent open tabs                                                              |
| `SEARXNG_URL`                    | _(unset)_               | Your SearXNG instance for `search`, e.g. `http://searxng:8080`. Needs `formats: [html, json]`. Unset → SERP via the stealth browser. |
| `GROUNDHOG_SEARCH_BACKEND`       | `auto`                  | `auto` (SearXNG when `SEARXNG_URL` is set, else SERP), or force `searxng` / `serp`        |
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

### Limits of hidden-text detection

Worth knowing before treating an empty `threats` list as a clean bill of health. Nothing is
removed from the live page — the markup is stripped inside a separate inert document, which
is imported rather than cloned (`cloneNode` is itself `[CEReactions]`), and the rendered text
comes from the live page with the flagged nodes hidden by an adopted stylesheet. So a page
gets no synchronous hook to react to the strip. What that does *not* cover:

- **The style signals are thresholds, and the character set is a denylist.** Those are the
  real limits — see below. The detector itself runs in an isolated world
  (`Page.createIsolatedWorld`), so a page cannot suppress it by replacing the DOM APIs it
  uses; if the browser ever declines to provide one, the result carries a
  `detection_degraded` threat rather than quietly weaker detection.
- **Thresholds can be sat just inside.** `opacity: 0.06`, `font-size: 4px`, a contrast ratio
  just above 1.15 — all pass, as do hiding techniques the ten signals don't model
  (`clip-path`, `text-indent`, `transform: scale(0)`).
- **Invisible-character coverage is a set, not a rule.** Zero-width, bidi and the Unicode Tag
  block are stripped and reported; codepoints outside that set are not.
- **When the text is rebuilt, line breaks are guessed from tag names.** In the two cases
  above the rendered text is taken from the stripped markup, which has no layout — so an
  element the page styled `display:inline` still gets a break, and a block-level tag outside
  the list gets none. Word boundaries are preserved; exact line structure is not.
- **Closed shadow roots are not read.** Open ones are: their content is scanned for hidden
  text and composed into the output as the flat tree a reader sees, slots included. A
  closed root is unreachable from the isolated world, so it cannot be scanned — and what
  cannot be scanned is not composed in. Its content stays out of the result entirely rather
  than arriving unexamined.
- **A page can win the cascade against the hiding sheet, or hide its own `<body>`.** An
  inline `!important` beats an author stylesheet, and `innerText` returns raw text when
  nothing renders at all. In either case the rendered text is abandoned for the stripped
  markup, which is a weaker guarantee than reading real layout — reported as
  `strip_incomplete` rather than left to look like a clean strip.

**What the SSRF guard blocks.** Each host is resolved and rejected if it lands in loopback,
RFC-1918 private, link-local (incl. `169.254.169.254`), reserved, multicast, unspecified,
CGNAT `100.64.0.0/10`, or IPv4-mapped IPv6 ranges. Only `http` and `https` are allowed, and
credentials in URLs are rejected. The check runs again immediately before navigation, and
once more against `final_url` after redirects.

**Limits of the SSRF guard.** It is a strong default, not a sandbox. Know these before
pointing it at untrusted URLs:

- The guard resolves and checks the host *before* navigation and re-checks `final_url`
  *after* the page loads. A redirect into a private address is therefore still requested by
  Chrome — its content is never returned, but a blind SSRF or a state-changing internal `GET`
  has already landed. Intermediate hops in a longer redirect chain are not individually
  checked.
- Sub-resource requests the page itself issues (`img`, `script`, `iframe`, `fetch`) are not
  intercepted; only the top-level navigation is checked.
- Groundhog resolves DNS in its own process while Chrome resolves independently at navigate
  time, so a short-TTL rebinding window remains open. Closing these properly needs
  request-level interception (CDP `Fetch`).
- Fetches share the browser's default profile — targets are created without a separate
  browser context — so cookies and storage set by one page persist into later fetches.
  "Read-only" describes Groundhog's own API, not the JavaScript on a fetched page, which can
  issue requests of its own from that shared profile.

Set `GROUNDHOG_BLOCK_PRIVATE_IPS=false` only on a network where reaching internal addresses
is intended.

## A note on "stealth"

Best-effort, not a guarantee. It defeats common open-source detectors and lets cheap
proxies work on many mid-tier targets, but it does not beat sophisticated commercial
anti-bot systems that gate on IP reputation, TLS/HTTP2 fingerprints, and behavioral
analysis. Use it for legitimate, authorized automation and testing.

## License

[MIT](LICENSE)
