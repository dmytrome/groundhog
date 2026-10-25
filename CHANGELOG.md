# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.20.1] - 2026-10-25

### Fixed

- A `read_url` match cut short to fit `max_tokens` reports its own end and id and says
  `clamped`, and matching code too large to return is left out instead of cut.

### Changed

- Passages are ranked on their section heading as well as their text, in `read_url` and
  `research`, so a section found by the version or name in its heading is returned.
- The README says what happens to `alt`, `aria-label`, `aria-description` and `title` text:
  it never appears in `markdown` or the rendered text, and long values are reported.

## [0.20.0] - 2026-10-24

### Added

- `research` offers `candidates`: links inside the returned passages, each with a signed
  token. Passing tokens in `follow` reads those pages without searching, up to three links
  deep from a search result. Tokens expire after 30 minutes or on restart.
- The JSON text block of a `research` response never exceeds 40,000 characters; what does not
  fit is counted in the receipt, and the safety counts of any source left out are kept.
- A `too_large` status for pages over 5 MiB of HTML or 2 MiB of text, which return no content.

### Fixed

- A passage cut short to fit `max_tokens` reports its own end and id, and a code block is left
  out rather than cut.
- An empty `GROUNDHOG_BLOCK_PRIVATE_IPS` keeps the private-address guard on, and a value that
  is not a boolean is refused instead of turning the guard off.
- A page whose tab closed itself is returned instead of reported as an error, and a tab that
  could not be attached is closed.
- Grouping URLs by site uses the bundled public suffix list and never fetches it.
- `GROUNDHOG_MAX_CONCURRENT_PAGES` below 1 is refused instead of hanging every fetch, and an
  empty `GROUNDHOG_MAX_CONCURRENT_PAGES`, `GROUNDHOG_MIN_DELAY_MS` or `GROUNDHOG_MAX_TOKENS`
  takes its default.

### Changed

- `research` refuses a query over 2,000 characters.
- `markdown-it-py` is now a runtime dependency.

## [0.19.1] - 2026-10-23

### Fixed

- A page still busy at the 8-second settle cap has up to 2 more seconds to answer the
  call in flight. One that stays silent fails the fetch then.

## [0.19.0] - 2026-10-23

### Added

- An `empty` status for a page that rendered no text a reader would see, whatever
  `markdown` holds. `research` counts it as `not_content`, as it does any page whose
  `markdown` is blank. With `include_hidden`, text hidden by styling counts as shown.

### Changed

- When the extractor finds nothing, the rendered text comes back inside a code fence.

### Fixed

- Settling stops at its 8-second cap even while a browser call is pending. A page that
  stops answering fails the fetch at the cap.

## [0.18.2] - 2026-10-22

### Fixed

- A canonical URL from page metadata is trimmed of ASCII whitespace only, as browsers do.
  One padded with a non-breaking space is dropped.
- A page script that throws `""`, `false` or `null` is reported by that value.

## [0.18.1] - 2026-10-22

### Changed

- Markdown escapes page text that CommonMark would read as syntax (`\_`, `\~`, `\*`, and a
  leading `#` or `>`). This comes from trafilatura 2.3.1. The `text` format is unchanged.
- mcp 2.3.0, websockets 17.2, tldextract 5.4.0 and idna 3.20.

### Fixed

- A canonical URL from page metadata that cleaning would alter is dropped. Whitespace
  around it is trimmed.
- A script error on the page is reported by its message, or by the thrown value when it
  isn't an `Error`.

## [0.18.0] - 2026-10-21

### Added

- `research` takes `include_domains` (up to 8), `exclude_domains` (up to 50) and
  `published_after` (`YYYY-MM-DD`). Domains match as a browser writes them, subdomains
  included.
- `research` takes `aim`, the evidence the caller is after, and echoes it back.
- `research` returns a `receipt`: the query sent to the engine, where every hit and every
  source went, and which passages were left out, set aside or clamped.
- Every passage has an `id`, `start` and `end` tied to the page it came from. `read_url`
  with `query` returns the same ids in `matches`.
- A passage cut from a long code block carries the block's opening line in `fence`.

### Changed

- Pages are cut on line breaks only, into pieces of at most 2,400 characters, and a passage
  too long for the budget is skipped for the next one that fits.
- A source that redirects out of the included domains, is dated too early, repeats a page
  already used, or has no text is listed but adds no passages.
