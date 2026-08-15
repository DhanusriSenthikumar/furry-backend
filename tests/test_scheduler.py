"""The background runner: the loop's behaviour, and that it places real orders.

Two halves. `PeriodicTask` is tested on its own with trivial coroutines and
millisecond intervals — what matters there is that a failing run doesn't kill the
loop and that stopping actually stops. Then the real subscription pass is driven
once against the database, because a scheduler that runs reliably but calls the
wrong thing would pass every test above it.
"""

import asyncio

import pytest

from app.core.config import settings
from app.db import mongodb as mongodb_module
from app.modules.subscriptions.router import build_subscription_service
from app.scheduler import PeriodicTask, build_subscription_runner, run_due_subscriptions

# Long enough for the event loop to get round to the task, short enough that the
# suite stays sub-second.
TICK = 0.01


# ---------------------------------------------------------------------- #
# The loop
# ---------------------------------------------------------------------- #


async def test_a_started_task_runs_repeatedly():
    calls = []

    task = PeriodicTask("counter", interval_seconds=TICK, run=lambda: _record(calls))
    task.start()
    await asyncio.sleep(TICK * 6)
    await task.stop()

    assert len(calls) >= 2, "the loop should have come round more than once"
    assert task.running is False


async def test_a_failing_run_does_not_kill_the_loop():
    """A scheduler that dies on the first bad night is worse than one that
    occasionally logs."""
    calls = []

    async def explode():
        calls.append(1)
        raise RuntimeError("the database went away")

    task = PeriodicTask("flaky", interval_seconds=TICK, run=explode)
    task.start()
    await asyncio.sleep(TICK * 6)
    running_before_stop = task.running
    await task.stop()

    assert len(calls) >= 2, "the loop should have retried after the failure"
    assert running_before_stop is True
    assert task.failures == task.runs
    assert "database went away" in task.last_error


async def test_the_initial_delay_holds_the_first_run_back():
    calls = []

    task = PeriodicTask(
        "delayed", interval_seconds=TICK, run=lambda: _record(calls), initial_delay_seconds=10
    )
    task.start()
    await asyncio.sleep(TICK * 3)
    assert calls == []

    await task.stop()


async def test_stopping_waits_for_the_run_in_flight():
    """Shutdown must not race a run halfway through placing an order."""
    started = asyncio.Event()
    finished = []

    async def slow():
        started.set()
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            finished.append("cancelled")
            raise

    task = PeriodicTask("slow", interval_seconds=TICK, run=slow)
    task.start()
    await started.wait()
    await task.stop()

    assert finished == ["cancelled"]
    assert task.running is False


async def test_starting_twice_does_not_create_a_second_loop():
    calls = []
    task = PeriodicTask("once", interval_seconds=TICK, run=lambda: _record(calls))

    task.start()
    first = task._task
    task.start()

    assert task._task is first
    await task.stop()


async def test_stopping_a_task_that_never_started_is_harmless():
    task = PeriodicTask("idle", interval_seconds=TICK, run=lambda: _record([]))
    await task.stop()  # must not raise
    assert task.running is False


async def test_run_once_reports_the_result_and_counts_the_pass():
    task = PeriodicTask("direct", interval_seconds=TICK, run=_answer)

    result = await task.run_once()

    assert result == 42
    assert task.runs == 1
    assert task.failures == 0
    assert task.last_run_at is not None


# ---------------------------------------------------------------------- #
# Wiring
# ---------------------------------------------------------------------- #


async def test_the_runner_is_built_from_configuration(pinned_settings):
    settings.subscription_runner_enabled = True
    settings.subscription_runner_interval_minutes = 15

    runner = build_subscription_runner()

    assert runner is not None
    assert runner.interval_seconds == 15 * 60


@pytest.mark.parametrize(
    "flag", ["subscription_runner_enabled", "subscriptions_enabled"]
)
async def test_switching_either_flag_off_leaves_no_runner(pinned_settings, flag):
    """`SUBSCRIPTION_RUNNER_ENABLED=false` is the documented way to hand the job
    to an external scheduler instead."""
    settings.subscription_runner_enabled = True
    setattr(settings, flag, False)

    assert build_subscription_runner() is None


