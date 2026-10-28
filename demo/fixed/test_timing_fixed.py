import pytest

from demo.app import timing


# FIXES: timing (demo/tests/test_timing.py::test_warm_cache_is_fast)
def test_warm_cache_is_fast(monkeypatch):
    """ROOT_CAUSE
    Cause: the test asserted a 50 ms wall-clock budget around work whose duration depends on
    machine load (simulated by a random 0-60 ms sleep).
    Symptom: failed about 1 run in 6, more often on busy CI runners; never reproducible locally
    on demand.
    Fix: measure the work, not the clock. The sleep is replaced by a recorder, so the test
    checks the requested delay deterministically. Real performance belongs in a benchmark,
    not a unit test.
    """
    delays = []
    monkeypatch.setattr(timing.time, "sleep", delays.append)
    monkeypatch.setattr(timing.random, "uniform", lambda lo, hi: 0.03)
    assert timing.warm_cache() == {"warm": True}
    assert delays == [pytest.approx(0.03)]
