def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def mean(values: list[float]) -> float:
    if not values:
        raise ValueError("empty")
    return sum(values) / len(values)
