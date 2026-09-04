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

The corpus also holds one page with nothing hidden in it. A detector that reports
everything scores perfectly on the other cases; the control is what charges it for that.

Payload matching normalises the text first — zero-width characters are removed and Unicode
Tag codepoints are decoded back to ASCII — so smuggling a payload through an encoding that
renders as nothing does not count as containment.

## Carriers

Nineteen cases across six families: CSS (`display:none`, `visibility:hidden`, off-screen,
sub-4px text, transparent and background-matched colour, the `sr-only` clipping box), HTML
comments, attributes (`alt`, `aria-label`, `title`), `<template>` content including a
nested one, shadow DOM (hidden node, attribute, and an attribute projected through a
`<slot>`), an evasion case combining a hidden node with zero-width joining, the Unicode
Tag block, and the clean control.

## Running it

```bash
python3 build_corpus.py          # regenerate corpus/ and manifest.json
python3 run.py                   # serve locally, run every adapter, write RESULTS.md
python3 run.py --base-url URL    # measure a hosted fetcher against a published copy
```

Scrapling is optional and skipped when absent. To include it:

```bash
uv run --project ../mcp --with 'scrapling[fetchers,rag]' python run.py
```

`groundhog` needs a browser at `CDP_URL`. The local server binds an ephemeral port and is
reachable from the browser container as `host.docker.internal`.

## Limitations

Stated because they bound what the numbers mean.

- **Three fetchers so far.** Hosted services need the corpus published at a public URL
  and, in most cases, an API key. `--base-url` exists for exactly that and has no other
  user yet.
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
