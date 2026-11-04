"""
alembic/env.py — async-compatible Alembic environment.
Works with SQLite (dev) and PostgreSQL (production) unchanged.
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.db.models import Base
from app.config import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    """DATABASE_URL from settings (env / .env); `alembic -x url=...` overrides it."""
    return context.get_x_argument(as_dictionary=True).get("url") or get_settings().database_url


# Expression indexes (e.g. the PostgreSQL full-text GIN index ix_pages_fts) reflect with a
# normalised expression, so autogenerate would always report them as changed.
_EXPRESSION_INDEXES = {"ix_pages_fts"}


# Tables managed with raw SQL outside the ORM (PostgreSQL only): the pgvector store (0009).
_NON_ORM_TABLES = {"kb_vectors"}


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    if type_ == "table" and name in _NON_ORM_TABLES:
        return False
    return not (type_ == "index" and name in _EXPRESSION_INDEXES)


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=include_object,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    url = _url()
    connect_args = {}
    if get_settings().db_pgbouncer and not url.startswith("sqlite"):
        connect_args = {"statement_cache_size": 0, "prepared_statement_cache_size": 0}
    engine = create_async_engine(url, poolclass=NullPool, connect_args=connect_args)
    async with engine.begin() as conn:
        await conn.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
