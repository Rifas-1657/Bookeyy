"""
app/course_builder/schema.py — course outline + API request models.

All models serialize to camelCase (`model_dump(by_alias=True)`) and accept
either camelCase or snake_case input (LLM output and admin edits).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from app.knowledge.sources.base import SOURCE_TYPES


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")

    def dump(self) -> dict:
        return self.model_dump(by_alias=True, mode="json")


# ─────────────────────────────────────────────────────────────────────────────
# Outline
# ─────────────────────────────────────────────────────────────────────────────

class OutlineCitation(CamelModel):
    chunk_id: str = ""
    document_id: str = ""
    source_index: int | None = None      # index into job.sources
    quote: str = ""


class Practical(CamelModel):
    kind: Literal["code", "activity"] = "code"
    title: str
    instructions: str
    language: str = "python"
    starter_code: str = ""
    solution_code: str = ""
    expected_output: str = ""
    tests: list[str] = Field(default_factory=list)   # e.g. assert statements / checks


class LessonTask(CamelModel):
    id: str = ""
    kind: Literal["quiz", "task"] = "quiz"
    prompt: str
    options: list[str] = Field(default_factory=list)   # quiz choices (empty for open tasks)
    answer: str = ""
    explanation: str = ""


class ChapterProject(CamelModel):
    title: str
    brief: str
    deliverables: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    starter_code: str = ""
    language: str = "python"
    rubric: list[str] = Field(default_factory=list)


class OutlineLesson(CamelModel):
    id: str = ""                       # "ch1-l2" (assigned by normalize_outline)
    number: int = 0
    title: str
    summary: str = ""
    objectives: list[str] = Field(min_length=1)
    key_terms: list[str] = Field(default_factory=list)
    explanation_plan: list[str] = Field(min_length=1)   # narration beats, in order
    diagram_ideas: list[str] = Field(default_factory=list)
    practical: Practical
    tasks: list[LessonTask] = Field(min_length=2, max_length=3)
    citations: list[OutlineCitation] = Field(default_factory=list)
    estimated_minutes: int = Field(default=8, ge=1, le=120)

    @field_validator("tasks", mode="before")
    @classmethod
    def _trim_tasks(cls, v):
        return v[:3] if isinstance(v, list) else v


class OutlineChapter(CamelModel):
    id: str = ""
    number: int = 0
    title: str
    summary: str = ""
    lessons: list[OutlineLesson] = Field(min_length=1)
    project: ChapterProject


class CourseOutline(CamelModel):
    title: str
    subtitle: str = ""
    description: str = ""
    level: str = "beginner"
    language: str = "english"
    target_audience: str = ""
    prerequisites: list[str] = Field(default_factory=list)
    learning_outcomes: list[str] = Field(default_factory=list)
    chapters: list[OutlineChapter] = Field(min_length=1)
    generator: str = "llm"             # "llm" | "heuristic" | "admin"

    @model_validator(mode="after")
    def _number(self) -> "CourseOutline":
        normalize_outline(self)
        return self


def normalize_outline(outline: CourseOutline) -> CourseOutline:
    """Assign stable sequential numbers/ids: chapters ch1.., lessons ch1-l1.."""
    for ci, ch in enumerate(outline.chapters, start=1):
        ch.number = ci
        ch.id = f"ch{ci}"
        for li, lesson in enumerate(ch.lessons, start=1):
            lesson.number = li
            lesson.id = f"ch{ci}-l{li}"
            for ti, t in enumerate(lesson.tasks, start=1):
                if not t.id:
                    t.id = f"{lesson.id}-t{ti}"
    return outline


# ─────────────────────────────────────────────────────────────────────────────
# API requests
# ─────────────────────────────────────────────────────────────────────────────

SourceTypeLit = Literal["youtube", "github", "reddit", "arxiv", "pdf_url", "web", "upload", "syllabus",
                        "media", "images"]


class SourceSpec(CamelModel):
    type: SourceTypeLit
    url: str | None = None
    document_id: str | None = None     # for type="upload" / "media" (an existing Document id)
    document_ids: list[str] = Field(default_factory=list)   # for type="images" (stored image Documents)
    text: str | None = None            # for type="syllabus"
    title: str | None = None           # optional display title override
    # "syllabus" = the learner marked this material as THE syllabus: its text drives the outline.
    role: Literal["syllabus"] | None = None

    @model_validator(mode="after")
    def _check(self) -> "SourceSpec":
        if self.type in ("upload", "media") and not self.document_id:
            raise ValueError(f"documentId is required for {self.type} sources")
        if self.type == "images" and not (self.document_ids or self.document_id):
            raise ValueError("documentIds is required for images sources")
        if self.type == "syllabus" and not (self.text and self.text.strip()):
            raise ValueError("text is required for syllabus sources")
        if self.type not in ("upload", "syllabus", "media", "images") and not (self.url and self.url.strip()):
            raise ValueError(f"url is required for {self.type} sources")
        return self


class BuildOptions(CamelModel):
    max_chapters: int | None = Field(default=None, ge=1, le=20)
    lessons_per_chapter: int | None = Field(default=None, ge=1, le=10)
    auto_generate_remaining: bool | None = None


class CreateJobRequest(CamelModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = ""
    level: str = "beginner"
    language: Literal["tanglish", "english"] = "tanglish"
    target_audience: str = ""
    sources: list[SourceSpec] = Field(default_factory=list)
    syllabus_text: str | None = None
    # The learner's own syllabus (pasted, or a source with role="syllabus"): the outline follows
    # it exactly — same units, same order — instead of only using it as a guide.
    syllabus_strict: bool = False
    # Prompt-only books: the LLM writes a syllabus from this request first
    # (book is labelled "generated from AI knowledge").
    prompt: str | None = Field(default=None, max_length=2000)
    course_id: str | None = Field(default=None, max_length=60, pattern=r"^[a-z0-9][a-z0-9\-_]*$")
    options: BuildOptions = Field(default_factory=BuildOptions)

    @model_validator(mode="after")
    def _need_material(self) -> "CreateJobRequest":
        if (not self.sources and not (self.syllabus_text and self.syllabus_text.strip())
                and not (self.prompt and self.prompt.strip())):
            raise ValueError("Provide at least one source, a syllabusText or a prompt.")
        return self


class PreviewRequest(CamelModel):
    type: SourceTypeLit | None = None
    url: str


class GenerateRequest(CamelModel):
    lesson_ids: list[str] | None = None    # default: every lesson not yet done
    regenerate: bool = False               # also redo lessons already done


class PublishRequest(CamelModel):
    available: bool = True
    include_ungenerated: bool = True       # publish outline-derived pages for lessons not generated yet


assert set(SOURCE_TYPES) == set(SourceTypeLit.__args__)  # keep enums in sync
