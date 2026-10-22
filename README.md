# Groundhog

[![Conformance](https://github.com/dmytrome/groundhog/actions/workflows/conformance.yml/badge.svg)](https://github.com/dmytrome/groundhog/actions/workflows/conformance.yml)
[![Glama](https://glama.ai/mcp/servers/dmytrome/groundhog/badges/score.svg)](https://glama.ai/mcp/servers/dmytrome/groundhog)

Web search, read and research for AI agents, through a real, stealth-patched Chrome.
Groundhog is an [MCP](https://modelcontextprotocol.io) server that returns clean Markdown a
model can trust: text no human could see is **stripped before the model reads it and
reported**, every source comes with a provenance receipt, and a real browser reads pages
that block plain fetchers.

```text
agent / crawler  ──MCP──▶  Groundhog (search, read_url, research)  ──CDP──▶  stealth Chrome  ──▶  the web
```

## Measured against other fetch layers

Twenty-eight pages carry text a human can't see, and three controls carry none. A case
passes when the payload doesn't reach the model and the article text still does.

| | contained | kept the article | reported it | false positives |
| --- | --- | --- | --- | --- |
| `requests` + trafilatura | 25/28 | 28/28 | 0/28 | 0/3 |
| Scrapling (HTTP) | 16/28 | 28/28 | 0/28 | 0/3 |
| Jina Reader | 15/28 | 28/28 | 0/28 | 0/3 |
| Firecrawl | 7/28 | 28/28 | 0/28 | 0/3 |
| **Groundhog** | **28/28** | **28/28** | **25/28** | **0/3** |

Other extractors often drop hidden text by accident, but none of them tells the caller
anything was removed. The three cases Groundhog doesn't report (a hidden input value, a meta
description, an `iframe srcdoc`) never reach any extractor. The corpus is public at
[dmytrome.github.io/groundhog](https://dmytrome.github.io/groundhog/); the harness and its
limits are in [`benchmark/`](benchmark/).

## Quick start

Claude Code:

```bash
claude mcp add groundhog -- uvx groundhog-mcp
```

Claude Desktop, Cursor, Windsurf:

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

On the first fetch Groundhog starts the Chrome you already have, headless, in its own
profile at `~/.groundhog/chrome`. Your own browsing, tabs and logins are never touched. If
Chrome is missing, `status` and the error say what to install.

`GROUNDHOG_BROWSER=stealth` runs the hardened browser container instead (Docker or Podman;
the first pull takes a few minutes). To run it yourself:

```bash
docker run -d --rm --name groundhog-browser --shm-size 512m \
  -p 127.0.0.1:9222:9222 -- ghcr.io/dmytrome/groundhog:latest
```

`CDP_URL` points Groundhog at any browser and is used as given: `http://` is probed for
`/json/version`, `ws://` or `wss://` (what hosted browsers hand out) is connected to directly.
Auto-start only runs for a local address that isn't answering, and
`GROUNDHOG_AUTO_START_BROWSER=false` turns it off. From source: `cd mcp && uv sync && uv run groundhog-mcp`.

All four tools are annotated `readOnlyHint`: they write nothing and change no remote state.
The one side effect is auto-start, which may launch Chrome or, with `stealth`, run the
container after removing an unreachable one named `groundhog-browser`.

## What makes it different

- **Hidden text is stripped and reported.** A rendered DOM shows what a human would see;
  everything else goes, and each removal is listed in `threats`. It's a strong heuristic, not
  a proof: see [the limits](#limits-of-hidden-text-detection).
- **Every source carries a receipt**: a SHA-256 of the extracted content, canonical URL,
  language, word count, and author and date when the page declares them.
- **Safe by default.** An SSRF guard checks every document request, redirects and frames
  included, with per-domain rate limiting. See [Security](#security).
- **No automation tell.** Groundhog drives Chrome over raw CDP and never enables the
  `Runtime` domain that anti-bots detect in Puppeteer, Playwright and Selenium.
- **A real fingerprint.** Real Chrome means real TLS, HTTP/2, WebGL and canvas. In headless
  mode the `HeadlessChrome` token is replaced and client hints are copied from the browser;
  shared and service workers aren't covered. The stealth image runs headful under Xvfb.
- **No model, no API key.** `research` returns extracts and your agent does the synthesis.

## Tools

### `read_url(url, format="markdown", max_tokens=None, query=None, include_hidden=False)`

| Key          | Meaning |
| ------------ | ------- |
| `markdown`   | Extracted content, article first, falling back to the rendered text with markdown syntax backslash-escaped; `format` may be `markdown` or `text` |
| `title`      | Page title |
| `url`        | The URL you asked for |
| `final_url`  | The URL after redirects, re-checked by the SSRF guard. If unusable, the requested URL is reported with a `final_url_suppressed` threat |
| `fetched_at` | UTC ISO-8601 timestamp |
| `status`     | `ok`, or `challenge` / `blocked` / `rate_limited` / `not_found` / `server_error` / `unsupported_content` when what came back isn't the page you asked for. `blocked` covers 401, 403 and other 4xx error pages. `empty` means neither the rendered page nor its extracted markdown has words to read (text in closed shadow roots, iframes, `alt` attributes and link targets doesn't count). `unknown` means no usable status was observed. Judged on the document the text came from, after client-side redirects |
| `http_status`| The top-level HTTP status, or `null` |
| `truncated`  | Whether the token budget cut the content |
| `threats`    | What was stripped; empty when nothing was found |
| `matches`    | With `query`: ranked passages with `id`, `heading`, `offset`, `end` and `score` |
| `set_aside`  | With `query`: scoring passages the relevance floor dropped |
| `provenance` | Content hash, canonical URL, language, word count, author and date |

Stripped by default: `display:none`, `visibility:hidden`, `content-visibility:hidden`,
`opacity ≤ 0.05`, `font-size < 4px`, zero-size and `display:contents` boxes that render
nothing, `.sr-only`-style sub-pixel boxes, `clip: rect(...)`, transparent or
background-coloured text, off-page positioning, collapsed `<details>`, `<template>` content,
long `alt` / `aria-label` / `aria-description` / `title` text, and zero-width, bidi and
Unicode Tag characters. HTML comments are reported too. `include_hidden=True` keeps the text
but still reports it.

Treat `threats` as untrusted: its excerpts are page-authored text, cleaned and length-capped.

| `type` | Carries |
| ------ | ------- |
| `hidden_css` | The hiding `reason`, an 80-character `excerpt` and the DOM `location` |
| `hidden_attribute` | The same, for text in an `alt`, `aria-label`, `aria-description` or `title` attribute |
| `hidden_template` | The same, for text inside a `<template>` |
| `zero_width` / `bidi` / `tag` | A codepoint and count in `reason`, detected on the served text |
| `report_truncated` | How many entries the cap dropped |
| `final_url_suppressed` | The page's final URL was unusable and not returned |
| `detection_degraded` | Detection ran in the page's own JavaScript world, which the page can tamper with |
| `strip_incomplete` | The text was rebuilt from markup rather than layout, usually because of open shadow roots |

At most 50 findings per page are returned (10 per source in `research`), with the two classes
capped separately so decoys can't bury a real payload. Notices are appended after the cap.

`query` swaps head-truncation for BM25 passage ranking on the sanitized content, keeping
passages within half the best score up to the budget.

### `search(query, limit=10)`

Returns hits (`title`, `url`, `snippet`, `engine`, `score`, `published`) and the `backend`
that answered. Nothing is fetched. With `SEARXNG_URL` set it uses your
[SearXNG](https://docs.searxng.org) instance, which needs `formats: [html, json]`; otherwise
it renders a search page through the browser. Hit text is cleaned and capped, and a hit whose
URL would change under cleaning, isn't `http(s)`, carries credentials or exceeds 2048
characters is dropped. A backend that can't answer raises an error instead of returning an
empty web.

### `research(query, max_sources=5, max_tokens=None, include_domains=None, exclude_domains=None, published_after=None, aim=None)`

Searches, reads the top sources and ranks passages from all of them in one pass.

- `passages`: `id`, `text`, `source_url`, `heading`, `score`, `start`, `end`, `clamped`,
  `fence`. `text` is exactly `markdown[start:end]` unless `clamped`. A passage cut from a
  long code block carries the block's opening line in `fence`: reopen with it when `text`
  doesn't start with it, and close the block when `text` doesn't end with a closing fence.
  `id` stays stable while the page doesn't change.
- `sources`: `url`, `title`, `status` (`ok` / `blocked` / `timeout` / `error`), `page_status`
  (as in `read_url`), `threats`, `provenance`, `filtered`. A failed source doesn't fail the
  call. A challenge, an error page, an `empty` page or any page whose `markdown` is blank
  counts as `not_content` and contributes no passages.
- One page per registrable domain unless `include_domains` is set, and then no more pages
  per site than `GROUNDHOG_MAX_CONCURRENT_PAGES`; `max_sources` goes up to 10.

`include_domains` reads only those domains and their subdomains, `exclude_domains` never
reads them, and `published_after` (`YYYY-MM-DD`) drops pages dated earlier. Up to 8 domains
can be included and 50 excluded. A page that redirects into an excluded domain or out of the
included ones, is dated too early, or repeats the URL or text of a page already used is listed
with `filtered` and contributes no passages. The query sent to the engine names at most eight
domains; further excluded ones are filtered after the search. `aim` (up to 200 characters)
says what the evidence must show and is echoed back without changing the search or the
ranking.

`receipt` holds the `executed_query`, the distinct hits and `hits_by_domain`, the hits not
fetched (`hits_excluded`, `hits_outside_domains`, `hits_before_date`, `hits_same_domain`,
`hits_unread` past the source limit), each source counted
once as `failed`, `not_content`, `filtered` or `usable`, `outside_domains`, `unknown_dates`,
and the passages `omitted`, `set_aside` or `clamped`. Read it before concluding the web has
no answer.

### `status()`

Returns `browser_reachable`, `cdp_url` (scheme, host and port only) and a `hint` when the
browser can't be reached.

## Configuration

**MCP server:**

| Env var                          | Default                 | Purpose |
| -------------------------------- | ----------------------- | ------- |
| `CDP_URL`                        | `http://127.0.0.1:9222` | CDP endpoint, local or remote. Unauthenticated, so keep it private |
| `GROUNDHOG_BROWSER`              | `chrome`                | What auto-start launches: `chrome` or `stealth`. Defaults to `stealth` when `GROUNDHOG_COMPOSE_FILE` or `GROUNDHOG_BROWSER_IMAGE` is set |
| `GROUNDHOG_CHROME_PATH`          | _(found)_               | Chrome executable |
| `GROUNDHOG_CHROME_PROFILE`       | `~/.groundhog/chrome`   | Profile directory; a login made here is reused |
| `GROUNDHOG_BLOCK_PRIVATE_IPS`    | `true`                  | The SSRF guard |
| `GROUNDHOG_MIN_DELAY_MS`         | `5000`                  | Delay between requests to one domain |
| `GROUNDHOG_MAX_TOKENS`           | `20000`                 | Token budget |
| `GROUNDHOG_MAX_CONCURRENT_PAGES` | `4`                     | Concurrent tabs |
| `SEARXNG_URL`                    | _(unset)_               | SearXNG instance for `search` |
| `GROUNDHOG_SEARCH_BACKEND`       | `auto`                  | `auto`, `searxng` or `serp` |
| `GROUNDHOG_AUTO_START_BROWSER`   | `true`                  | Start the browser when a local `CDP_URL` isn't answering |
| `GROUNDHOG_BROWSER_IMAGE`        | `ghcr.io/dmytrome/groundhog:latest` | Image for `stealth` |
| `GROUNDHOG_COMPOSE_FILE`         | _(none)_                | Start with `docker compose -f <file> up -d` instead |

**Browser container:**

| Env var       | Default                       | Purpose |
| ------------- | ----------------------------- | ------- |
| `USER_AGENT`  | derived from installed Chrome | UA set at launch, clean in every scope |
| `PROXY`       | _(none)_                      | Upstream proxy `http://user:pass@host:port`; timezone and locale follow the exit IP |
| `TZ`          | `UTC`                         | Fallback timezone |
| `WINDOW_SIZE` | `1920,1080`                   | Chrome window size |
| `XVFB_WHD`    | `1920x1080x24`                | Virtual display |

## The stealth Chrome container

Headful Chrome under Xvfb with a CDP endpoint, usable by any CDP client. It reports `Chrome`
rather than `HeadlessChrome`, sets `navigator.webdriver` to `false`, and sets the UA at launch.
With `PROXY`, timezone and locale match the exit IP, credentials go through a local tinyproxy,
and WebRTC is pinned to the proxy. WebGL uses a detected GPU, else Mesa `llvmpipe`.

Against a fresh container (Chrome 151, no proxy), over raw CDP:

| Detector                                                               | Result                                  |
| ---------------------------------------------------------------------- | --------------------------------------- |
| [deviceandbrowserinfo](https://deviceandbrowserinfo.com/are_you_a_bot) | not a bot (`isBot: false`, zero flags)  |
| [browserscan](https://www.browserscan.net/bot-detection)               | Normal                                  |
| [bot.sannysoft.com](https://bot.sannysoft.com/)                        | 31 / 31 checks pass                     |

[iphey](https://iphey.com/) flags any datacenter IP, so it's tracked but not scored. The live
table is in [`RESULTS.md`](RESULTS.md). Puppeteer, Playwright and Selenium are still flagged
because they enable `Runtime`; [`examples/`](examples/) has clients for each, and
[`OTHER_TOOLS.md`](examples/OTHER_TOOLS.md) covers more.

## Security

The CDP endpoint is unauthenticated: anyone who reaches the port controls the browser. Keep it
on localhost or a private network. The stealth image runs `--no-sandbox`, so keep the
container isolated. Report vulnerabilities via [`SECURITY.md`](SECURITY.md).

The SSRF guard resolves each host and refuses loopback, private, link-local (including
`169.254.169.254`), reserved, multicast, unspecified, CGNAT and IPv4-mapped IPv6 addresses,
non-`http(s)` schemes, and URLs with credentials. It checks every document request Chrome is
about to send, redirects and cross-site frames included, and `final_url` once more.
Sub-resources are left to Chrome's Local Network Access rule, and the Chrome that Groundhog
launches has preloading turned off.

Limits of the guard:

- Only documents are checked. An older Chrome, or a site granted local-network access in the
  profile, isn't covered for sub-resources.
- Preloading stays on in the stealth container and in a browser you supply.
- Groundhog and Chrome resolve DNS separately, so a short-TTL rebinding window remains.
- Fetches share one profile, so cookies set by one page persist into later fetches, and page
  JavaScript can make its own requests from it.

`GROUNDHOG_BLOCK_PRIVATE_IPS=false` turns the guard off; use it only where reaching internal
addresses is intended.

### Limits of hidden-text detection

An empty `threats` list isn't a clean bill of health. Detection runs in an isolated world the
page can't tamper with, and the live page is never modified. But:

- Thresholds can be sat just inside (`opacity: 0.06`), and unmodelled techniques such as
  `clip-path`, `text-indent` or `transform: scale(0)` pass.
- The invisible-character set is a denylist.
- Closed shadow roots can't be read, so their content is left out entirely.
- When the page wins the cascade against the hiding stylesheet or hides its own `<body>`, text
  is rebuilt from markup, line breaks are guessed, and `strip_incomplete` says so.

### Limits of block detection

- A challenge is called with certainty from vendor headers (`cf-mitigated`,
  `x-vercel-mitigated`, `x-amzn-waf-action`, `x-dd-b`, `x-datadome-cid`) or challenge assets
  (Cloudflare, DataDome, PerimeterX, Imperva). Other vendors fall back to English wording on a
  near-empty page.
- A soft block, a 200 with the data quietly withheld, looks like content.
- `unknown` isn't `ok`, but it isn't treated as a block either.
- Vendor presence (`server: cloudflare`, `cf-ray`) is deliberately ignored.

## A note on "stealth"

Best effort. It beats common open-source detectors, not commercial anti-bot systems that gate
on IP reputation and behaviour. Use it for legitimate, authorized automation.

## Privacy Policy

Groundhog is self-hosted software and talks to no servers the project runs. It collects
nothing: no telemetry, analytics, crash reports or accounts.

Pages are fetched by a browser on your machine and returned to your MCP client. The server
keeps no database and writes no logs. The browser profile does persist cookies and storage:
`~/.groundhog/chrome` for local Chrome (delete the folder to discard it), or the container for
`stealth` (`docker rm -f groundhog-browser`).

Your traffic reaches the sites you fetch, your search backend (your SearXNG, or a public search
engine without one), an IP-geolocation service for the proxy's exit IP only when `PROXY` is
set, and PyPI and GHCR at install time.

Questions: [`SECURITY.md`](SECURITY.md) or <https://github.com/dmytrome/groundhog/issues>.

## License

[MIT](LICENSE)
