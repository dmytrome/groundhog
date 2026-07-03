import pytest

from groundhog_mcp import engine, server


class _Spy:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


async def test_lifespan_closes_provider_on_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _Spy()
    monkeypatch.setattr(engine, "_provider", spy)
    mcp = server.build_server()
    async with server._lifespan(mcp):
        pass
    assert spy.closed is True
    assert engine._provider is None
