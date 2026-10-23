from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import zip_longest
from typing import Literal

from . import classify, engine, extract, provenance, sanitize

Format = Literal["markdown", "text"]
_MAX_THREATS = 50
MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_MARKDOWN_BYTES = 2 * 1024 * 1024
_ATTRIBUTE_REASON_PREFIX = "attribute:"
_TEMPLATE_REASON = "template"
_LOW_SIGNAL_CARRIERS = ("hidden_attribute", "hidden_template")


@dataclass(frozen=True)
class Document:
    markdown: str
    title: str
    url: str
    final_url: str
    fetched_at: str
    status: classify.RetrievalStatus
    http_status: int | None
    threats: list[sanitize.Threat]
    provenance: provenance.Provenance
    hidden_found: int = 0
    characters_found: int = 0


def _carrier_type(reason: str) -> sanitize.ThreatType:
    if reason.startswith(_ATTRIBUTE_REASON_PREFIX):
        return "hidden_attribute"
    if reason == _TEMPLATE_REASON:
        return "hidden_template"
    return "hidden_css"


def _hidden_threats(spans: list[engine.HiddenSpan]) -> list[sanitize.Threat]:
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
    best: dict[str, tuple[sanitize.ThreatType, int]] = {}
    for scan in scans:
        for ch, (category, n) in scan.items():
            if ch not in best or n > best[ch][1]:
                best[ch] = (category, n)
    return best


def _folded(threats: list[sanitize.Threat]) -> list[sanitize.Threat]:
    first: dict[tuple[str, str], sanitize.Threat] = {}
    out: list[sanitize.Threat] = []
    for threat in threats:
        key = (threat["reason"], threat["excerpt"])
        already = first.get(key)
        if already is None:
            first[key] = threat
            out.append(threat)
            continue
        already["seen"] = already.get("seen", 1) + 1
        if already["location"] != threat["location"]:
            already["location"] = None
    return out


def _ranked(hidden: list[sanitize.Threat], *, trusted: bool, limit: int) -> list[sanitize.Threat]:
    if not trusted:
        return hidden
    low = [t for t in hidden if t["type"] in _LOW_SIGNAL_CARRIERS]
    high = [t for t in hidden if t["type"] not in _LOW_SIGNAL_CARRIERS]
    return _collection_order_then_turns(high, limit) + _interleaved_by_reason(low)


def _collection_order_then_turns(
    threats: list[sanitize.Threat], budget: int
) -> list[sanitize.Threat]:
    head = budget // 2
    return threats[:head] + _interleaved_by_reason(threats[head:])


def _interleaved_by_reason(threats: list[sanitize.Threat]) -> list[sanitize.Threat]:
    groups: dict[str, list[sanitize.Threat]] = {}
    for threat in threats:
        groups.setdefault(threat["reason"], []).append(threat)
    return [t for row in zip_longest(*groups.values()) for t in row if t is not None]


def _capped(
    char_threats: list[sanitize.Threat],
    hidden: list[sanitize.Threat],
    limit: int,
    already_dropped: int,
    *,
    trusted: bool,
) -> list[sanitize.Threat]:
    kept_char = char_threats[: min(len(char_threats), max(limit // 2, limit - len(hidden)))]
    budget = limit - len(kept_char)
    ranked = _ranked(hidden, trusted=trusted, limit=budget)
    kept_hidden = ranked[:budget]
    dropped = (
        (len(char_threats) - len(kept_char))
        + sum(t.get("seen", 1) for t in ranked[budget:])
        + already_dropped
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
    provider = await engine.get_provider()
    page = await provider.fetch(url, strip_hidden=not include_hidden)
    final_url = page.final_url or url

    too_large = len(page.html.encode()) > MAX_HTML_BYTES
    if format == "text" or too_large:
        markdown, meta = page.text, extract.ExtractMeta(None, None, None)
    else:
        markdown, meta = extract.to_document(page.html, final_url)
    too_large = too_large or len(markdown.encode()) > MAX_MARKDOWN_BYTES

    scans = [sanitize.counts(markdown)]
    if markdown is not page.text:
        scans.append(sanitize.counts(page.text))
    found = _merged(scans)
    char_threats = sanitize.threats(found)
    visible_text = sanitize.strip_invisible(page.text, scans[-1])
    text = page.text if include_hidden else visible_text
    if format == "text":
        markdown = text
    elif not include_hidden:
        markdown = sanitize.strip_invisible(markdown, scans[0])
    if format == "markdown" and not markdown.strip():
        markdown = extract.fenced(text)
    status = page.retrieval_status
    if status == "ok" and not visible_text.strip():
        status = "empty"
    if too_large:
        markdown = ""
        if status not in classify.NOT_CONTENT:
            status = "too_large"
    hidden = (
        _folded(_hidden_threats(page.hidden_spans))
        if page.isolated
        else _hidden_threats(page.hidden_spans)
    )
    threats = _capped(
        char_threats,
        hidden,
        max_threats,
        page.spans_dropped,
        trusted=page.isolated,
    )
    if not page.isolated:
        threats.append(
            sanitize.notice(
                "detection_degraded",
                "hidden-text detection ran in the page's own JavaScript world",
            )
        )
    if page.strip_incomplete:
        threats.append(
            sanitize.notice(
                "strip_incomplete",
                "the rendered text was rebuilt from markup rather than read from layout",
            )
        )
    if not page.final_url:
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
        status=status,
        http_status=page.http_status,
        threats=threats,
        provenance=provenance.build(markdown, meta, page.meta),
        hidden_found=sum(t.get("seen", 1) for t in hidden) + page.spans_dropped,
        characters_found=len(char_threats),
    )
