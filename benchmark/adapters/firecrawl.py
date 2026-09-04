"""Firecrawl's scrape endpoint, asked for markdown."""

import json
import os
import urllib.request

from . import Fetched

NAME = "firecrawl"
_ENDPOINT = "https://api.firecrawl.dev/v2/scrape"


def available() -> bool:
    return bool(os.environ.get("FIRECRAWL_API_KEY"))


def fetch(url: str) -> Fetched:
    body = json.dumps({"url": url, "formats": ["markdown"]}).encode()
    request = urllib.request.Request(
        _ENDPOINT,
        data=body,
        headers={
            "Authorization": f"Bearer {os.environ['FIRECRAWL_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        return Fetched(content="", disclosed=False, error=str(exc))
    data = payload.get("data") or {}
    return Fetched(content=data.get("markdown") or "", disclosed=False)
