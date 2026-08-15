"""What a customer is charged, and what they get back.

`price_order` and `price_refund` are pure functions over `settings`, so these
tests need no database. Every expected number below is worked out by hand from
the defaults pinned in `conftest.MONEY_DEFAULTS` — $5.99 shipping, free over $49,
8% tax — rather than by re-running the implementation, which would only assert
that the code agrees with itself.
"""

import pytest

from app.core.pricing import price_order, price_refund, returned_share

# Exact to well under a cent. The implementation rounds every field to 2dp, so
# this tolerance only absorbs float representation, never a real discrepancy.
EXACT = {"abs": 1e-9}


# ---------------------------------------------------------------------- #
# price_order
# ---------------------------------------------------------------------- #


async def test_small_basket_pays_shipping_and_tax():
    totals = price_order(20.0)
    assert totals.subtotal == pytest.approx(20.0, **EXACT)
    assert totals.shipping_fee == pytest.approx(5.99, **EXACT)
    assert totals.tax == pytest.approx(1.60, **EXACT)  # 20.00 * 0.08
    assert totals.total == pytest.approx(27.59, **EXACT)


async def test_free_shipping_starts_exactly_at_the_threshold():
    """The boundary is inclusive — $49.00 ships free, a cent less does not."""
    at = price_order(49.0)
    assert at.shipping_fee == 0.0
    assert at.total == pytest.approx(52.92, **EXACT)  # 49 + 0 + 3.92

    below = price_order(48.99)
    assert below.shipping_fee == pytest.approx(5.99, **EXACT)
    assert below.total == pytest.approx(58.90, **EXACT)  # 48.99 + 5.99 + 3.92


async def test_coupon_is_assessed_before_shipping_and_tax():
    """A coupon reduces the price of the goods, so it can drop a basket back
    under the free-shipping threshold and it lowers the tax owed."""
    totals = price_order(55.0, discount=10.0)
    assert totals.discount == pytest.approx(10.0, **EXACT)
    # 45.00 discounted — below 49, so shipping comes back.
    assert totals.shipping_fee == pytest.approx(5.99, **EXACT)
    assert totals.tax == pytest.approx(3.60, **EXACT)  # 45.00 * 0.08, not 55.00
    assert totals.total == pytest.approx(54.59, **EXACT)


async def test_discount_can_never_exceed_the_basket():
    totals = price_order(20.0, discount=50.0)
    assert totals.discount == pytest.approx(20.0, **EXACT)
    assert totals.tax == 0.0
    # Still owes the delivery on a basket the coupon took to nothing.
    assert totals.total == pytest.approx(5.99, **EXACT)


async def test_negative_inputs_are_clamped_rather_than_trusted():
    totals = price_order(-5.0, discount=-10.0)
    assert totals.subtotal == 0.0
    assert totals.discount == 0.0
    assert totals.total == pytest.approx(5.99, **EXACT)


async def test_points_are_tender_not_a_price_cut():
    """The invariant the whole rewards design rests on.

    Redeeming points comes off the bill *after* shipping and tax, so it can
    neither push a basket back below the free-shipping threshold nor quietly
    reduce the tax the store owes on the sale.
    """
    without = price_order(60.0)
    with_points = price_order(60.0, rewards_discount=20.0)

    # $60 of goods, less $20 of points, is $40 — which would attract shipping if
    # points were a discount. They are not.
    assert with_points.shipping_fee == without.shipping_fee == 0.0
    assert with_points.tax == without.tax == pytest.approx(4.80, **EXACT)
    assert with_points.rewards_discount == pytest.approx(20.0, **EXACT)
    assert with_points.total == pytest.approx(44.80, **EXACT)  # 64.80 − 20.00


async def test_points_can_zero_a_bill_but_never_invert_it():
    totals = price_order(20.0, rewards_discount=1000.0)
    # Clamped to the full payable amount: 20.00 + 5.99 + 1.60.
    assert totals.rewards_discount == pytest.approx(27.59, **EXACT)
    assert totals.total == 0.0


async def test_a_coupon_and_points_stack_in_that_order():
    totals = price_order(80.0, discount=20.0, rewards_discount=15.0)
    assert totals.shipping_fee == 0.0  # 60.00 discounted, still over the threshold
    assert totals.tax == pytest.approx(4.80, **EXACT)  # on 60.00, not 45.00
    assert totals.total == pytest.approx(49.80, **EXACT)  # 64.80 − 15.00


# ---------------------------------------------------------------------- #
# price_refund
#
# The order under test: two lines worth $100 together, a $10 coupon, free
# shipping, 8% tax on the discounted $90 — exactly what price_order(100, 10)
# produces, so the refund is being measured against a real charge.
# ---------------------------------------------------------------------- #

