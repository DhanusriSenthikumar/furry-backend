"""The points ledger, exercised through the real repository.

These run against `mongomock_motor` rather than a fake repository on purpose.
What is being tested *is* Mongo behaviour — a unique index that rejects a second
credit, an `$inc` guarded by `$gte` that refuses to overdraw, an aggregation that
rebuilds a balance from history. A hand-written stand-in would re-implement those
guarantees and then assert its own re-implementation, which proves nothing.
"""

import pytest

from app.core.exceptions import ValidationError
from app.modules.loyalty.repository import LoyaltyRepository
from app.modules.loyalty.service import LoyaltyService


@pytest.fixture
def ledger(db):
    return LoyaltyRepository(db)


@pytest.fixture
def loyalty(ledger):
    # No notification service: the feed is a side effect, and nothing here
    # depends on it. Its absence is a supported configuration.
    return LoyaltyService(ledger, notifications=None)


async def balance_of(db, user):
    doc = await db.users.find_one({"_id": user["_id"]})
    return doc["loyalty_points"], doc["loyalty_lifetime_points"]


# ---------------------------------------------------------------------- #
# Idempotence — the property that makes the balance trustworthy
# ---------------------------------------------------------------------- #


async def test_a_keyed_credit_lands_exactly_once(db, ledger, make_user):
    user = await make_user(points=0)
    user_id = str(user["_id"])

    first = await ledger.credit(user_id, 500, "earned", "Order delivered", _now(), dedupe_key="earn:o1")
    second = await ledger.credit(user_id, 500, "earned", "Order delivered", _now(), dedupe_key="earn:o1")

    assert first is not None
    assert second is None, "the second credit under the same key must be rejected"
    assert await balance_of(db, user) == (500, 500)
    assert await ledger.count_for_user(user_id) == 1


async def test_unkeyed_credits_do_not_collide_with_each_other(db, ledger, make_user):
    """The partial-index subtlety called out in app/main.py.

    Most notifications and ledger rows carry no dedupe key. Under a *sparse*
    compound index they would all key as null and collide, so a customer would
    receive exactly one un-keyed credit ever and the rest would vanish.
    """
    user = await make_user(points=0)
    user_id = str(user["_id"])

    for _ in range(3):
        assert await ledger.credit(user_id, 100, "adjustment", "Goodwill", _now()) is not None

    assert await balance_of(db, user) == (300, 300)
    assert await ledger.count_for_user(user_id) == 3


async def test_delivering_an_order_twice_earns_points_once(db, loyalty, make_user, make_order):
    """An admin nudging a status back and forth, or a webhook redelivered by a
    payment provider, must not mint new balance each time."""
    user = await make_user(points=0)
    order = await make_order(user["_id"], subtotal=100.0, discount=10.0)

    first = await loyalty.award_for_order(order)
    second = await loyalty.award_for_order(order)

    assert first == 900  # $90 of goods × 10 points, at bronze ×1.0
    assert second == 0
    assert await balance_of(db, user) == (900, 900)


async def test_earning_uses_the_tier_the_customer_already_held(db, loyalty, make_user, make_order):
    """Reaching gold makes the *next* orders worth more; it does not repay this
    one at a rate the customer had not yet earned."""
    user = await make_user(points=0, lifetime=10_000)  # gold, ×1.5
    order = await make_order(user["_id"], subtotal=100.0, discount=10.0)

    assert await loyalty.award_for_order(order) == 1_350  # 900 × 1.5


# ---------------------------------------------------------------------- #
# Spending
# ---------------------------------------------------------------------- #


async def test_points_cannot_be_overdrawn(db, ledger, make_user):
    """The `$gte` guard is what makes two checkouts racing for the same points
    safe — exactly one of them can match."""
    user = await make_user(points=100)
    user_id = str(user["_id"])

    assert await ledger.debit(user_id, 500, "redeemed", "Too many", _now()) is False
    assert await balance_of(db, user) == (100, 100)
    # Nothing was written, so the history does not record a spend that never happened.
    assert await ledger.count_for_user(user_id) == 0


async def test_spending_writes_a_negative_row_and_leaves_lifetime_alone(db, ledger, make_user):
    """Lifetime drives the tier, so spending points must never cost status."""
    user = await make_user(points=1_000)
    user_id = str(user["_id"])

    assert await ledger.debit(user_id, 400, "redeemed", "Order #1", _now(), order_id="o1") is True
    assert await balance_of(db, user) == (600, 1_000)

    row = await ledger.find_one({"order_id": "o1", "kind": "redeemed"})
    assert row["points"] == -400


async def test_spent_points_are_netted_against_what_was_already_given_back(ledger, make_user):
    """So a second partial return cannot re-refund the first one's points."""
    user = await make_user(points=1_000)
    user_id = str(user["_id"])

    await ledger.debit(user_id, 400, "redeemed", "Order #1", _now(), order_id="o1")
    assert await ledger.points_spent_on_order("o1") == 400

    await ledger.credit(user_id, 150, "refunded", "Partial return", _now(), order_id="o1")
    assert await ledger.points_spent_on_order("o1") == 250


