"""
app/voice/__init__.py

Voice pipeline interfaces.

STT (Speech-to-Text):
  SpeechToText ABC → GroqWhisperSTT | FasterWhisperSTT (fallback)

TTS (Text-to-Speech) — free, local, female by default:
  Tanglish: transliterate → PocketTTS (Geetha) → Edge Pallavi → Silent
  English:  Kokoro (af_heart) → Edge Neerja → Silent
  (Bodhan / Gemini / Indic-Parler remain opt-in via TTS_PROVIDER)

Audio cache keyed by sha256(tts_text + provider + voice + lang + style + speed).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class STTResult:
    transcript: str
    language: str = "ta"
    confidence: float = 1.0
    duration_s: float = 0.0


@dataclass
class WordTiming:
    word: str
    start_ms: int
    end_ms: int
    word_index: int = -1   # index into the DISPLAY token list (whitespace split)
    tts_word: str = ""     # what the engine actually spoke for this display word

    def to_dict(self, offset: int = 0) -> dict:
        d = {"word": self.word, "startMs": self.start_ms, "endMs": self.end_ms}
        if self.word_index >= 0:
            d["wordIndex"] = self.word_index + offset
        return d


@dataclass
class TTSResult:
    audio_bytes: bytes
    mime_type: str = "audio/mpeg"
    word_timings: list[WordTiming] = field(default_factory=list)
    duration_ms: int = 0
    provider: str = ""
    from_cache: bool = False
    lang: str = ""          # "tanglish" | "english"
    tts_text: str = ""      # exact text sent to the engine (Tamil script for Tanglish)
    voice: str = ""
    sample_rate: int = 0


class SpeechToText(ABC):
    name: str = "base"
    MAX_BYTES: int = 10 * 1024 * 1024   # 10 MB
    MAX_DURATION_S: float = 60.0

    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, mime: str, hint_lang: str = "ta") -> STTResult:
        """Transcribe audio. Raises ValueError if oversized."""


class TextToSpeech(ABC):
    name: str = "base"
    voices: tuple[str, ...] = ()
    default_voice: str = ""
    langs: tuple[str, ...] = ("tanglish", "english")

    @classmethod
    def is_installed(cls) -> bool:
        """True if the provider's optional dependencies are importable."""
        return True

    def pick_voice(self, voice: str = "") -> str:
        if voice and (not self.voices or voice in self.voices):
            return voice
        return self.default_voice or voice

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        voice: str = "default",
        speed: float = 1.0,
    ) -> TTSResult:
        """Synthesize speech. Returns TTSResult with audio bytes + word timings."""
