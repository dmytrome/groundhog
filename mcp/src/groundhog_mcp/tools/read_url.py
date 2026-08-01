from typing import Annotated, TypedDict

from pydantic import Field

from .. import config, document, engine, extract, provenance, retrieval, safety, sanitize


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
    url: Annotated[
        str, Field(description="Absolute http(s) URL. Private and loopback addresses are refused.")
    ],
    format: Annotated[
        document.Format,
        Field(
            description=(
                "'markdown' extracts the article; 'text' returns the page's rendered text."
            )
        ),
    ] = "markdown",
    max_tokens: Annotated[
        int | None,
        Field(
            description=(
                "Token budget for the content. Omit to use the server's "
                "GROUNDHOG_MAX_TOKENS (20000 by default). Must be positive."
            )
        ),
    ] = None,
    query: Annotated[
        str | None,
        Field(
            description=(
                "When set, `matches` carries the passages most relevant to it, each with "
                "its heading and offset for citation."
            )
        ),
    ] = None,
    include_hidden: Annotated[
        bool,
        Field(
            description=(
                "Keep text that is invisible to a human reader. It is reported in "
                "`threats` either way; this only controls whether it stays in the content."
            )
        ),
    ] = False,
) -> ReadResult:
    """Fetch one web page through the stealth browser and return clean, grounded
    content with provenance.

    Hidden text injected for models but invisible to humans is stripped by default
    and reported in `threats`. Use this to ground answers in live web content,
    including sites that block plain fetchers.

    Reads a URL you already have: use `search` to find URLs, or `research` to
    search and read in one call. Fetches are rate limited per domain (5s apart by
    default), so several pages from one site are not instant."""
    try:
        doc = await document.fetch_document(url, format=format, include_hidden=include_hidden)
    except engine.BrowserUnavailableError:
        raise  # our own text, and the caller needs the remediation steps verbatim
    except Exception as exc:
        # Without this the SSRF guard's "blocked address: host -> 169.254.x.x" and any
        # page-chosen exception text reach the model unchanged. `research` already
        # refuses to echo those; this is the same rule for the single-page tool.
        raise RuntimeError(safety.safe_detail(exc)) from exc
    limit = config.token_budget(max_tokens, engine.load_config().max_tokens)

    matches: list[retrieval.Match] = []
    body, truncated = doc.markdown, False
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
