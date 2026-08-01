import pytest

from groundhog_mcp import engine, search as search_module
from groundhog_mcp.tools.search import search


def _hit(url: str) -> dict:
    return {
        "title": "T",
        "url": url,
        "snippet": "s",
        "engine": "duckduckgo",
        "score": 1.0,
        "published": None,
    }


@pytest.fixture
def fake_backend(monkeypatch):
    def _install(hits):
        async def _search(query, cfg, limit):
            return hits[:limit], "serp"

        monkeypatch.setattr(search_module, "search", _search)

    return _install


async def test_returns_hits_and_the_backend_used(fake_backend):
    fake_backend([_hit("https://a.example/"), _hit("https://b.example/")])
    result = await search("anything")
    assert [h["url"] for h in result["hits"]] == ["https://a.example/", "https://b.example/"]
    assert result["backend"] == "serp"
    assert result["query"] == "anything"


async def test_limit_caps_the_hits(fake_backend):
    fake_backend([_hit(f"https://{n}.example/") for n in range(10)])
    result = await search("anything", limit=3)
    assert len(result["hits"]) == 3


async def test_rejects_an_empty_query():
    with pytest.raises(ValueError, match="query"):
        await search("   ")


async def test_browser_remediation_reaches_the_caller_intact(monkeypatch):
    # The SERP backend runs through the browser, so a browser-down message arrives
    # here. It is our own text and the caller copy-pastes the command out of it —
    # truncating it to 200 chars cuts the docker image name mid-token.
    hint = "Cannot reach the stealth browser at http://127.0.0.1:9222. " + "x" * 400

    async def _boom(query, cfg, limit):
        raise engine.BrowserUnavailableError(hint)

    monkeypatch.setattr(search_module, "search", _boom)
    with pytest.raises(engine.BrowserUnavailableError) as excinfo:
        await search("cats")
    assert str(excinfo.value) == hint
