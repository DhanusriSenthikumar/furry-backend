"""Carrier metadata and tracking-link construction.

The tracking URL is built once, on the server, and stored on the order. Every
surface — the order page, the shipped email, an MCP client — then reads the same
string, so a carrier changing its URL format is a one-line fix here rather than
a hunt through templates.
"""

from urllib.parse import quote

from app.core.enums import Carrier

CARRIER_NAMES: dict[str, str] = {
    "ups": "UPS",
    "fedex": "FedEx",
    "usps": "USPS",
    "dhl": "DHL",
    "bluedart": "Blue Dart",
    "delhivery": "Delhivery",
    "other": "Other courier",
}

# `{}` is replaced with the URL-escaped tracking number. A carrier with no
# public tracking page maps to "" — the number still gets recorded and shown.
_TRACKING_URLS: dict[str, str] = {
    "ups": "https://www.ups.com/track?tracknum={}",
    "fedex": "https://www.fedex.com/fedextrack/?trknbr={}",
    "usps": "https://tools.usps.com/go/TrackConfirmAction?tLabels={}",
    "dhl": "https://www.dhl.com/en/express/tracking.html?AWB={}",
    "bluedart": "https://www.bluedart.com/tracking/{}",
    "delhivery": "https://www.delhivery.com/track/package/{}",
    "other": "",
}


def carrier_name(carrier: str) -> str:
    return CARRIER_NAMES.get(carrier, carrier)


def tracking_url(carrier: str, tracking_number: str) -> str:
    """The carrier's tracking page for this parcel, or "" when there isn't one."""
    template = _TRACKING_URLS.get(carrier, "")
    if not template or not tracking_number:
        return ""
    return template.format(quote(tracking_number.strip()))


def carrier_options() -> list[dict[str, str]]:
    """Carriers as (value, label) pairs — the admin ship form renders this rather
    than keeping its own copy of the list."""
    return [{"value": value, "label": label} for value, label in CARRIER_NAMES.items()]


__all__ = ["Carrier", "CARRIER_NAMES", "carrier_name", "carrier_options", "tracking_url"]
