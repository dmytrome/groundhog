from typing import TypedDict

from .. import document, engine, extract, provenance, retrieval, sanitize


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


async def read_url(
    url: str,
    format: document.Format = "markdown",
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
    doc = await document.fetch_document(url, format=format, include_hidden=include_hidden)
    limit = max_tokens or engine.load_config().max_tokens

    body, matches, truncated = doc.markdown, [], False
    if query and query.strip():
        selected, selected_matches, selected_truncated = retrieval.select(
            doc.markdown, query, limit
        )
        if selected_matches:
            body, matches, truncated = selected, selected_matches, selected_truncated
    # select() admits the top passage unconditionally, so even a ranked body can
    # exceed the budget — one clamp covers both paths and keeps the flag honest.
    body, over_budget = extract.truncate(body, limit)
    truncated = truncated or over_budget

    return {
        "markdown": body,
        "title": doc.title,
        "url": doc.url,
        "final_url": doc.final_url,
        "fetched_at": doc.fetched_at,
        "truncated": truncated,
        "threats": doc.threats,
        "matches": matches,
        "provenance": doc.provenance,
    }
