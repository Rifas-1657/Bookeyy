"""
app/course_builder/publish.py — turn a course-build job into Course / Chapter / Page rows
so it shows up in /api/courses (same JSON shape as the seeded Python course).

Per chapter: one page per lesson (generated page if available, else an
outline-derived page) followed by one "Project" page.
"""
from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import delete, select

from app.course_builder.schema import ChapterProject, CourseOutline, OutlineLesson
from app.db.database import AsyncSessionLocal
from app.db.models import Chapter, Course, CourseBuildJob, CourseBuildLesson, Page
from app.page_gen.schema import GeneratedPage

_WORD = re.compile(r"[\w']+")


def _norm(w: str) -> str:
    return re.sub(r"[^\w]", "", w.lower())


def _diagram_from_generated(gp: GeneratedPage, title: str, caption: str) -> dict:
    nodes = [{"id": n.id, "label": n.label, "tone": "highlight" if i == 0 else "neutral", "font": "sans"}
             for i, n in enumerate(gp.diagram.nodes)]
    edges = [{"id": f"e{i + 1}", "from": e.from_id, "to": e.to_id, "label": e.label}
             for i, e in enumerate(gp.diagram.edges)]
    steps = [{"id": f"s{st.step}", "label": f"Step {st.step}", "description": st.description, "reveal": [st.node_id]}
             for st in gp.diagram.steps]
    return {"kind": "io-flow" if edges else "labeled-boxes", "title": title, "caption": caption,
            "nodes": nodes, "edges": edges, "steps": steps}


def _diagram_from_ideas(title: str, ideas: list[str], fallback: list[str]) -> dict:
    labels: list[str] = []
    if ideas:
        labels = [p.strip() for p in re.split(r"\s*(?:→|->|=>)\s*", ideas[0]) if p.strip()]
    if len(labels) < 2:
        labels = [x[:40] for x in fallback[:4]] or [title[:40]]
    nodes = [{"id": f"n{i + 1}", "label": l[:60], "tone": "highlight" if i == 0 else "neutral", "font": "sans"}
             for i, l in enumerate(labels[:6])]
    edges = [{"id": f"e{i + 1}", "from": nodes[i]["id"], "to": nodes[i + 1]["id"]} for i in range(len(nodes) - 1)]
    return {"kind": "io-flow" if edges else "labeled-boxes", "title": title,
            "caption": ideas[0] if ideas else "", "nodes": nodes, "edges": edges,
            "steps": [{"id": f"s{i + 1}", "label": f"Step {i + 1}", "description": n["label"], "reveal": [n["id"]]}
                      for i, n in enumerate(nodes)]}


def _cues(gp: GeneratedPage) -> list[dict]:
    words = [_norm(w) for w in _WORD.findall(gp.narration)]
    node_ids = {n.id for n in gp.diagram.nodes}
    out: list[dict] = []
    for seg in gp.segments:
        for cue in seg.cues:
            target = _norm(cue.at_word)
            idx = next((i for i, w in enumerate(words) if w == target), None)
            if idx is None:
                idx = min(len(words) - 1, len(out) * 10) if words else 0
            if cue.target_id in node_ids:
                kind, tgt = "focus-node", cue.target_id
            else:
                kind, tgt = "highlight-term", cue.at_word
            out.append({"id": f"c{len(out) + 1}", "atWord": idx, "kind": kind, "target": tgt})
    return sorted(out, key=lambda c: c["atWord"])


def _task_prompts(lesson: OutlineLesson) -> list[str]:
    out = []
    p = lesson.practical
    out.append(f"Practical — {p.title}: {p.instructions}")
    for t in lesson.tasks:
        if t.kind == "quiz" and t.options:
            out.append(f"Quiz: {t.prompt} Options: {' / '.join(t.options)}")
        else:
            out.append(f"Task: {t.prompt}")
    return out


