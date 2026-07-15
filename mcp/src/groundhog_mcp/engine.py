import asyncio
import json
import shutil
import sys
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

import tldextract

from . import safety
from .cdp import CDPClient, CDPError
from .config import Config, load_config
from .detect_js import DETECT_AND_COLLECT
from .ratelimit import RateLimiter

_GOTO_TIMEOUT_S = 60.0
_DETECT_TIMEOUT_S = 15.0
_PROBE_TIMEOUT_S = 2.0
_AUTOSTART_READY_TRIES = 30
_ERR_DETAIL_CHARS = 300
_VERSION_PATH = "/json/version"
_CONTAINER_NAME = "groundhog-browser"
_CONTAINER_SHM = "512m"
_CONTAINER_CDP_PORT = 9222
_CONTAINER_PLATFORM = "linux/amd64"  # the image is amd64-only (google-chrome-stable)
_CONTAINER_BIND_HOST = "127.0.0.1"  # never bind the auto-started CDP to a public interface
_RUNTIMES = ("docker", "podman")
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


class BrowserUnavailableError(Exception):
    """The stealth browser's CDP endpoint could not be reached."""


def _container_runtime() -> str | None:
    for runtime in _RUNTIMES:
        if shutil.which(runtime):
            return runtime
    return None


def _is_local(cdp_url: str) -> bool:
    return (urlparse(cdp_url).hostname or "") in _LOCAL_HOSTS


def _port_of(cdp_url: str) -> int:
    return urlparse(cdp_url).port or _CONTAINER_CDP_PORT


def remediation(cfg: Config) -> str:
    if _container_runtime() is None:
        return (
            "No container runtime found (looked for docker, podman). Install Docker "
            "(https://docs.docker.com/get-docker/) or Podman, or point CDP_URL at a "
            "hosted Groundhog browser for zero-install use."
        )
    bind = f"{_CONTAINER_BIND_HOST}:{_port_of(cfg.cdp_url)}:{_CONTAINER_CDP_PORT}"
    return (
        f"Cannot reach the stealth browser at {cfg.cdp_url}. Start it with "
        f"`docker run -d --rm -p {bind} {cfg.browser_image}` (or `docker compose up -d` "
        "from the repo), or point CDP_URL at a hosted Groundhog browser."
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


async def _run(cmd: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    return proc.returncode or 0, stderr.decode(errors="replace").strip()[:_ERR_DETAIL_CHARS]


async def _start_browser(cfg: Config) -> None:
    """Bring up the stealth browser.

    Default path: `docker run` the published image, so a bare `uvx groundhog-mcp`
    works with no repo checkout. `GROUNDHOG_COMPOSE_FILE` opts into `docker compose`
    against a local repo instead.
    """
    runtime = _container_runtime()
    if runtime is None:
        raise BrowserUnavailableError(remediation(cfg))
    if cfg.compose_file:
        cmd = [runtime, "compose", "-f", cfg.compose_file, "up", "-d"]
    else:
        await _run([runtime, "rm", "-f", _CONTAINER_NAME])  # clear a stale container, if any
        cmd = [
            runtime,
            "run",
            "-d",
            "--rm",
            "--name",
            _CONTAINER_NAME,
            "--platform",
            _CONTAINER_PLATFORM,
            "--shm-size",
            _CONTAINER_SHM,
            "-p",
            f"{_CONTAINER_BIND_HOST}:{_port_of(cfg.cdp_url)}:{_CONTAINER_CDP_PORT}",
            "--",
            cfg.browser_image,
        ]
    print(
        f"[groundhog] starting the stealth browser via {runtime} "
        "(first run pulls the image, which can take a few minutes)…",
        file=sys.stderr,
    )
    code, detail = await _run(cmd)
    if code != 0:
        raise BrowserUnavailableError(f"Could not start the browser via {runtime}: {detail}")
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
        cfg = self._cfg
        try:
            return await _browser_ws_url(cfg.cdp_url)
        except OSError as exc:
            # A remote/hosted CDP_URL is the user's to manage; only auto-start a local one.
            if not (cfg.auto_start_browser and _is_local(cfg.cdp_url)):
                raise BrowserUnavailableError(remediation(cfg)) from exc
        await _start_browser(cfg)
        try:
            return await _browser_ws_url(cfg.cdp_url)
        except OSError as exc:
            raise BrowserUnavailableError(remediation(cfg)) from exc

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
            # Re-check right before navigate: the rate-limiter/semaphore wait above plus
            # Chrome's own independent DNS resolution at nav time reopen a rebinding window.
            await safety.check_url(url, self._cfg)
            loaded = self._cdp.expect_event("Page.domContentEventFired", session_id=sid)
            nav = await self._cdp.send("Page.navigate", {"url": url}, session_id=sid)
            if nav.get("errorText"):
                raise CDPError(f"navigation failed: {nav['errorText']}")
            await asyncio.wait_for(loaded, timeout=_GOTO_TIMEOUT_S)

            final_url = await self._eval(sid, "document.location.href")
            # A page can redirect to an internal address the initial check never saw;
            # re-check the final URL so its content is never returned.
            await safety.check_url(final_url, self._cfg)
            detect_expr = f"({DETECT_AND_COLLECT})({json.dumps(strip_hidden)})"
            try:
                collected = await asyncio.wait_for(
                    self._eval(sid, detect_expr), timeout=_DETECT_TIMEOUT_S
                )
            except TimeoutError as exc:
                # An adversarial page (deeply nested, huge DOM) could otherwise force
                # unbounded style-recalc work here; fail this fetch instead of hanging
                # the page's concurrency slot indefinitely.
                raise CDPError("hidden-text detection timed out") from exc
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
