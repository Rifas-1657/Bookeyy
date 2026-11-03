# Bookey — Production Plan (Voice-first, free-first)

Written 2026-11-02. Supersedes the phase list in `PRODUCTION_ROADMAP.md` (keep that file for history).
Goal: anyone submits a PDF, PPT, DOCX, YouTube link, or just a prompt → Bookey builds an
interactive narrated book with a Tanglish/English voice tutor, live diagrams, runnable practicals,
Q&A, and an offline copy. Fast on cheap phones, free to run until real traffic.

---

## 0. Where we are today (audited)

| Area | State | Gap for production |
| --- | --- | --- |
| Reader (3D book, streaming text, diagrams, Pyodide code, Q&A, recap, offline) | Works in browser, live + demo mode | Only the seeded Python pages have hand-made content; reader must load generated books |
| Voice — TTS | Local Pocket-TTS (female, Tamil script), Kokoro (EN), Edge (free online, male/female); Tanglish→Tamil-script transliteration with LLM + lexicon | Pronunciation of rare English words is approximate; no single voice that is great at *both* Tamil and English; no chosen house voice |
| Voice — STT | faster-whisper large-v3-turbo on the 3050 (≈1.2 s/clip), output romanized | Not tuned for Tanglish; no test set to measure it |
| LLM | Groq gpt-oss-120b → OpenRouter free → (Gemini, key invalid) → mock | Groq free tier rate-limits big jobs (outline took 2 min via fallback) |
| Ingestion | PDF, DOCX, text, URL, YouTube (mcp-video-analyzer/yt-dlp), GitHub, Reddit, arXiv — **admin only** | No PPT/PPTX; learners can't submit; no prompt-only books |
| Book generation | Outline + page generator with verifier/repair | `page_gen` and tutor scope guard are **Python-only**; generated books don't flow into the reader yet |
| Jobs | In-process asyncio tasks | Lost on restart; one process only |
| Data | SQLite + Chroma + local files | Needs Postgres + pgvector + object storage |
| Auth | JWT, invite-only signup | Needs open signup + per-user quotas |

---

## 1. Voice — research findings

### TTS candidates (Tamil / Tanglish)

| Model | License | Why it matters | Runs on 3050 (4 GB)? |
| --- | --- | --- | --- |
| **AI4Bharat IndicF5** | MIT | Best open Tamil quality; **zero-shot voice cloning** from a short reference clip → we choose the house voice (male or female) | Inference yes (fp16, slow-ish); fine-tune needs 16 GB GPU |
| **AI4Bharat Indic Parler-TTS** | Apache-2.0 (HF access request) | Text prompt controls tone ("warm, patient teacher, moderate pace") — great for teaching | Tight; better on cloud GPU for pre-render |
| Pocket-TTS Tamil "Geetha" (current) | MIT | Fast on CPU, already integrated | Yes (CPU) |
| Kokoro-82M (current, English) | Apache-2.0 | Excellent English, tiny | Yes (CPU) |
| Edge neural voices (current fallback) | Free service, not open | Valluvar/Pallavi (Tamil), Prabhat/Neerja (EN); real word timings | Online only |
| Sarvam Bulbul v3 | Paid API (~₹30 / 10k chars, signup credits) | Built for Indian code-mix; quality reference | API |

No public **Tanglish** speech corpus or Tanglish TTS checkpoint exists — we have to create the data.
Closest recipe: a community F5-TTS fine-tune on Hindi-English code-mixed speech (OpenSLR-104).

### STT candidates

| Model | License | Notes |
| --- | --- | --- |
| Whisper large-v3-turbo (current, local) | MIT | Good English, OK Tamil, weak on Tanglish romanization |
| **vasista22/whisper-tamil-large-v2** (IIT Madras Speech Lab) | Apache-2.0 | 7.5 WER FLEURS / 6.6 Common Voice Tamil (self-reported) — strong Tamil base for fine-tuning |
| AI4Bharat IndicConformer | MIT | 22 Indian languages, fast; not code-mix tuned |
| Groq Whisper large-v3 (API) | Free tier | Fastest for live questions; fallback |
| Sarvam Saaras v3 (API) | Paid (~₹30/hr) | Has a code-mix mode; reference/benchmark only |

