import time

import pytest

from demo.app.timing import parse_duration, warm_cache


def test_parse_hours_minutes():
    assert parse_duration("1h30m") == 5400


def test_parse_seconds():
    assert parse_duration("45s") == 45


def test_parse_rejects_trailing_number():
    with pytest.raises(ValueError):
        parse_duration("10m5")


# PLANTED-FLAKY: timing
def test_warm_cache_is_fast():
    start = time.perf_counter()
    warm_cache()
    assert time.perf_counter() - start < 0.05
