# ─────────────────────────────────────────────────────────
#  Bookey backend — production image (Render free: 512 MB RAM, no GPU)
#
#  Slim by default: requirements-prod.txt (no torch / local TTS / local STT / Chroma).
#  Voice in production = Groq Whisper STT + Edge neural TTS; vectors = pgvector;
#  files = Supabase Storage. Build the full laptop image with:
#     docker build --build-arg REQUIREMENTS=requirements.txt -t bookey-full .
#
#  Render injects $PORT; migrations run before the server starts (idempotent).
# ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

ARG REQUIREMENTS=requirements-prod.txt
ARG FASTEMBED_MODEL=BAAI/bge-small-en-v1.5

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FASTEMBED_CACHE_DIR=/opt/fastembed \
    FASTEMBED_MODEL=${FASTEMBED_MODEL} \
    HF_HUB_DISABLE_TELEMETRY=1 \
    MALLOC_ARENA_MAX=2 \
    AUDIO_CACHE_DIR=/app/.cache/bookey-voice

# Runtime system deps: ffmpeg (Edge MP3 decode, media uploads), tesseract (OCR), curl (healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg tesseract-ocr tesseract-ocr-eng tesseract-ocr-tam curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r bookey && useradd -r -g bookey -d /app bookey
WORKDIR /app

COPY requirements*.txt ./
RUN pip install --upgrade pip && pip install -r ${REQUIREMENTS}

# Bake the embedding model into the image (no download on every cold start)
RUN if python -c "import fastembed" 2>/dev/null; then \
      python -c "import os; from fastembed import TextEmbedding; TextEmbedding(os.environ['FASTEMBED_MODEL'], cache_dir=os.environ['FASTEMBED_CACHE_DIR'])" \
      && chmod -R a+rX /opt/fastembed; \
    fi

COPY --chown=bookey:bookey . .
RUN rm -f .env && mkdir -p /app/uploads /app/.cache && chown bookey:bookey /app && chown -R bookey:bookey /app/uploads /app/.cache

USER bookey
ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT}/health" || exit 1

# Single worker (WebSockets, inline durable-job worker). --ws-max-size = WS_MAX_MESSAGE_SIZE.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --ws-max-size ${WS_MAX_MESSAGE_SIZE:-524288} --timeout-graceful-shutdown 10 --log-level info"]
