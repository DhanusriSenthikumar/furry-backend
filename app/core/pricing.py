"""Single source of truth for how an order's money is calculated.

Both the checkout quote endpoint and the real checkout run through `price_order`,
so the number a customer is shown is the number they are charged. Refunds run
through `price_refund` for the same reason — money leaving is computed with the
same care as money arriving.
"""

from dataclasses import asdict, dataclass

from app.core.config import settings


@dataclass
class OrderTotals:
    subtotal: float
    discount: float
    shipping_fee: float
    tax: float
    total: float

    def as_dict(self) -> dict:
        return asdict(self)


def price_order(subtotal: float, discount: float = 0.0) -> OrderTotals:
    subtotal = round(max(subtotal, 0.0), 2)
    # A coupon can never discount more than the cart is worth.
    discount = round(min(max(discount, 0.0), subtotal), 2)

    discounted = round(subtotal - discount, 2)
    shipping_fee = 0.0 if discounted >= settings.free_shipping_threshold else settings.shipping_flat_fee
    tax = round(discounted * settings.tax_rate, 2)
    total = round(discounted + shipping_fee + tax, 2)

    return OrderTotals(
        subtotal=subtotal,
        discount=discount,
        shipping_fee=round(shipping_fee, 2),
        tax=tax,
        total=total,
    )


@dataclass
class RefundBreakdown:
    """What a customer gets back, itemized the same way they were charged."""

    goods: float
    discount_share: float
    tax_share: float
    shipping_refund: float
    total: float

    def as_dict(self) -> dict:
        return asdict(self)


def price_refund(order: dict, returned_units: dict[str, int], is_full_return: bool) -> RefundBreakdown:
    """Refund owed for `returned_units` ({product_id: quantity}) of `order`.

    Shares of the coupon discount and the tax are apportioned by value rather
    than recomputed from today's settings: a customer who bought under an 8% tax
    rate is refunded at 8% even if the rate has since changed, and a whole-order
    coupon is clawed back only in proportion to what is going back.

    Shipping comes back only on a full return — a partial one still cost the
    store the same delivery.
    """
    order_subtotal = round(order.get("subtotal", order.get("total", 0.0)) or 0.0, 2)
    order_discount = round(order.get("discount", 0.0) or 0.0, 2)
    order_tax = round(order.get("tax", 0.0) or 0.0, 2)
    order_shipping = round(order.get("shipping_fee", 0.0) or 0.0, 2)

    goods = 0.0
    for item in order.get("items", []):
        quantity = returned_units.get(item["product_id"], 0)
        if quantity > 0:
            goods += item["price"] * quantity
    goods = round(goods, 2)

    # Proportion of the order, by value, that is coming back. Guarded because an
    # order with a zero subtotal (100% off) has nothing to apportion.
    share = goods / order_subtotal if order_subtotal > 0 else 0.0

    discount_share = round(order_discount * share, 2)
    net_goods = round(goods - discount_share, 2)
    discounted_subtotal = round(order_subtotal - order_discount, 2)
    tax_share = round(order_tax * (net_goods / discounted_subtotal), 2) if discounted_subtotal > 0 else 0.0
    shipping_refund = order_shipping if is_full_return else 0.0

    total = round(net_goods + tax_share + shipping_refund, 2)
    # Never hand back more than was taken, however the shares round.
    already_refunded = round(order.get("refunded_amount", 0.0) or 0.0, 2)
    refundable = round(max(order.get("total", 0.0) - already_refunded, 0.0), 2)
    total = min(total, refundable)

    return RefundBreakdown(
        goods=goods,
        discount_share=discount_share,
        tax_share=tax_share,
        shipping_refund=round(shipping_refund, 2),
        total=total,
    )