def _practical_code(lesson: OutlineLesson) -> dict:
    p = lesson.practical
    if p.kind != "code" or not (p.solution_code or p.starter_code):
        return {}
    return {"language": p.language or "python", "source": p.starter_code or p.solution_code,
            "expectedOutput": p.expected_output, "runnable": (p.language or "python") == "python"}


def page_fields_from_generated(gp: GeneratedPage, lesson: OutlineLesson) -> dict:
    code_seg = next((s for s in gp.segments if s.type == "code" and s.code), None)
    code = ({"language": code_seg.language, "source": code_seg.code, "expectedOutput": code_seg.expected_output,
             "runnable": code_seg.language == "python"} if code_seg else _practical_code(lesson))
    paragraphs = [s.text for s in gp.segments if s.type == "explanation" and s.text.strip()] or lesson.explanation_plan
    return {
        "title": (gp.title or lesson.title)[:200],
        "narration_text": gp.narration,
        "paragraphs": paragraphs,
        "key_terms": lesson.key_terms or [n.label for n in gp.diagram.nodes][:6],
        "code_example": code,
        "diagram_spec": _diagram_from_generated(gp, lesson.title, lesson.diagram_ideas[0] if lesson.diagram_ideas else ""),
        "cues": _cues(gp),
        "practice_prompts": [p.prompt for p in gp.practice_prompts] + _task_prompts(lesson),
        "analogies": {"movies": gp.analogies.movie, "cricket": gp.analogies.cricket, "neutral": ""},
        "estimated_minutes": lesson.estimated_minutes,
    }


def page_fields_from_outline(lesson: OutlineLesson) -> dict:
    return {
        "title": lesson.title[:200],
        "narration_text": " ".join(lesson.explanation_plan),
        "paragraphs": ([lesson.summary] if lesson.summary else []) + lesson.explanation_plan,
        "key_terms": lesson.key_terms,
        "code_example": _practical_code(lesson),
        "diagram_spec": _diagram_from_ideas(lesson.title, lesson.diagram_ideas, lesson.objectives),
        "cues": [],
        "practice_prompts": _task_prompts(lesson),
        "analogies": {},
        "estimated_minutes": lesson.estimated_minutes,
    }


def page_fields_for_project(project: ChapterProject) -> dict:
    code = ({"language": project.language or "python", "source": project.starter_code, "expectedOutput": "",
             "runnable": False} if project.starter_code else {})
    return {
        "title": f"Project: {project.title}"[:200] if not project.title.lower().startswith(("project", "mini project")) else project.title[:200],
        "narration_text": " ".join([project.brief] + project.steps),
        "paragraphs": [project.brief] + [f"Deliverable: {d}" for d in project.deliverables],
        "key_terms": [],
        "code_example": code,
        "diagram_spec": _diagram_from_ideas(project.title, [" → ".join(project.steps[:5])] if project.steps else [], project.deliverables),
        "cues": [],
        "practice_prompts": [f"Step {i + 1}: {s}" for i, s in enumerate(project.steps)] + [f"Rubric: {r}" for r in project.rubric],
        "analogies": {},
        "estimated_minutes": 20,
    }


