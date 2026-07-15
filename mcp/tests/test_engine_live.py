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


async def _fetch_local(html: str) -> engine.RenderedPage:
    """Serve `html` from the host and fetch it through the containerized browser."""
    srv = _serve(html)
    cfg = dataclasses.replace(load_config(), block_private_ips=False)
    provider = EngineProvider(cfg)
    await provider.start()
    try:
        return await provider.fetch(f"http://host.docker.internal:{srv.server_address[1]}/")
    finally:
        await provider.aclose()
        srv.shutdown()


async def test_detect_and_collect_finds_and_removes_hidden_text():
    page = await _fetch_local(HIDDEN_HTML)
    assert any("SECRET INJECTION" in h["text"] for h in page.hidden_spans)
    assert page.meta["lang"] == "en"
    assert page.meta["meta"].get("author") == "T"
    assert "SECRET INJECTION" not in page.html


ADVANCED_HIDING_HTML = """<html><body>
<p>Visible paragraph content here for baseline.</p>
<p style="color:#767676">Ordinary muted gray text, still clearly readable MUTED TEXT MARKER</p>
<div style="color:#ffffff;background:#ffffff">WHITE ON WHITE PAYLOAD MARKER</div>
<span style="width:1px;height:1px;overflow:hidden;position:absolute">SR ONLY PAYLOAD MARKER</span>
<div style="clip:rect(0,0,0,0);position:absolute">CLIP RECT PAYLOAD MARKER</div>
<div style="position:absolute;left:-9999px;top:-9999px">OFFSCREEN PAYLOAD MARKER</div>
<div style="height:3000px"></div>
<p style="margin-top:10px">BELOW THE FOLD LEGIT CONTENT MARKER</p>
<!-- a sufficiently long html comment carrying an embedded COMMENT PAYLOAD MARKER -->
</body></html>"""


async def test_detect_and_collect_finds_advanced_hiding_techniques():
    page = await _fetch_local(ADVANCED_HIDING_HTML)
    by_marker = {h["text"]: h["reason"] for h in page.hidden_spans}

    def reason_for(marker):
        return next((r for t, r in by_marker.items() if marker in t), None)

    assert reason_for("WHITE ON WHITE") == "color-contrast<1.15"
    assert reason_for("SR ONLY") == "sr-only-1px"
    assert reason_for("CLIP RECT") == "clip-zero-rect"
    assert reason_for("OFFSCREEN") == "off-screen"
    assert reason_for("COMMENT PAYLOAD") == "html-comment"

    # False-positive guards: none of these should ever be flagged as hidden,
    # and — unlike the payloads above — their text must survive stripping.
    assert reason_for("Visible paragraph") is None
    assert reason_for("MUTED TEXT") is None
    assert reason_for("BELOW THE FOLD") is None
    assert "Visible paragraph content here" in page.html
    assert "MUTED TEXT MARKER" in page.html
    assert "BELOW THE FOLD LEGIT CONTENT MARKER" in page.html
    assert "BELOW THE FOLD LEGIT CONTENT MARKER" in page.text

    assert "WHITE ON WHITE" not in page.html
    assert "SR ONLY PAYLOAD" not in page.html
    assert "CLIP RECT PAYLOAD" not in page.html
    assert "OFFSCREEN PAYLOAD" not in page.html
    assert "COMMENT PAYLOAD" not in page.html


SPA_HTML = """<html><body><div id="root"></div>
<script>
setTimeout(function () {
  document.getElementById("root").innerHTML =
    "<h1>Products</h1><p>LATE RENDERED PRODUCT GRID MARKER</p>";
}, 800);
</script>
</body></html>"""


async def test_fetch_waits_for_content_rendered_after_domcontentloaded():
    page = await _fetch_local(SPA_HTML)
    assert "LATE RENDERED PRODUCT GRID MARKER" in page.text
    assert "LATE RENDERED PRODUCT GRID MARKER" in page.html


async def test_fetch_exposes_hidden_spans_and_meta():
    provider = engine.EngineProvider(load_config())
    await provider.start()
    try:
        page = await provider.fetch("https://example.com/")
        assert isinstance(page.hidden_spans, list)
        assert set(page.meta) == {"meta", "lang", "canonical"}
    finally:
        await provider.aclose()
