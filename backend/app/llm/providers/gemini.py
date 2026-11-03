"""
app/llm/providers/gemini.py — Google Gemini provider via google-generativeai SDK.

Config env vars:
  GEMINI_API_KEY
  GEMINI_MODEL  (default: gemini-1.5-flash)
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.llm import LLMImage, LLMProvider, LLMResponse


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, api_key: str, model: str, vision_model: str = "") -> None:
        self._api_key = api_key
        self._model = model
        self._vision_model = vision_model or ""
        self.supports_images = bool(self._vision_model)
        self._base = "https://generativelanguage.googleapis.com/v1"

    async def is_available(self) -> bool:
        return bool(self._api_key)

    async def generate_text(self, prompt: str, system: str = "", temperature: float = 0.7, max_tokens: int = 4096,
                            timeout: float = 30.0, images: list[LLMImage] | None = None) -> LLMResponse:
        return await self._call(prompt, system, temperature, max_tokens, timeout, json_mode=False, images=images)

    async def generate_json(self, prompt: str, schema: dict, system: str = "", temperature: float = 0.2,
                            max_tokens: int = 8192, timeout: float = 60.0,
                            images: list[LLMImage] | None = None) -> LLMResponse:
        # Gemini supports response_mime_type for JSON
        return await self._call(
            prompt + "\n\nRespond ONLY with valid JSON matching the schema. No markdown, no explanation.",
            system, temperature, max_tokens, timeout, json_mode=True, images=images,
        )

    async def _call(self, prompt: str, system: str, temperature: float, max_tokens: int, timeout: float,
                    json_mode: bool, images: list[LLMImage] | None = None) -> LLMResponse:
        import base64

        from app.llm.router import LLMError

        if images and not self._vision_model:
            raise LLMError("Gemini: no vision model configured", provider=self.name, retryable=False)
        model = self._vision_model if images else self._model
        url = f"{self._base}/models/{model}:generateContent?key={self._api_key}"
        parts: list[dict[str, Any]] = [{"text": prompt}]
        for im in images or []:
            parts.append({"inline_data": {"mime_type": im.mime_type,
                                          "data": base64.b64encode(im.data).decode("ascii")}})
        contents = [{"role": "user", "parts": parts}]
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=body)
        except httpx.TimeoutException as exc:
            raise LLMError("Gemini timeout", provider=self.name, retryable=True) from exc

        if resp.status_code == 429:
            raise LLMError("Gemini 429 quota", provider=self.name, retryable=True)
        if resp.status_code != 200:
            raise LLMError(f"Gemini {resp.status_code}: {resp.text[:200]}", provider=self.name, retryable=False)

        data = resp.json()
        latency = (time.monotonic() - t0) * 1000
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Gemini unexpected response: {data}", provider=self.name, retryable=False) from exc

        usage = data.get("usageMetadata", {})
        return LLMResponse(
            text=text,
            provider=self.name,
            model=model,
            tokens_in=usage.get("promptTokenCount", 0),
            tokens_out=usage.get("candidatesTokenCount", 0),
            latency_ms=latency,
            raw=data,
        )
