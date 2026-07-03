# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.2.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.2.0
[0.1.1]: https://github.com/dmytrome/groundhog/releases/tag/v0.1.1
[0.1.0]: https://github.com/dmytrome/groundhog/releases/tag/v0.1.0
