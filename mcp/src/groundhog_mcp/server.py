from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

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
    mcp.tool()(read_url)
    mcp.tool()(research)
    mcp.tool()(search)
    mcp.tool()(status)
    mcp.prompt()(audit_hidden_text)
    return mcp
