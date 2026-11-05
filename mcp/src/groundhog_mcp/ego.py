import asyncio
import contextlib
import itertools
import json
import secrets
import shutil
from pathlib import Path

import websockets
from websockets.asyncio.server import Server, ServerConnection, serve

_BRIDGE = Path(__file__).with_name("ego_bridge.mjs")
STUB = Path(__file__).with_name("ego_stub.js").read_text()
TAKEOVER = "Control of this task space is with the user"
_CONNECT_TIMEOUT_S = 30.0
_EXIT_TIMEOUT_S = 10.0


def installed() -> bool:
    return shutil.which("ego-browser") is not None


def remediation() -> str:
    if not installed():
        return (
            "GROUNDHOG_BROWSER=ego needs Ego Lite: install it so `ego-browser` is on PATH, "
            "and open it."
        )
    return "Could not reach Ego Lite through `ego-browser`: check that Ego Lite is open."


class Bridge:
    def __init__(self) -> None:
        self._token = secrets.token_urlsafe(24)
        self._ids = itertools.count(1)
        self._clients: dict[int, ServerConnection] = {}
        self._ego: ServerConnection | None = None
        self._connected = asyncio.Event()
        self._server: Server | None = None
        self._process: asyncio.subprocess.Process | None = None

    @property
    def alive(self) -> bool:
        return (
            self._ego is not None and self._process is not None and self._process.returncode is None
        )

    async def start(self) -> str:
        binary = shutil.which("ego-browser")
        if binary is None:
            raise OSError(remediation())
        self._server = await serve(self._handle, "127.0.0.1", 0, max_size=None)
        port = self._server.sockets[0].getsockname()[1]
        prelude = f"const GROUNDHOG = {json.dumps({'port': port, 'token': self._token})};\n"
        self._process = await asyncio.create_subprocess_exec(
            binary,
            "nodejs",
            "-e",
            prelude + _BRIDGE.read_text(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        exited = asyncio.ensure_future(self._process.wait())
        connected = asyncio.ensure_future(self._connected.wait())
        try:
            await asyncio.wait(
                {exited, connected}, timeout=_CONNECT_TIMEOUT_S, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            exited.cancel()
            connected.cancel()
        if not self._connected.is_set():
            await self.aclose()
            raise OSError(remediation())
        return f"ws://127.0.0.1:{port}/cdp/{self._token}"

    async def _handle(self, ws: ServerConnection) -> None:
        path = ws.request.path if ws.request is not None else ""
        if path == f"/ego/{self._token}" and self._ego is None:
            await self._serve_ego(ws)
        elif path == f"/cdp/{self._token}" and self._ego is not None:
            await self._serve_client(ws)
        else:
            await ws.close(1008)

    async def _serve_ego(self, ws: ServerConnection) -> None:
        self._ego = ws
        self._connected.set()
        try:
            async for raw in ws:
                message = json.loads(raw)
                client = self._clients.get(message.get("cid"))
                if client is not None:
                    with contextlib.suppress(websockets.ConnectionClosed):
                        await client.send(json.dumps(message["msg"]))
        finally:
            self._ego = None
            for client in list(self._clients.values()):
                await client.close()

    async def _serve_client(self, ws: ServerConnection) -> None:
        cid = next(self._ids)
        self._clients[cid] = ws
        try:
            async for raw in ws:
                if self._ego is None:
                    break
                await self._ego.send(json.dumps({"cid": cid, "msg": json.loads(raw)}))
        finally:
            self._clients.pop(cid, None)
            if self._ego is not None:
                with contextlib.suppress(websockets.ConnectionClosed):
                    await self._ego.send(json.dumps({"cid": cid, "gone": True}))

    async def aclose(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        process, self._process = self._process, None
        if process is not None and process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), _EXIT_TIMEOUT_S)
            except TimeoutError:
                process.terminate()
                await process.wait()
