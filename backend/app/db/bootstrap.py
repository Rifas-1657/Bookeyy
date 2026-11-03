"""
app/db/bootstrap.py — schema preparation at startup.

SQLite (dev/tests, not alembic-managed): `Base.metadata.create_all` + small in-place
fixes for databases created by older code (create_all never alters existing tables).

PostgreSQL (and anything else): the schema is owned by Alembic.  Startup never runs
create_all; it checks that `alembic upgrade head` has been applied and refuses to start
(production) or warns (development) otherwise.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.db.models import Base

log = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]


class SchemaNotReady(RuntimeError):
    pass


def alembic_head() -> str | None:
    """Head revision of backend/alembic (None if alembic files are missing)."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        cfg = Config(str(BACKEND_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
        return ScriptDirectory.from_config(cfg).get_current_head()
    except Exception:  # pragma: no cover
        log.warning("could not read alembic head", exc_info=True)
        return None


async def _sqlite_dev_fixups(conn) -> None:
    async def cols(table: str) -> set[str]:
        return {r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}

    # users.token_version (migration 0006)
    users = await cols("users")
    if users and "token_version" not in users:
        await conn.execute(text("ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0"))
        log.info("Added users.token_version column (SQLite auto-migration)")
    # jobs became the durable queue (migration 0007). The old table was never written to,
    # so drop it and let create_all build the new shape.
    jobs = await cols("jobs")
    if jobs and "payload" not in jobs:
        n = (await conn.execute(text("SELECT COUNT(*) FROM jobs"))).scalar_one()
        if n == 0:
            await conn.execute(text("DROP TABLE jobs"))
            log.info("Recreating legacy empty `jobs` table as the durable job queue (SQLite auto-migration)")
        else:  # pragma: no cover - never seen in practice
            raise SchemaNotReady("SQLite `jobs` table has the pre-queue shape and is not empty; "
                                 "back it up and drop it, or run `alembic upgrade head`.")


async def prepare_database(engine: AsyncEngine, *, create: bool = True) -> None:
    """SQLite: create/patch tables (when `create`). Others: verify the alembic revision."""
    settings = get_settings()
    dialect = engine.dialect.name
    if dialect == "sqlite":
        if create:
            async with engine.begin() as conn:
                await _sqlite_dev_fixups(conn)
                await conn.run_sync(Base.metadata.create_all)
        return

    head = alembic_head()
    async with engine.connect() as conn:
        try:
            current = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()
        except Exception:
            current = None
    if current is None:
        raise SchemaNotReady(
            f"The {dialect} database has no schema. Run `alembic upgrade head` (from backend/) first.")
    if head and current != head:
        msg = f"Database schema is at {current}, code expects {head}: run `alembic upgrade head`."
        if settings.app_env == "production":
            raise SchemaNotReady(msg)
        log.warning(msg)
