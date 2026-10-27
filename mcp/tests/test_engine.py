import asyncio
import base64
import dataclasses
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import urllib.response
from collections.abc import Callable
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import requests
import tldextract
import websockets

from groundhog_mcp import cdp, engine, extract, http, provenance
from groundhog_mcp.config import Config
from groundhog_mcp.engine import _run


def _cfg(**over):
    base = dict(
        cdp_url="http://127.0.0.1:9222",
        min_delay_ms=0,
        block_private_ips=True,
        max_tokens=1000,
        auto_start_browser=True,
        compose_file=None,
        browser_image="ghcr.io/x/y:latest",
        max_concurrent_pages=1,
        search_backend="auto",
        searxng_url=None,
        browser="stealth",
        chrome_path=None,
        chrome_profile="/tmp/groundhog-test-profile",
    )
    base.update(over)
    return Config(**base)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://127.0.0.1:9222", True),
        ("http://localhost:9222", True),
        ("http://[::1]:9222", True),
        ("http://example.com:9222", False),
        ("http://10.0.0.5:9222", False),
    ],
)
def test_is_local(url, expected):
    assert engine._is_local(url) is expected


@pytest.mark.parametrize(
    "url,port",
    [("http://127.0.0.1:9222", 9222), ("http://127.0.0.1:7000", 7000), ("http://127.0.0.1", 9222)],
)
def test_port_of(url, port):
    assert engine._port_of(url) == port


def test_container_runtime_prefers_docker(monkeypatch):
    monkeypatch.setattr(
        engine.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "docker" else None
    )
    assert engine._container_runtime() == "docker"


def test_container_runtime_falls_back_to_podman(monkeypatch):
    monkeypatch.setattr(
        engine.shutil, "which", lambda name: "/usr/bin/podman" if name == "podman" else None
    )
    assert engine._container_runtime() == "podman"


def test_container_runtime_none(monkeypatch):
    monkeypatch.setattr(engine.shutil, "which", lambda name: None)
    assert engine._container_runtime() is None


def _stub_start(monkeypatch, calls, runtime="docker", code=0):
    async def fake_run(cmd):
        calls.append(cmd)
        return code, "boom" if code else ""

    async def ready(url, timeout=engine._PROBE_TIMEOUT_S):
        return True

    monkeypatch.setattr(engine, "_container_runtime", lambda: runtime)
    monkeypatch.setattr(engine, "_run", fake_run)
    monkeypatch.setattr(engine, "check_browser", ready)


async def test_start_browser_builds_docker_run(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    _stub_start(monkeypatch, calls)
    await engine._start_browser(_cfg(cdp_url="http://127.0.0.1:7000", browser_image="img:tag"))
    assert calls[0] == ["docker", "rm", "-f", engine._CONTAINER_NAME]  # clears a stale container
    run = calls[1]
    assert run[:3] == ["docker", "run", "-d"]
    assert engine._CONTAINER_NAME in run
    assert "127.0.0.1:7000:9222" in run
    assert "--platform" not in run  # multi-arch image: let the runtime pick the native arch
    assert run[-2:] == ["--", "img:tag"]  # `--` guards against a flag-like image ref


def test_ip_probe_url_keeps_ip_and_localhost():
    assert engine._ip_probe_url("http://127.0.0.1:9222") == "http://127.0.0.1:9222"
    assert engine._ip_probe_url("http://localhost:9222") == "http://localhost:9222"
    assert engine._ip_probe_url("http://[::1]:9222") == "http://[::1]:9222"


def test_ip_probe_url_resolves_dns_names(monkeypatch):
    monkeypatch.setattr(
        engine.socket,
        "getaddrinfo",
        lambda *a, **k: [(engine.socket.AF_INET, None, None, "", ("172.25.0.2", 0))],
    )
    assert engine._ip_probe_url("http://chrome:9222") == "http://172.25.0.2:9222"


@pytest.mark.parametrize("probe", [engine.check_browser, engine._browser_ws_url])
async def test_a_dns_cdp_url_is_resolved_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch, probe: Callable[[str], object]
) -> None:
    resolved_on: list[threading.Thread] = []

    def resolve(*args: object, **kwargs: object) -> list[tuple[object, ...]]:
        resolved_on.append(threading.current_thread())
        return [(engine.socket.AF_INET, None, None, "", ("172.25.0.2", 0))]

    monkeypatch.setattr(engine.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(
        http._opener,
        "open",
        lambda url, timeout: io.BytesIO(b'{"webSocketDebuggerUrl":"ws://172.25.0.2:9222/b"}'),
    )
    await probe("http://chrome:9222")
    assert resolved_on and threading.main_thread() not in resolved_on


def test_a_dns_name_with_both_address_families_is_dialed_over_ipv4(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        engine.socket,
        "getaddrinfo",
        lambda *a, **k: [
            (engine.socket.AF_INET6, None, None, "", ("fd00::2", 0, 0, 0)),
            (engine.socket.AF_INET, None, None, "", ("172.25.0.2", 0)),
        ],
    )
    assert engine._ip_probe_url("http://chrome:9222") == "http://172.25.0.2:9222"


def test_inflight_requests_redirect_refire_does_not_leak():
    inflight = engine._InflightRequests()
    inflight._started({"requestId": "r1"})
    inflight._started({"requestId": "r1"})  # a redirect hop re-fires with the same id
    inflight._finished({"requestId": "r1"})
    assert inflight.busy is False


async def test_start_browser_uses_podman(monkeypatch):
    calls: list[list[str]] = []
    _stub_start(monkeypatch, calls, runtime="podman")
    await engine._start_browser(_cfg())
    assert calls[1][0] == "podman"


async def test_start_browser_compose_path(monkeypatch):
    calls: list[list[str]] = []
    _stub_start(monkeypatch, calls)
    await engine._start_browser(_cfg(compose_file="/x/docker-compose.yml"))
    assert calls == [["docker", "compose", "-f", "/x/docker-compose.yml", "up", "-d"]]


async def test_start_browser_run_failure_raises(monkeypatch):
    _stub_start(monkeypatch, [], code=1)
    with pytest.raises(engine.BrowserUnavailableError, match="Could not start"):
        await engine._start_browser(_cfg())


async def test_remote_cdp_url_is_not_auto_started(monkeypatch):
    async def boom(cfg):
        raise AssertionError("must not auto-start a remote CDP_URL")

    monkeypatch.setattr(engine, "_start_browser", boom)
    provider = engine.EngineProvider(_cfg(cdp_url="http://cdp.invalid:9222"))
    try:
        with pytest.raises(engine.BrowserUnavailableError):
            await provider.start()
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://sub.example.co.uk/path", "example.co.uk"),  # multi-label public suffix
        ("https://example.com/path", "example.com"),
        ("http://127.0.0.1:9222/", "127.0.0.1"),  # no public suffix: bare host
        ("http://localhost:8080/x", "localhost"),
        ("https://a.unknowntld/1", "a.unknowntld"),  # grouping must not become per-URL
    ],
)
def test_registrable_domain(url, expected):
    assert engine.registrable_domain(url) == expected


async def test_an_unfired_event_waiter_does_not_accumulate():
    # A navigation that times out, or fails before the load event, would otherwise
    # retain its waiter for the life of the connection — once per such fetch.
    client = engine.CDPClient("ws://unused")
    for _ in range(5):
        waiter = client.expect_event("Page.domContentEventFired", session_id="s1")
        waiter.cancel()
        await asyncio.sleep(0)
    assert client._event_waiters == {}


@pytest.mark.parametrize("value", [None, 5, {"a": 1}, ["x"], b"bytes"])
def test_a_non_string_eval_result_becomes_empty_text(value):
    # Every expression the engine evaluates reads a DOM property a page can shadow
    # with `Object.defineProperty`, and a CDP reply carrying no `value` arrives as
    # None — both would otherwise raise inside `urlparse` or the sanitizer.
    assert engine._as_text(value) == ""


def test_a_string_eval_result_passes_through():
    assert engine._as_text("https://ex.com/") == "https://ex.com/"


@pytest.mark.parametrize(
    "cdp_url,probe",
    [
        ("http://127.0.0.1:9222", "http://127.0.0.1:9222/json/version"),
        ("http://127.0.0.1:9222/", "http://127.0.0.1:9222/json/version"),
        ("https://cdp.example.com/?token=abc", "https://cdp.example.com/json/version?token=abc"),
        (
            "https://cdp.example.com/base?a=1&b=2",
            "https://cdp.example.com/base/json/version?a=1&b=2",
        ),
    ],
)
def test_the_version_probe_keeps_everything_the_url_carries(cdp_url, probe):
    assert engine._version_url(cdp_url) == probe


def test_a_tls_endpoint_is_dialled_by_its_name_not_its_address(monkeypatch):
    monkeypatch.setattr(engine.socket, "getaddrinfo", lambda *a, **k: pytest.fail("resolved"))
    assert engine._ip_probe_url("https://cdp.example.com/") == "https://cdp.example.com/"


@pytest.mark.parametrize("cdp_url", ["wss://cdp.example.com/?token=abc", "ws://10.0.0.5:3000/x"])
def test_a_websocket_url_is_connected_to_exactly_as_given(cdp_url):
    assert asyncio.run(engine._browser_ws_url(cdp_url)) == cdp_url


