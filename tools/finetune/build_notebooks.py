"""
tools/finetune/build_notebooks.py — generate the three Kaggle notebooks from this folder.

  python tools/finetune/build_notebooks.py

Notebook 1 embeds tanglish_data.py verbatim (%%writefile), so the code that runs on Kaggle is
exactly the code tested by test_tanglish_data.py. Re-run this after editing either file.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": text.strip("\n").splitlines(keepends=True)}


def code(text: str) -> dict:
    return {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
            "source": text.strip("\n").splitlines(keepends=True)}


def notebook(cells: list[dict], gpu: bool) -> dict:
    return {
        "nbformat": 4, "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
            "language_info": {"name": "python"},
            "kaggle": {"accelerator": "nvidiaTeslaT4" if gpu else "none", "isInternetEnabled": True,
                       "isGpuEnabled": gpu},
        },
        "cells": cells,
    }


SECRETS = '''
# Kaggle: Add-ons → Secrets → add HF_TOKEN (a Hugging Face token with WRITE access).
# Elsewhere (Colab / Lightning / local): export HF_TOKEN before starting.
import os
try:
    from kaggle_secrets import UserSecretsClient
    os.environ["HF_TOKEN"] = UserSecretsClient().get_secret("HF_TOKEN").strip()
except Exception:
    assert os.environ.get("HF_TOKEN"), "Set HF_TOKEN (Kaggle secret or environment variable)"
os.environ["HF_TOKEN"] = os.environ["HF_TOKEN"].strip()
assert os.environ["HF_TOKEN"].startswith("hf_"), (
    "HF_TOKEN must be a Hugging Face token (starts with 'hf_', type Write) from "
    "huggingface.co/settings/tokens - not a Kaggle token")
os.environ["HF_HUB_DISABLE_XET"] = "1"          # plain HTTP: more reliable on slow links
from huggingface_hub import login, whoami
login(os.environ["HF_TOKEN"], add_to_git_credential=False)
HF_USER = whoami()["name"]
print("Logged in as", HF_USER)
'''

# --------------------------------------------------------------------------- notebook 1

NB1 = [
    md('''
# Bookey 1/3 — Build the native Tanglish speech dataset

Streams AI4Bharat's Tamil speech (IndicVoices + IndicVoices-R, CC-BY-4.0), keeps clean utterances
where **native speakers mix English into Tamil**, and pushes a private dataset to your Hugging Face
account. No cloning: thousands of speakers, each kept as themselves; descriptions are style-only.

* Runs on CPU (no GPU needed). Internet **on**. ~1–3 h for 30 h of speech.
* Shards are downloaded one at a time and deleted after filtering, so disk stays small.
* Output: `<you>/bookey-tanglish-speech` (private) with `train` / `test` splits split **by speaker**,
  plus `english_lexicon.json` (native Tamil-script spellings of English words) for Bookey's transliterator.
'''),
    code('!pip -q install "pyarrow>=15" soundfile "huggingface_hub>=0.25" "datasets>=2.20,<4"'),
    code(SECRETS),
    code("%%writefile tanglish_data.py\n" + (HERE / "tanglish_data.py").read_text(encoding="utf-8")),
    code('''
# ---- settings ---------------------------------------------------------------
TARGET_HOURS = 30            # stop once this much clean audio is collected
TEST_SPEAKER_SHARE = 0.02    # ~2% of speakers held out for evaluation
SOURCES = [                  # (repo, shard prefix); IndicVoices first = most natural code-mixing
    ("ai4bharat/IndicVoices", "tamil/"),
    ("ai4bharat/indicvoices_r", "Tamil/"),
]
OUT_REPO = f"{HF_USER}/bookey-tanglish-speech"
WORK = "/kaggle/working/tanglish" if os.path.isdir("/kaggle/working") else "./tanglish"
TMP = "/kaggle/tmp" if os.path.isdir("/kaggle") else "./tmp_shards"
os.makedirs(WORK, exist_ok=True); os.makedirs(TMP, exist_ok=True)
'''),
    code('''
import hashlib, json, os, time
import pyarrow as pa, pyarrow.parquet as pq
from huggingface_hub import HfApi, hf_hub_download
import tanglish_data as td

api = HfApi()
cfg = td.FilterConfig()
stats = td.Stats()
lexicon_rows, buffer, part = [], [], 0
SCHEMA = pa.schema([
    ("audio", pa.struct([("bytes", pa.binary()), ("path", pa.string())])),
    ("text", pa.string()), ("description", pa.string()), ("duration", pa.float64()),
    ("speaker_id", pa.string()), ("gender", pa.string()), ("scenario", pa.string()),
    ("english_words", pa.list_(pa.string())), ("source", pa.string()), ("split", pa.string()),
])

def split_of(speaker):
    h = int(hashlib.sha1(speaker.encode()).hexdigest(), 16) % 10_000
    return "test" if h < TEST_SPEAKER_SHARE * 10_000 else "train"

def flush():
    global buffer, part
    if not buffer:
        return
    pq.write_table(pa.Table.from_pylist(buffer, schema=SCHEMA), f"{WORK}/part-{part:05d}.parquet")
    part += 1
    buffer = []

t0 = time.time()
done = False
for repo, prefix in SOURCES:
    shards = sorted(f for f in api.list_repo_files(repo, repo_type="dataset")
                    if f.startswith(prefix) and f.endswith(".parquet"))
    if repo.endswith("indicvoices_r"):
        # IndicVoices-R has no [english] tags: spot English via native spellings learned so far.
        cfg.english_ta = frozenset(td.native_english_spellings(lexicon_rows).values())
        print(f"learned {len(cfg.english_ta)} native spellings of English words")
    print(f"{repo}: {len(shards)} shards")
    for shard in shards:
        path = hf_hub_download(repo, shard, repo_type="dataset", local_dir=TMP)
        for ex in td.filter_shard(path, cfg, stats):
            lexicon_rows.append({"unsanitized": ex.pop("unsanitized")})
            ex["source"] = repo.split("/")[-1]
            ex["split"] = split_of(ex["speaker_id"] or ex["audio"]["path"])
            buffer.append(ex)
            if len(buffer) >= 2000:
                flush()
        os.remove(path)
        hours = stats.kept_seconds / 3600
        print(f"  {shard}: kept {stats.kept}/{stats.seen} ({hours:.1f} h, {stats.kept_mixed} code-mixed) "
              f"after {(time.time() - t0) / 60:.0f} min")
        if hours >= TARGET_HOURS:
            done = True
            break
    if done:
        break
flush()
print("dropped:", stats.reasons)
'''),
    code('''
# Native spellings of English words (school → ஸ்கூல்) for Bookey's transliteration lexicon.
best = td.native_english_spellings(lexicon_rows)
json.dump(best, open(f"{WORK}/english_lexicon.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(best), "English words, e.g.", list(best.items())[:15])
'''),
    code('''
from datasets import Audio, load_dataset
ds = load_dataset("parquet", data_files=f"{WORK}/part-*.parquet", split="train")
ds = ds.cast_column("audio", Audio())
splits = {name: ds.filter(lambda s, n=name: s == n, input_columns="split").remove_columns("split")
          for name in ("train", "test")}
from datasets import DatasetDict
dd = DatasetDict(splits)
print(dd, {k: round(sum(v["duration"]) / 3600, 2) for k, v in dd.items()}, "hours")
dd.push_to_hub(OUT_REPO, private=True, max_shard_size="500MB")
api.upload_file(path_or_fileobj=f"{WORK}/english_lexicon.json", path_in_repo="english_lexicon.json",
                repo_id=OUT_REPO, repo_type="dataset")
print("Pushed https://huggingface.co/datasets/" + OUT_REPO)
'''),
    md('''
**Attribution (required by CC-BY-4.0):** derived from AI4Bharat IndicVoices and IndicVoices-R
(https://ai4bharat.iitm.ac.in). Keep this note in the dataset card and in Bookey's credits.
'''),
]

# --------------------------------------------------------------------------- notebook 2

NB2 = [
    md('''
# Bookey 2/3 — Fine-tune Indic Parler-TTS for native Tanglish *style*

Starts from `ai4bharat/indic-parler-tts` (Apache-2.0) and fine-tunes it on the dataset from
notebook 1. Voices come from a **text description**, not a reference recording, so the result
speaks *like* native Tanglish speakers without copying anyone. Bookey then fixes two descriptions
(male / female teacher) as its house voices.

* Kaggle: Accelerator **GPU T4 x2** (or P100), Internet **on**. No secrets: the base model and data are attached as Kaggle inputs.
* Memory: the 24-layer decoder (~0.6 B params) is trained, the Flan-T5 text encoder is frozen;
  fp16 + gradient checkpointing + batch 2 fits a 16 GB GPU. If you hit out-of-memory, run this same
  notebook on a 24 GB GPU (Lightning AI / Modal free credits) and double `BATCH`.
* Sessions are 12 h max: checkpoints are pushed every `SAVE_STEPS`, and re-running resumes.
'''),
    code('''
!pip -q install "parler_tts[train] @ git+https://github.com/huggingface/parler-tts.git" accelerate jiwer
!pip -q install -U "protobuf>=5.29,<7"   # parler pins an old protobuf that transformers cannot import
!git clone -q https://github.com/huggingface/parler-tts.git /kaggle/working/parler-tts 2>/dev/null || true
# Upstream indexes the optional metadata dataset list even when there is none.
# pip also installs a copy of `training`, and that is the one the script imports.
!find /kaggle/working/parler-tts /usr/local/lib -path '*training/data.py' -exec sed -i 's/metadata_dataset_names\[i\]/(metadata_dataset_names[i] if metadata_dataset_names else None)/' {} +
# transformers' DacModel takes no `bandwidth` and returns codes without the leading chunk axis.
from pathlib import Path as _P
_run = _P("/kaggle/working/parler-tts/training/run_parler_tts_training.py")
_src = _run.read_text()
for _old, _new in (
    ('if bandwidth is not None:',
     'if bandwidth is not None and "bandwidth" in inspect.signature(audio_decoder.encode).parameters:'),
    ('output["labels"] = labels.squeeze(0).transpose(1, 2)',
     'output["labels"] = (labels if labels.dim() == 4 else labels.unsqueeze(0)).squeeze(0).transpose(1, 2)'),
):
    assert _old in _src, _old
    _src = _src.replace(_old, _new)
_run.write_text(_src)
'''),
    code('''
import glob, os, shutil
parts = sorted(glob.glob("/kaggle/input/**/part-*.parquet", recursive=True))
assert parts, "attach the output of bookey-1-tanglish-dataset"
BASE = os.path.dirname(glob.glob("/kaggle/input/**/model.safetensors", recursive=True)[0])
DATA_DIR = "/kaggle/tmp/tanglish"   # train-*/test-* parquet files: load_dataset() finds the splits by name
os.makedirs(DATA_DIR, exist_ok=True)
from datasets import load_dataset
raw = load_dataset("parquet", data_files=parts, split="train")
for name in ("train", "test"):
    raw.filter(lambda s, n=name: s == n, input_columns="split").remove_columns("split").to_parquet(f"{DATA_DIR}/{name}-00000-of-00001.parquet")
BATCH, ACCUM = 2, 16                              # effective batch 32
LR = 8e-5                                          # gentle: we adapt style, not relearn Tamil
EPOCHS = 3
SAVE_STEPS = 500
MAX_STEPS = 2500     # about 6-7 h at 9 s/step: fits one 12 h Kaggle session with audio encoding
SMOKE_TEST = False   # True: 60 steps to check memory + audio quality
'''),
    code('''
import subprocess, shlex
args = {
    "model_name_or_path": BASE,
    "feature_extractor_name": BASE,
    "description_tokenizer_name": "google/flan-t5-large",
    "prompt_tokenizer_name": BASE,
    "train_dataset_name": DATA_DIR, "train_dataset_config_name": "default", "train_split_name": "train",
    "eval_dataset_name": DATA_DIR, "eval_dataset_config_name": "default", "eval_split_name": "test",
    "target_audio_column_name": "audio", "description_column_name": "description",
    "prompt_column_name": "text",
    "max_duration_in_seconds": 15, "min_duration_in_seconds": 2, "max_text_length": 400,
    "do_train": True, "do_eval": True, "predict_with_generate": False,
    "per_device_train_batch_size": BATCH, "per_device_eval_batch_size": BATCH,
    "gradient_accumulation_steps": ACCUM, "gradient_checkpointing": True,
    "learning_rate": LR, "lr_scheduler_type": "cosine", "warmup_steps": 100,
    "adam_beta1": 0.9, "adam_beta2": 0.99, "weight_decay": 0.01,
    "num_train_epochs": EPOCHS, "freeze_text_encoder": True,
    "dtype": "float16", "audio_encoder_per_device_batch_size": 8,
    "eval_steps": SAVE_STEPS, "save_steps": SAVE_STEPS, "save_total_limit": 1, "logging_steps": 10, "max_steps": MAX_STEPS,
    "output_dir": "/kaggle/working/parler_out", "overwrite_output_dir": False,
    "temporary_save_to_disk": "/kaggle/tmp/audio_codes", "save_to_disk": "/kaggle/tmp/encoded",
    "preprocessing_num_workers": 4, "dataloader_num_workers": 4, "group_by_length": True,
    "push_to_hub": False,
    "report_to": "none", "seed": 42, "attn_implementation": "sdpa",
}
if SMOKE_TEST:
    args.update(max_steps=60, max_train_samples=400, max_eval_samples=32, eval_steps=60, save_steps=60,
                )
cli = " ".join(f"--{k} {shlex.quote(str(v).lower() if isinstance(v, bool) else str(v))}" for k, v in args.items())
cmd = f"cd /kaggle/working/parler-tts && accelerate launch --mixed_precision fp16 training/run_parler_tts_training.py {cli}"
print(cmd[:400], "...")
!{cmd}
assert os.path.isdir("/kaggle/working/parler_out"), "training failed: see the log above"
'''),
    code('''
# Listen: Bookey's two house voices, same sentence, base model vs fine-tuned.
import torch, soundfile as sf
from IPython.display import Audio, display
from parler_tts import ParlerTTSForConditionalGeneration
from transformers import AutoTokenizer

TEXT = "இன்னைக்கு நம்ம பைதான் வேரியபிள்ஸ் பத்தி கத்துக்கப்போறோம். ரொம்ப சிம்பிள் தான், கவலைப்படாதீங்க!"
VOICES = {
    "male": "A male teacher talks in a warm, conversational Tamil style, mixing English words naturally "
            "like a native Tanglish speaker, at a moderate pace. The recording is clear and close to the microphone.",
    "female": "A female teacher talks in a warm, conversational Tamil style, mixing English words naturally "
              "like a native Tanglish speaker, at a moderate pace. The recording is clear and close to the microphone.",
}
device = "cuda" if torch.cuda.is_available() else "cpu"
prompt_tok = AutoTokenizer.from_pretrained(BASE)
desc_tok = AutoTokenizer.from_pretrained("google/flan-t5-large")
for name, ckpt in [("base", BASE), ("tuned", "/kaggle/working/parler_out")]:
    try:
        model = ParlerTTSForConditionalGeneration.from_pretrained(ckpt).to(device)
    except Exception as e:
        print(name, "not available:", e); continue
    for gender, desc in VOICES.items():
        torch.manual_seed(0)
        d = desc_tok(desc, return_tensors="pt").to(device)
        p = prompt_tok(TEXT, return_tensors="pt").to(device)
        audio = model.generate(input_ids=d.input_ids, attention_mask=d.attention_mask,
                               prompt_input_ids=p.input_ids, prompt_attention_mask=p.attention_mask)
        wav = audio.cpu().numpy().squeeze()
        sf.write(f"/kaggle/working/{name}_{gender}.wav", wav, model.config.sampling_rate)
        print(name, gender); display(Audio(wav, rate=model.config.sampling_rate))
    del model; torch.cuda.empty_cache()
'''),
    md('''
Download `tuned_male.wav` / `tuned_female.wav` (Output panel) and drop them into Bookey's voice test
page. If tuned beats base and today's engines, set `SMOKE_TEST = False`, run again (≈ 10–20 GPU-hours over a
few sessions), and Bookey will load `<you>/bookey-parler-tanglish` through its `indic_parler` provider.
'''),
]

# --------------------------------------------------------------------------- notebook 3

NB3 = [
    md('''
# Bookey 3/3 — Whisper-turbo LoRA for Tanglish questions

Fine-tunes `openai/whisper-large-v3-turbo` (the model Bookey already runs with faster-whisper) with
LoRA on the native Tanglish dataset. Targets stay in Tamil script (English words in Tamil script, as
native transcribers write them); Bookey's romanizer turns the output into the Latin Tanglish the
learner sees. The merged model is converted to CTranslate2 int8 for faster-whisper.

* Kaggle: GPU T4 (x1 is enough), Internet on, no secrets needed: data comes from notebook 1's output. ~3–5 h.
'''),
    code('!pip -q install "transformers>=4.44,<4.53" "peft>=0.12" accelerate "datasets>=2.20,<4" jiwer evaluate soundfile librosa ctranslate2 && pip -q uninstall -y torchao'),
    code('''
import os
os.environ["CUDA_VISIBLE_DEVICES"] = "0"   # two GPUs through DataParallel were slower here
BASE = "openai/whisper-large-v3-turbo"
MAX_STEPS, BATCH, ACCUM, LR = 1500, 4, 4, 1e-4   # batch 8 runs out of memory on a 16 GB T4
'''),
    code('''
import torch
from datasets import Audio, load_dataset
from transformers import WhisperForConditionalGeneration, WhisperProcessor

processor = WhisperProcessor.from_pretrained(BASE, language="tamil", task="transcribe")
import glob, os
from datasets import DatasetDict
parts = sorted(glob.glob("/kaggle/input/**/part-*.parquet", recursive=True))
assert parts, "attach the output of bookey-1-tanglish-dataset"
raw = load_dataset("parquet", data_files=parts, split="train")
ds = DatasetDict({n: raw.filter(lambda s, n=n: s == n, input_columns="split") for n in ("train", "test")})
ds = ds.cast_column("audio", Audio(sampling_rate=16_000))

def prepare(batch):
    a = batch["audio"]
    batch["input_features"] = processor.feature_extractor(a["array"], sampling_rate=16_000).input_features[0]
    batch["labels"] = processor.tokenizer(batch["text"]).input_ids
    return batch

ds = ds.map(prepare, remove_columns=ds["train"].column_names, num_proc=2)
# Tamil script is token-heavy in Whisper's BPE (~130 tokens for a 6 s clip); the decoder max is 448.
ds = ds.filter(lambda labels: len(labels) <= 440, input_columns="labels")
print(ds)
'''),
    code('''
from dataclasses import dataclass
from peft import LoraConfig, get_peft_model

# fp32 weights: with fp16 weights under fp16 autocast the loss went to NaN in the first steps.
model = WhisperForConditionalGeneration.from_pretrained(BASE, torch_dtype=torch.float32)
model.config.forced_decoder_ids = None
model.generation_config.language, model.generation_config.task = "tamil", "transcribe"
model = get_peft_model(model, LoraConfig(r=32, lora_alpha=64, lora_dropout=0.05,
                                         target_modules=["q_proj", "k_proj", "v_proj", "out_proj", "fc1", "fc2"]))
model.print_trainable_parameters()

@dataclass
class Collator:
    def __call__(self, features):
        inputs = processor.feature_extractor.pad([{"input_features": f["input_features"]} for f in features], return_tensors="pt")
        labels = processor.tokenizer.pad([{"input_ids": f["labels"]} for f in features], return_tensors="pt")
        lab = labels["input_ids"].masked_fill(labels.attention_mask.ne(1), -100)
        if (lab[:, 0] == processor.tokenizer.convert_tokens_to_ids("<|startoftranscript|>")).all():
            lab = lab[:, 1:]
        inputs["labels"] = lab
        return inputs
'''),
    code('''
import evaluate
import math
from transformers import Seq2SeqTrainer, Seq2SeqTrainingArguments, TrainerCallback

class StopOnBrokenLoss(TrainerCallback):
    """Fail in minutes instead of burning GPU hours on a run that has stopped learning."""
    def on_log(self, args, state, control, logs=None, **kwargs):
        loss = (logs or {}).get("loss")
        if loss is not None and (math.isnan(loss) or loss == 0 or loss > 50):
            raise RuntimeError(f"training broke at step {state.global_step}: loss={loss}")

cer = evaluate.load("cer")

def metrics(pred):
    ids = pred.label_ids; ids[ids == -100] = processor.tokenizer.pad_token_id
    hyp = processor.batch_decode(pred.predictions, skip_special_tokens=True)
    ref = processor.batch_decode(ids, skip_special_tokens=True)
    return {"cer": cer.compute(predictions=hyp, references=ref)}

trainer = Seq2SeqTrainer(
    model=model, data_collator=Collator(), train_dataset=ds["train"], eval_dataset=ds["test"].select(range(min(100, len(ds["test"])))),
    compute_metrics=metrics, callbacks=[StopOnBrokenLoss()],
    args=Seq2SeqTrainingArguments(
        output_dir="/kaggle/working/whisper_out", per_device_train_batch_size=BATCH, gradient_accumulation_steps=ACCUM,
        learning_rate=LR, warmup_steps=200, max_steps=MAX_STEPS, fp16=True, eval_strategy="steps", eval_steps=500,
        save_steps=500, save_total_limit=2, logging_steps=25, predict_with_generate=True, generation_max_length=444,
        per_device_eval_batch_size=4, gradient_checkpointing=True, gradient_checkpointing_kwargs={"use_reentrant": False}, remove_unused_columns=False, label_names=["labels"], report_to="none"),
)
print("baseline:", trainer.evaluate())
trainer.train(resume_from_checkpoint=any(p.startswith("checkpoint") for p in os.listdir("/kaggle/working/whisper_out")) if os.path.isdir("/kaggle/working/whisper_out") else False)
print("fine-tuned:", trainer.evaluate())
'''),
    code('''
# Merge LoRA → full model → CTranslate2 int8 (what Bookey's faster-whisper loads), kept in this notebook's output.
merged = model.merge_and_unload()
merged.save_pretrained("/kaggle/working/whisper_merged"); processor.save_pretrained("/kaggle/working/whisper_merged")
!ct2-transformers-converter --model /kaggle/working/whisper_merged --output_dir /kaggle/working/whisper_ct2 --quantization int8_float16 --copy_files tokenizer.json preprocessor_config.json --force
print("Done: download whisper_ct2 from this notebook's output and set FASTER_WHISPER_MODEL to it")
'''),
]


NB4 = [
    md('''
# Bookey 4 — Lightweight Tanglish voices with Piper

Piper (VITS) voices are ~60 MB ONNX files that speak faster than real time on a plain CPU, so
Bookey can talk instantly without a GPU. This fine-tunes the public-domain LJSpeech Piper `medium` checkpoint on the
Tanglish dataset from notebook 1, **multi-speaker**, then adds two **house voices** that are the
average of many speakers (one male, one female) — the result speaks in a native Tanglish *style*
without copying any single person.

* Kaggle: GPU **T4** (one is used), Internet on, attach the output of `bookey-1-tanglish-dataset`. No secrets.
* Sessions stop at 11 h and save `last.ckpt`; attach this notebook's own output to resume.
* `SMOKE_TEST = True` first: 2 epochs on a small subset, checks training, averaging and export.
'''),
    code('''
!apt-get -qq update && apt-get -qq install -y build-essential cmake ninja-build espeak-ng > /dev/null
# Built outside /kaggle/working so the notebook's output stays small.
!git clone -q https://github.com/OHF-voice/piper1-gpl.git /kaggle/tmp/piper1-gpl 2>/dev/null || true
%cd /kaggle/tmp/piper1-gpl
# The val_mos checkpoint needs the UTMOS scorer from torch.hub (GitHub); when that download is
# refused, current Lightning raises instead of skipping. Keep the val_mel and last.ckpt checkpoints.
_main = "src/piper/train/__main__.py"
_src = open(_main).read()
_at = _src.index('monitor="val_mos"')
_begin = _src.rindex("ModelCheckpoint(", 0, _at)
_src = _src[:_begin] + _src[_src.index("),", _at) + 2:]
# New torch exports through torch.export (dynamo), which trips on VITS' spline flow; use the classic exporter.
_exp = "src/piper/train/export_onnx.py"
_e = open(_exp).read()
assert "opset_version=OPSET_VERSION," in _e
open(_exp, "w").write(_e.replace("opset_version=OPSET_VERSION,", "opset_version=OPSET_VERSION, dynamo=False,", 1))
open(_main, "w").write(_src)
!pip -q install scikit-build cmake ninja onnx onnxscript onnxruntime && pip -q install -e '.[train]'
!./build_monotonic_align.sh > /dev/null && python3 setup.py build_ext --inplace 2>&1 | tail -3
!python3 -c "from piper import espeakbridge; print('espeak bridge ok')"
%cd /kaggle/working
'''),
    code('''
import glob, io, json, os, random
import numpy as np, soundfile as sf, librosa
from datasets import load_dataset

SMOKE_TEST = False
SPEAKERS_PER_GENDER = 6 if SMOKE_TEST else 20
MINUTES_PER_SPEAKER = 1.5 if SMOKE_TEST else 2.5      # ~1.7 h in total for the full run
MAX_EPOCHS = 2 if SMOKE_TEST else 1500                 # ~75 vocoder passes over clips of ~7 s
SR = 22050
WAVS, CSV = "/kaggle/tmp/piper/wavs", "/kaggle/tmp/piper/metadata.csv"
OUT = "/kaggle/working/piper"
os.makedirs(WAVS, exist_ok=True); os.makedirs(OUT, exist_ok=True)

parts = sorted(glob.glob("/kaggle/input/**/part-*.parquet", recursive=True))
assert parts, "attach the output of bookey-1-tanglish-dataset"
ds = load_dataset("parquet", data_files=parts, split="train")
ds = ds.filter(lambda s, d: s == "train" and 2.0 <= d <= 10.0, input_columns=["split", "duration"])
meta = ds.select_columns(["speaker_id", "gender", "duration"]).to_pandas()
totals = meta.groupby(["gender", "speaker_id"])["duration"].sum().reset_index()
chosen = {g: list(totals[totals.gender == g].nlargest(SPEAKERS_PER_GENDER, "duration").speaker_id) for g in ("male", "female")}
budget = {s: MINUTES_PER_SPEAKER * 60 for g in chosen for s in chosen[g]}
print({g: len(v) for g, v in chosen.items()})

rows = []
for i, ex in enumerate(ds):
    s = ex["speaker_id"]
    if budget.get(s, 0) <= 0:
        continue
    audio, sr = sf.read(io.BytesIO(ex["audio"]["bytes"]), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = librosa.resample(audio, orig_sr=sr, target_sr=SR)
    name = f"u{i}.wav"
    sf.write(f"{WAVS}/{name}", audio, SR)
    rows.append(f"{name}|{s}|{ex['text'].replace('|', ' ').strip()}")
    budget[s] -= ex["duration"]
open(CSV, "w", encoding="utf-8").write("\\n".join(rows) + "\\n")
GENDER = {s: g for g in chosen for s in chosen[g]}
json.dump(GENDER, open(f"{OUT}/speaker_gender.json", "w"))
print(len(rows), "clips,", round(sum(meta.set_index("speaker_id").duration.groupby(level=0).sum().clip(upper=MINUTES_PER_SPEAKER * 60)[list(GENDER)]) / 3600, 2), "h")
'''),
    code('''
from huggingface_hub import hf_hub_download
base = hf_hub_download("rhasspy/piper-checkpoints", "en/en_US/ljspeech/medium/lj-med_1000.ckpt"   # LJSpeech: public domain, repo_type="dataset")
previous = sorted(glob.glob("/kaggle/input/**/piper/**/last.ckpt", recursive=True))
start = f"--ckpt_path {previous[-1]}" if previous else f"--model.warmstart_ckpt {base}"
print("starting from", previous[-1] if previous else "the public-domain LJSpeech checkpoint")
cmd = (f"python3 -m piper.train fit --data.voice_name bookey_tanglish --data.csv_path {CSV} --data.audio_dir {WAVS} "
       f"--model.sample_rate {SR} --data.espeak_voice ta --data.cache_dir /kaggle/tmp/piper/cache "
       f"--data.config_path {OUT}/config.json --data.batch_size 24 --trainer.max_epochs {MAX_EPOCHS} "
       # One GPU: under DDP only rank 0 writes the voice config and rank 1 fails in setup().
       f"--trainer.max_time 00:11:00:00 --trainer.devices 1 --trainer.accelerator gpu "
       f"--trainer.default_root_dir {OUT} --model.num_speakers {len(GENDER)} {start}")
print(cmd)
!{cmd} 2>&1 | grep -vE "^\\s*$" | tail -60
assert glob.glob(f"{OUT}/lightning_logs/*/checkpoints/last.ckpt"), "training failed: see the log above"
'''),
    code('''
# House voices: average many speakers' embeddings per gender, so no single person's voice is reused.
import torch
last = sorted(glob.glob(f"{OUT}/lightning_logs/*/checkpoints/last.ckpt"), key=os.path.getmtime)[-1]
ckpt = torch.load(last, map_location="cpu", weights_only=False)
config = json.load(open(f"{OUT}/config.json"))
id_map = config["speaker_id_map"]
key = next(k for k in ckpt["state_dict"] if k.endswith("emb_g.weight"))
emb = ckpt["state_dict"][key]
houses = []
for gender in ("female", "male"):
    ids = [id_map[s] for s, g in GENDER.items() if g == gender and s in id_map]
    houses.append(emb[ids].mean(dim=0, keepdim=True))
    id_map[f"bookey_{gender}"] = emb.shape[0] + len(houses) - 1
ckpt["state_dict"][key] = torch.cat([emb, *houses])
ckpt["hyper_parameters"]["num_speakers"] = emb.shape[0] + 2
config["num_speakers"] = emb.shape[0] + 2
torch.save(ckpt, f"{OUT}/house.ckpt")
json.dump(config, open(f"{OUT}/ta-bookey-medium.onnx.json", "w"), ensure_ascii=False, indent=1)
!python3 -m piper.train.export_onnx --checkpoint {OUT}/house.ckpt --output-file {OUT}/ta-bookey-medium.onnx
print({k: v for k, v in id_map.items() if k.startswith("bookey_")}, os.path.getsize(f"{OUT}/ta-bookey-medium.onnx") // 2**20, "MB")
'''),
    code('''
# Listen: both house voices on a Tanglish teaching sentence, timed on CPU.
# /kaggle/working/piper (this notebook's output) would shadow the piper package, so import from elsewhere.
import sys, time
os.chdir('/kaggle/tmp')
sys.path = [p for p in sys.path if p not in ('', '/kaggle/working')]
from piper import PiperVoice, SynthesisConfig
from IPython.display import Audio, display
voice = PiperVoice.load(f"{OUT}/ta-bookey-medium.onnx", config_path=f"{OUT}/ta-bookey-medium.onnx.json", use_cuda=False)
TEXT = "இன்னைக்கு நம்ம பைதான் வேரியபிள்ஸ் பத்தி கத்துக்கப்போறோம். ரொம்ப சிம்பிள் தான், கவலைப்படாதீங்க!"
for gender in ("female", "male"):
    t0 = time.time()
    chunks = list(voice.synthesize(TEXT, SynthesisConfig(speaker_id=id_map[f"bookey_{gender}"])))
    audio = np.concatenate([c.audio_float_array for c in chunks])
    print(gender, f"{time.time() - t0:.2f}s on CPU for {len(audio) / chunks[0].sample_rate:.1f}s of speech")
    sf.write(f"{OUT}/sample_{gender}.wav", audio, chunks[0].sample_rate)
    display(Audio(audio, rate=chunks[0].sample_rate))
'''),
]


# Bookey 5: the same recipe as notebook 3 on whisper-small, light enough for a free CPU server.
NB5 = [
    {**cell, "source": [line.replace("whisper-large-v3-turbo", "whisper-small")
                        .replace("Whisper Tanglish LoRA", "Whisper-small Tanglish LoRA")
                        .replace("MAX_STEPS, BATCH, ACCUM, LR = 1500, 4, 4, 1e-4", "MAX_STEPS, BATCH, ACCUM, LR = 3000, 16, 1, 1e-4")
                        for line in cell["source"]]}
    for cell in NB3
]


def main() -> None:
    for name, cells, gpu in [
        ("01_build_tanglish_dataset.ipynb", NB1, False),
        ("02_finetune_indic_parler_style.ipynb", NB2, True),
        ("03_finetune_whisper_tanglish_lora.ipynb", NB3, True),
        ("04_finetune_piper_tanglish.ipynb", NB4, True),
        ("05_finetune_whisper_small.ipynb", NB5, True),
    ]:
        (HERE / name).write_text(json.dumps(notebook(cells, gpu), ensure_ascii=False, indent=1), encoding="utf-8")
        print("wrote", name)


if __name__ == "__main__":
    main()