A 2026 paper shows **LoRA/QLoRA Whisper fine-tuning works for Tamil-English code-mixed ASR** — that is our STT path.

### Data we can legally build on
- **IndicVoices** (AI4Bharat, CC-BY-4.0, gated): 16k+ hours, 76% extempore — natural speech that already contains English words.
- **IndicVoices-R** (CC-BY-4.0): TTS-grade subset, **Tamil = 99.5 h, 1,084 speakers**.
- **Our own Tanglish set** (the missing piece): ~3,000 teaching sentences (LLM-written, reviewed) recorded by one consenting voice actor (≈4–6 h). This single set trains the TTS voice *and* tests the STT.

### Compute
- **Laptop (Ryzen 5 5600H, 16 GB RAM, RTX 3050 4 GB, 64 GB free):** fine for inference and Whisper-small LoRA experiments; **not** for TTS fine-tuning.
- **Free GPUs for training:** Kaggle (T4/P100, ~30 h/week, 12 h sessions), Colab free (T4 16 GB, not guaranteed), Lightning AI free credits, Modal (~$30/month free credits, serverless — also good for production GPU jobs).

---

## 2. Voice plan

**V1 — Measure first (1–2 days)**
1. Build `tools/voice-eval/`: 150 Tanglish + 50 English teaching sentences (tech-heavy), a recorded test set of 100 Tanglish questions (your voice + 1–2 friends).
2. Extend the existing `tools/tts-audition` page: blind A/B of IndicF5 (2 reference voices), Indic Parler (2 descriptions), Pocket-TTS, Edge Valluvar/Pallavi, Kokoro (EN). You rate naturalness, Tanglish pronunciation, tech words.
3. STT benchmark on the recorded set: Whisper-turbo, whisper-tamil-large-v2, IndicConformer, Groq — WER on Tanglish (Latin-script reference), latency on the 3050.

**V2 — Best zero-training stack (2–3 days)**: integrate the winners behind the existing provider router
(already supports per-language chains + caching). Expected: IndicF5 with a chosen reference voice for Tanglish, Kokoro or IndicF5 for English, Edge as online fallback; Whisper-tamil or turbo + Groq fallback for STT.

**V3 — Fine-tune (1–2 weeks, mostly data work)**
- *Data*: generate 3,000 sentences → you review → record (studio-quiet room, phone mic is fine) → auto-align, trim, normalise (script provided).
- *TTS*: fine-tune IndicF5 on Kaggle T4 using our Tanglish set + IndicVoices-R Tamil (text side in Tamil script — matches your finding that Tamil script pronounces correctly). Keep display text in Latin via the existing word alignment.
- *STT*: LoRA fine-tune whisper-tamil-large-v2 (or turbo) with **Latin-script Tanglish targets** so it outputs exactly what the learner sees.
- *Gate*: ship only if it beats V2 on the blind test and WER.

---

## 3. Product build plan

**P1 — Generic books from any source (≈1 week)**
- Learner "Create a book" flow: upload PDF / PPTX / DOCX / TXT, paste YouTube or web link, or type a prompt ("Teach me SQL joins for beginners, in Tanglish").
- Add PPTX parser (`python-pptx`), prompt-only books (LLM syllabus → outline, labelled "generated from AI knowledge").
- Remove Python-only limits in `page_gen` and the tutor scope guard → scope = the book's own sources/topic.
- Generated pages flow into the reader through `/api/courses/{id}/pages` (replace the mock page store).

**P2 — Fast pipeline (≈1 week)**
- Durable job queue in Postgres (`jobs` table + worker process), resumable after restart, progress over SSE.
- Chapter 1 ready in ~60 s, rest in background; narration audio pre-rendered per sentence at build time and cached by hash (already supported) → instant playback, zero TTS cost per listen.
- LLM routing per task: Groq for tutor (low latency), Gemini Flash (free tier, long context) for outlines/pages, OpenRouter free as fallback; per-user quotas.

