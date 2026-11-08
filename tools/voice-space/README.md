---
title: Bookey Voice
emoji: 📖
colorFrom: yellow
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---

# Bookey voice service

Bookey's own Tanglish voices (Piper) and Tanglish speech recognition (fine-tuned whisper-small),
on free CPU. See `app.py` for the API. The Bookey backend calls it with `VOICE_SPACE_URL` and
`VOICE_SPACE_TOKEN`, and falls back to Edge TTS / Groq Whisper when it is unavailable.

Space secrets: `VOICE_TOKEN` (shared with the backend), `HF_TOKEN` (read access to the private
model repo `MODEL_REPO`, default `mohamedraj/bookey-voice-models`).

Model repo layout: `tts/ta-bookey-medium.onnx` + `.onnx.json`, `stt/` (CTranslate2 whisper-small).