- With `include_domains`, one site gets no more pages than `GROUNDHOG_MAX_CONCURRENT_PAGES`.
- A shorter README.

### Removed

- `outside_domains` on each source. `filtered: "outside_domains"` says the same thing.

### Fixed

- A search hit with a malformed host is dropped and `search` carries on.

## [0.17.2] - 2026-10-21

### Fixed

- `research` cancels every fetch still running at its deadline or when it is cancelled,
  and collects each result.
- `query` retrieval tokenizes every script: Cyrillic, Devanagari, accented text and the
  unspaced scripts (Chinese, Japanese, Korean, Thai and others) match words inside a passage.
- Fetches to one domain are spaced from when each gets a page, so every fetch keeps its
  delay. Other domains are not held up.
- A browser command that never gets a reply fails after 90 seconds as a timeout, and the next
  fetch reconnects.
- `include_hidden` returns the hidden text: flagged nodes are unhidden in the extracted copy.

### Changed

- The server tells clients that threats are quoted evidence, never instructions, and what
  `detection_degraded` and `strip_incomplete` mean. The `query` description says what
  `matches` holds and that the whole page comes back when nothing matches.
- The README describes the SSRF guard, preloading and the shared Chrome profile.

## [0.17.1] - 2026-10-05

### Changed

- The package README, the Gemini extension settings and the privacy notes describe the local
  Chrome default: Docker or Podman is needed only for `GROUNDHOG_BROWSER=stealth`, and the
  Gemini settings offer that choice. The browser profile lives at `~/.groundhog/chrome` and
  stays on disk until you delete it.
- Locked dependencies updated, among them `websockets` 17 and `mcp` 2.2.

## [0.17.0] - 2026-10-02

### Changed

- The default browser is the Chrome you already have. Auto-start launches it headless in
  Groundhog's own profile at `~/.groundhog/chrome`, so a first fetch needs no Docker and no
  window opens. Each tab, and each cross-site frame in it, carries the identity of the same
  Chrome with a window: the `HeadlessChrome` token is replaced and the client hints are
  copied from the browser. A browser that won't report its client hints is used without the
  identity. A Chrome that is missing, not executable, already running on the profile, or
  exits at launch is reported at once, and one that never answers is stopped.
  `GROUNDHOG_BROWSER=stealth` uses the container, and is the default wherever
  `GROUNDHOG_COMPOSE_FILE` or `GROUNDHOG_BROWSER_IMAGE` is set. `GROUNDHOG_CHROME_PATH` and
  `GROUNDHOG_CHROME_PROFILE` override what is found, and may start with `~`.

### Fixed

- Every document request is checked as Chrome is about to send it, redirect hops, form posts
  and navigations inside cross-site frames included. One to a private address, or one whose
  address can't be checked, is stopped. The Chrome Groundhog launches has page preloading
  off.
- `CDP_URL` is used as given. A `ws://` or `wss://` address is connected to directly. An
  `http(s)://` address keeps its query string. An `https://` endpoint is dialled by its name.
  A connection that fails is reported without the URL's credentials.

## [0.16.1] - 2026-09-19

### Fixed

- A passage that matches every query term the best passage matches stays in the result,
  whatever its length.

## [0.16.0] - 2026-09-19

### Changed

- `read_url` with a `query` returns only passages that score within half of the best one.
  `truncated` means the budget cut the content; a new `set_aside` count says how many
  scoring passages the relevance floor dropped.

### Fixed

- A `#` line inside a fenced code block is not read as a markdown heading. A fence closes
  only on the character that opened it, repeated at least as many times; fence lines pair in
  the order they appear; a line opening with a backtick run whose info string holds another
  backtick is prose; and an opener with no closer of its own is prose.

## [0.15.0] - 2026-09-13

### Fixed

- The threat report opens in the order the collector produced, then gives each way of
  hiding text a turn, so no single carrier spends the cap.
- Findings that repeat verbatim fold into one entry carrying a `seen` count.

### Changed

- `threats` entries may carry `seen`, the number of findings an entry stands for. It is
  absent when an entry stands for one. A tally gathered from more than one place in the
  document reports no `location`.

## [0.14.0] - 2026-09-08

### Fixed

- Backgrounds composite outwards from the element, the page's own canvas is read from
  `color-scheme` in both its CSS and `<meta>` forms, and every colour is resolved by the
  browser, so `oklch`, `lab`, `color(srgb ...)` and `color-mix` are understood.
