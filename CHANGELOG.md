# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

A minor bump rather than a patch: what `threats[]` reports changes for every page that
carries a `<script>` or a `<style>`, which is nearly all of them.

### Changed

- `<script>` and `<style>` source is no longer reported as hidden text. Both compute to
  `display:none`, so every one of them was flagged and its own source became the excerpt —
  text that reaches neither `innerText` nor the extracted Markdown. The findings were never
  a leak, but they spent the 50-threat cap and handed the page one more attacker-chosen
  string to put in front of the model. Inline SVG icon sprites were the worst case, each
  reporting its `<style>` block.

  They are still flagged and still stripped; only the report entry is skipped, and only
  while the element does not render. Two cases measured in Chrome 150 that a bare tag test
  gets wrong, and which stay reported: `script{display:block}` really does put the source
  on the page as text, so a page can render it and then hide it like any other text; and
  SVG-namespaced tags come back `display:inline` with no box, so they are caught as
  `zero-size` rather than `display:none`.

### Fixed

- `gemini-extension.json` was left on `0.10.0` by the previous release, so the Gemini CLI
  gallery advertised a version that disagreed with the one the server reports in the
  handshake. Every manifest version, the lockfile entry, the Dockerfile image pin and the
  changelog entry are now asserted against `pyproject.toml` by the test suite, which the
  release job runs before it publishes — so this fails the release instead of reaching a
  registry.

### Added

- `.cursor-plugin/plugin.json`, for the first-party Cursor Marketplace. That is a separate
  index from `cursor.directory`, and the in-IDE Plugins panel searches it.

## [0.10.1] - 2026-08-04

### Fixed

- The MCP handshake reported the SDK's version as Groundhog's, so a client asking what it
  had connected to was told `v1.29.0` — the version of `mcp`. The low-level server falls
  back to `pkg_version("mcp")` when its own version is unset, and `FastMCP` takes no
  version argument, so the fallback always won. It is now read from installed package
  metadata, which cannot drift from what was released.

  Found by watching an agent install the server from `README.md` and `llms-install.md`
  alone — the shape of bug that reading the code does not surface.

## [0.10.0] - 2026-08-03

A minor bump rather than a patch: `read_url` and `research` results carry new fields, and
`research` no longer ranks a challenge or error page's body into its passages — a change in
what an existing call returns, even though it is the bug being fixed. Still 0.x deliberately:
`blocked` currently means an SSRF refusal in `research`'s `status` and an HTTP 4xx in
`page_status`, which wants renaming, and the MCP SDK v2 migration is still ahead. Neither is
a change worth freezing behind a 1.0 compatibility promise yet.

### Added

