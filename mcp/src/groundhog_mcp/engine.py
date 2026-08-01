import asyncio
import ipaddress
import json
import shlex
import shutil
import socket
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import NotRequired, TypedDict
from urllib.parse import urlparse

import tldextract

from . import http, safety, sanitize
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
# Element count + visible-text length: cheap to sample, and one of them moves
# whenever an SPA hydrates or streams content in.
_DOM_SIZE_EXPR = (
    "document.querySelectorAll('*').length + '|' + "
    "(document.body ? document.body.innerText.length : 0)"
)
_PROBE_TIMEOUT_S = 2.0
_AUTOSTART_READY_TRIES = 30
_VERSION_PATH = "/json/version"
_CONTAINER_NAME = "groundhog-browser"
_CONTAINER_SHM = "512m"
_CONTAINER_CDP_PORT = 9222
_CONTAINER_BIND_HOST = "127.0.0.1"  # never bind the auto-started CDP to a public interface
_RUNTIMES = ("docker", "podman")
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
_ISOLATED_WORLD = "groundhog"


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
    try:
        return urlparse(cdp_url).port or _CONTAINER_CDP_PORT
    except ValueError:
        # A non-numeric port is a misconfiguration; the remediation hint that says so
        # must not itself raise on the way out.
        return _CONTAINER_CDP_PORT


def _run_argv(runtime: str, cfg: Config) -> list[str]:
    """The command that starts the browser.

    Built once so the message telling a user how to start it cannot drift from
    what auto-start actually runs — they did, over `--shm-size`.
    """
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


def remediation(cfg: Config) -> str:
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
    if host in _LOCAL_HOSTS:
        return cdp_url
    try:
        ipaddress.ip_address(host)
        return cdp_url
    except ValueError:
        pass
    infos = socket.getaddrinfo(host, parts.port, proto=socket.IPPROTO_TCP)
    family, _, _, _, addr = min(infos, key=lambda info: info[0] != socket.AF_INET)  # prefer IPv4
    netloc = f"[{addr[0]}]" if family == socket.AF_INET6 else addr[0]
    if parts.port:
        netloc += f":{parts.port}"
    return parts._replace(netloc=netloc).geturl()


async def _fetch_version(cdp_url: str, timeout: float) -> dict:
    """Read the CDP `/json/version` document off the event loop."""
    return await asyncio.get_running_loop().run_in_executor(
        None,
        # _ip_probe_url resolves DNS, which also blocks — keep it in the executor.
        lambda: http.read_json(_ip_probe_url(cdp_url).rstrip("/") + _VERSION_PATH, timeout),
    )


async def check_browser(cdp_url: str, timeout: float = _PROBE_TIMEOUT_S) -> bool:
    """Return whether the CDP endpoint answers its `/json/version` probe."""
    try:
        return "webSocketDebuggerUrl" in await _fetch_version(cdp_url, timeout)
    except OSError:
        return False  # refused / DNS / timeout all mean "not reachable"
    except ValueError:
        # A malformed CDP_URL (`http://host:abc`) raises out of `urlparse`. The tool
        # whose job is reporting a broken configuration must survive one.
        return False


async def _browser_ws_url(cdp_url: str, timeout: float = _PROBE_TIMEOUT_S) -> str:
    return (await _fetch_version(cdp_url, timeout))["webSocketDebuggerUrl"]


async def _run(cmd: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    # Runtime stderr is not page-authored, but it reaches the model in the
    # remediation message — bound it with the same rule as everything else.
    detail = sanitize.clean_field(stderr.decode(errors="replace"), sanitize.MAX_ERROR_CHARS)
    return proc.returncode or 0, detail or ""


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
        cmd = _run_argv(runtime, cfg)
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
        f"Browser container started but {safety.redacted_url(cfg.cdp_url)} "
        "did not become ready in time."
    )


class HiddenSpan(TypedDict):
    """A span the page hid from humans, as reported by the in-page collector."""

    text: str
    reason: str
    path: NotRequired[str]


class PageMeta(TypedDict):
    """Document-level metadata the in-page collector reads off the live DOM."""

    meta: dict[str, str]
    lang: str | None
    canonical: str | None


