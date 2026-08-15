"""Tiers, earning, and how much of a basket points may cover.

Pure arithmetic over `settings`, like `test_pricing` — no database.

One caveat worth knowing: `app.core.loyalty.TIERS` is built at *import* time from
the tier thresholds, so unlike every other setting it cannot be moved at runtime.
These tests therefore assert against the shipped defaults (silver 2,500 / gold
10,000 / platinum 25,000) rather than trying to monkeypatch them.
"""

import pytest

from app.core.config import settings
from app.core.loyalty import (
    clamp_redemption,
    max_redeemable_points,
    points_for_spend,
    points_for_value,
    redemption_value,
    standing,
    tier_for,
)

EXACT = {"abs": 1e-9}


# ---------------------------------------------------------------------- #
# Tiers
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "lifetime,expected_tier,expected_multiplier",
    [
        (0, "bronze", 1.0),
        (2_499, "bronze", 1.0),
        (2_500, "silver", 1.25),  # inclusive floor
        (9_999, "silver", 1.25),
        (10_000, "gold", 1.5),
        (24_999, "gold", 1.5),
        (25_000, "platinum", 2.0),
        (1_000_000, "platinum", 2.0),
    ],
)
async def test_tier_boundaries_are_inclusive(lifetime, expected_tier, expected_multiplier):
    assert tier_for(lifetime) == (expected_tier, expected_multiplier)


async def test_standing_reports_the_climb_to_the_next_tier():
    half_way = standing(1_250)
    assert half_way.tier == "bronze"
    assert half_way.next_tier == "silver"
    assert half_way.points_to_next_tier == 1_250
    assert half_way.progress_percent == pytest.approx(50.0, **EXACT)


async def test_progress_resets_against_the_new_floor_on_promotion():
    """Reaching silver restarts the bar against the silver→gold span, rather
    than carrying over progress measured from zero."""
    just_promoted = standing(2_500)
    assert just_promoted.tier == "silver"
    assert just_promoted.progress_percent == 0.0
    assert just_promoted.points_to_next_tier == 7_500  # 10,000 − 2,500


async def test_the_top_tier_has_nothing_left_to_climb():
    top = standing(30_000)
    assert top.tier == "platinum"
    assert top.next_tier is None
    assert top.next_tier_label is None
    assert top.points_to_next_tier == 0
    assert top.progress_percent == 100.0


async def test_a_negative_lifetime_reads_as_zero():
    assert standing(-500).lifetime_points == 0
    assert standing(-500).tier == "bronze"


# ---------------------------------------------------------------------- #
# Earning
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "lifetime,expected",
    [
        (0, 100),  # bronze, ×1.0
        (2_500, 125),  # silver, ×1.25
        (10_000, 150),  # gold, ×1.5
        (25_000, 200),  # platinum, ×2.0
    ],
)
async def test_earning_scales_with_the_tier_held_at_the_time(lifetime, expected):
    """$10 of goods at 10 points per unit, multiplied by the customer's tier."""
    assert points_for_spend(10.0, lifetime) == expected


async def test_nothing_is_earned_on_an_empty_or_negative_spend():
    assert points_for_spend(0.0) == 0
    assert points_for_spend(-25.0) == 0


async def test_earning_stops_when_loyalty_is_switched_off(pinned_settings):
    pinned_settings.loyalty_enabled = False
    assert points_for_spend(100.0) == 0


# ---------------------------------------------------------------------- #
# What points are worth
# ---------------------------------------------------------------------- #


async def test_redemption_value_rounds_down_to_the_cent():
    """Never hand back more than the points were worth: 199 points are worth
    $0.995, which is $0.99 — not a rounded-up dollar."""
    assert redemption_value(200) == pytest.approx(1.0, **EXACT)
    assert redemption_value(199) == pytest.approx(0.99, **EXACT)
    assert redemption_value(1_000) == pytest.approx(5.0, **EXACT)


async def test_worthless_redemptions_are_zero_not_negative():
    assert redemption_value(0) == 0.0
    assert redemption_value(-500) == 0.0
    assert points_for_value(0.0) == 0
    assert points_for_value(-1.0) == 0


async def test_value_and_points_invert_each_other():
    for points in (200, 1_000, 4_321):
        assert points_for_value(redemption_value(points)) <= points


# ---------------------------------------------------------------------- #
# How much of a basket points may cover
# ---------------------------------------------------------------------- #


async def test_redemption_is_capped_at_half_the_basket():
    """10,000 points are worth $50 — exactly half of a $100 basket, and the most
    that may be put towards it."""
    assert max_redeemable_points(balance=50_000, subtotal=100.0) == 10_000


async def test_redemption_is_capped_by_the_balance_when_that_bites_first():
    assert max_redeemable_points(balance=1_000, subtotal=100.0) == 1_000


async def test_a_balance_under_the_minimum_is_not_redeemable():
    """Returns 0 rather than a number below the minimum, so the UI has one
    thing to check."""
    assert max_redeemable_points(balance=199, subtotal=100.0) == 0


async def test_a_basket_too_small_to_reach_the_minimum_is_refused():
    """Half of a $1 basket is $0.50, worth 100 points — under the 200 minimum."""
    assert max_redeemable_points(balance=50_000, subtotal=1.0) == 0


async def test_nothing_is_redeemable_against_nothing():
    assert max_redeemable_points(balance=0, subtotal=100.0) == 0
    assert max_redeemable_points(balance=5_000, subtotal=0.0) == 0


async def test_redemption_stops_when_loyalty_is_switched_off(pinned_settings):
    pinned_settings.loyalty_enabled = False
    assert max_redeemable_points(balance=50_000, subtotal=100.0) == 0
    assert clamp_redemption(1_000, balance=50_000, subtotal=100.0) == 0


async def test_asking_for_too_many_points_spends_what_is_allowed():
    """Deliberately not an error: baskets shrink between the quote and the
    order, and failing a checkout over a number the customer never typed would
    be indefensible."""
    assert clamp_redemption(99_999, balance=1_000, subtotal=100.0) == 1_000


async def test_asking_for_fewer_than_the_minimum_spends_nothing():
    assert clamp_redemption(50, balance=1_000, subtotal=100.0) == 0
    assert clamp_redemption(settings.loyalty_min_redemption, balance=1_000, subtotal=100.0) == 200


async def test_asking_for_nothing_spends_nothing():
    assert clamp_redemption(0, balance=1_000, subtotal=100.0) == 0
    assert clamp_redemption(-100, balance=1_000, subtotal=100.0) == 0
