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

    def _evict_expired(self, now: float, keep: str | None = None) -> None:
        """Drop keys whose delay has already elapsed.

        A long-lived server driven by search results sees unboundedly many domains,
        and an entry older than the delay can no longer postpone anything.
        """
        stale = [k for k, seen in self._last.items() if k != keep and now - seen > self._min_delay]
        for key in stale:
            lock = self._locks.get(key)
            if lock is None or not lock.locked():
                self._last.pop(key, None)
                self._locks.pop(key, None)

    async def acquire(self, key: str) -> None:
        async with self._lock(key):
            # Swept while holding this key's lock, and never for this key: the
            # `locked()` test for other keys then cannot straddle the gap between
            # another caller fetching its Lock and entering it.
            self._evict_expired(self._clock(), keep=key)
            last = self._last.get(key)
            if last is not None:
                wait = self._min_delay - (self._clock() - last)
                if wait > 0:
                    await self._sleep(wait)
            self._last[key] = self._clock()
