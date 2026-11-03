"""
app/personalization/learner_model.py — R3 learner model (per user, per course, per concept).

Concepts
--------
A page's concepts are its key terms (generated books: the lesson's key terms; seeded books:
the authored `key_terms`), max 6 per page; a page with no key terms has one concept, its
title.  The concept id is a slug of the term, so the same term on two pages is one concept.

Mastery = Bayesian Knowledge Tracing (Corbett & Anderson 1995)
---------------------------------------------------------------
Each concept keeps P(L) = probability the learner knows it.  Prior P(L0) depends on the
learner's self-reported level (survey): new 0.15, basics 0.30, projects 0.45.

Graded observation (quiz / review answer):
    correct:   P(L|obs) = P(L)(1-S) / (P(L)(1-S) + (1-P(L))G)
    incorrect: P(L|obs) = P(L)S     / (P(L)S     + (1-P(L))(1-G))
    then learning: P(L) = P(L|obs) + (1 - P(L|obs)) * T
  S (slip) = 0.10, T (learn) = 0.15, G (guess) = 1/len(options) for multiple choice, 0.10
  for short answers.  A correct answer that took longer than SLOW_MS counts with weight 0.6.

Soft evidence is the same update, blended in with a weight w < 1
    P(L) <- P(L) + w * (P(L|obs) - P(L)), and the learning step uses T * w:
      code run succeeded  correct,   w = 0.5
      code run error      incorrect, w = 0.3
      "explain simpler"   incorrect, w = 0.25   (also counts as confusion)
      question asked      incorrect, w = 0.15   (also counts as confusion)
      "repeat"            incorrect, w = 0.15   (also counts as confusion)
      re-listen page      incorrect, w = 0.10
      page completed      no observation, learning step only (T * 0.5)
Evidence with no explicit concept goes to the page concepts it mentions; if it mentions
none, to all of the page's concepts at half weight.

Status: mastered >= 0.80, learning >= 0.50, weak < 0.50 (only once there is evidence),
otherwise "new".

Spaced review (FSRS-5, app/personalization/fsrs.py) cards are keyed "<course>::<concept>":
a wrong answer reviews the card as "again" (it becomes due the next day); a right answer
on a concept that already has a card reviews it "good" (or "hard" if it was slow), and a
right answer that still leaves mastery < 0.5 opens a card with "hard".
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

log = logging.getLogger(__name__)

LEVEL_PRIOR = {"new": 0.15, "basics": 0.30, "projects": 0.45}
DEFAULT_PRIOR = 0.20
P_SLIP = 0.10
P_TRANSIT = 0.15
GUESS_SHORT = 0.10
SLOW_MS = 45_000
MAX_CONCEPTS_PER_PAGE = 6
MASTERED, LEARNING = 0.80, 0.50
CONFUSION_WINDOW = timedelta(minutes=30)

# kind -> (observed correct?, weight, counts as confusion)
SOFT_EVIDENCE: dict[str, tuple[bool, float, bool]] = {
    "simpler": (False, 0.25, True),
    "question": (False, 0.15, True),
    "repeat": (False, 0.15, True),
    "relisten": (False, 0.10, False),
}
CLIENT_EVENT_KINDS = {"code_run", "simpler", "question", "repeat", "relisten", "page_complete"}


def clamp(p: float, lo: float = 0.01, hi: float = 0.99) -> float:
    return max(lo, min(hi, p))


def bkt_update(p: float, correct: bool, *, guess: float = 0.25, slip: float = P_SLIP,
               transit: float = P_TRANSIT, weight: float = 1.0) -> float:
    """One (optionally down-weighted) BKT observation followed by the learning step."""
    p = clamp(p)
    if correct:
        post = p * (1 - slip) / (p * (1 - slip) + (1 - p) * guess)
    else:
        post = p * slip / (p * slip + (1 - p) * (1 - guess))
    w = max(0.0, min(1.0, weight))
    blended = p + w * (post - p)
    return clamp(blended + (1 - blended) * transit * w)


def learn_step(p: float, weight: float = 0.5) -> float:
    p = clamp(p)
    return clamp(p + (1 - p) * P_TRANSIT * weight)


def status_for(mastery: float, has_evidence: bool) -> str:
    if mastery >= MASTERED:
        return "mastered"
    if mastery >= LEARNING:
        return "learning"
    return "weak" if has_evidence else "new"


_SLUG = re.compile(r"[^a-z0-9]+")


def concept_id_for(term: str) -> str:
    return _SLUG.sub("-", (term or "").lower()).strip("-")[:100]


def page_concepts(title: str, key_terms: list | None) -> list[tuple[str, str]]:
    """[(concept_id, label)] for one page."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for term in key_terms or []:
        label = str(term).strip()
        cid = concept_id_for(label)
        if cid and cid not in seen:
            seen.add(cid)
            out.append((cid, label[:200]))
        if len(out) >= MAX_CONCEPTS_PER_PAGE:
            break
    if not out and concept_id_for(title):
        out.append((concept_id_for(title), (title or "")[:200]))
    return out


