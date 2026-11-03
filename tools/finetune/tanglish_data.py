"""
tools/finetune/tanglish_data.py — pick native Tanglish speech out of AI4Bharat's Tamil corpora.

Used by the Kaggle notebooks (copied in with %%writefile) and tested locally by test_tanglish_data.py.
No cloning: every utterance keeps its own speaker; the model learns the *style* of many native
speakers mixing English into Tamil.

Text convention (matches Bookey's finding that Tamil-script input pronounces best): the
`normalized` transcript is Tamil script with English words already written phonetically in Tamil
script (ஸ்கூல் = school). We keep that as the TTS/STT text and drop every [tag] / <tag>.

Sources:
  ai4bharat/IndicVoices   tamil/*.parquet   (conversation + extempore, natural code-mixing)
  ai4bharat/indicvoices_r Tamil/*.parquet   (TTS-grade subset)
Both are CC-BY-4.0, gated (accept on huggingface.co first).
"""
from __future__ import annotations

import ast
import io
import json
import re
from dataclasses import dataclass, field
from typing import Iterable, Iterator

# [tags] in unsanitized transcripts that are sounds, not English words.
NOISE_TAGS = {
    "breathing", "uhh", "umm", "uh", "um", "hmm", "noise", "persistent-noise-start", "persistent-noise-end",
    "stammers", "stammer", "tsk", "horn", "laugh", "laughs", "laughter", "cough", "coughs", "music", "click",
    "silence", "inhale", "exhale", "babble", "overlap", "sneeze", "clears-throat", "lipsmack", "beep",
}
_TAG = re.compile(r"\[([^\]]+)\]")
_ANGLE = re.compile(r"<[^>]*>")
_SPACES = re.compile(r"\s+")
_LATIN_WORD = re.compile(r"^[A-Za-z][A-Za-z' -]*$")
_TAMIL = re.compile(r"[஀-௿]")


@dataclass
class FilterConfig:
    min_seconds: float = 2.0
    max_seconds: float = 15.0
    decisions: tuple[str, ...] = ("excellent", "good")
    min_english_words: int = 1          # code-mixed utterances
    keep_pure_tamil_every: int = 4      # also keep 1 in N clean pure-Tamil rows for balance (0 = never)
    scenarios: tuple[str, ...] = ()     # empty = any
    min_snr: float = 15.0               # IndicVoices-R ships SNR (dB); skip noisy clips
    # Tamil-script spellings of English words (ஸ்கூல், பேங்க்), learned from IndicVoices' [tags].
    # IndicVoices-R has no tags, so code-mixing there is detected with this set.
    english_ta: frozenset = frozenset()


@dataclass
class Stats:
    seen: int = 0
    kept: int = 0
    kept_seconds: float = 0.0
    kept_mixed: int = 0
    reasons: dict = field(default_factory=dict)

    def drop(self, why: str) -> None:
        self.reasons[why] = self.reasons.get(why, 0) + 1


def english_words(unsanitized: str | None) -> list[str]:
    """English words tagged in an unsanitized transcript: 'ஸ்கூல் [school]' → ['school']."""
    out = []
    for tag in _TAG.findall(unsanitized or ""):
        word = tag.strip().lower()
        if word in NOISE_TAGS or "noise" in word or not _LATIN_WORD.match(word):
            continue
        out.append(word)
    return out


def clean_text(text: str | None) -> str:
    """Tamil-script transcript without [tags], <tags> or stray Latin; '' if unusable."""
    t = _ANGLE.sub(" ", _TAG.sub(" ", text or ""))
    t = _SPACES.sub(" ", t).strip()
    return t if _TAMIL.search(t) else ""


def decision_of(report) -> str | None:
    """verification_report arrives as a dict, a JSON string, or a Python-dict string."""
    if isinstance(report, dict):
        return report.get("decision")
    if not isinstance(report, str) or not report.strip():
        return None
    for parse in (json.loads, ast.literal_eval):
        try:
            value = parse(report)
            if isinstance(value, dict):
                return value.get("decision")
        except Exception:
            continue
    return None


def describe(gender: str | None, seconds: float, text: str, scenario: str | None) -> str:
    """Parler-style voice description. Style words only — no speaker names, so nobody is cloned."""
    rate = len(text.replace(" ", "")) / max(seconds, 0.1)  # Tamil chars per second
    pace = "slightly fast" if rate > 15 else "moderate" if rate > 9 else "slow, clear"
    who = {"male": "A male speaker", "female": "A female speaker"}.get((gender or "").lower(), "A speaker")
    style = "conversational" if (scenario or "").lower().startswith("conv") else "natural, expressive"
    return (f"{who} talks in a {style} Tamil style, mixing English words naturally like a native "
            f"Tanglish speaker, at a {pace} pace. The recording is clear and close to the microphone.")


