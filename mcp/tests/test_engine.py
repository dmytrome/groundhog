import pytest

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
    assert engine._CONTAINER_PLATFORM in run
    assert run[-2:] == ["--", "img:tag"]  # `--` guards against a flag-like image ref


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
