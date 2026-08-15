"""The order state machine: stock, status transitions, and what each one settles.

Wired with the real repositories and a real `LoyaltyService`, because the
behaviour under test is the interaction between them — reserving stock, taking
points, and unwinding both when an order comes apart. Coupons, referrals and
notifications are left out; each is optional by construction and none of these
assertions depend on one.
"""

import pytest

from app.core.exceptions import InsufficientStockError, ValidationError
from app.modules.loyalty.repository import LoyaltyRepository
from app.modules.loyalty.service import LoyaltyService
from app.modules.orders.repository import OrderRepository
from app.modules.orders.schemas import OrderCreate, ShipmentCreate
from app.modules.orders.service import OrderService
from app.modules.products.repository import ProductRepository
from app.modules.users.repository import UserRepository

ADDRESS = {
    "name": "Sam Shopper",
    "line1": "1 Test Street",
    "line2": "",
    "city": "Testville",
    "state": "TS",
    "zip": "00000",
    "phone": "5550000",
}


@pytest.fixture
def orders(db):
    return OrderService(
        repo=OrderRepository(db),
        products=ProductRepository(db),
        users=UserRepository(db),
        loyalty=LoyaltyService(LoyaltyRepository(db), notifications=None),
    )


@pytest.fixture
def products_repo(db):
    return ProductRepository(db)


def checkout_payload(*lines, redeem_points=0):
    """`lines` are (product, quantity) pairs, in the order they'll be reserved."""
    return OrderCreate(
        items=[{"product_id": str(p["_id"]), "quantity": q} for p, q in lines],
        shipping_address=ADDRESS,
        redeem_points=redeem_points,
    )


async def stock_of(products_repo, product):
    return (await products_repo.find_by_id(str(product["_id"])))["stock"]


# ---------------------------------------------------------------------- #
# Checkout
# ---------------------------------------------------------------------- #


async def test_checkout_reserves_stock_and_prices_from_the_catalogue(
    orders, products_repo, make_user, make_product, sent_email
):
    user = await make_user()
    product = await make_product(price=30.0, stock=10)

    order = await orders.checkout(user, checkout_payload((product, 2)))

    assert order["status"] == "pending_payment"
    assert order["subtotal"] == pytest.approx(60.0)
    assert await stock_of(products_repo, product) == 8
    assert len(sent_email) == 1
    assert "order" in sent_email[0]["subject"].lower()


async def test_a_short_line_leaves_the_whole_shelf_untouched(
    orders, products_repo, make_user, make_product, sent_email
):
    """All-or-nothing: a basket that runs out on its second item must not leave
    the first one reserved against an order that was never created."""
    user = await make_user()
    plenty = await make_product(name="Kibble", price=30.0, stock=10)
    scarce = await make_product(name="Rare Toy", price=15.0, stock=1)

    with pytest.raises(InsufficientStockError, match="Rare Toy"):
        await orders.checkout(user, checkout_payload((plenty, 2), (scarce, 5)))

    assert await stock_of(products_repo, plenty) == 10
    assert await stock_of(products_repo, scarce) == 1
    assert await orders.repo.count({}) == 0


async def test_points_are_handed_back_when_the_order_cannot_be_placed(
    db, orders, make_user, make_product, sent_email
):
    """Points are taken before stock, so every path after that has to return
    them explicitly or the customer is out the balance and the goods."""
    user = await make_user(points=10_000)
    product = await make_product(price=100.0, stock=0)

    with pytest.raises(InsufficientStockError):
        await orders.checkout(user, checkout_payload((product, 1), redeem_points=1_000))

    refreshed = await db.users.find_one({"_id": user["_id"]})
    assert refreshed["loyalty_points"] == 10_000


async def test_redeemed_points_are_clamped_to_what_the_basket_allows(
    db, orders, make_user, make_product, sent_email
):
    """Half of a $100 basket is $50, worth 10,000 points — asking for more
    spends the ceiling rather than failing the checkout."""
    user = await make_user(points=50_000)
    product = await make_product(price=100.0, stock=5)

    order = await orders.checkout(user, checkout_payload((product, 1), redeem_points=99_999))

    assert order["redeem_points"] == 10_000
    assert order["rewards_discount"] == pytest.approx(50.0)
    refreshed = await db.users.find_one({"_id": user["_id"]})
    assert refreshed["loyalty_points"] == 40_000


