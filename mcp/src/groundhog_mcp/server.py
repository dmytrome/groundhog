from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from . import engine
from .prompts import audit_hidden_text
from .tools.read_url import read_url
from .tools.research import research
from .tools.search import search
from .tools.status import status


@asynccontextmanager
async def _lifespan(_server: FastMCP) -> AsyncIterator[dict[str, object]]:
    try:
        yield {}
    finally:
        await engine.shutdown_provider()


def build_server() -> FastMCP:
    mcp = FastMCP("groundhog", lifespan=_lifespan)
    # All four tools are read-only. The three that fetch reach arbitrary external hosts
    # (openWorldHint); `status` only probes the local browser. readOnlyHint is what lets
    # a client auto-approve these without a per-call confirmation, and titles/annotations
    # are a hard requirement for the Claude Connectors Directory and the MCPB bundle.
    mcp.tool(
        annotations=ToolAnnotations(title="Read a URL", readOnlyHint=True, openWorldHint=True)
    )(read_url)
    mcp.tool(
        annotations=ToolAnnotations(title="Research the web", readOnlyHint=True, openWorldHint=True)
    )(research)
    mcp.tool(
        annotations=ToolAnnotations(title="Web search", readOnlyHint=True, openWorldHint=True)
    )(search)
    mcp.tool(
        annotations=ToolAnnotations(title="Browser status", readOnlyHint=True, openWorldHint=False)
    )(status)
    mcp.prompt()(audit_hidden_text)
    return mcp