- `read_url` now reports what actually came back, not only the extracted text: a `status`
  (`ok`, `challenge`, `blocked`, `rate_limited`, `not_found`, `server_error`,
  `unsupported_content`, `unknown`) plus the raw `http_status`. A Cloudflare interstitial, a
  403, or a PDF is no longer returned as if it were the page — the caller can branch on it
  instead. The top-level response is read from CDP's `Network.responseReceived` (status, MIME
  type, and the `cf-mitigated` challenge header); classification is a pure function with its
  own unit suite, and the real capture is covered by the browser-backed tests.

  The verdict describes the document the text was actually read from. A page that redirects
  client-side — meta-refresh, or `location.href` from a script — replaces the document after
  the navigation returns, so the response is looked up by the *current* main frame's loader
  rather than the one `Page.navigate` reported. Keying on the navigation would have described
  the page that was left behind: a 200 redirecting to a 404 would have handed back the 404's
  body while claiming `ok`, and a 403 interstitial redirecting to the real article would have
  thrown that article away as `blocked`. Anti-bot flows redirect this way routinely. Both
  directions are pinned by browser-backed tests.

  Challenge detection is keyed on **vendor mitigation markers, not on wording**. A
  response header that exists only to announce mitigation (`cf-mitigated`, which
  Cloudflare documents as present on every challenge type, plus `x-vercel-mitigated`,
  `x-amzn-waf-action`, `x-dd-b`, `x-datadome-cid`), or a request for an asset only a
  challenge loads (a Cloudflare challenge orchestrator, DataDome's captcha delivery,
  PerimeterX, Imperva) is decisive on its own. Both are language-independent, so a
  challenge served in German is caught as readily as one in English — which no list of
  English phrases can do. The asset check runs against the URLs the page really
  requested rather than against its markup, because the same string inside HTML could
  be an article *about* the vendor, and because `<script>` is stripped from the markup
  that is returned.

  Wording is now the last tier and never decides alone: it must be corroborated by the
  page rendering too little text to be content. That is what an interstitial always is,
  in any language, and it is what an article whose *title* happens to read "Just a
  Moment (2024)" never is — so the false positive that would have cost that source
  every one of its passages is closed structurally rather than by tuning a threshold.

  Signals deliberately **not** used: `server: cloudflare`, `cf-ray` and `__cf_bm` mean
  only that a site sits behind a CDN, which is true of much of the web on every page it
  serves normally — a captured LinkedIn `999` block carries all three while having
  nothing to do with Cloudflare. `cf_clearance` is excluded because it is issued when a
  challenge is *passed*. Turnstile and reCAPTCHA widget URLs are excluded because
  ordinary login and contact forms embed them.

  A response that was never observed — or that never reported a usable status, which is
  what a service-worker-synthesized or client-blocked request gives — is reported as
  `unknown`, not `ok`. The distinction between "verified fine" and "not verified" is the
  caller's to make. Every 4xx that is not already mapped reports `blocked` rather than
  falling through to `ok`, so a `451` or a `400` error page is not handed back as content.
- Non-HTML responses (PDFs, images, binaries) are reported as `unsupported_content` rather
  than returning an empty or junk render.
- `research` carries the same verdict per source as `page_status`, alongside the existing
  fetch-outcome `status`, so a source that loaded but was a challenge or a non-HTML body is
  visible rather than passing as `ok`. Such a source now contributes **no passages**: an
  interstitial's body would otherwise be ranked against the query and compete for the caller's
  token budget with real content, and a `Passage` carries no status of its own to notice it by.
- MCP tool annotations on all four tools: `readOnlyHint` — so a client can auto-approve reads
  without a per-call confirmation — and `openWorldHint`, plus a human-readable title. These
  are also a prerequisite for listing in the Claude Connectors Directory and the MCPB bundle.

## [0.9.6] - 2026-08-01

### Added

- An `audit_hidden_text` prompt: given a URL, it walks the model through fetching the page
  twice — once stripped, once with `include_hidden=true` — and reporting the difference,
  signal by signal. Both halves are ordinary `read_url` calls; the comparison between them
  is the part nobody composes on their own, and it is the one thing this server does that a
  plain fetcher cannot. The template also tells the model to quote what it finds rather than
  obey it, since the text it is being walked through was written to steer models.

  Prompts reach fewer clients than tools (5 of 22 on the official clients page, against 20
  for tools), but those five include Claude Desktop and Claude Code, where it appears as a
  slash command. Resources are still not implemented: they carry contextual data the client
  attaches and manages, and a fetcher that reads live pages on demand has no corpus to hand
  over.

## [0.9.5] - 2026-08-01

### Changed

- Every tool parameter now carries a description in the input schema, taking schema
  description coverage from 0% to 100%. A schema states a parameter's type and default but
  not what it means, so `limit`, `max_sources` and `max_tokens` were values an agent had to
  guess at. Each now says what it controls, what the default is, and — for the two capped
  ones — that out-of-range values are clamped rather than rejected, which is what the code
  has always done.
- Each tool's description now says when *not* to reach for it, naming the sibling that fits
  instead: `search` returns links and never content, `read_url` is for a URL you already
  have, `research` reads several pages and is the slowest. `read_url` and `research` also
  disclose the per-domain rate limit (5s by default), which is the main reason a call is
  slower than an agent might expect.
- `read_url`'s description no longer enumerates its parameters, since each parameter now
  documents itself.

  Tool names are deliberately unchanged. `search`/`read_url`/`research`/`status` mix naming
  styles, but renaming them would break every existing client configuration for a
  consistency that costs real users something.

## [0.9.4] - 2026-08-01

### Fixed

