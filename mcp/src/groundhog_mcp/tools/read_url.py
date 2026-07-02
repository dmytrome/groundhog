from datetime import UTC, datetime
from typing import TypedDict

from .. import engine, extract, provenance, retrieval, sanitize

_FORMATS = ("markdown", "text")
_EXCERPT_CHARS = 80


class ReadResult(TypedDict):
    markdown: str
    title: str
    url: str
    final_url: str
    fetched_at: str
    truncated: bool
    threats: list[sanitize.Threat]
    matches: list[retrieval.Match]
    provenance: provenance.Provenance


def _hidden_threats(spans: list[dict]) -> list[sanitize.Threat]:
    return [
        {
            "type": "hidden_css",
            "reason": s["reason"],
            "location": s.get("path"),
            "excerpt": s["text"][:_EXCERPT_CHARS],
        }
        for s in spans
    ]


async def read_url(
    url: str,
    format: str = "markdown",
    max_tokens: int | None = None,
    query: str | None = None,
    include_hidden: bool = False,
) -> ReadResult:
    """Fetch a web page through the stealth browser and return clean, grounded
    content with provenance. Hidden text injected for models but invisible to
    humans is stripped by default and reported in `threats`. Pass `query` to get
    only the passages relevant to it (with `matches` provenance) instead of the
    whole page. `format` may be "markdown" (default) or "text"; set
    `include_hidden=true` to keep hidden text. Use this to ground answers in live
    web content, including sites that block plain fetchers."""
    if format not in _FORMATS:
        raise ValueError(f"format must be one of {_FORMATS}, got {format!r}")
    cfg = engine.load_config()
    provider = await engine.get_provider()
    page = await provider.fetch(url, strip_hidden=not include_hidden)
    limit = max_tokens or cfg.max_tokens

    if format == "text":
        markdown, meta = page.text, extract.ExtractMeta(None, None, None)
    else:
        markdown, meta = extract.to_document(page.html, page.final_url)
        if not markdown:
            markdown = page.text

    markdown, char_threats = sanitize.strip_invisible(markdown, strip=not include_hidden)
    threats = _hidden_threats(page.hidden_spans) + char_threats
    prov = provenance.build(markdown, meta, page.meta)

    if query and query.strip():
        body, matches, truncated = retrieval.select(markdown, query, limit)
        if not matches:
            body, truncated = extract.truncate(markdown, limit)
    else:
        body, truncated = extract.truncate(markdown, limit)
        matches = []

    return {
        "markdown": body,
        "title": page.title,
        "url": url,
        "final_url": page.final_url,
        "fetched_at": datetime.now(UTC).isoformat(),
        "truncated": truncated,
        "threats": threats,
        "matches": matches,
        "provenance": prov,
    }
