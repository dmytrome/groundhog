from .. import safety, sanitize
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


def _sanitized(hit: SearchHit) -> SearchHit | None:
    """Sanitize and cap every engine-supplied string, or drop the hit.

    A poisoned page controls how it is described, so every field of a hit is
    attacker-influenceable — including `published`, which an engine echoes from
    the page, and `url`, which the DuckDuckGo path percent-decodes out of a
    redirect wrapper. A hit must not be a smuggling channel into the model, nor
    an unbounded one.

    The URL is the one field a model treats as a citation, so it is never
    rewritten: if cleaning would change it, the hit is dropped instead of
    returning a link that no longer points where the engine said.

    The drop is silent, unlike the truncation `document._capped` discloses. The
    difference is what the caller would conclude: a threat list that quietly ends
    reads as "that was everything hidden on the page", while search results are
    already an arbitrary slice of the web and carry no completeness claim.
    """
    # One backend enforced the scheme itself and the other did not, so a
    # `javascript:`, `file:` or credential-bearing result reached the model as a
    # citation. `safe_url` is the single rule every model-facing URL passes.
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
    # Filter first, then slice: dropping a poisoned hit must not cost the caller a
    # result slot that a clean hit further down the list would have filled.
    clean = [cleaned for hit in hits if (cleaned := _sanitized(hit))]
    return clean[:limit], backend
