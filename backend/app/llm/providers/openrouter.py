"""
app/llm/providers/openrouter.py — OpenRouter provider (access to 100+ models).

Config env vars:
  OPENROUTER_API_KEY
  OPENROUTER_MODEL           (text model)
  OPENROUTER_VISION_MODELS   comma-separated multimodal models for `images=` calls, tried in
                             order (free vision models are often rate-limited upstream → 429/503
                             moves on to the next one).
"""
from __future__ import annotations

import time

import httpx

from app.llm import LLMImage, LLMProvider, LLMResponse, openai_content_parts


class OpenRouterProvider(LLMProvider):
    name = "openrouter"
    _BASE = "https://openrouter.ai/api/v1"

    def __init__(self, api_key: str, model: str, vision_models: list[str] | None = None) -> None:
        self._api_key = api_key
        self._model = model
        self._vision_models = [m for m in (vision_models or []) if m]
        self.supports_images = bool(self._vision_models)

    async def is_available(self) -> bool:
        return bool(self._api_key)

    async def generate_text(self, prompt: str, system: str = "", temperature: float = 0.7, max_tokens: int = 4096,
                            timeout: float = 30.0, images: list[LLMImage] | None = None) -> LLMResponse:
        return await self._call(prompt, system, temperature, max_tokens, timeout, json_mode=False, images=images)

    async def generate_json(self, prompt: str, schema: dict, system: str = "", temperature: float = 0.2,
                            max_tokens: int = 8192, timeout: float = 60.0,
                            images: list[LLMImage] | None = None) -> LLMResponse:
        return await self._call(
            prompt + "\n\nRespond ONLY with valid JSON. No markdown.",
            system, temperature, max_tokens, timeout, json_mode=True, images=images,
        )

    async def _call(self, prompt: str, system: str, temperature: float, max_tokens: int, timeout: float,
                    json_mode: bool, images: list[LLMImage] | None = None) -> LLMResponse:
        from app.llm.router import LLMError

        if images and not self._vision_models:
            raise LLMError("OpenRouter: no vision model configured", provider=self.name, retryable=False)
        models = self._vision_models if images else [self._model]
        last: LLMError | None = None
        for i, model in enumerate(models):
            try:
                return await self._call_model(model, prompt, system, temperature, max_tokens, timeout,
                                              json_mode, images)
            except LLMError as exc:
                last = exc
                # vision fallback: rate-limited / unavailable / model gone → next model
                if not images or i == len(models) - 1 or not getattr(exc, "next_model", False):
                    raise
        raise last or LLMError("OpenRouter: no model", provider=self.name)

    async def _call_model(self, model: str, prompt: str, system: str, temperature: float, max_tokens: int,
                          timeout: float, json_mode: bool, images: list[LLMImage] | None) -> LLMResponse:
        from app.llm.router import LLMError

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
        if json_mode and not images:   # many free vision models reject response_format
            body["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "HTTP-Referer": "https://bookey.app",
            "X-Title": "Bookey Tutor",
        }
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(f"{self._BASE}/chat/completions", json=body, headers=headers)
        except httpx.TimeoutException as exc:
            err = LLMError("OpenRouter timeout", provider=self.name, retryable=True)
            err.next_model = True
            raise err from exc

        if resp.status_code == 429:
            err = LLMError(f"OpenRouter 429 rate-limit ({model})", provider=self.name, retryable=True)
            err.next_model = True
            raise err
        if resp.status_code != 200:
            err = LLMError(f"OpenRouter {resp.status_code}: {resp.text[:200]}", provider=self.name, retryable=False)
            err.next_model = resp.status_code in (400, 402, 404, 502, 503)
            raise err

        data = resp.json()
        latency = (time.monotonic() - t0) * 1000
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            err = LLMError(f"OpenRouter unexpected response: {str(data)[:200]}", provider=self.name)
            err.next_model = True
            raise err from exc
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