def mentioned_concepts(text: str, concepts: list[tuple[str, str]]) -> list[str]:
    """Concept ids whose label (or every word of it) occurs in `text`."""
    t = " " + _SLUG.sub(" ", (text or "").lower()) + " "
    hits = []
    for cid, label in concepts:
        words = [w for w in _SLUG.sub(" ", label.lower()).split() if w]
        if not words:
            continue
        phrase = " " + " ".join(words) + " "
        if phrase in t or (len(words) > 1 and all(f" {w} " in t for w in words if len(w) > 2)):
            hits.append(cid)
    return hits


# ---------------------------------------------------------------------------
# Page data
# ---------------------------------------------------------------------------

@dataclass
class PageInfo:
    course_id: str
    page_id: str
    title: str
    chapter_number: int
    page_number: int
    key_terms: list
    paragraphs: list
    narration: str
    code_example: dict
    practice_prompts: list

    @property
    def concepts(self) -> list[tuple[str, str]]:
        return page_concepts(self.title, self.key_terms)

    @property
    def text(self) -> str:
        return " ".join([self.narration or ""] + [str(p) for p in (self.paragraphs or [])])


async def load_page_info(db, course_id: str, page_id: str) -> PageInfo | None:
    from app.db.models import Chapter, Page
    row = (await db.execute(
        select(Page, Chapter.number).join(Chapter, Chapter.id == Page.chapter_id)
        .where(Page.id == page_id, Page.course_id == course_id)
    )).first()
    if row is None:
        return None
    page, ch_no = row
    return PageInfo(course_id=course_id, page_id=page.id, title=page.title, chapter_number=ch_no,
                    page_number=page.page_number, key_terms=list(page.key_terms or []),
                    paragraphs=list(page.paragraphs or []), narration=page.narration_text or "",
                    code_example=dict(page.code_example or {}),
                    practice_prompts=list(page.practice_prompts or []))


async def learner_preferences(db, user_id: str, course_id: str) -> dict:
    """{level, language} from the learner's course survey (falls back to the course language)."""
    from app.db.models import Course, CourseSurvey
    survey = (await db.execute(select(CourseSurvey).where(
        CourseSurvey.user_id == user_id, CourseSurvey.course_id == course_id))).scalar_one_or_none()
    answers = dict(survey.answers or {}) if survey else {}
    lang = answers.get("language")
    if lang not in ("tanglish", "english"):
        course = await db.get(Course, course_id)
        lang = "tanglish" if course is None or (course.language or "").lower() in ("tanglish", "python") else "english"
    level = answers.get("level") if answers.get("level") in LEVEL_PRIOR else "new"
    style = answers.get("style") if isinstance(answers.get("style"), str) else None
    return {"level": level, "language": lang, "style": style}


# ---------------------------------------------------------------------------
# Concept rows
# ---------------------------------------------------------------------------

async def ensure_concepts(db, user_id: str, page: PageInfo, level: str = "new") -> dict:
    """concept_id -> LearnerConcept for every concept of `page` (created at the level prior)."""
    from app.db.models import LearnerConcept
    concepts = page.concepts
    ids = [cid for cid, _ in concepts]
    rows = {r.concept_id: r for r in (await db.execute(select(LearnerConcept).where(
        LearnerConcept.user_id == user_id, LearnerConcept.course_id == page.course_id,
        LearnerConcept.concept_id.in_(ids)))).scalars()} if ids else {}
    for cid, label in concepts:
        if cid not in rows:
            row = LearnerConcept(user_id=user_id, course_id=page.course_id, concept_id=cid, label=label,
                                 page_id=page.page_id, chapter_number=page.chapter_number,
                                 mastery=LEVEL_PRIOR.get(level, DEFAULT_PRIOR), attempts=0, correct=0,
                                 confusion=0, updated_at=datetime.utcnow())
            db.add(row)
            rows[cid] = row
    if any(r.id is None for r in rows.values()):
        await db.flush()
    return rows


