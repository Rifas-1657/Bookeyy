"""
scripts/supabase_harden.py — close Supabase's auto-generated Data API over Bookey's tables.

Supabase exposes every table in the `public` schema through PostgREST to the `anon` and
`authenticated` roles (anyone holding the project's publishable/anon key). Bookey never
uses that API — the FastAPI backend connects as the table owner — so this script:

  * enables Row Level Security on every public table (no policies = deny for API roles;
    the owner role the backend uses is not subject to RLS),
  * revokes all table/sequence privileges from anon + authenticated, now and for tables
    created later by migrations (ALTER DEFAULT PRIVILEGES).

Idempotent; safe to re-run after every `alembic upgrade head`.
Usage:  DATABASE_URL=postgresql+asyncpg://... DB_PGBOUNCER=true python scripts/supabase_harden.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main() -> int:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    url = os.environ.get("DATABASE_URL", "")
    if "postgresql" not in url:
        print("Set DATABASE_URL to the Supabase PostgreSQL URL (postgresql+asyncpg://...).")
        return 1
    connect_args = {}
    if os.getenv("DB_PGBOUNCER", "").lower() in ("1", "true", "yes"):
        connect_args = {"statement_cache_size": 0, "prepared_statement_cache_size": 0}
    eng = create_async_engine(url, poolclass=NullPool, connect_args=connect_args)
    try:
        async with eng.begin() as conn:
            roles = {r[0] for r in (await conn.execute(text(
                "SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')"))).all()}
            tables = [r[0] for r in (await conn.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))).all()]
            for t in tables:
                await conn.execute(text(f'ALTER TABLE public."{t}" ENABLE ROW LEVEL SECURITY'))
            if roles:
                who = ", ".join(sorted(roles))
                await conn.execute(text(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {who}"))
                await conn.execute(text(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {who}"))
                await conn.execute(text(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {who}"))
                await conn.execute(text(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {who}"))
        print(f"RLS enabled on {len(tables)} public tables; API roles revoked: {sorted(roles) or 'none found'}")
    finally:
        await eng.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
