"""Jina Reader, the hosted URL-to-markdown service.

Keyless at low volume. The default response is a cached snapshot, which would measure
whatever Jina fetched earlier rather than the page under test, so caching is opted out.
"""

import urllib.request

from . import Fetched

NAME = "jina reader"


def available() -> bool:
    return True


def fetch(url: str) -> Fetched:
    request = urllib.request.Request(
        f"https://r.jina.ai/{url}",
        headers={"x-no-cache": "true", "Accept": "text/plain"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            return Fetched(content=response.read().decode("utf-8", "replace"), disclosed=False)
    except Exception as exc:  # noqa: BLE001
        return Fetched(content="", disclosed=False, error=str(exc))
