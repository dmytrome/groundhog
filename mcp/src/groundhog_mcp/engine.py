import asyncio
import ipaddress
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import NotRequired, TypedDict
from urllib.parse import urlparse

import tldextract
import websockets
from websockets.exceptions import WebSocketException

from . import classify, http, safety, sanitize
from .cdp import CDPClient, CDPError
from .config import Config, load_config
from .detect_js import DETECT_AND_COLLECT
from .ratelimit import RateLimiter

_GOTO_TIMEOUT_S = 60.0
_DETECT_TIMEOUT_S = 15.0
_EVAL_TIMEOUT_S = 20.0
_SETTLE_POLL_S = 0.25
_SETTLE_QUIET_S = 1.0
_SETTLE_TIMEOUT_S = 8.0
_DOM_SIZE_EXPR = (
    "document.querySelectorAll('*').length + '|' + "
    "(document.body ? document.body.innerText.length : 0)"
)
_PROBE_TIMEOUT_S = 2.0
_AUTOSTART_READY_TRIES = 30
_VERSION_PATH = "/json/version"
_MAX_REPORTED_DROP = 1_000_000
_CONTAINER_NAME = "groundhog-browser"
_CONTAINER_SHM = "512m"
_CONTAINER_CDP_PORT = 9222
_CONTAINER_BIND_HOST = "127.0.0.1"
_RUNTIMES = ("docker", "podman")
_CHROME_PATHS = {
    "darwin": (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ),
    "win32": tuple(
        os.path.join(root, "Google", "Chrome", "Application", "chrome.exe")
        for root in filter(
            None,
            (os.environ.get(k) for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")),
        )
    ),
}
_CHROME_COMMANDS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
_WS_SCHEMES = ("ws", "wss")
_ISOLATED_WORLD = "groundhog"
_HEADLESS_TOKEN = "HeadlessChrome/"
_IDENTITY_TIMEOUT_S = 10.0
_IDENTITY_ATTEMPTS = 3
_FRAME_AUTO_ATTACH = {
    "autoAttach": True,
    "waitForDebuggerOnStart": True,
    "flatten": True,
    "filter": [{"type": "iframe"}, {"type": "worker"}],
}
_HINTS_PAGE = "chrome://version/"
_HINTS_EXPR = (
    "navigator.userAgentData.getHighEntropyValues(['platform', 'platformVersion', "
    "'architecture', 'model', 'bitness', 'fullVersionList', 'wow64']).then(v => "
    "JSON.stringify(Object.assign({brands: navigator.userAgentData.brands, "
    "mobile: navigator.userAgentData.mobile}, v)))"
)
_HINT_KEYS = (
    "brands",
    "fullVersionList",
    "platform",
    "platformVersion",
    "architecture",
    "model",
    "mobile",
    "bitness",
    "wow64",
)


class BrowserUnavailableError(safety.CallerFacingError):
    pass


def _container_runtime() -> str | None:
    for runtime in _RUNTIMES:
        if shutil.which(runtime):
            return runtime
    return None


def _is_local(cdp_url: str) -> bool:
    return (urlparse(cdp_url).hostname or "") in _LOCAL_HOSTS


def _port_of(cdp_url: str) -> int:
    try:
        return urlparse(cdp_url).port or _CONTAINER_CDP_PORT
    except ValueError:
        return _CONTAINER_CDP_PORT


def _run_argv(runtime: str, cfg: Config) -> list[str]:
    return [
        runtime,
        "run",
        "-d",
        "--rm",
        "--name",
        _CONTAINER_NAME,
        "--shm-size",
        _CONTAINER_SHM,
        "-p",
        f"{_CONTAINER_BIND_HOST}:{_port_of(cfg.cdp_url)}:{_CONTAINER_CDP_PORT}",
        "--",
        cfg.browser_image,
    ]


def _is_executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def _chrome_binary(cfg: Config) -> str | None:
    if cfg.chrome_path:
        return cfg.chrome_path if _is_executable(cfg.chrome_path) else None
    for path in _CHROME_PATHS.get(sys.platform, ()):
        if _is_executable(path):
            return path
    for name in _CHROME_COMMANDS:
        found = shutil.which(name)
        if found:
            return found
    return None


def _chrome_argv(binary: str, cfg: Config) -> list[str]:
    return [
        binary,
        f"--remote-debugging-port={_port_of(cfg.cdp_url)}",
        f"--user-data-dir={cfg.chrome_profile}",
        "--headless=new",
        "--window-size=1440,900",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ]


def _chrome_remediation(cfg: Config) -> str:
    if cfg.chrome_path and _chrome_binary(cfg) is None:
        return (
            f"GROUNDHOG_CHROME_PATH is {cfg.chrome_path}, which is not an executable file. "
            "Point it at Chrome's executable, unset it to use the installed Chrome, or set "
            "GROUNDHOG_BROWSER=stealth to run the stealth image in Docker."
        )
    if _chrome_binary(cfg) is None:
        return (
            "Chrome was not found. Install Google Chrome, set GROUNDHOG_CHROME_PATH to its "
            "executable, or set GROUNDHOG_BROWSER=stealth to run the stealth image in Docker."
        )
    return (
        f"Cannot reach Chrome at {safety.redacted_url(cfg.cdp_url)}. Start it with "
        f"`{shlex.join(_chrome_argv(_chrome_binary(cfg) or '', cfg))}`, or point CDP_URL "
        "at a running browser."
    )


def remediation(cfg: Config) -> str:
    if _is_websocket(cfg.cdp_url) or not _is_local(cfg.cdp_url):
        return (
            f"Cannot reach the browser at {safety.redacted_url(cfg.cdp_url)}. A browser "
            "Groundhog does not launch itself is not started automatically: check that it "
            "is running and that CDP_URL is its current address."
        )
    if cfg.browser == "chrome":
        return _chrome_remediation(cfg)
    runtime = _container_runtime()
    if runtime is None:
        return (
            "No container runtime found (looked for docker, podman). Install Docker "
            "(https://docs.docker.com/get-docker/) or Podman, or point CDP_URL at a "
            "hosted Groundhog browser for zero-install use."
        )
    return (
        f"Cannot reach the stealth browser at {safety.redacted_url(cfg.cdp_url)}. Start it "
        f"with `{shlex.join(_run_argv(runtime, cfg))}` (or `docker compose up -d` from the "
        "repo), or point CDP_URL at a hosted Groundhog browser."
    )


def _ip_probe_url(cdp_url: str) -> str:
    """Swap a DNS hostname in `cdp_url` for its resolved IP.

    Chrome rejects DevTools HTTP/WS requests whose Host header is a
    non-localhost DNS name (its DNS-rebinding protection), so a CDP_URL like
    http://chrome:9222 (compose/k8s service names) must be dialed by address —
    Chrome then also advertises a connectable `webSocketDebuggerUrl`.
    """
    parts = urlparse(cdp_url)
    host = parts.hostname or ""
    if host in _LOCAL_HOSTS or parts.scheme != "http":
        return cdp_url
    try:
        ipaddress.ip_address(host)
        return cdp_url
    except ValueError:
        pass
    infos = socket.getaddrinfo(host, parts.port, proto=socket.IPPROTO_TCP)
    family, _, _, _, addr = min(infos, key=lambda info: info[0] != socket.AF_INET)
    netloc = f"[{addr[0]}]" if family == socket.AF_INET6 else addr[0]
    if parts.port:
        netloc += f":{parts.port}"
    return parts._replace(netloc=netloc).geturl()


def _version_url(cdp_url: str) -> str:
    parts = urlparse(_ip_probe_url(cdp_url))
    return parts._replace(path=parts.path.rstrip("/") + _VERSION_PATH).geturl()


async def _fetch_version(cdp_url: str, timeout: float) -> dict:
    return await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: http.read_json(_version_url(cdp_url), timeout),
    )


