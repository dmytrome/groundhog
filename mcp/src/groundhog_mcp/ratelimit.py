import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class RateLimiter:
    def __init__(self, min_delay: float, *, clock=time.monotonic, sleep=asyncio.sleep):
        self._min_delay = min_delay
        self._clock = clock
        self._sleep = sleep
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._users: dict[str, int] = {}

    def _lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    def _evict_expired(self, now: float) -> None:
        stale = [
            key
            for key, seen in self._last.items()
            if now - seen > self._min_delay and key not in self._users
        ]
        for key in stale:
            self._last.pop(key, None)
            self._locks.pop(key, None)

    @asynccontextmanager
    async def turn(self, key: str) -> AsyncIterator[None]:
        self._users[key] = self._users.get(key, 0) + 1
        try:
            async with self._lock(key):
                self._evict_expired(self._clock())
                last = self._last.get(key)
                if last is not None:
                    wait = self._min_delay - (self._clock() - last)
                    if wait > 0:
                        await self._sleep(wait)
                yield
                self._last[key] = self._clock()
        finally:
            self._users[key] -= 1
            if not self._users[key]:
                del self._users[key]
                if key not in self._last:
                    self._locks.pop(key, None)