ORDER = {
    "items": [
        {"product_id": "a", "name": "Bed", "price": 50.0, "quantity": 1},
        {"product_id": "b", "name": "Bowl", "price": 25.0, "quantity": 2},
    ],
    "subtotal": 100.0,
    "discount": 10.0,
    "shipping_fee": 0.0,
    "tax": 7.20,
    "total": 97.20,
    "refunded_amount": 0.0,
}


async def test_returning_everything_gives_back_everything():
    refund = price_refund(ORDER, {"a": 1, "b": 2}, is_full_return=True)
    assert refund.goods == pytest.approx(100.0, **EXACT)
    assert refund.discount_share == pytest.approx(10.0, **EXACT)
    assert refund.tax_share == pytest.approx(7.20, **EXACT)
    # Down to the cent, the customer is made whole.
    assert refund.total == pytest.approx(ORDER["total"], **EXACT)


async def test_partial_return_apportions_the_coupon_and_the_tax():
    """Half the value goes back, so half the coupon is clawed back and half the
    tax is returned — not the whole of either."""
    refund = price_refund(ORDER, {"a": 1}, is_full_return=False)
    assert refund.goods == pytest.approx(50.0, **EXACT)
    assert refund.discount_share == pytest.approx(5.0, **EXACT)  # half of $10
    assert refund.tax_share == pytest.approx(3.60, **EXACT)  # half of $7.20
    assert refund.total == pytest.approx(48.60, **EXACT)  # 45.00 + 3.60


async def test_shipping_comes_back_only_on_a_full_return():
    """A partial return still cost the store the same delivery."""
    order = {
        "items": [
            {"product_id": "a", "name": "Bed", "price": 20.0, "quantity": 1},
            {"product_id": "b", "name": "Bowl", "price": 20.0, "quantity": 1},
        ],
        "subtotal": 40.0,
        "discount": 0.0,
        "shipping_fee": 5.99,
        "tax": 3.20,
        "total": 49.19,
        "refunded_amount": 0.0,
    }

    partial = price_refund(order, {"a": 1}, is_full_return=False)
    assert partial.shipping_refund == 0.0
    assert partial.total == pytest.approx(21.60, **EXACT)  # 20.00 + 1.60

    full = price_refund(order, {"a": 1, "b": 1}, is_full_return=True)
    assert full.shipping_refund == pytest.approx(5.99, **EXACT)
    assert full.total == pytest.approx(order["total"], **EXACT)


async def test_refund_is_capped_by_what_the_order_has_left_to_give():
    """Another return on the same order was already paid out, so this one is
    bounded by the remainder however the shares round."""
    order = {**ORDER, "refunded_amount": 90.0}
    refund = price_refund(order, {"a": 1, "b": 2}, is_full_return=True)
    assert refund.total == pytest.approx(7.20, **EXACT)  # 97.20 − 90.00


async def test_a_fully_discounted_order_refunds_no_cash():
    """A 100%-off coupon means nothing was charged, so nothing comes back —
    and the zero discounted subtotal must not divide by zero on the way there."""
    order = {
        "items": [{"product_id": "a", "name": "Bed", "price": 100.0, "quantity": 1}],
        "subtotal": 100.0,
        "discount": 100.0,
        "shipping_fee": 0.0,
        "tax": 0.0,
        "total": 0.0,
        "refunded_amount": 0.0,
    }
    refund = price_refund(order, {"a": 1}, is_full_return=True)
    assert refund.tax_share == 0.0
    assert refund.total == 0.0


async def test_two_partial_refunds_never_exceed_the_order():
    """The property that matters more than either individual number: settling
    every line separately pays out exactly the total, never a cent more."""
    first = price_refund(ORDER, {"a": 1}, is_full_return=False)
    after_first = {**ORDER, "refunded_amount": first.total}
    second = price_refund(after_first, {"b": 2}, is_full_return=True)

    assert first.total + second.total == pytest.approx(ORDER["total"], **EXACT)


# ---------------------------------------------------------------------- #
# returned_share — the single number the money and the points both split on
# ---------------------------------------------------------------------- #


async def test_returned_share_measures_goods_value_not_line_count():
    # One of three units, but half the money.
    assert returned_share(ORDER, {"a": 1}) == pytest.approx(0.5, **EXACT)
    assert returned_share(ORDER, {"b": 2}) == pytest.approx(0.5, **EXACT)
    assert returned_share(ORDER, {"a": 1, "b": 2}) == pytest.approx(1.0, **EXACT)


async def test_returned_share_ignores_unknown_products_and_empty_orders():
    assert returned_share(ORDER, {"not-on-this-order": 5}) == 0.0
    assert returned_share({"subtotal": 0.0, "items": []}, {"a": 1}) == 0.0


async def test_returned_share_cannot_exceed_the_whole_order():
    """Guards the points reversal: a share above 1.0 would refund more points
    than the order ever spent."""
    assert returned_share(ORDER, {"a": 10, "b": 10}) == 1.0