async def publish_job(job_id: str, *, available: bool = True, include_ungenerated: bool = True,
                      overrides: dict | None = None, cover: dict | None = None) -> dict:
    """Upsert the job's Course/Chapter/Page rows.  `overrides` replaces Course fields
    (e.g. author/category for learner books); `cover` replaces the cover JSON."""
    async with AsyncSessionLocal() as db:
        job = await db.get(CourseBuildJob, job_id)
        if job is None:
            raise LookupError("job not found")
        if not job.outline:
            raise ValueError("Job has no outline yet.")
        outline = CourseOutline.model_validate(job.outline)
        lessons = {l.lesson_key: l for l in (await db.execute(
            select(CourseBuildLesson).where(CourseBuildLesson.job_id == job_id))).scalars()}
        if not include_ungenerated:
            missing = [k for ch in outline.chapters for k in [l.id for l in ch.lessons]
                       if not (lessons.get(k) and lessons[k].status == "done" and lessons[k].page)]
            if missing:
                raise ValueError(f"{len(missing)} lesson(s) not generated yet: {', '.join(missing[:8])}")

        cid = job.course_id
        course = await db.get(Course, cid)
        all_minutes = [l.estimated_minutes for ch in outline.chapters for l in ch.lessons]
        fields = dict(
            title=outline.title[:200] or job.title,
            subtitle=(outline.subtitle or outline.description)[:400],
            description=outline.description or job.request.get("description", ""),
            level=(outline.level or "beginner").capitalize()[:20],
            category="Course Builder",
            language=("Tanglish" if outline.language == "tanglish" else "English"),
            available=available,
            author="Bookey Course Builder",
            outcomes=outline.learning_outcomes,
            topics=[ch.title for ch in outline.chapters],
            tutor_style=("Tanglish narration (Tamil + English technical terms)" if outline.language == "tanglish"
                         else "English narration"),
            minutes_per_page=max(1, round(sum(all_minutes) / len(all_minutes))) if all_minutes else 4,
        )
        fields.update(overrides or {})
        if course is None:
            course = Course(id=cid, cover=cover or {"accent": "violet", "source": "course-builder"}, **fields)
            db.add(course)
        else:
            for k, v in fields.items():
                setattr(course, k, v)
            if cover is not None:
                course.cover = cover
        await db.flush()

        existing_chapters = {c.number: c for c in (await db.execute(
            select(Chapter).where(Chapter.course_id == cid))).scalars()}
        counts = {"generated": 0, "outlineOnly": 0, "project": 0}
        for ch in outline.chapters:
            chapter = existing_chapters.get(ch.number)
            if chapter is None:
                chapter = Chapter(course_id=cid, number=ch.number, title=ch.title[:200], summary=ch.summary)
                db.add(chapter)
                await db.flush()
            else:
                chapter.title, chapter.summary = ch.title[:200], ch.summary
            page_specs: list[dict] = []
            for lesson in ch.lessons:
                row = lessons.get(lesson.id)
                if row and row.status == "done" and row.page:
                    page_specs.append(page_fields_from_generated(GeneratedPage.model_validate(row.page), lesson))
                    counts["generated"] += 1
                else:
                    page_specs.append(page_fields_from_outline(lesson))
                    counts["outlineOnly"] += 1
            page_specs.append(page_fields_for_project(ch.project))
            counts["project"] += 1

            existing_pages = {p.page_number: p for p in (await db.execute(
                select(Page).where(Page.chapter_id == chapter.id))).scalars()}
            for n, spec in enumerate(page_specs, start=1):
                page = existing_pages.get(n)
                if page is None:
                    db.add(Page(id=f"{cid}-c{ch.number}-p{n}"[:80], course_id=cid, chapter_id=chapter.id,
                                page_number=n, **spec))
                else:
                    for k, v in spec.items():
                        setattr(page, k, v)
            await db.execute(delete(Page).where(Page.chapter_id == chapter.id, Page.page_number > len(page_specs)))
            chapter.page_count = len(page_specs)
        stale = [c.id for num, c in existing_chapters.items() if num > len(outline.chapters)]
        if stale:
            await db.execute(delete(Page).where(Page.chapter_id.in_(stale)))
            await db.execute(delete(Chapter).where(Chapter.id.in_(stale)))
        job.published_at = datetime.utcnow()
        await db.commit()
        # Keep /search in sync: pages_fts is an external-content FTS5 table.
        from app.api.search import refresh_search_index
        await refresh_search_index(db)
        await db.commit()

    total = counts["generated"] + counts["outlineOnly"] + counts["project"]
    return {
        "courseId": cid,
        "available": available,
        "chapters": len(outline.chapters),
        "pages": total,
        "generatedPages": counts["generated"],
        "outlineOnlyPages": counts["outlineOnly"],
        "projectPages": counts["project"],
        "courseUrl": f"/api/courses/{cid}",
        "publishedAt": datetime.utcnow().isoformat() + "Z",
    }