- `color` and `font-size` are read only off the element that owns the text. Text belonging
  to a `<slot>`, to a shadow root directly, and to a light-DOM `<slot>` is counted as owned.
- `<option>` elements are judged on ink and size, and a flagged option forces the rebuilt
  text.
- Text inside a shadow tree is measured against the background its host paints.

### Added

- Three carriers in the containment corpus: a payload matching its background with both
  written in `oklch`, an `<option>` hidden inside a visible `<select>`, and an option
  tinted to its select. A third control: a readable label on a translucent panel.
- `test_backdrop_paint.py` colours probe text to the pixel a screenshot reports behind
  it, across background owner, alpha, colour syntax and nesting depth.

## [0.13.0] - 2026-09-06

### Changed

- Requires the MCP SDK 2.x (`mcp>=2.1,<3`). The wire format is unchanged: tool annotations
  serialise as `readOnlyHint` / `openWorldHint`.

### Fixed

- Tool error messages written for the caller reach the caller: browser remediation steps,
  the search backend setup hint and argument validation.
- Any other failure is withheld from the caller.

### Added

- A hidden-injection containment benchmark in `benchmark/`: nineteen cases across six
  carrier families, scored on containment, article preservation and disclosure. The
  corpus is published at https://dmytrome.github.io/groundhog/ and results for five fetch
  layers are in `benchmark/RESULTS.md`. Not part of the published package.

## [0.12.0] - 2026-09-03

### Added

- `hidden_attribute` and `hidden_template` threat types.
- Text in `alt`, `aria-label`, `aria-description` and `title`: reported above 125
  characters, removed from the returned markup at any length.
- `<template>` content: reported above 20 characters, emptied from the returned markup.
  Content fragments are scanned as their own scope, covering nested templates and
  attribute carriers inside them.

### Fixed

- Attribute carriers projected through a `<slot>`, or held inside a shadow tree, are
  stripped from the returned markup.
- Attribute carriers on `<html>`, on `<body>`, and inside `<head>` are scanned.
- A hidden scan root suppresses the findings inside its subtree, including shadow scopes
  and slot projections.
- Findings are bounded at 400 per page and 100 for attribute and template carriers; the
  overflow is counted into the `report_truncated` notice.
- Threat ranking is not applied when detection is degraded.
- `spans_dropped` rejects a boolean and is clamped.

### Changed

- Conformance results regenerated against Chrome 151.

## [0.11.0] - 2026-08-07

### Changed

- `<script>` and `<style>` source is not reported in `threats[]` while the element doesn't
  render. It is still flagged and stripped. `script{display:block}` and SVG-namespaced tags
  stay reported.

### Fixed

- Every manifest version, the lockfile entry, the Dockerfile image pin and the changelog
  entry are checked against `pyproject.toml` by the test suite, which the release job runs
  before it publishes.

### Added

- `.cursor-plugin/plugin.json`, for the Cursor Marketplace.

## [0.10.1] - 2026-08-04

### Fixed

- The MCP handshake reports Groundhog's own version, read from installed package metadata.

## [0.10.0] - 2026-08-03

### Added

- `read_url` reports a `status` (`ok`, `challenge`, `blocked`, `rate_limited`, `not_found`,
  `server_error`, `unsupported_content`, `unknown`) and the raw `http_status`, read from the
  top-level response.
- The status describes the document the text was read from, after client-side redirects.
- Challenges are detected from vendor mitigation headers (`cf-mitigated`,
  `x-vercel-mitigated`, `x-amzn-waf-action`, `x-dd-b`, `x-datadome-cid`) and from requests
  for assets only a challenge loads (Cloudflare's challenge orchestrator, DataDome's captcha
  delivery, PerimeterX, Imperva). Challenge wording counts only on a page that renders too
  little text to be content.
- A response that was never observed, or reported no usable status, is `unknown`. Every
  unmapped 4xx is `blocked`.
- Non-HTML responses (PDFs, images, binaries) are `unsupported_content`.
- `research` reports the same status per source as `page_status`, and a source that isn't
  the page contributes no passages.
- MCP tool annotations on all four tools: `readOnlyHint`, `openWorldHint` and a title.

## [0.9.6] - 2026-08-01

### Added

