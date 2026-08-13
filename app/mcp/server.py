"""The store, exposed as an MCP server mounted at /mcp.

Every tool runs through the same services the REST API uses, so business rules —
stock reservation, coupon limits, admin gating, order-status history — behave
identically whether a request arrives from the storefront or from an agent.

Scope follows the credential the caller presents:

  no token      catalogue browsing only
  customer JWT  that customer's cart, wishlist, pets, orders and recommendations
  admin JWT     the customer tools plus shop management

Tools are always listed; the ones that need a credential say so and fail with an
explanation, which is friendlier to an agent than an endpoint that vanishes.
"""

from collections.abc import Mapping
from typing import Any, get_args

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from app.core.config import settings
from app.core.enums import OrderStatus, ReturnReason
from app.core.pagination import Pagination
from app.core.shipping import CARRIER_NAMES, carrier_name
from app.mcp.auth import McpError, current_user, get_database, require_admin, require_user
from app.modules.admin.service import AdminService
from app.modules.cart.repository import CartRepository
from app.modules.cart.service import CartService
from app.modules.categories.repository import CategoryRepository
from app.modules.coupons.repository import CouponRepository
from app.modules.coupons.service import CouponService
from app.modules.notifications.repository import NotificationRepository
from app.modules.notifications.service import NotificationService
from app.modules.orders.repository import OrderRepository
from app.modules.orders.router import build_order_service
from app.modules.orders.schemas import OrderItemIn, OrderQuote, ShipmentCreate
from app.modules.orders.service import OrderService
from app.modules.payments.razorpay_client import razorpay_client
from app.modules.payments.repository import PaymentRepository
from app.modules.payments.stripe_client import stripe_client
from app.modules.pets.repository import PetRepository
from app.modules.products.repository import ProductRepository
from app.modules.products.schemas import ProductUpdate
from app.modules.products.service import ProductService
from app.modules.questions.repository import QuestionRepository
from app.modules.questions.schemas import AnswerCreate, QuestionCreate
from app.modules.questions.service import QuestionService
from app.modules.recommendations.repository import ProductViewRepository
from app.modules.recommendations.service import RecommendationService
from app.modules.returns.repository import ReturnRepository
from app.modules.returns.schemas import ReturnCreate, ReturnItemIn, ReturnRefund, ReturnResolve
from app.modules.returns.service import ReturnService
from app.modules.reviews.repository import ReviewRepository
from app.modules.stock_alerts.router import build_stock_alert_service
from app.modules.stock_alerts.service import StockAlertService
from app.modules.users.repository import UserRepository
from app.modules.wishlist.repository import WishlistRepository
from app.modules.wishlist.service import WishlistService

INSTRUCTIONS = """\
Furry Friends is a pet-supply and plant store. Use these tools to search the
catalogue, answer questions about products, manage the signed-in customer's
cart, wishlist and orders, and (for staff) run the shop.

Prices are US dollars. Product tools accept either a product slug or its id.
Anything that touches a customer or the shop needs the store JWT sent as an
Authorization: Bearer header; POST /auth/token exchanges email and password for
one. Without a token only the catalogue tools work.
"""


# --------------------------------------------------------------------------- #
# Serializers — deliberately narrower than the REST schemas. An agent reasoning
# over a list of products does not need every field, and trimming keeps results
# inside a sensible token budget.
# --------------------------------------------------------------------------- #


def _product_brief(doc: dict) -> dict[str, Any]:
    price = doc.get("price", 0)
    compare_at = doc.get("compare_at_price", 0) or 0
    on_sale = compare_at > price > 0
    brief = {
        "id": str(doc["_id"]),
        "slug": doc.get("slug", ""),
        "name": doc.get("name", ""),
        "brand": doc.get("brand", ""),
        "category": doc.get("category_name", ""),
        "kind": doc.get("product_kind", "pet"),
        "price": price,
        "in_stock": doc.get("stock", 0) > 0,
        "stock": doc.get("stock", 0),
        "rating": doc.get("rating", 0.0),
        "rating_count": doc.get("rating_count", 0),
        "url": f"{settings.frontend_url}/products/{doc.get('slug', '')}",
    }
    if on_sale:
        brief["was_price"] = compare_at
        brief["discount_percent"] = round((1 - price / compare_at) * 100)
    return brief


