"""
tools/finetune/eval_stt.py — which speech recognizer understands Tanglish best?

Scores candidates on the held-out speakers of the Tanglish dataset (split == "test": speakers
never seen in training), with the same character error rate the Kaggle notebook reports, plus
seconds per clip. Results go to tools/voice-eval/out/stt_eval.json.

  backend/.venv/Scripts/python tools/finetune/eval_stt.py --limit 150 --models groq-v3,local-tuned

Models:
  groq-v3, groq-turbo       Groq API (what production uses today; reads GROQ_API_KEY from backend/.env)
  local-base                faster-whisper large-v3-turbo
  local-tuned               the fine-tuned model converted to CTranslate2 (--tuned DIR)
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import io
import json
import os
import re
import sys
import time
from pathlib import Path

import jiwer
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT / "backend")

OUT = ROOT / "tools" / "voice-eval" / "out" / "stt_eval.json"
DATA = HERE / "kernels" / "01" / "out" / "tanglish"


def norm(text: str) -> str:
    """Both sides to Latin Tanglish with Bookey's romanizer, so Tamil-script and Latin output score alike."""
    from app.voice.transliterate import romanize_tamil
    text = romanize_tamil(text or "").lower().replace("-", " ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def test_clips(limit: int) -> list[tuple[bytes, str]]:
    clips = []
    for part in sorted(glob.glob(str(DATA / "part-*.parquet"))):
        rows = pq.read_table(part, columns=["audio", "text", "split"]).to_pylist()
        clips += [(r["audio"]["bytes"], r["text"]) for r in rows if r["split"] == "test"]
    step = max(1, len(clips) // limit)          # spread the sample over all held-out speakers
    return clips[::step][:limit]


def groq(model: str, language: str | None = None):
    from app.config import get_settings
    from app.voice.stt.groq_whisper import DEFAULT_TANG_VOCAB_HINT, GroqWhisperSTT
    stt = GroqWhisperSTT(api_key=get_settings().groq_api_key, model=model, vocab_hint=DEFAULT_TANG_VOCAB_HINT)

    def run(audio: bytes) -> str:
        for wait in (0, 20, 60):                # free tier: back off on rate limits
            time.sleep(wait)
            try:
                return asyncio.run(stt.transcribe(audio_bytes=audio, mime="audio/wav", hint_lang=language)).transcript
            except Exception as exc:            # noqa: BLE001 - report and retry
                last = exc
        raise last
    return run


def local(model_path: str):
    from app.voice.stt.faster_whisper import _add_nvidia_dll_dirs
    from faster_whisper import WhisperModel
    _add_nvidia_dll_dirs()   # cuBLAS from the pip CUDA wheels
    try:
        model = WhisperModel(model_path, device="cuda", compute_type="int8_float16")
    except Exception:                           # noqa: BLE001 - no usable GPU
        model = WhisperModel(model_path, device="cpu", compute_type="int8")

    def run(audio: bytes) -> str:
        segments, _ = model.transcribe(io.BytesIO(audio), language="ta", beam_size=1)
        return " ".join(s.text for s in segments)
    return run


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=150)
    ap.add_argument("--models", default="groq-v3,local-tuned")
    ap.add_argument("--tuned", default=str(HERE / "kernels" / "eval-3" / "whisper_ct2"))
    args = ap.parse_args()

    clips = test_clips(args.limit)
    makers = {"groq-v3": lambda: groq("whisper-large-v3"), "groq-turbo": lambda: groq("whisper-large-v3-turbo"),
              "groq-v3-ta": lambda: groq("whisper-large-v3", "ta"),
              "local-base": lambda: local("large-v3-turbo"), "local-tuned": lambda: local(args.tuned)}
    report = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    for name in args.models.split(","):
        run = makers[name]()
        refs, hyps, seconds = [], [], 0.0
        for audio, ref in clips:
            t0 = time.monotonic()
            hyp = run(audio)
            seconds += time.monotonic() - t0
            refs.append(norm(ref) or "-")
            hyps.append(norm(hyp) or "-")
        report[name] = {"clips": len(clips), "cer": round(jiwer.cer(refs, hyps), 3), "wer": round(jiwer.wer(refs, hyps), 3),
                        "seconds_per_clip": round(seconds / len(clips), 2), "example": [refs[0], hyps[0]]}
        print(name, report[name], flush=True)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
