import asyncio
from typing import Literal, TypedDict

from .. import (
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
_MAX_ERROR_CHARS = 200
_BLOCKED_MESSAGE = "blocked by SSRF policy"

SourceStatus = Literal["ok", "blocked", "timeout", "error"]


class Passage(TypedDict):
    text: str
    source_url: str
    heading: str | None
    score: float


class Source(TypedDict):
    url: str
    title: str
    status: SourceStatus
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
        return "blocked", _BLOCKED_MESSAGE  # the host is already in Source.url
    if isinstance(exc, TimeoutError):
        return "timeout", "fetch timed out"
    detail, _ = sanitize.strip_invisible(f"{type(exc).__name__}: {exc}")
    return "error", detail.strip()[:_MAX_ERROR_CHARS]


async def _fetch_all(urls: list[str]) -> list[document.Document | BaseException]:
    """Fetch every URL concurrently, bounded overall.

    Page loads can otherwise outlast a client's MCP timeout. Whatever finished is
    kept; the rest are cancelled so one slow source cannot cost the caller the
    others.
    """
    tasks = [asyncio.create_task(document.fetch_document(url)) for url in urls]
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
    query: str,
    max_sources: int = _DEFAULT_MAX_SOURCES,
    max_tokens: int | None = None,
) -> ResearchResult:
    """Search the web and return ranked passages drawn from several sources.

    One call does what `search` + repeated `read_url` would: finds pages, reads
    them through the stealth browser, and returns the passages most relevant to
    `query` — each attributed to its source, with that source's provenance
    receipt and any stripped injection payloads. A source that fails is reported
    in `sources` rather than failing the whole call.
    """
    if not query.strip():
        raise ValueError("query must not be empty")
    cfg = load_config()
    limit = config.token_budget(max_tokens, cfg.max_tokens)
    capped = max(1, min(max_sources, _MAX_SOURCES))

    hits, backend = await search_backend.search(query, cfg, capped * _OVERFETCH)
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
                "threats": outcome.threats,
                "provenance": outcome.provenance,
                "error": None,
            }
        )
        # `final_url` comes from an in-page eval and could in principle be empty;
        # the requested URL is always a valid citation, so every chunk gets one.
        chunks.extend(
            retrieval.chunk_document(outcome.markdown, source=outcome.final_url or hit["url"])
        )

    # One BM25 pass over every passage from every source, so a passage from the
    # last source is directly comparable to one from the first.
    ranked, truncated = retrieval.rank(chunks, query, limit)
    passages: list[Passage] = []
    for scored in ranked:
        source_url = scored.chunk.source
        if not source_url:
            # Unattributable: never hand the model a passage it would present as
            # grounded without a citation. Cannot happen by construction below.
            continue
        # rank() admits its top passage unconditionally, so a single huge block
        # can still exceed the budget — clamp it and keep `truncated` honest.
        text, over_budget = extract.truncate(scored.chunk.text, limit)
        truncated = truncated or over_budget
        passages.append(
            {
                "text": text,
                "source_url": source_url,
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
