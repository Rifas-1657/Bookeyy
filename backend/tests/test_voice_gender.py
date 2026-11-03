"""
tests/test_voice_gender.py — the learner's male/female tutor choice picks the TTS chain and voice.
"""
from __future__ import annotations

from app.voice.pipeline import get_tts_chain, make_provider


def _names(chain):
    return [p.name for p in chain]


def test_female_chain_prefers_local_engines():
    assert _names(get_tts_chain("tanglish", "female"))[0] == "pocket_tts"
    english = get_tts_chain("english", "female")
    assert english[0].name == "kokoro"
    assert english[0].default_voice == "af_heart"


def test_male_chain_uses_male_voices():
    english = get_tts_chain("english", "male")
    assert english[0].name == "kokoro"
    assert english[0].default_voice == "am_michael"
    tanglish = get_tts_chain("tanglish", "male")
    assert tanglish[0].name == "edge_tts"
    assert tanglish[0].default_voice == "ta-IN-ValluvarNeural"


def test_edge_voice_follows_gender_and_language():
    assert make_provider("edge", "english", "female").default_voice == "en-IN-NeerjaNeural"
    assert make_provider("edge", "english", "male").default_voice == "en-IN-PrabhatNeural"
    assert make_provider("edge", "tanglish", "female").default_voice == "ta-IN-PallaviNeural"


def test_unknown_gender_keeps_default_chain():
    assert _names(get_tts_chain("tanglish", "robot")) == _names(get_tts_chain("tanglish"))
    assert get_tts_chain("tanglish")[-1].name == "silent"