def test_a_websocket_endpoint_that_answers_is_reachable():
    async def scenario() -> tuple[bool, bool]:
        async def accept(ws) -> None:
            await ws.wait_closed()

        async with websockets.serve(accept, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            up = await engine.check_browser(f"ws://127.0.0.1:{port}/devtools/browser/x?token=t")
        down = await engine.check_browser(f"ws://127.0.0.1:{port}/devtools/browser/x")
        return up, down

    assert asyncio.run(scenario()) == (True, False)


def test_a_websocket_endpoint_that_refuses_the_handshake_is_not_reachable():
    async def scenario() -> bool:
        async def reject(connection, request):
            return connection.respond(401, "no\n")

        async with websockets.serve(
            lambda ws: None, "127.0.0.1", 0, process_request=reject
        ) as server:
            port = server.sockets[0].getsockname()[1]
            return await engine.check_browser(f"ws://127.0.0.1:{port}/?token=wrong")

    assert asyncio.run(scenario()) is False


def test_a_websocket_endpoint_that_cannot_be_reached_is_reported_without_its_credential():
    async def scenario() -> str:
        async def reject(connection, request):
            return connection.respond(401, "no\n")

        async with websockets.serve(
            lambda ws: None, "127.0.0.1", 0, process_request=reject
        ) as server:
            port = server.sockets[0].getsockname()[1]
            provider = engine.EngineProvider(_cfg(cdp_url=f"ws://127.0.0.1:{port}/?token=SECRET"))
            with pytest.raises(engine.BrowserUnavailableError) as raised:
                await provider.start()
        return str(raised.value)

    message = asyncio.run(scenario())
    assert "SECRET" not in message and "127.0.0.1" in message


def _stub_chrome(monkeypatch, launched, ready=True):
    def fake_launch(argv):
        launched.append(argv)

    async def answers(url, timeout=engine._PROBE_TIMEOUT_S):
        return ready

    async def no_docker(cmd):
        pytest.fail(f"the chrome backend ran {cmd}")

    monkeypatch.setattr(engine, "_launch_detached", fake_launch)
    monkeypatch.setattr(engine, "_is_executable", lambda path: True)
    monkeypatch.setattr(engine, "check_browser", answers)
    monkeypatch.setattr(engine, "_run", no_docker)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(engine.asyncio, "sleep", lambda s: real_sleep(0))


async def test_the_chrome_backend_launches_the_installed_chrome_with_its_own_profile(
    monkeypatch, tmp_path
):
    launched: list[list[str]] = []
    _stub_chrome(monkeypatch, launched)
    profile = tmp_path / "chrome"
    cfg = _cfg(
        browser="chrome",
        chrome_path="/opt/chrome",
        chrome_profile=str(profile),
        cdp_url="http://127.0.0.1:7000",
    )
    await engine._start_browser(cfg)
    argv = launched[0]
    assert argv[0] == "/opt/chrome"
    assert "--remote-debugging-port=7000" in argv
    assert f"--user-data-dir={profile}" in argv
    assert "--headless=new" in argv
    assert "--window-size=1440,900" in argv
    assert profile.is_dir() and profile.stat().st_mode & 0o777 == 0o700


async def test_the_chrome_backend_finds_chrome_where_the_platform_installs_it(monkeypatch):
    monkeypatch.setattr(engine.sys, "platform", "darwin")
    mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    monkeypatch.setattr(engine.os.path, "isfile", lambda p: p == mac)
    monkeypatch.setattr(engine.os, "access", lambda p, mode: p == mac)
    assert engine._chrome_binary(_cfg(browser="chrome")) == mac

    monkeypatch.setattr(engine.sys, "platform", "linux")
    monkeypatch.setattr(engine.os.path, "isfile", lambda p: False)
    monkeypatch.setattr(
        engine.shutil, "which", lambda name: "/usr/bin/chromium" if name == "chromium" else None
    )
    assert engine._chrome_binary(_cfg(browser="chrome")) == "/usr/bin/chromium"


async def test_without_chrome_the_chrome_backend_says_how_to_get_a_browser(monkeypatch):
    launched: list[list[str]] = []
    _stub_chrome(monkeypatch, launched)
    monkeypatch.setattr(engine, "_chrome_binary", lambda cfg: None)
    with pytest.raises(engine.BrowserUnavailableError) as raised:
        await engine._start_browser(_cfg(browser="chrome"))
    assert launched == []
    assert "GROUNDHOG_CHROME_PATH" in str(raised.value)
    assert "GROUNDHOG_BROWSER=stealth" in str(raised.value)


async def test_a_chrome_that_never_answers_is_reported(monkeypatch):
    launched: list[list[str]] = []
    _stub_chrome(monkeypatch, launched, ready=False)
    with pytest.raises(engine.BrowserUnavailableError):
        await engine._start_browser(_cfg(browser="chrome", chrome_path="/opt/chrome"))
    assert len(launched) == 1


def test_remediation_names_the_backend_that_is_configured(monkeypatch):
    monkeypatch.setattr(engine, "_chrome_binary", lambda cfg: "/opt/chrome")
    assert "Chrome" in engine.remediation(_cfg(browser="chrome"))
    assert "docker" not in engine.remediation(_cfg(browser="chrome")).lower()


_HEADLESS_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) HeadlessChrome/154.0.0.0 Safari/537.36"
)
_HINTS = {
    "secure": True,
    "brands": [
        {"brand": "Chromium", "version": "154"},
        {"brand": "Google Chrome", "version": "154"},
    ],
    "fullVersionList": [{"brand": "Google Chrome", "version": "154.0.8037.92"}],
    "mobile": False,
    "platform": "macOS",
    "platformVersion": "26.5.1",
    "architecture": "arm",
    "model": "",
    "bitness": "64",
    "wow64": False,
}
_HEADFUL_IDENTITY = {
    "userAgent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
    ),
    "userAgentMetadata": {
        "brands": [
            {"brand": "Chromium", "version": "154"},
            {"brand": "Google Chrome", "version": "154"},
        ],
        "fullVersionList": [{"brand": "Google Chrome", "version": "154.0.8037.92"}],
        "platform": "macOS",
        "platformVersion": "26.5.1",
        "architecture": "arm",
        "model": "",
        "mobile": False,
        "bitness": "64",
        "wow64": False,
    },
}


def test_a_headless_browser_is_given_the_identity_of_the_same_chrome_with_a_window():
    assert engine._headful_identity(_HEADLESS_UA, _HINTS) == _HEADFUL_IDENTITY


def test_a_browser_with_a_window_keeps_its_own_identity():
    headful = _HEADLESS_UA.replace("HeadlessChrome/", "Chrome/")
    assert engine._headful_identity(headful, _HINTS) is None


class _ScriptedCDP:
    def __init__(self, user_agent: str, hints: dict) -> None:
        self.user_agent = user_agent
        self.hints = hints
        self.calls: list[tuple[str, dict | None, str | None]] = []
        self.closed = False

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        self.closed = True

    async def send(self, method, params=None, session_id=None):
        self.calls.append((method, params, session_id))
        replies = {
            "Browser.getVersion": {"userAgent": self.user_agent},
            "Target.createTarget": {"targetId": "probe"},
            "Target.attachToTarget": {"sessionId": "probe-session"},
            "Runtime.evaluate": {"result": {"value": json.dumps(self.hints)}},
        }
        return replies.get(method, {})

    def expect_event(self, method, session_id=None):
        done = asyncio.get_running_loop().create_future()
        done.set_result({})
        return done

    def methods(self) -> list[str]:
        return [method for method, _, _ in self.calls]


async def test_a_headless_browser_has_its_identity_read_from_its_own_client_hints():
    cdp = _ScriptedCDP(_HEADLESS_UA, _HINTS)
    identity = await engine._read_identity(cdp)
    assert identity == _HEADFUL_IDENTITY
    opened = next(p for m, p, _ in cdp.calls if m == "Target.createTarget")
    assert opened == {"url": engine._HINTS_PAGE}
    assert cdp.methods()[-1] == "Target.closeTarget"


async def test_a_browser_with_a_window_is_not_probed_for_an_identity():
    cdp = _ScriptedCDP(_HEADLESS_UA.replace("HeadlessChrome/", "Chrome/"), _HINTS)
    assert await engine._read_identity(cdp) is None
    assert "Target.createTarget" not in cdp.methods()


async def test_the_first_tab_on_a_headless_browser_arms_the_identity(monkeypatch):
    cdp = _ScriptedCDP(_HEADLESS_UA, _HINTS)
    monkeypatch.setattr(engine, "CDPClient", lambda ws_url: cdp)
    provider = engine.EngineProvider(_cfg(cdp_url="ws://127.0.0.1:9/devtools/browser/x"))
    await provider.start()
    assert "Browser.getVersion" not in cdp.methods()
    await provider._prepare_tab("tab")
    assert provider._identity == _HEADFUL_IDENTITY


class _RefusesChromePages(_ScriptedCDP):
    async def send(self, method, params=None, session_id=None):
        if method == "Target.createTarget" and (params or {}).get("url") == engine._HINTS_PAGE:
            self.calls.append((method, params, session_id))
            raise engine.CDPError("Target.createTarget: not allowed")
        return await super().send(method, params, session_id)


async def test_a_browser_that_refuses_the_identity_probe_still_fetches(monkeypatch, capsys):
    cdp = _RefusesChromePages(_HEADLESS_UA, _HINTS)
    monkeypatch.setattr(engine, "CDPClient", lambda ws_url: cdp)
    provider = engine.EngineProvider(
        _cfg(cdp_url="ws://127.0.0.1:9/devtools/browser/x", block_private_ips=False)
    )
    await provider.start()
    for _ in range(engine._IDENTITY_ATTEMPTS + 2):
        await provider._prepare_tab("tab")
    probes = [p for m, p, _ in cdp.calls if m == "Target.createTarget"]
    assert len(probes) == engine._IDENTITY_ATTEMPTS
    assert provider._identity is None
    assert cdp.methods().count("Page.enable") == engine._IDENTITY_ATTEMPTS + 2
    assert capsys.readouterr().err.count("HeadlessChrome") == 1


async def test_an_identity_probe_without_a_result_is_a_failed_probe_not_a_crash():
    class NoValue(_ScriptedCDP):
        async def send(self, method, params=None, session_id=None):
            if method == "Runtime.evaluate":
                self.calls.append((method, params, session_id))
                return {"result": {"type": "undefined"}}
            return await super().send(method, params, session_id)

    with pytest.raises(ValueError):
        await engine._read_identity(NoValue(_HEADLESS_UA, _HINTS))


async def test_each_tab_takes_the_identity_before_anything_loads(monkeypatch):
    cdp = _ScriptedCDP(_HEADLESS_UA, _HINTS)
    monkeypatch.setattr(engine, "CDPClient", lambda ws_url: cdp)
    provider = engine.EngineProvider(_cfg(cdp_url="ws://127.0.0.1:9/devtools/browser/x"))
    await provider.start()
    await provider._prepare_tab("first")
    cdp.calls.clear()

    await provider._prepare_tab("tab")

    override = ("Emulation.setUserAgentOverride", _HEADFUL_IDENTITY, "tab")
    assert cdp.calls.index(override) < cdp.methods().index("Page.enable")


