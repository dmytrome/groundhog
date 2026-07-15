import asyncio
import itertools
import json
from collections.abc import Callable

import websockets


class CDPError(Exception):
    """A CDP command returned an error or the page raised during evaluate."""


class CDPClient:
    """Minimal multiplexed CDP client over a single browser WebSocket.

    Deliberately never enables the Runtime or Console domains: doing so makes the
    session observable to pages (the `isAutomatedWithCDP` fingerprint). `Runtime.evaluate`
    works against the default context without `Runtime.enable`, which is all a read-only
    fetch needs.
    """

    def __init__(self, ws_url: str):
        self._ws_url = ws_url
        self._ws: websockets.ClientConnection | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[dict]] = {}
        self._event_waiters: dict[tuple[str | None, str], list[asyncio.Future[dict]]] = {}
        self._event_listeners: dict[tuple[str | None, str], list[Callable[[dict], None]]] = {}
        self._reader: asyncio.Task | None = None

    async def connect(self) -> None:
        self._ws = await websockets.connect(self._ws_url, max_size=None)
        self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                mid = msg.get("id")
                if mid is not None and mid in self._pending:
                    fut = self._pending.pop(mid)
                    if not fut.done():
                        if "error" in msg:
                            fut.set_exception(CDPError(str(msg["error"])))
                        else:
                            fut.set_result(msg.get("result", {}))
                elif "method" in msg:
                    key = (msg.get("sessionId"), msg["method"])
                    for fut in self._event_waiters.pop(key, []):
                        if not fut.done():
                            fut.set_result(msg.get("params", {}))
                    for cb in list(self._event_listeners.get(key, [])):
                        cb(msg.get("params", {}))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # connection dropped — fail everything waiting
            self._fail_all(CDPError(f"CDP connection closed: {exc}"))

    def _fail_all(self, exc: Exception) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()
        for waiters in self._event_waiters.values():
            for fut in waiters:
                if not fut.done():
                    fut.set_exception(exc)
        self._event_waiters.clear()

    async def send(
        self, method: str, params: dict | None = None, session_id: str | None = None
    ) -> dict:
        if self._ws is None:
            raise CDPError("CDP client is not connected")
        mid = next(self._ids)
        fut: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        msg: dict = {"id": mid, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        await self._ws.send(json.dumps(msg))
        return await fut

    def expect_event(self, method: str, session_id: str | None = None) -> asyncio.Future[dict]:
        """Register a waiter BEFORE the command that triggers the event, then await it."""
        fut: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._event_waiters.setdefault((session_id, method), []).append(fut)
        return fut

    def on_event(
        self, method: str, session_id: str | None, callback: Callable[[dict], None]
    ) -> Callable[[], None]:
        """Persistent listener (expect_event is one-shot). Returns an unsubscribe."""
        key = (session_id, method)
        self._event_listeners.setdefault(key, []).append(callback)

        def unsubscribe() -> None:
            callbacks = self._event_listeners.get(key, [])
            if callback in callbacks:
                callbacks.remove(callback)
            if not callbacks:
                self._event_listeners.pop(key, None)

        return unsubscribe

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
        if self._ws is not None:
            await self._ws.close()
        self._fail_all(CDPError("CDP client closed"))
