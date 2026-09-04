"""Scrapling's HTTP fetcher, read through the markdown its own CLI produces.

No browser, so nothing rendered by script is present to contain or to leak. Reported
separately from the browser-backed fetchers for that reason.
"""

from . import Fetched

NAME = "scrapling (http)"


def available() -> bool:
    try:
        import markdownify  # noqa: F401
        import scrapling.fetchers  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def fetch(url: str) -> Fetched:
    from scrapling import Fetcher

    try:
        page = Fetcher.get(url, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return Fetched(content="", disclosed=False, error=str(exc))
    return Fetched(content=page.markdown(main_content_only=True) or "", disclosed=False)