async def test_a_pass_without_a_database_is_a_quiet_no_op(monkeypatch):
    """The normal state of a fresh checkout, with MONGODB_URI left blank."""
    monkeypatch.setattr(mongodb_module.mongodb, "database", None)

    assert await run_due_subscriptions() is None


# ---------------------------------------------------------------------- #
# What it actually does
# ---------------------------------------------------------------------- #


async def test_a_scheduled_pass_places_the_order_a_due_subscription_owes(
    db, make_user, make_product, sent_email
):
    """The whole point of the module: without something driving `run_due`, this
    order is never placed at all."""
    user = await make_user()
    product = await make_product(name="Salmon Kibble", price=40.0, stock=10)
    await _subscribe(db, user, product, due=_hours_ago(1))

    result = await run_due_subscriptions()

    assert result["claimed"] == 1
    assert result["ordered"] == 1

    order = await db.orders.find_one({"source": "subscription"})
    assert order is not None
    assert order["status"] == "pending_payment"
    assert order["items"][0]["name"] == "Salmon Kibble"
    # 10% subscriber discount on $40, then free shipping is not reached: the
    # discount is what the plan was signed up for, applied every time.
    assert order["discount"] == pytest.approx(4.0)

    # Stock was reserved, and the plan moved on rather than staying due.
    assert (await db.products.find_one({"_id": product["_id"]}))["stock"] == 9
    plan = await db.subscriptions.find_one({"user_id": str(user["_id"])})
    assert plan["orders_placed"] == 1
    assert plan["run_batch"] is None, "the claim must be released"


async def test_a_subscription_not_yet_due_is_left_alone(db, make_user, make_product, sent_email):
    user = await make_user()
    product = await make_product(stock=10)
    await _subscribe(db, user, product, due=_hours_ago(-24))  # due tomorrow

    result = await run_due_subscriptions()

    assert result["claimed"] == 0
    assert await db.orders.count_documents({}) == 0


async def test_two_passes_place_exactly_one_order(db, make_user, make_product, sent_email):
    """Back-to-back passes don't re-deliver: the first moves `next_delivery_at`
    out by the interval, so the second finds nothing due."""
    user = await make_user()
    product = await make_product(stock=10)
    await _subscribe(db, user, product, due=_hours_ago(1))

    first, second = await asyncio.gather(run_due_subscriptions(), run_due_subscriptions())

    assert first["ordered"] + second["ordered"] == 1
    assert await db.orders.count_documents({}) == 1


# ---------------------------------------------------------------------- #
# The claim
#
# Tested against the repository rather than through `run_due`, because the claim
# only exists between being taken and being released — a pass that completes puts
# the row back, so nothing observed afterwards can tell you it was ever held.
# ---------------------------------------------------------------------- #


async def test_a_claimed_row_is_invisible_to_the_next_run(db, make_user, make_product, sent_email):
    """What makes the runner safe alongside the admin button and on more than
    one web worker: whoever claims a row owns it until they let go."""
    from datetime import datetime, timezone

    from app.modules.subscriptions.repository import SubscriptionRepository

    user = await make_user()
    product = await make_product(stock=10)
    await _subscribe(db, user, product, due=_hours_ago(1))

    repo = SubscriptionRepository(db)
    now = datetime.now(timezone.utc)

    mine = await repo.claim_due(now, "batch-a", limit=100)
    theirs = await repo.claim_due(now, "batch-b", limit=100)

    assert len(mine) == 1
    assert theirs == [], "a row already claimed must not be handed to a second caller"


async def test_a_released_row_is_available_again(db, make_user, make_product, sent_email):
    from datetime import datetime, timezone

    from app.modules.subscriptions.repository import SubscriptionRepository

    user = await make_user()
    product = await make_product(stock=10)
    plan = await _subscribe(db, user, product, due=_hours_ago(1))

    repo = SubscriptionRepository(db)
    now = datetime.now(timezone.utc)

    await repo.claim_due(now, "batch-a", limit=100)
    await repo.release(str(plan["_id"]))

    assert len(await repo.claim_due(now, "batch-b", limit=100)) == 1