- Five ways page content reached the model without ever being examined, all introduced by
  0.9.2's shadow-DOM composition and all found by review of that release. The rule that
  makes composing shadow content safe is that scanning strictly precedes it; each of these
  broke that rule somewhere different:
  - **A shadow host with no light children was never tested.** The walk skips elements whose
    `textContent` is empty, and `textContent` is node-tree text — a host reads as empty
    however much its shadow tree renders. `<x-note style="display:none"></x-note>` with the
    payload in its shadow root was never passed to any signal, then composed into the
    output. The gate now counts the shadow tree's text too.
  - **A `<slot>` hidden by its container projected anyway.** A filled slot has no text of its
    own and is `display: contents`, so it had no box to measure; the container hiding it has
    no text either. Neither was examined and the projected nodes were composed in.
  - **`display: contents` skipped every box test.** That guard existed so a component's slot
    fallback was not flagged on sight, but `zero-size` is the only signal modelling "no box
    because of where this sits", so skipping it let such an element inside a `display:none`
    subtree through. It is now judged by whether its contents paint anything — measured in
    Chrome 150, a real wrapper returns boxes and a hidden one returns none. `checkVisibility()`
    is not usable here: it reports `false` for legitimate wrappers too.
  - **`<body>` was never tested.** A TreeWalker never returns its own root, so the one
    element no signal was applied to was the body. `content-visibility: hidden` on it kept a
    principal box, so the layout-collapse check did not fire either, and text owned by no
    element — a bare text node, or one under a `display: contents` wrapper — had nothing else
    to catch it.
  - **`include_hidden=true` dropped shadow content silently.** The composed copy is the only
    place that content exists and it was built only when stripping, so the very omission
    0.9.2 closed for the default path remained on that one, undisclosed. The copy is now
    built whenever there are open shadow roots, and nothing is dropped from it when the
    caller asked to keep hidden text.

  Each is covered by a live test that fails when its fix is reverted.

### Changed

- `strip_incomplete`'s reason now says what happened — the rendered text was rebuilt from
  markup rather than read from layout — instead of implying a node resisted removal. Its
  most common trigger is a page using open shadow roots, which is routine, not adversarial.

## [0.9.3] - 2026-08-01

### Fixed

- The package declared `requires-python = ">=3.11"` but did not work on 3.11: pydantic
  rejects `typing.TypedDict` below 3.12, so building the tool schemas raised and **every
  one of the four tools failed to register**. An installer honouring that metadata — `uvx
  groundhog-mcp` on a machine whose default interpreter is 3.11 — got a server that could
  do nothing. The floor is now 3.12, which is the version CI has always tested; the
  metadata claimed a range nothing verified.

### Added

- `mcp/Dockerfile`: an image that runs the MCP server *and* the browser it drives, for
  registries that build a repository and then expect an MCP handshake on stdio. The
  repository root `Dockerfile` builds the browser alone — it exposes CDP and never speaks
  MCP — so such a build produced a container that looked broken. The entrypoint starts the
  browser, waits for CDP, and only then hands stdio to the server, with the browser's
  logging kept on stderr because stdout is the transport. A build step asks the server for
  its tools, so an image whose tools cannot register fails the build instead of shipping.

## [0.9.2] - 2026-08-01

### Fixed

- A page can no longer write into the result by reacting to the strip. `Element.remove()`
  is `[CEReactions]`, so removing a hidden node ran a custom element's
  `disconnectedCallback` *synchronously*, and that callback could add content the reader
  never saw — as a bare text node, by writing into an element that already existed, by
  moving a node into view, or through `document.title` and `<meta>`. None of it was
  reported. A single hidden decoy element was enough.

  Nothing is removed from the live document now, so the callback never runs at all and all
  five routes close together. The markup is stripped inside a separate inert document; the
  rendered text still comes from the live page, with the flagged nodes hidden by one
  adopted stylesheet — which disconnects nothing and rewrites no existing attribute, so it
  queues no reaction. Covered by
  `tests/test_engine_live.py::test_a_removal_reaction_cannot_write_into_the_result`,
  parametrized over all five, plus a page that re-renders on disconnect to prove ordinary
  content still comes through.
