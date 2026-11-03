"""
app/personalization/teaching_style.py — R3 contextual bandit for HOW the tutor explains.

Arms (12) = explanation style × depth
    style: analogy-first | step-by-step | example-first | visual-first
    depth: simpler | standard | deeper
    arm index = style_index * 3 + depth_index

Context (discretised into a bucket):
    level (survey: new / basics / projects) × mastery band of the concept (low < 0.4,
    mid < 0.7, high) × language (tanglish / english) × recent confusion (≥ 2 "simpler" /
    "repeat" requests in the last 30 min).        bucket key: "ts:<level>:<band>:<lang>:<0|1>"

Policy = Thompson sampling with Beta posteriors per (bucket, arm), backed off to the learner's
pooled posterior across all buckets ("ts:all"), plus a small context prior:
    α = prior_α(ctx) + (α_bucket − 1) + ½ (α_all − 1),   β likewise
    prior: depth "simpler" +1 α when mastery is low or the learner is confused;
           depth "deeper"  +1 α when mastery is high and not confused;
           depth "standard" +0.5 α otherwise.
    Sample θ ~ Beta(α, β) per arm, pick argmax.  "explain simpler" forces depth = simpler and
    avoids the style that was just shown (it evidently did not land).

Reward (attributed to the last style shown in that course within 60 minutes, max 4 rewards
per explanation): quiz answer right 1 / wrong 0 (w 1), "simpler" 0 (w 1), "repeat" 0 (w 0.5),
page completed 1 (w 0.5), follow-up question 0.3 (w 0.3).  Update: α += w·r, β += w·(1−r),
in both the context bucket and "ts:all".  Stored in `bandit_arms` (per user), so it persists.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

STYLES = ("analogy-first", "step-by-step", "example-first", "visual-first")
DEPTHS = ("simpler", "standard", "deeper")
NUM_ARMS = len(STYLES) * len(DEPTHS)
GLOBAL_BUCKET = "ts:all"
BACKOFF = 0.5
ATTRIBUTION_WINDOW = timedelta(minutes=60)
MAX_REWARDS_PER_SHOWN = 4

# Survey "style" (example / diagram / code) seeds the pooled posterior a little.
SURVEY_STYLE = {"example": "example-first", "diagram": "visual-first", "code": "step-by-step"}

STYLE_INSTRUCTIONS = {
    "analogy-first": "Open with one everyday analogy (use the learner's interests if known), then map it to the idea.",
    "step-by-step": "Explain in short numbered steps, one idea per step, in the order things happen.",
    "example-first": "Start with one small concrete example, then state the general idea it shows.",
    "visual-first": "Describe a simple picture or diagram first (boxes and arrows in words), then explain it.",
}
DEPTH_INSTRUCTIONS = {
    "simpler": "Keep it very simple: short sentences, no new jargon, at most ~90 words.",
    "standard": "Normal depth for this learner.",
    "deeper": "Go one level deeper: add the 'why', an edge case or a common mistake.",
}

REWARDS: dict[str, tuple[float, float]] = {   # kind -> (reward, weight); quiz handled separately
    "simpler": (0.0, 1.0),
    "repeat": (0.0, 0.5),
    "page_complete": (1.0, 0.5),
    "question": (0.3, 0.3),
}


def arm_of(style: str, depth: str) -> int:
    return STYLES.index(style) * len(DEPTHS) + DEPTHS.index(depth)


def arm_parts(arm: int) -> tuple[str, str]:
    return STYLES[arm // len(DEPTHS)], DEPTHS[arm % len(DEPTHS)]


@dataclass
class StyleContext:
    level: str = "new"
    mastery: float = 0.2
    language: str = "tanglish"
    confused: bool = False

    @property
    def band(self) -> str:
        return "low" if self.mastery < 0.4 else "mid" if self.mastery < 0.7 else "high"

    @property
    def bucket(self) -> str:
        return f"ts:{self.level}:{self.band}:{self.language}:{int(self.confused)}"


def context_prior(ctx: StyleContext) -> tuple[list[float], list[float]]:
    alpha, beta = [1.0] * NUM_ARMS, [1.0] * NUM_ARMS
    for arm in range(NUM_ARMS):
        _, depth = arm_parts(arm)
        if depth == "simpler" and (ctx.band == "low" or ctx.confused):
            alpha[arm] += 1.0
        elif depth == "deeper" and ctx.band == "high" and not ctx.confused:
            alpha[arm] += 1.0
        elif depth == "standard" and ctx.band != "low" and not ctx.confused:
            alpha[arm] += 0.5
    return alpha, beta


@dataclass
class StyleChoice:
    arm: int
    style: str
    depth: str
    bucket: str
    expected: float

    @property
    def instruction(self) -> str:
        return f"{STYLE_INSTRUCTIONS[self.style]} {DEPTH_INSTRUCTIONS[self.depth]}"

    def to_dict(self) -> dict:
        return {"arm": self.arm, "style": self.style, "depth": self.depth, "bucket": self.bucket,
                "expected": round(self.expected, 3)}


async def _posteriors(db, user_id: str, bucket: str) -> dict[str, dict[int, tuple[float, float]]]:
    from app.db.models import BanditArm
    rows = (await db.execute(select(BanditArm).where(
        BanditArm.user_id == user_id, BanditArm.topic.in_([bucket, GLOBAL_BUCKET])))).scalars().all()
    out: dict[str, dict[int, tuple[float, float]]] = {bucket: {}, GLOBAL_BUCKET: {}}
    for r in rows:
        if 0 <= r.arm < NUM_ARMS:
            out.setdefault(r.topic, {})[r.arm] = (r.alpha, r.beta)
    return out


def combine(ctx: StyleContext, post: dict, survey_style: str | None = None) -> tuple[list[float], list[float]]:
    alpha, beta = context_prior(ctx)
    pref = SURVEY_STYLE.get(survey_style or "")
    for arm in range(NUM_ARMS):
        if pref and arm_parts(arm)[0] == pref:
            alpha[arm] += 0.5
        ba, bb = post.get(ctx.bucket, {}).get(arm, (1.0, 1.0))
        ga, gb = post.get(GLOBAL_BUCKET, {}).get(arm, (1.0, 1.0))
        alpha[arm] += (ba - 1.0) + BACKOFF * (ga - 1.0)
        beta[arm] += (bb - 1.0) + BACKOFF * (gb - 1.0)
    return alpha, beta


async def choose(db, user_id: str, ctx: StyleContext, *, force_depth: str | None = None,
                 avoid_style: str | None = None, survey_style: str | None = None,
                 rng: random.Random | None = None) -> StyleChoice:
    alpha, beta = combine(ctx, await _posteriors(db, user_id, ctx.bucket), survey_style)
    rng = rng or random.Random()
    best, best_theta = 0, -1.0
    for arm in range(NUM_ARMS):
        style, depth = arm_parts(arm)
        if force_depth and depth != force_depth:
            continue
        if avoid_style and style == avoid_style:
            continue
        theta = rng.betavariate(max(alpha[arm], 0.01), max(beta[arm], 0.01))
        if theta > best_theta:
            best, best_theta = arm, theta
    style, depth = arm_parts(best)
    return StyleChoice(arm=best, style=style, depth=depth, bucket=ctx.bucket,
                       expected=alpha[best] / (alpha[best] + beta[best]))


async def preferred_style(db, user_id: str, ctx: StyleContext) -> dict:
    """The arm with the highest posterior mean for this context (no sampling) — for display."""
    post = await _posteriors(db, user_id, ctx.bucket)
    alpha, beta = combine(ctx, post)
    means = [alpha[i] / (alpha[i] + beta[i]) for i in range(NUM_ARMS)]
    best = max(range(NUM_ARMS), key=lambda i: means[i])
    style, depth = arm_parts(best)
    # Total reward weight observed for this learner (0 → the choice is still just the prior).
    evidence = sum(a + b - 2.0 for a, b in post.get(GLOBAL_BUCKET, {}).values())
    return {"arm": best, "style": style, "depth": depth, "expected": round(means[best], 3),
            "bucket": ctx.bucket, "evidence": round(max(0.0, evidence), 2)}


async def _update(db, user_id: str, topic: str, arm: int, reward: float, weight: float) -> None:
    from app.db.models import BanditArm
    row = (await db.execute(select(BanditArm).where(
        BanditArm.user_id == user_id, BanditArm.topic == topic, BanditArm.arm == arm))).scalar_one_or_none()
    if row is None:
        row = BanditArm(user_id=user_id, topic=topic, arm=arm, alpha=1.0, beta=1.0, updated_at=datetime.utcnow())
        db.add(row)
    r = max(0.0, min(1.0, reward))
    row.alpha += weight * r
    row.beta += weight * (1.0 - r)
    row.updated_at = datetime.utcnow()


async def reward(db, user_id: str, bucket: str, arm: int, value: float, weight: float = 1.0) -> None:
    await _update(db, user_id, bucket, arm, value, weight)
    if bucket != GLOBAL_BUCKET:
        await _update(db, user_id, GLOBAL_BUCKET, arm, value, weight)


async def record_shown(db, user_id: str, course_id: str, page_id: str | None, choice: StyleChoice,
                       reason: str = "answer") -> None:
    from app.db.models import LearnerEvent
    db.add(LearnerEvent(user_id=user_id, course_id=course_id, kind="style_shown", page_id=page_id,
                        meta={**choice.to_dict(), "reason": reason, "rewards": 0},
                        created_at=datetime.utcnow()))


async def last_shown(db, user_id: str, course_id: str):
    from app.db.models import LearnerEvent
    since = datetime.utcnow() - ATTRIBUTION_WINDOW
    return (await db.execute(select(LearnerEvent).where(
        LearnerEvent.user_id == user_id, LearnerEvent.course_id == course_id,
        LearnerEvent.kind == "style_shown", LearnerEvent.created_at >= since,
    ).order_by(LearnerEvent.created_at.desc()).limit(1))).scalar_one_or_none()


async def reward_from_evidence(db, user_id: str, course_id: str, kind: str, correct) -> bool:
    """Credit the last explanation style with this evidence. True if a reward was applied."""
    if kind in ("quiz_answer", "review_answer"):
        value, weight = (1.0 if correct else 0.0), 1.0
    elif kind in REWARDS:
        value, weight = REWARDS[kind]
    else:
        return False
    shown = await last_shown(db, user_id, course_id)
    if shown is None:
        return False
    meta = dict(shown.meta or {})
    if int(meta.get("rewards", 0)) >= MAX_REWARDS_PER_SHOWN or "arm" not in meta:
        return False
    await reward(db, user_id, str(meta.get("bucket") or GLOBAL_BUCKET), int(meta["arm"]), value, weight)
    meta["rewards"] = int(meta.get("rewards", 0)) + 1
    shown.meta = meta
    return True


async def choose_for_answer(user_id: str | None, course_id: str | None, page_id: str | None,
                            question: str, *, simpler: bool = False) -> StyleChoice | None:
    """Tutor hook: pick (and record) the style for one answer. None when there is no learner."""
    if not user_id or not course_id or user_id == "anonymous":
        return None
    from app.db.database import AsyncSessionLocal
    from app.db.models import User
    from app.personalization import learner_model as lm

    async with AsyncSessionLocal() as db:
        if await db.get(User, user_id) is None:
            return None
        prefs = await lm.learner_preferences(db, user_id, course_id)
        page = await lm.load_page_info(db, course_id, page_id) if page_id else None
        mastery = lm.LEVEL_PRIOR.get(prefs["level"], lm.DEFAULT_PRIOR)
        if page is not None:
            # Evidence first: the question itself (and "simpler") says something about the learner.
            await lm.apply_evidence(db, user_id, course_id, [{
                "kind": "simpler" if simpler else "question", "pageId": page.page_id, "text": question,
            }], level=prefs["level"])
            from app.db.models import LearnerConcept
            ids = lm.mentioned_concepts(question, page.concepts) or [c for c, _ in page.concepts]
            rows = (await db.execute(select(LearnerConcept).where(
                LearnerConcept.user_id == user_id, LearnerConcept.course_id == course_id,
                LearnerConcept.concept_id.in_(ids)))).scalars().all()
            if rows:
                mastery = min(r.mastery for r in rows)
        confused = (await lm.recent_confusion(db, user_id, course_id)) >= 2
        ctx = StyleContext(level=prefs["level"], mastery=mastery, language=prefs["language"], confused=confused)
        avoid = None
        if simpler:
            prev = await last_shown(db, user_id, course_id)
            avoid = (prev.meta or {}).get("style") if prev is not None else None
        choice = await choose(db, user_id, ctx, force_depth="simpler" if simpler else None,
                              avoid_style=avoid, survey_style=prefs.get("style"))
        await record_shown(db, user_id, course_id, page_id, choice, reason="simpler" if simpler else "answer")
        await db.commit()
        return choice
