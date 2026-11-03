"""R2 "More inputs": video / audio / image books, vision LLM plumbing, syllabus mode.

Everything heavy is mocked here (ffmpeg, faster-whisper, vision LLM, tesseract); the real
end-to-end smoke tests live in tests/test_books_media_smoke.py (opt-in: BOOKEY_MEDIA_SMOKE=1).
"""
from __future__ import annotations

import io
import json

import httpx
import pytest
import respx
from PIL import Image, ImageDraw

from app.course_builder import runner
from app.course_builder.outline import count_syllabus_units
from app.knowledge.sources import images as img_mod
from app.knowledge.sources import media as media_mod
from app.knowledge.sources.base import SourceError, TimedSegment
from app.llm import LLMImage, LLMProvider, LLMResponse
from app.llm.providers.groq import GroqProvider
from app.llm.providers.mock import MockLLMProvider
from app.llm.providers.openrouter import OpenRouterProvider
from app.llm.router import LLMError, LLMRouter

# shared helpers + autouse fixtures (isolated storage / vector store / mock LLM, test-user cleanup)
from tests.test_books_api import _client, _new_user, _retire_test_users, isolated_backends  # noqa: F401


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

class FakeVision(LLMProvider):
    """Vision provider double: records how many images each call carried."""
    name = "fakevision"
    supports_images = True

    def __init__(self, reply: str | list[str]) -> None:
        self.replies = reply if isinstance(reply, list) else [reply]
        self.calls: list[int] = []

    async def is_available(self) -> bool:
        return True

    async def generate_text(self, prompt, system="", temperature=0.7, max_tokens=4096, timeout=30.0, images=None):
        self.calls.append(len(images or []))
        text = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        return LLMResponse(text=text, provider=self.name, model="fake-vl")

    async def generate_json(self, prompt, schema, system="", temperature=0.2, max_tokens=8192, timeout=60.0, images=None):
        return await self.generate_text(prompt, system, images=images)


def _vision(monkeypatch, reply) -> FakeVision:
    fv = FakeVision(reply)
    monkeypatch.setattr(img_mod, "VISION_ROUTER_OVERRIDE", LLMRouter([fv], retry_per_provider=0))
    return fv


def _png(text: str = "Hello", size=(400, 160), color="white") -> bytes:
    im = Image.new("RGB", size, color)
    if text:
        ImageDraw.Draw(im).text((20, 60), text, fill="black")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _jpeg(text: str = "Slide", color="white") -> bytes:
    im = Image.new("RGB", (320, 180), color)
    if text:
        d = ImageDraw.Draw(im)
        d.rectangle((10, 10, 200, 120), outline="black", width=4)
        d.text((30, 50), text, fill="black")
    buf = io.BytesIO()
    im.save(buf, "JPEG")
    return buf.getvalue()


FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 2048

VISION_NOTES = json.dumps({
    "kind": "notes",
    "text": "# Photosynthesis\n- Light + CO2 + water -> glucose + oxygen\n- Happens in chloroplasts",
    "diagrams": ["Leaf cross-section: sunlight -> chloroplast -> glucose"],
    "summary": "Class notes on photosynthesis",
})


@pytest.fixture(autouse=True)
def _media_env(monkeypatch):
    monkeypatch.setattr(img_mod, "VISION_ROUTER_OVERRIDE", LLMRouter([MockLLMProvider(latency_ms=0)], retry_per_provider=0))
    monkeypatch.setattr(img_mod, "tesseract_cmd", lambda: None)   # deterministic: no real OCR here
    monkeypatch.setenv("BOOKS_SYLLABUS_MAX_CHAPTERS", "3")
    monkeypatch.setenv("BOOKS_SYLLABUS_MAX_LESSONS", "2")
    yield


