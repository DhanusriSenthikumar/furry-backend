from pydantic import BaseModel


class RevenuePoint(BaseModel):
    date: str
    revenue: float


class TopProduct(BaseModel):
    product_id: str
    name: str
    units_sold: int


class AdminStats(BaseModel):
    # Gross, then what went back out, then the difference. Kept as three numbers
    # so a refund never silently rewrites a figure already reported.
    total_revenue: float
    total_refunded: float = 0.0
    net_revenue: float = 0.0
    total_orders: int
    total_users: int
    total_products: int
    pending_returns: int = 0
    orders_by_status: dict[str, int]
    revenue_last_30_days: list[RevenuePoint]
    top_products: list[TopProduct]
