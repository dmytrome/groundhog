import asyncio
import json
import urllib.request
from dataclasses import dataclass

import tldextract

from . import safety
from .cdp import CDPClient, CDPError
from .config import Config, load_config
from .detect_js import DETECT_AND_COLLECT
from .ratelimit import RateLimiter

_GOTO_TIMEOUT_S = 60.0
_PROBE_TIMEOUT_S = 2.0
_AUTOSTART_READY_TRIES = 30
_ERR_DETAIL_CHARS = 300
_VERSION_PATH = "/json/version"


class BrowserUnavailableError(Exception):
    """The stealth browser's CDP endpoint could not be reached."""


def remediation(cfg: Config) -> str:
    return (
        f"Cannot reach the stealth browser at {cfg.cdp_url}. "
        "Start it with `docker compose up -d` (see the Groundhog README), "
        "or set GROUNDHOG_AUTO_START_BROWSER=true to let Groundhog start it for you."
    )


def _http_json(url: str, timeout: float) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.load(resp)


async def _fetch_version(cdp_url: str, timeout: float) -> dict:
    """Read the CDP `/json/version` document off the event loop."""
    url = cdp_url.rstrip("/") + _VERSION_PATH
    return await asyncio.get_running_loop().run_in_executor(None, lambda: _http_json(url, timeout))


async def check_browser(cdp_url: str, timeout: float = _PROBE_TIMEOUT_S) -> bool:
    """Return whether the CDP endpoint answers its `/json/version` probe."""
    try:
        return "webSocketDebuggerUrl" in await _fetch_version(cdp_url, timeout)
    except OSError:
        return False  # refused / DNS / timeout all mean "not reachable"


async def _browser_ws_url(cdp_url: str, timeout: float = _PROBE_TIMEOUT_S) -> str:
    return (await _fetch_version(cdp_url, timeout))["webSocketDebuggerUrl"]


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
    hidden_spans: list[dict]
    meta: dict


def registrable_domain(url: str) -> str:
    ext = tldextract.extract(url)
    return ext.registered_domain or ext.fqdn or url


class EngineProvider:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._cdp: CDPClient | None = None
        self._rl = RateLimiter(cfg.min_delay_ms / 1000)
        self._pages = asyncio.Semaphore(cfg.max_concurrent_pages)

    async def start(self) -> None:
        ws_url = await self._resolve_ws()
        self._cdp = CDPClient(ws_url)
        await self._cdp.connect()

    async def _resolve_ws(self) -> str:
        try:
            return await _browser_ws_url(self._cfg.cdp_url)
        except OSError as exc:
            if not self._cfg.auto_start_browser:
                raise BrowserUnavailableError(remediation(self._cfg)) from exc
        await _start_browser_container(self._cfg)
        try:
            return await _browser_ws_url(self._cfg.cdp_url)
        except OSError as exc:
            raise BrowserUnavailableError(remediation(self._cfg)) from exc

    async def fetch(self, url: str, strip_hidden: bool = True) -> RenderedPage:
        await safety.check_url(url, self._cfg)
        await self._rl.acquire(registrable_domain(url))
        async with self._pages:
            return await self._fetch_in_target(url, strip_hidden)

    async def _fetch_in_target(self, url: str, strip_hidden: bool) -> RenderedPage:
        assert self._cdp is not None
        target = await self._cdp.send("Target.createTarget", {"url": "about:blank"})
        tid = target["targetId"]
        att = await self._cdp.send("Target.attachToTarget", {"targetId": tid, "flatten": True})
        sid = att["sessionId"]
        try:
            # Only Page is enabled — never Runtime/Console, which would expose the CDP
            # session to the page as the `isAutomatedWithCDP` signal.
            await self._cdp.send("Page.enable", session_id=sid)
            loaded = self._cdp.expect_event("Page.domContentEventFired", session_id=sid)
            nav = await self._cdp.send("Page.navigate", {"url": url}, session_id=sid)
            if nav.get("errorText"):
                raise CDPError(f"navigation failed: {nav['errorText']}")
            await asyncio.wait_for(loaded, timeout=_GOTO_TIMEOUT_S)

            final_url = await self._eval(sid, "document.location.href")
            # A page can redirect to an internal address the initial check never saw;
            # re-check the final URL so its content is never returned.
            await safety.check_url(final_url, self._cfg)
            collected = await self._eval(sid, f"({DETECT_AND_COLLECT})({json.dumps(strip_hidden)})")
            return RenderedPage(
                html=await self._eval(sid, "document.documentElement.outerHTML"),
                text=await self._eval(sid, "document.body ? document.body.innerText : ''"),
                final_url=final_url,
                title=await self._eval(sid, "document.title"),
                hidden_spans=collected["hidden"],
                meta={
                    "meta": collected["meta"],
                    "lang": collected["lang"],
                    "canonical": collected["canonical"],
                },
            )
        finally:
            await self._cdp.send("Target.closeTarget", {"targetId": tid})

    async def _eval(self, session_id: str, expression: str) -> object:
        res = await self._cdp.send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            session_id=session_id,
        )
        if "exceptionDetails" in res:
            raise CDPError(str(res["exceptionDetails"]))
        return res.get("result", {}).get("value")

    async def aclose(self) -> None:
        if self._cdp is not None:
            await self._cdp.close()
            self._cdp = None


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


async def shutdown_provider() -> None:
    global _provider
    async with _provider_lock:
        if _provider is not None:
            await _provider.aclose()
            _provider = None
