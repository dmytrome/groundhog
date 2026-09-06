import asyncio
from typing import Annotated, Literal, TypedDict

from pydantic import Field

from .. import (
    classify,
    config,
    document,
    engine,
    extract,
    provenance,
    retrieval,
    safety,
    sanitize,
    search as search_backend,
)
from ..config import load_config
from ..search import SearchHit

_DEFAULT_MAX_SOURCES = 5
_MAX_SOURCES = 10
# One page per registrable domain: source diversity, and it sidesteps the
# per-domain rate limiter, which would otherwise serialise same-domain fetches.
_OVERFETCH = 3  # headroom for the same-domain hits that dedupe discards
_DEADLINE_S = 90.0
_CANCEL_DRAIN_S = 5.0
# Per source, not per page: the fan-out multiplies the report by `max_sources`,
# and `threats` sits outside the caller's token budget.
_MAX_THREATS_PER_SOURCE = 10

SourceStatus = Literal["ok", "blocked", "timeout", "error"]


class Passage(TypedDict):
    text: str
    source_url: str
    heading: str | None
    score: float


class Source(TypedDict):
    url: str
    title: str
    # Whether the fetch itself succeeded, failed the SSRF guard, timed out, or errored.
    status: SourceStatus
    # What that fetch returned when it succeeded (a challenge page, a non-text body, …),
    # per `classify`; None when the fetch never produced a page.
    page_status: classify.RetrievalStatus | None
    threats: list[sanitize.Threat]
    provenance: provenance.Provenance | None
    error: str | None


class ResearchResult(TypedDict):
    query: str
    backend: str
    passages: list[Passage]
    sources: list[Source]
    truncated: bool


def _diverse(hits: list[SearchHit], max_sources: int) -> list[SearchHit]:
    seen: set[str] = set()
    picked: list[SearchHit] = []
    for hit in hits:
        domain = engine.registrable_domain(hit["url"])
        if domain in seen:
            continue
        seen.add(domain)
        picked.append(hit)
        if len(picked) == max_sources:
            break
    return picked


def _status_of(exc: BaseException) -> tuple[SourceStatus, str]:
    """Map a failed fetch to a status and a message that is safe to return.

    The message reaches the model, and on a search-chosen URL the exception text
    can be attacker-authored (a page can make an eval throw with a payload of its
    choosing) or carry our own internal addresses. So: never echo a blocked
    address, and strip/cap everything else.
    """
    if isinstance(exc, safety.BlockedURLError):
        status: SourceStatus = "blocked"
    elif isinstance(exc, TimeoutError):
        status = "timeout"
    else:
        status = "error"
    return status, safety.safe_detail(exc)


async def _fetch_all(urls: list[str]) -> list[document.Document | BaseException]:
    """Fetch every URL concurrently, bounded overall.

    Page loads can otherwise outlast a client's MCP timeout. Whatever finished is
    kept; the rest are cancelled so one slow source cannot cost the caller the
    others.
    """
    tasks = [
        asyncio.create_task(document.fetch_document(url, max_threats=_MAX_THREATS_PER_SOURCE))
        for url in urls
    ]
    _, pending = await asyncio.wait(tasks, timeout=_DEADLINE_S)
    for task in pending:
        task.cancel()
    if pending:
        # Bounded too: a dead websocket can stall a cancelled fetch's teardown.
        await asyncio.wait(pending, timeout=_CANCEL_DRAIN_S)
    return [
        TimeoutError() if task.cancelled() else (task.exception() or task.result())
        for task in tasks
    ]


