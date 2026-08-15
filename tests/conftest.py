"""Shared fixtures.

Two decisions shape everything here.

**The real repositories are exercised, not fakes.** The ledger's correctness
lives in Mongo semantics — a unique index that rejects a second credit, an
`$inc` guarded by `$gte` that refuses to overdraw — so a hand-written fake would
only test itself. `mongomock_motor` honours both, plus the aggregation pipelines
the balance is rebuilt from, which makes it a faithful enough stand-in to trust.

**Settings are pinned per-test.** `app.core.pricing` and `app.core.loyalty` read
`settings` at call time, so a developer's `.env` would otherwise change what the
arithmetic tests expect. `pinned_settings` fixes the documented defaults so a
failure means the code moved, not the environment.

Anything a test is allowed to flip must be listed in `PINNED_DEFAULTS`, because
that dict is also the restore list — a setting changed by a test but missing from
it stays changed for the rest of the session.
"""

import pytest
from mongomock_motor import AsyncMongoMockClient

from app.core.config import settings

# The index definitions the money tests depend on are owned by app.main. Importing
# it here rather than restating them means dropping one of those indexes fails the
# suite instead of silently making the ledger non-idempotent. MCP is switched off
# first: build_mcp_app() runs at import time and the transport is irrelevant here.
settings.mcp_enabled = False

from app.db import mongodb as mongodb_module  # noqa: E402
from app.main import create_indexes  # noqa: E402

# The defaults documented in .env.example and the README. Every expected number in
# test_pricing.py and test_loyalty_rules.py is derived from these by hand.
#
# This doubles as the restore list, so it has to name every setting any test
# touches — not just the ones with a number in them. A test that flips a flag
# missing from here leaks it into every test that follows.
PINNED_DEFAULTS = {
    "shipping_flat_fee": 5.99,
    "free_shipping_threshold": 49.0,
    "tax_rate": 0.08,
    "loyalty_enabled": True,
    "loyalty_points_per_currency": 10.0,
    "loyalty_points_per_redeemed_currency": 200.0,
    "loyalty_min_redemption": 200,
    "loyalty_max_redemption_percent": 0.5,
    "referrals_enabled": True,
    "referral_referrer_points": 1000,
    "referral_referee_points": 500,
    "return_window_days": 30,
    "subscriptions_enabled": True,
    "subscription_discount_percent": 10.0,
    "subscription_max_failures": 3,
    "subscription_runner_enabled": True,
    "subscription_runner_interval_minutes": 60,
    "subscription_runner_batch_limit": 100,
    "subscription_runner_initial_delay_seconds": 30,
}


@pytest.fixture(autouse=True)
def pinned_settings():
    """Pin the commerce settings to their defaults, and put them back afterwards.

    Autouse: a test that reads a stale value from someone's local .env is worse
    than useless, because it passes or fails for a reason unrelated to the code.
    """
    original = {name: getattr(settings, name) for name in PINNED_DEFAULTS}
    for name, value in PINNED_DEFAULTS.items():
        setattr(settings, name, value)
    yield settings
    for name, value in original.items():
        setattr(settings, name, value)


@pytest.fixture
async def db(monkeypatch):
    """A fresh in-memory database carrying the app's real indexes."""
    database = AsyncMongoMockClient()["test_petstore"]
    # create_indexes reads the module-level singleton rather than taking an
    # argument, so point that at the mock for the duration of the test.
    monkeypatch.setattr(mongodb_module.mongodb, "database", database)
    await create_indexes()

    # create_indexes swallows every failure so a cold start survives an
    # unreachable database. That is right in production and wrong here: the
    # dedupe tests would pass vacuously against a collection with no unique
    # index, so prove the one they depend on actually exists.
    names = await database.loyalty_entries.index_information()
    assert any("dedupe_key" in name for name in names), (
        "loyalty_entries has no dedupe_key index — credits would not be idempotent"
    )
    return database


# ---------------------------------------------------------------------- #
# Factories
#
# Each returns the inserted document, so a test reads as the situation it is
# describing rather than as a pile of dict literals.
# ---------------------------------------------------------------------- #