async def test_a_run_that_died_mid_flight_frees_its_rows(db, make_user, make_product, sent_email):
    """A crashed process or killed container must not strand a subscription as
    permanently claimed — nothing would ever pick it up again."""
    from datetime import datetime, timedelta, timezone

    from app.modules.subscriptions.repository import SubscriptionRepository

    user = await make_user()
    product = await make_product(stock=10)
    await _subscribe(db, user, product, due=_hours_ago(1))

    repo = SubscriptionRepository(db)
    now = datetime.now(timezone.utc)
    await repo.claim_due(now, "batch-that-died", limit=100)

    # Nothing to free while the claim is fresh.
    assert await repo.release_stale(now - timedelta(minutes=30)) == 0
    assert await repo.claim_due(now, "batch-b", limit=100) == []

    # Once it looks orphaned, it comes back.
    assert await repo.release_stale(now + timedelta(minutes=1)) == 1
    assert len(await repo.claim_due(now, "batch-c", limit=100)) == 1


async def test_an_out_of_stock_delivery_retries_rather_than_failing_the_batch(
    db, make_user, make_product, sent_email
):
    user = await make_user()
    empty = await make_product(name="Sold Out", stock=0)
    stocked = await make_product(name="In Stock", stock=10)
    await _subscribe(db, user, empty, due=_hours_ago(1))
    await _subscribe(db, user, stocked, due=_hours_ago(1))

    result = await run_due_subscriptions()

    assert result["claimed"] == 2
    assert result["ordered"] == 1, "one bad row must not abandon the rest of the batch"
    assert result["failed"] == 1

    plan = await db.subscriptions.find_one({"product_id": str(empty["_id"])})
    assert plan["status"] == "active", "still trying"
    assert plan["failure_count"] == 1
    assert "out of stock" in plan["last_error"].lower()
    # A failed delivery has to let go of the row too, or it is claimed forever
    # and never retried — the failure path is the one that matters most here,
    # because it is the one that repeats.
    assert plan["run_batch"] is None


async def test_repeated_misses_pause_the_subscription(db, make_user, make_product, sent_email):
    """A plan that retried forever in silence would be indistinguishable from
    one that was working."""
    user = await make_user()
    empty = await make_product(name="Sold Out", stock=0)
    await _subscribe(
        db, user, empty, due=_hours_ago(1), failure_count=settings.subscription_max_failures - 1
    )

    result = await run_due_subscriptions()

    assert result["paused"] + result["failed"] == 1
    plan = await db.subscriptions.find_one({"product_id": str(empty["_id"])})
    assert plan["status"] == "paused"


async def test_a_paused_subscription_is_never_delivered(db, make_user, make_product, sent_email):
    user = await make_user()
    product = await make_product(stock=10)
    await _subscribe(db, user, product, due=_hours_ago(1), status="paused")

    result = await run_due_subscriptions()

    assert result["claimed"] == 0
    assert await db.orders.count_documents({}) == 0


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #


async def _record(calls):
    calls.append(1)


async def _answer():
    return 42


def _hours_ago(hours):
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone.utc) - timedelta(hours=hours)


async def _subscribe(db, user, product, due, status="active", failure_count=0):
    """A stored subscription, shaped exactly as SubscriptionService.create leaves it."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    doc = {
        "user_id": str(user["_id"]),
        "product_id": str(product["_id"]),
        "product_name": product["name"],
        "product_slug": product.get("slug", ""),
        "quantity": 1,
        "interval_days": 30,
        "discount_percent": settings.subscription_discount_percent,
        "status": status,
        "next_delivery_at": due,
        "shipping_address": {
            "name": "Sam Shopper",
            "line1": "1 Test Street",
            "line2": "",
            "city": "Testville",
            "state": "TS",
            "zip": "00000",
            "phone": "5550000",
        },
        "orders_placed": 0,
        "last_order_id": None,
        "last_ordered_at": None,
        "failure_count": failure_count,
        "last_error": "",
        "run_batch": None,
        "run_started_at": None,
        "created_at": now,
        "updated_at": now,
    }
    result = await db.subscriptions.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


@pytest.fixture(autouse=True)
def live_database(db, monkeypatch):
    """`run_due_subscriptions` reads the module-level singleton, so point it at
    the test database for every test in this file."""
    monkeypatch.setattr(mongodb_module.mongodb, "database", db)
    return db