- An `audit_hidden_text` prompt: given a URL, it has the model fetch the page twice, once
  stripped and once with `include_hidden=true`, and report the difference signal by signal,
  quoting what it finds rather than obeying it.

## [0.9.5] - 2026-08-01

### Changed

- Every tool parameter carries a description in the input schema, saying what it controls,
  its default, and for `limit` and `max_sources` that out-of-range values are clamped.
- Each tool's description says when to use a sibling instead: `search` returns links and
  never content, `read_url` is for a URL you already have, `research` reads several pages and
  is the slowest. `read_url` and `research` state the per-domain rate limit (5s by default).
- `read_url`'s description leaves its parameters to their own descriptions.

## [0.9.4] - 2026-08-01

### Fixed

- A shadow host with no light children is tested for hidden text.
- A `<slot>` hidden by its container doesn't project its nodes.
- An element with `display: contents` is judged by whether its contents paint anything.
- `<body>` is tested for hidden text.
- `include_hidden=true` keeps open shadow-root content.

### Changed

- `strip_incomplete`'s reason says the rendered text was rebuilt from markup rather than
  read from layout.

## [0.9.3] - 2026-08-01

### Fixed

- Requires Python 3.12 or later.

### Added

- `mcp/Dockerfile`: an image that runs the MCP server and the browser it drives, for
  registries that build a repository and expect an MCP handshake on stdio. The entrypoint
  starts the browser, waits for CDP, then hands stdio to the server, with browser logging on
  stderr. The build fails if the server's tools don't register.

## [0.9.2] - 2026-08-01

### Fixed

- The live document is never modified while stripping, so a page's custom-element callbacks
  can't write into the result. The markup is stripped in a separate inert document, and the
  rendered text comes from the live page with flagged nodes hidden by one adopted
  stylesheet.
- The copy is imported into a document from `createHTMLDocument`, which runs none of the
  page's code and keeps every node where it was.
- An element with `display: contents` is not reported as hidden. Its children are walked in
  their own right.
- `content-visibility: hidden` is detected, as a tenth signal. `content-visibility: auto`
  is not flagged.
- When the page defends hidden text with an inline `!important`, or hides its own
  `<body>`/`<html>`, the text is taken from the stripped markup.

### Added

- Content inside an open shadow root is read. It is scanned for hidden text in its own scope
  first, then composed into the result as the flat tree a reader sees: a `<slot>` is replaced
  by the nodes assigned to it, and an unfilled slot contributes its fallback. Nested roots
  compose recursively. Such a page reports `strip_incomplete`, and threat locations name the
  host and the boundary, e.g. `div#widget::shadow>section>div`. Closed shadow roots are not
  read.
- A `strip_incomplete` threat, reported when a flagged node won the cascade against the
  hiding stylesheet, the page hid its own root, or a recorded node position didn't resolve
  in the copy.

## [0.9.1] - 2026-08-01

### Fixed

- Page-authored strings are sanitized where they enter the process. `RenderedPage` strips
  invisible characters and bounds every metadata field on construction, and search hits get
  the same treatment. This covers `threats[].excerpt`, `.location` and `.reason`,
  `provenance.language`, a search hit's `published`, `engine`, `title` and `snippet`,
  `matches[].heading` and `passages[].heading`, and the text of errors a page can provoke.
  Two tests check the property over whole tool results.
- URLs are never rewritten. A search hit whose `url` doesn't survive cleaning unchanged is
  dropped, before the result limit is applied. An unusable `final_url` is replaced by the
  requested URL, with a `final_url_suppressed` threat. An unusable `canonical` is dropped.
- Every URL Groundhog reports passes one rule (`safety.safe_url`): characters, length, the
  `http`/`https` allowlist and no embedded credentials.
- Invisible-character findings (`zero_width`, `bidi`, `tag`) are reported on the `markdown`
  path, from the text the page served.
- `read_url` and `research`, including its search step, return an opaque error message
  that names no internal address. `BrowserUnavailableError` passes through.
- Malformed spans and metadata from the collector are dropped.
- The CDP websocket has a frame-size limit, page evaluations have a timeout, and the rate
  limiter evicts per-domain entries.
- The two threat classes are capped independently. Neither can take the last slot from the
  other; below two slots the hidden-node findings keep theirs.
- `threats` holds at most 50 findings per page, and 10 per source in `research`. A dropped
  tail is disclosed by a `report_truncated` entry, appended after the cap.