- The copy taken to build that markup no longer runs the page's code either. `cloneNode` is
  itself `[CEReactions]`: it re-creates every custom element with the synchronous flag
  unset, which enqueues an upgrade reaction drained as it returns — so a clone would have
  run the page's `constructor` and `attributeChangedCallback` inside the strip, moving the
  hook rather than closing it. The tree is now imported into a document from
  `createHTMLDocument`, which has no browsing context, so no definition is looked up and no
  reaction is queued. Covered by `test_an_upgrade_reaction_cannot_write_into_the_result`.
- Imported rather than serialized and reparsed, so the strip removes the node it meant to.
  Reparsing is not structure-preserving — measured in Chrome 150, adjacent text nodes merge
  into one, `<noscript>` parses as markup with scripting off, and a script-inserted child of
  `<table>` is foster-parented out. Each shifts every later sibling, so the recorded node
  positions addressed the wrong nodes: the strip deleted visible text and left the hidden
  payload in place. Covered by `test_node_indices_still_address_the_right_node_after_a_shift`.
- An element with `display: contents` is no longer reported as hidden. It generates no box
  of its own while its children render normally, so every box-shaped test read it as
  invisible — which made a web component's `<slot>` fallback copy a finding on sight, since
  `<slot>` is `display: contents` by default. Its children are still walked in their own
  right, so nothing goes unexamined.
- `content-visibility: hidden` is detected, closing a bypass that delivered a payload
  straight into the Markdown with **no threat reported at all**. It skips the subtree from
  layout while the element keeps an ordinary `display`, a real box and a normal font, so
  every one of the nine existing signals missed it — yet a reader sees nothing and it is
  absent from `innerText`. It is now a tenth signal. `content-visibility: auto` is
  deliberately not flagged: that content renders once scrolled into view.
- Hidden text no longer reaches `format="text"` and the extraction fallback when the page
  defends it with an inline `!important`, which beats the author stylesheet that hides
  flagged nodes, or when the page hides its own `<body>`/`<html>` — nothing renders then, so
  `innerText` falls back to raw text and hands back everything the page hid, which the sheet
  cannot suppress because `innerText` on an unrendered element never consults layout at all.
  Both are detected and the text is taken from the already-stripped markup instead.

### Added

- Content rendered inside an **open shadow root** is read. `importNode` does not carry
  shadow roots and neither `outerHTML` nor `innerText` crosses one, so a page built from web
  components previously came back with that content missing from `markdown`, `text` and the
  extraction alike — silently, since nothing reported the omission. The shadow tree is now
  scanned for hidden text in its own scope and composed into the result as the flat tree a
  reader sees: `<slot>` is replaced by the nodes assigned to it, so light children appear
  once and in the position they render, and an unfilled slot contributes its fallback.
  Nested roots compose recursively. Scanning comes first by construction — a node flagged
  anywhere, in a shadow tree or already removed from the light DOM, is skipped during
  composition, so this adds content to the output without adding a route into it that the
  detector never examined. A `<slot>` is examined in its own right, since a filled one has
  no text of its own and the nodes it projects inherit their style through it. Verified in
  Chrome 150 to queue no custom-element reaction and to leave the live tree untouched.

  Reading a page this way means its text is rebuilt from markup rather than from layout,
  which is a weaker source, so such a page reports `strip_incomplete` — a page can attach
  an open shadow root at will, and that choice should not be silent. Threat locations name
  the host and the boundary they crossed, e.g. `div#widget::shadow>section>div`.

  **Closed** shadow roots remain unreadable from the isolated world; their content is
  therefore neither scanned nor composed in, and stays out of the result as before.
- A `strip_incomplete` threat, reported when the strip could not remove a flagged node
  outright: the node won the cascade against the hiding stylesheet, the page hid its own
  root so nothing rendered, or a recorded node position did not resolve in the copy. Each
  leaves a weaker guarantee than a structural removal, so the caller is told rather than
  being handed a result that looks fully stripped.

## [0.9.1] - 2026-08-01

### Fixed

