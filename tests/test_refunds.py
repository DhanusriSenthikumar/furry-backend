"""Returns end to end: what is still returnable, and what settling one pays out.

The unit-level apportionment is covered in `test_pricing`. What these add is the
part that only shows up once several returns share an order — the claim on units,
the re-pricing at settlement, and the guarantee that the parts never add up to
more than the whole.

Refunds settle as "manual" throughout: no payment record is wired up, which is
the same fallback a Cash-on-Delivery order takes in production.
"""

import pytest

from app.core.exceptions import ValidationError
from app.modules.loyalty.repository import LoyaltyRepository
from app.modules.loyalty.service import LoyaltyService
from app.modules.orders.repository import OrderRepository
from app.modules.orders.service import OrderService
from app.modules.products.repository import ProductRepository
from app.modules.returns.repository import ReturnRepository
from app.modules.returns.schemas import ReturnCreate, ReturnRefund, ReturnResolve
from app.modules.returns.service import ReturnService
from app.modules.users.repository import UserRepository

ADMIN = {"name": "Ada Admin", "is_admin": True}

# Two lines worth $100 together, a $10 coupon, free shipping, 8% tax on the
# discounted $90 — the same shape as test_pricing.ORDER, so the expected refunds
# are the numbers already verified there.
BED = {"product_id": None, "name": "Dog Bed", "price": 50.0, "quantity": 1}
BOWL = {"product_id": None, "name": "Bowl", "price": 25.0, "quantity": 2}


@pytest.fixture
def orders(db):
    return OrderService(
        repo=OrderRepository(db),
        products=ProductRepository(db),
        users=UserRepository(db),
        loyalty=LoyaltyService(LoyaltyRepository(db), notifications=None),
    )


@pytest.fixture
def returns(db, orders):
    return ReturnService(
        repo=ReturnRepository(db),
        orders=orders,
        products=ProductRepository(db),
        users=UserRepository(db),
    )


@pytest.fixture
async def delivered(make_user, make_product, make_order):
    """A delivered order with two real products behind its lines, so restocking
    has somewhere to put the units back."""
    user = await make_user()
    bed = await make_product(name="Dog Bed", price=50.0, stock=5)
    bowl = await make_product(name="Bowl", price=25.0, stock=5)
    order = await make_order(
        user["_id"],
        items=[
            {**BED, "product_id": str(bed["_id"])},
            {**BOWL, "product_id": str(bowl["_id"])},
        ],
        subtotal=100.0,
        discount=10.0,
        shipping_fee=0.0,
        tax=7.20,
        total=97.20,
        status="delivered",
    )
    return {"user": user, "bed": bed, "bowl": bowl, "order": order}


def request_for(delivered, *lines):
    """`lines` are (product_key, quantity) pairs, e.g. ("bed", 1)."""
    return ReturnCreate(
        order_id=str(delivered["order"]["_id"]),
        items=[
            {"product_id": str(delivered[key]["_id"]), "quantity": qty, "reason": "no_longer_needed"}
            for key, qty in lines
        ],
    )


async def settle(returns, ret, restock=True):
    """Approve and refund in one step — the happy path staff actually walk."""
    return_id = str(ret["_id"])
    await returns.approve(return_id, ADMIN, ReturnResolve(note=""))
    return await returns.refund(return_id, ADMIN, ReturnRefund(restock=restock))


# ---------------------------------------------------------------------- #
# Eligibility
# ---------------------------------------------------------------------- #


async def test_a_delivered_order_is_fully_returnable(returns, delivered, sent_email):
    result = await returns.eligibility(str(delivered["order"]["_id"]), delivered["user"])

    assert result["can_return"] is True
    assert {item["name"]: item["quantity_returnable"] for item in result["items"]} == {
        "Dog Bed": 1,
        "Bowl": 2,
    }


@pytest.mark.parametrize("status", ["pending_payment", "paid", "processing", "shipped"])
async def test_a_return_cannot_start_before_delivery(
    returns, make_user, make_order, sent_email, status
):
    user = await make_user()
    order = await make_order(user["_id"], status=status)

    result = await returns.eligibility(str(order["_id"]), user)

    assert result["can_return"] is False
    assert "delivered" in result["reason"]


async def test_the_return_window_closes(returns, make_user, make_order, sent_email, pinned_settings):
    """The self-service window is bounded; staff can still refund by hand."""
    pinned_settings.return_window_days = 0
    user = await make_user()
    order = await make_order(user["_id"], status="delivered")

    result = await returns.eligibility(str(order["_id"]), user)

    assert result["can_return"] is False
    assert "window closed" in result["reason"]


