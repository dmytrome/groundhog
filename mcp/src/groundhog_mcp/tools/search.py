from typing import TypedDict

from .. import engine, safety, search as search_backend
from ..config import load_config
from ..search import SearchHit

_DEFAULT_LIMIT = 10
_MAX_LIMIT = 25


class SearchResult(TypedDict):
    query: str
    backend: str
    hits: list[SearchHit]


async def search(query: str, limit: int = _DEFAULT_LIMIT) -> SearchResult:
    """Search the web and return ranked hits (title, url, snippet, engine).

    Use this to find pages, then pass the URLs you want to `read_url` for safe,
    grounded content. Results come from a self-hosted SearXNG instance when
    `SEARXNG_URL` is set, otherwise from a search page rendered through the
    stealth browser. Hits are links only — nothing is fetched until you ask.
    """
    if not query.strip():
        raise ValueError("query must not be empty")
    capped = max(1, min(limit, _MAX_LIMIT))
    try:
        hits, backend = await search_backend.search(query, load_config(), capped)
    except (search_backend.SearchUnavailableError, engine.BrowserUnavailableError):
        raise  # our own text: it tells the operator how to fix the backend, verbatim
    except Exception as exc:
        # Same boundary the other two tools have: a third party's payload must not
        # decide the text, or the length, of what the caller's model reads.
        raise RuntimeError(safety.safe_detail(exc)) from exc
    return {"query": query, "backend": backend, "hits": hits}
