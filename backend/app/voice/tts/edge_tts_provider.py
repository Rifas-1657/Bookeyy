"""
app/voice/tts/edge_tts_provider.py  — Microsoft Edge TTS (edge-tts) fallback.

Free (no key) but online. FEMALE voices by default:
  Tamil / Tanglish (Tamil-script input): ta-IN-PallaviNeural
  English:                               en-IN-NeerjaNeural
Env: EDGE_TTS_VOICE (Tamil), EDGE_TTS_VOICE_EN (English)

Word boundaries are requested explicitly (edge-tts ≥ 7 defaults to sentence
boundaries). Durations come from the audio itself, never from wall-clock time.
"""
from __future__ import annotations

import asyncio

import importlib.util
import inspect

from app.voice import TextToSpeech, TTSResult, WordTiming
from app.voice.audio import PCMAudio
from app.voice.normalizer import estimate_word_timings

TAMIL_FEMALE = "ta-IN-PallaviNeural"
ENGLISH_FEMALE = "en-IN-NeerjaNeural"
FEMALE_VOICES = (TAMIL_FEMALE, ENGLISH_FEMALE, "en-US-AriaNeural", "en-GB-SoniaNeural",
                 "en-US-JennyNeural", "ta-LK-SaranyaNeural", "ta-MY-KaniNeural", "ta-SG-VenbaNeural")


def _mp3_duration_ms(audio: bytes) -> int:
    """Duration of the MP3 stream: decode via ffmpeg if possible, else 48 kbps estimate."""
    try:
        from app.voice.audio import decode
        samples, sr = decode(audio, "audio/mpeg")
        if len(samples):
            return int(len(samples) * 1000 / sr)
    except Exception:
        pass
    # edge-tts default output is 24 kHz 48 kbit/s mono MP3 → 6000 bytes / second
    return int(len(audio) / 6000 * 1000)


class EdgeTTSProvider(TextToSpeech):
    name = "edge_tts"
    voices = FEMALE_VOICES

    def __init__(self, voice: str = TAMIL_FEMALE) -> None:
        self.default_voice = voice or TAMIL_FEMALE

    @classmethod
    def is_installed(cls) -> bool:
        return importlib.util.find_spec("edge_tts") is not None

    def pick_voice(self, voice: str = "") -> str:
        # Any Azure neural voice id is accepted ("xx-XX-NameNeural")
        if voice and voice.endswith("Neural"):
            return voice
        return self.default_voice

    async def _stream(self, text: str, voice: str, speed: float):
        try:
            import edge_tts
        except ImportError:
            raise RuntimeError("edge-tts not installed: pip install edge-tts")

        rate = f"{int(round((speed - 1.0) * 100)):+d}%"
        kwargs = {"rate": rate}
        if "boundary" in inspect.signature(edge_tts.Communicate.__init__).parameters:
            kwargs["boundary"] = "WordBoundary"
        communicate = edge_tts.Communicate(text, voice, **kwargs)
        audio_chunks: list[bytes] = []
        boundaries: list[tuple[str, int, int]] = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_chunks.append(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                offset_ms = chunk.get("offset", 0) // 10000  # 100ns → ms
                duration_ms = chunk.get("duration", 0) // 10000
                boundaries.append((chunk.get("text", ""), offset_ms, offset_ms + duration_ms))
        audio = b"".join(audio_chunks)
        if not audio:
            raise RuntimeError("edge-tts returned no audio")
        return audio, boundaries

    async def synthesize_pcm(self, text: str, voice: str = "", speed: float = 1.0) -> PCMAudio:
        from app.voice.audio import decode
        audio, boundaries = await self._stream(text, self.pick_voice(voice), speed)
        samples, sr = await asyncio.to_thread(decode, audio, "audio/mpeg")
        return PCMAudio(samples=samples, sample_rate=sr, boundaries=boundaries, speed_applied=True)

    async def synthesize(self, text: str, voice: str = "", speed: float = 1.0) -> TTSResult:
        v = self.pick_voice(voice)
        audio_bytes, boundaries = await self._stream(text, v, speed)
        duration_ms = await asyncio.to_thread(_mp3_duration_ms, audio_bytes)
        word_timings = [WordTiming(w, s, e) for (w, s, e) in boundaries]
        if not word_timings:
            estimated = estimate_word_timings(text, duration_ms)
            word_timings = [WordTiming(w["word"], w["startMs"], w["endMs"]) for w in estimated]
        return TTSResult(
            audio_bytes=audio_bytes,
            mime_type="audio/mpeg",
            word_timings=word_timings,
            duration_ms=max(duration_ms, word_timings[-1].end_ms if word_timings else 0),
            provider=self.name,
            voice=v,
        )