def _targets(page: PageInfo, rows: dict, concept_ids: list[str] | None, text: str) -> tuple[list[str], float]:
    """(concept ids, weight multiplier) for one piece of evidence."""
    valid = [c for c in (concept_ids or []) if c in rows]
    if valid:
        return valid, 1.0
    hits = mentioned_concepts(text, page.concepts) if text else []
    if hits:
        return hits, 1.0
    return list(rows.keys()), 0.5


async def apply_evidence(db, user_id: str, course_id: str, events: list[dict], *, level: str | None = None,
                         record_reward: bool = True) -> list[dict]:
    """Apply a batch of evidence events; returns the updated concepts.

    Event: {kind, pageId, conceptIds?, correct?, responseMs?, text?, guess?, meta?}
    kind ∈ quiz_answer | review_answer | code_run | simpler | question | repeat | relisten | page_complete
    """
    from app.db.models import LearnerEvent
    from app.personalization import teaching_style

    if level is None:
        level = (await learner_preferences(db, user_id, course_id))["level"]
    pages: dict[str, PageInfo | None] = {}
    touched: dict[str, object] = {}
    now = datetime.utcnow()
    for ev in events:
        kind = str(ev.get("kind") or "")
        page_id = str(ev.get("pageId") or "")
        if not kind or not page_id:
            continue
        if page_id not in pages:
            pages[page_id] = await load_page_info(db, course_id, page_id)
        page = pages[page_id]
        if page is None:
            continue
        rows = await ensure_concepts(db, user_id, page, level)
        text = str(ev.get("text") or "")[:500]
        targets, mult = _targets(page, rows, ev.get("conceptIds"), text)
        correct = ev.get("correct")
        for cid in targets:
            row = rows[cid]
            if kind in ("quiz_answer", "review_answer"):
                slow = bool(correct) and (ev.get("responseMs") or 0) > SLOW_MS
                row.mastery = bkt_update(row.mastery, bool(correct), guess=float(ev.get("guess") or GUESS_SHORT),
                                         weight=(0.6 if slow else 1.0) * mult)
                row.attempts += 1
                row.correct += 1 if correct else 0
            elif kind == "code_run":
                row.mastery = bkt_update(row.mastery, bool(correct), guess=0.3,
                                         weight=(0.5 if correct else 0.3) * mult)
            elif kind == "page_complete":
                row.mastery = learn_step(row.mastery, 0.5 * mult)
            elif kind in SOFT_EVIDENCE:
                obs, w, confusion = SOFT_EVIDENCE[kind]
                row.mastery = bkt_update(row.mastery, obs, guess=0.25, weight=w * mult)
                if confusion:
                    row.confusion += 1
            else:
                continue
            row.last_seen_at = now
            row.updated_at = now
            touched[cid] = row
        db.add(LearnerEvent(user_id=user_id, course_id=course_id, kind=kind[:30], page_id=page_id,
                            concept_id=(targets[0] if len(targets) == 1 else None),
                            correct=(bool(correct) if correct is not None else None),
                            value=(float(ev["responseMs"]) if ev.get("responseMs") is not None else None),
                            meta={"concepts": targets, **(ev.get("meta") or {})}, created_at=now))
        if record_reward:
            await teaching_style.reward_from_evidence(db, user_id, course_id, kind, correct)
        if kind in ("quiz_answer", "review_answer"):
            for cid in targets:
                await _schedule_review(db, user_id, course_id, cid, bool(correct), rows[cid].mastery,
                                       slow=(ev.get("responseMs") or 0) > SLOW_MS, review=kind == "review_answer")
    await db.commit()
    return [concept_dict(r) for r in touched.values()]


def fsrs_topic(course_id: str, concept_id: str) -> str:
    return f"{course_id}::{concept_id}"[:120]


