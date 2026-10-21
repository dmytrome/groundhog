import asyncio
import re
from datetime import date
from typing import Annotated, Literal, TypedDict
from urllib.parse import urlsplit

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
_OVERFETCH = 3  # headroom for the same-domain hits that dedupe discards
_DEADLINE_S = 90.0
_STARTS_WITHIN_S = _DEADLINE_S / 3
_CANCEL_DRAIN_S = 5.0
# Per source, not per page: the fan-out multiplies the report by `max_sources`,
# and `threats` sits outside the caller's token budget.
_MAX_THREATS_PER_SOURCE = 10
_MAX_AIM_CHARS = 200
_MAX_DOMAINS = 50
_FILTERED_HITS = 50
_SITE_TERMS = 8
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$"
)

SourceStatus = Literal["ok", "blocked", "timeout", "error"]
Filter = Literal["excluded_domain", "outside_domains", "published_before", "duplicate"]


class Passage(TypedDict):
    id: str
    text: str
    source_url: str
    heading: str | None
    score: float
    start: int
    end: int
    clamped: bool
    fence: str | None


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
    filtered: Filter | None


class Receipt(TypedDict):
    executed_query: str
    aim: str | None
    include_domains: list[str]
    exclude_domains: list[str]
    published_after: str | None
    hits: int
    hits_by_domain: dict[str, int]
    hits_outside_domains: int
    hits_before_date: int
    hits_same_domain: int
    failed: int
    not_content: int
    filtered: int
    usable: int
    outside_domains: int
    unknown_dates: int | None
    omitted: int
    set_aside: int
    clamped: bool


class ResearchResult(TypedDict):
    query: str
    backend: str
    passages: list[Passage]
    sources: list[Source]
    truncated: bool
    receipt: Receipt


def _date(value: str | None) -> date | None:
    found = _DATE_RE.match(value or "")
    try:
        return date.fromisoformat(found.group()) if found else None
    except ValueError:
        return None


def _domain(value: str) -> str:
    try:
        domain = value.strip().lower().encode("idna").decode("ascii")
    except UnicodeError:
        domain = ""
    if not _DOMAIN_RE.match(domain):
        raise safety.InvalidArgument(f"not a domain name: {value[:80]!r}")
    return domain


def _domains(values: list[str] | None, name: str, most: int) -> list[str]:
    if values and len(values) > most:
        raise safety.InvalidArgument(f"{name} takes at most {most} domains")
    return list(dict.fromkeys(_domain(value) for value in values or []))


def _within(url: str, domains: list[str]) -> str | None:
    try:
        host = (urlsplit(url).hostname or "").rstrip(".").encode("idna").decode("ascii")
    except ValueError:
        return None
    found = [d for d in domains if host == d or host.endswith("." + d)]
    return max(found, key=len, default=None)


def _per_domain(min_delay_ms: int) -> int:
    if min_delay_ms <= 0:
        return _MAX_SOURCES
    return 1 + int(_STARTS_WITHIN_S * 1000 / min_delay_ms)


def _round_robin(
    hits: list[SearchHit], domains: list[str], max_sources: int, per_domain: int
) -> tuple[list[SearchHit], int]:
    queues: dict[str, list[SearchHit]] = {}
    taken: dict[str, int] = {}
    skipped = 0
    for hit in dict((hit["url"], hit) for hit in hits).values():
        domain = _within(hit["url"], domains)
        if not domain:
            continue
        registrable = engine.registrable_domain(hit["url"])
        if taken.get(registrable, 0) >= per_domain:
            skipped += 1
            continue
        taken[registrable] = taken.get(registrable, 0) + 1
        queues.setdefault(domain, []).append(hit)
    picked: list[SearchHit] = []
    while len(picked) < max_sources and any(queues.values()):
        for queue in queues.values():
            if queue and len(picked) < max_sources:
                picked.append(queue.pop(0))
    return picked, skipped


def _diverse(hits: list[SearchHit], max_sources: int) -> tuple[list[SearchHit], int]:
    seen: set[str] = set()
    picked: list[SearchHit] = []
    skipped = 0
    for hit in hits:
        domain = engine.registrable_domain(hit["url"])
        if domain in seen:
            skipped += 1
            continue
        seen.add(domain)
        picked.append(hit)
        if len(picked) == max_sources:
            break
    return picked, skipped


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