async def test_a_tab_on_a_browser_with_a_window_keeps_its_own_identity(monkeypatch):
    cdp = _ScriptedCDP(_HEADLESS_UA.replace("HeadlessChrome/", "Chrome/"), _HINTS)
    monkeypatch.setattr(engine, "CDPClient", lambda ws_url: cdp)
    provider = engine.EngineProvider(_cfg(cdp_url="ws://127.0.0.1:9/devtools/browser/x"))
    await provider.start()

    await provider._prepare_tab("tab")

    assert "Emulation.setUserAgentOverride" not in cdp.methods()


def test_a_fresh_process_reports_an_unreachable_browser_instead_of_raising():
    probe = (
        "import asyncio; from groundhog_mcp import engine; "
        "print(asyncio.run(engine.check_browser('http://127.0.0.1:1')), "
        "asyncio.run(engine.check_browser('ws://127.0.0.1:1/devtools/browser/x')))"
    )
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == ["False", "False"]


_FIXTURES = Path(__file__).parent / "fixtures"


def _attached(kind: str, session: str) -> dict:
    captured = json.loads((_FIXTURES / f"cdp_attached_{kind}.json").read_text())
    return {**captured, "sessionId": session}


def _paused(url: str) -> dict:
    return {"requestId": "r1", "request": {"url": url}, "resourceType": "Document"}


async def test_a_document_on_a_private_address_is_stopped_before_it_is_requested():
    cdp = _ScriptedCDP(_HEADLESS_UA, _HINTS)
    guard = engine._PrivateDocuments(_cfg())
    await guard._decide(cdp, "tab", _paused("http://127.0.0.1:8080/admin"))
    assert cdp.calls == [
        ("Fetch.failRequest", {"requestId": "r1", "errorReason": "AddressUnreachable"}, "tab")
    ]
    assert guard.blocked and "127.0.0.1" in guard.blocked


async def test_a_document_on_a_public_address_goes_ahead(monkeypatch):
    async def public(url, cfg):
        return None

    monkeypatch.setattr(engine.safety, "check_url", public)
    cdp = _ScriptedCDP(_HEADLESS_UA, _HINTS)
    guard = engine._PrivateDocuments(_cfg())
    await guard._decide(cdp, "tab", _paused("https://example.com/"))
    assert cdp.calls == [("Fetch.continueRequest", {"requestId": "r1"}, "tab")]
    assert guard.blocked is None


@pytest.mark.parametrize("url", ["data:text/html,hi", "blob:https://example.com/x", "about:srcdoc"])
async def test_a_document_that_makes_no_request_goes_ahead_unchecked(monkeypatch, url):
    async def must_not_check(url, cfg):
        pytest.fail("checked a URL that makes no network request")

    monkeypatch.setattr(engine.safety, "check_url", must_not_check)
    cdp = _ScriptedCDP(_HEADLESS_UA, _HINTS)
    await engine._PrivateDocuments(_cfg())._decide(cdp, "tab", _paused(url))
    assert cdp.methods() == ["Fetch.continueRequest"]


@pytest.mark.parametrize(
    "error",
    [engine.socket.gaierror("nodename nor servname provided"), UnicodeError("label too long")],
)
async def test_a_document_whose_address_cannot_be_checked_is_stopped(monkeypatch, error):
    async def unresolvable(url, cfg):
        raise error

    monkeypatch.setattr(engine.safety, "check_url", unresolvable)
    cdp = _ScriptedCDP(_HEADLESS_UA, _HINTS)
    guard = engine._PrivateDocuments(_cfg())
    await guard._decide(cdp, "tab", _paused("https://no-such-host.invalid/"))
    assert cdp.calls == [
        ("Fetch.failRequest", {"requestId": "r1", "errorReason": "NameNotResolved"}, "tab")
    ]
    assert guard.blocked is None


async def test_a_long_hostname_label_stops_the_document_instead_of_leaving_it_paused():
    cdp = _ScriptedCDP(_HEADLESS_UA, _HINTS)
    await engine._PrivateDocuments(_cfg())._decide(
        cdp, "tab", _paused(f"http://{'a' * 64}.example.com/")
    )
    assert cdp.methods() == ["Fetch.failRequest"]


async def test_a_tab_that_closes_mid_decision_does_not_raise():
    class Closed(_ScriptedCDP):
        async def send(self, method, params=None, session_id=None):
            raise engine.CDPError("CDP client is not connected")

    await engine._PrivateDocuments(_cfg())._decide(
        Closed(_HEADLESS_UA, _HINTS), "tab", _paused("http://127.0.0.1/")
    )


@pytest.mark.parametrize("block", [True, False])
async def test_document_requests_are_intercepted_only_while_private_addresses_are_blocked(
    monkeypatch, block
):
    cdp = _ScriptedCDP(_HEADLESS_UA.replace("HeadlessChrome/", "Chrome/"), _HINTS)
    monkeypatch.setattr(engine, "CDPClient", lambda ws_url: cdp)
    provider = engine.EngineProvider(
        _cfg(cdp_url="ws://127.0.0.1:9/devtools/browser/x", block_private_ips=block)
    )
    await provider.start()
    cdp.calls.clear()
    await provider._prepare_tab("tab")
    fetch = [p for m, p, _ in cdp.calls if m == "Fetch.enable"]
    requests = [{"urlPattern": "*", "resourceType": "Document"}] if block else []
    responses = [{"urlPattern": "*", "resourceType": "Document", "requestStage": "Response"}]
    assert fetch == [{"patterns": requests + responses}]



_PDF_PAUSE = json.loads((_FIXTURES / "cdp_paused_pdf_response.json").read_text())
_PDF_EOF = json.loads((_FIXTURES / "cdp_io_read_eof.json").read_text())
_PDF_BYTES = (_FIXTURES / "copyright_circular_1.pdf").read_bytes()


class _StreamingCDP(_ScriptedCDP):
    def __init__(self, body: bytes) -> None:
        super().__init__(_HEADLESS_UA, _HINTS)
        encoded = base64.b64encode(body).decode()
        self.chunks = [{"base64Encoded": True, "data": encoded, "eof": False}, _PDF_EOF]

    async def send(self, method, params=None, session_id=None):
        self.calls.append((method, params, session_id))
        if method == "Fetch.takeResponseBodyAsStream":
            return {"stream": "18"}
        if method == "IO.read":
            return self.chunks.pop(0)
        return {}


def _pdf_pause(**changes) -> dict:
    return {**_PDF_PAUSE, **changes}


async def test_a_top_frame_pdf_response_is_read_whole_then_its_navigation_is_stopped():
    cdp = _StreamingCDP(_PDF_BYTES)
    documents = engine._PdfDocuments(_PDF_PAUSE["frameId"])

    await documents._decide(cdp, "tab", _PDF_PAUSE)

    captured = documents.captured
    assert captured is not None and captured.data == _PDF_BYTES
    assert captured.url == "https://www.copyright.gov/circs/circ01.pdf"
    assert captured.response["status"] == 200
    assert captured.response["headers"]["content-type"] == "application/pdf"
    assert cdp.methods() == [
        "Fetch.takeResponseBodyAsStream",
        "IO.read",
        "IO.read",
        "IO.close",
        "Fetch.failRequest",
    ]
    assert cdp.calls[-1][1] == {"requestId": _PDF_PAUSE["requestId"], "errorReason": "Aborted"}


@pytest.mark.parametrize(
    "changes",
    [
        {"frameId": "CHILD-FRAME"},
        {
            "responseHeaders": [
                {"name": "content-type", "value": "text/html; charset=utf-8"}
                if h["name"] == "content-type"
                else h
                for h in _PDF_PAUSE["responseHeaders"]
            ]
        },
        {"responseStatusCode": 404},
    ],
    ids=["child frame", "not a pdf", "error status"],
)
async def test_a_response_that_is_not_a_top_frame_pdf_is_let_through(changes):
    cdp = _StreamingCDP(_PDF_BYTES)
    documents = engine._PdfDocuments(_PDF_PAUSE["frameId"])

    await documents._decide(cdp, "tab", _pdf_pause(**changes))

    assert documents.captured is None
    assert cdp.calls == [("Fetch.continueRequest", {"requestId": _PDF_PAUSE["requestId"]}, "tab")]


@pytest.mark.parametrize("content_type", ["application/octet-stream", "application/x-pdf"])
async def test_a_pdf_served_under_another_type_is_still_read(content_type):
    headers = [
        {"name": "content-type", "value": content_type} if h["name"] == "content-type" else h
        for h in _PDF_PAUSE["responseHeaders"]
    ]
    documents = engine._PdfDocuments(_PDF_PAUSE["frameId"])

    await documents._decide(_StreamingCDP(_PDF_BYTES), "tab", _pdf_pause(responseHeaders=headers))

    assert documents.captured is not None and documents.captured.data == _PDF_BYTES


async def test_a_browser_that_refuses_response_interception_still_opens_unguarded_tabs(
    monkeypatch,
):
    class NoFetch(_ScriptedCDP):
        async def send(self, method, params=None, session_id=None):
            if method == "Fetch.enable":
                raise engine.CDPError("Fetch.enable is not allowed")
            return await super().send(method, params, session_id)

    cdp = NoFetch(_HEADLESS_UA.replace("HeadlessChrome/", "Chrome/"), _HINTS)
    monkeypatch.setattr(engine, "CDPClient", lambda ws_url: cdp)
    provider = engine.EngineProvider(
        _cfg(cdp_url="ws://127.0.0.1:9/devtools/browser/x", block_private_ips=False)
    )
    await provider.start()

    await provider._prepare_tab("tab")

    assert "Network.enable" in cdp.methods()


async def test_a_pdf_over_the_byte_cap_is_captured_without_its_body(monkeypatch):
    monkeypatch.setattr(engine.pdf, "MAX_PDF_BYTES", 1000)
    cdp = _StreamingCDP(_PDF_BYTES)
    documents = engine._PdfDocuments(_PDF_PAUSE["frameId"])

    await documents._decide(cdp, "tab", _PDF_PAUSE)

    assert documents.captured is not None and documents.captured.data is None
    assert cdp.methods() == [
        "Fetch.takeResponseBodyAsStream",
        "IO.read",
        "IO.close",
        "Fetch.failRequest",
    ]


async def test_each_paused_stage_is_decided_by_one_handler_only():
    cdp = _StreamingCDP(_PDF_BYTES)

    await engine._PrivateDocuments(_cfg())._decide(cdp, "tab", _PDF_PAUSE)
    await engine._PdfDocuments(_PDF_PAUSE["frameId"])._decide(
        cdp, "tab", _paused("https://example.com/")
    )

    assert cdp.calls == []


