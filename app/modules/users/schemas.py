from typing import Annotated

from email_validator import EmailNotValidError, validate_email
from pydantic import AfterValidator, BaseModel, Field


def _validate_email(value: str) -> str:
    # test_environment=True accepts special-use/reserved domains (e.g. ".test",
    # ".local") so local/dev accounts like admin@petstore.test are valid.
    try:
        result = validate_email(value, check_deliverability=False, test_environment=True)
    except EmailNotValidError as exc:
        raise ValueError(str(exc)) from exc
    return result.normalized


EmailStr = Annotated[str, AfterValidator(_validate_email)]


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    # Optional invite code. An unrecognised one is ignored rather than rejected —
    # nobody should lose a completed signup over a mistyped bonus.
    referral_code: str | None = Field(default=None, max_length=32)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class ForgotPassword(BaseModel):
    email: EmailStr


class ResetPassword(BaseModel):
    token: str = Field(min_length=1)
    password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    id: str
    name: str
    email: EmailStr
    is_admin: bool
    is_active: bool
    # Carried on the session so the navbar can show a balance without a second
    # round trip. The rewards page is still the authority on the detail.
    loyalty_points: int = 0
    loyalty_tier: str = "bronze"


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)


class AdminUserUpdate(BaseModel):
    is_admin: bool | None = None
    is_active: bool | None = None


class AddressCreate(BaseModel):
    label: str = Field(min_length=1, max_length=50, default="Home")
    name: str = Field(min_length=1, max_length=100)
    line1: str = Field(min_length=1, max_length=200)
    line2: str = ""
    city: str = Field(min_length=1, max_length=100)
    state: str = Field(min_length=1, max_length=100)
    zip: str = Field(min_length=1, max_length=20)
    phone: str = Field(min_length=1, max_length=20)
    is_default: bool = False


class AddressUpdate(BaseModel):
    label: str | None = None
    name: str | None = None
    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    phone: str | None = None
    is_default: bool | None = None


class AddressOut(BaseModel):
    id: str
    label: str
    name: str
    line1: str
    line2: str
    city: str
    state: str
    zip: str
    phone: str
    is_default: bool
