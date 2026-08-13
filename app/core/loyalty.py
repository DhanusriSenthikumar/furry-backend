"""Single source of truth for how loyalty points are earned, spent, and tiered.

Kept beside `pricing` and free of any database access for the same reason: the
number shown on the rewards page, the number quoted at checkout, and the number
actually written to the ledger all come from these functions, so they cannot
drift apart. The service layer decides *when* points move; this decides how many.
"""

from dataclasses import asdict, dataclass

from app.core.config import settings

# Ordered floor → ceiling. Each tier multiplies what every later order earns, so
# the benefit is forward-looking: reaching Gold doesn't repay old orders, it
# makes the next ones worth more.
TIERS: list[tuple[str, int, float]] = [
    ("bronze", 0, 1.0),
    ("silver", settings.loyalty_silver_threshold, 1.25),
    ("gold", settings.loyalty_gold_threshold, 1.5),
    ("platinum", settings.loyalty_platinum_threshold, 2.0),
]

TIER_LABELS = {
    "bronze": "Bronze",
    "silver": "Silver",
    "gold": "Gold",
    "platinum": "Platinum",
}


@dataclass
class TierStanding:
    """Where a customer sits, and what it would take to move up."""

    tier: str
    label: str
    multiplier: float
    lifetime_points: int
    # None at the top tier — there is nothing left to climb towards.
    next_tier: str | None
    next_tier_label: str | None
    points_to_next_tier: int
    # 0–100, for a progress bar. Sits at 100 once the top tier is reached.
    progress_percent: float

    def as_dict(self) -> dict:
        return asdict(self)


def tier_for(lifetime_points: int) -> tuple[str, float]:
    """The highest tier this lifetime total has reached, and its earn multiplier."""
    name, multiplier = "bronze", 1.0
    for tier_name, threshold, tier_multiplier in TIERS:
        if lifetime_points >= threshold:
            name, multiplier = tier_name, tier_multiplier
    return name, multiplier


def standing(lifetime_points: int) -> TierStanding:
    lifetime_points = max(int(lifetime_points), 0)
    name, multiplier = tier_for(lifetime_points)

    index = next(i for i, (tier_name, _, _) in enumerate(TIERS) if tier_name == name)
    current_floor = TIERS[index][1]
    upcoming = TIERS[index + 1] if index + 1 < len(TIERS) else None

    if upcoming is None:
        return TierStanding(
            tier=name,
            label=TIER_LABELS[name],
            multiplier=multiplier,
            lifetime_points=lifetime_points,
            next_tier=None,
            next_tier_label=None,
            points_to_next_tier=0,
            progress_percent=100.0,
        )

    next_name, next_threshold, _ = upcoming
    span = max(next_threshold - current_floor, 1)
    earned_into_tier = lifetime_points - current_floor
    return TierStanding(
        tier=name,
        label=TIER_LABELS[name],
        multiplier=multiplier,
        lifetime_points=lifetime_points,
        next_tier=next_name,
        next_tier_label=TIER_LABELS[next_name],
        points_to_next_tier=max(next_threshold - lifetime_points, 0),
        progress_percent=round(min(earned_into_tier / span, 1.0) * 100, 1),
    )


def points_for_spend(net_goods: float, lifetime_points: int = 0) -> int:
    """Points earned by an order, at the multiplier the customer had when it landed.

    `net_goods` is what was paid for the goods themselves — subtotal less every
    discount, before tax and shipping. Earning on tax would have the store paying
    rewards on money it never kept, and earning on the pre-discount subtotal
    would pay twice for the same promotion.
    """
    if not settings.loyalty_enabled or net_goods <= 0:
        return 0
    _tier, multiplier = tier_for(lifetime_points)
    return int(net_goods * settings.loyalty_points_per_currency * multiplier)


def redemption_value(points: int) -> float:
    """What `points` are worth as money, rounded down to the cent so redeeming
    can never hand back more than the points were worth."""
    if points <= 0 or settings.loyalty_points_per_redeemed_currency <= 0:
        return 0.0
    value = points / settings.loyalty_points_per_redeemed_currency
    return int(value * 100) / 100


def points_for_value(amount: float) -> int:
    """Inverse of `redemption_value` — the points a given discount costs."""
    if amount <= 0:
        return 0
    return int(round(amount * settings.loyalty_points_per_redeemed_currency))


def max_redeemable_points(balance: int, subtotal: float) -> int:
    """The most a customer may spend on this basket.

    Bounded by three things: what they hold, the share of the order points are
    allowed to cover, and the point value of that share. Returns 0 rather than a
    number below the minimum, so the UI has one thing to check.
    """
    if not settings.loyalty_enabled or balance <= 0 or subtotal <= 0:
        return 0

    cap_value = subtotal * settings.loyalty_max_redemption_percent
    allowed = min(balance, points_for_value(cap_value))
    # Round down to a whole cent's worth: points that buy a fraction of a cent
    # would be silently swallowed by the rounding in `redemption_value`.
    allowed = points_for_value(redemption_value(allowed))
    return allowed if allowed >= settings.loyalty_min_redemption else 0


def clamp_redemption(points: int, balance: int, subtotal: float) -> int:
    """Normalize a requested redemption to something spendable, or 0.

    Raising nothing on an over-ask is deliberate: baskets change between the
    quote and the checkout, and silently spending less is kinder than failing an
    order over a number the customer never typed.
    """
    ceiling = max_redeemable_points(balance, subtotal)
    if ceiling <= 0 or points <= 0:
        return 0
    points = min(int(points), ceiling)
    return points if points >= settings.loyalty_min_redemption else 0
