"""
app/auth/router.py — Auth REST endpoints.

POST /auth/signup    — open learner signup
POST /auth/login     — email + password → access + refresh cookies
POST /auth/logout    — clear cookies
POST /auth/refresh   — rotate refresh token → new access token
POST /auth/password-reset/request  — request reset (emailed; see app/auth/mailer.py)
POST /auth/password-reset/confirm  — confirm reset with token + new password
GET  /auth/me                      — the signed-in account (name, email, verified)
PATCH /auth/me                     — change the display name
POST /auth/verify-email            — confirm the email with the token from the signup email
POST /auth/verify-email/resend     — send the verification email again

Cookie strategy:
  - access_token:  httpOnly, SameSite=Lax (Strict in prod), 15 min
  - refresh_token: httpOnly, SameSite=Lax (Strict in prod), 7 days
  - Also returns tokens in body for clients that prefer Authorization header.

CSRF: SameSite=Lax prevents CSRF for same-site requests. In cross-origin (prod),
use HTTPS + SameSite=None;Secure with a same-site proxy (Next.js rewrites).
"""
from __future__ import annotations

import logging
from datetime import datetime

import jwt
from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import service
from app.auth.dependencies import _resolve_token, get_current_user
from app.auth.mailer import send_email_verification, send_password_reset
from app.auth.service import (
    bump_token_version,
    token_version_matches,
    consume_password_reset_token,
    create_access_token,
    create_email_verify_token,
    create_password_reset_token,
    create_refresh_token,
    decode_token,
    hash_password,
    mark_reset_token_used,
    needs_rehash,
    verify_password,
)
from app.config import get_settings
from app.db.database import get_db
from app.db.models import Notification, User
from app.middleware.rate_limit import auth_limit, limiter

log = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter(prefix="/auth", tags=["auth"])

_COOKIE_KWARGS = dict(
    httponly=True,
    samesite="lax",
    secure=settings.app_env == "production",
)


# ---------------------------------------------------------------------------
# Request/response schemas
# ---------------------------------------------------------------------------

