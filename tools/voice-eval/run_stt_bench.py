"""
tools/voice-eval/run_stt_bench.py — word/char error rates for STT candidates on Tanglish questions.

Audio per question (first match wins):
  1. tools/voice-eval/recordings/<id>*.wav   ← real people; record these, they are what counts
  2. tools/voice-eval/out/stt/clips/<id>_<voice>.wav  ← synthetic proxies (made on first run)

Every hypothesis is converted to Latin-script Tanglish with Bookey's own romanizer, then scored
against the Latin reference with WER and CER (CER is fairer: Tanglish spelling varies).

Usage: backend/.venv/Scripts/python tools/voice-eval/run_stt_bench.py [--models a,b]
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import sys
import time
from pathlib import Path

import soundfile as sf

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1] / "backend"
OUT = HERE / "out" / "stt"
REC = HERE / "recordings"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

PROXY_VOICES = {"tanglish": ["ta-IN-ValluvarNeural", "ta-IN-PallaviNeural"], "english": ["en-IN-PrabhatNeural", "en-IN-NeerjaNeural"]}


def norm(text: str) -> str:
    text = text.lower().replace("-", " ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


async def make_proxy_clips(items: list[dict]) -> list[tuple[str, Path, str]]:
    import edge_tts
    from app.voice.transliterate import align_tanglish
    clips = []
    (OUT / "clips").mkdir(parents=True, exist_ok=True)
    for it in items:
        recorded = sorted(REC.glob(f"{it['id']}*.wav")) if REC.exists() else []
        if recorded:
            clips += [(it["id"], p, "recorded") for p in recorded]
            continue
        text = " ".join(a.tts for a in await align_tanglish(it["text"])) if it["lang"] == "tanglish" else it["text"]
        for voice in PROXY_VOICES[it["lang"]]:
            path = OUT / "clips" / f"{it['id']}_{voice}.wav"
            if not path.exists():
                chunks = []
                async for c in edge_tts.Communicate(text, voice).stream():
                    if c["type"] == "audio":
                        chunks.append(c["data"])
                audio, sr = sf.read(io.BytesIO(b"".join(chunks)), dtype="float32")
                sf.write(path, audio, sr)
            clips.append((it["id"], path, "proxy"))
    return clips


# --------------------------------------------------------------------------- models

def m_faster_whisper_turbo():
    from app.voice.stt.faster_whisper import FasterWhisperSTT
    stt = FasterWhisperSTT(model_name="auto")

    async def run(path: Path) -> str:
        return (await stt.transcribe(path.read_bytes(), "audio/wav")).transcript
    return run


def m_hf_whisper(repo: str):
    import torch
    from transformers import pipeline
    device = 0 if torch.cuda.is_available() else -1
    pipe = pipeline("automatic-speech-recognition", model=repo, device=device,
                    torch_dtype=torch.float16 if device == 0 else torch.float32)
    from app.voice.transliterate import romanize_tamil

    async def run(path: Path) -> str:
        audio, sr = sf.read(path, dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        out = await asyncio.to_thread(pipe, {"raw": audio, "sampling_rate": sr},
                                      generate_kwargs={"language": "tamil", "task": "transcribe"})
        return romanize_tamil(out["text"])
    return run


def m_groq():
    import httpx
    from dotenv import dotenv_values
    key = dotenv_values(".env").get("GROQ_API_KEY")
    from app.voice.transliterate import romanize_tamil

    async def run(path: Path) -> str:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post("https://api.groq.com/openai/v1/audio/transcriptions",
                             headers={"Authorization": f"Bearer {key}"},
                             files={"file": (path.name, path.read_bytes(), "audio/wav")},
                             data={"model": "whisper-large-v3", "response_format": "json"})
            r.raise_for_status()
            return romanize_tamil(r.json().get("text", ""))
    return run


MODELS = {
    "faster_whisper_turbo": m_faster_whisper_turbo,
    "whisper_tamil_medium": lambda: m_hf_whisper("vasista22/whisper-tamil-medium"),
    "whisper_tamil_large_v2": lambda: m_hf_whisper("vasista22/whisper-tamil-large-v2"),
    "groq_whisper_large_v3": m_groq,
}


async def main() -> None:
    import jiwer
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(MODELS))
    args = ap.parse_args()

    items = json.loads((HERE / "sentences.json").read_text(encoding="utf-8"))["stt"]
    refs = {it["id"]: it for it in items}
    clips = await make_proxy_clips(items)
    report_path = OUT / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}

    for mid in [m for m in args.models.split(",") if m]:
        try:
            run = MODELS[mid]()
        except Exception as exc:
            print(f"[{mid}] unavailable: {exc}")
            continue
        rows, refs_l, hyps_l, secs = [], [], [], []
        for qid, path, kind in clips:
            t0 = time.monotonic()
            try:
                hyp = await run(path)
            except Exception as exc:
                print(f"[{mid}] {path.name} failed: {exc}")
                continue
            secs.append(time.monotonic() - t0)
            ref = norm(refs[qid]["text"])
            rows.append({"id": qid, "clip": path.name, "kind": kind, "lang": refs[qid]["lang"], "ref": ref, "hyp": norm(hyp)})
            refs_l.append(ref)
            hyps_l.append(norm(hyp) or "<empty>")
        if not rows:
            continue
        tang = [i for i, r in enumerate(rows) if r["lang"] == "tanglish"]
        report[mid] = {
            "wer": round(jiwer.wer(refs_l, hyps_l), 3),
            "cer": round(jiwer.cer(refs_l, hyps_l), 3),
            "tanglish_cer": round(jiwer.cer([refs_l[i] for i in tang], [hyps_l[i] for i in tang]), 3) if tang else None,
            "avg_seconds": round(sum(secs) / len(secs), 2),
            "clips": len(rows),
            "sources": sorted({r["kind"] for r in rows}),
            "rows": rows,
        }
        print(f"[{mid}] WER {report[mid]['wer']}  CER {report[mid]['cer']}  Tanglish CER {report[mid]['tanglish_cer']}  {report[mid]['avg_seconds']}s/clip")
        OUT.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