_abandoned: set[asyncio.Task[document.Document]] = set()


def _retire(task: asyncio.Task[document.Document]) -> None:
    _abandoned.discard(task)
    if not task.cancelled():
        task.exception()


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
    try:
        _, pending = await asyncio.wait(tasks, timeout=_DEADLINE_S)
        for task in pending:
            task.cancel()
        if pending:
            # Bounded too: a dead websocket can stall a cancelled fetch's teardown.
            await asyncio.wait(pending, timeout=_CANCEL_DRAIN_S)
    finally:
        for task in tasks:
            if task.done():
                _retire(task)
            else:
                task.cancel()
                _abandoned.add(task)
                task.add_done_callback(_retire)
    return [
        TimeoutError()
        if task.cancelled() or not task.done()
        else (task.exception() or task.result())
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
    include_domains: Annotated[
        list[str] | None,
        Field(
            description=(
                f"Read only pages on these domains or their subdomains (at most "
                f"{_SITE_TERMS}), e.g. a standards body or the vendor's docs. Several pages "
                "from one domain may be read."
            )
        ),
    ] = None,
    exclude_domains: Annotated[
        list[str] | None,
        Field(description=f"Never read pages on these domains (at most {_MAX_DOMAINS})."),
    ] = None,
    published_after: Annotated[
        str | None,
        Field(
            description=(
                "YYYY-MM-DD. Leave out pages dated before this day. A page with no date is "
                "kept and counted in the receipt's `unknown_dates`."
            )
        ),
    ] = None,
    aim: Annotated[
        str | None,
        Field(
            description=(
                f"What the evidence must show, in at most {_MAX_AIM_CHARS} characters: the "
                "authority, artifact and version sought. Echoed in the receipt; it does not "
                "change the search or the ranking."
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
    if aim is not None and len(aim) > _MAX_AIM_CHARS:
        raise safety.InvalidArgument(f"aim must be at most {_MAX_AIM_CHARS} characters")
    after = _date(published_after)
    if published_after is not None and (after is None or len(published_after) != 10):
        raise safety.InvalidArgument("published_after must be a date as YYYY-MM-DD")
    included = _domains(include_domains, "include_domains", _SITE_TERMS)
    excluded = _domains(exclude_domains, "exclude_domains", _MAX_DOMAINS)
    executed = query
    if included:
        executed += " (" + " OR ".join(f"site:{d}" for d in included) + ")"
    executed += "".join(f" -site:{d}" for d in excluded[: _SITE_TERMS - len(included)])
    cfg = load_config()
    limit = config.token_budget(max_tokens, cfg.max_tokens)
    capped = max(1, min(max_sources, _MAX_SOURCES))

    try:
        hits, backend = await search_backend.search(
            executed, cfg, _FILTERED_HITS if included or excluded or after else capped * _OVERFETCH
        )
    except (search_backend.SearchUnavailableError, engine.BrowserUnavailableError):
        raise  # our own text, and the caller needs it to fix the backend
    except Exception as exc:
        # The same boundary `search` has: the SERP leg runs through the browser, so
        # its failures can name internal addresses or carry page-chosen text.
        raise safety.CallerFacingError(safety.safe_detail(exc)) from exc
    hits_by_domain = {d: 0 for d in included}
    for hit in hits:
        if domain := _within(hit["url"], included):
            hits_by_domain[domain] += 1
    in_domains = [
        hit
        for hit in hits
        if not _within(hit["url"], excluded) and (not included or _within(hit["url"], included))
    ]
    kept = [
        hit
        for hit in in_domains
        if not (after and (dated := _date(hit["published"])) and dated < after)
    ]
    if included:
        chosen, same_domain = _round_robin(kept, included, capped, _per_domain(cfg.min_delay_ms))
    else:
        chosen, same_domain = _diverse(kept, capped)
    outcomes = await _fetch_all([hit["url"] for hit in chosen]) if chosen else []

    sources: list[Source] = []
    counts = {
        "failed": 0,
        "not_content": 0,
        "filtered": 0,
        "usable": 0,
        "outside": 0,
        "undated": 0,
    }
    chunks: list[retrieval.Chunk] = []
    hashes: dict[str, str] = {}
    seen: set[str] = set()
    for hit, outcome in zip(chosen, outcomes, strict=True):
        if isinstance(outcome, engine.BrowserUnavailableError):
            # Infrastructure, not a property of this source: every source would
            # carry the same failure, and the caller needs the remediation.
            raise outcome
        if isinstance(outcome, BaseException):
            status, detail = _status_of(outcome)
            counts["failed"] += 1
            sources.append(
                {
                    "url": hit["url"],
                    "title": hit["title"],
                    "status": status,
                    "page_status": None,
                    "threats": [],
                    "provenance": None,
                    "error": detail,
                    "filtered": None,
                }
            )
            continue
        dated = _date(outcome.provenance["published"]) or _date(hit["published"])
        outside = bool(included) and not _within(outcome.final_url, included)
        filtered: Filter | None = None
        if _within(outcome.final_url, excluded):
            filtered = "excluded_domain"
        elif outside:
            filtered = "outside_domains"
        elif after and dated and dated < after:
            filtered = "published_before"
        elif outcome.final_url in seen or outcome.provenance["content_hash"] in seen:
            filtered = "duplicate"
        seen.update((outcome.final_url, outcome.provenance["content_hash"]))
        sources.append(
            {
                "url": outcome.final_url,
                "title": outcome.title,
                "status": "ok",
                "page_status": outcome.status,
                "threats": outcome.threats,
                "provenance": outcome.provenance,
                "error": None,
                "filtered": filtered,
            }
        )
        if filtered == "duplicate":
            counts["filtered"] += 1
            continue
        if outcome.status in classify.NOT_CONTENT:
            # A challenge, a 404 or a non-HTML body is not this source's content, and
            # ranking it would let "Checking your browser before accessing…" compete
            # for the caller's token budget against real passages. The source still
            # appears above, carrying the `page_status` that says why it contributed none.
            counts["not_content"] += 1
            continue
        counts["outside"] += outside
        if filtered:
            counts["filtered"] += 1
            continue
        counts["usable"] += 1
        counts["undated"] += dated is None
        hashes[outcome.final_url] = outcome.provenance["content_hash"]
        chunks.extend(retrieval.chunk_document(outcome.markdown, source=outcome.final_url))

    # One BM25 pass over every passage from every source, so a passage from the
    # last source is directly comparable to one from the first.
    ranked, left_out, set_aside = retrieval.rank(
        chunks, query, limit, floor=retrieval.RELEVANCE_FLOOR
    )
    clamped = False
    passages: list[Passage] = []
    for scored in ranked:
        if not scored.chunk.source:
            # Never hand the model a passage it would present as grounded without a
            # citation. Unreachable today; kept so the invariant fails closed.
            continue
        # rank() admits its top passage unconditionally, so a single huge block
        # can still exceed the budget — clamp it and keep `truncated` honest.
        text, over_budget = extract.truncate(scored.chunk.text, limit)
        clamped = clamped or over_budget
        start = scored.chunk.offset
        end = start + len(scored.chunk.text)
        passages.append(
            {
                "id": retrieval.passage_id(
                    scored.chunk.source, hashes[scored.chunk.source], start, end
                ),
                "text": text,
                "source_url": scored.chunk.source,
                "heading": scored.chunk.heading,
                "score": round(scored.score, retrieval.SCORE_DIGITS),
                "start": start,
                "end": end,
                "clamped": over_budget,
                "fence": scored.chunk.fence,
            }
        )
    return {
        "query": query,
        "backend": backend,
        "passages": passages,
        "sources": sources,
        "truncated": left_out > 0 or clamped,
        "receipt": {
            "executed_query": executed,
            "aim": aim,
            "include_domains": included,
            "exclude_domains": excluded,
            "published_after": published_after,
            "hits": len(hits),
            "hits_by_domain": hits_by_domain,
            "hits_outside_domains": len(hits) - len(in_domains),
            "hits_before_date": len(in_domains) - len(kept),
            "hits_same_domain": same_domain,
            "failed": counts["failed"],
            "not_content": counts["not_content"],
            "filtered": counts["filtered"],
            "usable": counts["usable"],
            "outside_domains": counts["outside"],
            "unknown_dates": counts["undated"] if after else None,
            "omitted": left_out,
            "set_aside": set_aside,
            "clamped": clamped,
        },
    }
