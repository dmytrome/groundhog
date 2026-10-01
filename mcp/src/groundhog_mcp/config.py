import os
from dataclasses import dataclass
from typing import Literal, get_args
from urllib.parse import urlparse

_DEFAULT_BROWSER_IMAGE = "ghcr.io/dmytrome/groundhog:latest"

SearchBackend = Literal["auto", "searxng", "serp"]
_SEARCH_BACKENDS = get_args(SearchBackend)
Browser = Literal["chrome", "stealth"]
_BROWSERS = get_args(Browser)
ALLOWED_SCHEMES = ("http", "https")


@dataclass(frozen=True)
class Config:
    cdp_url: str
    min_delay_ms: int
    block_private_ips: bool
    max_tokens: int
    auto_start_browser: bool
    compose_file: str | None
    browser_image: str
    max_concurrent_pages: int
    search_backend: SearchBackend
    searxng_url: str | None
    browser: Browser
    chrome_path: str | None
    chrome_profile: str


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def load_config() -> Config:
    return Config(
        cdp_url=os.environ.get("CDP_URL", "http://127.0.0.1:9222"),
        min_delay_ms=int(os.environ.get("GROUNDHOG_MIN_DELAY_MS", "5000")),
        block_private_ips=_bool(os.environ.get("GROUNDHOG_BLOCK_PRIVATE_IPS"), True),
        max_tokens=int(os.environ.get("GROUNDHOG_MAX_TOKENS", "20000")),
        auto_start_browser=_bool(os.environ.get("GROUNDHOG_AUTO_START_BROWSER"), True),
        compose_file=os.environ.get("GROUNDHOG_COMPOSE_FILE") or None,
        browser_image=os.environ.get("GROUNDHOG_BROWSER_IMAGE") or _DEFAULT_BROWSER_IMAGE,
        max_concurrent_pages=int(os.environ.get("GROUNDHOG_MAX_CONCURRENT_PAGES", "4")),
        search_backend=_search_backend(),
        searxng_url=_searxng_url(),
        browser=_browser(),
        chrome_path=os.environ.get("GROUNDHOG_CHROME_PATH") or None,
        chrome_profile=os.environ.get("GROUNDHOG_CHROME_PROFILE")
        or os.path.join(os.path.expanduser("~"), ".groundhog", "chrome"),
    )


def token_budget(requested: int | None, default: int) -> int:
    """Resolve a caller-supplied token budget.

    MCP arguments are model-supplied, and a non-positive budget would otherwise
    reach truncation and yield a meaningless fragment.
    """
    return requested if requested and requested > 0 else default


def _search_backend() -> SearchBackend:
    value = (os.environ.get("GROUNDHOG_SEARCH_BACKEND") or "auto").strip().lower()
    for backend in _SEARCH_BACKENDS:
        if value == backend:
            return backend
    raise ValueError(f"GROUNDHOG_SEARCH_BACKEND must be one of {_SEARCH_BACKENDS}, got {value!r}")


def _browser() -> Browser:
    value = (os.environ.get("GROUNDHOG_BROWSER") or "chrome").strip().lower()
    for browser in _BROWSERS:
        if value == browser:
            return browser
    raise ValueError(f"GROUNDHOG_BROWSER must be one of {_BROWSERS}, got {value!r}")


def _searxng_url() -> str | None:
    value = os.environ.get("SEARXNG_URL") or None
    if value is None:
        return None
    scheme = urlparse(value).scheme
    if scheme not in ALLOWED_SCHEMES:
        # Report the scheme only: the full value is an internal address that
        # would otherwise land in model context and transcripts.
        raise ValueError(f"SEARXNG_URL must be http(s), got scheme {scheme!r}")
    return value