**P3 — Production infrastructure (free tiers)**
| Concern | Choice |
| --- | --- |
| Frontend | Vercel or Cloudflare Pages |
| API + workers | Oracle Cloud Always-Free ARM VM (4 vCPU / 24 GB) — CPU TTS, ingestion, API |
| GPU jobs (TTS pre-render, STT fallback) | Modal serverless (free credits), laptop GPU during development |
| DB / vectors / files | Supabase: Postgres + pgvector + Storage (code already supports asyncpg + S3) |
| CDN for audio | Supabase Storage / Cloudflare R2 |
| Monitoring | Sentry free + existing structlog |

**P4 — UI/UX + performance**
- Gold "lux" theme everywhere (started), mobile-first reader, 375 px checks, reduced-motion.
- Budgets: LCP < 2.5 s on 4G, JS < 250 KB on first load (lazy-load Monaco, 3D, Pyodide), narration start < 1 s, tutor first audio < 2.5 s.
- Load test with `backend/load_test.py` (target: 200 concurrent readers on the free VM), rate limits, upload size limits, abuse checks.

**P5 — Offline + polish**: encrypted offline books, lesson recap screenshots (built), progress sync, accessibility audit.

---

## 4. What I need from you

| Item | Why | You do |
| --- | --- | --- |
| Hugging Face account + **read token**, and accept access to `ai4bharat/indic-parler-tts` and `ai4bharat/IndicVoices-R` | Gated models/data | Put the token in `backend/.env` as `HF_TOKEN=` yourself |
| A **valid Gemini key** (aistudio.google.com/apikey, free) | Long-context page generation | `GEMINI_API_KEY=` in `backend/.env` |
| Kaggle account (phone-verified for GPU) | Free fine-tuning GPU | Share nothing — I'll give you notebooks to run |
| Supabase + Oracle Cloud accounts (P3) | Free production hosting | You create the accounts; I script the setup |
| Voice actor + consent (V3) | Training our own Tanglish voice | Record with the script I prepare |

I can't create accounts or paste secrets for you — you add keys to `.env` directly (never in chat).

---

## 5. Decisions (2026-11-02)

- **Voices:** two house voices, male and female; the learner picks. Every narration sentence is pre-rendered in both.
- **Budget:** free only — open models and free tiers. No paid TTS/STT, not even as a fallback.
- **Start:** V1 (measure) + V2 (best zero-training voice and STT).

## 5b. Voice speed + style fine-tune (2026-11-03)

**Measured on this laptop (fresh sentences, ~5 s of audio each):**

| Engine | Latency | Where |
| --- | --- | --- |
| Edge (current live male voice) | 3.0–6.5 s | online — slow on this connection |
| Pocket-TTS (female, Tanglish) | ~2.0 s | local CPU |
| Kokoro (English) | ~0.9 s | local CPU |
| IndicF5 (any voice, GPU) | 17–30 s | RTX 3050 — pre-render only |
| Whisper-turbo STT | ~1.4 s / question | RTX 3050 |

→ Live tutor answers should use local engines; IndicF5/Parler only for narration pre-rendered at book-build time.

**Style, not voice (decision 2026-11-03).** The goal is the *speaking style* of native Tamil speakers
using Tanglish — rhythm, stress, how English words sit inside Tamil sentences — not a copy of any
one person. So:

- **Model: AI4Bharat Indic Parler-TTS** (Apache-2.0). Voices are chosen by a *text description*
  ("a male teacher with a warm, conversational Chennai Tamil accent, moderate pace"), not a
  reference recording, and it was trained on many speakers → no cloning. Two fixed descriptions
  (+ fixed seed) give the two house voices, male and female.
- **Data (streamed on a cloud GPU, never stored on the laptop):** Tamil rows of
  `ai4bharat/IndicVoices` (extempore + conversational, natural code-mixing) and
  `ai4bharat/indicvoices_r` (TTS-grade, 99 h, 1,084 speakers, CC-BY-4.0). Keep utterances that
  contain English words; normalise text the Bookey way (Tamil script, English words written
  phonetically in Tamil script). Target 20–40 h. Optionally add Tanglish teaching lines read by
  several consenting volunteers.
- **Fine-tune:** Kaggle 2×T4 (16 GB), fp16 + gradient checkpointing, ~10–20 GPU-hours across a few
  weekly sessions. Checkpoints pushed to a private HF repo.
