"""app/voice/tts/__init__.py

Heavy local engines (pocket_tts, kokoro, indic_parler) are imported lazily by
app.voice.pipeline so the server boots without them installed.
"""
from app.voice.tts.bodhan_tts import BodhanTTSProvider
from app.voice.tts.gemini_tts import GeminiTTSProvider
from app.voice.tts.edge_tts_provider import EdgeTTSProvider
from app.voice.tts.silent import SilentTTS

__all__ = ["BodhanTTSProvider", "GeminiTTSProvider", "EdgeTTSProvider", "SilentTTS"]
