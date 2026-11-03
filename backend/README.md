# Bookey Backend B0

> **B0 milestone**: a working mock realtime server the existing frontend can connect to with no UI change.

---

## Quick start

```powershell
# 1. Create and activate a Python 3.11+ virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt
pip install "sqlalchemy[asyncio]" greenlet

# 3. Start the server (creates and seeds the SQLite DB automatically on first run)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 4. (Optional) Connect the frontend in WS mode
# In the frontend directory:
$env:NEXT_PUBLIC_BOOKEY_CLIENT="ws"
$env:NEXT_PUBLIC_BOOKEY_WS_URL="ws://localhost:8000/ws/book"
pnpm start -p 4100
```

---

## Architecture

```
backend/
├── app/
│   ├── main.py              FastAPI app — CORS, lifespan, WebSocket mount, REST router
│   ├── config.py            pydantic-settings Settings (DATABASE_URL, CORS_ORIGINS, timings)
│   ├── contracts/
│   │   ├── models.py        Pydantic v2 models for every server→client and client→server frame
│   │   └── validate.py      parse_client_event() — validates inbound frames, raises ParseError
│   ├── db/
│   │   ├── models.py        SQLAlchemy 2 async ORM (11 tables)
│   │   ├── database.py      Async engine + session factory + get_db() dependency
│   │   └── seed.py          Python from Zero course: 6+4 pages with Tanglish narration
│   ├── ws/
│   │   └── gateway.py       WebSocket handler — send queue, ping/pong, dispatch, auth stub
│   ├── services/
│   │   └── mock_book_service.py  Core mock: narration loop, questions, code runner, survey
│   └── api/
│       └── routes.py        GET /health, GET /api/courses, GET /api/courses/{id}/pages/{pageId}
├── tests/
│   ├── test_contracts.py    56 tests: round-trip, parity, invalid frame rejection
│   ├── test_api.py          REST endpoint tests
│   └── test_websocket.py    11 WS integration tests
├── alembic/                 Migrations (alembic upgrade head)
├── requirements.txt
├── pytest.ini
└── .env.example
```

---

## Protocol

The server implements **`BOOK_PROTOCOL_VERSION = 1`** from `lib/contracts/book-events.ts`.

### Server → client (15 event types)

| type | description |
|---|---|
| `session_started` | New narration session |
| `text_chunk` | One word of narration with word offset |
| `audio_chunk` | TTS audio (B0: not sent) |
| `cue` | Highlight or action anchored to a word |
| `diagram_spec` | Full diagram specification |
| `diagram_draw` | Draw one diagram step |
| `highlight` | Text highlight |
| `code_stream` | Streamed generated code (B0: not used) |
| `run_result` | Code execution result with optional trace |
| `question_received` | Echo of ask_question |
| `answer_chunk` | Streaming tutor answer |
| `scope_refused` | Out-of-scope question rejection |
| `sources` | Citation sources for an answer |
| `page_complete` | All words delivered |
| `error` | Recoverable or fatal error |

### Client → server (9 event types)

`start_page`, `pause`, `resume`, `seek`, `speed`, `ask_question`, `run_code`, `submit_survey`, `audio_in`

### Connection-level frames (not in protocol union)

| type | description |
|---|---|
| `ping` | Client heartbeat |
| `pong` | Server heartbeat reply with server timestamp |

---

## REST endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Liveness probe `{"status":"ok","version":"B0"}` |
| GET | `/api/courses` | List all seeded courses |
| GET | `/api/courses/{id}/pages/{pageId}` | Full page payload for the reader |

---

## Database schema

SQLite for development and tests (tables created on startup). PostgreSQL for production — set
`DATABASE_URL=postgresql+asyncpg://...` and run `alembic upgrade head`; startup never runs
`create_all` on PostgreSQL. `tests/test_migrations.py` checks the migrations match the models.

**Tables:** `users`, `seats`, `courses`, `chapters`, `pages`, `progress`, `bookmarks`, `notes`, `sessions`, `documents`, `chunk_metadata`, `generated_pages`, `media_assets`, `jobs`

### Seeded data