- **STT:** LoRA on `vasista22/whisper-tamil-large-v2` with targets converted to Latin Tanglish by
  Bookey's romanizer, same code-mixed subset + volunteer recordings.
- **Gate:** ship only if it beats today's engines on the blind test page *and* on Whisper CER of
  the generated audio.
- **Blocked on:** dataset access — both datasets need "Agree and access" on huggingface.co
  (download is 403 until approved).

## 5c. Readiness after the 2026-11-03 audit + P2 (≈ 58% production-ready)

Audit found 25 backend + 25 frontend issues; all critical/high ones are fixed and tested
(backend 494 tests, frontend tsc + build clean, 0 known dependency vulnerabilities).

| Area | Ready | What's left |
| --- | --- | --- |
| Learning experience (reader, narration, diagrams, code lab, Q&A, recap) | 80% | browser QA of the new fixes on phones; a11y audit |
| Create-a-book pipeline | 80% | ✅ durable DB job queue (leases, retries, resume after restart, Retry button); chapter 1 still ~100 s (target < 60 s) |
| Voice (TTS/STT) | 55% | Tanglish style fine-tune (notebooks ready); fast local male Tamil voice |
| Security | 75% | real code-exec isolation if ever enabled; DNS-rebinding egress filter; strict CSP; SMTP |
| Infrastructure (Postgres, queue, hosting, backups, monitoring, CI) | 40% | ✅ Postgres + alembic chain (0001→0007, zero drift), separate worker process; left: hosting (P3), backups, monitoring, CI |
| Testing / QA | 50% | backend 510 (SQLite) / 511 (Postgres) pass; left: frontend unit + Playwright e2e in CI; load test |
| Mobile UX / performance | 55% | 375 px pass, bundle budgets, real-device testing |

Local demo / pilot with a few trusted users: ready now. Public launch: after P2 + P3 + fine-tune.

## 5d. "Real teacher" phases (2026-11-03)

Goal: it should feel like a person sitting next to you — listens, adapts, acts — in Tanglish or English,
for any subject, with nothing faked.

| Phase | What | Status |
| --- | --- | --- |
| R1 Voice commands + interaction | "stop / niruthu", "next page / aduththa page", "go back", "thirumba sollu", "slow-a / fast-a sollu", "run the code", "explain again simpler", "quiz me"; barge-in while she speaks; Tanglish + English; works by voice and text | ✅ done (matcher + `POST /api/voice/intent`, hands-free, command chips; browser-checked "niruthu" + "aduththa page") |
| R2 More inputs | video files (mp4/mov/webm → audio → Whisper transcript + key frames), images (photos of notes/diagrams → OCR / vision description), syllabus files | ✅ done (video/audio → Whisper on GPU + key frames; photos → free vision model / tesseract; syllabus mode) |
| R3 Adaptive learning | learner model (mastery per concept from questions, quiz answers, code runs, re-listens); per-lesson quizzes graded automatically; contextual bandit picks explanation style/depth/analogy; FSRS review queue; tutor prompts use the learner model | in progress |
| R4 Offline intelligence | in-browser STT (Whisper via WebGPU/WASM) and TTS (Piper Tamil voices) after "Download for offline"; book-only offline tutor answering from the book's pages + build-time FAQ | planned |
| R5 Project workspace | Chrome File System Access: learner picks a project folder; practicals read/write files there with explicit permission per folder (a web app cannot drive VS Code itself) | planned |
| P3 Hosting | needs the owner's free accounts (Vercel/Cloudflare, Supabase or managed Postgres, Oracle/Render VM, Modal) | blocked on accounts |
| V3 Fine-tune | Kaggle notebooks ready (`tools/finetune/`) | blocked on Kaggle token |

## 6. Order of work

1. V1 measure → 2. V2 best zero-training voice + STT → 3. P1 generic books → 4. P2 fast pipeline →
5. V3 fine-tune (in parallel with P3 once data is recorded) → 6. P3 deploy → 7. P4 perf/UX → 8. P5.

Each step ends with something you can try in the browser, plus tests.
