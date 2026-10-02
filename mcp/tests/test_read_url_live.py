import os

import pytest

from groundhog_mcp import engine
from groundhog_mcp.tools.read_url import read_url

from .test_engine_live import PAGE_HOST, _serve_paths

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE") != "1",
    reason="requires the engine running; set RUN_LIVE=1 and CDP_URL",
)


@pytest.fixture(autouse=True)
async def _close_provider():
    # read_url uses a lazy singleton provider; close it after each test so the
    # CDP connection does not keep the process alive.
    yield
    await engine.shutdown_provider()


async def test_read_url_returns_markdown_and_provenance():
    result = await read_url("https://example.com/")
    assert "Example Domain" in result["title"]
    assert "documentation examples" in result["markdown"]
    assert result["url"] == "https://example.com/"
    assert result["final_url"].startswith("https://example.com")
    assert result["fetched_at"].endswith("+00:00")
    assert result["truncated"] is False


async def test_read_url_text_format():
    result = await read_url("https://example.com/", format="text")
    assert "documentation examples" in result["markdown"]


async def test_read_url_populates_provenance_and_threats():
    result = await read_url("https://example.com/")
    assert isinstance(result["threats"], list)
    assert result["matches"] == []
    assert len(result["provenance"]["content_hash"]) == 64


async def test_read_url_detects_the_language_of_the_page_it_read(monkeypatch):
    monkeypatch.setenv("GROUNDHOG_BLOCK_PRIVATE_IPS", "false")
    await engine.shutdown_provider()
    paragraph = (
        "Tomatoes need full sun, steady watering and a sturdy stake once the first trusses "
        "set fruit. Pinch out the side shoots every week so the plant puts its strength "
        "into the fruit rather than the leaves. "
    )
    article = (
        "<html><body><article><h1>Growing tomatoes</h1>"
        f"<p>{paragraph * 3}</p></article></body></html>"
    )
    srv = _serve_paths({"/": (200, article)})
    try:
        result = await read_url(f"http://{PAGE_HOST}:{srv.server_address[1]}/")
    finally:
        srv.shutdown()

    assert result["provenance"]["language"] == "en"
