# groundhog-mcp

**Web search, read and research for AI agents — through a real, stealth-patched Chrome.**

Groundhog is an [MCP](https://modelcontextprotocol.io) server that finds pages, reads them,
and researches across them, returning clean Markdown a model can trust: text a human could
not see is **stripped by default before the model reads it**, and every source comes back
with a provenance receipt.

## Quick start

Add this to your MCP client (Claude Desktop / Claude Code / Cursor / Windsurf):

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

That's the whole setup — no repo checkout, no manual steps. On the first fetch Groundhog
starts the stealth browser for you by running `ghcr.io/dmytrome/groundhog:latest` under
Docker or Podman, pulling it if it isn't already local. The first pull takes a few minutes;
everything after is instant. That means a mutable `:latest` tag is pulled and run on your
machine, Chrome runs `--no-sandbox` inside it, and any stale container named
`groundhog-browser` is removed first on that path (a reachable browser is never touched) —
set `GROUNDHOG_AUTO_START_BROWSER=false` to manage the browser yourself.

**Prefer to manage the browser yourself?** Start it and Groundhog will just use it — the
default `CDP_URL` already points there, so there is nothing else to configure:

```bash
docker run -d --rm --name groundhog-browser --shm-size 512m \
  -p 127.0.0.1:9222:9222 -- ghcr.io/dmytrome/groundhog:latest
```

**Browser on another host?** Point `CDP_URL` at it. Auto-start is skipped for any non-local
value, so a remote browser is never touched:

```json
{
  "mcpServers": {
    "groundhog": {
      "command": "uvx",
      "args": ["groundhog-mcp"],
      "env": { "CDP_URL": "http://browser-host:9222" }
    }
  }
}
```

## Tools

| Tool | What it does |
| ---- | ------------ |
| `search(query, limit=10)` | Ranked hits (`title`, `url`, `snippet`, `engine`, `score`, `published`). Links only — nothing is fetched until you ask. Uses your own [SearXNG](https://docs.searxng.org) via `SEARXNG_URL`, else renders a search page through the stealth browser. |
| `read_url(url, format="markdown", max_tokens=None, query=None, include_hidden=False)` | One page as clean Markdown, plus `threats`, `provenance`, `fetched_at` and `final_url`. Pass `query` to get BM25-ranked passages instead of blunt truncation; `include_hidden=True` keeps hidden text in the output. |
| `research(query, max_sources=5, max_tokens=None)` | One call for "find out about X": searches, reads the top sources, and returns passages ranked across **all** of them in a single pass. Each passage carries its `source_url`; each entry in `sources` carries `provenance` and `threats`, or a `status` and `error` when that source failed. `max_sources` is capped at 10. |
| `status()` | Whether the browser is reachable, with remediation steps when it isn't. |

`research` returns extracts, not summaries. Nothing is generated and **no model or API key is
involved** — your agent does the synthesis.

## Why it's different

- **Hidden text is stripped before the model reads it.** Groundhog renders a real DOM, so it
  can evaluate computed styles and judge what a *human* would actually see. Nine
  rendered-style signals — `display:none`, near-zero opacity, sub-pixel boxes, off-screen
  positioning and background-matched text among them — are stripped by default, so the
  payload is out of the content the model reasons over. Each occurrence is reported in
  `threats` with its type and a short excerpt. That excerpt and its DOM path are
  page-authored, so both are sanitized and length-capped before they are returned — but they
  are still attacker-*chosen* text arriving in the tool result, so treat `threats` as
  untrusted data rather than as instructions. At most 50 findings are returned per page (10
  per source in `research`), each class capped independently, with any drop disclosed by a
  notice appended after the cap. It is a strong heuristic over
  rendered styles, not a proof: a payload tuned to sit just inside a threshold can still
  pass. The detector runs in an isolated JavaScript world, so a page cannot suppress it by
  replacing the DOM APIs it uses; if a browser declines to provide one, the result says so
  with a `detection_degraded` threat. `include_hidden=True` keeps the text in the content
  (ranking then runs over it too).
- **Every source carries a receipt.** `provenance` gives a SHA-256 hash of the extracted
  content, plus canonical URL, detected language, word count and author/date when the page
  declares them — so a downstream claim traces back to exactly what was read. `read_url` also
  returns `fetched_at`.
- **Safe by default.** Before navigating, the SSRF guard resolves the host and blocks
  loopback, private and other internal address ranges (the exact list is in the
  [Security section](https://github.com/dmytrome/groundhog#security)), and it refuses to
  return content from a URL that redirects into one. That matters most in `research`, where a
  *third party* picks the URLs. Known limits: the redirect target is still requested by
  Chrome before its content is withheld, page-issued sub-resource requests are not
  intercepted, and Chrome resolves DNS independently, so a short-TTL rebind is not fully
  closed. See
  [Security](https://github.com/dmytrome/groundhog#security).
- **Reads pages plain fetchers can't.** Real Chrome, headful under Xvfb, driven over raw CDP
  — the `Runtime` domain is never enabled, so the `isAutomatedWithCDP` signal that flags
  Puppeteer/Playwright/Selenium simply isn't there.
- **Self-hosted and MIT.** You run the container; fetched pages never leave your
  infrastructure.

## Configuration

The ones you are most likely to want; defaults and the full list live in the project README,
so they cannot drift from it here.

| Env var | Purpose |
| ------- | ------- |
| `CDP_URL` | Browser endpoint; may be remote, and auto-start is skipped for non-local values. **The endpoint is unauthenticated — anyone who can reach the port controls the browser. Keep it on localhost, a private network, or a tunnel.** |
| `SEARXNG_URL` | Your SearXNG instance. Needs `formats: [html, json]`. |
| `GROUNDHOG_MAX_TOKENS` | Token budget before truncation |
| `GROUNDHOG_MIN_DELAY_MS` | Minimum delay between requests to the same domain |
| `GROUNDHOG_AUTO_START_BROWSER` | Auto-run the browser container when unreachable |

Full env-var list, stealth details, verified anti-bot results and the
[Security](https://github.com/dmytrome/groundhog#security) section:
**https://github.com/dmytrome/groundhog**

## A note on "stealth"

Best-effort, not a guarantee. It defeats common open-source detectors and lets cheap proxies
work on many mid-tier targets, but it does not beat sophisticated commercial anti-bot systems
that gate on IP reputation, TLS/HTTP2 fingerprints, and behavioral analysis. Use it for
legitimate, authorized automation and testing.

MIT licensed.

<!-- mcp-name: io.github.dmytrome/groundhog-mcp -->
