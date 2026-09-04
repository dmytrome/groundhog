"""Firecrawl's scrape endpoint, asked for markdown."""

import json
import os
import urllib.request

from . import Fetched

NAME = "firecrawl"
_ENDPOINT = "https://api.firecrawl.dev/v2/scrape"


def available() -> bool:
    return True


def build_request(url: str) -> urllib.request.Request:
    body = json.dumps({"url": url, "formats": ["markdown"]}).encode()
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("FIRECRAWL_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return urllib.request.Request(_ENDPOINT, data=body, headers=headers)


def fetch(url: str) -> Fetched:
    request = build_request(url)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        return Fetched(content="", disclosed=False, error=str(exc))
    data = payload.get("data") or {}
    return Fetched(content=data.get("markdown") or "", disclosed=False)
