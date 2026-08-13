from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.exceptions import (
    ConflictError,
    DatabaseNotConfiguredError,
    ForbiddenError,
    InsufficientStockError,
    NotFoundError,
    PaymentGatewayNotConfiguredError,
    UnauthorizedError,
    ValidationError,
)
from app.db.mongodb import connect, mongodb
from app.mcp import build_mcp_app
from app.modules.admin.router import router as admin_router
from app.modules.auth.router import router as auth_router
from app.modules.cart.router import router as cart_router
from app.modules.categories.router import router as categories_router
from app.modules.coupons.router import router as coupons_router
from app.modules.loyalty.router import router as loyalty_router
from app.modules.notifications.router import router as notifications_router
from app.modules.orders.router import router as orders_router
from app.modules.payments.router import router as payments_router
from app.modules.pets.router import router as pets_router
from app.modules.products.router import router as products_router
from app.modules.questions.router import router as questions_router
from app.modules.referrals.router import router as referrals_router
from app.modules.returns.router import router as returns_router
from app.modules.reviews.router import router as reviews_router
from app.modules.stock_alerts.router import router as stock_alerts_router
from app.modules.subscriptions.router import router as subscriptions_router
from app.modules.support.router import router as support_router
from app.modules.users.router import router as users_router
from app.modules.wishlist.router import router as wishlist_router

# The same store, spoken to over MCP. None when MCP_ENABLED=false.
mcp_app = build_mcp_app()


