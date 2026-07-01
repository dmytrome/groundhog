"""Raw DevTools Protocol over a WebSocket — the lowest-level way to drive the
container, with no browser-automation framework.

Shows exactly which CDP commands set the User-Agent and capture a screenshot.

    pip install -r requirements.txt
    python main.py

CDP_URL defaults to http://127.0.0.1:9222.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import urllib.request

import websockets

CDP_URL = os.environ.get("CDP_URL", "http://127.0.0.1:9222")
REAL_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36"
)


def open_target(target_url: str) -> str:
    """Open a new tab and return its WebSocket debugger URL."""
    req = urllib.request.Request(f"{CDP_URL}/json/new?{target_url}", method="PUT")
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)["webSocketDebuggerUrl"]


async def main() -> None:
    ws_url = open_target("https://bot.sannysoft.com/")
    async with websockets.connect(ws_url, max_size=None) as ws:
        msg_id = 0

        async def send(method: str, params: dict | None = None) -> dict:
            nonlocal msg_id
            msg_id += 1
            await ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
            while True:
                reply = json.loads(await ws.recv())
                if reply.get("id") == msg_id:
                    return reply

        await send("Page.enable")
        await send("Network.enable")
        await send("Network.setUserAgentOverride", {"userAgent": REAL_UA})
        await send("Page.navigate", {"url": "https://bot.sannysoft.com/"})
        await asyncio.sleep(5)  # let the page and its async checks render

        shot = await send("Page.captureScreenshot", {"format": "png"})
        with open("sannysoft.png", "wb") as fh:
            fh.write(base64.b64decode(shot["result"]["data"]))
        print("saved sannysoft.png")


asyncio.run(main())
