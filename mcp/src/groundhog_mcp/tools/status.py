from typing import TypedDict

from ..config import load_config
from ..engine import check_browser, remediation


class StatusResult(TypedDict):
    browser_reachable: bool
    cdp_url: str
    hint: str | None


async def status() -> StatusResult:
    """Check whether Groundhog can reach the stealth browser. Call this to
    diagnose setup before fetching: if `browser_reachable` is false, follow
    `hint` to start the browser, then retry."""
    cfg = load_config()
    reachable = await check_browser(cfg.cdp_url)
    hint = None if reachable else remediation(cfg)
    return {"browser_reachable": reachable, "cdp_url": cfg.cdp_url, "hint": hint}
