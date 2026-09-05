import functools
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from importlib.metadata import version

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import engine
from .prompts import audit_hidden_text
from .search import SearchUnavailableError
from .tools.read_url import read_url
from .tools.research import research
from .tools.search import search
from .tools.status import status


@asynccontextmanager
async def _lifespan(_server: MCPServer) -> AsyncIterator[dict[str, object]]:
    try:
        yield {}
    finally:
        await engine.shutdown_provider()


_RAISED_TO_BE_READ = (
    engine.BrowserUnavailableError,
    SearchUnavailableError,
    ValueError,
    RuntimeError,
)


def _surfaced(tool: Callable[..., object]) -> Callable[..., object]:
    @functools.wraps(tool)
    async def surfaced(*args: object, **kwargs: object) -> object:
        try:
            return await tool(*args, **kwargs)
        except _RAISED_TO_BE_READ as exc:
            raise ToolError(str(exc)) from exc

    return surfaced


def _register_read_only(
    mcp: MCPServer, tool: Callable[..., object], title: str, *, open_world: bool
) -> None:
    """Register a read-only tool, carrying its title in both places the spec allows.

    A client following 2025-06-18 reads `Tool.title` and only falls back to
    `annotations.title`, so setting one alone shows some clients a bare function name.
    Writing the title once here is also what keeps the two from drifting apart.
    """
    mcp.tool(
        title=title,
        annotations=ToolAnnotations(
            title=title, read_only_hint=True, open_world_hint=open_world
        ),
    )(_surfaced(tool))


def build_server() -> MCPServer:
    mcp = MCPServer("groundhog", version=version("groundhog-mcp"), lifespan=_lifespan)
    # All four tools are read-only. The three that fetch reach arbitrary external hosts
    # (openWorldHint); `status` only probes the local browser. readOnlyHint is what lets
    # a client auto-approve these without a per-call confirmation, and titles/annotations
    # are a hard requirement for the Claude Connectors Directory and the MCPB bundle.
    _register_read_only(mcp, read_url, "Read a URL", open_world=True)
    _register_read_only(mcp, research, "Research the web", open_world=True)
    _register_read_only(mcp, search, "Web search", open_world=True)
    _register_read_only(mcp, status, "Browser status", open_world=False)
    mcp.prompt()(audit_hidden_text)
    return mcp
