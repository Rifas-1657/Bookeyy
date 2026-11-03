"""
app/worker.py — standalone job worker process.

    python -m app.worker                 # JOBS_CONCURRENCY jobs at once (default 2)
    python -m app.worker --concurrency 4

Run any number of these next to the API (set JOBS_INLINE_WORKER=false on the API then,
or keep both — claims are exclusive).  Needs the same DATABASE_URL / storage / LLM env
as the API.  The schema must exist: PostgreSQL → `alembic upgrade head` first.
SIGINT/SIGTERM: stop claiming, give running jobs a few seconds, then release them back
to the queue (they resume on the next worker).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from app.config import get_settings
from app.db.bootstrap import prepare_database
from app.db.database import engine
from app.jobs.worker import Worker

log = logging.getLogger("app.worker")


async def main(concurrency: int | None = None) -> None:
    await prepare_database(engine, create=False)
    worker = Worker(concurrency)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):  # Windows: KeyboardInterrupt instead
            pass
    task = worker.start()
    try:
        await asyncio.wait([task, asyncio.ensure_future(stop.wait())], return_when=asyncio.FIRST_COMPLETED)
    finally:
        await worker.stop()
        await engine.dispose()


def cli() -> None:
    p = argparse.ArgumentParser(description="Bookey durable job worker")
    p.add_argument("--concurrency", type=int, default=None, help="jobs at once (default JOBS_CONCURRENCY)")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    log.info("Bookey worker starting (%s, db=%s)", get_settings().app_env,
             get_settings().database_url.split("@")[-1].split("?")[0])
    try:
        asyncio.run(main(args.concurrency))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    cli()
