"""
scripts/dev_postgres.py — a local PostgreSQL 16 (+ pgvector) for development and tests,
using the `pgserver` wheel (requirements-dev.txt). No Docker, no admin rights, no password.

    python scripts/dev_postgres.py start            start the server, create bookey_dev + bookey_test
    python scripts/dev_postgres.py stop             stop the server
    python scripts/dev_postgres.py status           running? pid / port / databases
    python scripts/dev_postgres.py url [db]         print the app URL (postgresql+asyncpg://…/bookey_dev)
    python scripts/dev_postgres.py reset-test-db    drop + recreate bookey_test (empty)
    python scripts/dev_postgres.py psql "SQL"       run SQL against the `postgres` database

Data lives in backend/.cache/pgdata-dev (override with BOOKEY_PGDATA). The port is picked
when the server starts, so always read the URL from `url` instead of hard-coding it:

    $env:DATABASE_URL = (python scripts/dev_postgres.py url)            # PowerShell
    export DATABASE_URL="$(python scripts/dev_postgres.py url)"         # bash
    alembic upgrade head
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
PGDATA = Path(os.getenv("BOOKEY_PGDATA") or (BACKEND / ".cache" / "pgdata-dev"))
DATABASES = ("bookey_dev", "bookey_test")


def _pgserver():
    try:
        import pgserver  # noqa: F401
    except ImportError:
        sys.exit("pgserver is not installed: pip install -r requirements-dev.txt")
    return pgserver


def _info():
    from pgserver.utils import PostmasterInfo
    info = PostmasterInfo.read_from_pgdata(PGDATA)
    return info if info is not None and info.is_running() else None


def _server():
    """Attach to (or start) the server. cleanup_mode=None: it keeps running after we exit."""
    PGDATA.parent.mkdir(parents=True, exist_ok=True)
    return _pgserver().get_server(str(PGDATA), cleanup_mode=None)


def _psql(srv, sql: str) -> str:
    return srv.psql(sql)


def _existing_dbs(srv) -> set[str]:
    out = _psql(srv, "SELECT datname FROM pg_database;")
    return {line.strip() for line in out.splitlines() if line.strip()}


def _ensure_db(srv, name: str) -> None:
    if name not in _existing_dbs(srv):
        _psql(srv, f'CREATE DATABASE "{name}";')
    # pgvector is bundled with pgserver; enabling it is harmless when unused.
    try:
        _db_psql(srv, name, "CREATE EXTENSION IF NOT EXISTS vector;")
    except Exception:
        pass


def _db_psql(srv, db: str, sql: str) -> str:
    import subprocess
    from pgserver._commands import POSTGRES_BIN_PATH
    return subprocess.check_output([str(POSTGRES_BIN_PATH / "psql"), srv.get_uri(db)],
                                   input=sql.encode()).decode("utf-8")


def app_url(db: str = "bookey_dev") -> str:
    info = _info()
    if info is None:
        raise SystemExit("dev Postgres is not running: python scripts/dev_postgres.py start")
    return f"postgresql+asyncpg://postgres@127.0.0.1:{info.port}/{db}"


def cmd_start() -> None:
    srv = _server()
    for db in DATABASES:
        _ensure_db(srv, db)
    info = _info()
    print(f"dev Postgres running: pid={info.pid} port={info.port} data={PGDATA}")
    for db in DATABASES:
        print(f"  {db}: {app_url(db)}")


def cmd_stop() -> None:
    if _info() is None:
        print("dev Postgres is not running")
        return
    from pgserver._commands import pg_ctl
    pg_ctl(["-w", "stop", "-m", "fast"], pgdata=PGDATA)
    print("dev Postgres stopped")


def cmd_status() -> None:
    info = _info()
    if info is None:
        print(f"dev Postgres: stopped (data={PGDATA})")
        sys.exit(1)
    srv = _server()
    dbs = sorted(d for d in _existing_dbs(srv) if d.startswith("bookey"))
    print(f"dev Postgres: running pid={info.pid} port={info.port} data={PGDATA}")
    print(f"  databases: {', '.join(dbs) or '(none — run start)'}")


def cmd_url(db: str = "bookey_dev") -> None:
    print(app_url(db))


def cmd_reset_test_db(db: str = "bookey_test") -> None:
    if not db.startswith("bookey_test"):
        sys.exit("refusing to reset a non-test database")
    srv = _server()
    _psql(srv, f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE);')
    _psql(srv, f'CREATE DATABASE "{db}";')
    _ensure_db(srv, db)
    print(f"reset {db}: {app_url(db)}")


def main(argv: list[str]) -> None:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return
    cmd, rest = argv[0], argv[1:]
    if cmd == "start":
        cmd_start()
    elif cmd == "stop":
        cmd_stop()
    elif cmd == "status":
        cmd_status()
    elif cmd == "url":
        cmd_url(*rest[:1])
    elif cmd == "reset-test-db":
        cmd_reset_test_db(*rest[:1])
    elif cmd == "psql":
        print(_psql(_server(), " ".join(rest)))
    else:
        sys.exit(f"unknown command {cmd!r}\n{__doc__}")


if __name__ == "__main__":
    main(sys.argv[1:])
