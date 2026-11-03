"""Real local smoke tests for R2 media books (opt-in: BOOKEY_MEDIA_SMOKE=1).

  1. 20 s video: TTS speech (edge-tts, Windows SAPI fallback) + a Pillow slide, muxed with ffmpeg,
     then the REAL video adapter: ffmpeg audio → faster-whisper → sections → key frame → vision/OCR.
  2. Image OCR: a Pillow PNG with text read by tesseract (if installed) and the vision LLM (if configured).

Run:  BOOKEY_MEDIA_SMOKE=1 .venv/Scripts/python -m pytest tests/test_books_media_smoke.py -q -s
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.knowledge.sources import images as img_mod
from app.knowledge.sources import media as media_mod

pytestmark = pytest.mark.skipif(os.getenv("BOOKEY_MEDIA_SMOKE") != "1", reason="set BOOKEY_MEDIA_SMOKE=1")

SPEECH = ("Photosynthesis is how green plants make food. Light energy, carbon dioxide and water "
          "become glucose and oxygen inside the chloroplast. This happens mainly in the leaves.")


def _font(size: int):
    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _slide(path: Path) -> None:
    im = Image.new("RGB", (1280, 720), (24, 32, 64))
    d = ImageDraw.Draw(im)
    d.text((80, 80), "Photosynthesis", fill="white", font=_font(72))
    d.text((80, 260), "light + CO2 + water", fill=(255, 215, 0), font=_font(48))
    d.text((80, 360), "-> glucose + oxygen", fill=(255, 215, 0), font=_font(48))
    d.rectangle((900, 250, 1180, 450), outline="white", width=6)
    d.text((930, 330), "chloroplast", fill="white", font=_font(36))
    im.save(path)


def _tts(out: Path) -> str:
    try:
        subprocess.run([sys.executable, "-m", "edge_tts", "--voice", "en-US-AriaNeural", "--text", SPEECH,
                        "--write-media", str(out)], check=True, capture_output=True, timeout=90)
        if out.exists() and out.stat().st_size > 1000:
            return "edge-tts"
    except Exception:
        pass
    wav = out.with_suffix(".wav")
    ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          f"$s.SetOutputToWaveFile('{wav}'); $s.Speak('{SPEECH}'); $s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, capture_output=True, timeout=90)
    os.replace(wav, out)
    return "windows-sapi"


def test_real_video_adapter_end_to_end(tmp_path):
    ff = media_mod.ffmpeg_path()
    assert ff, "ffmpeg is required"
    speech = tmp_path / "speech.mp3"
    engine = _tts(speech)
    slide = tmp_path / "slide.png"
    _slide(slide)
    video = tmp_path / "lecture.mp4"
    subprocess.run([ff, "-hide_banner", "-y", "-loop", "1", "-i", str(slide), "-i", str(speech),
                    "-af", "apad", "-t", "20", "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(video)], check=True, capture_output=True, timeout=120)
    assert video.stat().st_size > 10_000
    seen: list[str] = []

    async def progress(d):
        seen.append(d)

    t0 = time.monotonic()
    doc = asyncio.run(media_mod.media_to_document(str(video), title="Smoke lecture", filename="lecture.mp4",
                                                  kind="video", max_seconds=7200, on_progress=progress))
    took = time.monotonic() - t0
    print(f"\n[smoke] tts={engine} video={video.stat().st_size // 1024} KB adapter={took:.1f}s "
          f"method={doc.metadata['method']} lang={doc.metadata['language']} "
          f"segments={doc.metadata['segmentCount']} frames={doc.metadata['frameCount']}")
    print("[smoke] progress:", seen)
    print("[smoke] document:\n" + doc.text[:1500])
    low = doc.text.lower()
    assert "photosynthesis" in low and ("chloroplast" in low or "glucose" in low)
    assert doc.metadata["durationSeconds"] == pytest.approx(20, abs=1.5)
    assert seen[0] == "Extracting audio" and any(s.startswith("Transcribing") for s in seen)
    assert "## Part 1 (0:00" in doc.text


def test_real_image_ocr(tmp_path):
    im = Image.new("RGB", (1000, 400), "white")
    d = ImageDraw.Draw(im)
    d.text((40, 60), "Mitochondria is the powerhouse of the cell", fill="black", font=_font(40))
    d.text((40, 180), "ATP = energy currency", fill="black", font=_font(40))
    d.line((40, 300, 600, 300), fill="black", width=4)
    path = tmp_path / "notes.png"
    im.save(path)
    jpeg, _w, _h = img_mod.prepare_image(path.read_bytes())
    results = {}
    if img_mod.tesseract_cmd():
        r = asyncio.run(img_mod.tesseract_read(jpeg))
        results["tesseract"] = r
        print(f"\n[smoke] tesseract ({r.model}): {r.text!r}")
        assert "powerhouse" in r.text.lower() and "atp" in r.text.lower()
    if img_mod.vision_available():
        r = asyncio.run(img_mod.vision_read(jpeg))
        results["vision"] = r
        print(f"[smoke] vision ({r.model}): error={r.error} kind={r.kind} text={r.text!r} diagrams={r.diagrams}")
        if r.error is None:
            assert "powerhouse" in r.text.lower()
    assert results, "neither tesseract nor a vision model is available"
