# Bookey Production Deploy Guide

## Live stack (P3): Vercel + Render free + Supabase free

| Concern | Choice | Notes |
|---|---|---|
| Frontend | Vercel Hobby | `NEXT_PUBLIC_BOOKEY_SAME_ORIGIN=1`: REST goes through Next rewrites to `BOOKEY_API_ORIGIN` (first-party cookies); WebSockets go direct to `NEXT_PUBLIC_BOOKEY_WS_URL` |
| API + job worker | Render free web service, region singapore, Docker (`backend/Dockerfile`, `requirements-prod.txt`) | 512 MB RAM, no GPU, sleeps when idle, ephemeral disk. One uvicorn worker with the inline job worker (`JOBS_CONCURRENCY=1`). The container runs `alembic upgrade head` before starting |
| Database | Supabase Postgres via the transaction pooler (:6543) | `DB_PGBOUNCER=true`, URL `postgresql+asyncpg://...?ssl=require` |
| Vectors | pgvector, table `kb_vectors` (migration 0009) | `VECTOR_STORE=pgvector`; untyped `vector` column + partial HNSW index per dimension |
| Embeddings | fastembed, `BAAI/bge-small-en-v1.5` (384-dim, ONNX, baked into the image) | Chosen because torch does not fit in 512 MB, the Gemini key is invalid and the HF token has no inference credits. Measured about 125 MB RSS after import, 313 MB with the model loaded. English-focused; `FASTEMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` is multilingual but about 220 MB, so it needs a larger host. Changing model means re-indexing |
| Files | Supabase Storage, private bucket `bookey`, REST backend | `STORAGE_BACKEND=supabase`; 1 GB total, 50 MB per object, hence `BOOKS_MAX_MEDIA_MB=45` |
| Voice | STT Groq Whisper (`STT_PROVIDER=groq`), TTS Edge neural voices, male + female | `TTS_PROVIDER=local` with every `TTS_*_PROVIDER=edge`. Video/audio books transcribe through Groq in 10-minute chunks when faster-whisper is absent. Local engines stay optional for the laptop (`requirements.txt`) |
| Narration | Pre-rendered audio stays the target (`scripts/prerender_audio.py`, zero TTS cost per listen) | `NARRATION_TTS=false` in production, same as the laptop: the server paces text and the reader plays prebuilt audio. Learner-created books have no narration audio until that is rendered to Storage |

### Owner one-offs

```bash
cd backend
# PowerShell: $env:DATABASE_URL = '...'; $env:DB_PGBOUNCER = 'true'
export DATABASE_URL='postgresql+asyncpg://<user>:<password>@<pooler-host>:6543/postgres?ssl=require' DB_PGBOUNCER=true
.venv/Scripts/python scripts/supabase_harden.py                          # optional: revoke anon/authenticated grants
.venv/Scripts/python scripts/create_admin.py --email you@example.com     # prompts for a password
```

This Supabase project has an `ensure_rls` event trigger, so every `public` table already has RLS on
with no policies: the anon and authenticated roles read no rows through the Data API, and the backend
(table owner) is unaffected. Those roles do still hold table grants, so `supabase_harden.py` is optional
defence in depth that revokes them. Check `pg_tables.rowsecurity` if the project is ever recreated. Invite learners from the admin Members page
(`POST /api/admin/members/invite`).

The sections below are the general reference.

## Overview

Bookey requires three durable backing services — relational DB, vector store, and object storage.
A free host's local filesystem is **ephemeral** (erased on restart, redeploy, and spin-down).
SQLite and a local ChromaDB directory are development-only; never use them in production.

---

## Service Map

| Service | Dev | Production |
|---|---|---|
| Relational DB + pgvector | SQLite (aiosqlite) | Managed PostgreSQL >= 14 with pgvector extension |
| Vector store | ChromaDB (local disk) | pgvector table in same PostgreSQL instance |
| Object storage (uploads, audio) | Local disk `/uploads` | S3-compatible (Supabase Storage, Cloudflare R2, AWS S3) |
| Backend | uvicorn process | Docker container, single worker (WebSockets need sticky sessions) |
| Background jobs (course/book builds) | Inline worker in the API process | Durable `jobs` table in PostgreSQL; inline worker and/or `python -m app.worker` processes |
| Frontend | Next.js dev server | Vercel / Railway / Render |
| Code runner (B3) | In-process sandbox | Isolated container (no network, no filesystem write) |

---

## Recommended Free-Tier Stack

### Supabase (PostgreSQL + pgvector + Storage)
- Database: 500 MB on free tier, 8 GB on Pro ($25/mo)
- pgvector: built-in extension, enable with `create extension vector;`
- Storage: 1 GB on free tier, 100 GB on Pro
- Pauses after 1 week inactivity on free tier — use Pro or set a daily query cron

