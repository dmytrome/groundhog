from mcp.server.fastmcp import FastMCP

from .tools.read_url import read_url
from .tools.status import status


def build_server() -> FastMCP:
    mcp = FastMCP("groundhog")
    mcp.tool()(read_url)
    mcp.tool()(status)
    return mcp