async def _schedule_review(db, user_id: str, course_id: str, concept_id: str, correct: bool, mastery: float,
                           *, slow: bool, review: bool) -> None:
    from app.db.models import FSRSCardModel
    from app.personalization.fsrs import load_card, save_card
    topic = fsrs_topic(course_id, concept_id)
    exists = (await db.execute(select(FSRSCardModel.id).where(
        FSRSCardModel.user_id == user_id, FSRSCardModel.topic == topic))).first() is not None
    if not correct:
        rating = "again"
    elif exists or review:
        rating = "hard" if slow else "good"
    elif mastery < LEARNING:
        rating = "hard"
    else:
        return
    card = await load_card(db, user_id, topic)
    await save_card(db, card.review(rating), commit=False)


def concept_dict(row) -> dict:
    has_evidence = bool(row.attempts or row.confusion or row.last_seen_at)
    return {
        "id": row.concept_id, "label": row.label, "pageId": row.page_id, "chapter": row.chapter_number,
        "mastery": round(float(row.mastery), 3), "attempts": row.attempts, "correct": row.correct,
        "confusion": row.confusion, "status": status_for(row.mastery, has_evidence),
        "lastSeenAt": row.last_seen_at.isoformat() if row.last_seen_at else None,
    }


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------

async def recent_confusion(db, user_id: str, course_id: str, window: timedelta = CONFUSION_WINDOW) -> int:
    from app.db.models import LearnerEvent
    since = datetime.utcnow() - window
    rows = (await db.execute(select(LearnerEvent.kind).where(
        LearnerEvent.user_id == user_id, LearnerEvent.course_id == course_id,
        LearnerEvent.created_at >= since,
        LearnerEvent.kind.in_(["simpler", "repeat", "question"]),
    ))).all()
    # questions are normal; only count them half
    return sum(1 if k in ("simpler", "repeat") else 0.5 for (k,) in rows)


async def due_reviews(db, user_id: str, course_id: str | None = None) -> list[dict]:
    """Concepts whose FSRS card is due (retrievability < 0.9), most overdue first."""
    from app.db.models import Course, FSRSCardModel, LearnerConcept
    from app.personalization.fsrs import FSRSCard

    q = select(FSRSCardModel).where(FSRSCardModel.user_id == user_id, FSRSCardModel.topic.like("%::%"))
    if course_id:
        q = q.where(FSRSCardModel.topic.like(f"{course_id}::%"))
    out = []
    titles: dict[str, str] = {}
    for row in (await db.execute(q)).scalars():
        cid, _, concept = row.topic.partition("::")
        lrd = row.last_review_date.date() if row.last_review_date and hasattr(row.last_review_date, "date") else row.last_review_date
        card = FSRSCard(user_id=user_id, topic=row.topic, stability=row.stability, difficulty=row.difficulty,
                        last_review_date=lrd, review_count=row.review_count, lapses=row.lapses)
        if not card.is_due:
            continue
        concept_row = (await db.execute(select(LearnerConcept).where(
            LearnerConcept.user_id == user_id, LearnerConcept.course_id == cid,
            LearnerConcept.concept_id == concept))).scalar_one_or_none()
        if concept_row is None:
            continue
        if cid not in titles:
            course = await db.get(Course, cid)
            titles[cid] = course.title if course else cid
        out.append({"courseId": cid, "courseTitle": titles[cid], "conceptId": concept, "label": concept_row.label,
                    "pageId": concept_row.page_id, "chapter": concept_row.chapter_number,
                    "mastery": round(float(concept_row.mastery), 3), "lapses": row.lapses,
                    "retrievability": round(card.retrievability, 3),
                    "lastReviewed": lrd.isoformat() if lrd else None})
    out.sort(key=lambda r: (r["retrievability"], r["mastery"]))
    return out


