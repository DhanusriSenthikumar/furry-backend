from pydantic import BaseModel, Field, field_validator

from app.core.enums import DiscountType


class CouponBase(BaseModel):
    description: str = ""
    discount_type: DiscountType = "percent"
    value: float = Field(gt=0)
    min_subtotal: float = Field(default=0.0, ge=0)
    # Caps a percent discount, e.g. "20% off, up to $15". 0 means uncapped.
    max_discount: float = Field(default=0.0, ge=0)
    # 0 means unlimited.
    usage_limit: int = Field(default=0, ge=0)
    per_user_limit: int = Field(default=1, ge=0)
    starts_at: str | None = None
    expires_at: str | None = None
    is_active: bool = True


class CouponCreate(CouponBase):
    code: str = Field(min_length=3, max_length=32)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.strip().upper()


class CouponUpdate(BaseModel):
    description: str | None = None
    discount_type: DiscountType | None = None
    value: float | None = Field(default=None, gt=0)
    min_subtotal: float | None = Field(default=None, ge=0)
    max_discount: float | None = Field(default=None, ge=0)
    usage_limit: int | None = Field(default=None, ge=0)
    per_user_limit: int | None = Field(default=None, ge=0)
    starts_at: str | None = None
    expires_at: str | None = None
    is_active: bool | None = None


class CouponOut(CouponBase):
    id: str
    code: str
    used_count: int


class CouponApply(BaseModel):
    """Preview a code against a cart subtotal before the order exists."""

    code: str
    subtotal: float = Field(ge=0)


class CouponPreviewOut(BaseModel):
    code: str
    description: str
    discount: float
    subtotal: float
    shipping_fee: float
    tax: float
    total: float
