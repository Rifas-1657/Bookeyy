"""
tools/voice-eval/run_tts_audition.py — render every TTS candidate on the Bookey eval set.

Tanglish lines go through Bookey's own word-aligned transliteration (Tamil script for the
engine, Latin for the learner), so every engine is judged on the input it would get in prod.

Usage (from repo root):
  backend/.venv/Scripts/python tools/voice-eval/run_tts_audition.py [--engines a,b] [--limit N]

Writes tools/voice-eval/out/tts/<engine>/<id>.wav and tools/voice-eval/out/tts/results.json,
which tools/voice-eval/index.html reads for blind listening.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BACKEND = ROOT / "backend"
OUT = HERE / "out" / "tts"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)  # backend settings resolve .env / cache paths relative to here


# --------------------------------------------------------------------------- engines
# Each engine: (gender, langs, local?, synth(text_for_engine, lang) -> (float32 audio, sample_rate))

def _edge(voice: str):
    async def run(text: str, _lang: str):
        import edge_tts
        chunks = []
        async for c in edge_tts.Communicate(text, voice).stream():
            if c["type"] == "audio":
                chunks.append(c["data"])
        audio, sr = sf.read(io.BytesIO(b"".join(chunks)), dtype="float32")
        return audio, sr
    return run


def _pocket():
    from app.voice.tts import pocket_tts_provider as p

    async def run(text: str, _lang: str):
        res = await p.PocketTTSProvider(output_format="wav").synthesize(text)
        audio, sr = sf.read(io.BytesIO(res.audio_bytes), dtype="float32")
        return audio, sr
    return run


_KOKORO = None


def _kokoro(voice: str):
    async def run(text: str, _lang: str):
        global _KOKORO
        if _KOKORO is None:
            from kokoro_onnx import Kokoro
            from app.voice.tts.kokoro_provider import _paths  # model files already downloaded by the app
            model, voices = _paths()
            _KOKORO = Kokoro(str(model), str(voices))
        lang = "en-gb" if voice.startswith("b") else "en-us"
        audio, sr = await asyncio.to_thread(_KOKORO.create, text, voice=voice, speed=1.0, lang=lang)
        return np.asarray(audio, dtype="float32"), sr
    return run


_PIPER: dict[str, object] = {}


def _piper(name: str):
    async def run(text: str, _lang: str):
        from huggingface_hub import hf_hub_download
        from piper import PiperVoice
        if name not in _PIPER:
            onnx = hf_hub_download("Jeyaram-K/piper-tamil-voices", f"{name}/{name}.onnx")
            hf_hub_download("Jeyaram-K/piper-tamil-voices", f"{name}/{name}.onnx.json")
            _PIPER[name] = PiperVoice.load(onnx)
        voice = _PIPER[name]
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            if hasattr(voice, "synthesize_wav"):
                await asyncio.to_thread(voice.synthesize_wav, text, wf)
            else:
                await asyncio.to_thread(voice.synthesize, text, wf)
        audio, sr = sf.read(io.BytesIO(buf.getvalue()), dtype="float32")
        return audio, sr
    return run


ENGINES = {
    # id: (gender, langs, local, factory, license)
    "pocket_geetha": ("female", {"tanglish"}, True, _pocket, "MIT"),
    "piper_hemalatha": ("female", {"tanglish"}, True, lambda: _piper("ta_IN-HemaLatha-medium"), "see model card"),
    "piper_valluvar": ("male", {"tanglish"}, True, lambda: _piper("ta_IN-ValluvarNeural-medium"), "see model card"),
    "edge_pallavi": ("female", {"tanglish"}, False, lambda: _edge("ta-IN-PallaviNeural"), "free service"),
    "edge_valluvar": ("male", {"tanglish"}, False, lambda: _edge("ta-IN-ValluvarNeural"), "free service"),
    "kokoro_af_heart": ("female", {"english"}, True, lambda: _kokoro("af_heart"), "Apache-2.0"),
    "kokoro_am_michael": ("male", {"english"}, True, lambda: _kokoro("am_michael"), "Apache-2.0"),
    "kokoro_bm_george": ("male", {"english"}, True, lambda: _kokoro("bm_george"), "Apache-2.0"),
    "edge_neerja": ("female", {"english"}, False, lambda: _edge("en-IN-NeerjaNeural"), "free service"),
    "edge_prabhat": ("male", {"english"}, False, lambda: _edge("en-IN-PrabhatNeural"), "free service"),
}


async def engine_text(item: dict) -> str:
    """What the engine is fed: Tamil script for Tanglish, normalised English otherwise."""
    from app.voice.transliterate import align_english, align_tanglish
    aligned = await align_tanglish(item["text"]) if item["lang"] == "tanglish" else align_english(item["text"])
    return " ".join(a.tts for a in aligned if a.tts)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default=",".join(ENGINES))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    items = json.loads((HERE / "sentences.json").read_text(encoding="utf-8"))["tts"]
    if args.limit:
        items = items[: args.limit]
    results_path = OUT / "results.json"
    results = json.loads(results_path.read_text(encoding="utf-8")) if results_path.exists() else {"engines": {}, "items": {}}

    for it in items:
        results["items"][it["id"]] = {"lang": it["lang"], "text": it["text"], "engineText": await engine_text(it)}

    for eid in [e for e in args.engines.split(",") if e]:
        gender, langs, local, factory, lic = ENGINES[eid]
        try:
            synth = factory()
        except Exception as exc:
            print(f"[{eid}] unavailable: {exc}")
            continue
        results["engines"][eid] = {"gender": gender, "langs": sorted(langs), "local": local, "license": lic}
        (OUT / eid).mkdir(parents=True, exist_ok=True)
        for it in items:
            if it["lang"] not in langs:
                continue
            text = results["items"][it["id"]]["engineText"]
            t0 = time.monotonic()
            try:
                audio, sr = await synth(text, it["lang"])
            except Exception as exc:
                print(f"[{eid}] {it['id']} failed: {exc}")
                continue
            dt = time.monotonic() - t0
            audio = audio if audio.ndim == 1 else audio.mean(axis=1)
            sf.write(OUT / eid / f"{it['id']}.wav", audio, sr)
            dur = len(audio) / sr
            rms = float(np.sqrt(np.mean(audio ** 2))) if len(audio) else 0.0
            results["items"][it["id"]].setdefault("clips", {})[eid] = {
                "file": f"out/tts/{eid}/{it['id']}.wav", "seconds": round(dur, 2),
                "rtf": round(dt / dur, 2) if dur else None, "rms": round(rms, 4),
            }
            print(f"[{eid}] {it['id']} {dur:5.1f}s audio, rtf {dt / dur if dur else 0:.2f}")
        results_path.parent.mkdir(parents=True, exist_ok=True)
        results_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", results_path)


if __name__ == "__main__":
    asyncio.run(main())
