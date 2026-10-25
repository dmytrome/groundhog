from typing import Annotated, TypedDict

from pydantic import Field

from .. import classify, config, document, engine, extract, provenance, retrieval, safety, sanitize
from ..config import load_config


class ReadResult(TypedDict):
    markdown: str
    title: str
    url: str
    final_url: str
    fetched_at: str
    status: classify.RetrievalStatus
    http_status: int | None
    truncated: bool
    threats: list[sanitize.Threat]
    matches: list[retrieval.Match]
    set_aside: int
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
                "Token budget for the content. Omit it, or pass a value below 1, to use the "
                "server's GROUNDHOG_MAX_TOKENS (20000 by default)."
            )
        ),
    ] = None,
    query: Annotated[
        str | None,
        Field(
            description=(
                "When set, the content is built from the passages most relevant to it, and "
                "`matches` lists each one's heading, offset and score for citation; it carries "
                "no text. If nothing matches, `matches` is empty and the whole page is returned; "
                "if something matches but none of it fits `max_tokens` (a large code block), "
                "`markdown` is empty and `truncated` is true, so raise `max_tokens`. "
                "On a long page the relevant passages can still be most of it, so set "
                "`max_tokens` to bound the read."
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
        raise
    except Exception as exc:
        raise safety.CallerFacingError(safety.safe_detail(exc)) from exc
    limit = config.token_budget(max_tokens, load_config().max_tokens)

    matches: list[retrieval.Match] = []
    body, truncated, set_aside = doc.markdown, False, 0
    if query and query.strip():
        selected, selected_matches, selected_truncated, selected_set_aside = retrieval.select(
            doc.markdown,
            query,
            limit,
            source_url=doc.final_url,
            content_hash=doc.provenance["content_hash"],
        )
        if selected_matches or selected_truncated:
            body, matches, truncated = selected, selected_matches, selected_truncated
            set_aside = selected_set_aside
    if matches:
        body += extract.TRUNCATION_MARKER if truncated else ""
    else:
        body, over_budget = extract.truncate(body, limit)
        truncated = truncated or over_budget

    return {
        "markdown": body,
        "title": doc.title,
        "url": doc.url,
        "final_url": doc.final_url,
        "fetched_at": doc.fetched_at,
        "status": doc.status,
        "http_status": doc.http_status,
        "truncated": truncated,
        "threats": doc.threats,
        "matches": matches,
        "set_aside": set_aside,
        "provenance": doc.provenance,
    }
