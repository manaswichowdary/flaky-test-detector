import asyncio

from demo.app.counter import AsyncCounter, random_delay


def test_single_increment():
    counter = AsyncCounter()
    asyncio.run(counter.increment_unsafe(0))
    assert counter.value == 1


def test_locked_increments():
    async def run():
        counter, lock = AsyncCounter(), asyncio.Lock()
        await asyncio.gather(*(counter.increment_locked(lock) for _ in range(50)))
        return counter.value

    assert asyncio.run(run()) == 50


def test_sequential_unsafe_increments():
    async def run():
        counter = AsyncCounter()
        for _ in range(3):
            await counter.increment_unsafe(0)
        return counter.value

    assert asyncio.run(run()) == 3


# PLANTED-FLAKY: async-race
def test_concurrent_increments():
    async def run():
        counter = AsyncCounter()

        async def late():
            await asyncio.sleep(0.008)
            await counter.increment_unsafe(0)

        await asyncio.gather(counter.increment_unsafe(random_delay()), late())
        return counter.value

    assert asyncio.run(run()) == 2