def _is_websocket(cdp_url: str) -> bool:
    return urlparse(cdp_url).scheme in _WS_SCHEMES


async def _websocket_answers(cdp_url: str, timeout: float) -> bool:
    async with websockets.connect(cdp_url, open_timeout=timeout):
        return True


async def check_browser(cdp_url: str, timeout: float = _PROBE_TIMEOUT_S) -> bool:
    try:
        if _is_websocket(cdp_url):
            return await _websocket_answers(cdp_url, timeout)
        return "webSocketDebuggerUrl" in await _fetch_version(cdp_url, timeout)
    except (OSError, WebSocketException):
        return False
    except ValueError:
        return False


async def _browser_ws_url(cdp_url: str, timeout: float = _PROBE_TIMEOUT_S) -> str:
    if _is_websocket(cdp_url):
        return cdp_url
    return (await _fetch_version(cdp_url, timeout))["webSocketDebuggerUrl"]


async def _run(cmd: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    detail = sanitize.clean_field(stderr.decode(errors="replace"), sanitize.MAX_ERROR_CHARS)
    return proc.returncode or 0, detail or ""


def _launch_detached(argv: list[str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


async def _await_ready(
    cfg: Config, what: str, process: subprocess.Popen[bytes] | None = None
) -> None:
    for _ in range(_AUTOSTART_READY_TRIES):
        if await check_browser(cfg.cdp_url):
            return
        if process is not None and process.poll() is not None:
            raise BrowserUnavailableError(
                f"{what} exited at launch (code {process.returncode}) before "
                f"{safety.redacted_url(cfg.cdp_url)} answered."
            )
        await asyncio.sleep(1)
    if process is not None and process.poll() is None:
        process.kill()
    raise BrowserUnavailableError(
        f"{what} started but {safety.redacted_url(cfg.cdp_url)} did not become ready in time."
    )


def _profile_owner(profile: str) -> int | None:
    try:
        host, _, pid = os.readlink(os.path.join(profile, "SingletonLock")).rpartition("-")
        singleton = os.readlink(os.path.join(profile, "SingletonSocket"))
    except OSError:
        return None
    if host != socket.gethostname() or not pid.isdigit():
        return None
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.connect(singleton)
    except OSError:
        return None
    finally:
        probe.close()
    return int(pid)


def _disable_preloading(profile: str) -> None:
    path = os.path.join(profile, "Default", "Preferences")
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    try:
        with open(path, encoding="utf-8") as handle:
            prefs = json.load(handle)
    except (OSError, ValueError):
        prefs = {}
    if not isinstance(prefs, dict):
        prefs = {}
    net = prefs.get("net")
    prefs["net"] = {**(net if isinstance(net, dict) else {}), "network_prediction_options": 2}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(prefs, handle)


async def _start_chrome(cfg: Config) -> None:
    binary = _chrome_binary(cfg)
    if binary is None:
        raise BrowserUnavailableError(_chrome_remediation(cfg))
    if urlparse(cfg.cdp_url).hostname not in ("127.0.0.1", "localhost"):
        raise BrowserUnavailableError(
            f"Chrome only listens for DevTools on 127.0.0.1, so it cannot be started for "
            f"{safety.redacted_url(cfg.cdp_url)}. Set CDP_URL=http://127.0.0.1:"
            f"{_port_of(cfg.cdp_url)}."
        )
    owner = _profile_owner(cfg.chrome_profile)
    if owner is not None:
        raise BrowserUnavailableError(
            f"The Chrome profile at {cfg.chrome_profile} is in use by another Chrome "
            f"(pid {owner}) that is not answering at {safety.redacted_url(cfg.cdp_url)}. "
            "Point CDP_URL at that Chrome's debugging port, stop it, or set "
            "GROUNDHOG_CHROME_PROFILE to another directory."
        )
    try:
        os.makedirs(cfg.chrome_profile, mode=0o700, exist_ok=True)
        os.chmod(cfg.chrome_profile, 0o700)
        _disable_preloading(cfg.chrome_profile)
    except OSError as exc:
        raise BrowserUnavailableError(
            f"The Chrome profile at {cfg.chrome_profile} could not be prepared "
            f"({exc.strerror or exc}). Set GROUNDHOG_CHROME_PROFILE to a directory you can write."
        ) from exc
    print(
        f"[groundhog] starting Chrome with its own profile at {cfg.chrome_profile}…",
        file=sys.stderr,
    )
    try:
        process = _launch_detached(_chrome_argv(binary, cfg))
    except OSError as exc:
        raise BrowserUnavailableError(
            f"Chrome at {binary} could not be started: {exc.strerror or exc}"
        ) from exc
    await _await_ready(cfg, "Chrome", process)


async def _start_browser(cfg: Config) -> None:
    if cfg.browser == "chrome":
        await _start_chrome(cfg)
    else:
        await _start_stealth(cfg)


async def _start_stealth(cfg: Config) -> None:
    runtime = _container_runtime()
    if runtime is None:
        raise BrowserUnavailableError(remediation(cfg))
    if cfg.compose_file:
        cmd = [runtime, "compose", "-f", cfg.compose_file, "up", "-d"]
    else:
        await _run([runtime, "rm", "-f", _CONTAINER_NAME])
        cmd = _run_argv(runtime, cfg)
    print(
        f"[groundhog] starting the stealth browser via {runtime} "
        "(first run pulls the image, which can take a few minutes)…",
        file=sys.stderr,
    )
    code, detail = await _run(cmd)
    if code != 0:
        raise BrowserUnavailableError(f"Could not start the browser via {runtime}: {detail}")
    await _await_ready(cfg, "Browser container")


class HiddenSpan(TypedDict):
    text: str
    reason: str
    path: NotRequired[str]


class PageMeta(TypedDict):
    meta: dict[str, str]
    lang: str | None
    canonical: str | None


@dataclass
class RenderedPage:
    html: str
    text: str
    final_url: str
    title: str
    hidden_spans: list[HiddenSpan]
    meta: PageMeta
    isolated: bool
    strip_incomplete: bool
    spans_dropped: int
    http_status: int | None
    retrieval_status: classify.RetrievalStatus

    def __post_init__(self) -> None:
        self.html, self.text = _as_text(self.html), _as_text(self.text)
        self.final_url = safety.safe_url(self.final_url) or ""
        self.title = sanitize.clean_field(self.title, sanitize.MAX_TITLE_CHARS) or ""
        dropped = self.spans_dropped
        valid = type(dropped) is int and dropped > 0
        self.spans_dropped = min(dropped, _MAX_REPORTED_DROP) if valid else 0
        spans = self.hidden_spans if isinstance(self.hidden_spans, list) else []
        self.hidden_spans = [span for raw in spans if (span := _clean_span(raw))]
        raw_meta = self.meta if isinstance(self.meta, dict) else {}
        meta_pairs = raw_meta.get("meta")
        self.meta = {
            "meta": {
                key: cleaned
                for key, value in (meta_pairs if isinstance(meta_pairs, dict) else {}).items()
                if isinstance(key, str) and isinstance(value, str)
                if (
                    cleaned := safety.safe_url(value.strip(" \t\n\r\f"))
                    if key.lower() == "canonical"
                    else sanitize.clean_field(value, sanitize.MAX_URL_CHARS)
                )
            },
            "lang": sanitize.clean_field(raw_meta.get("lang"), sanitize.MAX_LANG_CHARS),
            "canonical": safety.safe_url(raw_meta.get("canonical")),
        }
        status = self.http_status
        self.http_status = status if isinstance(status, int) and status > 0 else None


def _as_text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _clean_span(span: object) -> HiddenSpan | None:
    if not isinstance(span, dict):
        return None
    text, reason, path = span.get("text"), span.get("reason"), span.get("path")
    if not isinstance(text, str) or not isinstance(reason, str):
        return None
    cleaned: HiddenSpan = {
        "text": sanitize.clean_field(text, sanitize.MAX_EXCERPT_CHARS) or "",
        "reason": sanitize.clean_field(reason, sanitize.MAX_SPAN_REASON_CHARS) or "unknown",
    }
    if isinstance(path, str) and (
        location := sanitize.clean_field(path, sanitize.MAX_LOCATION_CHARS)
    ):
        cleaned["path"] = location
    return cleaned


def registrable_domain(url: str) -> str:
    ext = tldextract.extract(url)
    return ext.top_domain_under_public_suffix or ext.fqdn or urlparse(url).hostname or url


class _InflightRequests:
    def __init__(self) -> None:
        self._ids: set[str] = set()

    @property
    def busy(self) -> bool:
        return bool(self._ids)

    def _started(self, params: dict) -> None:
        request_id = params.get("requestId")
        if request_id:
            self._ids.add(request_id)

    def _finished(self, params: dict) -> None:
        request_id = params.get("requestId")
        if request_id:
            self._ids.discard(request_id)

    def attach(self, cdp: CDPClient, session_id: str) -> list[Callable[[], None]]:
        return [
            cdp.on_event("Network.requestWillBeSent", session_id, self._started),
            cdp.on_event("Network.loadingFinished", session_id, self._finished),
            cdp.on_event("Network.loadingFailed", session_id, self._finished),
        ]


class _MainResponse:
    def __init__(self) -> None:
        self._by_loader: dict[str, dict] = {}
        self._loader_id: str | None = None

    def _on_response(self, params: dict) -> None:
        response = params.get("response")
        if not isinstance(response, dict):
            return
        request_id = params.get("requestId")
        if not isinstance(request_id, str) or request_id != params.get("loaderId"):
            return
        # Server-side redirect hops arrive as `requestWillBeSent.redirectResponse`, so
        # exactly one responseReceived fires per requestId — for the final hop.
        # setdefault only guards a repeat that should not happen.
        self._by_loader.setdefault(request_id, response)

    def _on_frame_navigated(self, params: dict) -> None:
        frame = params.get("frame")
        if not isinstance(frame, dict) or frame.get("parentId"):
            return
        loader_id = frame.get("loaderId")
        if isinstance(loader_id, str):
            self._loader_id = loader_id

    def attach(self, cdp: CDPClient, session_id: str) -> list[Callable[[], None]]:
        return [
            cdp.on_event("Network.responseReceived", session_id, self._on_response),
            cdp.on_event("Page.frameNavigated", session_id, self._on_frame_navigated),
        ]

    def main(self, navigated_loader_id: object) -> dict | None:
        loader_id = self._loader_id if self._loader_id is not None else navigated_loader_id
        if isinstance(loader_id, str):
            return self._by_loader.get(loader_id)
        return None


class _ChallengeAssets:
    def __init__(self) -> None:
        self.seen = False

    def _on(self, params: dict) -> None:
        if self.seen:
            return
        request = params.get("request")
        url = request.get("url") if isinstance(request, dict) else None
        if classify.is_challenge_asset(url):
            self.seen = True

    def attach(self, cdp: CDPClient, session_id: str) -> list[Callable[[], None]]:
        return [cdp.on_event("Network.requestWillBeSent", session_id, self._on)]


def _headful_identity(user_agent: str, hints: dict) -> dict | None:
    if _HEADLESS_TOKEN not in user_agent:
        return None
    return {
        "userAgent": user_agent.replace(_HEADLESS_TOKEN, "Chrome/"),
        "userAgentMetadata": {key: hints[key] for key in _HINT_KEYS if key in hints},
    }


async def _read_identity(cdp: CDPClient) -> dict | None:
    user_agent = (await cdp.send("Browser.getVersion"))["userAgent"]
    if _HEADLESS_TOKEN not in user_agent:
        return None
    target = await cdp.send("Target.createTarget", {"url": _HINTS_PAGE})
    try:
        att = await cdp.send(
            "Target.attachToTarget", {"targetId": target["targetId"], "flatten": True}
        )
        sid = att["sessionId"]
        loaded = cdp.expect_event("Page.loadEventFired", session_id=sid)
        await cdp.send("Page.enable", session_id=sid)
        await cdp.send("Page.reload", session_id=sid)
        await asyncio.wait_for(loaded, timeout=_GOTO_TIMEOUT_S)
        result = await cdp.send(
            "Runtime.evaluate",
            {"expression": _HINTS_EXPR, "awaitPromise": True, "returnByValue": True},
            session_id=sid,
        )
        value = result.get("result", {}).get("value")
        if not isinstance(value, str):
            raise ValueError("the browser returned no client hints")
        return _headful_identity(user_agent, json.loads(value))
    finally:
        await cdp.send("Target.closeTarget", {"targetId": target["targetId"]})


class _PrivateDocuments:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self.blocked: str | None = None
        self._pending: set[asyncio.Task[None]] = set()

    async def _decide(self, cdp: CDPClient, session_id: str, params: dict) -> None:
        request = params.get("request")
        url = request.get("url") if isinstance(request, dict) else None
        refusal: str | None = "NameNotResolved"
        try:
            if not (isinstance(url, str) and urlparse(url).scheme in ("http", "https")):
                refusal = None
            else:
                await safety.check_url(url, self._cfg)
                refusal = None
        except safety.BlockedURLError as exc:
            self.blocked = str(exc)
            refusal = "AddressUnreachable"
        except (OSError, ValueError):
            pass
        finally:
            await self._resolve(cdp, session_id, params.get("requestId"), refusal)

    @staticmethod
    async def _resolve(
        cdp: CDPClient, session_id: str, request_id: object, refusal: str | None
    ) -> None:
        try:
            if refusal is None:
                await cdp.send(
                    "Fetch.continueRequest", {"requestId": request_id}, session_id=session_id
                )
            else:
                await cdp.send(
                    "Fetch.failRequest",
                    {"requestId": request_id, "errorReason": refusal},
                    session_id=session_id,
                )
        except (CDPError, OSError, WebSocketException):
            pass

    def attach(self, cdp: CDPClient, session_id: str) -> list[Callable[[], None]]:
        def on_paused(params: dict) -> None:
            task = asyncio.ensure_future(self._decide(cdp, session_id, params))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)

        return [cdp.on_event("Fetch.requestPaused", session_id, on_paused)]


class _ChildFrames:
    def __init__(
        self,
        prepare: Callable[[CDPClient, str], Awaitable[list[Callable[[], None]]]],
        *,
        guarded: bool,
    ) -> None:
        self._prepare = prepare
        self._guarded = guarded
        self._closed = False
        self._unsubscribes: list[Callable[[], None]] = []
        self._pending: set[asyncio.Task[None]] = set()

    async def _adopt(self, cdp: CDPClient, params: dict) -> None:
        child = params.get("sessionId")
        if not isinstance(child, str):
            return
        target = params.get("targetInfo")
        if not isinstance(target, dict) or target.get("type") != "iframe":
            await self._release(cdp, child)
            return
        if self._closed:
            return
        prepared = False
        try:
            self._watch(cdp, child)
            added = await self._prepare(cdp, child)
            if self._closed:
                for unsubscribe in added:
                    unsubscribe()
                return
            self._unsubscribes += added
            await cdp.send("Target.setAutoAttach", _FRAME_AUTO_ATTACH, session_id=child)
            prepared = True
        except (CDPError, OSError, WebSocketException):
            pass
        finally:
            if prepared or not self._guarded:
                await self._release(cdp, child)

    @staticmethod
    async def _release(cdp: CDPClient, session_id: str) -> None:
        try:
            await cdp.send("Runtime.runIfWaitingForDebugger", session_id=session_id)
        except (CDPError, OSError, WebSocketException):
            pass

    def _watch(self, cdp: CDPClient, session_id: str) -> None:
        def on_attached(params: dict) -> None:
            task = asyncio.ensure_future(self._adopt(cdp, params))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)

        self._unsubscribes.append(cdp.on_event("Target.attachedToTarget", session_id, on_attached))

    def _detach(self) -> None:
        self._closed = True
        unsubscribes, self._unsubscribes = self._unsubscribes, []
        for unsubscribe in unsubscribes:
            unsubscribe()

    def attach(self, cdp: CDPClient, session_id: str) -> list[Callable[[], None]]:
        self._watch(cdp, session_id)
        return [self._detach]


class EngineProvider:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._cdp: CDPClient | None = None
        self._rl = RateLimiter(cfg.min_delay_ms / 1000)
        self._pages = asyncio.Semaphore(cfg.max_concurrent_pages)
        self._reconnect_lock = asyncio.Lock()
        self._identity: dict | None = None
        self._identity_settled = False
        self._identity_failures = 0
        self._identity_lock = asyncio.Lock()

    async def start(self) -> None:
        ws_url = await self._resolve_ws()
        self._cdp = CDPClient(ws_url)
        try:
            await self._cdp.connect()
        except (OSError, WebSocketException) as exc:
            raise BrowserUnavailableError(remediation(self._cfg)) from exc
        self._identity, self._identity_settled, self._identity_failures = None, False, 0

    async def _ensure_identity(self) -> None:
        assert self._cdp is not None
        async with self._identity_lock:
            if self._identity_settled:
                return
            try:
                self._identity = await asyncio.wait_for(
                    _read_identity(self._cdp), timeout=_IDENTITY_TIMEOUT_S
                )
                self._identity_settled = True
            except (CDPError, OSError, ValueError, KeyError, WebSocketException):
                self._identity_failures += 1
                if self._identity_failures >= _IDENTITY_ATTEMPTS:
                    self._identity_settled = True
                    print(
                        "[groundhog] could not read the browser's client hints; "
                        "pages will see it as HeadlessChrome",
                        file=sys.stderr,
                    )

    async def _resolve_ws(self) -> str:
        cfg = self._cfg
        try:
            return await _browser_ws_url(cfg.cdp_url)
        except OSError as exc:
            if not (cfg.auto_start_browser and _is_local(cfg.cdp_url)):
                raise BrowserUnavailableError(remediation(cfg)) from exc
        await _start_browser(cfg)
        try:
            return await _browser_ws_url(cfg.cdp_url)
        except OSError as exc:
            raise BrowserUnavailableError(remediation(cfg)) from exc

    async def _ensure_connected(self) -> None:
        if self._cdp is not None and not self._cdp.closed:
            return
        async with self._reconnect_lock:
            if self._cdp is None or self._cdp.closed:
                if self._cdp is not None:
                    await self._cdp.close()
                await self.start()

    async def fetch(self, url: str, strip_hidden: bool = True) -> RenderedPage:
        await safety.check_url(url, self._cfg)
        await self._ensure_connected()
        async with self._rl.turn(registrable_domain(url)):
            await self._pages.acquire()
        try:
            return await self._fetch_in_target(url, strip_hidden)
        finally:
            self._pages.release()

    async def _prepare_tab(self, sid: str) -> None:
        assert self._cdp is not None
        await self._ensure_identity()
        if self._identity is not None:
            await self._cdp.send("Emulation.setUserAgentOverride", self._identity, session_id=sid)
        await self._cdp.send("Page.enable", session_id=sid)
        await self._cdp.send("Network.enable", session_id=sid)
        if self._cfg.block_private_ips:
            await self._cdp.send(
                "Fetch.enable",
                {"patterns": [{"urlPattern": "*", "resourceType": "Document"}]},
                session_id=sid,
            )
        if self._identity is not None or self._cfg.block_private_ips:
            await self._cdp.send("Target.setAutoAttach", _FRAME_AUTO_ATTACH, session_id=sid)

    async def _prepare_frame(self, cdp: CDPClient, sid: str) -> list[Callable[[], None]]:
        unsubscribes: list[Callable[[], None]] = []
        if self._identity is not None:
            await cdp.send("Emulation.setUserAgentOverride", self._identity, session_id=sid)
        if self._cfg.block_private_ips:
            unsubscribes += _PrivateDocuments(self._cfg).attach(cdp, sid)
            try:
                await cdp.send(
                    "Fetch.enable",
                    {"patterns": [{"urlPattern": "*", "resourceType": "Document"}]},
                    session_id=sid,
                )
            except (CDPError, OSError, WebSocketException):
                for unsubscribe in unsubscribes:
                    unsubscribe()
                raise
        return unsubscribes

    async def _fetch_in_target(self, url: str, strip_hidden: bool) -> RenderedPage:
        assert self._cdp is not None
        target = await self._cdp.send("Target.createTarget", {"url": "about:blank"})
        tid = target["targetId"]
        att = await self._cdp.send("Target.attachToTarget", {"targetId": tid, "flatten": True})
        sid = att["sessionId"]
        inflight = _InflightRequests()
        responses = _MainResponse()
        challenge_assets = _ChallengeAssets()
        private_documents = _PrivateDocuments(self._cfg)
        child_frames = _ChildFrames(self._prepare_frame, guarded=self._cfg.block_private_ips)
        unsubscribes = (
            inflight.attach(self._cdp, sid)
            + responses.attach(self._cdp, sid)
            + challenge_assets.attach(self._cdp, sid)
            + private_documents.attach(self._cdp, sid)
            + child_frames.attach(self._cdp, sid)
        )
        try:
            await self._prepare_tab(sid)
            # Re-check right before navigate: the rate-limiter/semaphore wait above plus
            # Chrome's own independent DNS resolution at nav time reopen a rebinding window.
            await safety.check_url(url, self._cfg)
            loaded = self._cdp.expect_event("Page.domContentEventFired", session_id=sid)
            try:
                nav = await self._cdp.send("Page.navigate", {"url": url}, session_id=sid)
                if nav.get("errorText"):
                    if private_documents.blocked:
                        raise safety.BlockedURLError(private_documents.blocked)
                    reason = sanitize.clean_field(str(nav["errorText"]), sanitize.MAX_ERROR_CHARS)
                    raise CDPError(f"navigation failed: {reason or 'unknown error'}")
                await asyncio.wait_for(loaded, timeout=_GOTO_TIMEOUT_S)
            finally:
                loaded.cancel()
            await self._settle(sid, inflight)

            ctx = await self._isolated_context(sid)

            final_url = _as_text(await self._eval(sid, "document.location.href", ctx))
            await safety.check_url(final_url, self._cfg)
            detect_expr = f"({DETECT_AND_COLLECT})({json.dumps(strip_hidden)})"
            try:
                collected = await asyncio.wait_for(
                    self._eval(sid, detect_expr, ctx), timeout=_DETECT_TIMEOUT_S
                )
            except TimeoutError as exc:
                raise CDPError("hidden-text detection timed out") from exc
            # The collector's own result is page-world data too: a reply carrying no
            # `value` at all arrives as None, so index it defensively rather than
            # letting a TypeError surface as the fetch's error message.
            found = collected if isinstance(collected, dict) else {}
            main = responses.main(nav.get("loaderId"))
            http_status = main.get("status") if main else None
            retrieval_status = classify.classify(
                main, challenge_assets.seen, found.get("title"), found.get("text")
            )
            return RenderedPage(
                html=_as_text(found.get("html")),
                text=_as_text(found.get("text")),
                final_url=final_url,
                title=_as_text(found.get("title")),
                isolated=ctx is not None,
                strip_incomplete=bool(found.get("stripIncomplete")),
                spans_dropped=found.get("spansDropped") or 0,
                http_status=http_status,
                retrieval_status=retrieval_status,
                hidden_spans=found.get("hidden") or [],
                meta={
                    "meta": found.get("meta") or {},
                    "lang": found.get("lang"),
                    "canonical": found.get("canonical"),
                },
            )
        finally:
            for unsubscribe in unsubscribes:
                unsubscribe()
            if not self._cdp.closed:
                await self._cdp.send("Target.closeTarget", {"targetId": tid})

    async def _settle(self, session_id: str, inflight: _InflightRequests) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _SETTLE_TIMEOUT_S
        prev: object = None
        quiet_since = loop.time()
        ctx = await self._isolated_context(session_id)
        while loop.time() < deadline:
            try:
                current = await self._eval(session_id, _DOM_SIZE_EXPR, ctx)
            except CDPError:
                ctx = await self._isolated_context(session_id)
                prev, quiet_since = None, loop.time()
                await asyncio.sleep(_SETTLE_POLL_S)
                continue
            if current != prev or inflight.busy:
                prev = current
                quiet_since = loop.time()
            elif loop.time() - quiet_since >= _SETTLE_QUIET_S:
                return
            await asyncio.sleep(_SETTLE_POLL_S)

    async def _isolated_context(self, session_id: str) -> int | None:
        try:
            tree = await asyncio.wait_for(
                self._cdp.send("Page.getFrameTree", session_id=session_id),
                timeout=_EVAL_TIMEOUT_S,
            )
            frame_id = tree["frameTree"]["frame"]["id"]
            world = await asyncio.wait_for(
                self._cdp.send(
                    "Page.createIsolatedWorld",
                    {"frameId": frame_id, "worldName": _ISOLATED_WORLD},
                    session_id=session_id,
                ),
                timeout=_EVAL_TIMEOUT_S,
            )
            context_id = world.get("executionContextId")
            return context_id if isinstance(context_id, int) else None
        except (CDPError, KeyError, TypeError, TimeoutError):
            if self._cdp.closed:
                raise
            return None

    async def _eval(
        self, session_id: str, expression: str, context_id: int | None = None
    ) -> object:
        res = await asyncio.wait_for(
            self._cdp.send(
                "Runtime.evaluate",
                {
                    "expression": expression,
                    "returnByValue": True,
                    "awaitPromise": True,
                    **({"contextId": context_id} if context_id is not None else {}),
                },
                session_id=session_id,
            ),
            timeout=_EVAL_TIMEOUT_S,
        )
        if "exceptionDetails" in res:
            details = res["exceptionDetails"]
            thrown = details.get("exception") if isinstance(details, dict) else None
            message = (
                thrown.get("description") or thrown.get("value")
                if isinstance(thrown, dict)
                else None
            )
            detail = sanitize.clean_field(str(message or details), sanitize.MAX_ERROR_CHARS)
            raise CDPError(detail or "page evaluation failed")
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
            _provider = EngineProvider(load_config())
    return _provider


async def shutdown_provider() -> None:
    global _provider
    async with _provider_lock:
        if _provider is not None:
            await _provider.aclose()
            _provider = None