### Railway (Backend container)
- Free tier: 512 MB RAM, 1 vCPU, 5 GB disk (ephemeral — use Supabase for DB/storage)
- 500 hours/month on free tier (~20 days/month); upgrade to Hobby ($5/mo) for always-on
- WebSockets supported; sticky sessions not required with single worker
- Cold start: ~3-5 s on first request after idle

### Vercel (Frontend)
- Free tier: 100 GB bandwidth
- Analytics auto-injected on Vercel — remove manual Analytics if using a custom provider

---

## Environment Variables

Copy `backend/.env.example` to `backend/.env`. Never commit `.env` to git.

| Variable | Required | Description |
|---|---|---|
| DATABASE_URL | YES | `postgresql+asyncpg://user:pass@host:5432/db` (asyncpg driver; `?ssl=require` for managed hosts) |
| DB_PGBOUNCER | no | `true` when DATABASE_URL goes through PgBouncer in transaction mode (Supabase pooler, port 6543) — disables asyncpg's prepared-statement cache |
| DB_POOL_SIZE / DB_MAX_OVERFLOW | no | SQLAlchemy pool per process (default 5 / 10) |
| JOBS_INLINE_WORKER | no | `true` (default): the API process also runs the job worker. Set `false` on the API when you run separate `python -m app.worker` processes |
| JOBS_CONCURRENCY | no | Jobs one worker runs at once (default 2) |
| JOBS_LEASE_SECONDS | no | Lease a worker holds on a job, renewed every lease/3 (default 60). A dead worker's job is requeued after this |
| JOBS_MAX_ATTEMPTS | no | Attempts before a job ends `failed` (default 3; crash loops included) |
| JOBS_BACKOFF_BASE_SECONDS / JOBS_BACKOFF_MAX_SECONDS | no | Retry delay `base * 2^(attempt-1)`, capped (default 10 / 600) |
| JOBS_POLL_INTERVAL | no | Idle poll of the queue in seconds (default 1) |
| SECRET_KEY | YES | 64-char random hex for JWT signing |
| GEMINI_API_KEY | YES | Google Gemini API key (primary LLM) |
| GROQ_API_KEY | no | Groq fallback LLM |
| NVIDIA_NIM_API_KEY | no | NVIDIA NIM fallback LLM |
| OPENROUTER_API_KEY | no | OpenRouter fallback LLM |
| GROQ_WHISPER_MODEL | no | Groq STT model (default: whisper-large-v3) |
| S3_ENDPOINT_URL | prod | S3-compatible endpoint URL |
| S3_ACCESS_KEY | prod | S3 access key |
| S3_SECRET_KEY | prod | S3 secret key |
| S3_BUCKET_UPLOADS | prod | Bucket for uploaded documents |
| S3_BUCKET_AUDIO | prod | Bucket for TTS audio cache |
| CORS_ORIGINS | prod | Comma-separated allowed origins |
| SENTRY_DSN | no | Enable Sentry error tracking |
| LANGFUSE_PUBLIC_KEY | no | Enable Langfuse LLM tracing |
| LANGFUSE_SECRET_KEY | no | Langfuse secret key |
| LOG_LEVEL | no | INFO (default) or DEBUG |
| APP_ENV | no | development (default) or production |
| GEMINI_MODEL | no | Default: gemini-1.5-pro (read from config, never hardcoded) |
| GROQ_MODEL | no | Default: llama-3.3-70b-versatile |

---

## Cold Start and WebSocket Idle Limits

### Cold Start
- Railway free tier spins down after ~15 min of inactivity. Cold start: 3-5 s.
- Mitigation: UptimeRobot free monitor (5-min pings to /health) keeps it warm.
- Frontend shows "Connecting..." during cold start and retries WS automatically.

### WebSocket Idle Limits
- Railway: no hard idle limit per WS connection (TCP keepalive)
- Cloudflare: 100 s idle timeout on proxied WS. Use Cloudflare Argo or direct TCP.
- Client heartbeat: send a ping frame every 30 s from frontend; backend echoes pong.
- Resume: on reconnect, client resends start_page with resumeFrom: lastSeenSeq so narration
  continues without duplicating cues.

---

## Deployment Steps

### 1. Provision PostgreSQL (Supabase or any managed PostgreSQL >= 14)
```sql
-- In Supabase SQL editor (optional until the pgvector store is switched on):
create extension if not exists vector;
```
Set DATABASE_URL in your environment, with the asyncpg driver:

- Direct connection (port 5432, also used for migrations):
  `postgresql+asyncpg://postgres:<password>@db.<project>.supabase.co:5432/postgres?ssl=require`
