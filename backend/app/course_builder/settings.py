"""
app/course_builder/settings.py — course-builder knobs read from the environment
(kept out of app/config.py on purpose).

  COURSE_BUILDER_MAX_SOURCES            default 25
  COURSE_BUILDER_MAX_CHAPTERS           default 8
  COURSE_BUILDER_MAX_LESSONS_PER_CHAPTER default 6
  COURSE_BUILDER_AUTO_GENERATE_REMAINING default true  (queue lessons 2..N after lesson 1)
  COURSE_BUILDER_FETCH_CONCURRENCY      default 3
  COURSE_BUILDER_OUTLINE_CONTEXT_CHARS  default 24000  (source excerpts sent to the LLM)
  COURSE_BUILDER_OUTLINE_MAX_TOKENS     default 12000
  COURSE_BUILDER_LESSON_CONCURRENCY     default 1
  COURSE_BUILDER_SSE_INTERVAL_S         default 1.0
  COURSE_BUILDER_SSE_MAX_SECONDS        default 1800
(source adapters read GITHUB_TOKEN, GITHUB_MAX_FILES, REDDIT_USER_AGENT,
 COURSE_BUILDER_YT_* — see app/knowledge/sources/*)
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class CourseBuilderSettings:
    max_sources: int
    max_chapters: int
    max_lessons_per_chapter: int
    auto_generate_remaining: bool
    fetch_concurrency: int
    outline_context_chars: int
    outline_max_tokens: int
    lesson_concurrency: int
    sse_interval_s: float
    sse_max_seconds: int


def get_cb_settings() -> CourseBuilderSettings:
    # Not cached: tests flip env vars at runtime.
    return CourseBuilderSettings(
        max_sources=_int("COURSE_BUILDER_MAX_SOURCES", 25),
        max_chapters=_int("COURSE_BUILDER_MAX_CHAPTERS", 8),
        max_lessons_per_chapter=_int("COURSE_BUILDER_MAX_LESSONS_PER_CHAPTER", 6),
        auto_generate_remaining=_bool("COURSE_BUILDER_AUTO_GENERATE_REMAINING", True),
        fetch_concurrency=max(1, _int("COURSE_BUILDER_FETCH_CONCURRENCY", 3)),
        outline_context_chars=_int("COURSE_BUILDER_OUTLINE_CONTEXT_CHARS", 24_000),
        outline_max_tokens=_int("COURSE_BUILDER_OUTLINE_MAX_TOKENS", 12_000),
        lesson_concurrency=max(1, _int("COURSE_BUILDER_LESSON_CONCURRENCY", 1)),
        sse_interval_s=_float("COURSE_BUILDER_SSE_INTERVAL_S", 1.0),
        sse_max_seconds=_int("COURSE_BUILDER_SSE_MAX_SECONDS", 1800),
    )