@dataclass
class RenderedPage:
    """One page as the browser rendered it — this process's untrusted-input boundary.

    Every string here is written by the page, so they are sanitized on construction
    rather than at each place that later reads them. Doing it per consumer made the
    rule something a call site had to remember, and fields kept being added that
    quietly didn't: the title, then the metadata, then the threat excerpts, then the
    detected language and the final URL.

    Every page-authored field is handled explicitly below, so one added here must be
    added there too — the result walk in `tests/test_boundary.py` is what catches the
    omission. Two carve-outs: `html` and `text` are only type-checked, because they
    carry the content itself and are stripped downstream *with* threat collection so
    the caller learns what was hidden in it; and the boolean flags are ours, not the
    page's — they record how the reads above were performed, not what they returned.
    """

    html: str
    text: str
    final_url: str
    title: str
    hidden_spans: list[HiddenSpan]
    meta: PageMeta
    # Whether the reads above ran in an isolated world. False means the page could
    # have suppressed its own hidden-text report, and the caller is told so. No
    # default: a construction site that forgets it must say which it means, rather
    # than silently asserting the reassuring answer.
    isolated: bool
    # Whether the strip fell short of removing a flagged node outright — the cases are
    # enumerated in `detect_js.py`, which is where they are detected. Same no-default
    # rule as `isolated`.
    strip_incomplete: bool

    def __post_init__(self) -> None:
        # A URL is a citation, so it is never rewritten: if cleaning would change
        # it, drop it and let the caller fall back to the URL it asked for. Same
        # rule the search backend applies to a hit's URL.
        # `html`/`text` carry the content and are stripped downstream with threat
        # collection; they still have to *be* text, since a page can shadow the
        # getters these are read from.
        self.html, self.text = _as_text(self.html), _as_text(self.text)
        self.final_url = safety.safe_url(self.final_url) or ""
        self.title = sanitize.clean_field(self.title, sanitize.MAX_TITLE_CHARS) or ""
        spans = self.hidden_spans if isinstance(self.hidden_spans, list) else []
        self.hidden_spans = [span for raw in spans if (span := _clean_span(raw))]
        raw_meta = self.meta if isinstance(self.meta, dict) else {}
        meta_pairs = raw_meta.get("meta")
        self.meta = {
            "meta": {
                key: cleaned
                for key, value in (meta_pairs if isinstance(meta_pairs, dict) else {}).items()
                if isinstance(key, str) and isinstance(value, str)
                # Bounded at URL length, not metadata length: `canonical` is read
                # back out of this map, and pre-truncating it here would leave
                # `safe_url` comparing against an already-shortened string and
                # calling a rewritten URL unchanged.
                if (cleaned := sanitize.clean_field(value, sanitize.MAX_URL_CHARS))
            },
            "lang": sanitize.clean_field(raw_meta.get("lang"), sanitize.MAX_LANG_CHARS),
            "canonical": safety.safe_url(raw_meta.get("canonical")),
        }


def _as_text(value: object) -> str:
    """A page-world eval result as text.

    Every expression here reads a DOM property a page can shadow with
    `Object.defineProperty`, so the declared `str` is a claim about the happy path.
    Anything else means no content, matching how `_clean_span` treats a malformed span.
    """
    return value if isinstance(value, str) else ""


def _clean_span(span: object) -> HiddenSpan | None:
    """Clean one hidden span to the size its threat report uses, or reject it.

    `HiddenSpan` describes what the collector is *supposed* to return, but the
    collector runs in the page's own JS world, so the shape is a claim rather than
    a guarantee — indexing it directly turns a patched `Array.prototype.push` into
    an unhandled crash on the untrusted-input boundary. Anything malformed is
    dropped rather than trusted.
    """
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
    """The key requests are grouped under, for rate limiting and source diversity.

    Falls back to the bare host for addresses with no public suffix (IPs,
    `localhost`, unknown TLDs) — grouping on the full URL instead would give
    every path its own bucket and effectively disable both.
    """
    ext = tldextract.extract(url)
    return ext.top_domain_under_public_suffix or ext.fqdn or urlparse(url).hostname or url


class _InflightRequests:
    """Tracks a session's in-flight network requests by CDP request id.

    Ids rather than a counter: `Network.requestWillBeSent` re-fires per redirect
    hop with the same request id while `loadingFinished`/`loadingFailed` fire
    once, so a counter would drift upward and never return to zero.
    """

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