- Transaction pooler (port 6543, many short connections): same form, port 6543, **and** `DB_PGBOUNCER=true`.
  Run `alembic upgrade head` over the direct connection.

### 1b. Create / upgrade the schema with Alembic
The app never runs `create_all` on PostgreSQL — the schema is owned by Alembic, and startup refuses
to run in production when the database is not at the latest revision.
```bash
cd backend
DATABASE_URL=postgresql+asyncpg://... alembic upgrade head      # or: alembic -x url=postgresql+asyncpg://... upgrade head
```
Run this before every deploy (e.g. as the platform's release / pre-deploy command:
`alembic upgrade head`). Migration chain: `0001_baseline` → … → `0009_pgvector_store`.

### 2. Build and push Docker image
```bash
cd backend
docker build --target runtime -t ghcr.io/your-org/bookey-backend:latest .
docker push ghcr.io/your-org/bookey-backend:latest
```

### 3. Deploy to Railway
- Create a new service from Docker image
- Set all env vars from the table above
- Set PORT=8000
- Pre-deploy command: `alembic upgrade head`
- Enable health check: /health

### 3b. Background job worker
Course / book builds run in a durable, PostgreSQL-backed queue (`jobs` table, `app/jobs/`).
A build survives restarts and redeploys: when a worker dies its lease expires
(`JOBS_LEASE_SECONDS`) and another worker resumes the build — indexed sources, the stored
outline and finished lessons are kept, the book is re-published. A build that keeps failing
ends `failed` after `JOBS_MAX_ATTEMPTS`; learners and admins can retry it
(`POST /api/books/jobs/{id}/retry`, `POST /api/admin/course-builder/jobs/{id}/retry`).

- Small deploy (one VM / one container): keep `JOBS_INLINE_WORKER=true` — the API runs the worker.
- Scale out: set `JOBS_INLINE_WORKER=false` on the API and run one or more workers from the same
  image and env:
  ```bash
  python -m app.worker --concurrency 2
  ```
  Claims are exclusive (`SELECT … FOR UPDATE SKIP LOCKED`), so any number of workers (and inline
  workers) can share the queue. On SIGTERM a worker stops claiming, gives running jobs a few
  seconds, then hands them back to the queue.

### 4. Deploy frontend to Vercel
```bash
vercel deploy --prod
```
Set NEXT_PUBLIC_BOOKEY_API_URL and NEXT_PUBLIC_BOOKEY_CLIENT=ws.

### 5. First deploy
Run `alembic upgrade head` against the empty database (step 1b), then start the API; it seeds the
built-in course on first boot. SQLite dev databases are still created with `create_all` on
startup — that path is development-only.

---

## Local PostgreSQL for development and tests

No Docker needed: `pgserver` (PostgreSQL 16 + pgvector in a pip wheel, `backend/requirements-dev.txt`).
```bash
cd backend
pip install -r requirements-dev.txt
python scripts/dev_postgres.py start            # data in backend/.cache/pgdata-dev; creates bookey_dev + bookey_test
python scripts/dev_postgres.py url              # postgresql+asyncpg://postgres@127.0.0.1:<port>/bookey_dev

# app on Postgres (PowerShell: $env:DATABASE_URL = (python scripts/dev_postgres.py url))
export DATABASE_URL="$(python scripts/dev_postgres.py url)"
alembic upgrade head
uvicorn app.main:app --port 8000

# test suite on Postgres (default without TEST_DATABASE_URL: a temp SQLite file)
TEST_DATABASE_URL="$(python scripts/dev_postgres.py url bookey_test)" python -m pytest -q

python scripts/dev_postgres.py status | stop | reset-test-db
```

---

## DB Backup Script

```bash
#!/bin/bash
# Run daily via cron or Railway cron job
set -e
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
pg_dump "$DATABASE_URL" --no-acl --no-owner \
  | gzip \
  | aws s3 cp - "s3://$BACKUP_BUCKET/bookey-db-${TIMESTAMP}.sql.gz"
echo "Backup complete: bookey-db-${TIMESTAMP}.sql.gz"
```

---

## Security Checklist

- [ ] SECRET_KEY is 64 random hex chars
- [ ] CORS_ORIGINS is set to exact frontend domain (no wildcard in production)
- [ ] APP_ENV=production disables OpenAPI docs
- [ ] Upload quarantine: files virus-scanned before indexing
- [ ] pip-audit passes with no known CVEs: `pip-audit -r requirements.txt`
- [ ] No secrets in git history: `git log --all --full-diff -p -- .env`
- [ ] Rate limits enforced: 60 req/min per IP, 10 questions/min per user
- [ ] Non-root Docker user (bookey:bookey in Dockerfile)
