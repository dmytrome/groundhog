import asyncio

from groundhog_mcp.ratelimit import RateLimiter


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
    await rl.acquire("example.com")  # first call: no wait
    await rl.acquire("example.com")  # second call: must wait 5s
    assert sleeps == [5.0]


async def test_different_keys_do_not_wait():
    clock = make_clock()
    sleeps = []

    async def fake_sleep(d):
        sleeps.append(d)
        clock["t"] += d

    rl = RateLimiter(5.0, clock=lambda: clock["t"], sleep=fake_sleep)
    await rl.acquire("a.com")
    await rl.acquire("b.com")
    assert sleeps == []


async def test_concurrent_same_key_serialized():
    clock = make_clock()
    sleeps = []

    async def fake_sleep(d):
        sleeps.append(d)
        clock["t"] += d

    rl = RateLimiter(5.0, clock=lambda: clock["t"], sleep=fake_sleep)
    await asyncio.gather(rl.acquire("x.com"), rl.acquire("x.com"))
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
    await rl.acquire("old.com")
    clock["t"] += 60.0
    await rl.acquire("other.com")  # any acquisition drives the sweep
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
    await rl.acquire("busy.com")
    clock["t"] += 1.0
    await rl.acquire("other.com")  # drives the sweep
    await rl.acquire("busy.com")
    assert sleeps == [4.0]


async def test_an_in_flight_key_is_not_evicted():
    # Its lock is held, so dropping the entry would forget a delay still in force.
    clock = make_clock()

    async def fake_sleep(d):
        clock["t"] += d

    rl = RateLimiter(5.0, clock=lambda: clock["t"], sleep=fake_sleep)
    await rl.acquire("busy.com")
    clock["t"] += 60.0
    async with rl._lock("busy.com"):
        rl._evict_expired(clock["t"])
        assert "busy.com" in rl._last
