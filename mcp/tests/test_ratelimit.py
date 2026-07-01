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
    await rl.acquire("example.com")   # first call: no wait
    await rl.acquire("example.com")   # second call: must wait 5s
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