async def test_checkout_survives_a_dead_mail_server(
    orders, products_repo, make_user, make_product, monkeypatch
):
    """Email must never take an order down."""

    def explode(*_args, **_kwargs):
        raise OSError("SMTP is down")

    monkeypatch.setattr("app.core.email.EmailService.send", explode)
    user = await make_user()
    product = await make_product(stock=10)

    order = await orders.checkout(user, checkout_payload((product, 1)))

    assert order["status"] == "pending_payment"
    assert await stock_of(products_repo, product) == 9


# ---------------------------------------------------------------------- #
# Cancellation
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize("status", ["pending_payment", "paid", "processing"])
async def test_a_customer_may_cancel_until_the_parcel_leaves(
    orders, make_user, make_order, sent_email, status
):
    user = await make_user()
    order = await make_order(user["_id"], status=status)

    cancelled = await orders.cancel_own(str(order["_id"]), user, reason="Changed my mind")

    assert cancelled["status"] == "cancelled"
    assert "Changed my mind" in cancelled["status_history"][-1]["note"]


@pytest.mark.parametrize("status", ["shipped", "delivered", "refunded"])
async def test_a_customer_may_not_cancel_once_it_has_shipped(
    orders, make_user, make_order, sent_email, status
):
    user = await make_user()
    order = await make_order(user["_id"], status=status)

    with pytest.raises(ValidationError):
        await orders.cancel_own(str(order["_id"]), user)


async def test_cancelling_puts_the_stock_back(orders, products_repo, make_user, make_product, sent_email):
    user = await make_user()
    product = await make_product(stock=10)
    order = await orders.checkout(user, checkout_payload((product, 3)))
    assert await stock_of(products_repo, product) == 7

    await orders.cancel_own(str(order["_id"]), user)

    assert await stock_of(products_repo, product) == 10


async def test_cancelling_an_already_cancelled_order_is_refused(orders, make_user, make_order, sent_email):
    user = await make_user()
    order = await make_order(user["_id"], status="cancelled")

    with pytest.raises(ValidationError, match="already cancelled"):
        await orders.cancel_own(str(order["_id"]), user)


# ---------------------------------------------------------------------- #
# Status transitions
# ---------------------------------------------------------------------- #


async def test_delivery_earns_points_and_announces_itself_once(
    db, orders, make_user, make_order, sent_email
):
    """Re-saving a status the order already has must not email the customer
    twice or mint new balance."""
    user = await make_user(points=0)
    order = await make_order(user["_id"], subtotal=100.0, discount=10.0, status="shipped")
    order_id = str(order["_id"])

    await orders.set_status(order_id, "delivered")
    assert len(sent_email) == 1
    assert (await db.users.find_one({"_id": user["_id"]}))["loyalty_points"] == 900

    await orders.set_status(order_id, "delivered")
    assert len(sent_email) == 1, "a repeated status must not re-email"
    assert (await db.users.find_one({"_id": user["_id"]}))["loyalty_points"] == 900


async def test_processing_is_not_worth_interrupting_someone_over(orders, make_user, make_order, sent_email):
    user = await make_user()
    order = await make_order(user["_id"], status="paid")

    await orders.set_status(str(order["_id"]), "processing")

    assert sent_email == []


async def test_every_transition_is_written_into_the_timeline(orders, make_user, make_order, sent_email):
    user = await make_user()
    order = await make_order(user["_id"], status="paid")
    order_id = str(order["_id"])

    await orders.set_status(order_id, "processing", "Picking")
    updated = await orders.set_status(order_id, "cancelled", "Out of stock after all")

    statuses = [entry["status"] for entry in updated["status_history"]]
    assert statuses == ["paid", "processing", "cancelled"]
    assert updated["status_history"][-1]["note"] == "Out of stock after all"


