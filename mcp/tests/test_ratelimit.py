import asyncio

import pytest

from groundhog_mcp.ratelimit import RateLimiter


async def _take(rl: RateLimiter, key: str) -> None:
    async with rl.turn(key):
        pass


def make_clock(start=100.0):
    state = {"t": start}
    return state


async def test_same_key_waits_once():
    clock = make_clock()
    sleeps = []

    async def fake_sleep(d):
        sleeps.append(d)
        clock["t"] += d

    rl = RateLimiter(5.0, clock=lambda: clock["t"], sleep=fake_sleep)
    await _take(rl, "example.com")  # first call: no wait
    await _take(rl, "example.com")  # second call: must wait 5s
    assert sleeps == [5.0]


async def test_different_keys_do_not_wait():
    clock = make_clock()
    sleeps = []

    async def fake_sleep(d):
        sleeps.append(d)
        clock["t"] += d

    rl = RateLimiter(5.0, clock=lambda: clock["t"], sleep=fake_sleep)
    await _take(rl, "a.com")
    await _take(rl, "b.com")
    assert sleeps == []


async def test_concurrent_same_key_serialized():
    clock = make_clock()
    sleeps = []

    async def fake_sleep(d):
        sleeps.append(d)
        clock["t"] += d

    rl = RateLimiter(5.0, clock=lambda: clock["t"], sleep=fake_sleep)
    await asyncio.gather(_take(rl, "x.com"), _take(rl, "x.com"))
    assert len(sleeps) == 1


async def test_a_key_idle_longer_than_the_delay_is_forgotten():
    # A long-lived server driven by search results sees unboundedly many domains.
    #
    # This asserts on internal state deliberately: eviction reclaims memory without
    # changing any behaviour — that is the point of it — so there is nothing
    # observable to assert instead. A version of this test written against the
    # public contract passed with eviction removed entirely.
    clock = make_clock()

    async def fake_sleep(d):
        clock["t"] += d

    rl = RateLimiter(5.0, clock=lambda: clock["t"], sleep=fake_sleep)
    await _take(rl, "old.com")
    clock["t"] += 60.0
    await _take(rl, "other.com")  # any acquisition drives the sweep
    assert "old.com" not in rl._last
    assert "other.com" in rl._last


async def test_a_recently_used_key_still_waits_after_a_sweep():
    # Eviction must not forget a delay that is still in force — this half *is*
    # observable, so it is asserted through the public contract.
    clock = make_clock()
    sleeps = []

    async def fake_sleep(d):
        sleeps.append(d)
        clock["t"] += d

    rl = RateLimiter(5.0, clock=lambda: clock["t"], sleep=fake_sleep)
    await _take(rl, "busy.com")
    clock["t"] += 1.0
    await _take(rl, "other.com")  # drives the sweep
    await _take(rl, "busy.com")
    assert sleeps == [4.0]


async def test_an_in_flight_key_is_not_evicted():
    # Its lock is held, so dropping the entry would forget a delay still in force.
    clock = make_clock()

    async def fake_sleep(d):
        clock["t"] += d

    rl = RateLimiter(5.0, clock=lambda: clock["t"], sleep=fake_sleep)
    await _take(rl, "busy.com")
    clock["t"] += 60.0
    async with rl.turn("busy.com"):
        rl._evict_expired(clock["t"])
        assert "busy.com" in rl._last


async def test_a_turn_abandoned_before_its_key_was_ever_used_is_forgotten() -> None:
    clock = make_clock()

    async def fake_sleep(d: float) -> None:
        clock["t"] += d

    rl = RateLimiter(5.0, clock=lambda: clock["t"], sleep=fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        async with rl.turn("gone.com"):
            raise asyncio.CancelledError
    clock["t"] += 60.0

    await _take(rl, "other.com")

    assert "gone.com" not in rl._locks


async def test_a_waiter_woken_by_an_abandoned_turn_still_excludes_later_callers() -> None:
    rl = RateLimiter(0.0)
    entered = asyncio.Event()
    active = 0
    peak = 0

    async def abandoned() -> None:
        async with rl.turn("x.com"):
            entered.set()
            await asyncio.sleep(10)

    async def holder() -> None:
        nonlocal active, peak
        async with rl.turn("x.com"):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

    first = asyncio.create_task(abandoned())
    await entered.wait()
    woken = asyncio.create_task(holder())
    await asyncio.sleep(0)
    first.cancel()
    later = [asyncio.create_task(_take(rl, "y.com")), asyncio.create_task(holder())]
    await asyncio.gather(first, woken, *later, return_exceptions=True)

    assert peak == 1
