import pytest

from demo.app import cart
from demo.app.cart import Cart


@pytest.fixture(autouse=True)
def _restore_default_discounts():
    saved = dict(cart.DEFAULT_DISCOUNTS)
    yield
    cart.DEFAULT_DISCOUNTS.clear()
    cart.DEFAULT_DISCOUNTS.update(saved)


# FIXES: shared-state (demo/tests/test_cart.py::test_book_full_price)
def test_book_full_price():
    """ROOT_CAUSE
    Cause: Cart() used a mutable default argument, so every Cart shared one module-level
    discounts dict. test_cart_discount_applies added a 25% book discount to it, and the
    discount leaked into this test.
    Symptom: "assert 15.0 == 20.0", only when the discount test happened to run first.
    Fix: give each cart its own state (Cart(discounts={})); the fixture also restores the
    shared default so no test can leak into another. In application code the real fix is
    `discounts=None` with `self.discounts = {} if discounts is None else discounts`.
    """
    shopping = Cart(discounts={})
    shopping.add("book", 20.0)
    assert shopping.total() == 20.0
