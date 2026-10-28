import asyncio
import random


class AsyncCounter:
    def __init__(self) -> None:
        self.value = 0

    async def increment_unsafe(self, delay: float) -> None:
        current = self.value
        await asyncio.sleep(delay)  # yields between read and write
        self.value = current + 1

    async def increment_locked(self, lock: asyncio.Lock) -> None:
        async with lock:
            self.value += 1


def random_delay() -> float:
    return random.uniform(0.0, 0.01)