async def create_indexes() -> None:
    if mongodb.database is None:
        print("Warning: MONGODB_URI is empty — running with no database connection until it is configured.")
        return
    try:
        await mongodb.database.users.create_index("email", unique=True)
        await mongodb.database.products.create_index("slug", unique=True)
        await mongodb.database.products.create_index([("product_kind", 1), ("category_id", 1)])
        await mongodb.database.products.create_index("compare_at_price")
        await mongodb.database.categories.create_index("slug", unique=True)
        await mongodb.database.categories.create_index("kind")
        await mongodb.database.carts.create_index("user_id", unique=True)
        await mongodb.database.wishlists.create_index("user_id", unique=True)
        await mongodb.database.reviews.create_index([("product_id", 1), ("user_id", 1)], unique=True)
        await mongodb.database.coupons.create_index("code", unique=True)
        await mongodb.database.questions.create_index([("product_id", 1), ("created_at", -1)])
        # A return is looked up three ways: the customer's list, the staff queue,
        # and "what of this order is already spoken for" on every new request.
        await mongodb.database.returns.create_index([("user_id", 1), ("_id", -1)])
        await mongodb.database.returns.create_index([("order_id", 1), ("status", 1)])
        await mongodb.database.returns.create_index("status")
        # Backs the verified-purchase check on every review submission.
        await mongodb.database.orders.create_index([("user_id", 1), ("status", 1), ("items.product_id", 1)])
        # One row per customer per product; re-viewing updates the timestamp.
        await mongodb.database.product_views.create_index([("user_id", 1), ("product_id", 1)], unique=True)
        await mongodb.database.product_views.create_index([("user_id", 1), ("viewed_at", -1)])
        # One standing request per customer per product, and the lookup every
        # restock runs: who is still waiting on this one.
        await mongodb.database.stock_alerts.create_index([("user_id", 1), ("product_id", 1)], unique=True)
        await mongodb.database.stock_alerts.create_index([("product_id", 1), ("notified_at", 1)])

        # The customer's feed, newest first, plus the badge count.
        await mongodb.database.notifications.create_index([("user_id", 1), ("_id", -1)])
        await mongodb.database.notifications.create_index([("user_id", 1), ("read_at", 1)])
        # Makes an event idempotent: a second attempt to record the same thing
        # for the same person is rejected rather than duplicated.
        #
        # Partial, not sparse. A compound *sparse* index only skips a document
        # when every indexed field is missing, and user_id never is — so the
        # keyless notifications, which are most of them, would all index as
        # dedupe_key: null and collide with each other. A customer would receive
        # exactly one un-keyed notification, ever, and the rest would be dropped
        # in silence because pushing a notification deliberately can't raise.
        await mongodb.database.notifications.create_index(
            [("user_id", 1), ("dedupe_key", 1)],
            unique=True,
            partialFilterExpression={"dedupe_key": {"$exists": True}},
        )

        # The points ledger. The unique key on dedupe_key is what makes crediting
        # single-shot — it is the only lock available for "this order has already
        # earned", so it must exist for the balance to be trustworthy.
        await mongodb.database.loyalty_entries.create_index([("user_id", 1), ("_id", -1)])
        await mongodb.database.loyalty_entries.create_index("dedupe_key", unique=True, sparse=True)
        await mongodb.database.loyalty_entries.create_index([("order_id", 1), ("kind", 1)])

        # One code per customer, and one referral per referee — an account can
        # be attributed once, ever.
        await mongodb.database.users.create_index("referral_code", unique=True, sparse=True)
        await mongodb.database.referrals.create_index("referee_id", unique=True)
        await mongodb.database.referrals.create_index([("referrer_id", 1), ("_id", -1)])

        # The runner's query: active plans that have fallen due and aren't
        # already claimed by another run.
        await mongodb.database.subscriptions.create_index(
            [("status", 1), ("next_delivery_at", 1), ("run_batch", 1)]
        )
        await mongodb.database.subscriptions.create_index([("user_id", 1), ("status", 1)])
        await mongodb.database.subscriptions.create_index([("user_id", 1), ("product_id", 1)])

        # The customer's ticket list and the staff queue, which is ordered by who
        # has been waiting longest.
        await mongodb.database.support_tickets.create_index([("user_id", 1), ("last_message_at", -1)])
        await mongodb.database.support_tickets.create_index([("status", 1), ("last_message_at", 1)])
        await mongodb.database.support_tickets.create_index([("awaiting", 1), ("status", 1)])
    except Exception as exc:
        print(f"Warning: could not create indexes (DB unreachable at startup?): {exc}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    connect()
    await create_indexes()
    if mcp_app is None:
        yield
        return
    # The MCP transport keeps its own session manager, which only runs inside the
    # app's lifespan — a mounted sub-app doesn't get one of its own.
    async with mcp_app.router.lifespan_context(mcp_app):
        yield


app = FastAPI(title="Pet Store API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

EXCEPTION_STATUS = {
    NotFoundError: 404,
    ConflictError: 409,
    ForbiddenError: 403,
    ValidationError: 400,
    UnauthorizedError: 401,
    InsufficientStockError: 409,
    PaymentGatewayNotConfiguredError: 503,
    DatabaseNotConfiguredError: 503,
}

for exc_class, status_code in EXCEPTION_STATUS.items():

    def make_handler(code: int):
        async def handler(request: Request, exc):
            return JSONResponse(status_code=code, content={"detail": exc.message})

        return handler

    app.add_exception_handler(exc_class, make_handler(status_code))

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(pets_router)
app.include_router(categories_router)
app.include_router(products_router)
app.include_router(stock_alerts_router)
app.include_router(cart_router)
app.include_router(wishlist_router)
app.include_router(reviews_router)
app.include_router(questions_router)
app.include_router(orders_router)
app.include_router(returns_router)
app.include_router(coupons_router)
app.include_router(payments_router)
app.include_router(subscriptions_router)
app.include_router(loyalty_router)
app.include_router(referrals_router)
app.include_router(support_router)
app.include_router(notifications_router)
app.include_router(admin_router)


@app.get("/health")
async def health():
    db_connected = False
    if mongodb.client is not None:
        try:
            await mongodb.client.admin.command("ping")
            db_connected = True
        except Exception:
            db_connected = False
    return {
        "status": "ok",
        "db_configured": mongodb.database is not None,
        "db_connected": db_connected,
        "mcp_enabled": mcp_app is not None,
    }


# Graft the transport's own /mcp route onto this router rather than mounting its
# Starlette app at "/", which would also inherit every unmatched path and answer
# with a plain-text 404 instead of the API's JSON one.
if mcp_app is not None:
    mcp_route = next((r for r in mcp_app.routes if getattr(r, "path", None) == "/mcp"), None)
    if mcp_route is not None:
        app.router.routes.append(mcp_route)
    else:
        # The transport moved its route; fall back to mounting the whole app so
        # /mcp keeps working even if this assumption stops holding.
        app.mount("/", mcp_app)
