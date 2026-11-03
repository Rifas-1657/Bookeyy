"""
app/llm/providers/groq.py — Groq (ultra-fast inference) provider.

Config env vars:
  GROQ_API_KEY
  GROQ_MODEL  (default: llama-3.1-70b-versatile)
  GROQ_VISION_MODEL  optional multimodal model for `images=` calls (OpenAI-style content parts)

Groq exposes an OpenAI-compatible API. We use json_object response_format for JSON mode.
"""
from __future__ import annotations

import json
import time

import httpx

from app.llm import LLMImage, LLMProvider, LLMResponse, openai_content_parts


class GroqProvider(LLMProvider):
    name = "groq"
    _BASE = "https://api.groq.com/openai/v1"

    def __init__(self, api_key: str, model: str, vision_model: str = "") -> None:
        self._api_key = api_key
        self._model = model
        self._vision_model = vision_model or ""
        self.supports_images = bool(self._vision_model)

    async def is_available(self) -> bool:
        return bool(self._api_key)

    async def generate_text(self, prompt: str, system: str = "", temperature: float = 0.7, max_tokens: int = 4096,
                            timeout: float = 30.0, images: list[LLMImage] | None = None) -> LLMResponse:
        return await self._oai_call(prompt, system, temperature, max_tokens, timeout, json_mode=False, images=images)

    async def generate_json(self, prompt: str, schema: dict, system: str = "", temperature: float = 0.2,
                            max_tokens: int = 8192, timeout: float = 60.0,
                            images: list[LLMImage] | None = None) -> LLMResponse:
        return await self._oai_call(
            prompt + "\n\nRespond ONLY with valid JSON. No markdown fences.",
            system, temperature, max_tokens, timeout, json_mode=True, images=images,
        )

    async def _oai_call(self, prompt: str, system: str, temperature: float, max_tokens: int, timeout: float,
                        json_mode: bool, images: list[LLMImage] | None = None) -> LLMResponse:
        from app.llm.router import LLMError

        if images and not self._vision_model:
            raise LLMError("Groq: no vision model configured", provider=self.name, retryable=False)
        model = self._vision_model if images else self._model
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": openai_content_parts(prompt, images)})

        body: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        # gpt-oss models reason before answering; low effort keeps tutor replies fast
        # and leaves max_tokens for the answer itself.
        if model.startswith("openai/gpt-oss"):
            body["reasoning_effort"] = "low"

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(f"{self._BASE}/chat/completions", json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise LLMError("Groq timeout", provider=self.name, retryable=True) from exc

        if resp.status_code == 429:
            raise LLMError("Groq 429 rate-limit", provider=self.name, retryable=True)
        if resp.status_code != 200:
            # JSON-mode validation failures are sampling noise; another attempt usually succeeds.
            retryable = resp.status_code == 400 and "json_validate_failed" in resp.text
            raise LLMError(f"Groq {resp.status_code}: {resp.text[:200]}", provider=self.name, retryable=retryable)

        data = resp.json()
        latency = (time.monotonic() - t0) * 1000
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return LLMResponse(
            text=text,
            provider=self.name,
            model=model,
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
            latency_ms=latency,
            raw=data,
        )
