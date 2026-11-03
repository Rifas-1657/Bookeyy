"""
backend/scripts/prerender_audio.py — Pre-render all seeded page narrations (offline books).

For each seeded page:
  * every sentence is synthesized through app.voice.pipeline.synthesize_segment
    (so the runtime cache is warm → zero-latency playback, works fully offline)
  * a single per-page audio file + JSON timings (display words, global wordIndex)
    is written to the output folder together with a manifest.json

Usage (from backend/):
    .venv/Scripts/python scripts/prerender_audio.py                    # all pages
    .venv/Scripts/python scripts/prerender_audio.py --pages lesson_python_1_1 --dry-run
    .venv/Scripts/python scripts/prerender_audio.py --out .cache/voice-prerender --format mp3
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def run(args) -> int:
    import numpy as np

    from app.db.seed import PYTHON_PAGES
    from app.voice.audio import decode, encode, silence
    from app.voice.pipeline import split_sentences, synthesize_segment

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = BACKEND / out_dir
    course_dir = out_dir / "python"
    course_dir.mkdir(parents=True, exist_ok=True)

    pages = [p for p in PYTHON_PAGES if not args.pages or p["id"] in args.pages]
    manifest = {"course": "python", "generatedAt": int(time.time()), "pages": []}
    total_audio_ms = 0
    t_start = time.monotonic()

    for page in pages:
        narration = page.get("narration_text", "") or ""
        sentences = split_sentences(narration)
        print(f"[{page['id']}] {len(sentences)} sentences")
        if args.dry_run:
            continue
        pcm_parts = []
        timings: list[dict] = []
        offset_ms = 0
        providers: set[str] = set()
        for idx, (sentence, word_offset) in enumerate(sentences):
            t0 = time.monotonic()
            res = await synthesize_segment(sentence, lang=args.lang, speed=args.speed)
            providers.add(res.provider)
            dt = time.monotonic() - t0
            print(f"   {idx + 1:>2}/{len(sentences)} {res.provider:<10} {res.lang:<8} "
                  f"{res.duration_ms:>5}ms audio {dt:5.1f}s {'(cache)' if res.from_cache else ''}")
            if res.audio_bytes:
                samples, sr = decode(res.audio_bytes, res.mime_type, 24000)
            else:
                samples = silence(res.duration_ms, 24000)
            for t in res.word_timings:
                d = t.to_dict(word_offset)
                d["startMs"] += offset_ms
                d["endMs"] += offset_ms
                timings.append(d)
            pcm_parts.append(samples)
            offset_ms += int(len(samples) * 1000 / 24000)
            if idx < len(sentences) - 1:
                pcm_parts.append(silence(args.pause_ms, 24000))
                offset_ms += args.pause_ms
        if not pcm_parts:
            continue
        audio, mime = encode(np.concatenate(pcm_parts), 24000, args.format)
        ext = "mp3" if "mpeg" in mime else "wav"
        audio_path = course_dir / f"{page['id']}.{ext}"
        audio_path.write_bytes(audio)
        (course_dir / f"{page['id']}.json").write_text(json.dumps(
            {"pageId": page["id"], "mime": mime, "durationMs": offset_ms, "wordTimings": timings},
            ensure_ascii=False, indent=1), encoding="utf-8")
        total_audio_ms += offset_ms
        manifest["pages"].append({
            "pageId": page["id"], "fileName": audio_path.name, "mime": mime, "durationMs": offset_ms,
            "byteSize": len(audio), "sha256": hashlib.sha256(audio).hexdigest(),
            "providers": sorted(providers), "words": len(narration.split()),
        })

    if not args.dry_run:
        (course_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        el = time.monotonic() - t_start
        print(f"Done: {len(manifest['pages'])} pages, {total_audio_ms / 1000:.1f}s audio in {el:.1f}s "
              f"→ {course_dir}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Pre-render seeded page narrations with local TTS")
    ap.add_argument("--out", default=".cache/voice-prerender")
    ap.add_argument("--pages", nargs="*", default=[])
    ap.add_argument("--lang", default="auto", help="auto | tanglish | english")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--format", default="mp3", help="wav | mp3")
    ap.add_argument("--pause-ms", type=int, default=350)
    ap.add_argument("--dry-run", action="store_true")
    sys.exit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()
