"""
app/voice/transliterate.py — Word-aligned Tanglish → Tamil-script transliteration.

Why: the local Tamil TTS voices only pronounce correctly when the input is in
Tamil script. Romanized Tanglish and raw English words come out wrong. The
learner, however, always sees the original Latin text. So we transliterate
*per display word* and keep the index mapping, which lets TTS word timings map
1:1 back to the Latin words on screen.

    align_tanglish("Vanakkam! Innaiku namma Python variables pathi kathukkaporom.")
    → [AlignedWord("Vanakkam!", "வணக்கம்!"), AlignedWord("Innaiku", "இன்னைக்கு"), ...,
       AlignedWord("Python", "பைதான்"), AlignedWord("variables", "வேரியபிள்ஸ்"), ...]

Layers (first hit wins, per word piece):
  (a) curated lexicon (Tanglish words + ~300 tech terms + numbers + symbols)
  (b) LLM transliteration (app.llm.router) for unknown words — strict per-word
      JSON, cached to disk by sha256 so each sentence is transliterated once
  (c) deterministic rule-based Latin → Tamil phonetic fallback (fully offline)

Display tokenization == whitespace split (identical to the frontend's
tokenizeNarration), so `wordIndex` values line up with what the reader sees.

Also provides:
  detect_language(text)      → "english" | "tanglish"
  align_english(text)        → per-word normalized English (numbers, symbols…)
  romanize_tamil(text)       → Tamil script → Latin Tanglish (for STT output)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from app.voice.lexicon import (
    ENGLISH_STOPWORDS,
    KNOWN_ACRONYMS_EN,
    LETTERS,
    NUMBER_WORDS_TA,
    SUFFIXES,
    SYMBOLS_EN,
    SYMBOLS_TA,
    TANGLISH,
    TANGLISH_MARKERS,
    TECH,
)

log = logging.getLogger(__name__)

TRANSLIT_VERSION = "v1"

_TAMIL_RE = re.compile(r"[஀-௿]")
_SENT_PUNCT = ".,!?;:"
_LEAD_STRIP = "\"'“”‘’([{<`*_#>"
_TRAIL_STRIP = "\"'“”‘’)]}>`*_"
_NUM_RE = re.compile(r"^[-+]?\d+(?:[.,]\d+)*$")
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
_URL_RE = re.compile(r"^(https?://|www\.)", re.I)

PULLI = "்"
VOWEL_SIGNS = {
    "அ": "", "ஆ": "ா", "இ": "ி", "ஈ": "ீ", "உ": "ு", "ஊ": "ூ",
    "எ": "ெ", "ஏ": "ே", "ஐ": "ை", "ஒ": "ொ", "ஓ": "ோ", "ஔ": "ௌ",
}


@dataclass
class AlignedWord:
    display: str          # exactly as shown to the user (whitespace token)
    tts: str              # what the TTS engine reads ("" = silent token)
    source: str = "lexicon"  # lexicon | llm | rule | number | symbol | english | tamil | empty


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

def detect_language(text: str) -> str:
    """Decide whether a segment is plain English or Tanglish (Tamil-English mix)."""
    if _TAMIL_RE.search(text or ""):
        return "tanglish"
    words = [w.strip(_SENT_PUNCT + _LEAD_STRIP + _TRAIL_STRIP).lower() for w in (text or "").split()]
    words = [w for w in words if w]
    if not words:
        return "english"
    tanglish = 0
    english = 0
    for w in words:
        parts = w.split("-")
        if len(parts) > 1 and parts[-1] in SUFFIXES:
            tanglish += 1
            continue
        if w in TANGLISH or w in TANGLISH_MARKERS:
            tanglish += 1
        elif w in ENGLISH_STOPWORDS:
            english += 1
    if tanglish >= 2 or (tanglish and tanglish / len(words) >= 0.15):
        return "tanglish"
    if english == 0 and tanglish == 0:
        # No evidence either way: words that the rule-based Tanglish detector would
        # recognise (doubled consonants / zh / dh endings) lean Tanglish.
        if sum(1 for w in words if re.search(r"(zh|dh|kk|pp|tt|aa|oo)\w*$", w)) >= max(2, len(words) // 3):
            return "tanglish"
    return "english"


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------
_SMALL = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
          "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
          "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def number_to_english_words(n_str: str) -> str:
    """'42' → 'forty two'; '3.14' → 'three point one four'; up to billions."""
    s = n_str.replace(",", "")
    try:
        if s.startswith(("-", "+")):
            sign = "negative " if s[0] == "-" else ""
            return sign + number_to_english_words(s[1:])
        if "." in s:
            ip, dp = s.split(".", 1)
            return f"{number_to_english_words(ip or '0')} point " + " ".join(_SMALL[int(d)] for d in dp if d.isdigit())
        n = int(s)
    except (ValueError, IndexError):
        return n_str

    def _below_1000(x: int) -> str:
        parts = []
        if x >= 100:
            parts.append(f"{_SMALL[x // 100]} hundred")
            x %= 100
        if x >= 20:
            parts.append(_TENS[x // 10] + (f" {_SMALL[x % 10]}" if x % 10 else ""))
        elif x or not parts:
            parts.append(_SMALL[x])
        return " ".join(parts)

    if n < 1000:
        return _below_1000(n)
    out = []
    for value, name in ((10**9, "billion"), (10**6, "million"), (1000, "thousand")):
        if n >= value:
            out.append(f"{_below_1000(n // value)} {name}")
            n %= value
    if n:
        out.append(_below_1000(n))
    return " ".join(out)


def number_to_tamil_script(n_str: str) -> str:
    """English number words in Tamil script: '42' → 'ஃபோர்ட்டி டூ'."""
    words = number_to_english_words(n_str).split()
    return " ".join(NUMBER_WORDS_TA.get(w, rule_based_tamil(w)) for w in words)


# ---------------------------------------------------------------------------
# Rule-based Latin → Tamil phonetic fallback (deterministic, offline)
# ---------------------------------------------------------------------------
_VOWELS = [  # (latin, independent, sign) longest first
    ("aa", "ஆ", "ா"), ("ae", "ஏ", "ே"), ("ai", "ஐ", "ை"), ("au", "ஔ", "ௌ"), ("ee", "ஈ", "ீ"),
    ("ii", "ஈ", "ீ"), ("oo", "ஊ", "ூ"), ("uu", "ஊ", "ூ"), ("oa", "ஓ", "ோ"), ("ou", "ஔ", "ௌ"),
    ("ei", "ஏ", "ே"), ("a", "அ", ""), ("e", "எ", "ெ"), ("i", "இ", "ி"), ("o", "ஒ", "ொ"),
    ("u", "உ", "ு"),
]
_CONS = [  # longest first
    ("ksh", "க்ஷ"), ("sh", "ஷ"), ("ch", "ச"), ("th", "த"), ("dh", "த"), ("zh", "ழ"), ("ng", "ங"),
    ("nj", "ஞ"), ("kh", "க"), ("gh", "க"), ("ph", "ஃப"), ("bh", "ப"), ("rr", "ற"),
    ("k", "க"), ("g", "க"), ("c", "க"), ("j", "ஜ"), ("t", "ட"), ("d", "ட"), ("n", "ன"),
    ("p", "ப"), ("b", "ப"), ("m", "ம"), ("y", "ய"), ("r", "ர"), ("l", "ல"), ("v", "வ"),
    ("w", "வ"), ("s", "ஸ"), ("h", "ஹ"), ("f", "ஃப"), ("z", "ஸ"), ("q", "க"), ("x", "க்ஸ"),
]


def _match(word: str, i: int, table):
    for item in table:
        if word.startswith(item[0], i):
            return item
    return None


def rule_based_tamil(word: str) -> str:
    """Greedy phonetic Latin → Tamil script, tuned for Tanglish romanization."""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return ""
    out: list[str] = []
    i = 0
    n = len(w)
    while i < n:
        v = _match(w, i, _VOWELS)
        if v:
            latin, indep, _sign = v
            # word-final short e/o are long in Tanglish ("adhe", "ippo")
            if i + len(latin) == n and latin == "e" and i > 0:
                indep = "ஏ"
            out.append(indep)
            i += len(latin)
            continue
        c = _match(w, i, _CONS)
        if not c:
            i += 1
            continue
        latin, tam = c
        j = i + len(latin)
        prev = w[i - 1] if i > 0 else ""
        # contextual consonant choices
        if latin == "n":
            if i == 0 or w.startswith(("th", "dh"), j):
                tam = "ந"
            elif w.startswith(("t", "d"), j) and not w.startswith(("th", "dh"), j):
                tam = "ண"
        elif latin == "s" and (i == 0 or (prev in "aeiou" and j < n and w[j] in "aeiou")):
            tam = "ச"
        elif latin == "c" and j < n and w[j] in "ei":
            tam = "ஸ"
        elif latin == "t" and i == 0:
            tam = "ட"
        elif latin == "y" and (j >= n or w[j] not in "aeiou"):
            # trailing / pre-consonant y behaves as a vowel ("happy", "my")
            if out and out[-1].endswith(PULLI):
                out[-1] = out[-1][:-1] + ("ி" if j >= n else "ை")
            else:
                out.append("இ" if j >= n else "ஐ")
            i = j
            continue
        # word-final doubled consonant collapses ("will" → வில்)
        if j < n and w[j] == latin and j + 1 == n:
            j += 1
        # Tanglish writes intervocalic "kk" as single k before final u ("innaiku")
        if latin == "k" and prev in "aeiou" and w[j:] == "u":
            tam = "க்க"
        # ங is almost always followed by க ("sollunga" → சொல்லுங்க)
        if latin == "ng" and j < n and w[j] in "aeiou":
            tam = "ங்க"
        v2 = _match(w, j, _VOWELS)
        if v2:
            vlatin, _indep, sign = v2
            end = j + len(vlatin) == n
            if end and vlatin == "e":
                sign = "ே"
            elif vlatin == "o" and (end or w[j + 1:] in ("m", "n")):
                sign = "ோ"  # Tanglish "-om"/"-on" verb endings are long
            out.append(tam + sign)
            i = j + len(vlatin)
        else:
            out.append(tam + PULLI)
            i = j
    return "".join(out)


# ---------------------------------------------------------------------------
# Tamil-script helpers
# ---------------------------------------------------------------------------

def merge_suffix(base: str, suffix: str) -> str:
    """Glue a Tamil suffix onto a base word with natural sandhi.

    ரியாக்ட் + ல → ரியாக்ட்ல ; பைதான் + உம் → பைதானும் ; டேட்டா + அ → டேட்டாவ
    """
    if not base:
        return suffix
    if not suffix:
        return base
    if suffix.startswith(" "):
        return base + suffix
    first = suffix[0]
    if first in VOWEL_SIGNS:
        if base.endswith(PULLI):
            return base[:-1] + VOWEL_SIGNS[first] + suffix[1:]
        last = base[-1]
        glide = "ய" if last in "ிீெேைஇஈஎஏஐ" else "வ"
        return base + glide + VOWEL_SIGNS[first] + suffix[1:]
    if suffix == "க்கு" and base.endswith(PULLI):
        return base[:-1] + "ுக்கு"
    return base + suffix


def _split_affixes(token: str) -> tuple[str, str, str]:
    """Split a whitespace token into (leading, core, trailing punctuation)."""
    lead = ""
    trail = ""
    core = token
    while core and core[0] in _LEAD_STRIP:
        lead += core[0]
        core = core[1:]
    while core and (core[-1] in _SENT_PUNCT or core[-1] in _TRAIL_STRIP):
        trail = core[-1] + trail
        core = core[:-1]
    return lead, core, trail


def _speech_punct(trail: str) -> str:
    """Keep only the punctuation that affects prosody (last . , ! ? ; :)."""
    for ch in reversed(trail):
        if ch in _SENT_PUNCT:
            return ch
    return ""


def _split_camel(piece: str) -> list[str]:
    parts = _CAMEL_RE.findall(piece)
    return parts if parts else [piece]


# ---------------------------------------------------------------------------
# Tanglish word lookup (layer a + c)
# ---------------------------------------------------------------------------
# Process-wide learned lexicon. Intentionally NOT fed from per-request LLM output
# (see align_tanglish); kept for curated additions only and bounded by _LEARNED_MAX.
_LEARNED: dict[str, str] = {}
_LEARNED_MAX = 5000


def _lex(word: str) -> str | None:
    lw = word.lower()
    for table in (_LEARNED, TANGLISH, TECH):
        if lw in table:
            return table[lw]
    return None


def _inflected(word: str) -> str | None:
    """Plural / -ing / -ed forms of lexicon words: variables → வேரியபிள்ஸ்."""
    lw = word.lower()
    if len(lw) > 3 and lw.endswith("ies") and _lex(lw[:-3] + "y"):
        return _lex(lw[:-3] + "y")[:-1] + "ீஸ்" if _lex(lw[:-3] + "y").endswith("ி") else _lex(lw[:-3] + "y") + "ஸ்"
    if len(lw) > 3 and lw.endswith("es") and _lex(lw[:-2]):
        base = _lex(lw[:-2])
        return base + ("ஸ்" if not base.endswith(("ஸ்", "ஷ்", "ச்")) else "ஸ்")
    if len(lw) > 2 and lw.endswith("s") and _lex(lw[:-1]):
        return _lex(lw[:-1]) + "ஸ்"
    if len(lw) > 4 and lw.endswith("ing"):
        stem = _lex(lw[:-3]) or _lex(lw[:-3] + "e")
        if stem:
            return merge_suffix(stem, "இங்")
    if len(lw) > 3 and lw.endswith("ed"):
        stem = _lex(lw[:-2]) or _lex(lw[:-1])
        if stem:
            return stem + ("டு" if stem.endswith("ட்") else "ட்")
    return None


def _known_piece(piece: str) -> str | None:
    """Return Tamil for a piece if any deterministic layer knows it confidently."""
    if not piece:
        return ""
    if _NUM_RE.match(piece):
        return number_to_tamil_script(piece)
    hit = _lex(piece)
    if hit is not None:
        return hit
    hit = _inflected(piece)
    if hit is not None:
        return hit
    if len(piece) == 1 and piece.isalpha():
        return LETTERS.get(piece.lower(), piece)
    if piece.isupper() and 2 <= len(piece) <= 5 and piece.isalpha():
        return " ".join(LETTERS[c.lower()] for c in piece)
    # camelCase / PascalCase / letters+digits: useState → யூஸ் ஸ்டேட்
    parts = _split_camel(piece)
    if len(parts) > 1:
        sub = [_known_piece(p) for p in parts]
        if all(s is not None for s in sub):
            return " ".join(s for s in sub if s)
    return None


def _piece_tamil(piece: str, overrides: dict[str, str]) -> tuple[str, str]:
    """Tamil for one alphanumeric piece → (tamil, source)."""
    lw = piece.lower()
    if lw in overrides:
        return overrides[lw], "llm"
    if _NUM_RE.match(piece):
        return number_to_tamil_script(piece), "number"
    known = _known_piece(piece)
    if known is not None:
        return known, "lexicon"
    parts = _split_camel(piece)
    if len(parts) > 1:
        out = []
        for p in parts:
            t, _ = _piece_tamil(p, overrides)
            if t:
                out.append(t)
        return " ".join(out), "rule"
    return rule_based_tamil(piece), "rule"


def _core_pieces(core: str) -> list[tuple[str, str]]:
    """Split a token core into (kind, text) pieces: word | num | sym | sep."""
    pieces: list[tuple[str, str]] = []
    for m in re.finditer(r"[A-Za-z]+(?:'[a-z]+)?|\d+(?:[.,]\d+)*|[=!<>+\-*/%&|^#@]+|[^\sA-Za-z\d]", core):
        txt = m.group(0)
        if re.match(r"[A-Za-z]", txt):
            pieces.append(("word", txt.replace("'", "")))
        elif txt[0].isdigit():
            pieces.append(("num", txt))
        elif txt in SYMBOLS_TA or txt in SYMBOLS_EN:
            pieces.append(("sym", txt))
        else:
            pieces.append(("sep", txt))
    return pieces


def _unknown_cores(token: str) -> list[str]:
    """Words in a token that no deterministic layer knows (LLM candidates)."""
    _lead, core, _trail = _split_affixes(token)
    if not core or _TAMIL_RE.search(core) or _URL_RE.match(core):
        return []
    if _lex(core) is not None:
        return []
    unknown = []
    for kind, txt in _core_pieces(core):
        if kind == "word" and _known_piece(txt) is None and txt.lower() not in SUFFIXES:
            unknown.append(txt)
    return unknown


def transliterate_token(token: str, overrides: dict[str, str] | None = None) -> AlignedWord:
    """Transliterate a single display token to Tamil script (keeps prosody punctuation)."""
    overrides = overrides or {}
    lead, core, trail = _split_affixes(token)
    punct = _speech_punct(trail)
    if not core:
        return AlignedWord(token, punct if punct and punct != "." else "", "empty")
    if _TAMIL_RE.search(core) and not re.search(r"[A-Za-z]", core):
        return AlignedWord(token, core + punct, "tamil")
    if _URL_RE.match(core):
        return AlignedWord(token, "லிங்க்" + punct, "symbol")
    whole = overrides.get(core.lower()) or _lex(core)
    if whole is not None:
        return AlignedWord(token, whole + punct, "llm" if core.lower() in overrides else "lexicon")

    pieces = _core_pieces(core)
    if pieces and all(k == "sym" for k, _ in pieces):
        return AlignedWord(token, " ".join(SYMBOLS_TA.get(t, "") for _, t in pieces).strip() + punct, "symbol")

    out = ""
    source = "lexicon"
    prev_kind = ""
    for idx, (kind, txt) in enumerate(pieces):
        if kind == "sep":
            prev_kind = "sep_" + txt
            continue
        if kind == "sym":
            # a "-" between two word pieces is a hyphen, not "minus"
            if txt == "-" and 0 < idx < len(pieces) - 1:
                prev_kind = "sep_-"
                continue
            spoken = SYMBOLS_TA.get(txt, "")
            out = (out + " " + spoken).strip() if spoken else out
            prev_kind = "sym"
            continue
        # word / number piece
        is_suffix = (
            kind == "word" and out and prev_kind == "sep_-" and txt.lower() in SUFFIXES
        )
        if is_suffix:
            out = merge_suffix(out, SUFFIXES[txt.lower()])
            prev_kind = "suffix"
            continue
        tam, src = _piece_tamil(txt, overrides)
        if src in ("rule", "llm"):
            source = src
        elif src == "number" and source == "lexicon":
            source = "number"
        out = (out + " " + tam).strip() if out else tam
        prev_kind = kind
    return AlignedWord(token, (out + punct) if out else (punct if punct != "." else ""), source)


def align_tanglish_sync(text: str, overrides: dict[str, str] | None = None) -> list[AlignedWord]:
    """Offline transliteration (lexicon + rules), one AlignedWord per display token."""
    return [transliterate_token(tok, overrides) for tok in (text or "").split()]


# ---------------------------------------------------------------------------
# Layer (b): LLM transliteration with disk cache
# ---------------------------------------------------------------------------

def _translit_cache_dir() -> Path:
    from app.voice.cache import _cache_dir
    d = _cache_dir() / "translit"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sentence_hash(text: str, words: list[str]) -> str:
    raw = f"{TRANSLIT_VERSION}\0{' '.join(text.split())}\0{'|'.join(words)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


_LLM_SYSTEM = (
    "You convert Tanglish (Tamil written in English letters, mixed with English words) into "
    "TAMIL SCRIPT for a Tamil text-to-speech engine. Output how a friendly Chennai teacher "
    "would PRONOUNCE each word, written in Tamil letters. English/technical words must be "
    "transliterated phonetically, never translated (React→ரியாக்ட், useState→யூஸ் ஸ்டேட், "
    "component→காம்போனன்ட், variables→வேரியபிள்ஸ்). Romanized Tamil words use colloquial "
    "spelling (pannumbodhu→பண்ணும்போது, innaiku→இன்னைக்கு). Never add or drop words."
)

_LLM_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"w": {"type": "string"}, "ta": {"type": "string"}},
                "required": ["w", "ta"],
            },
        }
    },
    "required": ["items"],
}

_VALID_TA = re.compile(r"^[஀-௿\s\-]+$")


_LLM_DISABLED_UNTIL = 0.0
_LLM_BACKOFF_S = 600.0  # after an LLM failure, use offline rules for 10 minutes


def _llm_enabled() -> bool:
    import time as _t
    if _t.monotonic() < _LLM_DISABLED_UNTIL:
        return False
    try:
        from app.config import get_settings
        s = get_settings()
        if not getattr(s, "tts_translit_llm", True):
            return False
        if s.app_env == "testing":
            return False
        return bool(s.gemini_api_key or s.groq_api_key or s.openrouter_api_key)
    except Exception:
        return False


async def _llm_transliterate(sentence: str, words: list[str]) -> dict[str, str]:
    """Ask the LLM router for Tamil-script spellings of *words*; strict validation."""
    from app.config import get_settings
    from app.llm.router import get_llm_router

    prompt = (
        f"Sentence (context only): {sentence}\n\n"
        f"Words to convert, in order: {json.dumps(words, ensure_ascii=False)}\n\n"
        'Return JSON exactly like {"items":[{"w":"<word as given>","ta":"<Tamil script>"}]} '
        "with one item per word, same order, same spelling in \"w\". \"ta\" must contain only "
        "Tamil script characters (no Latin letters, no explanations)."
    )
    timeout = float(getattr(get_settings(), "tts_translit_llm_timeout_s", 12.0))
    resp = await asyncio.wait_for(
        get_llm_router().generate_json(prompt=prompt, schema=_LLM_SCHEMA, system=_LLM_SYSTEM,
                                       temperature=0.0, max_tokens=1024, timeout=timeout),
        timeout=timeout + 3,
    )
    text = (resp.text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text).strip()
    data = json.loads(text)
    items = data.get("items", []) if isinstance(data, dict) else data
    out: dict[str, str] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        w = str(item.get("w", "")).strip()
        ta = str(item.get("ta", "")).strip()
        if w and ta and w in words and _VALID_TA.match(ta):
            out[w.lower()] = ta.replace("-", "")
    return out


async def align_tanglish(text: str, use_llm: bool | None = None) -> list[AlignedWord]:
    """Full transliteration: lexicon → (cached) LLM for unknown words → rules."""
    tokens = (text or "").split()
    unknown: list[str] = []
    for tok in tokens:
        for u in _unknown_cores(tok):
            if u not in unknown:
                unknown.append(u)

    overrides: dict[str, str] = {}
    if unknown:
        key = _sentence_hash(text, unknown)
        path = None
        try:
            path = _translit_cache_dir() / f"{key}.json"
            if path.exists():
                overrides = json.loads(path.read_text(encoding="utf-8")).get("map", {})
        except Exception:
            log.debug("translit cache read failed", exc_info=True)
        if not overrides and (use_llm if use_llm is not None else _llm_enabled()):
            try:
                overrides = await _llm_transliterate(" ".join(tokens), unknown)
                if overrides and path is not None:
                    from app.voice.cache import _atomic_write_text
                    _atomic_write_text(path, json.dumps(
                        {"text": text, "words": unknown, "map": overrides, "v": TRANSLIT_VERSION},
                        ensure_ascii=False, indent=1))
            except Exception as exc:
                import time as _t
                global _LLM_DISABLED_UNTIL
                _LLM_DISABLED_UNTIL = _t.monotonic() + _LLM_BACKOFF_S
                log.info("LLM transliteration unavailable (%s); offline rules for %ds",
                         type(exc).__name__, int(_LLM_BACKOFF_S))
                overrides = {}
        # Per-call only: LLM output (steerable by page/user text) must never leak into the
        # process-wide lexicon used for every other learner. The disk cache is per sentence.
    return align_tanglish_sync(text, overrides)


# ---------------------------------------------------------------------------
# English alignment (Kokoro / English voices)
# ---------------------------------------------------------------------------

def _english_piece(piece: str) -> str:
    if _NUM_RE.match(piece):
        return number_to_english_words(piece)
    if piece.isupper() and len(piece) >= 2 and piece.isalpha():
        return piece if piece in KNOWN_ACRONYMS_EN else " ".join(piece)
    parts = _split_camel(piece)
    if len(parts) > 1 and not piece.istitle():
        return " ".join(_english_piece(p) for p in parts)
    return piece


def english_token(token: str) -> AlignedWord:
    lead, core, trail = _split_affixes(token)
    punct = _speech_punct(trail)
    if not core:
        return AlignedWord(token, punct if punct and punct != "." else "", "empty")
    if _URL_RE.match(core):
        return AlignedWord(token, "link" + punct, "symbol")
    pieces = _core_pieces(core)
    out: list[str] = []
    for idx, (kind, txt) in enumerate(pieces):
        if kind == "sep":
            if txt in ("_",):
                continue
            continue
        if kind == "sym":
            if txt == "-" and 0 < idx < len(pieces) - 1:
                continue
            spoken = SYMBOLS_EN.get(txt, "")
            if spoken:
                out.append(spoken)
            continue
        out.append(_english_piece(txt))
    spoken = " ".join(o for o in out if o)
    # Keep natural contractions / hyphenated words intact for the English G2P
    if re.fullmatch(r"[A-Za-z]+(?:[-'][A-Za-z]+)*", core) and not any(
        k == "word" and _split_camel(t) != [t] for k, t in pieces
    ):
        spoken = core
    return AlignedWord(token, (spoken + punct) if spoken else "", "english")


def align_english(text: str) -> list[AlignedWord]:
    return [english_token(tok) for tok in (text or "").split()]


async def align_text(text: str, lang: str = "auto") -> tuple[str, list[AlignedWord]]:
    """Resolve language and return (lang, aligned words)."""
    lang = (lang or "auto").lower()
    if lang in ("auto", ""):
        lang = detect_language(text)
    if lang in ("english", "en"):
        return "english", align_english(text)
    return "tanglish", await align_tanglish(text)


def tts_text_of(words: list[AlignedWord]) -> str:
    return " ".join(w.tts for w in words if w.tts).strip()


# ---------------------------------------------------------------------------
# Tamil script → Latin Tanglish (STT output shown to the user)
# ---------------------------------------------------------------------------
_TA_CONS = {
    "க": "k", "ங": "ng", "ச": "s", "ஞ": "nj", "ட": "d", "ண": "n", "த": "th", "ந": "n",
    "ப": "p", "ம": "m", "ய": "y", "ர": "r", "ல": "l", "வ": "v", "ழ": "zh", "ள": "l",
    "ற": "r", "ன": "n", "ஜ": "j", "ஷ": "sh", "ஸ": "s", "ஹ": "h", "ஶ": "sh",
}
_TA_VOWEL = {"அ": "a", "ஆ": "aa", "இ": "i", "ஈ": "ee", "உ": "u", "ஊ": "oo", "எ": "e",
             "ஏ": "e", "ஐ": "ai", "ஒ": "o", "ஓ": "o", "ஔ": "au"}
_TA_SIGN = {"ா": "aa", "ி": "i", "ீ": "ee", "ு": "u", "ூ": "oo", "ெ": "e", "ே": "e",
            "ை": "ai", "ொ": "o", "ோ": "o", "ௌ": "au"}

_REVERSE: dict[str, str] | None = None


def _reverse_lexicon() -> dict[str, str]:
    global _REVERSE
    if _REVERSE is None:
        rev: dict[str, str] = {}
        for table, cap in ((TECH, False), (TANGLISH, False)):
            for k, v in table.items():
                if " " in v or not re.fullmatch(r"[a-z+]+", k):
                    continue
                rev.setdefault(v, k)
        rev["பைதான்"] = "Python"
        rev["ரியாக்ட்"] = "React"
        _REVERSE = rev
    return _REVERSE


def _romanize_word(word: str) -> str:
    rev = _reverse_lexicon()
    if word in rev:
        return rev[word]
    if word.endswith("ஸ்") and word[:-2] in rev:
        return rev[word[:-2]] + "s"
    out: list[str] = []
    chars = list(word)
    n = len(chars)
    i = 0
    while i < n:
        ch = chars[i]
        if ch in _TA_VOWEL:
            out.append(_TA_VOWEL[ch])
            i += 1
            continue
        if ch == "ஃ":
            out.append("f" if i + 1 < n and chars[i + 1] == "ப" else "h")
            if i + 1 < n and chars[i + 1] == "ப":
                i += 1  # consume ப (and its pulli if present)
                if i + 1 < n and chars[i + 1] == PULLI:
                    i += 1
            i += 1
            continue
        if ch in _TA_CONS:
            nxt = chars[i + 1] if i + 1 < n else ""
            prev_pulli = i > 0 and chars[i - 1] == PULLI
            word_start = i == 0
            lat = _TA_CONS[ch]
            # intervocalic softening / doubled consonants (colloquial spelling)
            if ch == "த" and not word_start and not prev_pulli:
                lat = "dh"
            elif ch == "ச" and prev_pulli:
                lat = "ch"
            elif ch == "ட" and (word_start or prev_pulli):
                lat = "t"
            elif ch == "க" and not word_start and not prev_pulli and i > 0 and chars[i - 1] in "ஙனணமந":
                lat = "g"
            elif ch == "ற" and prev_pulli and i > 1 and chars[i - 2] == "ற":
                lat = "tr"
            if nxt == PULLI:
                out.append(lat)
                i += 2
                continue
            if nxt in _TA_SIGN:
                out.append(lat + _TA_SIGN[nxt])
                i += 2
                continue
            out.append(lat + "a")
            i += 1
            continue
        if ch == PULLI or ch in _TA_SIGN:
            i += 1
            continue
        out.append(ch)
        i += 1
    s = "".join(out)
    s = s.replace("rr", "tr") if "ற்ற" in word else s
    return s


def romanize_tamil(text: str) -> str:
    """Convert any Tamil-script runs in *text* to Latin Tanglish; Latin text is untouched."""
    if not text or not _TAMIL_RE.search(text):
        return text

    def _sub(m: re.Match) -> str:
        return _romanize_word(m.group(0))

    out = re.sub(r"[஀-௿]+", _sub, text)
    # Capitalise sentence starts that were Tamil
    out = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), out)
    return out
