# Bookey — Production Roadmap

Written 2026-10-27 after a full audit of this repository. It covers where Bookey
stands, what was fixed in this pass, and the ordered work to reach a real,
production-level product while spending as little money as possible.

---

## 1. Honest status

Bookey has two halves that were built separately and **are not connected**:

| Layer | State |
| --- | --- |
| **Frontend** (Next.js 16, R3F, Tailwind) | Polished UI, builds with 0 type errors. But every learner feature runs on seeded mock data (`lib/mock/*`). Only Python chapter 1 (6 pages) has real content. Narration is silent text timing. Code runner only "runs" pre-scripted examples. |
| **Backend** (FastAPI, ~14k lines) | Real modules exist: auth (argon2 + JWT + WS tickets), document ingestion (PDF/DOCX/URL), chunking + Chroma retrieval, LLM router (Gemini → Groq → OpenRouter), page generator with verification, tutor graph with scope guard, TTS/STT providers, bandit + FSRS personalization. |
| **Connection between them** | Mostly missing. The reader never calls the page API, the admin UI never calls the generator, login never created a session, and the WebSocket client never authenticated (fixed below). |

So the problem is not "lots of bugs everywhere" — it is that the product is two
demos side by side. Production readiness means **wiring the real backend into
the real UI, one workflow at a time**, and deleting the mock path as each one
goes live.

`iqrabook/` is an older prototype. It is excluded from the build. Mine it for
ideas (its streaming TTS turn handling is useful) and then delete it.

---

## 2. Fixed in this pass

1. **Languages restricted to English + Tanglish.** Pure Tamil was offered in the
   survey, tutor, profile, protocol types and backend contract. Removed
   everywhere; saved `tamil` preferences fall back to Tanglish instead of
   breaking.
   (`lib/stores/personalization.ts`, `lib/stores/profile.ts`,
   `lib/contracts/book-events.ts`, `components/survey/*`, `lib/tutor/demo-tutor.ts`,
   `backend/app/contracts/models.py`)
2. **Live WebSocket mode could never connect.** The gateway requires an auth
   ticket as the first frame; `createBookClient()` never supplied one. It now
   fetches a ticket from `POST /api/ws-ticket`. (`lib/book-client/index.ts`)
3. **Login never created a session.** With `NEXT_PUBLIC_BOOKEY_API_URL` set, the
   login form now calls `POST /auth/login`, stores the token (localStorage when
   "Remember me", else sessionStorage), and shows a real "incorrect password"
   error. Without a backend it keeps the demo behaviour.
   (`lib/api/auth.ts`, `components/auth/login-form.tsx`)
4. **Three copies of the token helper** (each reading only sessionStorage) merged
   into `lib/api/auth.ts`; all API calls now send `credentials: 'include'` so the
   backend's httpOnly cookies work cross-origin.
5. **Search was empty on a fresh install.** The FTS5 index was populated
   *before* the database was seeded, and re-inserted on every restart. It is
   now rebuilt after seeding. (`backend/app/main.py`)
6. **Critical security advisories.** Next.js 16.4.0 had two unauthenticated
   remote-code-execution advisories; upgraded to 16.4.3. `shadcn` (a CLI) moved
   to devDependencies, and DOMPurify pinned to a patched version via
   `pnpm-workspace.yaml` overrides. `pnpm audit --prod` now shows 0 critical,
   4 high, 1 moderate; clear those in Phase 1.

Verified after these changes: `tsc` 0 errors, `pnpm build` succeeds (40 routes),
backend `pytest` 351 passed.

---

## 3. Target architecture (free-first)

| Concern | Choice | Cost |
| --- | --- | --- |
| Frontend hosting | Vercel Hobby or Cloudflare Pages | Free |
| API + workers | Oracle Cloud Always Free ARM VM (generous CPU/RAM, no sleep), or Render/Fly free tier to start | Free |
| Database + vectors + files | Supabase free: Postgres + `pgvector` + Storage (code already supports `postgresql+asyncpg`, pgvector and S3-compatible storage) | Free to start |
| Job queue (5–10 min book builds) | Postgres-backed job table polled by a worker (no Redis needed); progress pushed to UI over the existing SSE `status-stream` | Free |
| LLM — book generation | Gemini Flash free tier (long context, cheap). Keep the router fallback to Groq/OpenRouter | Free tier |
| LLM — live tutor | Groq (very low latency) with Gemini fallback | Free tier |
| STT | Groq Whisper `large-v3-turbo` (free tier, handles code-mixed speech well). Browser VAD to only send speech | Free tier |
| TTS — English | Kokoro (open-source, high quality, runs on CPU) self-hosted on the worker | Free |
| TTS — Tanglish | **The one place worth paying.** See §5 | Paid |
| Code practice | Pyodide (Python in WebAssembly, in-browser) + sandboxed iframe for JS/HTML | Free |
| Offline | PWA service worker + IndexedDB | Free |
| Errors/monitoring | Sentry free tier, existing structlog JSON logs | Free |

Big cost lever: **narration audio is generated once at book-build time and
cached in storage**, so you pay TTS per page, not per learner per listen. Only
live tutor answers need real-time TTS.

---

## 4. Phased plan

