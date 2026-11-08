"""Alembic migrations build exactly the schema of app.db.models (SQLite and PostgreSQL).

SQLite: `alembic upgrade head` on a fresh file, then compare_metadata.
PostgreSQL (TEST_DATABASE_URL): conftest already built the test database with
`alembic upgrade head` (the app never runs create_all there) — compare that.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.db.bootstrap import alembic_head
from app.db.models import Base

BACKEND = Path(__file__).resolve().parent.parent


def _include(obj, name, type_, reflected, compare_to) -> bool:
    if type_ == "table" and name == "kb_vectors":   # pgvector store, raw SQL (0009)
        return False
    return not (type_ == "index" and name == "ix_pages_fts")   # expression index (see alembic/env.py)


async def _diff(url: str) -> list:
    eng = create_async_engine(url, poolclass=NullPool)
    try:
        async with eng.connect() as conn:
            def run(sync_conn):
                mc = MigrationContext.configure(sync_conn, opts={"compare_type": True, "include_object": _include})
                return compare_metadata(mc, Base.metadata)
            diff = await conn.run_sync(run)
            version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
            extra = {}
            if eng.dialect.name == "postgresql":
                extra["fts"] = (await conn.execute(text(
                    "SELECT 1 FROM pg_indexes WHERE indexname = 'ix_pages_fts'"))).scalar_one_or_none()
    finally:
        await eng.dispose()
    return diff, version, extra


def _upgrade(url: str) -> None:
    subprocess.run([sys.executable, "-m", "alembic", "-x", f"url={url}", "upgrade", "head"],
                   cwd=str(BACKEND), check=True, capture_output=True)


async def test_sqlite_migrations_match_models():
    d = tempfile.mkdtemp(prefix="bookey_migr_")
    url = f"sqlite+aiosqlite:///{Path(d, 'm.db').as_posix()}"
    _upgrade(url)
    diff, version, _ = await _diff(url)
    assert diff == [], diff
    assert version == alembic_head() == "0010_user_email_verified"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL only (TEST_DATABASE_URL)")
async def test_postgres_migrations_match_models():
    diff, version, extra = await _diff(os.environ["TEST_DATABASE_URL"])
    assert diff == [], diff
    assert version == alembic_head()
    assert extra["fts"] == 1


def test_migration_chain_is_linear():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
    revs = [r.revision for r in script.walk_revisions("base", "heads")]
    assert revs[-1] == "0001_baseline" and revs[0] == "0010_user_email_verified" and len(revs) == 10