- Page-authored strings are now sanitized at the boundary where they enter the process,
  rather than at each place that reads them. `RenderedPage` — the browser's output — strips
  invisible characters and bounds every metadata field on construction (`html` and `text`
  carry the content itself and are stripped downstream, with threat collection), and search
  hits get the same treatment where they arrive. Sanitization used to be applied per call site, so
  each field added later silently opted out; these were reaching the model unsanitized,
  unbounded, or both, and a zero-width, bidi or Unicode-Tag payload in any of them arrived
  intact:
  - `threats[].excerpt` and `.location` — the payload survived inside the report about the
    stripping that removed it. `.reason` is cleaned too, as a precaution.
  - `provenance.language`, which is the page's own `<html lang>` attribute verbatim whenever
    the extracted text is too short to classify or classification fails.
  - a search hit's `published` and `engine`; `title` and `snippet` were stripped but
    unbounded, and are now capped.
  - URLs, which are never rewritten, because a rewritten URL is a citation that points
    somewhere else. A search hit whose `url` does not survive cleaning unchanged is dropped
    (reachable on the DuckDuckGo path, which percent-decodes the redirect wrapper), and a
    `final_url` that does not survive falls back to the URL actually requested. Filtering
    now happens before the result limit is applied, so a dropped hit does not cost a slot a
    clean result would have filled.
  - `matches[].heading` / `passages[].heading`, which are repeated per passage and sit
    outside `max_tokens` entirely.
  - the text of errors a page can provoke — a failed page evaluation, a navigation failure,
    and the detail SearXNG reports for a dead engine.

  Two tests assert the property over whole tool results rather than field by field, so a
  field added later that reaches the model is caught without a new case being written for it.
- Invisible-character findings (`zero_width`, `bidi`, `tag`) were never reported on the
  default `markdown` path. The extractor removes those characters on its way to Markdown, so
  scanning its output found nothing: the payload was dropped but the caller was never told
  the page had carried one — the disclosure this field exists for. Detection now runs on the
  text the page actually served.
- `read_url` returned raw exception text to the caller, including the SSRF guard's
  `blocked address: <host> -> <resolved ip>`, publishing internal addresses into model
  context. It now returns the same opaque message `research` already used; both share one
  rule. `BrowserUnavailableError` still passes through, since its remediation text is ours
  and the caller needs it.
- The boundary trusted the *shape* of the collector's payload, which is built in the page's
  own JS world: a malformed entry raised an unhandled error instead of being rejected.
  Malformed spans and metadata are now dropped.
- A page's final URL is never rewritten. When it is unusable, the requested URL is reported
  and a `final_url_suppressed` threat discloses the substitution rather than implying no
  redirect occurred.
- Bounded three unbounded resources: the CDP websocket had no frame-size limit (a page
  choosing its own `outerHTML` size could exhaust memory), page evaluations had no timeout
  (a page that wedges its renderer after load held a concurrency slot indefinitely), and the
  rate limiter never evicted per-domain entries.
- The two threat classes are capped independently. A single cap over both let the page pick
  which findings survived: flooding the report with decoys of one kind pushed every finding
  of the other past the limit — including the hidden-node entries carrying the injection
  excerpt.
- Search hits are now checked against the `http`/`https` allowlist at the shared boundary.
  One backend enforced it and the other did not, so a `javascript:` or `file:` URL could be
  returned to the model as a citation.
- `search` gained the error boundary the other two tools had, and a SearXNG result's `score`
  is validated where the payload is parsed — a non-numeric value raised an unbounded,
  unsanitized error straight to the caller.
- `canonical` is dropped rather than truncated when cleaning would change it. It is a URL in
  a provenance receipt, so a rewritten one is a citation pointing somewhere else — the rule
  already applied to `final_url` and to search hits, now shared as `safety.safe_url`.
- `clean_field` rejects non-strings. Values like `document.title` come from the page's own JS
  world and can be shadowed to return anything, which previously raised on the boundary.
- The SSRF guard runs before the browser is acquired, so a URL it will reject no longer
  triggers a container start and image pull.
- `status` and the remediation hint report the CDP endpoint as scheme, host and port only. A
  hosted browser commonly carries its credential in the URL, and both values reach the model
  — the treatment `SEARXNG_URL` already had.