- `search` has the same error boundary as the other tools, and a SearXNG result's `score` is
  validated where the payload is parsed.
- `clean_field` rejects non-strings.
- The SSRF guard runs before the browser is acquired, and the browser starts only when a
  fetch needs it.
- `status` and the remediation hint report the CDP endpoint as scheme, host and port only.
- Page metadata is bounded at URL length.
- A cancelled CDP command, a dropped socket during a send, and a navigation that times out
  leave no pending entry or event waiter behind.
- A page that returns a non-string for `outerHTML`/`innerText` is handled, and in-page
  evaluation results are narrowed to text.
- The documented `docker run` command and the remediation message include
  `--shm-size 512m`.
- Invisible-character coverage includes the variation-selector supplement
  (U+E0100–E01EF), the space-like fillers (U+3164, U+2800, U+180E) and U+2028/U+2029. The
  base variation-selector block, which holds the emoji presentation selector, is not
  covered. Characters that occupy width are replaced by a space.
- Control characters in single-line fields are replaced by a space. A URL containing one is
  rejected.
- `search` passes a "browser not reachable" error through verbatim.
- A scheme-less or non-numeric-port `CDP_URL` is shown as given in `status` and the hint.

### Added

- The hidden-text collector, the readiness poll and every other read of the page run in an
  isolated JavaScript world (`Page.createIsolatedWorld`). The `Runtime` domain is never
  enabled. A browser that declines to provide a world gives a `detection_degraded` threat.
- The content is read inside the collector's own evaluation, so a page can't put a stripped
  node back before it is read.
- The README documents the limits of hidden-text detection: the style signals are evadable
  thresholds and the invisible-character coverage is a denylist, so an empty `threats` list
  is not proof that a page is clean.

### Changed

- The PyPI project page and the MCP registry description describe the current server.
- The documentation states that hidden-text stripping is a nine-signal heuristic over
  rendered styles with fixed, evadable thresholds; that `provenance` carries no fetch
  timestamp; that the SSRF guard withholds content from a URL redirecting into a private
  address while Chrome still issues the request; and that ranking runs on sanitized content
  only when `include_hidden` is false.
- The documentation covers the limits of the SSRF guard (blind SSRF via redirect,
  un-intercepted sub-resource requests, the DNS-rebinding window, the shared browser
  profile), that `threats` excerpts are page-chosen text, that the CDP endpoint is
  unauthenticated, that auto-start pulls a mutable `:latest` tag and removes a stale
  `groundhog-browser` container, the `research` cap of 10 sources, and that a failed source
  carries `provenance: null`.

## [0.9.0] - 2026-07-26

### Added

- `research(query, max_sources=5, max_tokens=None)` MCP tool: searches, reads the top
  sources through the stealth browser, and ranks passages across all of them in one BM25
  pass. Returns `passages` (text, source_url, heading, score) and `sources` (url, title,
  status, threats, provenance). At most one page per registrable domain. Passages are
  extracts, not summaries.
- A source that fails is reported in `sources` with a `blocked`/`timeout`/`error` status,
  and the fan-out is bounded.
- `search(query, limit=10)` MCP tool: ranked hits (title, url, snippet, engine, score). The
  backend is your own SearXNG instance when `SEARXNG_URL` is set (it needs
  `formats: [html, json]`), otherwise a search page rendered through the stealth browser.
  `GROUNDHOG_SEARCH_BACKEND` forces one.
- Hit titles and snippets are stripped of invisible characters.
- A search backend that is unreachable, has JSON disabled, has every upstream engine
  rate-limited, or whose rendered page doesn't match the expected layout raises an actionable
  error.

## [0.8.0] - 2026-07-25

### Added

- A "try it" playground (`demo/`): paste a URL, see the safe markdown, provenance and
  stripped threats. Runs behind Caddy with per-visitor and global rate limits, as a
  non-root container.

### Fixed

- Page titles and provenance metadata (author, dates, canonical) are stripped of
  invisible characters and length-capped.
- Requests to a URL with no public suffix (IP addresses, `localhost`, unknown TLDs) are
  rate-limited by bare host. Requires `tldextract>=5.3`.
- A non-positive `max_tokens` uses the default budget.
- The MCP server reconnects when the browser drops the CDP websocket.
- A `CDP_URL` naming the browser by DNS host (`http://chrome:9222` in Compose, a Kubernetes
  service, a remote machine) is dialled by resolved IP.