async def _pdf_page(monkeypatch, data: bytes | None, *, challenge_seen: bool = False):
    async def public(url, cfg):
        return None

    monkeypatch.setattr(engine.safety, "check_url", public)
    documents = engine._PdfDocuments(_PDF_PAUSE["frameId"])
    await documents._decide(_StreamingCDP(_PDF_BYTES), "tab", _PDF_PAUSE)
    captured = dataclasses.replace(documents.captured, data=data)
    provider = engine.EngineProvider(_cfg())
    return await provider._pdf_page(captured, challenge_seen, strip_hidden=True)


async def test_a_captured_pdf_becomes_a_page_of_its_text(monkeypatch):
    page = await _pdf_page(monkeypatch, _PDF_BYTES)

    assert page.retrieval_status == "ok" and page.http_status == 200
    assert page.final_url == "https://www.copyright.gov/circs/circ01.pdf"
    assert page.title == "Circular 1 Copyright Basics"
    assert page.pdf_pages is not None and len(page.pdf_pages) == 10
    assert page.text == "\n\n".join(page.pdf_pages) and page.html == ""


@pytest.mark.parametrize(
    "data, challenge_seen, status",
    [
        (None, False, "too_large"),
        (b"<html>not a pdf</html>", False, "unsupported_content"),
        (b" " * 2000 + _PDF_BYTES, False, "ok"),
        (_PDF_BYTES, True, "challenge"),
    ],
    ids=["over the cap", "not a pdf", "leading junk", "challenge"],
)
async def test_a_captured_pdf_that_cannot_be_read_says_why(
    monkeypatch, data, challenge_seen, status
):
    page = await _pdf_page(monkeypatch, data, challenge_seen=challenge_seen)

    assert page.retrieval_status == status

def test_a_chrome_path_that_is_not_an_executable_is_reported_as_not_found(tmp_path):
    missing = tmp_path / "no-chrome"
    not_executable = tmp_path / "chrome.txt"
    not_executable.write_text("")
    for path in (missing, not_executable):
        cfg = _cfg(browser="chrome", chrome_path=str(path))
        assert engine._chrome_binary(cfg) is None
        message = engine.remediation(cfg)
        assert str(path) in message and "--remote-debugging-port" not in message


async def test_a_chrome_that_cannot_be_executed_is_reported_not_raised(monkeypatch):
    launched: list[list[str]] = []
    _stub_chrome(monkeypatch, launched)

    def refused(argv):
        raise PermissionError(13, "Permission denied", argv[0])

    monkeypatch.setattr(engine, "_launch_detached", refused)
    with pytest.raises(engine.BrowserUnavailableError, match="could not be started"):
        await engine._start_browser(_cfg(browser="chrome", chrome_path="/opt/chrome"))


def _locked_profile(tmp_path, *, chrome_listening: bool):
    profile = tmp_path / "chrome"
    profile.mkdir()
    socket_dir = tempfile.mkdtemp(prefix="gh", dir="/tmp")
    singleton = os.path.join(socket_dir, "SingletonSocket")
    listener = engine.socket.socket(engine.socket.AF_UNIX, engine.socket.SOCK_STREAM)
    if chrome_listening:
        listener.bind(singleton)
        listener.listen()
    (profile / "SingletonLock").symlink_to(f"{engine.socket.gethostname()}-{os.getpid()}")
    (profile / "SingletonSocket").symlink_to(singleton)
    return profile, listener


async def test_a_profile_a_running_chrome_holds_is_reported_without_launching(
    monkeypatch, tmp_path
):
    launched: list[list[str]] = []
    _stub_chrome(monkeypatch, launched)
    profile, listener = _locked_profile(tmp_path, chrome_listening=True)
    try:
        with pytest.raises(engine.BrowserUnavailableError) as raised:
            await engine._start_browser(
                _cfg(browser="chrome", chrome_path="/opt/chrome", chrome_profile=str(profile))
            )
    finally:
        listener.close()

    assert launched == []
    assert str(profile) in str(raised.value) and str(os.getpid()) in str(raised.value)


async def test_a_lock_whose_pid_was_reused_by_another_process_does_not_stop_a_launch(
    monkeypatch, tmp_path
):
    launched: list[list[str]] = []
    _stub_chrome(monkeypatch, launched)
    profile, listener = _locked_profile(tmp_path, chrome_listening=False)
    listener.close()

    await engine._start_browser(
        _cfg(browser="chrome", chrome_path="/opt/chrome", chrome_profile=str(profile))
    )

    assert len(launched) == 1


async def test_a_profile_that_cannot_be_created_is_reported_not_raised(monkeypatch, tmp_path):
    launched: list[list[str]] = []
    _stub_chrome(monkeypatch, launched)
    read_only = tmp_path / "read-only"
    read_only.mkdir(mode=0o500)
    profile = read_only / "chrome"
    try:
        with pytest.raises(engine.BrowserUnavailableError, match="GROUNDHOG_CHROME_PROFILE"):
            await engine._start_browser(
                _cfg(browser="chrome", chrome_path="/opt/chrome", chrome_profile=str(profile))
            )
    finally:
        read_only.chmod(0o700)

    assert launched == []


async def test_a_lock_left_by_a_chrome_that_has_exited_does_not_stop_a_launch(
    monkeypatch, tmp_path
):
    launched: list[list[str]] = []
    _stub_chrome(monkeypatch, launched)
    profile = tmp_path / "chrome"
    profile.mkdir()
    (profile / "SingletonLock").symlink_to(f"{engine.socket.gethostname()}-999999999")
    await engine._start_browser(
        _cfg(browser="chrome", chrome_path="/opt/chrome", chrome_profile=str(profile))
    )
    assert len(launched) == 1


async def test_a_chrome_that_exits_at_launch_is_reported_at_once(monkeypatch):
    launched: list[list[str]] = []
    _stub_chrome(monkeypatch, launched, ready=False)

    class Exited:
        returncode = 21

        def poll(self) -> int:
            return self.returncode

    def exits(argv):
        launched.append(argv)
        return Exited()

    monkeypatch.setattr(engine, "_launch_detached", exits)
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def counting_sleep(seconds):
        slept.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(engine.asyncio, "sleep", counting_sleep)
    with pytest.raises(engine.BrowserUnavailableError, match="exited"):
        await engine._start_browser(_cfg(browser="chrome", chrome_path="/opt/chrome"))
    assert len(slept) <= 1


async def test_the_launched_chrome_never_preloads_pages(monkeypatch, tmp_path):
    launched: list[list[str]] = []
    _stub_chrome(monkeypatch, launched)
    profile = tmp_path / "chrome"
    (profile / "Default").mkdir(parents=True)
    (profile / "Default" / "Preferences").write_text(json.dumps({"profile": {"name": "kept"}}))
    await engine._start_browser(
        _cfg(browser="chrome", chrome_path="/opt/chrome", chrome_profile=str(profile))
    )
    prefs = json.loads((profile / "Default" / "Preferences").read_text())
    assert prefs["net"]["network_prediction_options"] == 2
    assert prefs["profile"]["name"] == "kept"


async def test_unreadable_preferences_are_replaced_rather_than_failing_the_launch(
    monkeypatch, tmp_path
):
    launched: list[list[str]] = []
    _stub_chrome(monkeypatch, launched)
    profile = tmp_path / "chrome"
    (profile / "Default").mkdir(parents=True)
    (profile / "Default" / "Preferences").write_text("{not json")
    await engine._start_browser(
        _cfg(browser="chrome", chrome_path="/opt/chrome", chrome_profile=str(profile))
    )
    prefs = json.loads((profile / "Default" / "Preferences").read_text())
    assert prefs == {"net": {"network_prediction_options": 2}}


@pytest.mark.parametrize(
    "cdp_url", ["http://browser.internal:9222", "ws://127.0.0.1:9222/devtools/browser/abc"]
)
def test_a_browser_groundhog_does_not_start_gets_a_hint_about_that_browser(monkeypatch, cdp_url):
    monkeypatch.setattr(engine, "_chrome_binary", lambda cfg: None)
    for browser in ("chrome", "stealth"):
        message = engine.remediation(_cfg(browser=browser, cdp_url=cdp_url))
        assert "not started automatically" in message
        assert "Install Google Chrome" not in message and "docker" not in message.lower()


class _Frames(_ScriptedCDP):
    def __init__(self, *args, fail_on: str | None = None) -> None:
        super().__init__(*args)
        self.fail_on = fail_on
        self.listeners: dict[tuple[str, str], list] = {}

    async def send(self, method, params=None, session_id=None):
        if method == self.fail_on:
            self.calls.append((method, params, session_id))
            raise engine.CDPError(f"{method} failed")
        return await super().send(method, params, session_id)

    def on_event(self, method, session_id, callback):
        self.listeners.setdefault((method, session_id), []).append(callback)
        return lambda: self.listeners[(method, session_id)].remove(callback)

    def fire(self, method, session_id, params):
        for callback in list(self.listeners.get((method, session_id), [])):
            callback(params)

    async def until_sent(self, method):
        async def sent():
            while method not in self.methods():
                await asyncio.sleep(0.01)

        await asyncio.wait_for(sent(), timeout=5)


async def test_a_cross_site_frame_is_prepared_then_released_and_its_own_frames_watched():
    cdp = _Frames(_HEADLESS_UA, _HINTS)
    prepared: list[str] = []

    async def prepare(cdp, child):
        prepared.append(child)
        return []

    frames = engine._ChildFrames(prepare, guarded=True)
    detach = frames.attach(cdp, "tab")
    await frames._adopt(cdp, _attached("iframe", "frame"))
    assert prepared == ["frame"]
    assert cdp.calls[0] == ("Target.setAutoAttach", engine._FRAME_AUTO_ATTACH, "frame")
    assert cdp.calls[-1] == ("Runtime.runIfWaitingForDebugger", None, "frame")
    assert {key for key, callbacks in cdp.listeners.items() if callbacks} == {
        ("Target.attachedToTarget", "tab"),
        ("Target.attachedToTarget", "frame"),
    }
    detach[0]()
    assert not any(cdp.listeners.values())