def select(row: dict, cfg: FilterConfig, stats: Stats, pure_counter: list[int]) -> dict | None:
    """Return a training example for *row*, or None (reason recorded in *stats*)."""
    stats.seen += 1
    seconds = float(row.get("duration") or 0)
    if not cfg.min_seconds <= seconds <= cfg.max_seconds:
        stats.drop("duration")
        return None
    decision = decision_of(row.get("verification_report"))
    if decision is not None and decision not in cfg.decisions:
        stats.drop("quality")
        return None
    snr = row.get("snr")
    if snr is not None and snr < cfg.min_snr:
        stats.drop("noisy")
        return None
    unsanitized = row.get("unsanitized_normalized") or row.get("unsanitized_verbatim") or ""
    if "<" in unsanitized or "<" in (row.get("normalized") or ""):
        stats.drop("unintelligible")
        return None
    if cfg.scenarios and (row.get("scenario") or "") not in cfg.scenarios:
        stats.drop("scenario")
        return None
    text = clean_text(row.get("normalized") or row.get("text"))
    if not text:
        stats.drop("no_text")
        return None
    if unsanitized:
        english = english_words(unsanitized)
    else:
        english = [w for w in (t.strip(".,!?") for t in text.split()) if w in cfg.english_ta]
    if len(english) < cfg.min_english_words:
        pure_counter[0] += 1
        if not cfg.keep_pure_tamil_every or pure_counter[0] % cfg.keep_pure_tamil_every:
            stats.drop("pure_tamil")
            return None
    audio = row.get("audio_filepath") or row.get("audio")
    if not isinstance(audio, dict) or not audio.get("bytes"):
        stats.drop("no_audio")
        return None
    stats.kept += 1
    stats.kept_seconds += seconds
    stats.kept_mixed += bool(english)
    return {
        "audio": {"bytes": audio["bytes"], "path": audio.get("path") or f"{row.get('speaker_id', 'spk')}.flac"},
        "text": text,
        "unsanitized": unsanitized,
        "english_words": english,
        "duration": seconds,
        "speaker_id": row.get("speaker_id") or "",
        "gender": (row.get("gender") or "").lower(),
        "scenario": row.get("scenario") or "",
        "description": describe(row.get("gender"), seconds, text, row.get("scenario")),
    }


READ_COLUMNS = [
    "audio_filepath", "audio", "text", "normalized", "duration", "speaker_id", "gender", "scenario",
    "verification_report", "unsanitized_normalized", "unsanitized_verbatim", "snr",
]


def iter_parquet(path: str, batch_rows: int = 256) -> Iterator[dict]:
    """Rows of a parquet shard in small batches (keeps RAM low on Kaggle)."""
    import pyarrow.parquet as pq
    f = pq.ParquetFile(path)
    cols = [c for c in READ_COLUMNS if c in f.schema_arrow.names]
    for batch in f.iter_batches(batch_size=batch_rows, columns=cols):
        yield from batch.to_pylist()


def filter_shard(path: str, cfg: FilterConfig | None = None, stats: Stats | None = None) -> Iterator[dict]:
    cfg = cfg or FilterConfig()
    stats = stats if stats is not None else Stats()
    pure = [0]
    for row in iter_parquet(path):
        ex = select(row, cfg, stats, pure)
        if ex is not None:
            yield ex


def english_lexicon(examples: Iterable[dict]) -> dict[str, dict[str, int]]:
    """English word → {Tamil-script spelling: count}, harvested from tagged transcripts.

    Feeds Bookey's transliteration lexicon with *native* spellings (school → ஸ்கூல்).
    Pairs come from 'தமிழ் [english]' adjacency in unsanitized transcripts.
    """
    pair = re.compile(r"(\S+)\s*\[([A-Za-z][A-Za-z' -]*)\]")
    out: dict[str, dict[str, int]] = {}
    for ex in examples:
        for ta, en in pair.findall(ex.get("unsanitized") or ""):
            en = en.lower().strip()
            if en in NOISE_TAGS or not _TAMIL.search(ta):
                continue
            ta = ta.strip(".,!?")
            out.setdefault(en, {}).setdefault(ta, 0)
            out[en][ta] += 1
    return out


def native_english_spellings(examples: Iterable[dict], min_count: int = 2,
                             min_tagged_ratio: float = 0.6) -> dict[str, str]:
    """English word → its most common native Tamil-script spelling, keeping only spellings that are
    tagged as English in most places they occur (so ordinary Tamil words that merely sit next to a
    tag, like இருக்குது, are not mistaken for English)."""
    rows = list(examples)
    lex = english_lexicon(rows)
    tagged: dict[str, int] = {}
    for spellings in lex.values():
        for ta, n in spellings.items():
            tagged[ta] = tagged.get(ta, 0) + n
    total: dict[str, int] = {}
    for ex in rows:
        for tok in _TAG.sub(" ", _ANGLE.sub(" ", ex.get("unsanitized") or "")).split():
            tok = tok.strip(".,!?")
            if tok in tagged:
                total[tok] = total.get(tok, 0) + 1
    out = {}
    for en, spellings in lex.items():
        ta, n = max(spellings.items(), key=lambda kv: kv[1])
        if sum(spellings.values()) >= min_count and tagged[ta] / max(total.get(ta, 1), 1) >= min_tagged_ratio:
            out[en] = ta
    return out


def flac_seconds(audio_bytes: bytes) -> float:
    """Duration check used by tests (soundfile reads FLAC from memory)."""
    import soundfile as sf
    info = sf.info(io.BytesIO(audio_bytes))
    return info.frames / info.samplerate
