import json
import urllib.error
import urllib.parse

from .. import http
from .types import SearchHit, SearchUnavailableError

_FETCH_TIMEOUT_S = 20.0
_SETUP_HINT = (
    "Check SEARXNG_URL points at a reachable instance with `formats: [html, json]` "
    "enabled in its settings.yml (JSON is off by default upstream)."
)


def build_url(instance_url: str, query: str) -> str:
    query_string = urllib.parse.urlencode({"q": query, "format": "json"})
    return f"{instance_url.rstrip('/')}/search?{query_string}"


def parse(payload: dict[str, object]) -> list[SearchHit]:
    """Map a SearXNG JSON envelope onto hits.

    `answers` and `infoboxes` are separate top-level arrays and are ignored: only
    `results` carries fetchable pages. `url` is optional on SearXNG's result type,
    so entries without one are dropped rather than returned as dead links.
    """
    results = payload.get("results")
    if not isinstance(results, list):
        raise SearchUnavailableError(
            f"SearXNG response had no `results` array — not a search envelope. {_SETUP_HINT}"
        )
    hits: list[SearchHit] = []
    for result in results:
        url = result.get("url")
        if not url:
            continue
        hits.append(
            {
                "title": result.get("title", ""),
                "url": url,
                "snippet": result.get("content") or "",
                "engine": result.get("engine") or "",
                "score": float(result.get("score") or 0.0),
                "published": result.get("publishedDate"),
            }
        )
    if not hits:
        # A 200 with no results and dead engines is a broken backend, not an
        # empty web: SearXNG reports which engines failed and why.
        down = payload.get("unresponsive_engines") or []
        if down:
            detail = ", ".join(" ".join(str(part) for part in entry) for entry in down)
            raise SearchUnavailableError(f"every SearXNG engine failed: {detail}")
    return hits


async def search(instance_url: str, query: str) -> list[SearchHit]:
    try:
        payload = await http.read_json_async(build_url(instance_url, query), _FETCH_TIMEOUT_S)
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise SearchUnavailableError(f"SearXNG request failed ({exc}). {_SETUP_HINT}") from exc
    return parse(payload)