- Every URL Groundhog *reports* — from a page, a search engine or a redirect — now passes
  one rule (`safety.safe_url`): characters, length, the `http`/`https` allowlist and no
  embedded credentials. (`read_url`'s `url` field is the caller's own argument, echoed back
  as received.) Previously a `javascript:` or `data:`
  `canonical` reached `provenance` intact, and a hit URL carrying `user:pass@` was returned
  as a citation.
- Page metadata is bounded at URL length rather than metadata length, because `canonical` is
  read back out of that map: truncating it first left the URL check comparing against an
  already-shortened string and accepting a rewritten URL as unchanged.
- `research`'s own search leg gained the error boundary `search` has. Without it a blocked
  SERP fetch returned `blocked address: <host> -> <internal ip>` to the model.
- A cancelled CDP command no longer leaves its pending entry behind, and a page that returns
  a non-string for `outerHTML`/`innerText` no longer raises at the boundary.
- The browser is no longer started when the provider is first requested, only when a fetch
  actually needs it — so a URL the SSRF guard rejects never triggers a container start.
- `threats` is now bounded: at most 50 findings per page, and 10 per source in `research`
  where the fan-out multiplies it (notices are appended after the cap, so a disclosure is
  never itself dropped). It was unbounded and outside `max_tokens`, so a page with
  many hidden nodes could flood the caller's context. A dropped tail is disclosed by a
  `report_truncated` entry — its own type, so it cannot be miscounted as a finding.
- The documented `docker run` command omitted `--shm-size 512m`, which auto-start always
  passes; following the docs produced a browser more likely to crash on heavy pages. The
  "browser not reachable" remediation message omitted it too.
- Invisible-character coverage extended to the variation-selector supplement (U+E0100–E01EF,
  immediately above the Tag block), the space-like fillers (U+3164, U+2800, U+180E) and the
  line/paragraph separators (U+2028/U+2029). The base variation-selector block is
  deliberately excluded: U+FE0F is the emoji presentation selector, so flagging it would
  fill `threats` with false positives and strip the selector out of every emoji on a page.
  Characters that occupy width are replaced by a space rather than deleted, so sanitizing
  cannot join two words a human reads as separate.
- Control characters are removed from single-line fields. A newline in a title, error detail
  or URL is a free line break in model context, and `urlparse` discards tab/CR/LF silently —
  so a URL could pass the never-rewritten check as one string and be returned as another.
- `search` now passes a "browser not reachable" error through verbatim, as the other two
  tools do. It was being sanitized like a third party's text and truncated to 200
  characters, cutting the suggested `docker run` command mid-image-name.
- Control characters are replaced by a space rather than deleted, for the same reason as
  the width-occupying invisibles: a newline separates words, so dropping it joined two the
  reader sees apart. A URL containing one is still rejected outright.
- Results of in-page evaluations are narrowed to text at the boundary. Each reads a DOM
  property a page can shadow, and a CDP reply carrying no value arrives as `None` — both
  previously raised inside the SSRF guard or the collector's own indexing.
- A misconfigured `CDP_URL` no longer breaks the tool that reports it: a scheme-less value
  is shown rather than erased (`urlparse` reads its host as a scheme), and a non-numeric
  port no longer raises out of `status` or the remediation hint.
- The threat cap divides its budget so neither class can take the last slot from the other;
  below two slots the hidden-node findings keep theirs, since those carry the excerpt.
- A CDP command registers and sends inside the block that cleans it up, so a dropped socket
  or a cancellation during the send no longer strands a pending entry.
- A CDP event waiter now deregisters itself when its future settles. A navigation that timed
  out, or failed before the load event fired, previously retained one for the life of the
  connection.

### Added