- **Python from Zero** — 9 chapters
  - Chapter 1 (Variables) — 6 pages, **unlocked**, full Tanglish narration + cues + code + diagram
  - Chapter 2 (Data Types) — 4 pages, **unlocked**, full content
  - Chapters 3-9 — **locked** (seed title/summary only, no pages; generates when tutor backend connects)

---

## Running tests

```powershell
.\.venv\Scripts\python -m pytest -q                 # temp SQLite database (default)
```

The same suite runs against PostgreSQL when `TEST_DATABASE_URL` is set (the test database
is wiped and rebuilt with `alembic upgrade head`; its name must contain `test`):

```powershell
.\.venv\Scripts\python scripts\dev_postgres.py start
$env:TEST_DATABASE_URL = (.\.venv\Scripts\python scripts\dev_postgres.py url bookey_test)
.\.venv\Scripts\python -m pytest -q
Remove-Item Env:TEST_DATABASE_URL
```

---

## Local PostgreSQL (`scripts/dev_postgres.py`)

`pip install -r requirements-dev.txt` installs `pgserver` — PostgreSQL 16 + pgvector as a
wheel (no Docker, no admin rights, no password; data in `backend/.cache/pgdata-dev`).

| Command | What it does |
|---|---|
| `python scripts/dev_postgres.py start` | start the server (keeps running), create `bookey_dev` + `bookey_test` |
| `python scripts/dev_postgres.py url [db]` | print `postgresql+asyncpg://postgres@127.0.0.1:<port>/<db>` (port is chosen at start) |
| `python scripts/dev_postgres.py status` | running? pid / port / databases |
| `python scripts/dev_postgres.py reset-test-db` | drop + recreate `bookey_test` |
| `python scripts/dev_postgres.py stop` | stop the server |

Run the app on it:

```powershell
$env:DATABASE_URL = (.\.venv\Scripts\python scripts\dev_postgres.py url)
.\.venv\Scripts\python -m alembic upgrade head      # PostgreSQL schema = Alembic (no create_all)
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000
```

## Background jobs

Course / book builds run in a durable DB-backed queue (`jobs` table, `app/jobs/`): the API
enqueues, a worker claims with a lease, heartbeats, retries with exponential backoff, and a
build resumes after a crash or restart. The worker runs inside the API process by default
(`JOBS_INLINE_WORKER=true`, `JOBS_CONCURRENCY=2`); extra workers: `python -m app.worker`.

---

## Environment variables

See `.env.example` for all supported variables.

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./bookey_dev.db` | Async SQLAlchemy URL (`postgresql+asyncpg://…` → run `alembic upgrade head`) |
| `JOBS_INLINE_WORKER` | `true` | Run the job worker inside the API process |
| `JOBS_CONCURRENCY` | `2` | Jobs per worker at once |
| `CORS_ORIGINS` | `["http://localhost:3000","http://localhost:4100"]` | Comma-separated allowed origins |
| `APP_ENV` | `development` | `development` / `production` |
| `MOCK_TOKEN_MS` | `85` | Delay between narration words (ms) |
| `MOCK_THINKING_MS` | `600` | Tutor "thinking" pause (ms) |
| `MOCK_ANSWER_TOKEN_MS` | `45` | Delay between answer chunks (ms) |

---

## Authentication (B0 stub)

B0 accepts all WebSocket connections without credentials. The `AuthStub` class in `app/ws/gateway.py` always returns `{"user_id": null, "anonymous": true}`.

**B1 plan:** Add a Route Handler in Next.js that issues a short-lived signed ticket. The WebSocket client sends the ticket in the first frame or as a query parameter. `AuthStub` is replaced by a ticket-validation service.

---

## B1 roadmap

- [ ] Gemini/Groq LLM for real question answering (replace scripted answers)
- [ ] Whisper STT for `audio_in` frames
- [ ] TTS audio generation → `audio_chunk` frames
- [ ] Pyodide runner → real code execution
- [ ] JWT/ticket authentication
- [ ] PostgreSQL migration + Alembic
- [ ] Persistent progress, bookmarks, notes per user
