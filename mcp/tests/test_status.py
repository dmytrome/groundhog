import pytest

from groundhog_mcp import engine
from groundhog_mcp.config import load_config
from groundhog_mcp.engine import BrowserUnavailableError, EngineProvider, check_browser
from groundhog_mcp.tools.status import status

_UNREACHABLE = "http://127.0.0.1:1"


async def test_check_browser_unreachable():
    assert await check_browser(_UNREACHABLE) is False


async def test_status_reports_unreachable(monkeypatch):
    monkeypatch.setenv("CDP_URL", _UNREACHABLE)
    result = await status()
    assert result["browser_reachable"] is False
    assert result["cdp_url"] == _UNREACHABLE
    assert result["hint"]


async def test_start_raises_actionable_error(monkeypatch):
    monkeypatch.setenv("CDP_URL", _UNREACHABLE)
    monkeypatch.setenv("GROUNDHOG_AUTO_START_BROWSER", "false")
    provider = EngineProvider(load_config())
    try:
        with pytest.raises(BrowserUnavailableError, match="hosted Groundhog browser"):
            await provider.start()
    finally:
        await provider.aclose()


async def test_no_container_runtime_message(monkeypatch):
    monkeypatch.setenv("CDP_URL", _UNREACHABLE)
    monkeypatch.delenv("GROUNDHOG_AUTO_START_BROWSER", raising=False)  # default-on
    monkeypatch.setattr(engine, "_container_runtime", lambda: None)  # no docker/podman
    provider = EngineProvider(load_config())
    try:
        with pytest.raises(BrowserUnavailableError, match="No container runtime"):
            await provider.start()
    finally:
        await provider.aclose()


@pytest.mark.parametrize("value", ["127.0.0.1:9222", "http://host:abc"])
async def test_a_misconfigured_cdp_url_is_still_shown(monkeypatch, value):
    # `urlparse` reads a scheme-less `host:port` as a scheme, so naive redaction
    # erased the very value the operator has to fix. A bad port must not raise either.
    monkeypatch.setenv("CDP_URL", value)
    monkeypatch.setenv("GROUNDHOG_AUTO_START_BROWSER", "false")
    result = await status()
    assert result["cdp_url"], f"redaction erased {value!r}"


async def test_a_credential_bearing_cdp_url_is_redacted(monkeypatch):
    monkeypatch.setenv("CDP_URL", "wss://user:secret@hosted.example/cdp?token=SECRET")
    monkeypatch.setenv("GROUNDHOG_AUTO_START_BROWSER", "false")
    result = await status()
    assert "secret" not in result["cdp_url"].lower()
    assert "hosted.example" in result["cdp_url"]
