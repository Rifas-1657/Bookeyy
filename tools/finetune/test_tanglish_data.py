"""
tools/finetune/test_tanglish_data.py — unit tests + a real-shard check for the Tanglish filter.

  tools/voice-eval/.venv-f5/Scripts/python -m pytest tools/finetune/test_tanglish_data.py -q
The real-shard test runs only if an IndicVoices Tamil shard is already in the HF cache.
"""
from __future__ import annotations

import glob
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import tanglish_data as td  # noqa: E402


def test_english_words_skip_noise_tags():
    s = "[uhh] செலான் [challan] ஃபில் [fill] பண்ணி [breathing] <unintelligible> [persistent-noise-start]"
    assert td.english_words(s) == ["challan", "fill"]


def test_clean_text_strips_tags_and_requires_tamil():
    assert td.clean_text("ஸ்கூல் [school] படிக்கும்போது <noise>") == "ஸ்கூல் படிக்கும்போது"
    assert td.clean_text("[uhh] okay") == ""


@pytest.mark.parametrize("report,expected", [
    ({"decision": "good"}, "good"),
    ('{"decision": "excellent"}', "excellent"),
    ("{'decision': 'bad', 'sst': False}", "bad"),
    ("", None),
])
def test_decision_of_handles_all_encodings(report, expected):
    assert td.decision_of(report) == expected


def _row(**kw):
    base = {
        "duration": 4.0, "verification_report": "{'decision': 'excellent'}",
        "unsanitized_normalized": "நான் ஸ்கூல் [school] போனேன்", "normalized": "நான் ஸ்கூல் போனேன்",
        "audio_filepath": {"bytes": b"x", "path": "a.flac"}, "speaker_id": "S1", "gender": "Male",
        "scenario": "Conversation",
    }
    base.update(kw)
    return base


def test_select_keeps_code_mixed_and_describes_style():
    stats = td.Stats()
    ex = td.select(_row(), td.FilterConfig(), stats, [0])
    assert ex and ex["text"] == "நான் ஸ்கூல் போனேன்" and ex["english_words"] == ["school"]
    assert "male speaker" in ex["description"].lower() and "Tanglish" in ex["description"]
    assert "S1" not in ex["description"]  # style only, never the speaker's identity


@pytest.mark.parametrize("kw,reason", [
    ({"duration": 1.0}, "duration"),
    ({"verification_report": "{'decision': 'bad'}"}, "quality"),
    ({"unsanitized_normalized": "நான் <unintelligible> [school]"}, "unintelligible"),
    ({"audio_filepath": None}, "no_audio"),
])
def test_select_drops_with_reason(kw, reason):
    stats = td.Stats()
    assert td.select(_row(**kw), td.FilterConfig(), stats, [0]) is None
    assert stats.reasons == {reason: 1}


def test_pure_tamil_is_sampled_one_in_n():
    cfg, stats, counter = td.FilterConfig(keep_pure_tamil_every=3), td.Stats(), [0]
    pure = _row(unsanitized_normalized="நான் போனேன்", normalized="நான் போனேன்")
    kept = [td.select(dict(pure), cfg, stats, counter) is not None for _ in range(6)]
    assert kept.count(True) == 2


def test_indicvoices_r_rows_use_lexicon_and_snr():
    """IndicVoices-R: no [tags] or quality report — English found via learned spellings, noise via SNR."""
    cfg = td.FilterConfig(english_ta=frozenset({"ஸ்கூல்"}), keep_pure_tamil_every=0)
    r = {"duration": 5.0, "normalized": "நான் ஸ்கூல் போனேன்", "audio": {"bytes": b"x", "path": "r.flac"},
         "snr": 30.0, "gender": "Female", "scenario": "Extempore", "speaker_id": "S9"}
    stats = td.Stats()
    ex = td.select(dict(r), cfg, stats, [0])
    assert ex and ex["english_words"] == ["ஸ்கூல்"]
    assert td.select(dict(r, snr=8.0), cfg, stats, [0]) is None and stats.reasons == {"noisy": 1}
    assert td.select(dict(r, normalized="நான் போனேன்"), cfg, stats, [0]) is None


def test_lexicon_collects_native_spellings():
    lex = td.english_lexicon([{"unsanitized": "ஸ்கூல் [school] பேங்க் [bank] ஸ்கூல் [school]"}])
    assert lex["school"] == {"ஸ்கூல்": 2} and lex["bank"] == {"பேங்க்": 1}


def test_native_spellings_drop_plain_tamil_words_next_to_tags():
    rows = [{"unsanitized": "ஸ்கூல் [school] இருக்குது [it]"},
            {"unsanitized": "ஸ்கூல் [school] போனேன் இருக்குது"},
            {"unsanitized": "இருக்குது [it] நல்லா இருக்குது இருக்குது"}]
    assert td.native_english_spellings(rows) == {"school": "ஸ்கூல்"}


SHARDS = glob.glob(os.path.expanduser(
    "~/.cache/huggingface/hub/datasets--ai4bharat--IndicVoices/snapshots/*/tamil/*.parquet"))


@pytest.mark.skipif(not SHARDS, reason="no IndicVoices Tamil shard cached")
def test_real_shard_yields_playable_code_mixed_audio():
    stats = td.Stats()
    examples = list(td.filter_shard(SHARDS[0], stats=stats))
    assert stats.kept == len(examples) > 100
    assert stats.kept_mixed / stats.kept > 0.7
    secs = td.flac_seconds(examples[0]["audio"]["bytes"])
    assert abs(secs - examples[0]["duration"]) < 0.5
    print(f"\nkept {stats.kept}/{stats.seen} = {stats.kept_seconds / 3600:.2f} h, "
          f"code-mixed {stats.kept_mixed}, dropped {stats.reasons}")
