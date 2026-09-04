"""A plain HTTP fetch through an article extractor.

The baseline because it is what most agents actually do: no browser, no computed styles,
no notion that a page might be hiding text from the reader.
"""

import urllib.request

import trafilatura

from . import Fetched

NAME = "requests + trafilatura"


def fetch(url: str) -> Fetched:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310
            html = response.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return Fetched(content="", disclosed=False, error=str(exc))
    content = trafilatura.extract(
        html, url=url, output_format="markdown", include_links=True, include_tables=True
    )
    return Fetched(content=content or "", disclosed=False)
