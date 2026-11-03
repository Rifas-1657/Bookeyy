"""
app/db/database.py — async SQLAlchemy engine and session factory.

SQLite (dev/tests) and PostgreSQL (production, via asyncpg) share the same models.
"""
from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings

settings = get_settings()


def is_sqlite_url(url: str) -> bool:
    return url.lower().startswith("sqlite")


def engine_kwargs(url: str) -> dict:
    """create_async_engine() options for a URL (shared by the app, worker and alembic)."""
    s = get_settings()
    if is_sqlite_url(url):
        return {"connect_args": {"check_same_thread": False}}
    kw: dict = {"pool_pre_ping": True}
    if s.app_env == "testing":
        # The test suite drives the app from more than one event loop (TestClient
        # lifespan thread + pytest-asyncio loop); asyncpg connections are loop-bound.
        kw["poolclass"] = NullPool
    else:
        kw.update(pool_size=s.db_pool_size, max_overflow=s.db_max_overflow, pool_recycle=1800)
    if s.db_pgbouncer:
        kw["connect_args"] = {"statement_cache_size": 0, "prepared_statement_cache_size": 0}
    return kw


engine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    **engine_kwargs(settings.database_url),
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields an async db session, closes on exit."""
    async with AsyncSessionLocal() as session:
        yield session