async def test_requesting_more_than_was_ordered_is_refused(returns, delivered, sent_email):
    with pytest.raises(ValidationError, match="Only 1 of Dog Bed"):
        await returns.request(delivered["user"], request_for(delivered, ("bed", 5)))


async def test_an_item_not_on_the_order_is_refused(returns, delivered, make_product, sent_email):
    stranger = await make_product(name="Leash", price=10.0)
    payload = ReturnCreate(
        order_id=str(delivered["order"]["_id"]),
        items=[{"product_id": str(stranger["_id"]), "quantity": 1, "reason": "wrong_item"}],
    )

    with pytest.raises(ValidationError, match="isn't on this order"):
        await returns.request(delivered["user"], payload)


# ---------------------------------------------------------------------- #
# Claiming units
# ---------------------------------------------------------------------- #


async def test_an_open_return_holds_the_units_it_asked_for(returns, delivered, sent_email):
    await returns.request(delivered["user"], request_for(delivered, ("bed", 1)))

    result = await returns.eligibility(str(delivered["order"]["_id"]), delivered["user"])

    returnable = {item["name"]: item["quantity_returnable"] for item in result["items"]}
    assert returnable == {"Dog Bed": 0, "Bowl": 2}


async def test_the_same_unit_cannot_be_claimed_twice(returns, delivered, sent_email):
    await returns.request(delivered["user"], request_for(delivered, ("bed", 1)))

    with pytest.raises(ValidationError, match="already been returned"):
        await returns.request(delivered["user"], request_for(delivered, ("bed", 1)))


async def test_a_rejected_return_releases_its_units(returns, delivered, sent_email):
    ret = await returns.request(delivered["user"], request_for(delivered, ("bed", 1)))
    await returns.reject(str(ret["_id"]), ADMIN, ReturnResolve(note="Outside policy"))

    # Asking again is allowed now that nothing holds the unit.
    again = await returns.request(delivered["user"], request_for(delivered, ("bed", 1)))
    assert again["status"] == "requested"


async def test_returning_everything_left_over_two_requests_is_allowed(returns, delivered, sent_email):
    await returns.request(delivered["user"], request_for(delivered, ("bed", 1)))
    second = await returns.request(delivered["user"], request_for(delivered, ("bowl", 2)))

    assert second["status"] == "requested"
    result = await returns.eligibility(str(delivered["order"]["_id"]), delivered["user"])
    assert result["can_return"] is False
    assert "already been returned" in result["reason"]


# ---------------------------------------------------------------------- #
# The state machine
# ---------------------------------------------------------------------- #


async def test_only_an_approved_return_can_be_refunded(returns, delivered, sent_email):
    ret = await returns.request(delivered["user"], request_for(delivered, ("bed", 1)))

    with pytest.raises(ValidationError, match="Only an approved return"):
        await returns.refund(str(ret["_id"]), ADMIN, ReturnRefund())


async def test_a_return_cannot_be_refunded_twice(returns, delivered, sent_email):
    ret = await returns.request(delivered["user"], request_for(delivered, ("bed", 1)))
    await settle(returns, ret)

    with pytest.raises(ValidationError, match="already been refunded"):
        await returns.refund(str(ret["_id"]), ADMIN, ReturnRefund())


async def test_a_decided_return_cannot_be_decided_again(returns, delivered, sent_email):
    ret = await returns.request(delivered["user"], request_for(delivered, ("bed", 1)))
    await returns.approve(str(ret["_id"]), ADMIN, ReturnResolve())

    with pytest.raises(ValidationError, match="already been approved"):
        await returns.approve(str(ret["_id"]), ADMIN, ReturnResolve())


# ---------------------------------------------------------------------- #
# Settlement
# ---------------------------------------------------------------------- #


async def test_a_partial_refund_pays_its_apportioned_share(db, returns, delivered, sent_email):
    ret = await returns.request(delivered["user"], request_for(delivered, ("bed", 1)))

    settled = await settle(returns, ret)

    # Half the order's value: $50 goods − $5 coupon share + $3.60 tax share.
    assert settled["refund_amount"] == pytest.approx(48.60)
    assert settled["refund_method"] == "manual"

    order = await db.orders.find_one({"_id": delivered["order"]["_id"]})
    assert order["refunded_amount"] == pytest.approx(48.60)
    assert order["status"] == "delivered", "a partial refund does not close the order"


async def test_restocking_puts_the_units_back_on_the_shelf(db, returns, delivered, sent_email):
    ret = await returns.request(delivered["user"], request_for(delivered, ("bowl", 2)))

    await settle(returns, ret, restock=True)

    bowl = await db.products.find_one({"_id": delivered["bowl"]["_id"]})
    assert bowl["stock"] == 7  # 5 + the 2 that came back


