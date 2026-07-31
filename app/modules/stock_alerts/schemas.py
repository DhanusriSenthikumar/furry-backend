from pydantic import BaseModel


class StockAlertStatusOut(BaseModel):
    """What the product page needs to draw the button: whether *you* are on the
    list, and how many people are waiting in total."""

    product_id: str
    subscribed: bool
    waiting: int


class StockAlertDemandOut(BaseModel):
    """One row of the admin restock queue — a product ranked by how many
    customers asked to hear about it coming back."""

    product_id: str
    name: str
    slug: str
    image: str = ""
    stock: int
    waiting: int
    # ISO timestamp of the longest-waiting request, so a shelf that has been
    # empty for a month reads differently from one emptied this morning.
    oldest_request: str
