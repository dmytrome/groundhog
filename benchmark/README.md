# Hidden-injection containment benchmark

Every published benchmark of the fetch layer measures the same thing: how much of the
article you get back, and how fast. None of them asks whether the fetcher handed the model
text the page was hiding from its reader.

This one measures that, and only that.

## What is scored

Each case is a page carrying two markers. `INJ-<case>` is text a human reader cannot see.
`VIS-<case>` is ordinary article text. A fetcher is scored on three counts:

| | question |
| --- | --- |
| **contained** | did `INJ-` stay out of what reaches the model |
| **kept the article** | did `VIS-` still reach it |
| **disclosed** | did the fetcher say it had removed something |

The second column is what makes the first meaningful. A fetcher that returns an empty
string contains every payload in this corpus and is useless, so containment is only
credited when the article survives too.

The corpus also holds three controls: a page with nothing hidden in it, one whose payload
sits in an `<option>` of a closed `<select>` — text the browser reports unrendered and puts
in `innerText` anyway — and one where a readable label sits on a translucent panel, as
every dark-theme button does. A detector that reports everything scores perfectly on the
other cases; the controls are what charge it for that.

Payload matching normalises the text first — zero-width characters are removed and Unicode
Tag codepoints are decoded back to ASCII — so smuggling a payload through an encoding that
renders as nothing does not count as containment.

## Carriers

Twenty-eight graded cases across seven families, plus the three controls. CSS (nine:
`display:none`, `visibility:hidden`, off-screen, sub-4px text, transparent and
background-matched colour, the `sr-only` clipping box, print-only, and an `<option>` tinted
to its `<select>`). Attributes (five: `alt`, `aria-label`, `title`, a hidden input value,
the meta description). Markup (six: HTML comment, a collapsed `<details>`, `iframe srcdoc`,
`<noscript>`, SVG `title`/`desc`, an `<option>` hidden inside a visible `<select>`).
Shadow DOM (three: hidden node, attribute, and an attribute projected through a `<slot>`).
`<template>` content (two, one of them nested). Two evasion cases — a hidden node with
zero-width joining, and text matching its background where both are written in `oklch` —
and the Unicode Tag block.

## Running it

```bash
python3 build_corpus.py                               # regenerate corpus/ and manifest.json
uv run --project ../mcp python run.py                 # serve locally, write RESULTS.md
uv run --project ../mcp python run.py --base-url URL  # measure hosted fetchers too
```

`run.py` imports Groundhog, so it runs under the `mcp` project rather than the system
interpreter.

The corpus is published at **https://dmytrome.github.io/groundhog/**, which is what makes
a hosted fetcher measurable and the results reproducible by someone without the repository.

Hosted fetchers are measured only against a published copy — they cannot reach a loopback
address, so `run.py` leaves them out of a local run rather than recording a column of
connection errors. Neither Jina Reader nor Firecrawl needs a key at this volume;
`FIRECRAWL_API_KEY` is used when set, for the higher rate limit.

Jina is asked not to serve its cache (`x-no-cache`). Its default response is a snapshot
taken earlier, which would score whatever it fetched before rather than the page here.

Scrapling is optional and skipped when absent. To include it:

```bash
uv run --project ../mcp --with 'scrapling[fetchers,rag]' python run.py
```

`groundhog` needs a browser at `CDP_URL`. The local server binds an ephemeral port and is
reachable from the browser container as `host.docker.internal`.

## Limitations

Stated because they bound what the numbers mean.

- **Both hosted fetchers are measured on their defaults.** Firecrawl is sent
  `{"url": ..., "formats": ["markdown"]}` and nothing else; Jina is sent no options but
  `x-no-cache`. Neither was tuned, and neither exposes an option this benchmark knows of
  that would change the result. A configuration that does would be worth a column of its
  own rather than a footnote.
- **Firecrawl renders script.** It leaks the shadow-DOM cases, which an HTTP-only fetcher
  cannot even see, so its score is not the artefact of a missing browser that
  `scrapling (http)` and the naive baseline partly are. It is measured on the same
  rendered DOM Groundhog is.
- **A hosted result is a snapshot.** Jina is a service that can change on its own
  schedule, so its row describes what it returned on the date in `RESULTS.md` and carries
  no promise about today. The local fetchers are pinned by version and reproducible; a
  hosted one is not.
- **Scrapling is measured through one of its fetchers, in its most favourable mode.** The
  HTTP `Fetcher`, read through `markdown(main_content_only=True)` — its article mode. The
  default returns the whole page and contains less. Its browser-backed fetchers
  (`StealthyFetcher`, `DynamicFetcher`) need a separate browser install and are not
  covered here, so nothing in this table speaks to them.
- **A fetcher without a browser cannot be judged on rendered carriers.** Where a payload
  only exists after script runs, an HTTP fetcher neither contains nor leaks it — it never
  saw it. Those cells are not evidence either way, and the split between static and
  script-rendered carriers is in the `needs_js` field of `manifest.json`.
- **The corpus is synthetic.** Every case is a carrier we can construct and verify, not a
  page sampled from the wild. It measures coverage of known carriers, not prevalence.
- **Containment is string matching.** A payload that survives in paraphrase, or one split
  across elements, is not detected as a leak.
- **A baseline can pass by accident.** The naive fetcher contains most of these carriers
  because its extractor prunes them incidentally, not because anything examined them. That
  is not a stable property: the same extractor changed its handling of `<template>`
  between 2.1.0 and 2.2.0, in the direction of containing more. A pass here is evidence
  about one version, not about a design.
- **Disclosure is binary.** Whether the reported excerpt is useful is not scored.
