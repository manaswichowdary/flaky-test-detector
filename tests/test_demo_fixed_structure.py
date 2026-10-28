"""Every planted flaky test has a fixed version with a root-cause note (source only)."""

import pytest

from demo.planted import fixes, planted

FIXES = fixes()


def test_one_fix_per_planted_kind():
    assert set(FIXES) == set(planted().values())


@pytest.mark.parametrize("kind", sorted(FIXES))
def test_fix_points_at_the_planted_test(kind):
    original = FIXES[kind]["original"]  # e.g. demo/tests/test_cart.py::test_book_full_price
    path, name = original.split("::")
    key = f"{path.rsplit('/', 1)[-1].removesuffix('.py')}::{name}"
    assert planted()[key] == kind
    assert FIXES[kind]["fixed"].endswith(f"::{name}")  # same test name, fixed module


@pytest.mark.parametrize("kind", sorted(FIXES))
def test_root_cause_note_has_cause_symptom_fix(kind):
    note = FIXES[kind]["root_cause"]
    assert note.startswith("ROOT_CAUSE")
    for section in ("Cause:", "Symptom:", "Fix:"):
        assert section in note, f"{kind} note is missing {section}"
