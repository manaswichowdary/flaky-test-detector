import pytest

from demo.app.cart import Cart


def test_empty_cart_total_is_zero():
    assert Cart().total() == 0


def test_cart_sums_quantities():
    cart = Cart(discounts={})
    cart.add("pen", 1.5, 4)
    assert cart.total() == 6.0


def test_cart_rejects_zero_qty():
    with pytest.raises(ValueError):
        Cart().add("pen", 1.0, 0)


def test_cart_explicit_discounts_are_isolated():
    a, b = Cart(discounts={}), Cart(discounts={})
    a.add_discount("pen", 0.5)
    assert b.discounts == {}


def test_cart_discount_applies():
    cart = Cart()
    cart.add("book", 20.0)
    cart.add_discount("book", 0.25)
    assert cart.total() == 15.0


# PLANTED-FLAKY: shared-state
def test_book_full_price():
    cart = Cart()
    cart.add("book", 20.0)
    assert cart.total() == 20.0
