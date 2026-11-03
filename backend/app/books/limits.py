"""
app/books/limits.py — size/duration limits for learner media uploads (video, audio, images).

Kept import-light (no DB / FastAPI) so the body-size middleware can read it.
Read on every call so tests can flip the env vars.

  BOOKS_MAX_MEDIA_MB          default 500   one video / audio file
  BOOKS_MAX_MEDIA_MINUTES     default 120   video / audio duration
  BOOKS_MAX_IMAGES            default 20    images per book
  BOOKS_MAX_IMAGE_MB          default 15    each image
  BOOKS_SYLLABUS_MAX_CHAPTERS default 12    chapters when a learner's syllabus drives the outline
  BOOKS_SYLLABUS_MAX_LESSONS  default 6     max lessons per syllabus unit
"""
from __future__ import annotations

import os
from dataclasses import dataclass

VIDEO_EXTS = (".mp4", ".mov", ".webm", ".mkv", ".m4v")
AUDIO_EXTS = (".m4a", ".mp3", ".wav", ".ogg", ".opus", ".aac", ".flac")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif")
DOCUMENT_EXTS = (".pdf", ".pptx", ".docx", ".txt", ".md")

MULTIPART_OVERHEAD = 2 * 1024 * 1024


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class MediaLimits:
    max_media_bytes: int
    max_media_seconds: int
    max_images: int
    max_image_bytes: int
    syllabus_max_chapters: int
    syllabus_max_lessons: int

    @property
    def body_bytes(self) -> int:
        """Largest legal POST /api/books/media body."""
        return max(self.max_media_bytes, self.max_images * self.max_image_bytes) + MULTIPART_OVERHEAD


def get_media_limits() -> MediaLimits:
    return MediaLimits(
        max_media_bytes=max(1, _int("BOOKS_MAX_MEDIA_MB", 500)) * 1024 * 1024,
        max_media_seconds=max(1, _int("BOOKS_MAX_MEDIA_MINUTES", 120)) * 60,
        max_images=max(1, _int("BOOKS_MAX_IMAGES", 20)),
        max_image_bytes=max(1, _int("BOOKS_MAX_IMAGE_MB", 15)) * 1024 * 1024,
        syllabus_max_chapters=min(20, max(1, _int("BOOKS_SYLLABUS_MAX_CHAPTERS", 12))),
        syllabus_max_lessons=min(10, max(1, _int("BOOKS_SYLLABUS_MAX_LESSONS", 6))),
    )


def media_kind(filename: str) -> str | None:
    """'video' | 'audio' | 'image' | 'document' | None (by extension)."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in DOCUMENT_EXTS:
        return "document"
    return None