async def research(
    query: Annotated[
        str,
        Field(description="The question to research. Passages are ranked against it."),
    ],
    max_sources: Annotated[
        int,
        Field(
            description=(
                f"How many pages to read. Values outside 1-{_MAX_SOURCES} are clamped "
                "rather than rejected. Each source is a full page fetch, so this is the "
                "main cost and latency control."
            )
        ),
    ] = _DEFAULT_MAX_SOURCES,
    max_tokens: Annotated[
        int | None,
        Field(
            description=(
                "Token budget for the returned passages. Omit to use the server's "
                "GROUNDHOG_MAX_TOKENS (20000 by default). Must be positive."
            )
        ),
    ] = None,
) -> ResearchResult:
    """Search the web and return ranked passages drawn from several sources.

    One call does what `search` + repeated `read_url` would: finds pages, reads
    them through the stealth browser, and returns the passages most relevant to
    `query` — each attributed to its source, with that source's provenance
    receipt and any stripped injection payloads. A source that fails is reported
    in `sources` rather than failing the whole call.

    Prefer `read_url` when you already have the URL, and `search` when you only
    want links. This reads `max_sources` pages, rate limited per domain, so it is
    the slowest of the three and the one to avoid for a single known page.
    """
    if not query.strip():
        raise safety.InvalidArgument("query must not be empty")
    cfg = load_config()
    limit = config.token_budget(max_tokens, cfg.max_tokens)
    capped = max(1, min(max_sources, _MAX_SOURCES))

    try:
        hits, backend = await search_backend.search(query, cfg, capped * _OVERFETCH)
    except (search_backend.SearchUnavailableError, engine.BrowserUnavailableError):
        raise  # our own text, and the caller needs it to fix the backend
    except Exception as exc:
        # The same boundary `search` has: the SERP leg runs through the browser, so
        # its failures can name internal addresses or carry page-chosen text.
        raise safety.CallerFacingError(safety.safe_detail(exc)) from exc
    chosen = _diverse(hits, capped)
    outcomes = await _fetch_all([hit["url"] for hit in chosen]) if chosen else []

    sources: list[Source] = []
    chunks: list[retrieval.Chunk] = []
    for hit, outcome in zip(chosen, outcomes, strict=True):
        if isinstance(outcome, engine.BrowserUnavailableError):
            # Infrastructure, not a property of this source: every source would
            # carry the same failure, and the caller needs the remediation.
            raise outcome
        if isinstance(outcome, BaseException):
            status, detail = _status_of(outcome)
            sources.append(
                {
                    "url": hit["url"],
                    "title": hit["title"],
                    "status": status,
                    "page_status": None,
                    "threats": [],
                    "provenance": None,
                    "error": detail,
                }
            )
            continue
        sources.append(
            {
                "url": outcome.final_url,
                "title": outcome.title,
                "status": "ok",
                "page_status": outcome.status,
                "threats": outcome.threats,
                "provenance": outcome.provenance,
                "error": None,
            }
        )
        if outcome.status in classify.NOT_CONTENT:
            # A challenge, a 404 or a non-HTML body is not this source's content, and
            # ranking it would let "Checking your browser before accessing…" compete
            # for the caller's token budget against real passages. The source still
            # appears above, carrying the `page_status` that says why it contributed none.
            continue
        chunks.extend(retrieval.chunk_document(outcome.markdown, source=outcome.final_url))

    # One BM25 pass over every passage from every source, so a passage from the
    # last source is directly comparable to one from the first.
    ranked, truncated = retrieval.rank(chunks, query, limit)
    passages: list[Passage] = []
    for scored in ranked:
        if not scored.chunk.source:
            # Never hand the model a passage it would present as grounded without a
            # citation. Unreachable today; kept so the invariant fails closed.
            continue
        # rank() admits its top passage unconditionally, so a single huge block
        # can still exceed the budget — clamp it and keep `truncated` honest.
        text, over_budget = extract.truncate(scored.chunk.text, limit)
        truncated = truncated or over_budget
        passages.append(
            {
                "text": text,
                "source_url": scored.chunk.source,
                "heading": scored.chunk.heading,
                "score": round(scored.score, retrieval.SCORE_DIGITS),
            }
        )
    return {
        "query": query,
        "backend": backend,
        "passages": passages,
        "sources": sources,
        "truncated": truncated,
    }