def _product_detail(doc: dict) -> dict[str, Any]:
    detail = _product_brief(doc)
    detail["description"] = doc.get("description", "")
    detail["images"] = doc.get("images", [])
    if doc.get("suitable_pet_types"):
        detail["suitable_pet_types"] = doc["suitable_pet_types"]
    if doc.get("plant"):
        detail["plant_care"] = doc["plant"]
    return detail


def _iso(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _order_brief(doc: dict) -> dict[str, Any]:
    return {
        "id": str(doc["_id"]),
        "status": doc.get("status", ""),
        "total": doc.get("total", 0),
        "item_count": sum(item.get("quantity", 0) for item in doc.get("items", [])),
        "placed_at": _iso(doc.get("created_at", "")),
        "can_cancel": OrderService.can_cancel(doc),
    }


def _shipment_brief(shipment: dict | None) -> dict[str, Any] | None:
    if not shipment:
        return None
    return {
        "carrier": carrier_name(shipment.get("carrier", "")),
        "tracking_number": shipment.get("tracking_number", ""),
        "tracking_url": shipment.get("tracking_url", ""),
        "estimated_delivery": shipment.get("estimated_delivery", ""),
        "shipped_at": _iso(shipment.get("shipped_at", "")),
    }


def _order_detail(doc: dict) -> dict[str, Any]:
    detail = _order_brief(doc)
    detail["items"] = [
        {
            "product_id": item["product_id"],
            "name": item["name"],
            "price": item["price"],
            "quantity": item["quantity"],
        }
        for item in doc.get("items", [])
    ]
    detail["subtotal"] = doc.get("subtotal", 0)
    detail["discount"] = doc.get("discount", 0)
    detail["coupon_code"] = doc.get("coupon_code")
    detail["shipping_fee"] = doc.get("shipping_fee", 0)
    detail["tax"] = doc.get("tax", 0)
    detail["shipping_address"] = doc.get("shipping_address", {})
    detail["shipment"] = _shipment_brief(doc.get("shipment"))
    detail["refunded_amount"] = doc.get("refunded_amount", 0.0)
    can_return, blocked = OrderService.return_eligibility(doc)
    detail["can_return"] = can_return
    if not can_return:
        detail["return_blocked_reason"] = blocked
    detail["status_history"] = [
        {"status": entry["status"], "note": entry.get("note", ""), "at": _iso(entry["at"])}
        for entry in doc.get("status_history", [])
    ]
    return detail


def _return_brief(doc: dict) -> dict[str, Any]:
    brief = {
        "id": str(doc["_id"]),
        "order_id": doc["order_id"],
        "status": doc.get("status", ""),
        "items": [
            {"name": item["name"], "quantity": item["quantity"], "reason": item["reason"]}
            for item in doc.get("items", [])
        ],
        "comment": doc.get("comment", ""),
        "refund_estimate": doc.get("refund_estimate", 0.0),
        "requested_at": _iso(doc.get("created_at", "")),
    }
    if doc.get("status") == "refunded":
        brief["refund_amount"] = doc.get("refund_amount", 0.0)
        brief["refund_method"] = doc.get("refund_method", "")
        brief["restocked"] = doc.get("restocked", False)
    if doc.get("resolution_note"):
        brief["resolution_note"] = doc["resolution_note"]
    return brief


def _question_brief(doc: dict) -> dict[str, Any]:
    answer = doc.get("answer")
    slug = doc.get("product_slug", "")
    return {
        "id": str(doc["_id"]),
        "product_id": doc["product_id"],
        "product_name": doc.get("product_name", ""),
        "product_url": f"{settings.frontend_url}/products/{slug}" if slug else "",
        "asked_by": doc.get("user_name", ""),
        "question": doc.get("body", ""),
        "asked_at": _iso(doc.get("created_at", "")),
        "answer": answer["body"] if answer else None,
        "answered_by": answer.get("answered_by") if answer else None,
    }


# --------------------------------------------------------------------------- #
# Shared lookups
# --------------------------------------------------------------------------- #


def _alert_service(db) -> StockAlertService:
    return build_stock_alert_service(db)


def _order_service(db) -> OrderService:
    """Fully wired, including the user lookup — a status change made by an agent
    should email the customer, land in their feed, and settle their points
    exactly as one made in the admin UI does."""
    return build_order_service(db)


def _return_service(db) -> ReturnService:
    return ReturnService(
        ReturnRepository(db),
        _order_service(db),
        ProductRepository(db),
        UserRepository(db),
        PaymentRepository(db),
        stripe_client,
        razorpay_client,
        _alert_service(db),
        NotificationService(NotificationRepository(db)),
    )


async def _resolve_product(db, reference: str) -> dict:
    """Products are addressable by slug (what a URL shows) or id (what the cart
    stores). Agents see both, so accept either."""
    products = ProductRepository(db)
    doc = await products.find_by_slug(reference)
    if not doc:
        doc = await products.find_by_id(reference)
    if not doc:
        raise McpError(f"No product matches '{reference}'. Try search_products first.")
    return doc


async def _resolve_category_id(db, reference: str | None) -> str | None:
    if not reference:
        return None
    categories = CategoryRepository(db)
    for doc in await categories.find_many({}, limit=200):
        if reference.lower() in (doc.get("name", "").lower(), doc.get("slug", "").lower(), str(doc["_id"])):
            return str(doc["_id"])
    raise McpError(f"No category matches '{reference}'. Call list_categories to see the options.")


def _headers(ctx: Context) -> Mapping[str, str] | None:
    return ctx.headers


def build_mcp_server() -> MCPServer:
    mcp = MCPServer(
        name="furry-friends",
        title="Furry Friends Store",
        instructions=INSTRUCTIONS,
        version="1.0.0",
    )

    # ----------------------------------------------------------------- #
    # Catalogue — no credential required
    # ----------------------------------------------------------------- #

    @mcp.tool()
    async def search_products(
        query: str = "",
        product_kind: str | None = None,
        category: str | None = None,
        pet_type: str | None = None,
        brand: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        min_rating: float | None = None,
        on_sale: bool = False,
        in_stock_only: bool = True,
        sort: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search the catalogue. `query` matches name, brand and description.
        `product_kind` is "pet" or "plant"; `category` takes a category name,
        slug or id; `pet_type` is dog/cat/rabbit/bird/fish/reptile/other.
        `sort` is one of price_asc, price_desc, rating, newest, discount.
        No sign-in required."""
        db = get_database()
        service = ProductService(ProductRepository(db), CategoryRepository(db))
        docs = await service.list_products(
            Pagination(page=1, page_size=max(1, min(limit, 50))),
            category_id=await _resolve_category_id(db, category),
            pet_type=pet_type,
            q=query or None,
            sort=sort,
            brand=brand,
            min_price=min_price,
            max_price=max_price,
            in_stock=in_stock_only,
            product_kind=product_kind,
            min_rating=min_rating,
            on_sale=on_sale,
        )
        return [_product_brief(doc) for doc in docs]

    @mcp.tool()
    async def get_product(product: str) -> dict[str, Any]:
        """Full detail for one product by slug or id, including its description,
        care notes for plants, recent reviews and the answered Q&A thread.
        No sign-in required."""
        db = get_database()
        doc = await _resolve_product(db, product)
        product_id = str(doc["_id"])

        reviews = await ReviewRepository(db).find_by_product(product_id)
        questions = await QuestionRepository(db).find_by_product(product_id, limit=10)

        detail = _product_detail(doc)
        detail["reviews"] = [
            {"by": r.get("user_name", ""), "rating": r["rating"], "comment": r.get("comment", "")}
            for r in reviews[:5]
        ]
        detail["questions"] = [
            {"question": q["body"], "answer": q["answer"]["body"]} for q in questions if q.get("answer")
        ][:5]
        return detail

    @mcp.tool()
    async def list_categories(kind: str | None = None) -> list[dict[str, Any]]:
        """Every shopping category. `kind` narrows to "pet" or "plant".
        No sign-in required."""
        db = get_database()
        filter_ = {"kind": kind} if kind else {}
        docs = await CategoryRepository(db).find_many(filter_, limit=100)
        return [
            {
                "id": str(doc["_id"]),
                "name": doc["name"],
                "slug": doc["slug"],
                "kind": doc.get("kind", "pet"),
                "description": doc.get("description", ""),
            }
            for doc in docs
        ]

    @mcp.tool()
    async def list_brands(product_kind: str | None = None) -> list[str]:
        """Brands stocked, optionally scoped to the "pet" or "plant" range.
        No sign-in required."""
        return await ProductRepository(get_database()).distinct_brands(product_kind)

    # ----------------------------------------------------------------- #
    # The signed-in customer
    # ----------------------------------------------------------------- #

    @mcp.tool()
    async def whoami(ctx: Context) -> dict[str, Any]:
        """Who the current token belongs to, and what these tools may do for
        them. Returns `signed_in: false` when no token was sent."""
        user = await current_user(_headers(ctx))
        if not user:
            return {
                "signed_in": False,
                "scope": "catalogue only",
                "hint": "POST /auth/token with email and password, then send Authorization: Bearer <token>.",
            }
        return {
            "signed_in": True,
            "name": user["name"],
            "email": user["email"],
            "is_admin": bool(user.get("is_admin")),
            "scope": "shop management" if user.get("is_admin") else "own cart, wishlist, pets and orders",
        }

    @mcp.tool()
    async def get_recommendations(ctx: Context) -> dict[str, Any]:
        """Personalized picks for the signed-in customer, ranked against their
        pet profiles, past orders and browsing history. Each pick explains why
        it was chosen. Requires a customer token."""
        user = await require_user(_headers(ctx))
        db = get_database()
        service = RecommendationService(
            ProductRepository(db), PetRepository(db), OrderRepository(db), ProductViewRepository(db)
        )
        result = await service.recommend(user, limit=8)
        return {
            "pets": result["pet_names"],
            "personalized": result["personalized"],
            "picks": [
                {**_product_brief(item["product"]), "reason": item["reason"]} for item in result["items"]
            ],
        }

    @mcp.tool()
    async def list_my_pets(ctx: Context) -> list[dict[str, Any]]:
        """The customer's pet profiles — the basis for recommendations.
        Requires a customer token."""
        user = await require_user(_headers(ctx))
        pets = await PetRepository(get_database()).find_by_owner(str(user["_id"]))
        return [
            {
                "id": str(pet["_id"]),
                "name": pet["name"],
                "pet_type": pet["pet_type"],
                "breed": pet.get("breed", ""),
                "age_years": pet.get("age_years", 0),
                "weight_kg": pet.get("weight_kg", 0),
                "special_requirements": pet.get("special_requirements", ""),
            }
            for pet in pets
        ]

    @mcp.tool()
    async def get_cart(ctx: Context) -> dict[str, Any]:
        """The customer's current basket with a line-by-line breakdown.
        Requires a customer token."""
        user = await require_user(_headers(ctx))
        db = get_database()
        items = await CartService(CartRepository(db), ProductRepository(db)).get_cart(str(user["_id"]))
        lines = [
            {
                "product_id": str(item["product"]["_id"]),
                "name": item["product"]["name"],
                "unit_price": item["product"]["price"],
                "quantity": item["quantity"],
                "line_total": round(item["product"]["price"] * item["quantity"], 2),
            }
            for item in items
        ]
        return {"items": lines, "subtotal": round(sum(line["line_total"] for line in lines), 2)}

    @mcp.tool()
    async def add_to_cart(ctx: Context, product: str, quantity: int = 1) -> dict[str, Any]:
        """Add a product (slug or id) to the basket. Adding something already
        there increases its quantity. Requires a customer token."""
        if quantity < 1:
            raise McpError("Quantity must be at least 1 — use update_cart_item to remove a line.")
        user = await require_user(_headers(ctx))
        db = get_database()
        doc = await _resolve_product(db, product)
        if doc.get("stock", 0) < quantity:
            raise McpError(f"Only {doc.get('stock', 0)} of {doc['name']} left in stock.")

        service = CartService(CartRepository(db), ProductRepository(db))
        await service.add_item(str(user["_id"]), str(doc["_id"]), quantity)
        return {"added": doc["name"], "quantity": quantity, "cart": await get_cart(ctx)}

    @mcp.tool()
    async def update_cart_item(ctx: Context, product: str, quantity: int) -> dict[str, Any]:
        """Set the quantity of a basket line. A quantity of 0 removes it.
        Requires a customer token."""
        user = await require_user(_headers(ctx))
        db = get_database()
        doc = await _resolve_product(db, product)
        service = CartService(CartRepository(db), ProductRepository(db))
        await service.update_item(str(user["_id"]), str(doc["_id"]), quantity)
        return await get_cart(ctx)

    @mcp.tool()
    async def quote_cart(ctx: Context, coupon_code: str | None = None) -> dict[str, Any]:
        """Price the basket exactly as checkout would: subtotal, coupon discount,
        shipping and tax. Never places an order. An unusable coupon comes back as
        `coupon_error` with the totals still correct. Requires a customer token."""
        user = await require_user(_headers(ctx))
        db = get_database()
        cart_items = await CartService(CartRepository(db), ProductRepository(db)).get_cart(str(user["_id"]))
        if not cart_items:
            raise McpError("The basket is empty — add something with add_to_cart first.")

        orders = _order_service(db)
        quote = await orders.quote(
            user,
            OrderQuote(
                items=[
                    OrderItemIn(product_id=str(item["product"]["_id"]), quantity=item["quantity"])
                    for item in cart_items
                ],
                coupon_code=coupon_code,
            ),
        )
        return quote

    @mcp.tool()
    async def get_wishlist(ctx: Context) -> list[dict[str, Any]]:
        """Products the customer has saved for later. Requires a customer token."""
        user = await require_user(_headers(ctx))
        db = get_database()
        products = await WishlistService(WishlistRepository(db), ProductRepository(db)).get(str(user["_id"]))
        return [_product_brief(doc) for doc in products]

    @mcp.tool()
    async def save_to_wishlist(ctx: Context, product: str, remove: bool = False) -> list[dict[str, Any]]:
        """Save a product to the wishlist, or take it off with `remove: true`.
        Returns the updated wishlist. Requires a customer token."""
        user = await require_user(_headers(ctx))
        db = get_database()
        doc = await _resolve_product(db, product)
        service = WishlistService(WishlistRepository(db), ProductRepository(db))
        user_id = str(user["_id"])
        products = (
            await service.remove_item(user_id, str(doc["_id"]))
            if remove
            else await service.add_item(user_id, str(doc["_id"]))
        )
        return [_product_brief(p) for p in products]

    @mcp.tool()
    async def list_my_orders(ctx: Context, limit: int = 10) -> list[dict[str, Any]]:
        """The customer's order history, newest first. Requires a customer token."""
        user = await require_user(_headers(ctx))
        orders = await OrderRepository(get_database()).find_by_user(str(user["_id"]))
        return [_order_brief(doc) for doc in orders[: max(1, min(limit, 50))]]

    @mcp.tool()
    async def get_order(ctx: Context, order_id: str) -> dict[str, Any]:
        """One order in full: line items, totals, delivery address and the
        status timeline. Requires a customer token; staff can read any order."""
        user = await require_user(_headers(ctx))
        db = get_database()
        service = _order_service(db)
        return _order_detail(await service.get_owned(order_id, user))

    @mcp.tool()
    async def cancel_order(ctx: Context, order_id: str, reason: str = "") -> dict[str, Any]:
        """Cancel one of the customer's own orders. Allowed until it ships;
        stock goes back on the shelf. Requires a customer token."""
        user = await require_user(_headers(ctx))
        db = get_database()
        service = _order_service(db)
        return _order_detail(await service.cancel_own(order_id, user, reason))

    @mcp.tool()
    async def check_return_eligibility(ctx: Context, order_id: str) -> dict[str, Any]:
        """Whether an order can still be sent back, and how many of each line are
        left to return. Call this before request_return so the quantities asked
        for are ones the order can actually give. Requires a customer token."""
        user = await require_user(_headers(ctx))
        return await _return_service(get_database()).eligibility(order_id, user)

    @mcp.tool()
    async def request_return(
        ctx: Context,
        order_id: str,
        product: str,
        quantity: int,
        reason: str,
        comment: str = "",
    ) -> dict[str, Any]:
        """Open a return on a delivered order for one product.

        `reason` must be one of: damaged, wrong_item, not_as_described,
        no_longer_needed, arrived_late, other. The reply carries the estimated
        refund; staff still have to approve it before any money moves.
        Requires a customer token.
        """
        user = await require_user(_headers(ctx))
        db = get_database()
        doc = await _resolve_product(db, product)

        valid_reasons = set(get_args(ReturnReason))
        if reason not in valid_reasons:
            raise McpError(f"'{reason}' is not a return reason. Valid: {', '.join(sorted(valid_reasons))}.")
        if quantity < 1:
            raise McpError("Quantity must be at least 1.")

        created = await _return_service(db).request(
            user,
            ReturnCreate(
                order_id=order_id,
                items=[ReturnItemIn(product_id=str(doc["_id"]), quantity=quantity, reason=reason)],
                comment=comment,
            ),
        )
        return _return_brief(created)

    @mcp.tool()
    async def list_my_returns(ctx: Context) -> list[dict[str, Any]]:
        """The customer's returns and where each one stands. Requires a customer
        token."""
        user = await require_user(_headers(ctx))
        returns = await _return_service(get_database()).list_mine(str(user["_id"]))
        return [_return_brief(r) for r in returns]

    @mcp.tool()
    async def ask_product_question(ctx: Context, product: str, question: str) -> dict[str, Any]:
        """Post a question about a product for store staff to answer publicly.
        Requires a customer token."""
        user = await require_user(_headers(ctx))
        db = get_database()
        doc = await _resolve_product(db, product)
        service = QuestionService(QuestionRepository(db), ProductRepository(db), NotificationService(NotificationRepository(db)))
        asked = await service.ask(str(doc["_id"]), user, QuestionCreate(body=question))
        return _question_brief(asked)

    # ----------------------------------------------------------------- #
    # Shop management — admin token required
    # ----------------------------------------------------------------- #

    @mcp.tool()
    async def admin_dashboard(ctx: Context) -> dict[str, Any]:
        """Revenue, order counts by status, the 30-day revenue series and the
        best sellers. Requires an admin token."""
        await require_admin(_headers(ctx))
        db = get_database()
        service = AdminService(
            OrderRepository(db), UserRepository(db), ProductRepository(db), ReturnRepository(db)
        )
        stats = await service.get_stats()
        stats["unanswered_questions"] = await QuestionRepository(db).count_unanswered()
        return stats

    @mcp.tool()
    async def admin_low_stock(ctx: Context, threshold: int = 5, limit: int = 25) -> list[dict[str, Any]]:
        """The restock queue: everything at or below `threshold` units, thinnest
        shelf first. Requires an admin token."""
        await require_admin(_headers(ctx))
        docs = await ProductRepository(get_database()).find_low_stock(threshold, limit=max(1, min(limit, 100)))
        return [_product_brief(doc) for doc in docs]

    @mcp.tool()
    async def admin_stock_demand(ctx: Context, limit: int = 25) -> list[dict[str, Any]]:
        """What customers are waiting to be told is back, most-wanted first.
        Unlike `admin_low_stock` this is ranked by asked-for demand rather than
        by how thin the shelf is, so it says what to reorder rather than what
        merely ran out. Requires an admin token."""
        await require_admin(_headers(ctx))
        return await _alert_service(get_database()).demand(max(1, min(limit, 100)))

    @mcp.tool()
    async def admin_set_stock(ctx: Context, product: str, stock: int) -> dict[str, Any]:
        """Set a product's stock level. Requires an admin token."""
        if stock < 0:
            raise McpError("Stock cannot be negative.")
        await require_admin(_headers(ctx))
        db = get_database()
        doc = await _resolve_product(db, product)
        # Through the service rather than the repository, so refilling a shelf
        # here emails the waiting list exactly as an admin UI edit does.
        service = ProductService(ProductRepository(db), CategoryRepository(db), _alert_service(db))
        updated = await service.update(str(doc["_id"]), ProductUpdate(stock=stock))
        return _product_brief(updated)

    @mcp.tool()
    async def admin_set_sale_price(ctx: Context, product: str, compare_at_price: float) -> dict[str, Any]:
        """Put a product on sale by recording the price it used to sell at. Pass
        0 to end the sale. A value at or below the current price is stored as 0,
        so a sale badge always means a real saving. Requires an admin token."""
        await require_admin(_headers(ctx))
        db = get_database()
        doc = await _resolve_product(db, product)
        service = ProductService(ProductRepository(db), CategoryRepository(db))
        updated = await service.update(str(doc["_id"]), ProductUpdate(compare_at_price=compare_at_price))
        return _product_brief(updated)

    @mcp.tool()
    async def admin_list_orders(ctx: Context, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Store-wide orders, newest first, optionally filtered by status
        (pending_payment, paid, processing, shipped, delivered, cancelled,
        payment_failed). Requires an admin token."""
        await require_admin(_headers(ctx))
        db = get_database()
        service = _order_service(db)
        docs = await service.list_all(Pagination(page=1, page_size=max(1, min(limit, 100))), status=status)
        return [_order_brief(doc) for doc in docs]

    @mcp.tool()
    async def admin_update_order_status(
        ctx: Context, order_id: str, status: str, note: str = ""
    ) -> dict[str, Any]:
        """Move an order along its lifecycle and append a note to its timeline.
        Cancelling a not-yet-shipped order returns its stock. Requires an admin
        token."""
        await require_admin(_headers(ctx))
        valid = set(get_args(OrderStatus))
        if status not in valid:
            raise McpError(f"'{status}' is not an order status. Valid: {', '.join(sorted(valid))}.")

        db = get_database()
        service = _order_service(db)
        return _order_detail(await service.set_status(order_id, status, note))

    @mcp.tool()
    async def admin_ship_order(
        ctx: Context,
        order_id: str,
        carrier: str,
        tracking_number: str,
        estimated_delivery: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        """Hand a parcel to a carrier: records the tracking number, moves the
        order to shipped, and emails the customer a tracking link.

        `carrier` is one of: ups, fedex, usps, dhl, bluedart, delhivery, other.
        Calling it again on an already-shipped order corrects the number without
        re-notifying the customer. Requires an admin token.
        """
        await require_admin(_headers(ctx))
        if carrier not in CARRIER_NAMES:
            raise McpError(f"'{carrier}' is not a carrier. Valid: {', '.join(sorted(CARRIER_NAMES))}.")

        service = _order_service(get_database())
        order = await service.ship(
            order_id,
            ShipmentCreate(
                carrier=carrier,
                tracking_number=tracking_number,
                estimated_delivery=estimated_delivery,
                note=note,
            ),
        )
        return _order_detail(order)

    @mcp.tool()
    async def admin_list_returns(ctx: Context, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Returns across the store, newest first. Filter with
        `status: "requested"` for the queue still awaiting a decision
        (also: approved, rejected, refunded). Requires an admin token."""
        await require_admin(_headers(ctx))
        service = _return_service(get_database())
        docs = await service.list_all(Pagination(page=1, page_size=max(1, min(limit, 100))), status=status)
        return [_return_brief(doc) for doc in docs]

    @mcp.tool()
    async def admin_resolve_return(ctx: Context, return_id: str, approve: bool, note: str = "") -> dict[str, Any]:
        """Rule on a requested return. `approve: false` declines it. The note is
        shown to the customer verbatim, so say why. Approving does not move
        money — call admin_refund_return once the goods are back.
        Requires an admin token."""
        admin = await require_admin(_headers(ctx))
        service = _return_service(get_database())
        payload = ReturnResolve(note=note)
        resolved = (
            await service.approve(return_id, admin, payload)
            if approve
            else await service.reject(return_id, admin, payload)
        )
        return _return_brief(resolved)

    @mcp.tool()
    async def admin_refund_return(
        ctx: Context, return_id: str, restock: bool = True, amount: float | None = None, note: str = ""
    ) -> dict[str, Any]:
        """Pay out an approved return: refunds through the original gateway when
        one is configured, puts the units back on the shelf unless
        `restock: false` (damaged goods), and marks the order refunded once
        nothing is left owing.

        Leave `amount` unset to refund exactly what the pricing rules say is
        owed — goods, the coupon's share, tax, and shipping on a full return.
        Requires an admin token.
        """
        admin = await require_admin(_headers(ctx))
        service = _return_service(get_database())
        refunded = await service.refund(
            return_id, admin, ReturnRefund(note=note, restock=restock, amount=amount)
        )
        return _return_brief(refunded)

    @mcp.tool()
    async def admin_unanswered_questions(ctx: Context) -> list[dict[str, Any]]:
        """Customer questions still waiting on a reply, oldest first.
        Requires an admin token."""
        await require_admin(_headers(ctx))
        db = get_database()
        service = QuestionService(QuestionRepository(db), ProductRepository(db), NotificationService(NotificationRepository(db)))
        return [_question_brief(q) for q in await service.list_unanswered()]

    @mcp.tool()
    async def admin_answer_question(ctx: Context, question_id: str, answer: str) -> dict[str, Any]:
        """Answer a customer question. The reply is published on the product page
        under the staff name on the token. Requires an admin token."""
        admin = await require_admin(_headers(ctx))
        db = get_database()
        service = QuestionService(QuestionRepository(db), ProductRepository(db), NotificationService(NotificationRepository(db)))
        answered = await service.answer(question_id, admin, AnswerCreate(body=answer))
        return _question_brief(answered)

    return mcp


def build_mcp_app() -> Starlette | None:
    """The MCP endpoint as an ASGI app, or None when MCP_ENABLED is false.

    Streamable HTTP rejects unrecognised Host and Origin headers so a browser on
    another site can't drive a locally-running server. Deployments therefore have
    to declare their own hostname through MCP_ALLOWED_HOSTS.
    """
    if not settings.mcp_enabled:
        return None

    allowed_hosts = settings.mcp_allowed_host_list
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=[f"http://{host}" for host in allowed_hosts]
        + [f"https://{host}" for host in allowed_hosts]
        + settings.cors_origin_list,
    )
    return build_mcp_server().streamable_http_app(
        streamable_http_path="/mcp",
        # A tool call is a request/response round trip; no server-initiated
        # streaming means plain JSON replies and no session state to keep warm.
        json_response=True,
        stateless_http=True,
        transport_security=security,
    )
