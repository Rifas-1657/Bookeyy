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
from typing import Literal, Optional

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from sqlalchemy import update

from app.db.models import Invite, PasswordResetToken, User, utcnow

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

MAX_LEARNER_SEATS = 4  # hard platform limit


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

async def count_learner_seats(db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count()).where(User.role == "learner", User.is_active == True)
    )
    return result.scalar_one()


async def count_admin_seats(db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count()).where(User.role == "admin", User.is_active == True)
    )
    return result.scalar_one()


async def can_add_learner(db: AsyncSession) -> bool:
    count = await count_learner_seats(db)
    return count < MAX_LEARNER_SEATS


# ---------------------------------------------------------------------------
# Invite management
# ---------------------------------------------------------------------------

InviteRole = Literal["admin", "learner"]


async def create_invite(
    db: AsyncSession,
    created_by: str,
    role: InviteRole = "learner",
    invited_email: Optional[str] = None,
    ttl_hours: int = 48,
) -> str:
    """Create an invite token. Returns the raw (unhashed) token."""
    if role not in ("admin", "learner"):
        raise ValueError(f"invalid invite role: {role!r}")
    raw = secrets.token_urlsafe(32)
    token_hash = _sha256(raw)
    expires_at = utcnow() + timedelta(hours=ttl_hours)
    invite = Invite(
        token_hash=token_hash,
        invited_email=invited_email.strip().lower() if invited_email else None,
        role=role,
        created_by=created_by,
        expires_at=expires_at,
    )
    db.add(invite)
    await db.commit()
    log.info("Invite created by %s for role=%s email=%s", created_by, role, invited_email)
    return raw


async def consume_invite(db: AsyncSession, raw_token: str) -> Optional[Invite]:
    """Look up a valid (unused, unexpired) invite. Does NOT consume it —
    call claim_invite() inside the signup transaction to consume atomically."""
    token_hash = _sha256(raw_token)
    result = await db.execute(
        select(Invite).where(
            Invite.token_hash == token_hash,
            Invite.used_at == None,  # noqa: E711
            Invite.expires_at > utcnow(),
        )
    )
    invite = result.scalar_one_or_none()
    return invite


def invite_email_matches(invite: Invite, email: str) -> bool:
    if not invite.invited_email:
        return True
    return invite.invited_email.strip().lower() == (email or "").strip().lower()


async def claim_invite(db: AsyncSession, invite_id: str, user_id: str) -> bool:
    """Atomically mark the invite used. Returns False if another request won the race.
    Does not commit — the caller commits together with the new user row."""
    now = utcnow()
    res = await db.execute(
        update(Invite)
        .where(Invite.id == invite_id, Invite.used_at == None, Invite.expires_at > now)  # noqa: E711
        .values(used_at=now, used_by=user_id)
        .execution_options(synchronize_session=False)
    )
    return (res.rowcount or 0) == 1


async def mark_invite_used(db: AsyncSession, invite: Invite, user_id: str) -> None:
    invite.used_by = user_id
    invite.used_at = utcnow()
    await db.commit()


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