def _mock_pipeline(monkeypatch, *, duration=95.0, segments=None, frames=None, progress_log=None):
    """ffmpeg + whisper doubles for media_to_document."""
    async def probe(path):
        return media_mod.MediaInfo(duration=duration, has_video=True, has_audio=True)

    async def extract(src, dst):
        open(dst, "wb").close()
        return duration

    def transcribe(wav, state=None):
        segs = segments or [TimedSegment(i * 10.0, i * 10.0 + 9.0, f"Sentence {i} about photosynthesis and light.")
                            for i in range(int(duration // 10))]
        if state is not None:
            state["pos"] = segs[-1].end
        return media_mod.Transcript(segments=segs, language="en", device="cpu", model="auto")

    async def key_frames(src, out_dir, dur, max_frames):
        import os
        out = []
        for i, (t, data) in enumerate(frames or []):
            p = os.path.join(out_dir, f"f{i}.jpg")
            open(p, "wb").write(data)
            out.append((t, p))
        return out

    monkeypatch.setattr(media_mod, "ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(media_mod, "probe_media", probe)
    monkeypatch.setattr(media_mod, "extract_audio", extract)
    monkeypatch.setattr(media_mod, "transcribe_file", transcribe)
    monkeypatch.setattr(media_mod, "extract_key_frames", key_frames)


# ─────────────────────────────────────────────────────────────────────────────
# pure helpers
# ─────────────────────────────────────────────────────────────────────────────

def test_detect_media_mime():
    assert media_mod.detect_media_mime(FAKE_MP4, "talk.mp4") == "video/mp4"
    assert media_mod.detect_media_mime(FAKE_MP4, "talk.mov") == "video/quicktime"
    assert media_mod.detect_media_mime(FAKE_MP4, "talk.m4a") == "audio/mp4"
    assert media_mod.detect_media_mime(b"\x1a\x45\xdf\xa3" + b"\x00" * 60, "rec.webm") == "video/webm"
    assert media_mod.detect_media_mime(b"ID3\x04" + b"\x00" * 60, "song.mp3") == "audio/mpeg"
    assert media_mod.detect_media_mime(b"RIFF\x00\x00\x00\x00WAVEfmt ", "a.wav") == "audio/wav"
    assert media_mod.detect_media_mime(b"%PDF-1.7", "fake.mp4") is None
    assert media_mod.detect_media_mime(FAKE_MP4, "talk.webm") is None   # extension must match the bytes


def test_parse_ffmpeg_info():
    err = ("Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'x.mp4':\n  Duration: 00:45:00.52, start: 0.000000\n"
           "  Stream #0:0[0x1](und): Video: h264 (High), yuv420p, 1280x720\n"
           "  Stream #0:1[0x2](und): Audio: aac (LC), 44100 Hz, stereo\n")
    info = media_mod.parse_ffmpeg_info(err)
    assert info.duration == pytest.approx(2700.52) and info.has_video and info.has_audio
    mp3 = media_mod.parse_ffmpeg_info("Duration: 00:01:00.00\n Stream #0:0: Audio: mp3\n"
                                      " Stream #0:1: Video: mjpeg (Baseline), (attached pic)\n")
    assert mp3.has_audio and not mp3.has_video


def test_sections_split_on_pauses():
    segs = [TimedSegment(i * 20.0, i * 20.0 + 18.0, f"Point {i}.") for i in range(30)]   # 10 minutes
    secs = media_mod.sections_from_segments(segs, 240)
    assert 2 <= len(secs) <= 3
    assert sum(len(s) for s in secs) == 30
    assert all(secs[i][-1].start < secs[i + 1][0].start for i in range(len(secs) - 1))
    # a tiny tail is merged into the previous section
    secs = media_mod.sections_from_segments(segs[:13], 240)
    assert len(secs) == 1 or (secs[-1][-1].end - secs[-1][0].start) >= 60


def test_image_mime_prepare_and_frame_filter():
    png = _png("Notes")
    assert img_mod.detect_image_mime(png) == "image/png"
    assert img_mod.detect_image_mime(_jpeg()) == "image/jpeg"
    assert img_mod.detect_image_mime(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert img_mod.detect_image_mime(b"\x00\x00\x00\x18ftypheic") == "image/heic"
    jpeg, w, h = img_mod.prepare_image(_png("x", size=(3000, 1000)), max_side=1200)
    assert jpeg[:3] == b"\xff\xd8\xff" and (w, h) == (1200, 400)
    with pytest.raises(SourceError):
        img_mod.prepare_image(b"\x89PNG\r\n\x1a\nbroken")
    blank, slide = _jpeg("", "black"), _jpeg("Slide 1")
    kept = media_mod.pick_frames([(0.0, blank), (5.0, slide), (6.0, slide), (30.0, _jpeg("Other diagram text", "lightblue"))], 8)
    assert [t for t, _ in kept] == [5.0, 30.0]          # blank + duplicate dropped


def test_parse_vision_reply_and_syllabus_units():
    r = img_mod.parse_vision_reply("```json\n" + VISION_NOTES + "\n```")
    assert r.kind == "notes" and "chloroplasts" in r.text and r.diagrams[0].startswith("Leaf")
    plain = img_mod.parse_vision_reply("Just the words on the page")
    assert plain.text == "Just the words on the page" and plain.diagrams == []
    assert count_syllabus_units("Unit 1: Cells\n - a\nUNIT II - Plants\nWeek 3 Soil\nrandom line") == 3


# ─────────────────────────────────────────────────────────────────────────────
# LLM image plumbing
# ─────────────────────────────────────────────────────────────────────────────

async def test_router_routes_images_only_to_vision_providers():
    fv = FakeVision("seen")
    router = LLMRouter([MockLLMProvider(latency_ms=0), fv], retry_per_provider=0)
    assert router.supports_images
    resp = await router.generate_text("read", images=[LLMImage(b"x", "image/png")])
    assert resp.provider == "fakevision" and fv.calls == [1]
    resp = await router.generate_text("plain text")          # no images → first provider, kwarg not passed
    assert resp.provider == "mock"
    with pytest.raises(LLMError):
        await LLMRouter([MockLLMProvider(latency_ms=0)], retry_per_provider=0).generate_text(
            "read", images=[LLMImage(b"x")])


async def test_openrouter_vision_content_parts_and_model_fallback():
    p = OpenRouterProvider("k", "text-model", vision_models=["vl-a:free", "vl-b:free"])
    assert p.supports_images and not OpenRouterProvider("k", "m").supports_images
    bodies = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        bodies.append(body)
        if body["model"] == "vl-a:free":
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok text"}}], "usage": {}})

    with respx.mock() as m:
        m.post("https://openrouter.ai/api/v1/chat/completions").mock(side_effect=handler)
        resp = await p.generate_json("read it", {}, images=[LLMImage(b"\x89PNG", "image/png")])
        text_resp = await p.generate_text("hello")
    assert resp.text == "ok text" and resp.model == "vl-b:free"
    content = bodies[1]["messages"][-1]["content"]
    assert content[0]["type"] == "text" and content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "response_format" not in bodies[1]                       # vision call: no JSON mode
    assert bodies[2]["model"] == "text-model" and isinstance(bodies[2]["messages"][-1]["content"], str)
    assert text_resp.model == "text-model"


async def test_groq_vision_uses_vision_model():
    assert not GroqProvider("k", "m").supports_images
    p = GroqProvider("k", "openai/gpt-oss-120b", vision_model="llama-vision")
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    with respx.mock() as m:
        m.post("https://api.groq.com/openai/v1/chat/completions").mock(side_effect=handler)
        await p.generate_text("x", images=[LLMImage(b"jpg")])
    assert seen["model"] == "llama-vision" and "reasoning_effort" not in seen
    assert seen["messages"][-1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


# ─────────────────────────────────────────────────────────────────────────────
# adapters
# ─────────────────────────────────────────────────────────────────────────────

async def test_read_image_engines(monkeypatch):
    # vision unavailable (mock router has no vision) → tesseract → nothing
    r = await img_mod.read_image(_jpeg())
    assert r.method == "none" and "tesseract" in (r.error or "")

    async def fake_tess(jpeg):
        return img_mod.ImageReading(text="OCR words", method="tesseract")
    monkeypatch.setattr(img_mod, "tesseract_read", fake_tess)
    assert (await img_mod.read_image(_jpeg())).text == "OCR words"

    fv = _vision(monkeypatch, VISION_NOTES)
    r = await img_mod.read_image(_jpeg())
    assert r.method == "vision" and r.model == "fakevision:fake-vl" and fv.calls == [1]


async def test_images_to_document(monkeypatch):
    _vision(monkeypatch, [VISION_NOTES, "not json at all: Respiration releases energy"])
    seen = []

    async def progress(d):
        seen.append(d)
    doc = await img_mod.images_to_document([("n1.png", _png()), ("n2.jpg", _jpeg()), ("bad.png", b"\x89PNG\r\n\x1a\nxx")],
                                           title="Bio notes", on_progress=progress)
    assert doc.kind == "images" and "## Page 1 (notes): n1.png" in doc.text and "Respiration" in doc.text
    assert "- Diagram: Leaf cross-section" in doc.text
    assert doc.metadata["diagrams"] == ["Leaf cross-section: sunlight -> chloroplast -> glucose"]
    assert any("bad.png" in w for w in doc.metadata["warnings"])
    assert seen == ["Reading image 1/3", "Reading image 2/3", "Reading image 3/3"]
    with pytest.raises(SourceError, match="Couldn't read"):
        _vision(monkeypatch, json.dumps({"kind": "other", "text": "", "diagrams": []}))
        await img_mod.images_to_document([("x.png", _png())], title="t")


async def test_media_to_document_mocked(monkeypatch):
    slide = _jpeg("Photosynthesis slide")
    _mock_pipeline(monkeypatch, duration=400.0,
                   frames=[(0.0, _jpeg("", "black")), (12.0, slide), (330.0, _jpeg("Second diagram here", "lightblue"))])
    fv = _vision(monkeypatch, [json.dumps({"kind": "slide", "text": "Light reactions", "diagrams": ["Sun -> leaf"]}),
                               json.dumps({"kind": "other", "text": "", "diagrams": []})])
    monkeypatch.setenv("MEDIA_SECTION_SECONDS", "180")
    seen = []

    async def progress(d):
        seen.append(d)
    doc = await media_mod.media_to_document("in.mp4", title="Lecture", filename="lecture.mp4", kind="video",
                                            max_seconds=3600, on_progress=progress)
    assert doc.kind == "media" and doc.metadata["sectionCount"] >= 2
    assert "## Part 1 (0:00–" in doc.text and "> **On screen at 0:12** (slide):" in doc.text
    assert "> Diagram: Sun -> leaf" in doc.text and doc.metadata["diagrams"] == ["Sun -> leaf"]
    assert doc.metadata["frameCount"] == 1 and fv.calls == [1, 1]          # blank frame never sent
    assert seen[0] == "Extracting audio" and any(s.startswith("Transcribing") and "/ 6:40" in s for s in seen)
    assert "Reading slides 1/2" in seen

    with pytest.raises(SourceError, match="limit"):
        await media_mod.media_to_document("in.mp4", title="t", filename="t.mp4", max_seconds=60)


# ─────────────────────────────────────────────────────────────────────────────
# API: validation + limits
# ─────────────────────────────────────────────────────────────────────────────

def _files(*items):
    return [("files", (name, io.BytesIO(data), "application/octet-stream")) for name, data in items]


async def test_media_api_validation(app_client, monkeypatch):
    _, token = await _new_user()
    _vision(monkeypatch, VISION_NOTES)
    _mock_pipeline(monkeypatch)
    monkeypatch.setenv("BOOKS_MAX_IMAGES", "2")
    async with _client(token) as c:
        assert (await c.post("/api/books/media", json={"x": 1})).status_code == 422
        r = await c.post("/api/books/media", data={"title": "t"}, files=_files())
        assert r.status_code == 422
        r = await c.post("/api/books/media", files=_files(("virus.exe", b"MZ\x90")))
        assert r.status_code == 415
        r = await c.post("/api/books/media", files=_files(("notes.pdf", b"%PDF-1.7")))
        assert r.status_code == 415 and "normal upload" in r.json()["detail"]
        r = await c.post("/api/books/media", files=_files(("a.png", _png()), ("v.mp4", FAKE_MP4)))
        assert r.status_code == 422 and "not both" in r.json()["detail"]
        r = await c.post("/api/books/media", files=_files(*[(f"p{i}.png", _png()) for i in range(3)]))
        assert r.status_code == 422 and "Too many images" in r.json()["detail"]
        r = await c.post("/api/books/media", files=_files(("photo.png", _jpeg())))
        assert r.status_code == 415 and "not a PNG" in r.json()["detail"]
        r = await c.post("/api/books/media", files=_files(("phone.heic", b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64)))
        assert r.status_code == 415 and "JPG" in r.json()["detail"]
        r = await c.post("/api/books/media", files=_files(("talk.mp4", b"%PDF-not-a-video" + b"\x00" * 64)))
        assert r.status_code == 415

        monkeypatch.setenv("BOOKS_MAX_IMAGE_MB", "1")
        big = b"\xff\xd8\xff\xe0" + b"\x00" * (1024 * 1024 + 10)
        r = await c.post("/api/books/media", files=_files(("big.jpg", big)))
        assert r.status_code == 413

        async def long_probe(path):
            return media_mod.MediaInfo(duration=3 * 3600.0, has_video=True, has_audio=True)
        monkeypatch.setattr(media_mod, "probe_media", long_probe)
        r = await c.post("/api/books/media", files=_files(("talk.mp4", FAKE_MP4)))
        assert r.status_code == 413 and "3:00:00 long" in r.json()["detail"] and "2:00:00" in r.json()["detail"]

        monkeypatch.setattr(media_mod, "ffmpeg_path", lambda: None)
        r = await c.post("/api/books/media", files=_files(("talk.mp4", FAKE_MP4)))
        assert r.status_code == 503 and "ffmpeg" in r.json()["detail"]

        # no vision and no OCR → images can't be read
        monkeypatch.setattr(img_mod, "VISION_ROUTER_OVERRIDE", LLMRouter([MockLLMProvider(latency_ms=0)]))
        r = await c.post("/api/books/media", files=_files(("p.png", _png())))
        assert r.status_code == 503 and "tesseract" in r.json()["detail"]

        # syllabus flag without anything to mark
        r = await c.post("/api/books", json={"syllabus": True, "prompt": "Biology"})
        assert r.status_code == 422 and "syllabus" in r.json()["detail"]


async def test_media_route_has_its_own_body_limit(app_client, monkeypatch):
    from app.middleware.security import _body_limit_for
    assert _body_limit_for("/api/books/media") > _body_limit_for("/api/books") >= 20 * 1024 * 1024
    assert _body_limit_for("/api/books/media") >= 500 * 1024 * 1024
    monkeypatch.setenv("BOOKS_MAX_MEDIA_MB", "1")
    monkeypatch.setenv("BOOKS_MAX_IMAGES", "1")
    monkeypatch.setenv("BOOKS_MAX_IMAGE_MB", "1")
    limit = _body_limit_for("/api/books/media")
    assert limit == 3 * 1024 * 1024
    _, token = await _new_user()
    async with _client(token) as c:
        r = await c.post("/api/books/media", files=_files(("v.mp4", FAKE_MP4 + b"\x00" * (limit + 10))))
    assert r.status_code == 413 and "MB upload limit" in r.json()["detail"]


async def test_limits_endpoint(app_client, monkeypatch):
    _vision(monkeypatch, VISION_NOTES)
    _, token = await _new_user()
    async with _client(token) as c:
        r = await c.get("/api/books/limits")
    data = r.json()
    assert r.status_code == 200 and data["maxMediaMb"] == 500 and data["maxMediaMinutes"] == 120
    assert ".mp4" in data["accepted"]["video"] and ".png" in data["accepted"]["images"]
    assert data["images"]["vision"] is True and data["images"]["engine"] == "vision"


# ─────────────────────────────────────────────────────────────────────────────
# API: end-to-end books (mocked heavy parts)
# ─────────────────────────────────────────────────────────────────────────────

async def test_video_book_end_to_end(app_client, monkeypatch):
    _mock_pipeline(monkeypatch, duration=95.0, frames=[(4.0, _jpeg("Chloroplast diagram"))])
    _vision(monkeypatch, json.dumps({"kind": "diagram", "text": "Chloroplast", "diagrams": ["Chloroplast: thylakoid -> stroma"]}))
    _, token = await _new_user()
    async with _client(token) as c:
        r = await c.post("/api/books/media", files=_files(("Photosynthesis_lecture.mp4", FAKE_MP4)),
                         data={"language": "english"})
        assert r.status_code == 201, r.text
        created = r.json()
        assert created["detectedType"] == "video" and created["origin"] == "video"
        assert created["title"] == "Photosynthesis lecture" and created["syllabus"] is False
        await runner.wait(created["jobId"], timeout=120)
        job = (await c.get(f"/api/books/jobs/{created['jobId']}")).json()
    assert job["status"] == "ready", job
    src = job["sources"][0]
    assert src["type"] == "media" and src["status"] == "indexed" and src["chunkCount"] > 0
    assert src["metadata"]["mediaKind"] == "video" and src["metadata"]["frameCount"] == 1
    assert src["metadata"]["diagrams"] == ["Chloroplast: thylakoid -> stroma"]
    assert "inputDocumentIds" in src and "text" not in src
    lessons = [l for ch in job["outline"]["chapters"] for l in ch["lessons"]]
    assert lessons[0]["diagramIdeas"][0] == "Chloroplast: thylakoid -> stroma"   # diagram fed the outline
    steps = {s["key"]: s for s in job["steps"]}
    assert steps["ingest"]["status"] == "done" and steps["publish"]["status"] == "done"


async def test_images_book_and_retry_keeps_raw_files(app_client, monkeypatch):
    fv = _vision(monkeypatch, VISION_NOTES)
    _, token = await _new_user()
    async with _client(token) as c:
        r = await c.post("/api/books/media", files=_files(("IMG_20261103_101010.jpg", _jpeg("p1")),
                                                         ("IMG_20261103_101011.png", _png("p2"))))
        assert r.status_code == 201, r.text
        created = r.json()
        assert created["detectedType"] == "images" and created["title"] == "Notes from 2 photos"
        # first run: the vision model returns nothing readable → the build fails
        blank = _vision(monkeypatch, json.dumps({"kind": "other", "text": "", "diagrams": []}))
        await runner.wait(created["jobId"], timeout=120)
        job = (await c.get(f"/api/books/jobs/{created['jobId']}")).json()
        assert job["status"] == "failed" and "Couldn't read any text" in job["sources"][0]["error"]
        assert blank.calls == [1, 1]
        # retry re-reads the stored raw images
        fv = _vision(monkeypatch, VISION_NOTES)
        assert (await c.post(f"/api/books/jobs/{created['jobId']}/retry")).status_code == 202
        await runner.wait(created["jobId"], timeout=120)
        job = (await c.get(f"/api/books/jobs/{created['jobId']}")).json()
        books = (await c.get("/api/books")).json()
    assert job["status"] == "ready", job
    assert job["sources"][0]["type"] == "images" and job["sources"][0]["metadata"]["imageCount"] == 2
    assert fv.calls == [1, 1]
    assert books[0]["origin"] == "images" and "2 images" in books[0]["sourceLabel"]


async def test_pasted_syllabus_is_followed_exactly(app_client):
    syllabus = ("Unit 1: Cell structure\n  - Cell wall and membrane\n  - Nucleus\n"
                "Unit 2: Plant nutrition\n  - Photosynthesis\n  - Transpiration\n")
    _, token = await _new_user()
    async with _client(token) as c:
        r = await c.post("/api/books", json={"syllabusText": syllabus, "title": "Class 10 Biology"})
        assert r.status_code == 201, r.text
        created = r.json()
        assert created["syllabus"] is True and created["aiKnowledge"] is True and created["detectedType"] == "syllabus"
        await runner.wait(created["jobId"], timeout=120)
        job = (await c.get(f"/api/books/jobs/{created['jobId']}")).json()
    assert job["status"] == "ready", job
    chapters = job["outline"]["chapters"]
    assert [ch["title"] for ch in chapters] == ["Cell structure", "Plant nutrition"]
    assert [l["title"] for l in chapters[1]["lessons"]] == ["Photosynthesis", "Transpiration"]
    assert job["request"]["syllabusStrict"] is True and "syllabusText" not in job["request"]
    assert any("Follows your syllabus" in w for w in job["warnings"])
    assert job["sources"][0]["role"] == "syllabus"


async def test_syllabus_file_plus_extra_source(app_client):
    from tests.test_books_api import SQL_NOTES
    syllabus = b"Week 1: Joins basics\n  - Inner join\nWeek 2: Outer joins\n  - Left join\n  - Right join\n"
    _, token = await _new_user()
    async with _client(token) as c:
        r = await c.post("/api/books", files={"file": ("syllabus.txt", io.BytesIO(syllabus), "text/plain")},
                         data={"syllabus": "true", "language": "english"})
        assert r.status_code == 201, r.text
        created = r.json()
        assert created["syllabus"] is True and created["aiKnowledge"] is True
        await runner.wait(created["jobId"], timeout=120)
        job = (await c.get(f"/api/books/jobs/{created['jobId']}")).json()
    assert job["status"] == "ready", job
    assert [ch["title"] for ch in job["outline"]["chapters"]] == ["Joins basics", "Outer joins"]

    # syllabus photo + an extra source (a pasted link is mocked away: use notes as a second upload path)
    fake = FakeVision(json.dumps({"kind": "syllabus", "text": "Unit 1: Inner joins\n  - Matching rows\n"
                                  "Unit 2: Left joins\n  - Keeping all rows", "diagrams": []}))
    img_mod.VISION_ROUTER_OVERRIDE = LLMRouter([fake], retry_per_provider=0)
    try:
        async with _client(token) as c:
            r = await c.post("/api/books/media", files=_files(("syllabus.jpg", _jpeg("syl"))),
                             data={"syllabus": "true", "prompt": "Focus on SQL practice"})
            assert r.status_code == 201, r.text
            created = r.json()
            await runner.wait(created["jobId"], timeout=120)
            job = (await c.get(f"/api/books/jobs/{created['jobId']}")).json()
    finally:
        img_mod.VISION_ROUTER_OVERRIDE = None
    assert job["status"] == "ready", job
    assert [ch["title"] for ch in job["outline"]["chapters"]] == ["Inner joins", "Left joins"]
    assert job["sources"][0]["role"] == "syllabus"

    # pasted syllabus + an extra source that fills the content → not "AI knowledge"
    async with _client(token) as c:
        r = await c.post("/api/books", files={"file": ("sql-joins.md", io.BytesIO(SQL_NOTES), "text/markdown")},
                         data={"syllabusText": "Unit 1: Inner join\n  - Matching rows\nUnit 2: Left join\n  - NULLs"})
        assert r.status_code == 201, r.text
        created = r.json()
        assert created["syllabus"] is True and created["aiKnowledge"] is False and created["origin"] == "mixed"
        await runner.wait(created["jobId"], timeout=120)
        job = (await c.get(f"/api/books/jobs/{created['jobId']}")).json()
    assert job["status"] == "ready", job
    assert [s["type"] for s in job["sources"]] == ["upload", "syllabus"]
    assert [ch["title"] for ch in job["outline"]["chapters"]] == ["Inner join", "Left join"]
    lesson = job["outline"]["chapters"][0]["lessons"][0]
    assert lesson["citations"], "lessons cite the extra material"


async def test_media_failure_is_reported_on_the_source(app_client, monkeypatch):
    _mock_pipeline(monkeypatch)

    def broken(wav, state=None):
        raise SourceError("Local transcription (faster-whisper) is not installed on this server.")
    _, token = await _new_user()
    async with _client(token) as c:
        r = await c.post("/api/books/media", files=_files(("talk.m4a", FAKE_MP4)))
        assert r.status_code == 201, r.text
        monkeypatch.setattr(media_mod, "transcribe_file", broken)
        created = r.json()
        await runner.wait(created["jobId"], timeout=120)
        job = (await c.get(f"/api/books/jobs/{created['jobId']}")).json()
    assert job["status"] == "failed"
    assert "faster-whisper" in job["sources"][0]["error"] and job["error"] == "All sources failed to ingest."
