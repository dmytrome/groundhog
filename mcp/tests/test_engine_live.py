import os

import pytest

from groundhog_mcp import engine
from groundhog_mcp.config import load_config
from groundhog_mcp.detect_js import DETECT_AND_COLLECT
from groundhog_mcp.engine import EngineProvider
from groundhog_mcp.safety import BlockedURLError

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE") != "1",
    reason="requires the engine running; set RUN_LIVE=1 and CDP_URL",
)


async def test_fetch_example_com():
    provider = EngineProvider(load_config())
    await provider.start()
    try:
        page = await provider.fetch("https://example.com/")
        assert "Example Domain" in page.title
        assert "Example Domain" in page.html
        assert page.final_url.startswith("https://example.com")
        assert "Example Domain" in page.text
    finally:
        await provider.aclose()


async def test_fetch_blocks_internal():
    provider = EngineProvider(load_config())
    await provider.start()
    try:
        with pytest.raises(BlockedURLError):
            await provider.fetch("http://169.254.169.254/")
    finally:
        await provider.aclose()


HIDDEN_HTML = (
    "<html lang='en'><head><meta name='author' content='T'></head><body>"
    "<p>Visible paragraph content here.</p>"
    "<div style='display:none'>SECRET INJECTION PAYLOAD</div>"
    "</body></html>"
)


async def test_detect_and_collect_finds_and_removes_hidden_text():
    provider = engine.EngineProvider(load_config())
    await provider.start()
    try:
        page = await provider._context.new_page()
        await page.set_content(HIDDEN_HTML)
        result = await page.evaluate(DETECT_AND_COLLECT, True)
        assert any("SECRET INJECTION" in h["text"] for h in result["hidden"])
        assert result["lang"] == "en"
        assert result["meta"].get("author") == "T"
        assert "SECRET INJECTION" not in await page.content()
        await page.close()
    finally:
        await provider.aclose()


async def test_fetch_exposes_hidden_spans_and_meta():
    provider = engine.EngineProvider(load_config())
    await provider.start()
    try:
        page = await provider.fetch("https://example.com/")
        assert isinstance(page.hidden_spans, list)
        assert set(page.meta) == {"meta", "lang", "canonical"}
    finally:
        await provider.aclose()
