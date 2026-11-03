"""
tools/voice-eval/run_indicf5.py — IndicF5 (AI4Bharat, MIT) speaking the eval set in a reference voice.

Runs in its own env (tools/voice-eval/.venv-f5) so IndicF5's pinned numpy/transformers never
touch the backend env. Reads Tamil-script engine text from out/tts/results.json (written by
run_tts_audition.py) and adds an "indicf5_<name>" engine to it for the blind listening page.

Usage (from repo root):
  tools/voice-eval/.venv-f5/Scripts/python tools/voice-eval/run_indicf5.py [--ref refs/user_ref] [--name user] [--limit N]

Only clone a voice whose owner has agreed to it being used.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "out" / "tts" / "results.json"


def load_hf_token() -> None:
    """HF_TOKEN from backend/.env (IndicF5 is an auto-approved gated repo)."""
    if os.environ.get("HF_TOKEN"):
        return
    env = HERE.parents[1] / "backend" / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("HF_TOKEN="):
            os.environ["HF_TOKEN"] = line.split("=", 1)[1].strip().strip('"')


def voice_gender(wav: Path) -> str:
    """Rough gender label from median pitch, only used to group clips on the page."""
    import librosa
    y, sr = librosa.load(wav, sr=16000)
    f0, voiced, _ = librosa.pyin(y, fmin=60, fmax=400, sr=sr)
    median = float(np.nanmedian(f0[voiced])) if voiced.any() else 0.0
    print(f"reference median pitch: {median:.0f} Hz")
    return "male" if 0 < median < 165 else "female"


class IndicF5:
    """IndicF5 built directly from f5_tts parts (its transformers wrapper breaks under the
    meta-device init that current transformers uses). Same steps as the model card's forward()."""

    def __init__(self) -> None:
        import torch
        from huggingface_hub import hf_hub_download
        from safetensors.torch import load_file
        from f5_tts.infer.utils_infer import load_model, load_vocoder
        from f5_tts.model import DiT

        self.device = "cuda" if torch.cuda.is_available() else "cpu"

        # torchaudio >= 2.9 needs TorchCodec to read audio; soundfile already does the job here.
        import torchaudio

        def _sf_load(path, *_args, **_kwargs):
            data, sr = sf.read(str(path), dtype="float32", always_2d=True)
            return torch.from_numpy(data.T.copy()), sr

        torchaudio.load = _sf_load
        self.vocoder = load_vocoder(vocoder_name="vocos", is_local=False, device=self.device)
        vocab = hf_hub_download("ai4bharat/IndicF5", "checkpoints/vocab.txt")
        self.model = load_model(DiT, dict(dim=1024, depth=22, heads=16, ff_mult=2, text_dim=512, conv_layers=4),
                                mel_spec_type="vocos", vocab_file=vocab, device=self.device)
        state = load_file(hf_hub_download("ai4bharat/IndicF5", "model.safetensors"), device=self.device)
        prefix = "ema_model._orig_mod."
        state = {k[len(prefix):] if k.startswith(prefix) else k: v for k, v in state.items()}
        missing, unexpected = self.model.load_state_dict(state, strict=False)
        print(f"weights: {len(state)} tensors, missing {len(missing)}, unexpected {len(unexpected)}")

    def __call__(self, text: str, ref_audio_path: str, ref_text: str) -> np.ndarray:
        import io as _io
        from pydub import AudioSegment, silence
        from f5_tts.infer.utils_infer import infer_process, preprocess_ref_audio_text

        ref_audio, ref_txt = preprocess_ref_audio_text(ref_audio_path, ref_text)
        audio, _sr, _ = infer_process(ref_audio, ref_txt, text, self.model, self.vocoder,
                                      mel_spec_type="vocos", speed=1.0, device=self.device)
        buf = _io.BytesIO()
        sf.write(buf, audio, samplerate=24000, format="WAV")
        buf.seek(0)
        seg = AudioSegment.from_file(buf, format="wav")
        parts = silence.split_on_silence(seg, min_silence_len=1000, silence_thresh=-50, keep_silence=500, seek_step=10)
        seg = sum(parts, AudioSegment.silent(duration=0)) or seg
        seg = seg.apply_gain(-20.0 - seg.dBFS)
        return np.array(seg.get_array_of_samples())


def load_indicf5() -> IndicF5:
    return IndicF5()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="refs/user_ref", help="path stem: <stem>.wav + <stem>.json (with 'tamil' text)")
    ap.add_argument("--name", default="user")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    load_hf_token()
    ref_wav = HERE / f"{args.ref}.wav"
    ref_text = json.loads((HERE / f"{args.ref}.json").read_text(encoding="utf-8"))["tamil"]
    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    eid = f"indicf5_{args.name}"
    gender = voice_gender(ref_wav)

    t0 = time.monotonic()
    model = load_indicf5()
    print(f"IndicF5 loaded in {time.monotonic() - t0:.1f}s")

    results["engines"][eid] = {"gender": gender, "langs": ["tanglish", "english"], "local": True,
                               "license": "MIT (model) · reference voice used with consent"}
    (HERE / "out" / "tts" / eid).mkdir(parents=True, exist_ok=True)
    items = list(results["items"].items())
    if args.limit:
        items = items[: args.limit]
    for iid, it in items:
        if (HERE / "out" / "tts" / eid / f"{iid}.wav").exists() and eid in it.get("clips", {}):
            continue  # resumable: keep clips from an earlier (interrupted) run
        t0 = time.monotonic()
        # IndicF5 can't read Latin script: English goes in Tamil script too (Indian-English accent).
        audio = model(it.get("engineTextTa") or it["engineText"], ref_audio_path=str(ref_wav), ref_text=ref_text)
        audio = np.asarray(audio)
        if audio.dtype == np.int16:
            audio = audio.astype(np.float32) / 32768.0
        audio = audio.astype(np.float32).reshape(-1)
        dt = time.monotonic() - t0
        sf.write(HERE / "out" / "tts" / eid / f"{iid}.wav", audio, 24000)
        dur = len(audio) / 24000
        it.setdefault("clips", {})[eid] = {"file": f"out/tts/{eid}/{iid}.wav", "seconds": round(dur, 2),
                                           "rtf": round(dt / dur, 2) if dur else None,
                                           "rms": round(float(np.sqrt(np.mean(audio ** 2))), 4)}
        print(f"[{eid}] {iid} {dur:5.1f}s audio in {dt:5.1f}s (rtf {dt / dur if dur else 0:.2f})", flush=True)
        RESULTS.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print("done")


if __name__ == "__main__":
    main()