- The hidden-text collector now runs in an isolated JavaScript world
  (`Page.createIsolatedWorld`), as does every other read of the page. It previously ran in
  the page's own world, where the DOM APIs it depends on — `Array.prototype.push`,
  `document.createTreeWalker`, `getComputedStyle` — are replaceable, so a page could
  suppress its own hidden-text report entirely: nothing detected, nothing stripped, and the
  payload delivered to the model with an empty `threats` list.
  `tests/test_engine_live.py::test_detection_survives_a_page_that_patches_the_collectors_builtins`
  serves exactly such a page and asserts the injection is reported and kept out of the
  content; a companion test asserts every live fetch actually obtains an isolated world, so
  the mechanism cannot silently switch off. The `Runtime` domain is still never enabled, so
  the `isAutomatedWithCDP` signal remains absent. If a browser declines to provide a world,
  the result carries a `detection_degraded` threat rather than silently weaker detection.
- Stripped nodes can no longer be put back before the content is read. `remove()` mutates
  the DOM the isolated world shares with the page, so a page observing its own subtree could
  re-append a hidden node in the gap before `outerHTML`/`innerText` were fetched — the
  payload was reported in `threats` and *still* delivered in the content. Those reads now
  happen inside the collector's own evaluation, where a MutationObserver callback cannot
  interleave. Covered by
  `tests/test_engine_live.py::test_a_page_cannot_reinsert_a_stripped_node_before_the_content_is_read`.
- The readiness poll runs in an isolated world too. It was the last read left in the page's
  own world, so a page instrumenting `querySelectorAll` could count the probes and learn
  that extraction was underway.
- The README documents the limits of hidden-text detection, alongside the existing limits of
  the SSRF guard: the style signals are evadable thresholds and the invisible-character
  coverage is a denylist, so **an empty `threats` list is still not proof that a page is
  clean**.

### Changed

- The PyPI project page and the MCP registry description now describe the current server.
  The PyPI page still described the 0.1.x product — two tools, no `search`, no `research` —
  and still said a running browser was required, untrue since 0.5.0 made auto-start the
  default.
- Documentation now states what the code does, in place of claims that outran it:
  hidden-text stripping is a nine-signal heuristic over rendered styles with fixed,
  evadable thresholds rather than a guarantee; `provenance` carries no fetch timestamp
  (`read_url` returns `fetched_at` separately, `research` sources carry none); the SSRF
  guard withholds content from a URL redirecting into a private address, but the request is
  still issued by Chrome; and ranking runs on sanitized content only when `include_hidden`
  is false.