async def test_damaged_goods_stay_off_the_shelf(db, returns, delivered, sent_email):
    ret = await returns.request(delivered["user"], request_for(delivered, ("bowl", 2)))

    await settle(returns, ret, restock=False)

    bowl = await db.products.find_one({"_id": delivered["bowl"]["_id"]})
    assert bowl["stock"] == 5


async def test_settling_every_line_refunds_the_order_exactly_once_over(
    db, returns, delivered, sent_email
):
    """The property that matters most: two returns against one order pay out the
    order total to the cent, and the second one flips it to refunded."""
    first = await returns.request(delivered["user"], request_for(delivered, ("bed", 1)))
    second = await returns.request(delivered["user"], request_for(delivered, ("bowl", 2)))

    paid_first = (await settle(returns, first))["refund_amount"]
    paid_second = (await settle(returns, second))["refund_amount"]

    assert paid_first + paid_second == pytest.approx(delivered["order"]["total"])

    order = await db.orders.find_one({"_id": delivered["order"]["_id"]})
    assert order["status"] == "refunded"
    assert order["refunded_amount"] == pytest.approx(97.20)


async def test_the_last_return_reclaims_the_shipping(db, returns, make_user, make_product, make_order, sent_email):
    """Shipping comes back only once nothing is left un-returned — measured at
    settlement, so the return that happens to close the order collects it."""
    user = await make_user()
    bed = await make_product(name="Dog Bed", price=20.0, stock=5)
    bowl = await make_product(name="Bowl", price=20.0, stock=5)
    order = await make_order(
        user["_id"],
        items=[
            {"product_id": str(bed["_id"]), "name": "Dog Bed", "price": 20.0, "quantity": 1},
            {"product_id": str(bowl["_id"]), "name": "Bowl", "price": 20.0, "quantity": 1},
        ],
        subtotal=40.0,
        discount=0.0,
        shipping_fee=5.99,
        tax=3.20,
        total=49.19,
        status="delivered",
    )
    fixture = {"user": user, "bed": bed, "bowl": bowl, "order": order}

    first = await returns.request(user, request_for(fixture, ("bed", 1)))
    paid_first = (await settle(returns, first))["refund_amount"]
    assert paid_first == pytest.approx(21.60)  # no shipping on a partial

    second = await returns.request(user, request_for(fixture, ("bowl", 1)))
    paid_second = (await settle(returns, second))["refund_amount"]
    assert paid_second == pytest.approx(27.59)  # 20.00 + 1.60 + 5.99

    assert paid_first + paid_second == pytest.approx(49.19)


async def test_staff_may_override_the_computed_amount(db, returns, delivered, sent_email):
    ret = await returns.request(delivered["user"], request_for(delivered, ("bed", 1)))
    await returns.approve(str(ret["_id"]), ADMIN, ReturnResolve())

    settled = await returns.refund(str(ret["_id"]), ADMIN, ReturnRefund(amount=10.0))

    assert settled["refund_amount"] == pytest.approx(10.0)
    order = await db.orders.find_one({"_id": delivered["order"]["_id"]})
    assert order["refunded_amount"] == pytest.approx(10.0)


async def test_a_refund_unwinds_a_proportional_slice_of_the_points(
    db, orders, returns, make_user, make_product, make_order, sent_email
):
    """The money and the points split on the same share, so a half-value return
    claws back half the points the order earned."""
    user = await make_user(points=0)
    bed = await make_product(name="Dog Bed", price=50.0, stock=5)
    bowl = await make_product(name="Bowl", price=25.0, stock=5)
    order = await make_order(
        user["_id"],
        items=[
            {"product_id": str(bed["_id"]), "name": "Dog Bed", "price": 50.0, "quantity": 1},
            {"product_id": str(bowl["_id"]), "name": "Bowl", "price": 25.0, "quantity": 2},
        ],
        subtotal=100.0,
        discount=10.0,
        tax=7.20,
        total=97.20,
        status="shipped",
    )
    await orders.set_status(str(order["_id"]), "delivered")
    assert (await db.users.find_one({"_id": user["_id"]}))["loyalty_points"] == 900

    fixture = {"user": user, "bed": bed, "bowl": bowl, "order": order}
    ret = await returns.request(user, request_for(fixture, ("bed", 1)))
    await settle(returns, ret)

    # $48.60 of a $97.20 order is half, so half the 900 points go back out.
    balance = (await db.users.find_one({"_id": user["_id"]}))["loyalty_points"]
    assert balance == 900 - 450
