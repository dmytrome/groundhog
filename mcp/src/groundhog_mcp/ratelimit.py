import asyncio
import time


class RateLimiter:
    """Enforces a minimum delay between acquisitions sharing the same key."""

    def __init__(self, min_delay: float, *, clock=time.monotonic, sleep=asyncio.sleep):
        self._min_delay = min_delay
        self._clock = clock
        self._sleep = sleep
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    async def acquire(self, key: str) -> None:
        async with self._lock(key):
            last = self._last.get(key)
            if last is not None:
                wait = self._min_delay - (self._clock() - last)
                if wait > 0:
                    await self._sleep(wait)
            self._last[key] = self._clock()
