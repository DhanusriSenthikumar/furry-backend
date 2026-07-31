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