# ---------------------------------------------------------------------- #
# Unwinding an order
# ---------------------------------------------------------------------- #


async def test_a_reversed_order_returns_what_it_spent_and_reclaims_what_it_earned(
    db, loyalty, ledger, make_user, make_order
):
    user = await make_user(points=1_000)
    order = await make_order(user["_id"], subtotal=100.0, discount=10.0)
    order_id = str(order["_id"])

    await loyalty.spend(str(user["_id"]), 400, order_id)
    await loyalty.award_for_order(order)
    assert await balance_of(db, user) == (1_500, 1_900)  # 1000 − 400 + 900

    await loyalty.reverse_for_order(order, portion=1.0, reason="Order refunded")

    # The 400 spent comes back and the 900 earned goes out, landing exactly
    # where the customer started.
    balance, lifetime = await balance_of(db, user)
    assert balance == 1_000
    # Lifetime only ever climbs — a reversal costs balance, never tier standing.
    assert lifetime == 2_300


async def test_a_partial_return_unwinds_a_proportional_slice(db, loyalty, make_user, make_order):
    user = await make_user(points=1_000)
    order = await make_order(user["_id"], subtotal=100.0, discount=10.0)
    order_id = str(order["_id"])

    await loyalty.spend(str(user["_id"]), 400, order_id)
    await loyalty.award_for_order(order)

    await loyalty.reverse_for_order(order, portion=0.5, reason="Half returned")

    # +200 of the spend returned, −450 of the earnings clawed back.
    balance, _lifetime = await balance_of(db, user)
    assert balance == 1_500 + 200 - 450


async def test_clawing_back_never_pushes_a_balance_negative(db, loyalty, ledger, make_user, make_order):
    """A customer who has already spent their rewards elsewhere is not driven
    into debt when the order that earned them comes undone."""
    user = await make_user(points=0)
    order = await make_order(user["_id"], subtotal=100.0, discount=10.0)

    await loyalty.award_for_order(order)  # +900
    await ledger.debit(str(user["_id"]), 900, "redeemed", "Spent on another order", _now(), order_id="other")
    assert await balance_of(db, user) == (0, 900)

    await loyalty.reverse_for_order(order, portion=1.0, reason="Order refunded")

    balance, _lifetime = await balance_of(db, user)
    assert balance == 0


async def test_reversing_nothing_is_a_no_op(db, loyalty, make_user, make_order):
    user = await make_user(points=1_000)
    order = await make_order(user["_id"], subtotal=100.0, discount=10.0)

    await loyalty.reverse_for_order(order, portion=0.0)

    assert await balance_of(db, user) == (1_000, 1_000)


# ---------------------------------------------------------------------- #
# The cached balance vs the ledger
# ---------------------------------------------------------------------- #


async def test_the_cached_balance_agrees_with_the_history(db, loyalty, ledger, make_user, make_order):
    user = await make_user(points=0)
    user_id = str(user["_id"])
    order = await make_order(user["_id"], subtotal=100.0, discount=10.0)

    await loyalty.award_for_order(order)
    await loyalty.spend(user_id, 300, str(order["_id"]))
    await ledger.credit(user_id, 50, "adjustment", "Goodwill", _now())

    assert await ledger.balance_from_ledger(user_id) == await ledger.balances(user_id)


async def test_reconcile_repairs_a_drifted_balance(db, loyalty, ledger, make_user):
    user = await make_user(points=0)
    user_id = str(user["_id"])
    await ledger.credit(user_id, 500, "earned", "Order delivered", _now())

    # Simulate the drift the admin button exists to repair.
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"loyalty_points": 99}})

    result = await loyalty.reconcile(user_id)

    assert result["changed"] is True
    assert result["was"]["balance"] == 99
    assert result["now"]["balance"] == 500
    assert await balance_of(db, user) == (500, 500)


async def test_reconcile_is_quiet_when_nothing_has_drifted(loyalty, ledger, make_user):
    user = await make_user(points=0)
    user_id = str(user["_id"])
    await ledger.credit(user_id, 500, "earned", "Order delivered", _now())

    result = await loyalty.reconcile(user_id)

    assert result["changed"] is False


# ---------------------------------------------------------------------- #
# Staff adjustments
# ---------------------------------------------------------------------- #


async def test_staff_cannot_deduct_more_than_a_customer_holds(loyalty, make_user):
    user = await make_user(points=100)

    with pytest.raises(ValidationError, match="only has"):
        await loyalty.adjust(str(user["_id"]), -500, "Clawback", "Admin")


async def test_a_zero_adjustment_is_refused(loyalty, make_user):
    user = await make_user(points=100)

    with pytest.raises(ValidationError):
        await loyalty.adjust(str(user["_id"]), 0, "Nothing", "Admin")


async def test_an_unknown_user_has_no_balance(ledger):
    assert await ledger.balances("not-an-object-id") == (0, 0)


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