@pytest.fixture
def sent_email(monkeypatch):
    """Captures outbound mail instead of printing it, and hands back the list.

    Patches `EmailService.send` rather than the individual `send_order_*`
    helpers, so each template still runs its own formatting — a KeyError in a
    subject line stays a test failure — while the console stays quiet.
    """
    outbox: list[dict] = []

    def fake_send(_self, to, subject, body):
        outbox.append({"to": to, "subject": subject, "body": body})
        return True

    monkeypatch.setattr("app.core.email.EmailService.send", fake_send)
    return outbox


@pytest.fixture
def make_user(db):
    async def _make_user(name="Sam Shopper", email=None, points=0, lifetime=None, is_admin=False):
        doc = {
            "name": name,
            "email": email or f"{name.split()[0].lower()}@petstore.test",
            "password_hash": "not-a-real-hash",
            "is_admin": is_admin,
            "is_active": True,
            "loyalty_points": points,
            # Lifetime tracks everything ever earned and only goes up, so it
            # defaults to the current balance rather than to zero.
            "loyalty_lifetime_points": points if lifetime is None else lifetime,
        }
        result = await db.users.insert_one(doc)
        doc["_id"] = result.inserted_id
        return doc

    return _make_user


@pytest.fixture
def make_product(db):
    async def _make_product(name="Salmon Kibble", price=20.0, stock=10):
        doc = {
            "name": name,
            "slug": name.lower().replace(" ", "-"),
            "description": "",
            "category_id": "cat-food",
            "category_name": "Food",
            "product_kind": "pet",
            "suitable_pet_types": ["dog"],
            "brand": "Acme",
            "price": price,
            "compare_at_price": 0,
            "images": [],
            "stock": stock,
            "rating": 0.0,
            "rating_count": 0,
        }
        result = await db.products.insert_one(doc)
        doc["_id"] = result.inserted_id
        return doc

    return _make_product


@pytest.fixture
def make_order(db):
    """An order sitting at `status`, priced exactly as `price_order` would have.

    Tests that care about the arithmetic pass the totals explicitly; the rest
    take these defaults, which describe one $100 line with a $10 coupon:
    subtotal 100 − discount 10 = 90 goods, free shipping (over 49), 8% tax.
    """

    async def _make_order(
        user_id,
        items=None,
        subtotal=100.0,
        discount=10.0,
        shipping_fee=0.0,
        tax=7.2,
        rewards_discount=0.0,
        total=97.2,
        status="delivered",
        redeem_points=0,
        refunded_amount=0.0,
        coupon_code=None,
    ):
        from datetime import datetime, timezone

        from bson import ObjectId

        now = datetime.now(timezone.utc)
        doc = {
            "user_id": str(user_id),
            # A real ObjectId even though no product row backs it: cancelling a
            # restockable order calls restore_stock, which parses the id. In
            # production every line carries a genuine product id, so a fixture
            # using "p1" would fail on a path that cannot fail for real.
            "items": items if items is not None else [
                {"product_id": str(ObjectId()), "name": "Salmon Kibble", "price": 50.0, "quantity": 2}
            ],
            "shipping_address": {
                "name": "Sam Shopper",
                "line1": "1 Test Street",
                "line2": "",
                "city": "Testville",
                "state": "TS",
                "zip": "00000",
                "phone": "5550000",
            },
            "subtotal": subtotal,
            "discount": discount,
            "shipping_fee": shipping_fee,
            "tax": tax,
            "rewards_discount": rewards_discount,
            "total": total,
            "coupon_code": coupon_code,
            "redeem_points": redeem_points,
            "refunded_amount": refunded_amount,
            "status": status,
            # Delivered orders need the timestamp the return window is measured
            # from — return_eligibility reads it out of the history, not the doc.
            "status_history": [{"status": status, "note": "", "at": now}],
            "created_at": now,
        }
        result = await db.orders.insert_one(doc)
        doc["_id"] = result.inserted_id
        return doc

    return _make_order
