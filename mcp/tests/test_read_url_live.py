import os

import pytest

from groundhog_mcp import engine
from groundhog_mcp.tools.read_url import read_url

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
    # The "text" format returns the page's visible text, which includes the h1.
    result = await read_url("https://example.com/", format="text")
    assert "Example Domain" in result["markdown"]


async def test_read_url_populates_provenance_and_threats():
    result = await read_url("https://example.com/")
    assert isinstance(result["threats"], list)
    assert result["matches"] == []
    assert len(result["provenance"]["content_hash"]) == 64
    assert result["provenance"]["language"] == "en"
