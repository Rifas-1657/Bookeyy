"""
scripts/build_python_book.py — write Bookey's own Python book into app/content/python_book.json.

Run once (resumable; finished lessons are kept). Every code example is executed here and
its real output is stored, so a lesson whose code fails is rewritten, never published.

  cd backend && .venv/Scripts/python scripts/build_python_book.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app.content.python_curriculum import CHAPTERS  # noqa: E402
from app.llm.router import LLMError, get_llm_router  # noqa: E402

OUT = ROOT / "app" / "content" / "python_book.json"
TRIES = 3

SYSTEM = """\
You are the author of an original Python book that a voice tutor reads aloud to Tamil-speaking learners.
Write ONE lesson. Be accurate, warm and concrete; teach like a patient senior friend, never like a manual.
Output pure JSON only."""

PROMPT = """\
Chapter {chapter}: {chapter_title} ({chapter_summary})
Lesson: {lesson}
Earlier lessons in this chapter: {before}

Return JSON with exactly these keys:
{{
 "narration_english": "130-170 words, spoken English, no code symbols read out, no markdown",
 "narration_tanglish": "the same teaching in natural spoken Tanglish, 130-170 words",
 "paragraphs": ["3 or 4 short paragraphs in clear English that teach the lesson on the page"],
 "key_terms": ["3-5 exact words or phrases that appear in the narration"],
 "code": "a short complete Python 3 example (3-14 lines) that prints its result",
 "diagram": {{"title": "...", "caption": "...", "steps": ["3-5 short labels, each under 30 characters, showing the flow of the idea"]}},
 "practice": ["2 or 3 small tasks that change or extend the code"],
 "quiz": {{"question": "...", "options": ["4 options"], "answer": 0, "why": "one sentence"}},
 "analogies": {{"cricket": "one sentence", "movies": "one sentence", "neutral": "one sentence"}}
}}

Tanglish means Tamil written in English letters, with technical words left in English. Sound like this:
"Okay, ippo variable-na enna nu paapom. Oru box-ku label ottura maari dhaan idhu. Label-la score nu
ezhudhinaa, andha box-kulla 10 vechukkalam. Python andha label-a paathu value-a eduthu kudukkum."

Code rules: standard library only; no input(), no network, no time.sleep; deterministic output
(seed random, use fixed dates); files only in the current folder; it runs with no command-line
arguments, so give argparse an explicit list like parse_args(["--name", "Asha"]). {extra}"""


def run_code(source: str) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        try:
            r = subprocess.run([sys.executable, "-I", "-c", source], cwd=tmp, capture_output=True,
                               text=True, timeout=10, stdin=subprocess.DEVNULL, encoding="utf-8")
        except subprocess.TimeoutExpired:
            return False, "timed out"
    return (r.returncode == 0 and bool(r.stdout.strip())), (r.stdout if r.returncode == 0 else r.stderr[-400:])


def check(d: dict) -> str | None:
    for k in ("narration_english", "narration_tanglish", "paragraphs", "key_terms", "code", "diagram",
              "practice", "quiz", "analogies"):
        if not d.get(k):
            return f"missing {k}"
    # Tamil packs more into a word, so Tanglish runs shorter than the same English.
    for k, least in (("narration_english", 90), ("narration_tanglish", 60)):
        if len(d[k].split()) < least:
            return f"{k} has only {len(d[k].split())} words, write at least {least + 30}"
    if re.search(r"[஀-௿]", d["narration_tanglish"]):
        return "narration_tanglish must use English letters only"
    q = d["quiz"]
    if len(q.get("options", [])) != 4 or q.get("answer") not in (0, 1, 2, 3):
        return "quiz needs 4 options and an answer index"
    if len(d["diagram"].get("steps", [])) < 2:
        return "diagram needs steps"
    return None


async def write_lesson(router, ch, idx: int) -> dict:
    number, title, summary, _tier, _track, lessons = ch
    extra = ""
    for _ in range(TRIES):
        prompt = PROMPT.format(chapter=number, chapter_title=title, chapter_summary=summary, lesson=lessons[idx],
                               before=", ".join(lessons[:idx]) or "none", extra=extra)
        resp = await router.generate_json(prompt=prompt, schema={}, system=SYSTEM, temperature=0.4,
                                          max_tokens=4000, timeout=90)
        if resp.provider == "mock":
            raise LLMError("mock provider answered", provider="mock", retryable=False)
        try:
            d = json.loads(re.sub(r"^```\w*\s*|```\s*$", "", resp.text.strip()))
        except json.JSONDecodeError:
            extra = "Your last answer was not valid JSON."
            continue
        problem = check(d)
        if problem:
            extra = f"Your last answer was rejected: {problem}."
            continue
        ok, out = run_code(d["code"])
        if not ok:
            extra = f"Your last code failed when run: {out}"
            continue
        d.update(title=lessons[idx], expected_output=out.rstrip("\n"), provider=resp.provider)
        return d
    raise LLMError(f"could not write {lessons[idx]!r}: {extra}", provider="book", retryable=False)


async def main() -> int:
    book: dict = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    router = get_llm_router()
    total = sum(len(c[5]) for c in CHAPTERS)
    failed = []
    for ch in CHAPTERS:
        for i, lesson in enumerate(ch[5]):
            key = f"{ch[0]}.{i + 1}"
            if key in book:
                continue
            for wait in (0, 65, 130):
                await asyncio.sleep(wait)
                try:
                    book[key] = await write_lesson(router, ch, i)
                    break
                except LLMError as exc:
                    print(f"retry {key}: {str(exc)[:160]}", flush=True)
            if key in book:
                OUT.write_text(json.dumps(book, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"{len(book)}/{total} {key} {lesson} [{book[key]['provider']}]", flush=True)
            else:
                failed.append(key)
    print(f"done: {len(book)}/{total}; failed: {failed}", flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
