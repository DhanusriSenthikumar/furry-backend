from app.modules.orders.repository import OrderRepository
from app.modules.products.repository import ProductRepository
from app.modules.returns.repository import ReturnRepository
from app.modules.users.repository import UserRepository


class AdminService:
    def __init__(
        self,
        orders: OrderRepository,
        users: UserRepository,
        products: ProductRepository,
        returns: ReturnRepository | None = None,
    ):
        self.orders = orders
        self.users = users
        self.products = products
        self.returns = returns

    async def get_stats(self) -> dict:
        revenue, order_count = await self.orders.revenue_and_order_count()
        orders_by_status = await self.orders.count_by_status()
        revenue_series = await self.orders.revenue_by_day(days=30)
        top_products = await self.orders.top_products(limit=5)
        total_users = await self.users.count()
        total_products = await self.products.count()

        # Reported alongside the gross figure rather than folded into it: a
        # revenue number that silently moved when a refund was issued would make
        # the two dashboards disagree with every report already exported.
        total_refunded = await self.returns.total_refunded() if self.returns else 0.0
        pending_returns = await self.returns.count_pending() if self.returns else 0

        return {
            "total_revenue": revenue,
            "total_refunded": total_refunded,
            "net_revenue": round(revenue - total_refunded, 2),
            "total_orders": order_count,
            "total_users": total_users,
            "total_products": total_products,
            "pending_returns": pending_returns,
            "orders_by_status": orders_by_status,
            "revenue_last_30_days": revenue_series,
            "top_products": top_products,
        }
