import random

from demo.app.text import initials, slugify


def test_slugify_basic():
    assert slugify("Hello World") == "hello-world"


def test_slugify_collapses_punctuation():
    assert slugify("a -- b!!c") == "a-b-c"


def test_slugify_trims_dashes():
    assert slugify("  --hi--  ") == "hi"


def test_initials():
    assert initials("Ada Lovelace") == "AL"


# PLANTED-FLAKY: random-data
def test_slugify_random_title_is_nonempty():
    title = "".join(random.choice("abcé!? ") for _ in range(3))
    assert slugify(title), f"empty slug for {title!r}"
