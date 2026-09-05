from importlib.metadata import version

import pytest

from groundhog_mcp import engine, server


async def test_the_handshake_reports_our_version_not_empty_and_not_the_sdks():
    ours = version("groundhog-mcp")
    options = server.build_server()._lowlevel_server.create_initialization_options()
    assert options.server_name == "groundhog"
    assert options.server_version == ours
    assert options.server_version not in ("", version("mcp"))


async def test_every_tool_is_annotated_read_only():
    # Annotations drive a client's auto-approval and are a hard requirement for the
    # Claude Connectors Directory and the MCPB bundle. Assert the metadata the client
    # actually reads, via list_tools, rather than the registration call.
    mcp = server.build_server()
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    assert set(tools) == {"read_url", "research", "search", "status"}
    for tool in tools.values():
        assert tool.annotations is not None, f"{tool.name} has no annotations"
        assert tool.annotations.read_only_hint is True
        # Both title forms: a client following 2025-06-18 reads `Tool.title` first and
        # only falls back to the annotation, so one alone leaves some clients showing
        # the bare function name.
        assert tool.title, f"{tool.name} has no top-level title"
        assert tool.annotations.title == tool.title


async def test_fetching_tools_are_open_world_and_status_is_not():
    mcp = server.build_server()
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    for name in ("read_url", "research", "search"):
        assert tools[name].annotations.open_world_hint is True, name
    # `status` only probes the local browser; it does not reach the open web.
    assert tools["status"].annotations.open_world_hint is False


async def test_an_argument_the_caller_can_correct_is_explained_through_the_boundary():
    mcp = server.build_server()
    with pytest.raises(Exception, match="query must not be empty"):
        await mcp.call_tool("search", {"query": "   "})


async def test_browser_remediation_survives_the_boundary(monkeypatch: pytest.MonkeyPatch):
    async def _unavailable() -> object:
        raise engine.BrowserUnavailableError("start it with `docker run ...`")

    monkeypatch.setattr(engine, "get_provider", _unavailable)
    mcp = server.build_server()
    with pytest.raises(Exception, match="docker run"):
        await mcp.call_tool("read_url", {"url": "https://example.com/"})


async def test_the_ssrf_guards_internal_detail_does_not_cross_the_boundary():
    mcp = server.build_server()
    with pytest.raises(Exception) as caught:
        await mcp.call_tool("read_url", {"url": "http://169.254.169.254/latest/meta-data/"})
    message = str(caught.value)
    assert "blocked" in message
    assert "169.254" not in message
