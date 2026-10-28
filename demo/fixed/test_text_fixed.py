import random

import pytest

from demo.app.text import slugify


# FIXES: random-data (demo/tests/test_text.py::test_slugify_random_title_is_nonempty)
@pytest.mark.parametrize("title,slug", [
    ("abc", "abc"),
    ("a!é", "a"),
    ("é!?", ""),     # the edge case the random input occasionally hit
    ("   ", ""),
])
def test_slugify_random_title_is_nonempty(title, slug):
    """ROOT_CAUSE
    Cause: the test built its input from an unseeded random generator, and about 1 in 5
    inputs had no ASCII letters, so slugify correctly returned "" and the assertion failed.
    Symptom: "empty slug for 'é!?'" with a different title each failure; never reproducible.
    Fix: name the edge cases explicitly and assert the real behaviour (non-ASCII-only titles
    give an empty slug). Random inputs are kept only with a fixed seed, so a failure always
    reproduces.
    """
    assert slugify(title) == slug


def test_slugify_seeded_random_titles_are_url_safe():
    rng = random.Random(1234)
    for _ in range(200):
        title = "".join(rng.choice("abcé!? ") for _ in range(3))
        assert set(slugify(title)) <= set("abcdefghijklmnopqrstuvwxyz0123456789-")
