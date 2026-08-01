import asyncio
import itertools
import json
from collections.abc import Callable
from typing import TypeVar

import websockets

_T = TypeVar("_T")
_MAX_FRAME_BYTES = 64 * 1024 * 1024  # generous for real pages, fatal for a memory bomb


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
        self._closed = False

    async def connect(self) -> None:
        # Bounded, not unlimited: a CDP response carries the page's own `outerHTML`,
        # so a hostile page choosing its size could otherwise stream this process out
        # of memory — the same reason `http.MAX_BYTES` exists for plain fetches.
        self._ws = await websockets.connect(self._ws_url, max_size=_MAX_FRAME_BYTES)
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
            # Clean close from the browser side — fail waiters, don't strand them.
            self._fail_all(CDPError("CDP connection closed"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # connection dropped — fail everything waiting
            self._fail_all(CDPError(f"CDP connection closed: {exc}"))

    @property
    def closed(self) -> bool:
        """Whether this client can no longer talk to the browser."""
        return self._ws is None or self._closed

    def _fail_all(self, exc: Exception) -> None:
        self._closed = True
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
        if self.closed:
            raise CDPError("CDP client is not connected")
        mid = next(self._ids)
        fut: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        msg: dict = {"id": mid, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        self._pending[mid] = fut
        try:
            # The send is inside the block too: a cancellation or a dropped socket
            # while suspended here would otherwise strand the entry for the life of
            # the connection, once per wedged page.
            await self._ws.send(json.dumps(msg))
            return await fut
        finally:
            self._pending.pop(mid, None)

    def expect_event(self, method: str, session_id: str | None = None) -> asyncio.Future[dict]:
        """Register a waiter BEFORE the command that triggers the event, then await it.

        The waiter removes itself once settled. An event that never arrives — a
        navigation that times out, or fails before the load fires — would otherwise
        retain its entry for the life of the connection, once per such fetch.
        """
        key = (session_id, method)
        fut: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._event_waiters.setdefault(key, []).append(fut)
        fut.add_done_callback(lambda settled: self._discard(self._event_waiters, key, settled))
        return fut

    @staticmethod
    def _discard(
        registry: dict[tuple[str | None, str], list[_T]],
        key: tuple[str | None, str],
        item: _T,
    ) -> None:
        """Remove one entry from a per-(session, method) registry, pruning the key."""
        entries = registry.get(key)
        if not entries:
            return  # already delivered: `_read_loop` pops the whole list
        if item in entries:
            entries.remove(item)
        if not entries:
            registry.pop(key, None)

    def on_event(
        self, method: str, session_id: str | None, callback: Callable[[dict], None]
    ) -> Callable[[], None]:
        """Persistent listener (expect_event is one-shot). Returns an unsubscribe."""
        key = (session_id, method)
        self._event_listeners.setdefault(key, []).append(callback)

        def unsubscribe() -> None:
            self._discard(self._event_listeners, key, callback)

        return unsubscribe

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
        if self._ws is not None:
            await self._ws.close()
        self._fail_all(CDPError("CDP client closed"))
