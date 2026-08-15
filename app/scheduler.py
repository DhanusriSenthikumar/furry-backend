"""Background work that runs on a clock rather than on a request.

Right now that means one job: placing the orders that due subscriptions owe.
`SubscriptionService.run_due` was always written to be driven by a scheduler —
it claims each due row before acting on it, so it is safe to call from anywhere
and from more than one caller at once — but nothing was actually driving it, so
in a deployed store no repeat delivery would ever have been placed.

**Why in-process rather than an external cron.** The runner needs no credential
when it lives inside the app, which avoids minting a machine account with admin
rights purely so a cron job can call an admin endpoint. It also needs no
infrastructure the deploy doesn't already have: the start command is a single
uvicorn process, with nowhere to hang a cron entry. The admin button at
`POST /admin/subscriptions/run` stays exactly as it was — running both at once is
safe by construction.

**Running more than one web worker** means one loop per worker. That is correct
but wasteful: the claim makes the duplicate runs harmless, they simply find
nothing left to take. Set `SUBSCRIPTION_RUNNER_ENABLED=false` and drive
`POST /admin/subscriptions/run` from a real scheduler if you would rather have
exactly one runner.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from app.core.config import settings
from app.db.mongodb import mongodb


class PeriodicTask:
    """Runs a coroutine on a fixed interval until it is stopped.

    Deliberately forgiving about failure: a run that raises is logged and the
    loop carries on, because a scheduler that dies on the first bad night is
    worse than one that occasionally logs. Only cancellation stops it.
    """

    def __init__(
        self,
        name: str,
        interval_seconds: float,
        run: Callable[[], Awaitable[object]],
        initial_delay_seconds: float = 0.0,
    ):
        self.name = name
        self.interval_seconds = interval_seconds
        self.run = run
        self.initial_delay_seconds = initial_delay_seconds
        self._task: asyncio.Task | None = None
        # Counters, for the health endpoint and for tests to assert against
        # without reaching into the loop.
        self.runs = 0
        self.failures = 0
        self.last_run_at: datetime | None = None
        self.last_error: str = ""

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self._loop(), name=self.name)

    async def stop(self) -> None:
        """Cancel the loop and wait for it to actually finish.

        Awaited rather than fire-and-forget so shutdown doesn't race a run that
        is halfway through placing an order.
        """
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        # Nothing useful happens in the first seconds of a boot, and staggering
        # the first run keeps a redeploy of several instances from starting
        # every runner at the same instant.
        if self.initial_delay_seconds > 0:
            await asyncio.sleep(self.initial_delay_seconds)

        while True:
            await self.run_once()
            await asyncio.sleep(self.interval_seconds)

    async def run_once(self) -> object | None:
        """One pass, with its failure contained. Also the seam tests drive."""
        self.runs += 1
        self.last_run_at = datetime.now(timezone.utc)
        try:
            return await self.run()
        except asyncio.CancelledError:
            # Inherits from BaseException, so it passes the clause below
            # untouched — but say so, because a scheduler that swallowed its own
            # cancellation would never shut down.
            raise
        except Exception as exc:
            self.failures += 1
            self.last_error = str(exc)[:200]
            print(f"Warning: scheduled task {self.name!r} failed: {exc}")
            return None


async def run_due_subscriptions() -> dict | None:
    """One pass of the subscription runner, against the live database.

    Returns None when there is nothing to run against, which is the normal state
    for a checkout of this repo with `MONGODB_URI` left blank.
    """
    if mongodb.database is None:
        return None

    # Imported here rather than at module scope: the router pulls in most of the
    # module graph, and app.main imports this module while it is still assembling
    # its own routers.
    from app.modules.subscriptions.router import build_subscription_service

    service = build_subscription_service(mongodb.database)
    result = await service.run_due(limit=settings.subscription_runner_batch_limit)

    # Silent when there was nothing to do, which is most of the time — an hourly
    # job that logged every heartbeat would bury the runs that mattered.
    if result.get("claimed"):
        print(
            f"Subscription runner: {result['ordered']} ordered, "
            f"{result['failed']} failed, {result['paused']} paused, "
            f"{result['skipped']} skipped (of {result['claimed']} due)"
        )
    return result


def build_subscription_runner() -> PeriodicTask | None:
    """The configured runner, or None when it is switched off."""
    if not settings.subscription_runner_enabled or not settings.subscriptions_enabled:
        return None
    return PeriodicTask(
        name="subscription-runner",
        interval_seconds=settings.subscription_runner_interval_minutes * 60,
        run=run_due_subscriptions,
        initial_delay_seconds=settings.subscription_runner_initial_delay_seconds,
    )