async def test_an_unguarded_frame_whose_preparation_fails_is_still_released():
    cdp = _Frames(_HEADLESS_UA, _HINTS, fail_on="Target.setAutoAttach")

    async def prepare(cdp, child):
        return []

    await engine._ChildFrames(prepare, guarded=False)._adopt(cdp, _attached("iframe", "frame"))

    assert cdp.methods()[-1] == "Runtime.runIfWaitingForDebugger"


@pytest.mark.parametrize("failing", ["Fetch.enable", "Target.setAutoAttach"])
async def test_a_guarded_frame_whose_preparation_fails_is_never_let_load(failing):
    cdp = _Frames(_HEADLESS_UA, _HINTS, fail_on=failing)

    async def prepare(cdp, child):
        await cdp.send("Fetch.enable", {}, session_id=child)
        return []

    await engine._ChildFrames(prepare, guarded=True)._adopt(cdp, _attached("iframe", "frame"))

    assert "Runtime.runIfWaitingForDebugger" not in cdp.methods()


async def test_a_frame_prepared_after_the_fetch_ended_leaves_no_handlers_behind():
    cdp = _Frames(_HEADLESS_UA, _HINTS)
    preparing = asyncio.Event()

    async def prepare(cdp, child):
        await preparing.wait()
        return [cdp.on_event("Fetch.requestPaused", child, lambda params: None)]

    detach = engine._ChildFrames(prepare, guarded=True).attach(cdp, "tab")
    cdp.fire("Target.attachedToTarget", "tab", _attached("iframe", "frame"))
    await asyncio.sleep(0)

    detach[0]()
    preparing.set()
    for _ in range(3):
        await asyncio.sleep(0)

    assert not any(cdp.listeners.values())


@pytest.mark.parametrize(
    "block,headless", [(True, False), (False, True), (True, True), (False, False)]
)
async def test_frames_are_attached_whenever_they_need_a_guard_or_an_identity(
    monkeypatch, block, headless
):
    user_agent = _HEADLESS_UA if headless else _HEADLESS_UA.replace("HeadlessChrome/", "Chrome/")
    cdp = _Frames(user_agent, _HINTS)
    monkeypatch.setattr(engine, "CDPClient", lambda ws_url: cdp)
    provider = engine.EngineProvider(
        _cfg(cdp_url="ws://127.0.0.1:9/devtools/browser/x", block_private_ips=block)
    )
    await provider.start()
    await provider._prepare_tab("tab")
    attached = ("Target.setAutoAttach", engine._FRAME_AUTO_ATTACH, "tab") in cdp.calls
    assert attached is (block or headless)
    cdp.calls.clear()
    unsubscribes = await provider._prepare_frame(cdp, "frame")
    methods = cdp.methods()
    assert ("Emulation.setUserAgentOverride" in methods) is headless
    assert ("Fetch.enable" in methods) is block
    assert bool(cdp.listeners.get(("Fetch.requestPaused", "frame"))) is block
    for unsubscribe in unsubscribes:
        unsubscribe()
    assert not cdp.listeners.get(("Fetch.requestPaused", "frame"))


@pytest.mark.parametrize(
    "field,raw,cleaned",
    [
        ("hidden_spans", None, []),
        ("meta", None, {"meta": {}, "lang": None, "canonical": None}),
        ("meta", {"meta": None}, {"meta": {}, "lang": None, "canonical": None}),
    ],
    ids=["spans-not-a-list", "meta-not-a-map", "meta-pairs-not-a-map"],
)
def test_a_malformed_collector_result_is_emptied_not_trusted(make_page, field, raw, cleaned):
    page = dataclasses.replace(make_page(), **{field: raw})

    assert getattr(page, field) == cleaned


def test_a_page_metadata_value_is_cut_to_url_length(
    make_page: Callable[..., engine.RenderedPage],
) -> None:
    description = "\u200b" + "d" * 5000

    page = dataclasses.replace(
        make_page(), meta={"meta": {"description": description}, "lang": None, "canonical": None}
    )

    assert page.meta["meta"]["description"] == "d" * 2048


def test_a_page_canonical_url_reaches_the_result(make_page):
    meta = {"meta": {}, "lang": "en", "canonical": "https://ex.com/a"}

    page = dataclasses.replace(make_page(), meta=meta)

    assert page.meta["canonical"] == "https://ex.com/a"


def test_a_single_dropped_span_is_reported(make_page):
    page = dataclasses.replace(make_page(), spans_dropped=1)

    assert page.spans_dropped == 1


async def test_a_paused_private_document_is_failed_through_the_event_the_guard_subscribed_to():
    cdp = _Frames(_HEADLESS_UA, _HINTS)
    engine._PrivateDocuments(_cfg()).attach(cdp, "tab")

    cdp.fire("Fetch.requestPaused", "tab", _paused("http://127.0.0.1:8080/admin"))
    await cdp.until_sent("Fetch.failRequest")

    assert cdp.calls == [
        ("Fetch.failRequest", {"requestId": "r1", "errorReason": "AddressUnreachable"}, "tab")
    ]


async def test_a_new_cross_site_frame_is_prepared_through_the_event_it_was_attached_by():
    cdp = _Frames(_HEADLESS_UA, _HINTS)
    prepared: list[str] = []

    async def prepare(cdp, child):
        prepared.append(child)
        return []

    engine._ChildFrames(prepare, guarded=True).attach(cdp, "tab")

    cdp.fire("Target.attachedToTarget", "tab", _attached("iframe", "frame"))
    await cdp.until_sent("Runtime.runIfWaitingForDebugger")

    assert prepared == ["frame"]
    assert ("Runtime.runIfWaitingForDebugger", None, "frame") in cdp.calls


async def test_a_private_address_is_refused_before_any_browser_is_contacted(monkeypatch):
    def no_browser(ws_url):
        pytest.fail("a browser was contacted for a refused URL")

    monkeypatch.setattr(engine, "CDPClient", no_browser)
    provider = engine.EngineProvider(_cfg())

    with pytest.raises(engine.safety.BlockedURLError, match="127.0.0.1"):
        await provider.fetch("http://127.0.0.1:8080/admin")


def test_an_http_cdp_url_is_probed_with_its_query_kept():
    asked: list[str] = []

    class VersionOnlyWithToken(BaseHTTPRequestHandler):
        def do_GET(self):
            asked.append(self.path)
            body = b'{"webSocketDebuggerUrl": "ws://127.0.0.1/devtools/browser/x"}'
            self.send_response(200 if self.path == "/json/version?token=abc" else 404)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), VersionOnlyWithToken)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        reachable = asyncio.run(
            engine.check_browser(f"http://127.0.0.1:{server.server_address[1]}/?token=abc")
        )
    finally:
        server.shutdown()

    assert reachable is True
    assert asked == ["/json/version?token=abc"]


async def test_a_dedicated_worker_is_released_without_waiting_on_frame_preparation():
    cdp = _Frames(_HEADLESS_UA, _HINTS)
    prepared: list[str] = []

    async def prepare(cdp, child):
        prepared.append(child)
        return []

    engine._ChildFrames(prepare, guarded=True).attach(cdp, "tab")

    cdp.fire("Target.attachedToTarget", "tab", _attached("worker", "worker"))
    await cdp.until_sent("Runtime.runIfWaitingForDebugger")

    assert prepared == []
    assert cdp.calls == [("Runtime.runIfWaitingForDebugger", None, "worker")]


async def test_a_frame_whose_guard_cannot_be_enabled_leaves_no_listener(monkeypatch):
    cdp = _Frames(_HEADLESS_UA, _HINTS, fail_on="Fetch.enable")
    monkeypatch.setattr(engine, "CDPClient", lambda ws_url: cdp)
    provider = engine.EngineProvider(_cfg(cdp_url="ws://127.0.0.1:9/devtools/browser/x"))
    await provider.start()

    with pytest.raises(engine.CDPError):
        await provider._prepare_frame(cdp, "frame")

    assert not cdp.listeners.get(("Fetch.requestPaused", "frame"))


async def test_a_chrome_that_never_answers_is_stopped_before_the_error(monkeypatch):
    launched: list[list[str]] = []
    _stub_chrome(monkeypatch, launched, ready=False)

    class StillRunning:
        killed = False

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            self.killed = True

    chrome = StillRunning()

    def runs_but_never_answers(argv):
        return chrome

    monkeypatch.setattr(engine, "_launch_detached", runs_but_never_answers)

    with pytest.raises(engine.BrowserUnavailableError, match="did not become ready"):
        await engine._start_browser(_cfg(browser="chrome", chrome_path="/opt/chrome"))

    assert chrome.killed


async def test_an_ipv6_loopback_cdp_url_is_refused_before_launching(monkeypatch):
    launched: list[list[str]] = []
    _stub_chrome(monkeypatch, launched)

    with pytest.raises(engine.BrowserUnavailableError) as raised:
        await engine._start_browser(
            _cfg(browser="chrome", chrome_path="/opt/chrome", cdp_url="http://[::1]:9222")
        )

    assert launched == []
    assert "http://[::1]:9222" in str(raised.value)
    assert "CDP_URL=http://127.0.0.1:9222" in str(raised.value)


def test_a_launched_browser_outlives_the_process_that_launched_it(tmp_path):
    marker = tmp_path / "still-running"
    launcher = (
        "import asyncio\n"
        "from groundhog_mcp import engine\n"
        "async def main():\n"
        f"    launched = engine._launch_detached(['sh', '-c', 'sleep 1; touch {marker}'])\n"
        "    if asyncio.iscoroutine(launched):\n"
        "        await launched\n"
        "asyncio.run(main())\n"
    )

    subprocess.run([sys.executable, "-c", launcher], check=True, timeout=30)
    for _ in range(50):
        if marker.exists():
            break
        time.sleep(0.1)

    assert marker.exists()


async def test_same_domain_fetches_queued_behind_a_busy_page_still_start_spaced_apart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def public(url: str, cfg: Config) -> None:
        return None

    async def connected() -> None:
        return None

    started: dict[str, float] = {}

    async def load(url: str, strip_hidden: bool) -> None:
        started[url] = time.monotonic()
        if "other.com" in url:
            await asyncio.sleep(0.5)

    monkeypatch.setattr(engine.safety, "check_url", public)
    provider = engine.EngineProvider(_cfg(min_delay_ms=300, max_concurrent_pages=1))
    monkeypatch.setattr(provider, "_ensure_connected", connected)
    monkeypatch.setattr(provider, "_fetch_in_target", load)

    busy = asyncio.create_task(provider.fetch("https://other.com/"))
    await asyncio.sleep(0.05)
    await asyncio.gather(provider.fetch("https://same.com/a"), provider.fetch("https://same.com/b"))
    await busy

    assert started["https://same.com/b"] - started["https://same.com/a"] >= 0.25


