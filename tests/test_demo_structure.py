"""Fast structural check of the demo suite. Parses source only, never runs demo tests."""

from demo.planted import all_tests, planted

KINDS = {"timing", "shared-state", "test-order", "network", "random-data", "async-race"}


def test_demo_has_30_tests():
    assert len(all_tests()) == 30


def test_six_planted_flaky_tests_one_per_kind():
    kinds = planted()
    assert len(kinds) == 6
    assert set(kinds.values()) == KINDS
