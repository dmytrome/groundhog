import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import pytest

from groundhog_mcp import safety
from groundhog_mcp.config import load_config
from groundhog_mcp.engine import EngineProvider
from groundhog_mcp.safety import BlockedURLError

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE") != "1" or load_config().browser != "ego",
    reason="requires Ego Lite; set RUN_LIVE=1 and GROUNDHOG_BROWSER=ego",
)

_PROBE = """
<p>probe page</p>
<script>
const u = 'http://127.0.0.1:{port}';
fetch(u + '/fetch').catch(() => 0);
const x = new XMLHttpRequest(); x.open('GET', u + '/xhr'); x.send();
new Image().src = u + '/img';
navigator.sendBeacon(u + '/beacon', 'x');
const l = document.createElement('link'); l.rel = 'prefetch'; l.href = u + '/prefetch';
document.head.append(l);
const f = document.createElement('iframe'); f.src = u + '/frame'; document.body.append(f);
let ws = 'open';
try {{ new WebSocket('ws://127.0.0.1:{port}/ws'); }} catch (e) {{ ws = 'blocked'; }}
document.body.append(' websocket ' + ws);
const w = "try {{ new WebSocket('ws://127.0.0.1:{port}/worker') }} catch (e) {{}}";
new Worker(URL.createObjectURL(new Blob([w], {{ type: 'text/javascript' }})));
</script>
"""


def _counting_listener() -> tuple[socket.socket, list[bytes]]:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    hits: list[bytes] = []

    def accept() -> None:
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(1)
                try:
                    hits.append(conn.recv(200))
                except OSError:
                    hits.append(b"")

    threading.Thread(target=accept, daemon=True).start()
    return server, hits


def _serve(html: str) -> ThreadingHTTPServer:
    payload = html.encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


async def test_a_page_in_ego_opens_no_connection_to_a_local_port(monkeypatch):
    server, hits = _counting_listener()
    site = _serve(_PROBE.format(port=server.getsockname()[1]))
    page_port = site.server_address[1]

    async def only_the_page_is_public(url, cfg):
        if urlparse(url).port != page_port:
            raise BlockedURLError(f"blocked address: {url}")

    monkeypatch.setattr(safety, "check_url", only_the_page_is_public)
    provider = EngineProvider(load_config())
    try:
        page = await provider.fetch(f"http://127.0.0.1:{page_port}/")
    finally:
        await provider.aclose()
        server.close()
        site.shutdown()

    assert hits == []
    assert "probe page" in page.text
    assert "websocket blocked" in page.text
