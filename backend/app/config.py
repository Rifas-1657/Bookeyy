"""
app/config.py â€” pydantic-settings configuration.
All values have safe defaults so the server starts with no .env file (dev/test).
"""
from __future__ import annotations

import secrets
from functools import lru_cache
import json
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    # Skip reading the .env file when APP_ENV=testing so that a malformed
    # .env (e.g. bad CORS_ORIGINS syntax) never blocks the test runner.
    # All test values are supplied via environment variable overrides.
    _env_file = ".env" if __import__("os").environ.get("APP_ENV") != "testing" else None

    model_config = SettingsConfigDict(
        env_file=".env" if __import__("os").environ.get("APP_ENV") != "testing" else None,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App ---
    app_env: Literal["development", "testing", "production"] = "development"
    debug: bool = True

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8000

    # --- Database ---
    # SQLite for dev/tests; PostgreSQL for production (same SQLAlchemy models).
    database_url: str = Field(
        default="sqlite+aiosqlite:///./bookey_dev.db",
        description="Async SQLAlchemy URL. Use postgresql+asyncpg://... for production.",
    )
    db_echo: bool = False
    # PostgreSQL pool (ignored for SQLite). Tests use NullPool (see app/db/database.py).
    db_pool_size: int = 5
    db_max_overflow: int = 10
    # Behind PgBouncer in transaction mode (Supabase pooler on :6543) asyncpg's prepared
    # statement cache must be off.
    db_pgbouncer: bool = False

    # --- Durable job queue (app/jobs) ---
    # Run the worker loop inside the API process (single-box deploys / dev). Turn it off
    # when running `python -m app.worker` as separate process(es).
    jobs_inline_worker: bool = True
    jobs_concurrency: int = 2
    jobs_lease_seconds: float = 60.0         # a claimed job is requeued if not renewed in time
    jobs_poll_interval: float = 1.0          # idle poll; enqueue in-process wakes the worker immediately
    jobs_max_attempts: int = 3               # attempts before a job ends in `failed`
    jobs_backoff_base_seconds: float = 10.0  # retry delay = base * 2**(attempt-1), capped
    jobs_backoff_max_seconds: float = 600.0

    # --- Auth ---
    # IMPORTANT: override SECRET_KEY in production with a long random value!
    secret_key: str = Field(
        default_factory=lambda: secrets.token_hex(32),
        description="JWT signing key. Set SECRET_KEY env var in production.",
    )
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # --- CORS ---
    # Comma-separated list of allowed origins, or set as a JSON list in env
    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:3000",
        "http://localhost:4100",
        "http://127.0.0.1:3000",
    ]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                return json.loads(text)
            return [origin.strip() for origin in text.split(",") if origin.strip()]
        return value

    # --- WebSocket ---
    ws_heartbeat_interval: int = 30  # seconds
    ws_heartbeat_timeout: int = 10   # seconds after ping with no pong
    ws_max_message_size: int = 1024 * 512  # 512 KB
    ws_auth_timeout: float = 5.0  # seconds to send auth_ticket before socket is closed
    ws_max_audio_bytes: int = 10 * 1024 * 1024   # per-session audio_in buffer cap
    ws_max_audio_seconds: float = 60.0            # per-utterance audio_in duration cap

    # --- Protocol ---
    book_protocol_version: int = 2  # bumped to v2 in B1

    # --- MockBookService timing (ms) ---
    mock_token_ms: int = 85          # delay between word tokens
    mock_thinking_ms: int = 600      # tutor "thinking" pause
    mock_answer_token_ms: int = 45   # delay between answer tokens
    mock_code_stream_ms: int = 30    # delay between code stream chunks

    # --- Rate limiting ---
    rate_limit_per_minute: int = 60  # per IP default
    rate_limit_auth_per_minute: int = 10  # stricter on auth endpoints
    rate_limit_books_per_minute: int = 10  # POST /api/books
    rate_limit_voice_per_minute: int = 30  # per-user TTS / STT
    rate_limit_enabled: bool = True
    # Comma-separated proxy IPs/CIDRs whose X-Forwarded-For header is trusted.
    # Empty (default) = never trust X-Forwarded-For; key by the socket peer IP.
    trusted_proxies: str = ""

    # --- Request body limits ---
    # Early Content-Length rejection for upload endpoints (/api/books, admin documents, STT).
    max_upload_body_bytes: int = 30 * 1024 * 1024          # POST /api/books
    max_admin_upload_body_bytes: int = 200 * 1024 * 1024   # admin document batches (≤20 files)
    max_stt_upload_bytes: int = 10 * 1024 * 1024
    # POST /api/books/media (video / audio / images). 0 = derive from BOOKS_MAX_MEDIA_MB
    # (+ multipart overhead), so raising the media limit raises this route's body limit only.
    max_media_upload_body_bytes: int = 0

    # --- SMTP (optional â€” required for production password reset) ---
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "noreply@bookey.app"
    # --- Email (app/auth/mailer.py) ---
    brevo_api_key: str = ""
    mail_from: str = "noreply@bookey.app"
    public_app_url: str = "http://localhost:3000"

    # =========================================================
    # B2: Knowledge base
    # =========================================================
    max_document_size_bytes: int = 25 * 1024 * 1024
    # Phase-1: OCR — attempt Tesseract OCR on scanned PDFs when True
    ocr_enabled: bool = True
    pdf_max_pages: int = 300       # pages parsed per PDF
    pdf_max_ocr_pages: int = 30    # pages OCR'd per PDF
    # Zip-container (docx/pptx/xlsx) bomb protection
    zip_max_total_uncompressed_bytes: int = 200 * 1024 * 1024
    zip_max_entries: int = 5000
    zip_max_compression_ratio: float = 100.0
    storage_backend: str = "local"
    storage_local_root: str = "./uploads"
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "auto"
    s3_endpoint_url: str = ""
    # STORAGE_BACKEND=supabase: Supabase Storage over its REST API (service_role key, private bucket)
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_storage_bucket: str = "bookey"
    embedding_backend: str = "mock"
    embedding_batch_size: int = 32
    embedding_device: str = "auto"
    # EMBEDDING_BACKEND=fastembed: small ONNX model on CPU (no torch). Default fits a 512 MB host.
    fastembed_model: str = "BAAI/bge-small-en-v1.5"
    fastembed_cache_dir: str = ""      # "" = fastembed default; the Docker image bakes the model in
    fastembed_threads: int = 1
    vector_store: str = "chroma"
    chroma_persist_dir: str = "./chroma_db"
    retrieval_top_k: int = 5
    retrieval_score_threshold: float = 0.0
    retrieval_mmr_lambda: float = 0.7


    # =========================================================
    # B3: LLM Router + Page Generator
    # =========================================================

    # --- LLM Providers (comma-ordered fallback list) ---
    llm_providers: str = "groq,openrouter,gemini"
    llm_retry_per_provider: int = 2
    llm_backoff_base_s: float = 1.0

    # --- Gemini ---
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    # --- Groq ---
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"

    # --- OpenRouter ---
    openrouter_api_key: str = ""
    openrouter_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"

    # --- Vision (image input: photos of notes, video key frames) ---
    # Providers without a vision model are skipped for image calls. OpenRouter tries the
    # comma-separated models in order (free multimodal models get rate-limited upstream).
    vision_enabled: bool = True
    openrouter_vision_models: str = (
        "nvidia/nemotron-3.5-super-vl:free,google/gemma-4-31b-it:free,google/gemma-4-26b-a4b-it:free"
    )
    groq_vision_model: str = ""          # e.g. a Llama-4 vision model when Groq offers one
    gemini_vision_model: str = ""        # "" = GEMINI_MODEL (Gemini models accept images)

    # Mock LLM is only used when APP_ENV=testing or LLM_ALLOW_MOCK=true; otherwise
    # "mock" in LLM_PROVIDERS is ignored so placeholder content is never published.
    llm_allow_mock: bool = False
    # Overall deadline for one router call across all providers + retries (seconds).
    llm_call_deadline_s: float = 90.0

    # --- Page generator ---
    page_min_words: int = 60
    page_max_words: int = 300
    page_max_tokens: int = 8192
    page_gen_max_repair_attempts: int = 2
    # Execute LLM-written Python code blocks to verify expected_output.
    # OFF by default: code is only syntax-checked. Only enable behind real isolation
    # (container / nsjail / gVisor) — see app/page_gen/sandbox.py. Learner books never execute.
    code_runner_enabled: bool = False


    # =========================================================
    # B4: Tutor agent
    # =========================================================
    tutor_history_window: int = 3       # last N exchanges in context
    tutor_max_answer_tokens: int = 800

    # =========================================================
    # B5: Voice (STT + TTS)
    # =========================================================
    # Providers
    # TTS_PROVIDER: local (default: free, local, female) | pocket_tts | kokoro | edge |
    #               indic_parler | bodhan | gemini | silent
    tts_provider: str = "local"
    stt_provider: str = "local"         # local (faster-whisper), groq

    # Local TTS routing (used when tts_provider=local)
    tts_tanglish_provider: str = "pocket_tts"   # pocket_tts | indic_parler | edge
    tts_english_provider: str = "kokoro"        # kokoro | edge
    tts_tanglish_voice: str = "geetha"          # Pocket TTS female (only voice)
    tts_english_voice: str = "af_heart"         # Kokoro female: af_heart | af_bella | bf_emma
    # Learner-picked tutor voice ("male" | "female"). Live answers use these chains;
    # local engines first because they are 2-3x faster than Edge on slow connections.
    tts_female_tanglish_provider: str = "pocket_tts"
    tts_female_english_provider: str = "kokoro"
    tts_female_english_voice: str = "af_heart"
    tts_male_tanglish_provider: str = "edge"     # no local male Tamil voice yet (style fine-tune pending)
    tts_male_english_provider: str = "kokoro"
    tts_male_english_voice: str = "am_michael"
    edge_tts_voice_female: str = "ta-IN-PallaviNeural"
    edge_tts_voice_male: str = "ta-IN-ValluvarNeural"
    edge_tts_voice_en_female: str = "en-IN-NeerjaNeural"
    edge_tts_voice_en_male: str = "en-IN-PrabhatNeural"
    tts_default_lang: str = "auto"              # auto | tanglish | english
    tts_output_format: str = "wav"              # wav | mp3 (mp3 needs ffmpeg)
    tts_sentence_pause_ms: int = 350
    tts_comma_pause_ms: int = 180
    tts_clause_split_words: int = 22            # split sentences longer than this at commas
    tts_translit_llm: bool = True               # LLM transliteration for unknown words (cached)
    tts_translit_llm_timeout_s: float = 12.0

    # Pocket TTS (prasadvittaldev/pocket-tts-tamil-female-audiobook, MIT)
    pocket_tts_model_id: str = "prasadvittaldev/pocket-tts-tamil-female-audiobook"
    pocket_tts_quantize: bool = True
    pocket_tts_voice_prompt: str = ""           # optional custom female reference .wav

    # Kokoro-82M via kokoro-onnx (Apache-2.0)
    kokoro_model_path: str = ".cache/models/kokoro/kokoro-v1.0.onnx"
    kokoro_voices_path: str = ".cache/models/kokoro/voices-v1.0.bin"
    kokoro_auto_download: bool = True

    # Indic Parler-TTS (optional, gated; needs HF token)
    hf_token: str = ""
    indic_parler_voice: str = "Jaya"
    indic_parler_description: str = ""

    # STT (Local Faster-Whisper & Groq Whisper)
    # "auto" → large-v3-turbo on CUDA (int8_float16) else faster_whisper_cpu_model (int8)
    faster_whisper_model: str = "auto"
    faster_whisper_cpu_model: str = "small"
    faster_whisper_device: str = "auto"         # auto | cuda | cpu
    faster_whisper_compute_type: str = "auto"
    bookey_stt_model: str = "whisper-large-v3-turbo"
    groq_whisper_model: str = "whisper-large-v3-turbo"
    # Bookey's own voices + Tanglish STT (tools/voice-space); empty = not used
    voice_space_url: str = ""
    voice_space_token: str = ""

    # TTS: Bodhan Indic-Speak (paid, opt-in)
    bodhan_api_key: str = ""
    bodhan_tts_model: str = "indic-speak"
    bodhan_tts_voice: str = "Anitha"
    bodhan_tts_lang: str = "ta"
    bodhan_tts_style: str = "educational lecture"

    # TTS: Gemini (opt-in) & Edge (free online fallback, FEMALE voices)
    bookey_tts_model: str = "gemini-3.8-flash-tts"
    bookey_tts_voice: str = "Puck"
    bookey_tts_style: str = "clear, warm, patient teaching style"
    edge_tts_voice: str = "ta-IN-PallaviNeural"
    edge_tts_voice_en: str = "en-IN-NeerjaNeural"

    # Audio Cache
    audio_cache_dir: str = "backend/.cache/bookey-voice"
    voice_enabled: bool = True
    # Speak page narration over the WebSocket with live TTS. Off = the reader plays
    # prebuilt page audio (public/audio/...) and the server only paces text.
    narration_tts: bool = True
    tts_first_sentence_only: bool = True  # start playback after first sentence

    @property
    def mock_llm_allowed(self) -> bool:
        return self.app_env == "testing" or bool(self.llm_allow_mock)

    @property
    def effective_llm_providers(self) -> list[str]:
        """LLM_PROVIDERS with 'mock' dropped unless testing / LLM_ALLOW_MOCK=true."""
        names = [p.strip().lower() for p in self.llm_providers.split(",") if p.strip()]
        if not self.mock_llm_allowed:
            names = [n for n in names if n != "mock"]
        return names


