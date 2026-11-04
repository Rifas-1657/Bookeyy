"""
app/services/mock_book_service.py

MockBookService: drives the realtime book protocol for B0.
On start_page, replays the seeded page as:
  session_started → diagram_spec → text_chunk (per word) →
  [cue / diagram_draw / highlight at anchored words] →
  page_complete

Also handles:
  pause, resume, seek, speed
  ask_question (scripted answers, scope_refused for non-Python, sources)
  run_code (seeded examples → run_result with trace)
  submit_survey (acknowledgement)
  audio_in (no-op in B0 — STT not connected)
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional

from app.contracts.models import (
    BOOK_PROTOCOL_VERSION,
    AnswerChunkEvent,
    AnswerChunkPayload,
    AudioChunkEvent,
    AudioChunkPayload,
    CueEvent,
    CuePayload,
    DiagramDrawEvent,
    DiagramDrawPayload,
    DiagramSpecEvent,
    DiagramSpecPayload,
    DiagramNode,
    DiagramSpec,
    ErrorEvent,
    ErrorPayload,
    PageCompleteEvent,
    PageCompletePayload,
    QuestionReceivedEvent,
    QuestionReceivedPayload,
    RunResultEvent,
    RunResultPayload,
    RunTrace,
    RunTraceStep,
    RunTraceVariable,
    ScopeRefusedEvent,
    ScopeRefusedPayload,
    SessionStartedEvent,
    SessionStartedPayload,
    SourceItem,
    SourcesEvent,
    SourcesPayload,
    TextChunkEvent,
    TextChunkPayload,
    TutorAttachments,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
SendFn = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


# ---------------------------------------------------------------------------
# Scripted answers (Tanglish tutor)
# ---------------------------------------------------------------------------

_SCRIPTED_ANSWERS: dict[str, list[str]] = {
    "variable": [
        "Oru variable na oru labeled box maari! ",
        "Box mela irukkura label thaan variable name, ",
        "box-kulla irukkura value thaan actual data. ",
        "Python-la x = 10 nu ezhudhina, 'x' andha box-oda peru, ",
        "10 adha kulla irukkura value. ",
        "Idha neengal eppovume maatha mudiyum — adha thaan re-assignment!",
    ],
    "int": [
        "int na integer, adhaavadhu whole number. ",
        "7, -3, 100 ellaam int thaan. ",
        "Decimal point illatha ellaa numbers-um Python-la int aagidum. ",
        "type(7) nu kettaal Python '<class int>' nu solludhu.",
    ],
    "float": [
        "float na floating-point number, adhaavadhu decimal irukkura number. ",
        "3.14, 2.5, -0.001 ellaam float. ",
        "Oru int-um float-um serndhu calculate panna, result eppovume float aagum. ",
        "Udharanathukku 7 + 0.5 na 7.5 varum, adhu float.",
    ],
    "str": [
        "str na string — quotes kulla irukkura text. ",
        "Single quotes illa double quotes, rendume sari. ",
        "name = 'Priya' nu ezhudhina, name oru str. ",
        "Strings-a + vachu join pannalaam, adha concatenation nu solluvaanga.",
    ],
    "type": [
        "type() function oru value-oda type-a kaattum. ",
        "type(10) na <class 'int'>, type('hello') na <class 'str'>. ",
        "Type-a therinjukka venum pothu idha use pannunga!",
    ],
    "print": [
        "print() function screen-la output kaattum. ",
        "print(x) nu ezhudhina x-oda value screen-la varum. ",
        "Niraiya values comma pottu kudukkalaam: print(x, y, z) — naduvula space automatic-aa varum.",
    ],
    "input": [
        "input() user-kitta oru question kekkum. ",
        "User type pannadhu ellaam string-aa varum. ",
        "Number venumna, int(input('Enter age: ')) nu convert pannanum, ",
        "illa na age + 1 panna error varum!",
    ],
    "default": [
        "Idu pathi sollanum naa... ",
        "Python-la idhu romba important concept. ",
        "Unga question-a paartha, neenga padicha chapter-la idhu irukkum. ",
        "Andha page-a thirumba paathu, doubt vandhaa kelungal!",
    ],
}

_SCOPE_REFUSE_MSG = (
    "Naan ippo Python from Zero course mattum help panna mudiyum. "
    "Unga question idha pathi illaa maadhiri theriyudhu. "
    "Course-la irukkura Python topics — variables, types, operators, conditions — "
    "pathi enna venum naalum kelu!"
)

_PYTHON_TOPICS = {
    "variable", "variables", "int", "float", "str", "string", "bool",
    "boolean", "list", "type", "print", "input", "assign", "assignment",
    "name", "naming", "snake_case", "operator", "operators", "conversion",
    "convert", "code", "python", "value", "values", "number", "numbers",
    "text", "concatenation", "index", "length", "len",
}


def _is_python_question(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _PYTHON_TOPICS)


def _pick_answer_chunks(text: str) -> list[str]:
    lower = text.lower()
    for keyword, chunks in _SCRIPTED_ANSWERS.items():
        if keyword != "default" and keyword in lower:
            return chunks
    return _SCRIPTED_ANSWERS["default"]


# ---------------------------------------------------------------------------
# Seeded code examples (mirrors lib/runner.ts)
# ---------------------------------------------------------------------------

_SEEDED_EXAMPLES: dict[str, dict[str, Any]] = {
    "x = 10\nprint(x)\nprint(type(x))": {
        "stdout": "10\n<class 'int'>\n",
        "stderr": "",
        "status": "success",
        "duration_ms": 8.0,
        "trace": {
            "exampleId": "var_basic",
            "steps": [
                {"logicalLine": 1, "output": None, "variables": [{"name": "x", "value": "10", "type": "int"}]},
                {"logicalLine": 2, "output": "10", "variables": [{"name": "x", "value": "10", "type": "int"}]},
                {"logicalLine": 3, "output": "<class 'int'>", "variables": [{"name": "x", "value": "10", "type": "int"}]},
            ],
            "stdin": [],
        },
    },
    "score = 10\nscore = score + 2\nprint(score)": {
        "stdout": "12\n",
        "stderr": "",
        "status": "success",
        "duration_ms": 6.0,
        "trace": {
            "exampleId": "assignment",
            "steps": [
                {"logicalLine": 1, "output": None, "variables": [{"name": "score", "value": "10", "type": "int"}]},
                {"logicalLine": 2, "output": None, "variables": [{"name": "score", "value": "12", "type": "int"}]},
                {"logicalLine": 3, "output": "12", "variables": [{"name": "score", "value": "12", "type": "int"}]},
            ],
            "stdin": [],
        },
    },
    'age = 21\nprice = 3.5\nname = "Priya"\nready = True\nscores = [90, 85, 77]\nprint(type(age), type(price))\nprint(type(name), type(ready))\nprint(type(scores))': {
        "stdout": "<class 'int'> <class 'float'>\n<class 'str'> <class 'bool'>\n<class 'list'>\n",
        "stderr": "",
        "status": "success",
        "duration_ms": 10.0,
        "trace": None,
    },
    'total_score = 42\n_hidden = "ok"\nplayer2 = "Arun"\n# 2player = "no"   -> SyntaxError\n# total score = 1  -> SyntaxError\nprint(total_score, player2)': {
        "stdout": "42 Arun\n",
        "stderr": "",
        "status": "success",
        "duration_ms": 7.0,
        "trace": None,
    },
    'item = "Notebook"\nprice = 45.0\nquantity = 3\ntotal = price * quantity\nprint(item, "x", quantity, "=", total)': {
        "stdout": "Notebook x 3 = 135.0\n",
        "stderr": "",
        "status": "success",
        "duration_ms": 9.0,
        "trace": {
            "exampleId": "bill_calc",
            "steps": [
                {"logicalLine": 1, "output": None, "variables": [{"name": "item", "value": '"Notebook"', "type": "str"}]},
                {"logicalLine": 2, "output": None, "variables": [{"name": "price", "value": "45.0", "type": "float"}]},
                {"logicalLine": 3, "output": None, "variables": [{"name": "quantity", "value": "3", "type": "int"}]},
                {"logicalLine": 4, "output": None, "variables": [{"name": "total", "value": "135.0", "type": "float"}]},
                {"logicalLine": 5, "output": "Notebook x 3 = 135.0", "variables": []},
            ],
            "stdin": [],
        },
    },
    "apples = 7\nprice = 12.5\nprint(apples * price)\nprint(7 // 2)\nprint(7 % 2)\nprint(2 ** 3)": {
        "stdout": "87.5\n3\n1\n8\n",
        "stderr": "",
        "status": "success",
        "duration_ms": 8.0,
        "trace": None,
    },
    'name = "Priya"\nprint("Hello, " + name)\nprint(len(name))\nprint(name.upper())\nprint(name[0])': {
        "stdout": "Hello, Priya\n5\nPRIYA\nP\n",
        "stderr": "",
        "status": "success",
        "duration_ms": 7.0,
        "trace": None,
    },
    "age = 21\nis_adult = age >= 18\nprint(is_adult)\nprint(age == 21)\nprint(age < 18)\nprint(is_adult and age < 25)": {
        "stdout": "True\nTrue\nFalse\nTrue\n",
        "stderr": "",
        "status": "success",
        "duration_ms": 8.0,
        "trace": None,
    },
    'text = "5"\nnumber = int(text)\nprint(number + 5)\nprint(str(number) + "5")\nprint(float("3.5") * 2)\nprint(int(7.9))': {
        "stdout": "10\n55\n7.0\n7\n",
        "stderr": "",
        "status": "success",
        "duration_ms": 9.0,
        "trace": None,
    },
}


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

@dataclass
class _NarrationState:
    session_id: str
    course_id: str
    page_id: str
    words: list[str]
    cues: list[dict]
    diagram_spec: dict
    narration_text: str
    word_index: int = 0
    paused: bool = False
    speed: float = 1.0
    gender: str = ""
    task: Optional[asyncio.Task] = None


# ---------------------------------------------------------------------------
# MockBookService
# ---------------------------------------------------------------------------

class MockBookService:
    """
    Connection-scoped mock service. Instantiated per WebSocket connection.
    seq_counter: monotonically increasing per connection.
    """

    def __init__(
        self,
        send: SendFn,
        user_id: str = "",
        token_ms: int = 85,
        thinking_ms: int = 600,
        answer_token_ms: int = 45,
        code_stream_ms: int = 30,
        narration_tts: bool = False,
    ) -> None:
        self._send = send
        # Speak narration with the local female TTS and pace text by its word timings.
        self._narration_tts = narration_tts
        self._user_id = user_id
        self._token_ms = token_ms
        self._thinking_ms = thinking_ms
        self._answer_token_ms = answer_token_ms
        self._code_stream_ms = code_stream_ms

        self._seq: int = 0
        self._narration: Optional[_NarrationState] = None
        self._survey_answers: dict[str, dict[str, str]] = {}
        # Tutor: per-connection conversation history
        self._tutor_history: list[dict] = []
        self._tutor_course_id: str = "python"
        self._tutor_chapter: int = 1
        self._tutor_page: int = 1
        self._narration_active: bool = False
        # Audio: per-utterance buffer (seq → bytes), capped (see handle_audio_in)
        self._audio_buffers: dict[str, bytes] = {}
        self._audio_started: dict[str, float] = {}
        # Every task spawned for this connection; all cancelled in dispose()
        self._tasks: set[asyncio.Task] = set()
        self._gender = ""
        # Serialises start_page / seek so two narration loops never run at once
        self._page_lock = asyncio.Lock()
        self._disposed = False

    def spawn(self, coro) -> Optional[asyncio.Task]:
        """Run *coro* as a connection-scoped task (cancelled on dispose)."""
        if self._disposed:
            coro.close()
            return None
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _ts(self) -> int:
        return int(time.time() * 1000)

    async def _emit(self, event: Any, session_id: Optional[str] = None) -> None:
        data = event.model_dump(mode="json")
        # Inject seq and ts into the envelope if this is a server event
        if "seq" in data:
            data["seq"] = self._next_seq()
        if "ts" in data:
            data["ts"] = self._ts()
        if "sessionId" in data and session_id is not None:
            data["sessionId"] = session_id
        await self._send(data)

    async def _emit_error(self, code: str, message: str, recoverable: bool = True) -> None:
        ev = ErrorEvent(
            seq=0, sessionId=None, ts=0,
            payload=ErrorPayload(code=code, message=message, recoverable=recoverable),
        )
        await self._emit(ev)

    # -----------------------------------------------------------------------
    # Page loading helpers
    # -----------------------------------------------------------------------

    def _get_page_data(self, course_id: str, page_id: str) -> Optional[dict[str, Any]]:
        """Every book, the Python book included, is read from the database (_load_db_page)."""
        return None

    async def _load_db_page(self, course_id: str, page_id: str) -> Optional[dict[str, Any]]:
        """Pages of generated books (course builder / learner books) live in the DB.
        Private learner books are only narrated for their owner (and admins)."""
        try:
            from app.books.access import user_can_view_course
            from app.db.database import AsyncSessionLocal
            from app.db.models import Page

            if not await user_can_view_course(course_id, self._user_id):
                return None
            async with AsyncSessionLocal() as db:
                page = await db.get(Page, page_id)
            if page is None or page.course_id != course_id:
                return None
            spec = dict(page.diagram_spec or {})
            spec.setdefault("kind", "labeled-boxes")
            spec.setdefault("nodes", [])
            return {"id": page.id, "narration_text": page.narration_text or page.title,
                    "cues": list(page.cues or []), "diagram_spec": spec}
        except Exception:
            log.debug("DB page load failed for %s/%s", course_id, page_id, exc_info=True)
            return None

    def _build_diagram_spec_payload(self, page_data: dict) -> DiagramSpecPayload:
        spec_raw = page_data["diagram_spec"]
        nodes = [DiagramNode(**n) for n in spec_raw.get("nodes", [])]
        spec = DiagramSpec(
            kind=spec_raw["kind"],
            title=spec_raw.get("title"),
            caption=spec_raw.get("caption"),
            nodes=nodes,
        )
        steps = [n.id for n in nodes]
        return DiagramSpecPayload(
            diagramId=page_data["id"],
            spec=spec,
            steps=steps,
        )

    # -----------------------------------------------------------------------
    # Narration
    # -----------------------------------------------------------------------

    async def _cancel_narration_task(self) -> None:
        if self._narration and self._narration.task and not self._narration.task.done():
            self._narration.task.cancel()
            try:
                await self._narration.task
            except (asyncio.CancelledError, Exception):
                pass

    async def handle_start_page(self, course_id: str, page_id: str, gender: str = "") -> None:
        self._gender = gender
        async with self._page_lock:
            await self._start_page_locked(course_id, page_id)

    async def _start_page_locked(self, course_id: str, page_id: str) -> None:
        # Cancel any existing narration
        await self._cancel_narration_task()

        page_data = self._get_page_data(course_id, page_id) or await self._load_db_page(course_id, page_id)
        if not page_data:
            await self._emit_error(
                "page_not_found",
                f"No page {page_id!r} in course {course_id!r}.",
                recoverable=True,
            )
            return

        session_id = f"sess_{uuid.uuid4().hex[:12]}"
        words = page_data["narration_text"].split()
        cues = page_data.get("cues", [])

        state = _NarrationState(
            session_id=session_id,
            course_id=course_id,
            page_id=page_id,
            words=words,
            cues=cues,
            diagram_spec=page_data["diagram_spec"],
            narration_text=page_data["narration_text"],
        )
        self._narration = state

        # Send session_started
        await self._emit(
            SessionStartedEvent(
                seq=0, ts=0, sessionId=session_id,
                payload=SessionStartedPayload(
                    sessionId=session_id, courseId=course_id, pageId=page_id
                ),
            ),
            session_id=session_id,
        )

        # Send diagram_spec
        diagram_payload = self._build_diagram_spec_payload(page_data)
        await self._emit(
            DiagramSpecEvent(seq=0, ts=0, sessionId=session_id, payload=diagram_payload),
            session_id=session_id,
        )

        # Start narration task
        state.task = self.spawn(self._narration_loop(state))

    async def _narration_loop(self, state: _NarrationState) -> None:
        words = state.words
        cues = state.cues
        page_id = state.page_id
        session_id = state.session_id

        # Build cue index: word_index → list[cue]
        cue_map: dict[int, list[dict]] = {}
        for cue in cues:
            idx = cue.get("atWord", 0)
            cue_map.setdefault(idx, []).append(cue)

        if self._narration_tts:
            try:
                await self._narrate_with_voice(state, cue_map)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # TTS stack unavailable: keep timed text
                log.warning("Narration TTS failed, falling back to timed text: %s", exc)
                await self._narrate_timed(state, cue_map, state.word_index, len(words))
        else:
            await self._narrate_timed(state, cue_map, state.word_index, len(words))

        # page_complete
        await self._emit(
            PageCompleteEvent(seq=0, ts=0, sessionId=session_id, payload=PageCompletePayload(pageId=page_id)),
            session_id=session_id,
        )

    async def _emit_word(self, state: _NarrationState, i: int, cue_map: dict[int, list[dict]], audio_start_ms: float) -> None:
        """Emit one text_chunk plus the cues anchored to word *i*."""
        session_id = state.session_id
        page_id = state.page_id
        segment_id = f"{page_id}#s{i}"
        await self._emit(
            TextChunkEvent(
                seq=0, ts=0, sessionId=session_id,
                payload=TextChunkPayload(
                    segmentId=segment_id,
                    text=state.words[i],
                    wordStart=i,
                    wordEnd=i + 1,
                    audioStartMs=audio_start_ms,
                ),
            ),
            session_id=session_id,
        )
        state.word_index = i

        for cue in cue_map.get(i, []):
            kind = cue.get("kind", "")
            target = cue.get("target", "")

            if kind == "focus-node":
                await self._emit(
                    DiagramDrawEvent(
                        seq=0, ts=0, sessionId=session_id,
                        payload=DiagramDrawPayload(diagramId=page_id, stepId=target),
                    ),
                    session_id=session_id,
                )
            elif kind == "highlight-term":
                await self._emit(
                    CueEvent(
                        seq=0, ts=0, sessionId=session_id,
                        payload=CuePayload(
                            segmentId=segment_id, atWord=i,
                            action="highlight", targetId=target,
                        ),
                    ),
                    session_id=session_id,
                )
            elif kind in ("focus-code-line", "runCode"):
                await self._emit(
                    CueEvent(
                        seq=0, ts=0, sessionId=session_id,
                        payload=CuePayload(
                            segmentId=segment_id, atWord=i,
                            action="runCode", targetId=target,
                        ),
                    ),
                    session_id=session_id,
                )

    async def _narrate_timed(self, state: _NarrationState, cue_map: dict[int, list[dict]], start: int, end: int) -> None:
        """Fixed-rate text pacing (no audio)."""
        i = start
        while i < end:
            if state.paused:
                await asyncio.sleep(0.05)
                continue
            await self._emit_word(state, i, cue_map, i * (self._token_ms / state.speed))
            await asyncio.sleep((self._token_ms / 1000.0) / state.speed)
            i += 1

    async def _wait_ms(self, state: _NarrationState, ms: float) -> None:
        """Sleep *ms* of narration time; the clock stops while paused."""
        while ms > 0:
            if state.paused:
                await asyncio.sleep(0.05)
                continue
            step = min(50.0, ms)
            await asyncio.sleep(step / 1000.0)
            ms -= step

    async def _narrate_with_voice(self, state: _NarrationState, cue_map: dict[int, list[dict]]) -> None:
        """
        Speak the page sentence by sentence. Each sentence goes out as ONE playable
        audio_chunk (with display-word timings), then its words are paced by those timings
        so server cues line up with the voice. Two sentences of lookahead hide synthesis time
        (a cold Edge request takes 4-6 s, about as long as a sentence plays).
        """
        from app.voice.pipeline import build_audio_chunk_payloads, split_sentences, synthesize_segment

        sentences = [(t, o) for t, o in split_sentences(state.narration_text) if o + len(t.split()) > state.word_index]
        if not sentences:
            return

        def synth(text: str) -> asyncio.Task:
            return asyncio.create_task(synthesize_segment(text, speed=state.speed, lang="auto",
                                                          gender=self._gender))

        pending = [synth(t) for t, _ in sentences[:2]]
        try:
            for k, (text, offset) in enumerate(sentences):
                result = await pending.pop(0)
                if k + 2 < len(sentences):
                    pending.append(synth(sentences[k + 2][0]))
                n_words = len(text.split())
                first = max(offset, state.word_index)

                if not result.audio_bytes or result.provider == "silent":
                    await self._narrate_timed(state, cue_map, first, offset + n_words)
                    continue

                # Whole sentence in one chunk so the client can play it on its own.
                payload = build_audio_chunk_payloads(
                    result, segment_id=f"{state.page_id}#s{offset}", seq_start=0,
                    word_offset=offset, chunk_size=max(1, len(result.audio_bytes)),
                )[0]
                await self._emit(
                    AudioChunkEvent(seq=0, ts=0, sessionId=state.session_id, payload=AudioChunkPayload(**payload)),
                    session_id=state.session_id,
                )

                starts = {offset + t.word_index: t.start_ms for t in result.word_timings}
                clock_ms = 0.0
                for i in range(first, offset + n_words):
                    target = float(starts.get(i, clock_ms))
                    if target > clock_ms:
                        await self._wait_ms(state, target - clock_ms)
                        clock_ms = target
                    await self._emit_word(state, i, cue_map, target)
                # Let the tail of the sentence finish before pacing the next one.
                await self._wait_ms(state, float(result.duration_ms) - clock_ms)
        finally:
            for task in pending:
                task.cancel()

    async def handle_pause(self, session_id: str) -> None:
        if self._narration and self._narration.session_id == session_id:
            self._narration.paused = True

    async def handle_resume(self, session_id: str) -> None:
        if self._narration and self._narration.session_id == session_id:
            self._narration.paused = False

    async def handle_seek(self, session_id: str, segment_id: str, word_index: int) -> None:
        """Jump forward OR backward: stop the running narration loop and restart it at
        *word_index* (both the timed and the voice loop resume from state.word_index)."""
        async with self._page_lock:
            state = self._narration
            if not state or state.session_id != session_id or not state.words:
                return
            await self._cancel_narration_task()
            state.word_index = max(0, min(word_index, len(state.words) - 1))
            state.task = self.spawn(self._narration_loop(state))

    async def handle_speed(self, session_id: str, value: float) -> None:
        if value <= 0:
            await self._emit_error("invalid_speed", f"Speed must be > 0, got {value}.", recoverable=True)
            return
        if self._narration and self._narration.session_id == session_id:
            self._narration.speed = value

    # -----------------------------------------------------------------------
    # Questions
    # -----------------------------------------------------------------------

    async def handle_ask_question(
        self,
        session_id: Optional[str],
        source: str,
        text: str,
        question_id: str,
        context: dict,
    ) -> None:
        if not text.strip():
            await self._emit_error("empty_question", "Question cannot be empty.", recoverable=True)
            return

        # Echo back question_received
        await self._emit(
            QuestionReceivedEvent(
                seq=0, ts=0, sessionId=session_id,
                payload=QuestionReceivedPayload(questionId=question_id, source=source, text=text),  # type: ignore[arg-type]
            ),
            session_id=session_id,
        )

        # Update page context from the question's context payload
        course_id = context.get("courseId") or self._tutor_course_id
        chapter = context.get("chapter") or self._tutor_chapter
        page = context.get("page") or self._tutor_page
        page_script = context.get("pageScript") or ""
        learner_profile = context.get("learnerProfile") or {}
        page_id = context.get("pageId") or None
        intent = context.get("intent") or None
        # Resolve the page id to its chapter/page numbers so the tutor grounds on the right page.
        if page_id and course_id and not context.get("chapter"):
            try:
                from sqlalchemy import select as _select
                from app.db.database import AsyncSessionLocal
                from app.db.models import Chapter as _Chapter, Page as _Page
                async with AsyncSessionLocal() as _db:
                    _row = (await _db.execute(
                        _select(_Chapter.number, _Page.page_number)
                        .join(_Chapter, _Chapter.id == _Page.chapter_id)
                        .where(_Page.id == page_id, _Page.course_id == course_id)
                    )).first()
                if _row is not None:
                    chapter, page = int(_row[0]), int(_row[1])
                else:
                    page_id = None
            except Exception:
                page_id = None

        # Run B4 tutor pipeline
        import asyncio
        try:
            from app.tutor.graph import (
                process_question,
                ANSWER_START, ANSWER_CHUNK, ANSWER_END, SOURCES_EVENT, SCOPE_REFUSED,
            )

            gen = await process_question(
                question=text,
                user_id=self._user_id or "anonymous",
                session_id=session_id or "default",
                course_id=course_id,
                chapter=chapter,
                page=page,
                history=self._tutor_history[-6:],  # last 3 exchanges (6 messages)
                page_script=page_script,
                learner_profile=learner_profile,
                page_id=page_id,
                intent=intent,
            )

            full_answer_parts: list[str] = []
            sources_payload: list[dict] = []

            async for ev in gen:
                etype = ev.get("type", "")

                if etype == SCOPE_REFUSED:
                    await self._emit(
                        ScopeRefusedEvent(
                            seq=0, ts=0, sessionId=session_id,
                            payload=ScopeRefusedPayload(
                                questionId=question_id,
                                message=ev.get("message", "Out of scope."),
                            ),
                        ),
                        session_id=session_id,
                    )
                    return

                elif etype == ANSWER_CHUNK:
                    word = ev.get("word", "")
                    full_answer_parts.append(word)
                    await self._emit(
                        AnswerChunkEvent(
                            seq=0, ts=0, sessionId=session_id,
                            payload=AnswerChunkPayload(
                                questionId=question_id,
                                text=word + " ",
                                final=False,
                                attachments=None,
                            ),
                        ),
                        session_id=session_id,
                    )

                elif etype == ANSWER_END:
                    # Send final empty chunk as end-of-answer marker
                    await self._emit(
                        AnswerChunkEvent(
                            seq=0, ts=0, sessionId=session_id,
                            payload=AnswerChunkPayload(
                                questionId=question_id,
                                text="",
                                final=True,
                                attachments=None,
                            ),
                        ),
                        session_id=session_id,
                    )

                elif etype == SOURCES_EVENT:
                    sources_payload = ev.get("sources", [])

            # Emit sources (always, even if empty — protocol requires the event)
            _source_items = [
                SourceItem(
                    title=s.get("chunkId", "")[:40] or "Reference",
                    kind="doc",
                    ref=s.get("documentId", ""),
                )
                for s in sources_payload[:5]
            ] or [SourceItem(title=("Python Basics" if course_id == "python" else "This page"), kind="page", ref="0")]
            await self._emit(
                SourcesEvent(
                    seq=0, ts=0, sessionId=session_id,
                    payload=SourcesPayload(
                        questionId=question_id,
                        items=_source_items,
                    ),
                ),
                session_id=session_id,
            )

            # Update conversation history
            full_answer = " ".join(full_answer_parts)
            self._tutor_history.append({"role": "user", "content": text})
            self._tutor_history.append({"role": "assistant", "content": full_answer})
            if len(self._tutor_history) > 20:
                self._tutor_history = self._tutor_history[-20:]

        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("Tutor pipeline error: %s", exc, exc_info=True)
            # Fallback to scripted answers so UI is never blank
            chunks = _pick_answer_chunks(text)
            for idx, chunk in enumerate(chunks):
                is_final = idx == len(chunks) - 1
                await self._emit(
                    AnswerChunkEvent(
                        seq=0, ts=0, sessionId=session_id,
                        payload=AnswerChunkPayload(
                            questionId=question_id, text=chunk, final=is_final,
                            attachments=None,
                        ),
                    ),
                    session_id=session_id,
                )

            # Always emit sources so clients receive the expected protocol event
            await self._emit(
                SourcesEvent(
                    seq=0, ts=0, sessionId=session_id,
                    payload=SourcesPayload(
                        questionId=question_id,
                        items=[SourceItem(title="Python Basics", kind="page", ref="0")],
                    ),
                ),
                session_id=session_id,
            )

    # -----------------------------------------------------------------------
    # Code runner
    # -----------------------------------------------------------------------

    async def handle_run_code(
        self,
        course_id: str,
        page_id: str,
        code: str,
        language: str,
        code_id: str,
        stdin: Optional[list[str]] = None,
        use_sample_input: Optional[bool] = None,
    ) -> None:
        example = _SEEDED_EXAMPLES.get(code.strip())
        if example is None:
            # Unsupported
            trace = RunTrace(
                exampleId=None,
                steps=[],
                stdin=[],
                unsupported="This code is not in the demo examples. Edit it to match a seeded example, or connect the Pyodide runner.",
            )
            await self._emit(
                RunResultEvent(
                    seq=0, ts=0, sessionId=None,
                    payload=RunResultPayload(
                        codeId=code_id, stdout="", stderr="",
                        status="error", durationMs=0.0, trace=trace,
                    ),
                )
            )
            return

        # Small delay to feel realistic
        await asyncio.sleep(example["duration_ms"] / 1000.0 * 3)

        trace_data = example.get("trace")
        trace = None
        if trace_data:
            trace = RunTrace(
                exampleId=trace_data["exampleId"],
                steps=[
                    RunTraceStep(
                        logicalLine=s["logicalLine"],
                        output=s.get("output"),
                        variables=[RunTraceVariable(**v) for v in s["variables"]],
                    )
                    for s in trace_data["steps"]
                ],
                stdin=trace_data.get("stdin", []),
            )

        await self._emit(
            RunResultEvent(
                seq=0, ts=0, sessionId=None,
                payload=RunResultPayload(
                    codeId=code_id,
                    stdout=example["stdout"],
                    stderr=example["stderr"],
                    status=example["status"],
                    durationMs=example["duration_ms"],
                    trace=trace,
                ),
            )
        )

    # -----------------------------------------------------------------------
    # Survey
    # -----------------------------------------------------------------------

    async def handle_submit_survey(self, course_id: str, answers: dict[str, str]) -> None:
        self._survey_answers[course_id] = answers
        # No protocol event for survey in B0 — just acknowledged silently.
        # Future: persist to DB and adjust tutor persona.

    # -----------------------------------------------------------------------
    # Audio in (no-op in B0)
    # -----------------------------------------------------------------------

    async def handle_audio_in(
        self,
        session_id: Optional[str] = None,
        seq: int = 0,
        mime: str = "audio/webm",
        base64: str = "",
        final: bool = False,
        **kwargs,
    ) -> None:
        """B5: STT pipeline. Assembles chunks, transcribes on final=True."""
        import base64 as b64
        buf_key = session_id or "default"

        # Accumulate audio chunks
        try:
            chunk_bytes = b64.b64decode(base64) if base64 else b""
        except Exception:
            chunk_bytes = b""

        from app.config import get_settings
        cfg = get_settings()
        now = time.monotonic()
        self._audio_started.setdefault(buf_key, now)
        new_buf = self._audio_buffers.get(buf_key, b"") + chunk_bytes
        total = sum(len(v) for k, v in self._audio_buffers.items() if k != buf_key) + len(new_buf)
        too_big = total > cfg.ws_max_audio_bytes
        too_long = (now - self._audio_started[buf_key]) > cfg.ws_max_audio_seconds
        if too_big or too_long:
            # Drop the utterance; keep the connection.
            self._audio_buffers.pop(buf_key, None)
            self._audio_started.pop(buf_key, None)
            await self._emit_error(
                "audio_too_large",
                f"Voice question exceeds the {cfg.ws_max_audio_bytes // (1024 * 1024)} MB / "
                f"{int(cfg.ws_max_audio_seconds)} s limit.",
                recoverable=True,
            )
            return
        self._audio_buffers[buf_key] = new_buf

        if not final:
            return  # wait for more chunks

        audio_bytes = self._audio_buffers.pop(buf_key, b"")
        self._audio_started.pop(buf_key, None)
        if not audio_bytes:
            return

        # Run B5 STT + feed to tutor
        try:
            from app.voice.pipeline import handle_audio_in as stt_handle
            await stt_handle(
                session_id=buf_key,
                audio_bytes=audio_bytes,
                mime=mime,
                user_id=self._user_id or "anonymous",
                course_id=self._tutor_course_id,
                chapter=self._tutor_chapter,
                page=self._tutor_page,
                ws_send=self._send,
                narration_active=self._narration_active,
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("audio_in STT failed: %s", exc)

    # -----------------------------------------------------------------------
    # Cleanup
    # -----------------------------------------------------------------------

    async def dispose(self) -> None:
        self._disposed = True
        tasks = [t for t in self._tasks if not t.done()]
        if self._narration and self._narration.task and not self._narration.task.done():
            tasks.append(self._narration.task)
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._audio_buffers.clear()
        self._audio_started.clear()
