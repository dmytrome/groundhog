from groundhog_mcp import server


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
