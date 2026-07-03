import dataclasses
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from groundhog_mcp import engine
from groundhog_mcp.config import load_config
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


def _serve(body: str) -> ThreadingHTTPServer:
    payload = body.encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    # Bind all interfaces so the containerized Chrome can reach the host via
    # host.docker.internal (Docker Desktop; on Linux needs host-gateway mapping).
    srv = ThreadingHTTPServer(("", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


async def test_detect_and_collect_finds_and_removes_hidden_text():
    srv = _serve(HIDDEN_HTML)
    port = srv.server_address[1]
    cfg = dataclasses.replace(load_config(), block_private_ips=False)
    provider = EngineProvider(cfg)
    await provider.start()
    try:
        page = await provider.fetch(f"http://host.docker.internal:{port}/")
        assert any("SECRET INJECTION" in h["text"] for h in page.hidden_spans)
        assert page.meta["lang"] == "en"
        assert page.meta["meta"].get("author") == "T"
        assert "SECRET INJECTION" not in page.html
    finally:
        await provider.aclose()
        srv.shutdown()


async def test_fetch_exposes_hidden_spans_and_meta():
    provider = engine.EngineProvider(load_config())
    await provider.start()
    try:
        page = await provider.fetch("https://example.com/")
        assert isinstance(page.hidden_spans, list)
        assert set(page.meta) == {"meta", "lang", "canonical"}
    finally:
        await provider.aclose()
