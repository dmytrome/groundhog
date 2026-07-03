import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    cdp_url: str
    min_delay_ms: int
    block_private_ips: bool
    max_tokens: int
    auto_start_browser: bool
    compose_file: str | None
    max_concurrent_pages: int


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
        auto_start_browser=_bool(os.environ.get("GROUNDHOG_AUTO_START_BROWSER"), False),
        compose_file=os.environ.get("GROUNDHOG_COMPOSE_FILE") or None,
        max_concurrent_pages=int(os.environ.get("GROUNDHOG_MAX_CONCURRENT_PAGES", "4")),
    )
