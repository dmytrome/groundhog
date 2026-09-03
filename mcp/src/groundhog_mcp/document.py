from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from . import classify, engine, extract, provenance, sanitize

Format = Literal["markdown", "text"]
_MAX_THREATS = 50
_ATTRIBUTE_REASON_PREFIX = "attribute:"
_TEMPLATE_REASON = "template"
_LOW_SIGNAL_CARRIERS = ("hidden_attribute", "hidden_template")


@dataclass(frozen=True)
class Document:
    """One fetched page, sanitized and attributed — before any ranking or budgeting."""

    markdown: str
    title: str
    url: str
    final_url: str
    fetched_at: str
    status: classify.RetrievalStatus
    http_status: int | None
    threats: list[sanitize.Threat]
    provenance: provenance.Provenance


def _carrier_type(reason: str) -> sanitize.ThreatType:
    """How the payload was carried, which is not always by stylesheet.

    A caller filtering on `type` to see how a page smuggled text would otherwise be told
    an `alt` attribute, or inert `<template>` markup, was hidden by CSS.
    """
    if reason.startswith(_ATTRIBUTE_REASON_PREFIX):
        return "hidden_attribute"
    if reason == _TEMPLATE_REASON:
        return "hidden_template"
    return "hidden_css"


def _hidden_threats(spans: list[engine.HiddenSpan]) -> list[sanitize.Threat]:
    """Shape hidden spans into threat records.

    The strings arrive sanitized and already cut to report size — `RenderedPage`
    cleans every page-authored field at the boundary — so this only re-labels them.
    """
    return [
        {
            "type": _carrier_type(span["reason"]),
            "reason": span["reason"],
            "location": span.get("path"),
            "excerpt": span["text"],
        }
        for span in spans
    ]


def _merged(
    scans: list[dict[str, tuple[sanitize.ThreatType, int]]],
) -> dict[str, tuple[sanitize.ThreatType, int]]:
    """Union character scans, keeping the highest count seen for each character.

    The same character appears in both the served text and the extracted string;
    summing would double-count it, and taking the lower would understate it.
    """
    best: dict[str, tuple[sanitize.ThreatType, int]] = {}
    for scan in scans:
        for ch, (category, n) in scan.items():
            if ch not in best or n > best[ch][1]:
                best[ch] = (category, n)
    return best


def _ranked(hidden: list[sanitize.Threat], *, trusted: bool) -> list[sanitize.Threat]:
    """Order hidden findings so the cap truncates the least informative first.

    Without this a gallery of described images, or a page of component templates, evicts
    the injection finding from a scope the collector reached later — findings are cut in
    the order they were produced.

    The rank is read from `reason`, which the collector supplies. In an isolated world
    that is our own code; without one the page can write it, and then this stops being a
    cosmetic label and starts choosing which findings reach the caller — a page could
    label its own `display:none` finding `attribute:alt` and have it dropped. So the
    ranking is applied only when the labels are ours. Untrusted, collection order stands:
    it is the page's own emission order, which buys an attacker nothing this does not.
    """
    if not trusted:
        return hidden
    return sorted(hidden, key=lambda t: t["type"] in _LOW_SIGNAL_CARRIERS)


def _capped(
    char_threats: list[sanitize.Threat],
    hidden: list[sanitize.Threat],
    limit: int,
    already_dropped: int,
) -> list[sanitize.Threat]:
    """Bound the reported threats, disclosing the drop.

    Silent truncation would read as "that was everything", which is the opposite
    of what this field is for. The notice carries its own `type` so it cannot be
    mistaken for — or forged as — a hidden-node finding.

    It rides inside the list rather than as a sibling flag so the disclosure
    travels with the data: `threats` is forwarded on its own into each `research`
    source, where a flag on the enclosing result would be left behind. The cost is
    that `len(threats)` counts the notice, which is why it has a distinct type.
    """
    # Each class gets its own share, so a page cannot bury the findings that carry
    # its injection excerpt by flooding the other class with decoys.
    # Each class is guaranteed half the budget; whatever the other does not use is
    # free. Below two slots one class must yield, and it is the hidden-node findings
    # that keep theirs — those carry the injection excerpt.
    kept_char = char_threats[: min(len(char_threats), max(limit // 2, limit - len(hidden)))]
    kept_hidden = hidden[: max(0, limit - len(kept_char))]
    dropped = (
        (len(char_threats) - len(kept_char)) + (len(hidden) - len(kept_hidden)) + already_dropped
    )
    kept = kept_char + kept_hidden
    if not dropped:
        return kept
    return kept + [
        sanitize.notice("report_truncated", f"{dropped} further threats not reported (cap {limit})")
    ]


async def fetch_document(
    url: str,
    *,
    format: Format = "markdown",
    include_hidden: bool = False,
    max_threats: int = _MAX_THREATS,
) -> Document:
    """Fetch one page and return it sanitized, with threats and provenance.

    Everything up to but excluding relevance ranking and token budgeting, which
    callers do differently. `max_threats` bounds the report: a caller fanning out
    over several pages pays this cost once per page, so it lowers it.
    """
    provider = await engine.get_provider()
    page = await provider.fetch(url, strip_hidden=not include_hidden)
    final_url = page.final_url or url

    if format == "text":
        markdown, meta = page.text, extract.ExtractMeta(None, None, None)
    else:
        markdown, meta = extract.to_document(page.html, final_url)
        if not markdown:
            markdown = page.text

    # Scan what the page served as well as what was extracted. The extractor drops
    # invisible characters on its way to Markdown, so scanning only its output
    # reports none of them; scanning only the rendered text misses anything that
    # exists solely in the extracted string (a link target, or a hidden node kept by
    # `include_hidden`). Neither source alone describes what the caller receives.
    scans = [sanitize.counts(markdown)]
    if markdown is not page.text:  # `format="text"` returns the served text itself
        scans.append(sanitize.counts(page.text))
    found = _merged(scans)
    char_threats = sanitize.threats(found)
    if not include_hidden:
        markdown = sanitize.strip_invisible(markdown, scans[0])
    threats = _capped(
        char_threats,
        _ranked(_hidden_threats(page.hidden_spans), trusted=page.isolated),
        max_threats,
        page.spans_dropped,
    )
    if not page.isolated:
        # The collector had to run in the page's own JavaScript world, where the page
        # can replace the DOM APIs it uses. Say so: a short `threats` list would
        # otherwise read as "little was hidden here".
        threats.append(
            sanitize.notice(
                "detection_degraded",
                "hidden-text detection ran in the page's own JavaScript world",
            )
        )
    if page.strip_incomplete:
        # Either half of the strip fell short — see `detect_js.py` for when. Disclosed
        # because the caller cannot otherwise tell a page with nothing hidden from one
        # whose hidden node the strip could not fully account for.
        threats.append(
            sanitize.notice(
                "strip_incomplete",
                "the rendered text was rebuilt from markup rather than read from layout",
            )
        )
    if not page.final_url:
        # Appended after the cap: a disclosure the cap could drop would be no
        # disclosure. Substituting the requested URL silently would tell the caller
        # no redirect happened.
        threats.append(
            sanitize.notice(
                "final_url_suppressed",
                "the page's final URL was unusable; the requested URL is reported",
            )
        )
    return Document(
        markdown=markdown,
        title=page.title,
        url=url,
        final_url=final_url,
        fetched_at=datetime.now(UTC).isoformat(),
        status=page.retrieval_status,
        http_status=page.http_status,
        threats=threats,
        provenance=provenance.build(markdown, meta, page.meta),
    )