Each phase ends with something a real user can use. Don't start the next phase
until the previous one works against the real backend.

### Phase 1 — Real foundation (≈2 weeks)
- Backend test suite green; CI (GitHub Actions free) runs `tsc`, `next build`, `pytest`.
- Decide signup policy: backend currently requires an **invite token** for
  signup. Open signup is needed for workflow B (users making their own books).
- Wire signup, logout, `/me` and route guards; admin routes check role server-side.
- Reader loads pages from `GET /api/courses/{id}/pages/{pageId}` instead of
  `lib/mock/book-pages.ts`. Delete mock pages once parity is reached.
- Move bookmarks, notes, progress, survey to the existing `/me/*` endpoints.
- Deploy: Supabase + one API VM + Vercel. Production config check in
  `main.py` already refuses SQLite/localhost CORS — keep it.

### Phase 2 — Workflow A: admin materials → master book (≈3 weeks)
- Admin knowledge-base page uploads to `/api/admin/documents` (already exists).
- Add **video ingestion**: audio extract (ffmpeg) → Groq Whisper transcript →
  same chunking path. YouTube links: fetch captions first, transcribe as fallback.
- Book builder job: outline from all chunks → chapter plan → page generation
  (`page_gen/pipeline.py`, already has verify + repair) → diagram specs →
  narration scripts with word-indexed cues → pre-rendered audio.
- Admin review screen: edit/regenerate/rollback a page (rollback API exists).

### Phase 3 — Survey → personalized book in 5–10 min (≈2 weeks)
- The survey (level, interests, style, pace, language) becomes the input to a
  **personalization job**, not just reader settings.
- Don't regenerate the whole book: keep the master book's structure and
  facts; rewrite only explanations, analogies (cricket/movies/animals/
  workflow examples from the survey), depth and language per page. Skip pages
  the learner already knows; add warm-up pages where they don't.
- Generate the first chapter first so the learner can start in ~1 minute while
  the rest builds in the background (meets the 5–10 min target without making
  them wait for all of it).
- Show live progress ("Writing chapter 3 of 8…") over SSE.

### Phase 4 — The live tutor experience (≈3 weeks)
- Real audio playback of pre-rendered narration with word-level sync driving
  highlights and **progressive diagram drawing** (DiagramSpec steps already
  exist; each step is tied to a narration word index).
- Barge-in conversation: learner speaks (VAD → Groq Whisper) → narration
  pauses → tutor answers with streamed LLM + sentence-by-sentence TTS → resumes.
  Target < 1.5 s to first audio.
- Practice tools in the book: Pyodide code lab, quizzes generated per page,
  flashcards on FSRS (backend has it), and subject widgets (e.g. 3Dmol.js for
  molecules, simple physics canvas sims, step-through algorithm visualizers).

### Phase 5 — Workflow B: users make their own books (≈2 weeks)
- Same pipeline as Phase 2, scoped to the user, with per-user quotas so free
  LLM limits aren't exhausted by one account.
- Input types: PDF, book, video/YouTube link, or just a syllabus (syllabus-only
  books are generated from the LLM's knowledge and clearly labelled as such).

### Phase 6 — Offline, "only inside Bookey" (≈1–2 weeks)
- PWA: service worker caches the app shell; a "Download for offline" action
  stores book JSON + narration audio in IndexedDB, encrypted with a per-user key
  fetched at login and cached for an offline window (e.g. 30 days).
- No export/print/download of raw content. Honest limit: no web app can make
  copying *impossible* (screenshots, devtools). This approach makes it
  impractical, which is what Kindle-style web readers also do.
- Live tutor needs internet; offline mode plays pre-rendered narration and
  practice tools (Pyodide works offline once cached).

### Phase 7 — The "wow" layer and polish
- 3D book opening scene upgrade (R3F already in place): page-turn physics,
  lighting tied to theme, chapter "worlds".
- Load/performance budgets, accessibility audit, error tracking, backups,
  rate limits, abuse protection on uploads.

---

## 5. Where spending money is genuinely worth it

**Tanglish voice is the only must-pay item.** Free TTS engines handle English
well but sound wrong on Tamil–English code-mixing. Options, best fit first:

1. **Sarvam AI (Bulbul)** — built for Indian languages and code-mixed speech;
   strongest fit for Tanglish. Has free starting credits.
2. **ElevenLabs (multilingual)** — very natural, good Tamil; the repo already
   has config for it. Higher cost per character.
3. **Gemini TTS** — usable fallback on free tier, less natural for Tanglish.

Recommendation: run a blind listening test of the same 5 Tanglish paragraphs
on each (the repo has `tools/tts-audition` for this), pick one, and because
narration is pre-rendered and cached, a small monthly plan covers a lot of
books. Check each provider's current pricing before committing.

Everything else in §3 runs on free tiers until you have real traffic.

---

## 6. Known risks

- **Free-tier rate limits** on Gemini/Groq will cap how many books can be built
  per day. Queue jobs and show honest ETAs rather than failing.
- **Generated content accuracy.** Keep the verifier step, cite source chunks on
  every page, and let admins correct pages.
- **Local Faster-Whisper `large-v3-turbo`** (current STT default) is too heavy
  for free hosting; switch production default to Groq Whisper.
