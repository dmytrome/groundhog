import asyncio

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
