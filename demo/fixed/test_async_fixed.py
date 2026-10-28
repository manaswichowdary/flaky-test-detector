import asyncio

from demo.app.counter import AsyncCounter, random_delay


# FIXES: async-race (demo/tests/test_async.py::test_concurrent_increments)
def test_concurrent_increments():
    """ROOT_CAUSE
    Cause: increment_unsafe reads the value, awaits, then writes. Two concurrent increments
    that interleave across the await both read the same value, and one update is lost.
    Symptom: "assert 1 == 2" whenever the random delay outlasted the second task's start.
    Fix: guard the read-modify-write with an asyncio.Lock (increment_locked), so the
    interleaving can no longer lose updates. The random delays stay in the test, to prove
    timing no longer matters.
    """
    async def run():
        counter, lock = AsyncCounter(), asyncio.Lock()

        async def slow_then_increment():
            await asyncio.sleep(random_delay())
            await counter.increment_locked(lock)

        await asyncio.gather(*(slow_then_increment() for _ in range(10)))
        return counter.value

    assert asyncio.run(run()) == 10
