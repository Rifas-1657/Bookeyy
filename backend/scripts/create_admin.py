"""
scripts/create_admin.py — create (or reset) the first admin account. Safe in production.

Nothing is hard-coded and nothing is printed except the email: the password comes from
BOOKEY_ADMIN_PASSWORD (env) or an interactive hidden prompt (asked twice). Run it from a
machine that can reach the production database, with the production DATABASE_URL.

  cd backend
  DATABASE_URL='postgresql+asyncpg://...:6543/postgres?ssl=require' DB_PGBOUNCER=true \
      python scripts/create_admin.py --email you@example.com
  # existing account → admin with a new password:
  ... python scripts/create_admin.py --email you@example.com --reset
  # or take the production database from deploy/secrets.env (works in any shell):
  python scripts/create_admin.py --email you@example.com --production

After logging in as admin, invite learners from the admin Members page
(POST /api/admin/members/invite).
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

MIN_LEN = 12


def _password() -> str:
    pw = os.environ.get("BOOKEY_ADMIN_PASSWORD", "")
    if pw:
        return pw
    if not sys.stdin.isatty():
        raise SystemExit("Set BOOKEY_ADMIN_PASSWORD or run in an interactive terminal.")
    pw = getpass.getpass("New admin password: ")
    if pw != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match.")
    return pw


def _use_production_db() -> None:
    """Point this run at SUPABASE_DB_URL from deploy/secrets.env (set before app imports)."""
    secrets = ROOT.parent / "deploy" / "secrets.env"
    if not secrets.is_file():
        raise SystemExit(f"{secrets} not found.")
    for line in secrets.read_text(encoding="utf-8").splitlines():
        key, _, value = line.strip().partition("=")
        if key == "SUPABASE_DB_URL" and "://" in value:
            os.environ["DATABASE_URL"] = f"postgresql+asyncpg://{value.split('://', 1)[1]}?ssl=require"
            os.environ["DB_PGBOUNCER"] = "true"
            return
    raise SystemExit("SUPABASE_DB_URL is missing from deploy/secrets.env.")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--email", default=os.environ.get("BOOKEY_ADMIN_EMAIL", ""))
    ap.add_argument("--name", default="Bookey Admin")
    ap.add_argument("--reset", action="store_true", help="reset password / promote an existing user")
    ap.add_argument("--production", action="store_true", help="use SUPABASE_DB_URL from deploy/secrets.env")
    args = ap.parse_args()
    if args.production:
        _use_production_db()
    email = args.email.strip().lower()
    if "@" not in email:
        raise SystemExit("Pass --email (or BOOKEY_ADMIN_EMAIL).")
    if "sqlite" in os.environ.get("DATABASE_URL", "sqlite").lower():
        print("Warning: DATABASE_URL is not set to PostgreSQL — writing to the local SQLite DB.")
    pw = _password()
    if len(pw) < MIN_LEN or len(set(pw)) < 6:
        raise SystemExit(f"Use a strong password (>= {MIN_LEN} characters, not repetitive).")

    from sqlalchemy import func, select
    from app.auth.service import hash_password
    from app.db.database import AsyncSessionLocal, engine
    from app.db.models import User

    try:
        async with AsyncSessionLocal() as db:
            user = (await db.execute(select(User).where(func.lower(User.email) == email))).scalar_one_or_none()
            if user is not None and not args.reset:
                raise SystemExit(f"{email} already exists; re-run with --reset to make it admin with a new password.")
            if user is None:
                user = User(email=email, display_name=args.name, role="admin")
                db.add(user)
            user.role = "admin"
            user.is_active = True
            user.password_hash = hash_password(pw)
            user.token_version = (user.token_version or 0) + 1   # sign out old sessions
            await db.commit()
    finally:
        await engine.dispose()
    print(f"Admin ready: {email}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
