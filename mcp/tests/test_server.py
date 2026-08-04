from importlib.metadata import version

from groundhog_mcp import server


async def test_the_handshake_reports_our_version_not_the_sdks():
    # The low-level server falls back to `pkg_version("mcp")` when its own version is
    # unset, and FastMCP takes no version argument — so a client asking who it is
    # connected to was told the SDK's version. Asserted through the initialization
    # options a client actually receives, not the attribute we set.
    ours = version("groundhog-mcp")
    options = server.build_server()._mcp_server.create_initialization_options()
    assert options.server_name == "groundhog"
    assert options.server_version == ours
    assert options.server_version != version("mcp")


async def test_every_tool_is_annotated_read_only():
    # Annotations drive a client's auto-approval and are a hard requirement for the
    # Claude Connectors Directory and the MCPB bundle. Assert the metadata the client
    # actually reads, via list_tools, rather than the registration call.
    mcp = server.build_server()
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    assert set(tools) == {"read_url", "research", "search", "status"}
    for tool in tools.values():
        assert tool.annotations is not None, f"{tool.name} has no annotations"
        assert tool.annotations.readOnlyHint is True
        # Both title forms: a client following 2025-06-18 reads `Tool.title` first and
        # only falls back to the annotation, so one alone leaves some clients showing
        # the bare function name.
        assert tool.title, f"{tool.name} has no top-level title"
        assert tool.annotations.title == tool.title


async def test_fetching_tools_are_open_world_and_status_is_not():
    mcp = server.build_server()
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    for name in ("read_url", "research", "search"):
        assert tools[name].annotations.openWorldHint is True, name
    # `status` only probes the local browser; it does not reach the open web.
    assert tools["status"].annotations.openWorldHint is False
