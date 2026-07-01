import asyncio
import urllib.request
from dataclasses import dataclass

import tldextract
from playwright.async_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Playwright,
    async_playwright,
)

from . import safety
from .config import Config, load_config
from .ratelimit import RateLimiter

_GOTO_TIMEOUT_MS = 60_000
_PROBE_TIMEOUT_S = 2.0
_AUTOSTART_READY_TRIES = 30
_ERR_DETAIL_CHARS = 300


class BrowserUnavailableError(Exception):
    """The stealth browser's CDP endpoint could not be reached."""


def remediation(cfg: Config) -> str:
    return (
        f"Cannot reach the stealth browser at {cfg.cdp_url}. "
        "Start it with `docker compose up -d` (see the Groundhog README), "
        "or set GROUNDHOG_AUTO_START_BROWSER=true to let Groundhog start it for you."
    )


async def check_browser(cdp_url: str, timeout: float = _PROBE_TIMEOUT_S) -> bool:
    """Return whether the CDP endpoint answers, via its `/json/version` probe."""
    probe_url = cdp_url.rstrip("/") + "/json/version"

    def _probe() -> bool:
        try:
            with urllib.request.urlopen(probe_url, timeout=timeout) as resp:
                return resp.status == 200
        except OSError:
            return False  # refused / DNS / timeout all mean "not reachable"

    return await asyncio.get_running_loop().run_in_executor(None, _probe)


async def _start_browser_container(cfg: Config) -> None:
    cmd = ["docker", "compose"]
    if cfg.compose_file:
        cmd += ["-f", cfg.compose_file]
    cmd += ["up", "-d"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except FileNotFoundError as exc:
        raise BrowserUnavailableError(
            "GROUNDHOG_AUTO_START_BROWSER is set but Docker was not found on PATH. "
            + remediation(cfg)
        ) from exc
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        detail = stderr.decode(errors="replace").strip()[:_ERR_DETAIL_CHARS]
        raise BrowserUnavailableError(f"`docker compose up -d` failed: {detail}")
    for _ in range(_AUTOSTART_READY_TRIES):
        if await check_browser(cfg.cdp_url):
            return
        await asyncio.sleep(1)
    raise BrowserUnavailableError(
        f"Browser container started but {cfg.cdp_url} did not become ready in time."
    )


@dataclass
class RenderedPage:
    html: str
    text: str
    final_url: str
    title: str


def registrable_domain(url: str) -> str:
    ext = tldextract.extract(url)
    return ext.registered_domain or ext.fqdn or url


class EngineProvider:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._rl = RateLimiter(cfg.min_delay_ms / 1000)

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._connect()
        self._context = await self._browser.new_context(
            user_agent=self._cfg.user_agent,
            viewport={"width": 1920, "height": 1080},
        )

    async def _connect(self) -> Browser:
        try:
            return await self._pw.chromium.connect_over_cdp(self._cfg.cdp_url)
        except PlaywrightError as exc:
            if not self._cfg.auto_start_browser:
                raise BrowserUnavailableError(remediation(self._cfg)) from exc
        await _start_browser_container(self._cfg)
        try:
            return await self._pw.chromium.connect_over_cdp(self._cfg.cdp_url)
        except PlaywrightError as exc:
            raise BrowserUnavailableError(remediation(self._cfg)) from exc

    async def fetch(self, url: str) -> RenderedPage:
        await safety.check_url(url, self._cfg)
        await self._rl.acquire(registrable_domain(url))
        page = await self._context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=_GOTO_TIMEOUT_MS)
            # A page can redirect to an internal address the initial check never
            # saw; re-check the final URL so its content is never returned.
            await safety.check_url(page.url, self._cfg)
            return RenderedPage(
                html=await page.content(),
                text=await page.inner_text("body"),
                final_url=page.url,
                title=await page.title(),
            )
        finally:
            await page.close()

    async def aclose(self) -> None:
        if self._context is not None:
            await self._context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()


_provider: EngineProvider | None = None
_provider_lock = asyncio.Lock()


async def get_provider() -> EngineProvider:
    global _provider
    async with _provider_lock:
        if _provider is None:
            provider = EngineProvider(load_config())
            await provider.start()
            _provider = provider
    return _provider
