from fastapi import APIRouter, Depends, Response

from app.core.config import settings
from app.core.security import create_access_token
from app.deps import get_current_user, get_db
from app.modules.auth.service import AuthService
from app.modules.referrals.router import build_referral_service
from app.modules.users.repository import UserRepository
from app.modules.users.router import user_out
from app.modules.users.schemas import (
    ForgotPassword,
    ResetPassword,
    UserCreate,
    UserLogin,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_MAX_AGE = settings.access_token_expire_minutes * 60


def _service(db=Depends(get_db)) -> AuthService:
    return AuthService(UserRepository(db), build_referral_service(db))


def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=COOKIE_MAX_AGE,
        path="/",
    )


@router.post("/signup", response_model=UserOut, status_code=201)
async def signup(payload: UserCreate, response: Response, service: AuthService = Depends(_service)):
    user, token = await service.signup(
        payload.name, payload.email, payload.password, payload.referral_code
    )
    _set_auth_cookie(response, token)
    return user_out(user)


@router.post("/login", response_model=UserOut)
async def login(payload: UserLogin, response: Response, service: AuthService = Depends(_service)):
    user, token = await service.login(payload.email, payload.password)
    _set_auth_cookie(response, token)
    return user_out(user)


@router.post("/token")
async def issue_token(payload: UserLogin, service: AuthService = Depends(_service)):
    """The same credentials as /auth/login, but the JWT comes back in the body.

    Browsers get their token as an httpOnly cookie they can't read, which is the
    point. Non-browser clients — the MCP server at /mcp, scripts, CI — need the
    raw token to send as `Authorization: Bearer <token>`, so they ask here."""
    _user, token = await service.login(payload.email, payload.password)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": COOKIE_MAX_AGE,
    }


@router.post("/logout")
async def logout(response: Response):
    # The clearing cookie has to carry the same attributes it was set with, or
    # the browser treats it as a different cookie and leaves the session intact.
    response.delete_cookie(
        "access_token",
        path="/",
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        httponly=True,
    )
    return {"ok": True}


@router.get("/me", response_model=UserOut)
async def me(user: dict = Depends(get_current_user)):
    return user_out(user)


@router.post("/forgot-password")
async def forgot_password(payload: ForgotPassword, service: AuthService = Depends(_service)):
    await service.request_password_reset(payload.email)
    # Deliberately identical whether or not the address exists.
    return {"ok": True, "detail": "If that email is registered, a reset link is on its way."}


@router.post("/reset-password", response_model=UserOut)
async def reset_password(payload: ResetPassword, response: Response, service: AuthService = Depends(_service)):
    user = await service.reset_password(payload.token, payload.password)
    # Log them straight in — they've just proven control of the mailbox.
    _set_auth_cookie(response, create_access_token(str(user["_id"])))
    return user_out(user)
