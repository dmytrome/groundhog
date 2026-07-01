from datetime import UTC, datetime
from typing import TypedDict

from .. import extract
from ..config import load_config
from ..engine import get_provider

_FORMATS = ("markdown", "text")


class ReadResult(TypedDict):
    markdown: str
    title: str
    url: str
    final_url: str
    fetched_at: str
    truncated: bool


async def read_url(url: str, format: str = "markdown", max_tokens: int | None = None) -> ReadResult:
    """Fetch a web page through the stealth browser and return clean Markdown
    plus provenance (source URL, final URL, title, fetch time). Use this to
    ground answers in live web content, including sites that block plain
    fetchers. `format` may be "markdown" (default) or "text"."""
    if format not in _FORMATS:
        raise ValueError(f"format must be one of {_FORMATS}, got {format!r}")
    cfg = load_config()
    provider = await get_provider()
    page = await provider.fetch(url)
    limit = max_tokens or cfg.max_tokens
    if format == "text":
        body, truncated = extract.truncate(page.text, limit)
    else:
        body, truncated = extract.to_markdown(page.html, page.text, page.final_url, limit)
    return {
        "markdown": body,
        "title": page.title,
        "url": url,
        "final_url": page.final_url,
        "fetched_at": datetime.now(UTC).isoformat(),
        "truncated": truncated,
    }