class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    display_name: str = Field(min_length=1, max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    role: str
    display_name: str
    email: str = ""


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str = Field(min_length=8)


class MessageResponse(BaseModel):
    message: str


class AccountOut(BaseModel):
    user_id: str
    email: str
    display_name: str
    role: str
    email_verified: bool


class AccountUpdate(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)


class VerifyEmailRequest(BaseModel):
    token: str


def _account(user: User) -> AccountOut:
    return AccountOut(user_id=user.id, email=user.email, display_name=user.display_name, role=user.role,
                      email_verified=user.email_verified_at is not None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    response.set_cookie(
        "access_token", access_token,
        max_age=settings.access_token_expire_minutes * 60,
        **_COOKIE_KWARGS,
    )
    response.set_cookie(
        "refresh_token", refresh_token,
        max_age=settings.refresh_token_expire_days * 86400,
        **_COOKIE_KWARGS,
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(auth_limit)
async def signup(
    request: Request,
    body: SignupRequest,
    response: Response,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    # 1. Check email uniqueness
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    # 2. Create user (open signup: always a learner; admins come from scripts/create_admin.py)
    user = User(
        email=body.email,
        display_name=body.display_name,
        role="learner",
        password_hash=hash_password(body.password),
    )
    db.add(user)
    await db.flush()  # get user.id

    await db.commit()

    # 3. Issue tokens
    access_token = create_access_token(user.id, user.role, user.token_version or 0)
    refresh_token = create_refresh_token(user.id, user.token_version or 0)
    _set_auth_cookies(response, access_token, refresh_token)

    # Learning is never blocked on this; the app shows a gentle banner until it's done.
    background.add_task(send_email_verification, user.email, user.display_name,
                        create_email_verify_token(user.id, user.email))
    log.info("User signed up: %s role=%s", user.email, user.role)
    return AuthResponse(
        access_token=access_token,
        user_id=user.id,
        role=user.role,
        display_name=user.display_name,
        email=user.email,
    )


@router.post("/login", response_model=AuthResponse)
@limiter.limit(auth_limit)
async def login(
    request: Request,
    body: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    result = await db.execute(
        select(User).where(User.email == body.email, User.is_active == True)  # noqa: E712
    )
    user = result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Rehash if params changed
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
        await db.commit()

    access_token = create_access_token(user.id, user.role, user.token_version or 0)
    refresh_token = create_refresh_token(user.id, user.token_version or 0)
    _set_auth_cookies(response, access_token, refresh_token)

    log.info("User logged in: %s", user.email)
    return AuthResponse(
        access_token=access_token,
        user_id=user.id,
        role=user.role,
        display_name=user.display_name,
        email=user.email,
    )


@router.post("/logout", response_model=MessageResponse)
async def logout(
    response: Response,
    access: str | None = Depends(_resolve_token),
    refresh_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    # Revoke every outstanding token for the caller (bump token_version), if we
    # can tell who they are from a still-valid access or refresh token.
    for tok in (access, refresh_token):
        if not tok:
            continue
        try:
            payload = decode_token(tok)
        except jwt.InvalidTokenError:
            continue
        user = await db.get(User, payload.get("sub"))
        if user is not None and token_version_matches(payload, user):
            await bump_token_version(db, user.id)
            await db.commit()
            break
    _clear_auth_cookies(response)
    return MessageResponse(message="Logged out")


@router.post("/refresh", response_model=AuthResponse)
async def refresh(
    response: Response,
    refresh_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No refresh token")
    try:
        payload = decode_token(refresh_token)
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
        user_id: str = payload["sub"]
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    result = await db.execute(
        select(User).where(User.id == user_id, User.is_active == True)  # noqa: E712
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if not token_version_matches(payload, user):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token revoked")

    new_access = create_access_token(user.id, user.role, user.token_version or 0)
    new_refresh = create_refresh_token(user.id, user.token_version or 0)
    _set_auth_cookies(response, new_access, new_refresh)

    return AuthResponse(
        access_token=new_access,
        user_id=user.id,
        role=user.role,
        display_name=user.display_name,
        email=user.email,
    )


@router.post("/password-reset/request", response_model=MessageResponse)
@limiter.limit(auth_limit)
async def request_password_reset(
    request: Request,
    body: PasswordResetRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    # Always return 200 to avoid email enumeration
    if user is None:
        return MessageResponse(message="If that email exists, a reset link was sent.")

    raw_token = await create_password_reset_token(db, user.id)

    # The raw token is NEVER logged (logs are routinely shipped/shared; a logged
    # token is an account-takeover primitive).
    background.add_task(send_password_reset, user.email, user.display_name, raw_token)
    log.info("Password reset requested for user_id=%s", user.id)

    return MessageResponse(message="If that email exists, a reset link was sent.")


@router.post("/password-reset/confirm", response_model=MessageResponse)
@limiter.limit(auth_limit)
async def confirm_password_reset(
    request: Request,
    body: PasswordResetConfirm,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    record = await consume_password_reset_token(db, body.token)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    result = await db.execute(select(User).where(User.id == record.user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if not await mark_reset_token_used(db, record):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )
    user.password_hash = hash_password(body.new_password)
    await bump_token_version(db, user.id)  # log out every existing session
    await db.commit()

    log.info("Password reset completed for %s", user.email)
    return MessageResponse(message="Password updated successfully")


@router.get("/me", response_model=AccountOut)
async def get_account(current_user: User = Depends(get_current_user)) -> AccountOut:
    return _account(current_user)


@router.patch("/me", response_model=AccountOut)
async def update_account(
    body: AccountUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AccountOut:
    name = body.display_name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Enter a display name.")
    current_user.display_name = name
    await db.commit()
    return _account(current_user)


@router.post("/verify-email", response_model=AccountOut)
@limiter.limit(auth_limit)
async def verify_email(
    request: Request,
    body: VerifyEmailRequest,
    db: AsyncSession = Depends(get_db),
) -> AccountOut:
    bad = HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This link is invalid or has expired.")
    try:
        payload = decode_token(body.token)
    except jwt.InvalidTokenError:
        raise bad
    if payload.get("type") != "verify_email":
        raise bad
    user = await db.get(User, payload.get("sub"))
    if user is None or user.email != payload.get("email"):
        raise bad
    if user.email_verified_at is None:
        user.email_verified_at = datetime.utcnow()
        await db.commit()
        log.info("Email verified for user_id=%s", user.id)
    return _account(user)


@router.post("/verify-email/resend", response_model=MessageResponse)
@limiter.limit(auth_limit)
async def resend_verification(
    request: Request,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
) -> MessageResponse:
    if current_user.email_verified_at is not None:
        return MessageResponse(message="Your email is already confirmed.")
    background.add_task(send_email_verification, current_user.email, current_user.display_name,
                        create_email_verify_token(current_user.id, current_user.email))
    return MessageResponse(message="Verification email sent.")
