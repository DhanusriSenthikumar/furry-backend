from typing import Literal

PetType = Literal["dog", "cat", "rabbit", "bird", "fish", "reptile", "other"]
Gender = Literal["male", "female", "unknown"]

# The catalogue holds two lines of business. Pet products are the historical
# default, so documents written before plants existed have no product_kind at
# all — filters treat "not plant" as "pet" rather than requiring a backfill.
ProductKind = Literal["pet", "plant"]
CategoryKind = ProductKind

PlantType = Literal[
    "indoor",
    "outdoor",
    "succulent",
    "flowering",
    "herb",
    "aquatic",
    "bonsai",
    "seed",
    "supply",
]
LightNeed = Literal["low", "medium", "bright_indirect", "full_sun"]
WaterNeed = Literal["low", "medium", "high"]
CareLevel = Literal["easy", "moderate", "expert"]

OrderStatus = Literal[
    "pending_payment",
    "paid",
    "processing",
    "shipped",
    "delivered",
    "cancelled",
    "payment_failed",
    # Reached only by refunding every unit on the order. A partial refund leaves
    # the order "delivered" and lives on the return record instead, so the
    # status never claims more than actually happened.
    "refunded",
]

# Carriers the store can hand a parcel to. "other" covers a local courier with
# no tracking page — the number is still recorded, just not linkable.
Carrier = Literal["ups", "fedex", "usps", "dhl", "bluedart", "delhivery", "other"]

# A return is a request until staff rule on it, and money only moves at the end.
#   requested → approved → refunded
#   requested → rejected
ReturnStatus = Literal["requested", "approved", "rejected", "refunded"]

# Why the customer is sending it back. Drives nothing automatically, but it is
# the only field that tells staff whether the units can go back on the shelf.
ReturnReason = Literal[
    "damaged",
    "wrong_item",
    "not_as_described",
    "no_longer_needed",
    "arrived_late",
    "other",
]

PaymentGateway = Literal["stripe", "razorpay", "cod"]
PaymentStatus = Literal["created", "succeeded", "failed"]

DiscountType = Literal["percent", "fixed"]

# ---------------------------------------------------------------------- #
# Loyalty
# ---------------------------------------------------------------------- #

# Every movement of a customer's balance is one ledger row, and the row says why.
#   earned     — an order was delivered
#   redeemed   — points were spent at checkout (negative)
#   reversed   — an order that had earned points was cancelled or refunded (negative)
#   refunded   — points spent on an order that never happened, handed back
#   referral   — a referral qualified, for either side of it
#   adjustment — staff moved the balance by hand, in either direction
LoyaltyKind = Literal["earned", "redeemed", "reversed", "refunded", "referral", "adjustment"]

# Tiers are earned on lifetime points and never fall — a customer who reaches
# Gold keeps it. What a tier buys is a higher earn rate on everything after it.
LoyaltyTier = Literal["bronze", "silver", "gold", "platinum"]

# ---------------------------------------------------------------------- #
# Subscriptions
# ---------------------------------------------------------------------- #

# A subscription is live, temporarily stopped, or over.
#   active    — will place an order when it next falls due
#   paused    — skipped indefinitely, keeps its schedule until resumed
#   cancelled — terminal; a new subscription is needed to start again
SubscriptionStatus = Literal["active", "paused", "cancelled"]

# ---------------------------------------------------------------------- #
# Notifications
# ---------------------------------------------------------------------- #

# What a feed entry is about. Drives the icon and colour the UI picks, so a
# customer can scan the list without reading every line.
NotificationKind = Literal[
    "order",
    "shipment",
    "return",
    "refund",
    "question",
    "stock",
    "reward",
    "referral",
    "subscription",
    "support",
    "system",
]

# ---------------------------------------------------------------------- #
# Support
# ---------------------------------------------------------------------- #

# A ticket is open until someone closes it. "pending" means the ball is back in
# the customer's court — staff replied and are waiting on them.
TicketStatus = Literal["open", "pending", "resolved", "closed"]

TicketCategory = Literal["order", "delivery", "refund", "product", "account", "other"]

# Set by staff, not the customer — everyone would pick "urgent".
TicketPriority = Literal["low", "normal", "high", "urgent"]

# ---------------------------------------------------------------------- #
# Referrals
# ---------------------------------------------------------------------- #

# An invite is claimed at signup and only pays out once the newcomer actually
# receives something — which is what stops a referral being free money for
# creating accounts.
#   pending   — signed up, no delivered order yet
#   rewarded  — first order delivered, both sides paid
#   void      — disqualified (self-referral caught late, account deactivated)
ReferralStatus = Literal["pending", "rewarded", "void"]