class EngineProvider:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._cdp: CDPClient | None = None
        self._rl = RateLimiter(cfg.min_delay_ms / 1000)
        self._pages = asyncio.Semaphore(cfg.max_concurrent_pages)
        self._reconnect_lock = asyncio.Lock()

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

    async def _ensure_connected(self) -> None:
        """Reconnect if the browser dropped the CDP socket.

        The MCP process outlives browser containers (Docker restarts, image
        upgrades); the endpoint can answer HTTP probes while this process's
        websocket is dead — without this, every fetch fails until a restart.
        """
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
        await self._rl.acquire(registrable_domain(url))
        async with self._pages:
            return await self._fetch_in_target(url, strip_hidden)

    async def _fetch_in_target(self, url: str, strip_hidden: bool) -> RenderedPage:
        assert self._cdp is not None
        target = await self._cdp.send("Target.createTarget", {"url": "about:blank"})
        tid = target["targetId"]
        att = await self._cdp.send("Target.attachToTarget", {"targetId": tid, "flatten": True})
        sid = att["sessionId"]
        inflight = _InflightRequests()
        unsubscribes = inflight.attach(self._cdp, sid)
        try:
            # Only Page and Network are enabled — never Runtime/Console, which would
            # expose the CDP session to the page as the `isAutomatedWithCDP` signal.
            await self._cdp.send("Page.enable", session_id=sid)
            await self._cdp.send("Network.enable", session_id=sid)
            # Re-check right before navigate: the rate-limiter/semaphore wait above plus
            # Chrome's own independent DNS resolution at nav time reopen a rebinding window.
            await safety.check_url(url, self._cfg)
            loaded = self._cdp.expect_event("Page.domContentEventFired", session_id=sid)
            try:
                nav = await self._cdp.send("Page.navigate", {"url": url}, session_id=sid)
                if nav.get("errorText"):
                    reason = sanitize.clean_field(str(nav["errorText"]), sanitize.MAX_ERROR_CHARS)
                    raise CDPError(f"navigation failed: {reason or 'unknown error'}")
                await asyncio.wait_for(loaded, timeout=_GOTO_TIMEOUT_S)
            finally:
                # Navigation can fail before the load event is ever awaited; the
                # waiter deregisters itself once cancelled.
                loaded.cancel()
            await self._settle(sid, inflight)

            # Created after the last navigation, so it belongs to the document being
            # read. Everything below evaluates here rather than in the page's world.
            ctx = await self._isolated_context(sid)

            # `_eval` returns whatever the page produced. Narrow before use: a
            # shadowed `location.href` reaching `urlparse` raises out of the guard
            # instead of being checked by it.
            final_url = _as_text(await self._eval(sid, "document.location.href", ctx))
            # A page can redirect to an internal address the initial check never saw;
            # re-check the final URL so its content is never returned.
            await safety.check_url(final_url, self._cfg)
            detect_expr = f"({DETECT_AND_COLLECT})({json.dumps(strip_hidden)})"
            try:
                collected = await asyncio.wait_for(
                    self._eval(sid, detect_expr, ctx), timeout=_DETECT_TIMEOUT_S
                )
            except TimeoutError as exc:
                # An adversarial page (deeply nested, huge DOM) could otherwise force
                # unbounded style-recalc work here; fail this fetch instead of hanging
                # the page's concurrency slot indefinitely.
                raise CDPError("hidden-text detection timed out") from exc
            # The collector's own result is page-world data too: a reply carrying no
            # `value` at all arrives as None, so index it defensively rather than
            # letting a TypeError surface as the fetch's error message.
            found = collected if isinstance(collected, dict) else {}
            # `html`/`text`/`title` come out of the collector's own evaluation rather
            # than from separate round trips: see the note in `detect_js.py`.
            return RenderedPage(
                html=_as_text(found.get("html")),
                text=_as_text(found.get("text")),
                final_url=final_url,
                title=_as_text(found.get("title")),
                isolated=ctx is not None,
                # Any non-false reply degrades to "say the strip was partial", so a
                # page cannot quiet the disclosure by returning a surprising shape.
                strip_incomplete=bool(found.get("stripIncomplete")),
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
            await self._cdp.send("Target.closeTarget", {"targetId": tid})

    async def _settle(self, session_id: str, inflight: _InflightRequests) -> None:
        """Wait until the network is quiet and the DOM stops changing.

        DOMContentLoaded fires on an SPA's empty shell. In-flight data fetches
        hold the wait open; the _SETTLE_QUIET_S window catches the render work
        and short timers that follow them; _SETTLE_TIMEOUT_S caps the whole wait.

        Polled from an isolated world, so a page instrumenting `querySelectorAll`
        cannot count the probes and learn that extraction is underway — which would
        hand it the timing signal for everything that happens next.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _SETTLE_TIMEOUT_S
        prev: object = None
        quiet_since = loop.time()
        ctx = await self._isolated_context(session_id)
        while loop.time() < deadline:
            try:
                current = await self._eval(session_id, _DOM_SIZE_EXPR, ctx)
            except CDPError:
                # A navigation during settle destroys the context. Rebuild it against
                # the new document and treat this poll as a change.
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
        """An execution context the page's own JavaScript cannot reach.

        The collector reads the DOM through `getComputedStyle`, `createTreeWalker`
        and `Array.prototype.push` — all replaceable from the page. Evaluated in the
        main world, a page could therefore silently suppress its own hidden-text
        report. An isolated world shares the DOM but gets fresh JS globals, so those
        substitutions do not apply to it.

        This does not enable the `Runtime` domain, so the `isAutomatedWithCDP` signal
        stays absent. Returns None if the browser will not provide one, and the
        caller degrades to the main world and says so.
        """
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
            # Both commands are serviced by the renderer, so a wedged page would
            # otherwise hold a concurrency slot here indefinitely.
            if self._cdp.closed:
                raise  # a dead socket is infrastructure, not reduced detection
            return None

    async def _eval(
        self, session_id: str, expression: str, context_id: int | None = None
    ) -> object:
        # Bounded: a page that wedges its renderer after load (a spinning timer, a
        # modal dialog nothing dismisses) would otherwise hang this await forever
        # while holding one of the `max_concurrent_pages` slots.
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
            # The eval runs in the page's own world, so a page can throw with a
            # payload of its choosing — this text can reach the caller's model.
            detail = sanitize.clean_field(str(res["exceptionDetails"]), sanitize.MAX_ERROR_CHARS)
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
            # Constructed, not connected: `fetch` validates the URL before
            # `_ensure_connected` starts anything, so a URL the SSRF guard rejects
            # never auto-starts a container.
            _provider = EngineProvider(load_config())
    return _provider


async def shutdown_provider() -> None:
    global _provider
    async with _provider_lock:
        if _provider is not None:
            await _provider.aclose()
            _provider = None
