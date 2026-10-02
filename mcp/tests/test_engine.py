import asyncio
import json
import subprocess
import sys

import pytest
import websockets

from groundhog_mcp import engine
from groundhog_mcp.config import Config


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


async def test_start_browser_builds_docker_run(monkeypatch):
    calls: list[list[str]] = []
    _stub_start(monkeypatch, calls)
    await engine._start_browser(_cfg(cdp_url="http://127.0.0.1:7000", browser_image="img:tag"))
    assert calls[0] == ["docker", "rm", "-f", engine._CONTAINER_NAME]  # clears a stale container
    run = calls[1]
    assert run[:3] == ["docker", "run", "-d"]
    assert engine._CONTAINER_NAME in run
    assert f"{engine._CONTAINER_BIND_HOST}:7000:{engine._CONTAINER_CDP_PORT}" in run
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
    async def fake_launch(argv):
        launched.append(argv)

    async def answers(url, timeout=engine._PROBE_TIMEOUT_S):
        return ready

    async def no_docker(cmd):
        pytest.fail(f"the chrome backend ran {cmd}")

    monkeypatch.setattr(engine, "_launch_detached", fake_launch)
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


def test_a_headless_browser_is_given_the_identity_of_the_same_chrome_with_a_window():
    identity = engine._headful_identity(_HEADLESS_UA, _HINTS)
    assert identity is not None
    assert "HeadlessChrome" not in identity["userAgent"]
    assert identity["userAgent"].endswith("Chrome/154.0.0.0 Safari/537.36")
    metadata = identity["userAgentMetadata"]
    assert metadata["brands"] == _HINTS["brands"]
    assert metadata["platformVersion"] == "26.5.1" and "secure" not in metadata


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
    assert identity == engine._headful_identity(_HEADLESS_UA, _HINTS)
    opened = next(p for m, p, _ in cdp.calls if m == "Target.createTarget")
    assert opened == {"url": engine._HINTS_PAGE}
    assert cdp.methods()[-1] == "Target.closeTarget"


async def test_a_browser_with_a_window_is_not_probed_for_an_identity():
    cdp = _ScriptedCDP(_HEADLESS_UA.replace("HeadlessChrome/", "Chrome/"), _HINTS)
    assert await engine._read_identity(cdp) is None
    assert cdp.methods() == ["Browser.getVersion"]


async def test_starting_against_a_headless_browser_arms_the_identity(monkeypatch):
    cdp = _ScriptedCDP(_HEADLESS_UA, _HINTS)
    monkeypatch.setattr(engine, "CDPClient", lambda ws_url: cdp)
    provider = engine.EngineProvider(_cfg(cdp_url="ws://127.0.0.1:9/devtools/browser/x"))
    await provider.start()
    assert provider._identity == engine._headful_identity(_HEADLESS_UA, _HINTS)


@pytest.mark.parametrize("headless", [True, False])
async def test_each_tab_takes_the_identity_before_anything_loads(monkeypatch, headless):
    user_agent = _HEADLESS_UA if headless else _HEADLESS_UA.replace("HeadlessChrome/", "Chrome/")
    cdp = _ScriptedCDP(user_agent, _HINTS)
    monkeypatch.setattr(engine, "CDPClient", lambda ws_url: cdp)
    provider = engine.EngineProvider(
        _cfg(cdp_url="ws://127.0.0.1:9/devtools/browser/x", block_private_ips=False)
    )
    await provider.start()
    cdp.calls.clear()
    await provider._prepare_tab("tab")
    expected = ["Page.enable", "Network.enable"]
    if headless:
        expected = ["Emulation.setUserAgentOverride", *expected]
        assert cdp.calls[0][1] == provider._identity
    assert cdp.methods() == expected
    assert all(session == "tab" for _, _, session in cdp.calls)


def test_a_fresh_process_reports_an_unreachable_browser_instead_of_raising():
    probe = (
        "import asyncio; from groundhog_mcp import engine; "
        "print(asyncio.run(engine.check_browser('http://127.0.0.1:1')), "
        "asyncio.run(engine.check_browser('ws://127.0.0.1:1/devtools/browser/x')))"
    )
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == ["False", "False"]


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
async def test_documents_are_intercepted_only_while_private_addresses_are_blocked(
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
    assert fetch == (
        [{"patterns": [{"urlPattern": "*", "resourceType": "Document"}]}] if block else []
    )
