"""
app/auth/service.py — Core auth logic.

- argon2id password hashing/verification
- JWT access token (15 min) and refresh token (7 days) in httpOnly cookies
- Seat-limit enforcement (max 4 learners + 1 admin per platform)
- Password reset token generation (printed to log in dev, emailed in prod)
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from sqlalchemy import update

from app.db.models import PasswordResetToken, User, utcnow

log = logging.getLogger(__name__)
settings = get_settings()

# argon2id hasher — RFC-9106 low-memory defaults for server use
_ph = PasswordHasher(
    time_cost=2,
    memory_cost=65536,  # 64 MB
    parallelism=2,
    hash_len=32,
    salt_len=16,
)



# ---------------------------------------------------------------------------
# Password
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hash_: str) -> bool:
    try:
        return _ph.verify(hash_, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(hash_: str) -> bool:
    return _ph.check_needs_rehash(hash_)


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def create_access_token(user_id: str, role: str, token_version: int = 0) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "role": role,
        "tv": int(token_version or 0),
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
        "type": "access",
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: str, token_version: int = 0) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "tv": int(token_version or 0),
        "iat": now,
        "exp": now + timedelta(days=settings.refresh_token_expire_days),
        "type": "refresh",
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    """Raises jwt.InvalidTokenError on any failure."""
    return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])


def token_version_matches(payload: dict, user: User) -> bool:
    """Tokens issued before the user's last logout / password reset are revoked."""
    return int(payload.get("tv", 0) or 0) == int(getattr(user, "token_version", 0) or 0)


async def bump_token_version(db: AsyncSession, user_id: str) -> None:
    """Revoke every outstanding access/refresh token for *user_id* (caller commits)."""
    await db.execute(
        update(User).where(User.id == user_id)
        .values(token_version=func.coalesce(User.token_version, 0) + 1)
    )


# ---------------------------------------------------------------------------
# Seat-limit enforcement
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

async def create_password_reset_token(db: AsyncSession, user_id: str) -> str:
    """Generate a reset token, store its hash, return raw token."""
    raw = secrets.token_urlsafe(32)
    token_hash = _sha256(raw)
    expires_at = utcnow() + timedelta(hours=2)
    record = PasswordResetToken(
        token_hash=token_hash,
        user_id=user_id,
        expires_at=expires_at,
    )
    db.add(record)
    await db.commit()
    return raw


async def consume_password_reset_token(
    db: AsyncSession, raw_token: str
) -> Optional[PasswordResetToken]:
    """Validate and consume a reset token."""
    token_hash = _sha256(raw_token)
    result = await db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.used_at == None,  # noqa: E711
            PasswordResetToken.expires_at > utcnow(),
        )
    )
    return result.scalar_one_or_none()


async def mark_reset_token_used(db: AsyncSession, record: PasswordResetToken) -> bool:
    """Atomically consume a reset token (single use). Returns False on a lost race."""
    now = utcnow()
    res = await db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.id == record.id, PasswordResetToken.used_at == None)  # noqa: E711
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return (res.rowcount or 0) == 1
