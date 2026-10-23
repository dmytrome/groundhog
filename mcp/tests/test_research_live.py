import os

import pytest

from groundhog_mcp import engine, search, tokens
from groundhog_mcp.search import SearchHit
from groundhog_mcp.tools.research import research

from .test_engine_live import PAGE_HOST, _serve_paths

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE") != "1",
    reason="requires the engine running; set RUN_LIVE=1 and CDP_URL",
)

_STUDY = (
    "<html><body><article><h1>Sleep</h1><p>"
    + "Cats nap for sixteen hours a day and hunt at dusk, the sleep study found. " * 4
    + "</p></article></body></html>"
)


def _hub(port: int) -> str:
    base = f"http://{PAGE_HOST}:{port}"
    return (
        "<html><body><article><h1>Cats</h1><p>"
        + "Cats nap through the afternoon, as house cats usually do. " * 3
        + f'Cats nap most of the day, says <a href="{base}/study">the sleep study</a>.'
        + f'<span style="display:none">Cats nap <a href="{base}/secret">secret</a></span>'
        + "</p></article></body></html>"
    )


@pytest.fixture(autouse=True)
async def _close_provider():
    yield
    await engine.shutdown_provider()


def _searching(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    hit: SearchHit = {
        "title": "Cats",
        "url": url,
        "snippet": "",
        "engine": "test",
        "score": 1.0,
        "published": None,
    }

    async def _search(query: str, cfg: object, limit: int) -> tuple[list[SearchHit], str]:
        return [hit], "test"

    monkeypatch.setattr(search, "search", _search)


async def test_a_visible_link_is_offered_and_following_it_reads_the_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GROUNDHOG_BLOCK_PRIVATE_IPS", "false")
    monkeypatch.setenv("GROUNDHOG_MIN_DELAY_MS", "0")
    await engine.shutdown_provider()
    routes = {"/study": (200, _STUDY)}
    srv = _serve_paths(routes)
    base = f"http://{PAGE_HOST}:{srv.server_address[1]}"
    routes["/"] = (200, _hub(srv.server_address[1]))
    _searching(monkeypatch, f"{base}/")
    try:
        offered = await research("cats nap")
        urls = [card["url"] for card in offered["candidates"]]
        assert urls == [f"{base}/study"]
        followed = await research(
            "cats nap",
            follow=[{"token": offered["candidates"][0]["token"], "why": "how long cats sleep"}],
        )
    finally:
        srv.shutdown()

    assert followed["followed"][0]["outcome"] == "read"
    (source,) = followed["sources"]
    assert source["depth"] == 1
    assert any("sixteen hours" in passage["text"] for passage in followed["passages"])


async def test_a_token_for_a_private_address_is_still_refused() -> None:
    url = "http://127.0.0.1:9/"
    token = tokens.issue(
        fetch_url=url,
        identity=url,
        depth=1,
        parent_source_url="https://example.com/",
        parent_passage_id="p:x:0-1",
        label="internal",
    )

    result = await research("cats", follow=[{"token": token, "why": "reach the host"}])

    assert result["followed"][0]["outcome"] == "failed"
    assert result["sources"][0]["status"] == "blocked"
