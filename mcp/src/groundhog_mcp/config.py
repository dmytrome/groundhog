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


def _bool(name: str, default: bool) -> bool:
    value = (os.environ.get(name) or "").strip().lower()
    if not value:
        return default
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} must be true or false, got {value!r}")


def _int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    return int(raw) if raw else default


def _positive(name: str, default: int) -> int:
    value = _int(name, default)
    if value < 1:
        raise ValueError(f"{name} must be at least 1, got {value}")
    return value


def load_config() -> Config:
    return Config(
        cdp_url=os.environ.get("CDP_URL", "http://127.0.0.1:9222"),
        min_delay_ms=_int("GROUNDHOG_MIN_DELAY_MS", 5000),
        block_private_ips=_bool("GROUNDHOG_BLOCK_PRIVATE_IPS", True),
        max_tokens=_int("GROUNDHOG_MAX_TOKENS", 20000),
        auto_start_browser=_bool("GROUNDHOG_AUTO_START_BROWSER", True),
        compose_file=os.environ.get("GROUNDHOG_COMPOSE_FILE") or None,
        browser_image=os.environ.get("GROUNDHOG_BROWSER_IMAGE") or _DEFAULT_BROWSER_IMAGE,
        max_concurrent_pages=_positive("GROUNDHOG_MAX_CONCURRENT_PAGES", 4),
        search_backend=_search_backend(),
        searxng_url=_searxng_url(),
        browser=_browser(),
        chrome_path=_path(os.environ.get("GROUNDHOG_CHROME_PATH")),
        chrome_profile=_path(os.environ.get("GROUNDHOG_CHROME_PROFILE"))
        or os.path.join(os.path.expanduser("~"), ".groundhog", "chrome"),
    )


def token_budget(requested: int | None, default: int) -> int:
    return requested if requested and requested > 0 else default


def _search_backend() -> SearchBackend:
    value = (os.environ.get("GROUNDHOG_SEARCH_BACKEND") or "auto").strip().lower()
    for backend in _SEARCH_BACKENDS:
        if value == backend:
            return backend
    raise ValueError(f"GROUNDHOG_SEARCH_BACKEND must be one of {_SEARCH_BACKENDS}, got {value!r}")


def _path(value: str | None) -> str | None:
    return os.path.expanduser(value) if value else None


def _browser() -> Browser:
    chose_a_container = bool(
        os.environ.get("GROUNDHOG_COMPOSE_FILE") or os.environ.get("GROUNDHOG_BROWSER_IMAGE")
    )
    default = "stealth" if chose_a_container else "chrome"
    value = (os.environ.get("GROUNDHOG_BROWSER") or default).strip().lower()
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
        raise ValueError(f"SEARXNG_URL must be http(s), got scheme {scheme!r}")
    return value