async def test_a_fetch_waiting_out_its_domain_delay_leaves_the_page_free_for_other_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def public(url: str, cfg: Config) -> None:
        return None

    async def connected() -> None:
        return None

    started: dict[str, float] = {}

    async def load(url: str, strip_hidden: bool) -> None:
        started[url] = time.monotonic()

    monkeypatch.setattr(engine.safety, "check_url", public)
    provider = engine.EngineProvider(_cfg(min_delay_ms=500, max_concurrent_pages=1))
    monkeypatch.setattr(provider, "_ensure_connected", connected)
    monkeypatch.setattr(provider, "_fetch_in_target", load)

    same = [asyncio.create_task(provider.fetch(f"https://same.com/{p}")) for p in ("a", "b")]
    await asyncio.sleep(0.05)
    await provider.fetch("https://other.com/")
    await asyncio.gather(*same)

    assert started["https://other.com/"] - started["https://same.com/a"] < 0.25
    assert started["https://same.com/b"] - started["https://same.com/a"] >= 0.45


async def test_a_command_the_browser_never_answers_fails_instead_of_hanging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def silent(ws: websockets.ServerConnection) -> None:
        await ws.wait_closed()

    monkeypatch.setattr(cdp, "_COMMAND_TIMEOUT_S", 0.2)

    async with websockets.serve(silent, "127.0.0.1", 0) as server:
        client = cdp.CDPClient(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/")
        await client.connect()
        try:
            with pytest.raises(TimeoutError, match="Target.closeTarget"):
                await client.send("Target.closeTarget", {"targetId": "t"})
            closed_after_timeout = client.closed
        finally:
            await client.close()

    assert closed_after_timeout


async def test_a_session_command_timeout_leaves_the_client_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def respond(ws: websockets.ServerConnection) -> None:
        async for raw in ws:
            request = json.loads(raw)
            if request["method"] != "Page.navigate":
                await ws.send(json.dumps({"id": request["id"], "result": {"alive": True}}))

    monkeypatch.setattr(cdp, "_COMMAND_TIMEOUT_S", 0.05)
    async with websockets.serve(respond, "127.0.0.1", 0) as server:
        client = cdp.CDPClient(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/")
        await client.connect()
        try:
            with pytest.raises(cdp.CommandTimeout, match="Page.navigate"):
                await client.send(
                    "Page.navigate", {"url": "https://example.com/"}, session_id="tab"
                )
            assert not client.closed
            assert await client.send("Browser.getVersion") == {"alive": True}
        finally:
            await client.close()


def test_the_collector_reply_fits_within_the_cdp_frame_limit() -> None:
    caps = engine._COLLECT_CAPS
    span_chars = 400 * (200 + caps["field"] + 80)
    meta_chars = caps["meta"] * 2 * caps["field"]
    chars = caps["html"] + caps["text"] + span_chars + meta_chars + 3 * caps["field"]
    assert chars * 6 + 64 * 1024 < cdp._MAX_FRAME_BYTES


class _DisconnectedOnNavigate(_ScriptedCDP):
    def on_event(self, method: str, callback: object, session_id: str | None = None) -> object:
        return lambda: None

    async def send(self, method, params=None, session_id=None):
        if self.closed:
            raise cdp.CDPError("CDP client is not connected")
        if method == "Page.navigate":
            self.closed = True
            raise cdp.CDPError("CDP connection closed")
        return await super().send(method, params, session_id)


async def test_a_fetch_whose_browser_disconnected_reports_that_not_the_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def public(url: str, cfg: Config) -> None:
        return None

    async def prepared(sid: str) -> None:
        return None

    monkeypatch.setattr(engine.safety, "check_url", public)
    provider = engine.EngineProvider(_cfg())
    provider._cdp = _DisconnectedOnNavigate(_HEADLESS_UA, _HINTS)
    monkeypatch.setattr(provider, "_prepare_tab", prepared)

    with pytest.raises(cdp.CDPError, match="CDP connection closed"):
        await provider._fetch_in_target("https://example.com/", True)


def test_json_responses_larger_than_the_default_limit_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def response(url: str, timeout: float) -> io.BytesIO:
        return io.BytesIO(b'{"value":"' + b'x' * (5 * 1024 * 1024) + b'"}')

    monkeypatch.setattr(http._opener, "open", response)
    with pytest.raises(ValueError, match="response body exceeded"):
        http.read_json("https://endpoint.example/", timeout=1.0)


def test_json_responses_within_the_limit_are_decoded(monkeypatch: pytest.MonkeyPatch) -> None:
    def response(url: str, timeout: float) -> io.BytesIO:
        return io.BytesIO(b'{"value":1}')

    monkeypatch.setattr(http._opener, "open", response)
    assert http.read_json("https://endpoint.example/", timeout=1.0) == {"value": 1}


@pytest.mark.parametrize("body", [b'"webSocketDebuggerUrl"', b"42"])
async def test_a_version_reply_that_is_not_a_json_object_is_not_a_browser(
    monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    monkeypatch.setattr(http._opener, "open", lambda url, timeout: io.BytesIO(body))
    assert await engine.check_browser("http://127.0.0.1:9222") is False


def test_json_fetches_refuse_redirects_to_a_different_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def response(
        self: urllib.request.HTTPHandler, req: urllib.request.Request
    ) -> urllib.response.addinfourl:
        headers = Message()
        headers["Location"] = "http://internal.example/"
        code = 302 if req.full_url == "http://operator.example/" else 200
        result = urllib.response.addinfourl(io.BytesIO(b"{}"), headers, req.full_url, code)
        result.msg = "Found" if code == 302 else "OK"
        return result

    monkeypatch.setattr(urllib.request.HTTPHandler, "http_open", response)
    with pytest.raises(urllib.error.HTTPError, match="302"):
        http.read_json("http://operator.example/", timeout=1.0)


async def test_an_oversized_cdp_message_fails_waiters_instead_of_being_buffered() -> None:
    async def flood(ws: websockets.ServerConnection) -> None:
        ws.transport.write(b"\x81\x7f" + (64 * 1024 * 1024 + 1).to_bytes(8, "big"))
        await ws.wait_closed()

    async with websockets.serve(flood, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = cdp.CDPClient(f"ws://127.0.0.1:{port}/")
        await client.connect()
        try:
            waiter = client.expect_event("Page.loadEventFired")
            with pytest.raises(cdp.CDPError, match="CDP connection closed"):
                await asyncio.wait_for(waiter, timeout=5)
        finally:
            await client.close()


def test_a_bad_cdp_port_does_not_break_the_startup_remediation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine, "_container_runtime", lambda: "docker")
    cfg = _cfg(cdp_url="http://localhost:abc")
    assert engine._port_of(cfg.cdp_url) == 9222
    assert "127.0.0.1:9222:9222" in engine.remediation(cfg)


async def test_runtime_stderr_is_sanitized_and_bounded() -> None:
    payload = "bad\u200b\n" + "x" * 500
    script = f"import sys; sys.stderr.write({payload!r}); sys.exit(7)"
    code, detail = await _run([sys.executable, "-c", script])
    assert code == 7
    assert detail == "bad " + "x" * 196


@pytest.mark.parametrize("omitted", ["isolated", "strip_incomplete"])
def test_a_rendered_page_requires_an_explicit_detection_flag(
    make_page: Callable[..., engine.RenderedPage],
    omitted: str,
) -> None:
    page = make_page()
    supplied = {
        field.name: getattr(page, field.name)
        for field in dataclasses.fields(page)
        if field.name != omitted
    }
    with pytest.raises(TypeError, match=omitted):
        engine.RenderedPage(**supplied)


def test_rendered_content_preserves_invisible_characters_and_its_full_length(
    make_page: Callable[..., engine.RenderedPage],
) -> None:
    html = "<p>hello\u200bworld</p>" * 1000
    text = "hello\u200bworld " * 1000
    page = dataclasses.replace(make_page(), html=html, text=text)
    assert page.html == html
    assert page.text == text


@pytest.mark.parametrize("field", ["html", "text"])
@pytest.mark.parametrize("raw", [None, 5])
def test_nonstring_rendered_content_becomes_empty_at_construction(
    make_page: Callable[..., engine.RenderedPage],
    field: str,
    raw: object,
) -> None:
    page = dataclasses.replace(make_page(), **{field: raw})
    assert getattr(page, field) == ""


@pytest.mark.parametrize("status", ["200", -1, {"status": 200}])
def test_a_rendered_pages_nonstatus_value_is_reported_as_absent(
    make_page: Callable[..., engine.RenderedPage],
    status: object,
) -> None:
    page = dataclasses.replace(make_page(), http_status=status)
    assert page.http_status is None


def test_a_canonical_metadata_url_longer_than_a_byline_is_not_shortened(
    make_page: Callable[..., engine.RenderedPage],
) -> None:
    canonical = "https://ex.com/" + "a" * 500
    page = make_page(meta={"meta": {"canonical": canonical}, "lang": None, "canonical": None})
    result = provenance.build("text", extract.ExtractMeta(None, None, None), page.meta)
    assert result["canonical"] == canonical


def test_a_canonical_metadata_url_padded_with_whitespace_is_kept_stripped(
    make_page: Callable[..., engine.RenderedPage],
) -> None:
    page = make_page(
        meta={"meta": {"canonical": " \thttps://ex.com/a\n"}, "lang": None, "canonical": None}
    )
    result = provenance.build("text", extract.ExtractMeta(None, None, None), page.meta)
    assert result["canonical"] == "https://ex.com/a"


@pytest.mark.parametrize(
    "canonical",
    ["https://ex.com/a\u200bb", "https://ex.com/" + "a" * 3000, "\u00a0https://ex.com/a"],
)
def test_a_canonical_metadata_url_that_cleaning_would_change_is_dropped(
    make_page: Callable[..., engine.RenderedPage], canonical: str
) -> None:
    page = make_page(meta={"meta": {"canonical": canonical}, "lang": None, "canonical": None})
    result = provenance.build("text", extract.ExtractMeta(None, None, None), page.meta)
    assert result["canonical"] is None


@pytest.mark.parametrize(
    ("fixture", "message"),
    [
        ("cdp_exception_details.json", "Error: " + "x" * 50),
        ("cdp_thrown_string_details.json", "boom"),
        ("cdp_thrown_empty_string_details.json", '""'),
        ("cdp_thrown_false_details.json", "false"),
        ("cdp_thrown_null_details.json", "null"),
    ],
)
async def test_a_real_page_exception_is_reported_by_its_message(
    fixture: str, message: str
) -> None:
    details = json.loads((_FIXTURES / fixture).read_text())

    async def respond(ws: websockets.ServerConnection) -> None:
        async for raw in ws:
            request = json.loads(raw)
            await ws.send(json.dumps({
                "id": request["id"],
                "result": {"result": {"type": "object"}, "exceptionDetails": details},
            }))

    async with websockets.serve(respond, "127.0.0.1", 0) as server:
        provider = engine.EngineProvider(_cfg())
        provider._cdp = cdp.CDPClient(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/")
        await provider._cdp.connect()
        try:
            with pytest.raises(cdp.CDPError) as caught:
                await provider._eval("tab", "1")
        finally:
            await provider._cdp.close()
    assert str(caught.value).startswith(message)


@pytest.mark.parametrize(("context_id", "context"), [(None, {}), (7, {"contextId": 7})])
async def test_a_page_evaluation_sends_one_awaited_by_value_evaluate_to_its_tab(
    context_id: int | None, context: dict[str, int]
) -> None:
    result = json.loads((_FIXTURES / "cdp_evaluate_result.json").read_text())
    requests: list[dict[str, object]] = []

    async def respond(ws: websockets.ServerConnection) -> None:
        async for raw in ws:
            request = json.loads(raw)
            requests.append(request)
            await ws.send(json.dumps({"id": request["id"], "result": result}))

    async with websockets.serve(respond, "127.0.0.1", 0) as server:
        provider = engine.EngineProvider(_cfg())
        provider._cdp = cdp.CDPClient(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/")
        await provider._cdp.connect()
        try:
            value = await provider._eval("tab", "document.title.length + 1", context_id)
        finally:
            await provider._cdp.close()
    assert value == 1
    assert [(r["method"], r["params"], r["sessionId"]) for r in requests] == [
        (
            "Runtime.evaluate",
            {
                "expression": "document.title.length + 1",
                "returnByValue": True,
                "awaitPromise": True,
                **context,
            },
            "tab",
        )
    ]


class _NoEvents(_ScriptedCDP):
    def on_event(self, method: str, callback: object, session_id: str | None = None) -> object:
        return lambda: None

    async def send(
        self,
        method: str,
        params: dict[str, object] | None = None,
        session_id: str | None = None,
    ) -> dict[str, object]:
        if method in {"Page.getFrameTree", "Page.createIsolatedWorld"}:
            self.calls.append((method, params, session_id))
            if method == "Page.getFrameTree":
                return {"frameTree": {"frame": {"id": "root"}}}
            return {"executionContextId": 7}
        return await super().send(method, params, session_id)


def _provider_collecting(
    monkeypatch: pytest.MonkeyPatch, collected: dict[str, str] | None
) -> tuple[engine.EngineProvider, _NoEvents]:
    async def public(url: str, cfg: Config) -> None:
        return None

    async def evaluate(sid: str, expression: str, context_id: int | None = None) -> object:
        if expression.startswith(f"({engine.DETECT_AND_COLLECT})"):
            if collected is None:
                await asyncio.Event().wait()
            return collected
        return "https://example.com/" if expression == "document.location.href" else 1

    monkeypatch.setattr(engine.safety, "check_url", public)
    monkeypatch.setattr(engine, "_DETECT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(engine, "_SETTLE_TIMEOUT_S", 0.05)
    provider = engine.EngineProvider(_cfg())
    fake = _NoEvents(_HEADLESS_UA, _HINTS)
    provider._cdp = fake
    monkeypatch.setattr(provider, "_eval", evaluate)
    return provider, fake


@pytest.mark.parametrize("failures", [1, 2])
async def test_hidden_text_detection_requires_an_isolated_world(
    monkeypatch: pytest.MonkeyPatch, failures: int
) -> None:
    provider, _ = _provider_collecting(monkeypatch, {"title": "Cats", "text": "Cats nap."})

    class RefusesIsolation(_NoEvents):
        async def send(
            self,
            method: str,
            params: dict[str, object] | None = None,
            session_id: str | None = None,
        ) -> dict[str, object]:
            if method == "Page.getFrameTree":
                self.calls.append((method, params, session_id))
                return {"frameTree": {"frame": {"id": "root"}}}
            if method == "Page.createIsolatedWorld":
                self.calls.append((method, params, session_id))
                if self.methods().count(method) <= failures:
                    raise cdp.CDPError("method unavailable")
                return {"executionContextId": 7}
            return await super().send(method, params, session_id)

    async def settled(sid: str, inflight: engine._InflightRequests) -> None:
        return None

    evaluations: list[tuple[str, int | None]] = []
    evaluate = provider._eval

    async def record_eval(sid: str, expression: str, context_id: int | None = None) -> object:
        evaluations.append((expression, context_id))
        return await evaluate(sid, expression, context_id)

    client = RefusesIsolation(_HEADLESS_UA, _HINTS)
    provider._cdp = client
    monkeypatch.setattr(provider, "_settle", settled)
    monkeypatch.setattr(provider, "_eval", record_eval)
    try:
        if failures == 2:
            with pytest.raises(
                cdp.CDPError, match="could not isolate hidden-text detection from the page"
            ):
                await provider.fetch("https://example.com/")
            assert evaluations == []
        else:
            page = await provider.fetch("https://example.com/")
            assert page.isolated
            assert page.text == "Cats nap."
            assert evaluations and all(ctx == 7 for _, ctx in evaluations)
        assert client.methods().count("Page.createIsolatedWorld") == 2
        assert client.methods()[-1] == "Target.closeTarget"
    finally:
        await provider.aclose()


async def test_a_collector_that_never_answers_fails_the_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, _ = _provider_collecting(monkeypatch, None)
    with pytest.raises(cdp.CDPError, match="hidden-text detection timed out"):
        await asyncio.wait_for(provider._fetch_in_target("https://example.com/", True), 5)


async def test_a_fetch_never_enables_the_runtime_or_console_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, fake = _provider_collecting(monkeypatch, {"title": "Cats", "text": "Cats nap."})
    page = await asyncio.wait_for(provider._fetch_in_target("https://example.com/", True), 5)
    assert page.text == "Cats nap."
    sent = fake.methods()
    assert {"Page.enable", "Network.enable", "Target.closeTarget"} <= set(sent)
    assert not {"Runtime.enable", "Console.enable"} & set(sent)


async def test_an_unanswered_evaluation_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    async def silent(ws: websockets.ServerConnection) -> None:
        await ws.wait_closed()

    monkeypatch.setattr(engine, "_EVAL_TIMEOUT_S", 0.05)
    async with websockets.serve(silent, "127.0.0.1", 0) as server:
        provider = engine.EngineProvider(_cfg())
        provider._cdp = cdp.CDPClient(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/")
        await provider._cdp.connect()
        operation = asyncio.create_task(provider._eval("tab", "1"))
        try:
            done, _ = await asyncio.wait({operation}, timeout=0.5)
            assert operation in done
            with pytest.raises(TimeoutError):
                operation.result()
        finally:
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            await provider.aclose()


@pytest.mark.parametrize("command", ["Page.getFrameTree", "Page.createIsolatedWorld"])
@pytest.mark.parametrize("failure", ["unanswered", "refused"])
async def test_unavailable_isolated_world_commands_degrade_without_hanging(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    failure: str,
) -> None:
    async def respond(ws: websockets.ServerConnection) -> None:
        async for raw in ws:
            request = json.loads(raw)
            if request["method"] == command:
                if failure == "refused":
                    await ws.send(json.dumps({
                        "id": request["id"],
                        "error": {"code": -32601, "message": "method unavailable"},
                    }))
            else:
                await ws.send(json.dumps({
                    "id": request["id"],
                    "result": {"frameTree": {"frame": {"id": "root"}}},
                }))

    monkeypatch.setattr(engine, "_EVAL_TIMEOUT_S", 0.05)
    async with websockets.serve(respond, "127.0.0.1", 0) as server:
        provider = engine.EngineProvider(_cfg())
        provider._cdp = cdp.CDPClient(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/")
        await provider._cdp.connect()
        operation = asyncio.create_task(provider._isolated_context("tab"))
        try:
            done, _ = await asyncio.wait({operation}, timeout=0.5)
            assert operation in done
            assert operation.result() is None
        finally:
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            await provider.aclose()


async def test_page_evaluation_exception_details_are_bounded() -> None:
    async def respond(ws: websockets.ServerConnection) -> None:
        async for raw in ws:
            request = json.loads(raw)
            details = {
                "exceptionId": 1,
                "text": "Error: " + "x" * 500,
                "lineNumber": 0,
                "columnNumber": 0,
            }
            await ws.send(json.dumps({
                "id": request["id"],
                "result": {"result": {"type": "undefined"}, "exceptionDetails": details},
            }))

    async with websockets.serve(respond, "127.0.0.1", 0) as server:
        provider = engine.EngineProvider(_cfg())
        provider._cdp = cdp.CDPClient(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/")
        await provider._cdp.connect()
        try:
            with pytest.raises(cdp.CDPError) as caught:
                await provider._eval("tab", "throw Error('payload')")
            assert len(str(caught.value)) <= engine.sanitize.MAX_ERROR_CHARS
            assert "x" * 50 in str(caught.value)
        finally:
            await provider.aclose()


def test_a_sub_frame_navigation_does_not_replace_the_main_documents_response() -> None:
    responses = engine._MainResponse()
    for loader, status in (("main", 200), ("child", 403)):
        responses._on_response({
            "requestId": loader, "loaderId": loader,
            "response": {"status": status, "mimeType": "text/html", "headers": {}},
        })
    responses._on_frame_navigated({"frame": {"id": "root", "loaderId": "main"}})
    responses._on_frame_navigated({"frame": {"id": "ad", "parentId": "root", "loaderId": "child"}})
    selected = responses.main(None)
    assert selected is not None and selected["status"] == 200


def test_an_unobserved_document_is_not_classified_from_another_documents_response() -> None:
    responses = engine._MainResponse()
    responses._on_response({
        "requestId": "observed", "loaderId": "observed",
        "response": {"status": 200, "mimeType": "text/html", "headers": {}},
    })
    for loader in ("unobserved", None):
        selected = responses.main(loader)
        assert engine.classify.classify(selected, False, "An article", "Some content") == "unknown"


async def _settle_against(
    monkeypatch: pytest.MonkeyPatch,
    stalls: str,
    stall_from: float = 0.0,
    stall_until: float = math.inf,
    busy: bool = False,
    reply_delay: float = 0.0,
) -> tuple[float, BaseException | None]:
    replies = {
        "Page.getFrameTree": json.loads((_FIXTURES / "cdp_frame_tree.json").read_text()),
        "Page.createIsolatedWorld": json.loads((_FIXTURES / "cdp_isolated_world.json").read_text()),
        "Runtime.evaluate": json.loads((_FIXTURES / "cdp_evaluate_result.json").read_text()),
        "Browser.getVersion": {"product": "Chrome/155"},
    }

    async def answer(ws: websockets.ServerConnection, request: dict[str, object]) -> None:
        if request["method"] == stalls and time.monotonic() - started >= stall_from:
            if stall_until == math.inf:
                return
            await asyncio.sleep(started + stall_until - time.monotonic())
        await asyncio.sleep(reply_delay)
        await ws.send(json.dumps({"id": request["id"], "result": replies[request["method"]]}))

    async def respond(ws: websockets.ServerConnection) -> None:
        async with asyncio.TaskGroup() as group:
            async for raw in ws:
                group.create_task(answer(ws, json.loads(raw)))

    monkeypatch.setattr(engine, "_SETTLE_TIMEOUT_S", 0.3)
    monkeypatch.setattr(engine, "_SETTLE_STALL_S", 0.2)
    monkeypatch.setattr(engine, "_SETTLE_POLL_S", 0.01)
    server = await websockets.serve(respond, "127.0.0.1", 0)
    provider = engine.EngineProvider(_cfg())
    provider._cdp = cdp.CDPClient(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/")
    await provider._cdp.connect()
    inflight = engine._InflightRequests()
    if busy:
        inflight._started({"requestId": "never-finishes"})
    error: BaseException | None = None
    started = time.monotonic()
    try:
        try:
            await provider._settle("tab", inflight)
        except TimeoutError as exc:
            error = exc
        elapsed = time.monotonic() - started
        await asyncio.sleep(1.0)
        assert (await provider._cdp.send("Browser.getVersion")) == {"product": "Chrome/155"}
        assert not provider._cdp.closed
    finally:
        await provider._cdp.close()
        server.close()
    return elapsed, error


async def test_settling_never_evaluates_in_the_page_world_when_isolation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    methods: list[str] = []
    frame_tree = json.loads((_FIXTURES / "cdp_frame_tree.json").read_text())

    async def respond(ws: websockets.ServerConnection) -> None:
        async for raw in ws:
            request = json.loads(raw)
            methods.append(request["method"])
            if request["method"] == "Page.getFrameTree":
                await ws.send(json.dumps({"id": request["id"], "result": frame_tree}))
            else:
                error = {"code": -32000, "message": "refused"}
                await ws.send(json.dumps({"id": request["id"], "error": error}))

    monkeypatch.setattr(engine, "_SETTLE_TIMEOUT_S", 0.2)
    monkeypatch.setattr(engine, "_SETTLE_STALL_S", 0.1)
    monkeypatch.setattr(engine, "_SETTLE_POLL_S", 0.01)
    async with websockets.serve(respond, "127.0.0.1", 0) as server:
        provider = engine.EngineProvider(_cfg())
        provider._cdp = cdp.CDPClient(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/")
        await provider._cdp.connect()
        try:
            await provider._settle("tab", engine._InflightRequests())
        finally:
            await provider._cdp.close()

    assert "Page.createIsolatedWorld" in methods
    assert "Runtime.evaluate" not in methods


@pytest.mark.parametrize(
    "stalls", ["Runtime.evaluate", "Page.getFrameTree", "Page.createIsolatedWorld"]
)
async def test_a_page_that_stops_answering_fails_the_fetch_at_the_settle_cap(
    monkeypatch: pytest.MonkeyPatch, stalls: str
) -> None:
    elapsed, error = await _settle_against(monkeypatch, stalls)
    assert isinstance(error, TimeoutError)
    assert elapsed < 1.0


async def test_a_reply_arriving_after_settling_gave_up_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elapsed, error = await _settle_against(monkeypatch, "Runtime.evaluate", stall_until=1.2)
    assert isinstance(error, TimeoutError)
    assert elapsed < 1.0


async def test_a_page_that_stops_answering_just_before_the_cap_fails_the_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elapsed, error = await _settle_against(monkeypatch, "Runtime.evaluate", stall_from=0.2)
    assert isinstance(error, TimeoutError)
    assert elapsed < 1.0


async def test_a_page_that_answers_again_soon_after_the_cap_settles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elapsed, error = await _settle_against(monkeypatch, "Runtime.evaluate", stall_until=0.4)
    assert error is None
    assert 0.4 <= elapsed < 1.0


async def test_a_page_that_answers_but_never_goes_quiet_settles_at_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elapsed, error = await _settle_against(monkeypatch, "", busy=True)
    assert error is None
    assert 0.3 <= elapsed < 1.0


async def test_a_slow_page_that_never_goes_quiet_settles_soon_after_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elapsed, error = await _settle_against(monkeypatch, "", busy=True, reply_delay=0.08)
    assert error is None
    assert elapsed < 1.0


async def test_an_evaluation_timeout_inside_settling_still_fails_the_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine, "_EVAL_TIMEOUT_S", 0.05)
    monkeypatch.setattr(engine, "_SETTLE_TIMEOUT_S", 5.0)
    fake = _ScriptedCDP(_HEADLESS_UA, _HINTS)
    provider = engine.EngineProvider(_cfg())
    provider._cdp = fake

    async def stall(method: str, params: object = None, session_id: object = None) -> object:
        if method == "Runtime.evaluate":
            await asyncio.Event().wait()
        return {"frameTree": {"frame": {"id": "root"}}, "executionContextId": 1}

    monkeypatch.setattr(fake, "send", stall)
    with pytest.raises(TimeoutError):
        await provider._settle("tab", engine._InflightRequests())


async def test_a_page_answering_with_errors_at_the_cap_settles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine, "_SETTLE_TIMEOUT_S", 0.1)
    fake = _ScriptedCDP(_HEADLESS_UA, _HINTS)
    provider = engine.EngineProvider(_cfg())
    provider._cdp = fake

    async def failing(method: str, params: object = None, session_id: object = None) -> object:
        if method == "Runtime.evaluate":
            raise cdp.CDPError("Cannot find context with specified id")
        return {}

    monkeypatch.setattr(fake, "send", failing)
    await asyncio.wait_for(provider._settle("tab", engine._InflightRequests()), 5)


async def test_a_connection_lost_while_waiting_past_the_cap_fails_the_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine, "_SETTLE_TIMEOUT_S", 0.1)
    fake = _ScriptedCDP(_HEADLESS_UA, _HINTS)
    provider = engine.EngineProvider(_cfg())
    provider._cdp = fake
    calls = 0
    deadline = time.monotonic() + 0.05

    async def drops(method: str, params: object = None, session_id: object = None) -> object:
        nonlocal calls
        if method != "Runtime.evaluate":
            return {"frameTree": {"frame": {"id": "root"}}, "executionContextId": 1}
        if time.monotonic() >= deadline:
            fake.closed = True
            raise cdp.CDPError("CDP client is not connected")
        calls += 1
        return {"result": {"type": "number", "value": calls}}

    monkeypatch.setattr(fake, "send", drops)
    with pytest.raises(cdp.CDPError):
        await asyncio.wait_for(provider._settle("tab", engine._InflightRequests()), 5)


async def test_settling_on_a_closed_connection_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "_SETTLE_TIMEOUT_S", 0.1)
    fake = _ScriptedCDP(_HEADLESS_UA, _HINTS)
    provider = engine.EngineProvider(_cfg())
    provider._cdp = fake

    async def dead(method: str, params: object = None, session_id: object = None) -> object:
        fake.closed = True
        raise cdp.CDPError("CDP client is not connected")

    monkeypatch.setattr(fake, "send", dead)
    with pytest.raises(cdp.CDPError):
        await provider._settle("tab", engine._InflightRequests())


class _FailingOn(_NoEvents):
    def __init__(self, failing: str, error: Exception | None = None) -> None:
        super().__init__(_HEADLESS_UA, _HINTS)
        self.failing = failing
        self.error = error or cdp.CDPError(f"{failing}: No target with given id")

    async def send(self, method, params=None, session_id=None):
        if method == self.failing:
            self.calls.append((method, params, session_id))
            raise self.error
        return await super().send(method, params, session_id)


@pytest.mark.parametrize(
    "error",
    [
        cdp.CDPError("Target.closeTarget: No target with given id"),
        websockets.exceptions.ConnectionClosedError(None, None),
    ],
)
async def test_a_tab_that_closed_itself_does_not_lose_the_page_it_returned(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    provider, _ = _provider_collecting(monkeypatch, {"title": "Cats", "text": "Cats nap."})
    provider._cdp = _FailingOn("Target.closeTarget", error)

    page = await asyncio.wait_for(provider._fetch_in_target("https://example.com/", True), 5)

    assert page.text == "Cats nap."


async def test_a_tab_that_could_not_be_attached_is_still_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, _ = _provider_collecting(monkeypatch, {"title": "Cats", "text": "Cats nap."})
    failing = _FailingOn("Target.attachToTarget")
    provider._cdp = failing

    with pytest.raises(cdp.CDPError, match="attachToTarget"):
        await provider._fetch_in_target("https://example.com/", True)

    assert failing.methods()[-1] == "Target.closeTarget"


def test_grouping_by_site_never_reaches_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def offline(*args: object, **kwargs: object) -> object:
        raise AssertionError("the public suffix list was fetched")

    monkeypatch.setattr(requests.Session, "get", offline)
    monkeypatch.setattr(
        tldextract.tldextract, "TLD_EXTRACTOR", tldextract.TLDExtract(cache_dir=None)
    )
    engine._suffixes.cache_clear()

    assert engine.registrable_domain("https://news.bbc.co.uk/x") == "bbc.co.uk"