### Changed

- `read_url`'s `format` argument is typed `Literal["markdown", "text"]` and validated by the
  tool schema. Called directly as a library, an unrecognised format falls back to markdown.

## [0.7.0] - 2026-07-15

### Fixed

- After DOMContentLoaded, `fetch` waits until the network is quiet and the DOM has stopped
  changing for 1s (capped at 8s) before extracting, tracking in-flight requests via CDP
  `Network` events.

### Added

- A linux/arm64 image with Debian Chromium behind the same `/usr/local/bin/chrome`
  entrypoint. Stealth conformance is validated on the amd64 real-Chrome image. The publish
  workflow builds both platforms, and auto-start doesn't force `--platform linux/amd64`.

## [0.6.2] - 2026-07-07

### Fixed

- The SSRF guard re-validates the target URL immediately before `Page.navigate`.

## [0.6.1] - 2026-07-06

### Fixed

- The scheduled Conformance workflow's `publish` job opens a PR and merges it once checks
  pass.
- GitHub Actions updated to releases on the `node24` runtime.
- `iphey` is informational in the conformance harness: its Location flag follows IP hosting
  reputation. Documented in the README.

## [0.6.0] - 2026-07-06

### Added

- Hidden-text detection catches the sub-pixel box of `.sr-only`/`.visually-hidden` classes,
  `clip: rect(...)`, transparent text, text matching its background, and elements positioned
  entirely outside the page (`left: -9999px` and similar). Non-trivial HTML comments are
  reported in `threats`.

## [0.5.0] - 2026-07-05

### Added

- `GROUNDHOG_AUTO_START_BROWSER` defaults on: the MCP server pulls and runs the published
  stealth-browser image (`ghcr.io/dmytrome/groundhog`) with `docker run` when the browser
  isn't reachable. It detects Docker or Podman and otherwise gives an actionable message.
  `GROUNDHOG_BROWSER_IMAGE` overrides the image; `GROUNDHOG_COMPOSE_FILE` uses
  `docker compose`.
- The stealth-browser image is published to GHCR on each release.

### Changed

- Auto-start only manages a local `CDP_URL`.

## [0.4.0] - 2026-07-03

### Added

- Proxy geo-coherence: when `PROXY` is set, the container geolocates the exit IP and sets
  the browser timezone and locale to match. The country to locale table is CLDR
  likely-subtags (`locales.map`). The geo source is Bright Data's exit-IP endpoint, with an
  ip-api.com per-field fallback.
- Authenticated proxies, through a local tinyproxy relay that adds the credentials upstream
  (http/socks). The `Via` header is suppressed.
- WebRTC keeps the real IP hidden behind a proxy
  (`--force-webrtc-ip-handling-policy=disable_non_proxied_udp`).

### Changed

- `TZ` is derived from the proxy exit IP when `PROXY` is set, and is the fallback otherwise.

## [0.3.1] - 2026-07-03

### Changed

- Container fingerprint: a realistic desktop font set (about 330 fonts), fake media devices
  so `enumerateDevices()` isn't empty, an Xvfb screen larger than the window, and a dark
  color scheme. CreepJS rates it 38% "like headless" and 0% stealth/tamper.

## [0.3.0] - 2026-07-03

### Changed

- The engine drives the browser over raw CDP and never enables the Runtime/Console domains,
  so the session isn't flagged as automated (`isAutomatedWithCDP`).
- The container runs headful under Xvfb: it reports `Chrome`, not `HeadlessChrome`, and
  uses the GPU path.
- The User-Agent is set at container launch from the installed Chrome version, in every
  scope including Web/Service Worker globals.

### Added

- GPU-aware WebGL: the entrypoint detects NVIDIA / Intel-AMD GPUs and uses hardware
  acceleration, or Mesa llvmpipe. Opt-in GPU passthrough in `docker-compose.yml`.
- `TZ` (browser timezone) and `GROUNDHOG_MAX_CONCURRENT_PAGES` (concurrent-tab cap)
  settings.

### Removed

- The `--load-extension` stealth extension.
- `GROUNDHOG_USER_AGENT`.

## [0.2.0] - 2026-07-03

### Added

- Injection-aware grounding: text invisible to humans (`display:none`,
  `visibility:hidden`, `opacity <= 0.05`, `font-size < 4px`, zero-size) is stripped before
  content is returned, and each occurrence is reported in `threats[]`. Pass
  `include_hidden=true` to keep it.
