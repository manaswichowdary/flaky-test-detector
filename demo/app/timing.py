import random
import time


def parse_duration(text: str) -> int:
    """'1h30m' -> 5400 seconds."""
    units = {"h": 3600, "m": 60, "s": 1}
    total, num = 0, ""
    for ch in text:
        if ch.isdigit():
            num += ch
        elif ch in units and num:
            total += int(num) * units[ch]
            num = ""
        else:
            raise ValueError(f"bad duration: {text!r}")
    if num:
        raise ValueError(f"bad duration: {text!r}")
    return total


def warm_cache() -> dict:
    """Simulates work whose duration depends on machine load."""
    time.sleep(random.uniform(0.0, 0.06))
    return {"warm": True}
