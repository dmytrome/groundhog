import asyncio
import contextlib
import itertools
import json
import secrets
import shutil
from pathlib import Path

import websockets
from websockets.asyncio.server import Server, ServerConnection, serve

from . import sanitize

_BRIDGE = Path(__file__).with_name("ego_bridge.mjs")
STUB = Path(__file__).with_name("ego_stub.js").read_text()
TAKEOVER = "Control of this task space is with the user"
_TAKEOVER_CLOSE = 4000
_NAME_IN_USE = "EGO_TASK_SPACE_NAME_IN_USE"
_CONNECT_TIMEOUT_S = 30.0
_PROBE_TIMEOUT_S = 5.0
_EXIT_TIMEOUT_S = 10.0
_KEPT_OUTPUT_BYTES = 4096


def installed() -> bool:
    return shutil.which("ego-browser") is not None


def remediation() -> str:
    if not installed():
        return (
            "GROUNDHOG_BROWSER=ego needs Ego Lite: install it so `ego-browser` is on PATH, "
            "and open it."
        )
    return "Could not reach Ego Lite through `ego-browser`: check that Ego Lite is open."


async def reachable() -> bool:
    binary = shutil.which("ego-browser")
    if binary is None:
        return False
    process = await asyncio.create_subprocess_exec(
        binary,
        "nodejs",
        "-e",
        "0",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        return await asyncio.wait_for(process.wait(), _PROBE_TIMEOUT_S) == 0
    except TimeoutError:
        process.kill()
        await process.wait()
        return False


class Bridge:
    def __init__(self) -> None:
        self._token = secrets.token_urlsafe(24)
        self._ids = itertools.count(1)
        self._clients: dict[int, ServerConnection] = {}
        self._ego: ServerConnection | None = None
        self._connected = asyncio.Event()
        self._server: Server | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._output = bytearray()
        self._reader: asyncio.Task[None] | None = None

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
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert self._process.stdin is not None and self._process.stdout is not None
        self._reader = asyncio.ensure_future(self._keep_output(self._process.stdout))
        self._process.stdin.write((prelude + _BRIDGE.read_text()).encode())
        with contextlib.suppress(ConnectionError):
            await self._process.stdin.drain()
        self._process.stdin.close()
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
            raise OSError(_startup_error(self._said()))
        return f"ws://127.0.0.1:{port}/cdp/{self._token}"

    async def _keep_output(self, stream: asyncio.StreamReader) -> None:
        while chunk := await stream.read(_KEPT_OUTPUT_BYTES):
            self._output += chunk
            del self._output[:-_KEPT_OUTPUT_BYTES]

    def _said(self) -> str:
        return self._output.decode(errors="replace").replace(self._token, "***")

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
            with contextlib.suppress(websockets.ConnectionClosed):
                async for raw in ws:
                    message = json.loads(raw)
                    client = self._clients.get(message.get("cid"))
                    if client is not None:
                        with contextlib.suppress(websockets.ConnectionClosed):
                            await client.send(json.dumps(message["msg"]))
        finally:
            self._ego = None
            taken_over = ws.close_code == _TAKEOVER_CLOSE
            for client in list(self._clients.values()):
                if taken_over:
                    await client.close(_TAKEOVER_CLOSE, TAKEOVER)
                else:
                    await client.close()

    async def _serve_client(self, ws: ServerConnection) -> None:
        cid = next(self._ids)
        self._clients[cid] = ws
        try:
            with contextlib.suppress(websockets.ConnectionClosed):
                async for raw in ws:
                    if self._ego is None:
                        break
                    with contextlib.suppress(websockets.ConnectionClosed):
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
        if self._reader is not None:
            await self._reader
            self._reader = None


def _startup_error(said: str) -> str:
    if _NAME_IN_USE in said:
        return (
            "Ego Lite's `groundhog` task space is with you, so Groundhog will not use it. "
            "Close it in Ego Lite to let Groundhog open a new one."
        )
    detail = sanitize.clean_field(said.strip()[-400:], sanitize.MAX_ERROR_CHARS)
    return remediation() + (f" ego-browser said: {detail}" if detail else "")
