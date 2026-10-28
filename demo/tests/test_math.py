import pytest

from demo.app.mathx import clamp, mean


def test_clamp():
    assert clamp(5, 0, 10) == 5
    assert clamp(-1, 0, 10) == 0 and clamp(11, 0, 10) == 10


def test_mean():
    assert mean([1, 2, 3]) == 2


def test_mean_empty_raises():
    with pytest.raises(ValueError):
        mean([])