- Caveats now documented: the limits of the SSRF guard (blind SSRF via redirect,
  un-intercepted sub-resource requests, the DNS-rebinding window between Groundhog's
  resolver and Chrome's, and the browser profile shared across fetches); that `threats`
  excerpts are attacker-chosen text reaching the model; that the CDP endpoint is
  unauthenticated; and that auto-start pulls a mutable `:latest` tag and removes a stale
  `groundhog-browser` container. The `research` cap of 10 sources is documented, as is the
  fact that a failed source carries `provenance: null`.

## [0.9.0] - 2026-07-26

### Added

- `research(query, max_sources=5, max_tokens=None)` MCP tool: searches, reads the top
  sources through the stealth browser, and returns passages ranked across **all** of them in
  one BM25 pass, so a passage from the last source competes fairly with one from the first.
  Returns `passages` (text, source_url, heading, score) and `sources` (url, title, status,
  threats, provenance). At most one page per registrable domain. Passages are extracts, not
  summaries — no model, no API key.
- A source that fails is reported in `sources` with a `blocked`/`timeout`/`error` status
  instead of failing the whole call, and the fan-out is bounded so one slow page cannot cost
  the caller the others.

- `search(query, limit=10)` MCP tool: ranked hits (title, url, snippet, engine, score) for a
  query, so an agent can find pages and then `read_url` the ones it wants. Backend is chosen
  automatically — your own SearXNG instance when `SEARXNG_URL` is set (it needs
  `formats: [html, json]`, off by default upstream), otherwise a search page rendered
  through the stealth browser, so search works with no extra infrastructure.
  `GROUNDHOG_SEARCH_BACKEND` forces one.
- Hit titles and snippets are stripped of invisible characters like page content is: a
  poisoned result controls how it describes itself, and must not be a smuggling channel
  into the model.
- A search backend that is unreachable, has JSON disabled, or whose upstream engines are all
  rate-limited now raises an actionable error instead of reporting an empty web. The same
  applies when the rendered SERP's layout no longer matches — a stale selector is reported,
  not silently returned as "no results".

## [0.8.0] - 2026-07-25

### Added

- Public "try it" playground (`demo/`): paste a URL, see the safe markdown, provenance and
  stripped threats. Runs behind Caddy with per-visitor and global rate limits, as a
  non-root container. Not yet publicly deployed.

### Fixed

- Page titles and provenance metadata (author, dates, canonical) are now stripped of
  invisible characters and length-capped like page content is. They come from the page's own
  markup, so on a URL chosen by a search engine they are attacker-authored.
- Requests are grouped by bare host when a URL has no public suffix (IP addresses,
  `localhost`, unknown TLDs). They previously fell back to the full URL, which gave every
  path its own bucket and effectively disabled per-domain rate limiting for those hosts.
- A non-positive `max_tokens` is no longer honoured: it reached truncation and produced a
  meaningless fragment. Requires `tldextract>=5.3`.
- The MCP server now reconnects when the browser drops the CDP websocket (container
  replaced, Docker restarted). Previously the long-lived server kept the dead
  connection forever: `status` reported the endpoint healthy while every `read_url`
  failed with "no close frame received or sent" until the server was restarted.
- A `CDP_URL` naming the browser by DNS host (`http://chrome:9222` in Compose, a Kubernetes
  service, a remote machine) now works: Chrome rejects DevTools requests whose Host header
  is a non-localhost DNS name, so the endpoint is dialled by resolved IP.

### Changed

- `read_url`'s `format` argument is now typed `Literal["markdown", "text"]` and validated at
  the MCP boundary by the tool schema, so MCP clients get a clearer error. Note for callers
  importing `read_url` directly as a library: an unrecognised format no longer raises
  `ValueError`, it falls back to markdown. Pass one of the two documented values.

## [0.7.0] - 2026-07-15

### Fixed

- SPA pages no longer return the pre-render shell: after DOMContentLoaded, `fetch` now
  waits until the network is quiet and the DOM has stopped changing for 1s (capped at 8s)
  before extracting, tracking in-flight requests via CDP `Network` events (never
  Runtime/Console, so the stealth posture is unchanged).

### Added

- linux/arm64 image: Google ships no arm64 Chrome, so arm64 builds install Debian
  Chromium behind the same `/usr/local/bin/chrome` entrypoint — Apple Silicon hosts run
  the browser natively instead of crashing under amd64 emulation on heavy pages. Stealth
  conformance remains validated on the amd64 real-Chrome image. The publish workflow now
  builds both platforms, and the MCP no longer forces `--platform linux/amd64` when
  auto-starting the container.

## [0.6.2] - 2026-07-07

### Fixed

- The SSRF guard now re-validates the target URL immediately before `Page.navigate`, not
  just before the rate-limiter/concurrency-queue wait, narrowing the DNS-rebinding TOCTOU
  window a stalled queue could otherwise leave open.

## [0.6.1] - 2026-07-06

### Fixed

- The scheduled Conformance workflow's `publish` job pushed directly to `main`, which now
  fails under the repo's required-status-check branch protection. It opens a PR and merges
  once checks pass instead, going through the same gate as any other change.
- Bumped several GitHub Actions to their latest release (all now declare the `node24`
  runtime), clearing a Node.js 20 deprecation warning on every run.
- `iphey` moved from pass/fail to informational in the conformance harness: its one
  recurring flag (Location) is driven by IP hosting-reputation, not fingerprint quality —
  confirmed via `ip-api.com` showing `hosting: true` on both a proxy exit and the CI
  runner's own IP. Documented in the README.

## [0.6.0] - 2026-07-06

### Added

- Hidden-instruction detection hardening: catches the sub-pixel box used by
  `.sr-only`/`.visually-hidden` accessibility utility classes (also usable to hide prompt
  injection, since it reads as an ordinary accessibility class), the legacy `clip: rect(...)`
  hiding technique, text-color transparency and color-matching the background (near-1:1
  contrast — "white text on white background"), and elements positioned entirely outside the
  rendered page (`left: -9999px` and similar). Non-trivial HTML comments are now reported in
  `threats` too (diagnostic only — they never reached extracted content either way).

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
