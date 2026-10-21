from .. import safety, sanitize
from ..config import Config, SearchBackend
from . import searxng, serp
from .types import SearchHit, SearchUnavailableError

__all__ = ["SearchHit", "SearchUnavailableError", "resolve_backend", "search"]


def resolve_backend(cfg: Config) -> SearchBackend:
    if cfg.search_backend == "searxng" and not cfg.searxng_url:
        raise SearchUnavailableError(
            "GROUNDHOG_SEARCH_BACKEND=searxng needs SEARXNG_URL pointing at an instance "
            "with `formats: [html, json]` enabled in its settings.yml."
        )
    if cfg.search_backend != "auto":
        return cfg.search_backend
    return "searxng" if cfg.searxng_url else "serp"


def _sanitized(hit: SearchHit) -> SearchHit | None:
    url = safety.safe_url(hit["url"])
    if url is None:
        return None
    return {
        **hit,
        "url": url,
        "title": sanitize.clean_field(hit["title"], sanitize.MAX_TITLE_CHARS) or "",
        "snippet": sanitize.clean_field(hit["snippet"], sanitize.MAX_SNIPPET_CHARS) or "",
        "engine": sanitize.clean_field(hit["engine"], sanitize.MAX_ENGINE_CHARS) or "unknown",
        "published": sanitize.clean_field(hit["published"], sanitize.MAX_PUBLISHED_CHARS),
    }


async def search(query: str, cfg: Config, limit: int) -> tuple[list[SearchHit], SearchBackend]:
    backend = resolve_backend(cfg)
    if backend == "searxng":
        hits = await searxng.search(str(cfg.searxng_url), query)
    else:
        hits = await serp.search(query)
    clean = [cleaned for hit in hits if (cleaned := _sanitized(hit))]
    return clean[:limit], backend
