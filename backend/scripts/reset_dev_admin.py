"""
scripts/reset_dev_admin.py — set admin@bookey.dev's password to BOOKEY_ADMIN_DEV_PASSWORD from .env.

Development only (refuses when APP_ENV=production). Use when the admin was seeded
with a random password and you need to log in locally.

Usage: .venv/Scripts/python scripts/reset_dev_admin.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


async def main() -> int:
    env = dotenv_values(".env")
    if (env.get("APP_ENV") or os.getenv("APP_ENV", "")).lower() == "production":
        print("Refusing to reset the admin password in production.")
        return 1
    password = env.get("BOOKEY_ADMIN_DEV_PASSWORD")
    if not password:
        print("Set BOOKEY_ADMIN_DEV_PASSWORD in backend/.env first.")
        return 1

    from sqlalchemy import select
    from app.auth.service import hash_password
    from app.db.database import AsyncSessionLocal
    from app.db.models import User

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == "admin@bookey.dev"))).scalar_one_or_none()
        if user is None:
            print("No admin@bookey.dev user yet — start the server once to seed it.")
            return 1
        user.password_hash = hash_password(password)
        await db.commit()
    print("admin@bookey.dev password now matches BOOKEY_ADMIN_DEV_PASSWORD in .env")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
