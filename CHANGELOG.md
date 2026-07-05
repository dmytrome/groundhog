# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-07-05

### Added

- One-command turnkey install: `GROUNDHOG_AUTO_START_BROWSER` now defaults on, and the MCP
  server pulls-and-runs the published stealth-browser image (`ghcr.io/dmytrome/groundhog`)
  with `docker run` when the browser isn't reachable — no repo checkout or manual step.
  Detects Docker or Podman and falls back to an actionable message (install a runtime, or
  point `CDP_URL` at a hosted browser). `GROUNDHOG_BROWSER_IMAGE` overrides the image;
  `GROUNDHOG_COMPOSE_FILE` still opts into `docker compose` for local-repo use.
- The stealth-browser image is published to GHCR on each release.

### Changed

- Auto-start only manages a local `CDP_URL`; a remote/hosted endpoint is left untouched.

## [0.4.0] - 2026-07-03

### Added

- Proxy geo-coherence: when `PROXY` is set, the container geolocates the exit IP and aligns
  the browser timezone and locale to it — a timezone or locale that disagrees with the IP is
  itself a block signal. The country → locale table is CLDR likely-subtags (`locales.map`).
  Geo source is Bright Data's exit-IP endpoint, with an ip-api.com per-field fallback.
- Authenticated proxies: Chrome cannot pass credentials over `--proxy-server`, so a local
  tinyproxy relay injects them into the upstream (http/socks). The `Via` header is
  suppressed so a proxy hop is not announced on plain-HTTP requests.
- WebRTC no longer leaks the real IP behind a proxy
  (`--force-webrtc-ip-handling-policy=disable_non_proxied_udp`).

### Changed

- `TZ` is derived from the proxy exit IP when `PROXY` is set; it remains the fallback
  otherwise.

## [0.3.1] - 2026-07-03

### Changed

- Container fingerprint hardening (no spoofing, so no detectable tampering): a realistic
  desktop font set (fonts jump from ~16 to ~330), fake media devices so
  `enumerateDevices()` is not empty, an Xvfb screen larger than the window (viewport no
  longer equals screen), and forced dark color-scheme. Lowers CreepJS "like headless" from
  44% to 38% while keeping its stealth/tamper score at 0%.

## [0.3.0] - 2026-07-03

### Changed

- The engine drives the browser over raw CDP and never enables the Runtime/Console domains,
  so the session is not flagged as automated (`isAutomatedWithCDP`). This replaces the
  Playwright client, which enables Runtime and is detectable over `connect_over_cdp`.
- The container runs headful under Xvfb instead of `--headless=new`: it reports `Chrome`
  (not `HeadlessChrome`), avoids headless-specific tells, and engages the GPU path.
- The User-Agent is set at container launch from the installed Chrome version, so it is
  clean in every scope including Web/Service Worker globals. Removes `GROUNDHOG_USER_AGENT`.

### Added

- GPU-aware WebGL: the entrypoint auto-detects NVIDIA / Intel-AMD GPUs and uses hardware
  acceleration, falling back to Mesa llvmpipe (a coherent software renderer). Opt-in GPU
  passthrough in `docker-compose.yml`.
- `TZ` (browser timezone, to match the proxy/exit-IP geo) and `GROUNDHOG_MAX_CONCURRENT_PAGES`
  (concurrent-tab cap) settings.

### Removed

- The `--load-extension` stealth extension, which recent Chrome ignores; its patches were
  redundant with native Chrome behavior.

## [0.2.0] - 2026-07-03

### Added

- Injection-aware grounding: text invisible to humans (`display:none`,
  `visibility:hidden`, `opacity <= 0.05`, `font-size < 4px`, zero-size) is stripped before
  content is returned, and each occurrence is reported in `threats[]`. Pass
  `include_hidden=true` to keep it.
- Query-focused retrieval: the `query` param ranks passages by lexical (BM25) relevance
  within the token budget instead of head-truncation; `matches[]` gives each passage's
  heading, offset, and score. Ranking runs on sanitized content so hidden-text injection
  cannot influence which passages surface.
- Citable provenance: the `provenance` field adds content hash, canonical URL, detected
  language, word count, and author/byline + published/modified date when present.
- Article-first Markdown extraction (trafilatura) with a full-text fallback.
- Text-level sanitizer for invisible characters: zero-width, bidi (marks,
  embeddings/overrides, and isolates), and the Unicode Tag block.

### Fixed

- Query retrieval no longer drops body text when a heading has no blank line before it.

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
  reserved, multicast, unspecified, CGNAT `100.64.0.0/10`, and IPv4-mapped IPv6 — with a
  post-redirect re-check of the final URL.
- Per-domain rate limiter.
- Stealth Chrome engine over CDP (`connect_over_cdp`) with configurable User-Agent and
  optional upstream proxy via `PROXY`.
- FastMCP server over stdio; an actionable error and opt-in `GROUNDHOG_AUTO_START_BROWSER`
  (with `GROUNDHOG_COMPOSE_FILE`) when the browser isn't running.

[0.5.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.5.0
[0.4.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.4.0
[0.3.1]: https://github.com/dmytrome/groundhog/releases/tag/v0.3.1
[0.3.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.3.0
[0.2.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.2.0
[0.1.1]: https://github.com/dmytrome/groundhog/releases/tag/v0.1.1
[0.1.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.1.0