- Query-focused retrieval: the `query` param ranks passages by BM25 relevance within the
  token budget; `matches[]` gives each passage's heading, offset and score. Ranking runs on
  sanitized content.
- Citable provenance: the `provenance` field adds content hash, canonical URL, detected
  language, word count, and author/byline and published/modified date when present.
- Article-first Markdown extraction (trafilatura) with a full-text fallback.
- Text-level sanitizer for invisible characters: zero-width, bidi (marks,
  embeddings/overrides, and isolates), and the Unicode Tag block.

### Fixed

- Query retrieval keeps body text when a heading has no blank line before it.

### Changed

- The engine provider is closed via the FastMCP lifespan.

## [0.1.1] - 2026-07-01

### Added

- Package README (shown as the PyPI project description) and an MCP Registry manifest
  (`server.json`). Installable via `uvx groundhog-mcp` and listed on the MCP Registry.

## [0.1.0] - 2026-07-01

Initial release.

### Added

- `read_url` tool returning clean Markdown plus provenance (`url`, `final_url`,
  `title`, `fetched_at`, `truncated`); `format` is `markdown` or `text`, with
  token-budget truncation at paragraph boundaries.
- `status` tool reporting whether the browser (CDP endpoint) is reachable, with a hint.
- SSRF guard: allows only `http`/`https`, rejects credentials in URLs, resolves the host
  and blocks loopback, private (RFC-1918), link-local (incl. `169.254.169.254`),
  reserved, multicast, unspecified, CGNAT `100.64.0.0/10`, and IPv4-mapped IPv6, with a
  post-redirect re-check of the final URL.
- Per-domain rate limiter.
- Stealth Chrome engine over CDP (`connect_over_cdp`) with configurable User-Agent and
  optional upstream proxy via `PROXY`.
- FastMCP server over stdio; an actionable error and opt-in `GROUNDHOG_AUTO_START_BROWSER`
  (with `GROUNDHOG_COMPOSE_FILE`) when the browser isn't running.

[0.20.1]: https://github.com/dmytrome/groundhog/releases/tag/v0.20.1
[0.20.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.20.0
[0.19.1]: https://github.com/dmytrome/groundhog/releases/tag/v0.19.1
[0.19.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.19.0
[0.18.2]: https://github.com/dmytrome/groundhog/releases/tag/v0.18.2
[0.18.1]: https://github.com/dmytrome/groundhog/releases/tag/v0.18.1
[0.18.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.18.0
[0.17.2]: https://github.com/dmytrome/groundhog/releases/tag/v0.17.2
[0.17.1]: https://github.com/dmytrome/groundhog/releases/tag/v0.17.1
[0.17.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.17.0
[0.16.1]: https://github.com/dmytrome/groundhog/releases/tag/v0.16.1
[0.16.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.16.0
[0.15.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.15.0
[0.14.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.14.0
[0.13.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.13.0
[0.12.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.12.0
[0.11.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.11.0
[0.10.1]: https://github.com/dmytrome/groundhog/releases/tag/v0.10.1
[0.10.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.10.0
[0.9.6]: https://github.com/dmytrome/groundhog/releases/tag/v0.9.6
[0.9.5]: https://github.com/dmytrome/groundhog/releases/tag/v0.9.5
[0.9.4]: https://github.com/dmytrome/groundhog/releases/tag/v0.9.4
[0.9.3]: https://github.com/dmytrome/groundhog/releases/tag/v0.9.3
[0.9.2]: https://github.com/dmytrome/groundhog/releases/tag/v0.9.2
[0.9.1]: https://github.com/dmytrome/groundhog/releases/tag/v0.9.1
[0.9.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.9.0
[0.8.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.8.0
[0.7.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.7.0
[0.6.2]: https://github.com/dmytrome/groundhog/releases/tag/v0.6.2
[0.6.1]: https://github.com/dmytrome/groundhog/releases/tag/v0.6.1
[0.6.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.6.0
[0.5.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.5.0
[0.4.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.4.0
[0.3.1]: https://github.com/dmytrome/groundhog/releases/tag/v0.3.1
[0.3.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.3.0
[0.2.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.2.0
[0.1.1]: https://github.com/dmytrome/groundhog/releases/tag/v0.1.1
[0.1.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.1.0