async def course_summary(db, user_id: str, course_id: str) -> dict:
    from app.db.models import Chapter, LearnerConcept
    from app.personalization import teaching_style

    prefs = await learner_preferences(db, user_id, course_id)
    rows = (await db.execute(select(LearnerConcept).where(
        LearnerConcept.user_id == user_id, LearnerConcept.course_id == course_id))).scalars().all()
    concepts = sorted((concept_dict(r) for r in rows), key=lambda c: (c["chapter"] or 0, c["label"].lower()))
    chapter_titles = {c.number: c.title for c in (await db.execute(
        select(Chapter).where(Chapter.course_id == course_id))).scalars()}
    chapters: dict[int, list[dict]] = {}
    for c in concepts:
        chapters.setdefault(c["chapter"] or 0, []).append(c)
    seen = [c for c in concepts if c["status"] != "new"]
    weak = sorted((c for c in seen if c["status"] == "weak" or c["confusion"] >= 2 and c["mastery"] < MASTERED),
                  key=lambda c: c["mastery"])[:5]
    mean = round(sum(c["mastery"] for c in seen) / len(seen), 3) if seen else None
    confusion = await recent_confusion(db, user_id, course_id)
    style = await teaching_style.preferred_style(db, user_id, teaching_style.StyleContext(
        level=prefs["level"], mastery=mean if mean is not None else LEVEL_PRIOR.get(prefs["level"], DEFAULT_PRIOR),
        language=prefs["language"], confused=confusion >= 2))
    return {
        "courseId": course_id,
        "level": prefs["level"],
        "language": prefs["language"],
        "overallMastery": mean,
        "concepts": concepts,
        "chapters": [
            {"number": n, "title": chapter_titles.get(n, f"Chapter {n}"),
             "mastery": round(sum(c["mastery"] for c in cs) / len(cs), 3),
             "seen": sum(1 for c in cs if c["status"] != "new"), "total": len(cs),
             "mastered": sum(1 for c in cs if c["status"] == "mastered")}
            for n, cs in sorted(chapters.items())
        ],
        "weak": weak,
        "strong": [c for c in concepts if c["status"] == "mastered"][:5],
        "recentConfusion": confusion,
        "style": style,
        "dueReviews": await due_reviews(db, user_id, course_id),
        "model": "bkt",
    }


async def overview(db, user_id: str) -> list[dict]:
    """One line per course the learner has evidence for."""
    from app.db.models import Course, LearnerConcept
    rows = (await db.execute(select(LearnerConcept).where(LearnerConcept.user_id == user_id))).scalars().all()
    by_course: dict[str, list] = {}
    for r in rows:
        by_course.setdefault(r.course_id, []).append(r)
    due = await due_reviews(db, user_id)
    out = []
    for cid, rs in by_course.items():
        course = await db.get(Course, cid)
        seen = [r for r in rs if r.attempts or r.confusion or r.last_seen_at]
        out.append({"courseId": cid, "title": course.title if course else cid, "concepts": len(rs),
                    "seen": len(seen),
                    "mastery": round(sum(r.mastery for r in seen) / len(seen), 3) if seen else None,
                    "mastered": sum(1 for r in rs if r.mastery >= MASTERED),
                    "due": sum(1 for d in due if d["courseId"] == cid)})
    out.sort(key=lambda c: c["title"].lower())
    return out


async def tutor_learner_note(user_id: str | None, course_id: str | None, page_id: str | None = None) -> str:
    """Short learner summary for the tutor prompt — THIS learner's data only. '' when unknown."""
    if not user_id or not course_id:
        return ""
    try:
        from app.db.database import AsyncSessionLocal
        from app.db.models import LearnerConcept
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(LearnerConcept).where(
                LearnerConcept.user_id == user_id, LearnerConcept.course_id == course_id))).scalars().all()
            confusion = await recent_confusion(db, user_id, course_id)
        seen = [r for r in rows if r.attempts or r.confusion or r.last_seen_at]
        if not seen and confusion < 1:
            return ""
        weak = sorted((r for r in seen if r.mastery < LEARNING), key=lambda r: r.mastery)[:3]
        strong = sorted((r for r in seen if r.mastery >= MASTERED), key=lambda r: -r.mastery)[:3]
        here = [r for r in rows if page_id and r.page_id == page_id]
        parts = []
        if weak:
            parts.append("weak concepts: " + ", ".join(f"{r.label} ({round(r.mastery * 100)}%)" for r in weak))
        if strong:
            parts.append("already strong: " + ", ".join(r.label for r in strong))
        if here:
            parts.append("this page's concepts: " + ", ".join(f"{r.label} ({round(r.mastery * 100)}%)" for r in here[:4]))
        if confusion >= 2:
            parts.append("asked for simpler/repeat explanations recently — keep it gentle and concrete")
        return "; ".join(parts)
    except Exception:
        log.debug("learner note failed", exc_info=True)
        return ""
