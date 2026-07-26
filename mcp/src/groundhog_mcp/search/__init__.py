from .. import sanitize
from ..config import Config, SearchBackend
from . import searxng, serp
from .types import SearchHit, SearchUnavailableError

__all__ = ["SearchHit", "SearchUnavailableError", "resolve_backend", "search"]


def resolve_backend(cfg: Config) -> SearchBackend:
    """Pick the concrete backend.

    `auto` prefers a configured SearXNG instance and otherwise renders a SERP
    through our own stealth browser, so search works with no extra infrastructure.
    """
    if cfg.search_backend == "searxng" and not cfg.searxng_url:
        raise SearchUnavailableError(
            "GROUNDHOG_SEARCH_BACKEND=searxng needs SEARXNG_URL pointing at an instance "
            "with `formats: [html, json]` enabled in its settings.yml."
        )
    if cfg.search_backend != "auto":
        return cfg.search_backend
    return "searxng" if cfg.searxng_url else "serp"


def _sanitized(hit: SearchHit) -> SearchHit:
    """Strip invisible characters from engine-supplied text.

    Titles and snippets are attacker-influenceable (a poisoned page controls how
    it is described), so they get the same zero-width/bidi/tag stripping as page
    content — a search hit must not be a smuggling channel into the model.
    """
    title, _ = sanitize.strip_invisible(hit["title"])
    snippet, _ = sanitize.strip_invisible(hit["snippet"])
    return {**hit, "title": title, "snippet": snippet}


async def search(query: str, cfg: Config, limit: int) -> tuple[list[SearchHit], SearchBackend]:
    backend = resolve_backend(cfg)
    if backend == "searxng":
        hits = await searxng.search(str(cfg.searxng_url), query)
    else:
        hits = await serp.search(query)
    return [_sanitized(hit) for hit in hits[:limit]], backend