async def test_cancelling_a_delivered_order_unwinds_what_it_earned(
    db, orders, make_user, make_order, sent_email
):
    user = await make_user(points=0)
    order = await make_order(user["_id"], subtotal=100.0, discount=10.0, status="shipped")
    order_id = str(order["_id"])

    await orders.set_status(order_id, "delivered")
    assert (await db.users.find_one({"_id": user["_id"]}))["loyalty_points"] == 900

    await orders.set_status(order_id, "cancelled", "Never arrived")

    assert (await db.users.find_one({"_id": user["_id"]}))["loyalty_points"] == 0


# ---------------------------------------------------------------------- #
# Despatch
# ---------------------------------------------------------------------- #


def shipment(tracking="1Z999AA10123456784"):
    return ShipmentCreate(carrier="ups", tracking_number=tracking, estimated_delivery="Tue 4 Aug")


async def test_shipping_records_the_carrier_and_builds_the_tracking_link(
    orders, make_user, make_order, sent_email
):
    user = await make_user()
    order = await make_order(user["_id"], status="paid")

    shipped = await orders.ship(str(order["_id"]), shipment())

    assert shipped["status"] == "shipped"
    assert shipped["shipment"]["carrier"] == "ups"
    assert "1Z999AA10123456784" in shipped["shipment"]["tracking_url"]
    assert len(sent_email) == 1


async def test_an_unpaid_order_cannot_ship(orders, make_user, make_order, sent_email):
    """A parcel shouldn't leave on an order nobody has paid for — and shipping
    it would strand the customer's cancel button."""
    user = await make_user()
    order = await make_order(user["_id"], status="pending_payment")

    with pytest.raises(ValidationError, match="hasn't been paid"):
        await orders.ship(str(order["_id"]), shipment())


@pytest.mark.parametrize("status", ["cancelled", "refunded"])
async def test_a_dead_order_cannot_ship(orders, make_user, make_order, sent_email, status):
    user = await make_user()
    order = await make_order(user["_id"], status=status)

    with pytest.raises(ValidationError, match="can't be shipped"):
        await orders.ship(str(order["_id"]), shipment())


async def test_correcting_a_tracking_number_does_not_re_announce_the_despatch(
    orders, make_user, make_order, sent_email
):
    user = await make_user()
    order = await make_order(user["_id"], status="paid")
    order_id = str(order["_id"])

    await orders.ship(order_id, shipment("WRONG-NUMBER"))
    assert len(sent_email) == 1

    corrected = await orders.ship(order_id, shipment("1Z999AA10123456784"))

    assert corrected["shipment"]["tracking_number"] == "1Z999AA10123456784"
    assert len(sent_email) == 1, "correcting a typo is not a second despatch"
    assert corrected["status"] == "shipped"


# ---------------------------------------------------------------------- #
# Refunds written onto the order
# ---------------------------------------------------------------------- #


async def test_a_partial_refund_leaves_the_order_delivered(orders, make_user, make_order, sent_email):
    user = await make_user()
    order = await make_order(user["_id"], total=97.20, status="delivered")

    updated = await orders.record_refund(str(order["_id"]), 40.0, "Partial return")

    assert updated["status"] == "delivered"
    assert updated["refunded_amount"] == pytest.approx(40.0)


async def test_refunding_the_last_dollar_flips_the_order(orders, make_user, make_order, sent_email):
    user = await make_user()
    order = await make_order(user["_id"], total=97.20, status="delivered")
    order_id = str(order["_id"])

    await orders.record_refund(order_id, 40.0, "First return")
    updated = await orders.record_refund(order_id, 57.20, "Second return")

    assert updated["status"] == "refunded"
    assert updated["refunded_amount"] == pytest.approx(97.20)


async def test_an_order_a_cent_short_still_counts_as_fully_refunded(
    orders, make_user, make_order, sent_email
):
    """Float arithmetic across several partial refunds can land a cent shy of
    the total; that must not leave the order stuck one penny from done."""
    user = await make_user()
    order = await make_order(user["_id"], total=97.20, status="delivered")

    updated = await orders.record_refund(str(order["_id"]), 97.19, "Return")

    assert updated["status"] == "refunded"