_WEAK_SECRET_KEYS = {
    "", "secret", "changeme", "change-me", "change_me", "dev", "development", "test",
    "rifas1234", "your-secret-key", "your_secret_key", "replace-me", "bookey",
}
MIN_SECRET_KEY_LENGTH = 32


def validate_production_settings(s: "Settings") -> list[str]:
    """Return a list of fatal misconfigurations for APP_ENV=production (empty = OK).
    Reads the *effective* settings object (env vars AND .env), not os.environ."""
    errors: list[str] = []
    key = s.secret_key or ""
    if "secret_key" not in s.model_fields_set:
        errors.append("SECRET_KEY must be explicitly set in production (env or .env); "
                      "a per-process random key breaks sessions across restarts/workers")
    elif len(key) < MIN_SECRET_KEY_LENGTH or key.strip().lower() in _WEAK_SECRET_KEYS or len(set(key)) < 8:
        errors.append(f"SECRET_KEY is too short or a known default (need >= {MIN_SECRET_KEY_LENGTH} random chars)")
    if s.debug:
        errors.append("DEBUG must be false in production")
    localhost_origins = [o for o in s.cors_origins if "localhost" in o or "127.0.0.1" in o]
    if localhost_origins:
        errors.append(f"CORS_ORIGINS contains localhost/127.0.0.1 in production: {localhost_origins}")
    if "sqlite" in s.database_url.lower():
        errors.append("DATABASE_URL is SQLite in production")
    raw_llm = [p.strip().lower() for p in s.llm_providers.split(",") if p.strip()]
    if s.llm_allow_mock or "mock" in raw_llm:
        errors.append("Mock LLM must not be configured in production (remove 'mock' from LLM_PROVIDERS "
                      "and unset LLM_ALLOW_MOCK)")
    return errors


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
