import urllib.parse

import lxml.html

from .. import engine
from ..config import ALLOWED_SCHEMES
from .types import SearchHit, SearchUnavailableError

_ENGINE = "duckduckgo"
# The no-JS endpoint: server-rendered results, no client-side app to wait on.
_SERP_URL = "https://html.duckduckgo.com/html/?q={query}"
_RESULT_XPATH = "//div[contains(@class,'result__body')]"
_TITLE_XPATH = ".//a[contains(@class,'result__a')]"
_SNIPPET_XPATH = ".//a[contains(@class,'result__snippet')]"
# Rendered when DuckDuckGo genuinely has nothing, which is not the same as our
# selectors having gone stale — the difference decides error vs empty result.
_EMPTY_XPATH = "//div[contains(@class,'no-results')]"


def _real_url(href: str) -> str | None:
    """Unwrap DuckDuckGo's `/l/?uddg=<encoded>` redirect to the destination.

    The unwrapped target is page content an attacker can plant, so it goes
    through the same scheme allowlist as any other URL we hand back.
    """
    if href.startswith("//"):
        href = f"https:{href}"
    parsed = urllib.parse.urlparse(href)
    if parsed.path.startswith("/l/"):
        target = urllib.parse.parse_qs(parsed.query).get("uddg")
        if not target:
            return None
        href = target[0]
        parsed = urllib.parse.urlparse(href)
    # Checked here as well as at the shared boundary: this unwrapper percent-decodes
    # the redirect target, so it is where a `javascript:`/`file:` URL can first come
    # into existence — rejecting at the point of introduction, not a second policy.
    return href if parsed.scheme in ALLOWED_SCHEMES else None


def parse(page_html: str) -> list[SearchHit]:
    tree = lxml.html.fromstring(page_html)
    blocks = tree.xpath(_RESULT_XPATH)
    if not blocks and not tree.xpath(_EMPTY_XPATH):
        raise SearchUnavailableError(
            "DuckDuckGo returned a page with neither results nor a no-results marker — "
            "the SERP layout likely changed, or the request was challenged."
        )
    hits: list[SearchHit] = []
    for position, block in enumerate(blocks):
        titles = block.xpath(_TITLE_XPATH)
        if not titles:
            continue
        url = _real_url(titles[0].get("href", ""))
        if not url:
            continue
        # Read the snippet from inside this block, so a result rendered without
        # one empties only its own hit instead of shifting every later snippet.
        snippets = block.xpath(_SNIPPET_XPATH)
        hits.append(
            {
                "title": titles[0].text_content().strip(),
                "url": url,
                "snippet": snippets[0].text_content().strip() if snippets else "",
                "engine": _ENGINE,
                # No engine-supplied relevance here, so rank by page order.
                "score": round(1.0 / (position + 1), 4),
                "published": None,
            }
        )
    return hits


async def search(query: str) -> list[SearchHit]:
    provider = await engine.get_provider()
    page = await provider.fetch(_SERP_URL.format(query=urllib.parse.quote_plus(query)))
    return parse(page.html)
