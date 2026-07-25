from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from . import engine, extract, provenance, sanitize

Format = Literal["markdown", "text"]
_EXCERPT_CHARS = 80


@dataclass
class Document:
    """One fetched page, sanitized and attributed — before any ranking or budgeting."""

    markdown: str
    title: str
    url: str
    final_url: str
    fetched_at: str
    threats: list[sanitize.Threat]
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


async def fetch_document(
    url: str, *, format: Format = "markdown", include_hidden: bool = False
) -> Document:
    """Fetch one page and return it sanitized, with threats and provenance.

    Everything up to but excluding relevance ranking and token budgeting, which
    callers do differently.
    """
    provider = await engine.get_provider()
    page = await provider.fetch(url, strip_hidden=not include_hidden)

    if format == "text":
        markdown, meta = page.text, extract.ExtractMeta(None, None, None)
    else:
        markdown, meta = extract.to_document(page.html, page.final_url)
        if not markdown:
            markdown = page.text

    markdown, char_threats = sanitize.strip_invisible(markdown, strip=not include_hidden)
    return Document(
        markdown=markdown,
        title=page.title,
        url=url,
        final_url=page.final_url,
        fetched_at=datetime.now(UTC).isoformat(),
        threats=_hidden_threats(page.hidden_spans) + char_threats,
        provenance=provenance.build(markdown, meta, page.meta),
    )
