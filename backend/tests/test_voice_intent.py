"""
tests/test_voice_intent.py — POST /api/voice/intent (R1 voice commands).

The LLM router is replaced with a stub; no provider is called.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.llm import LLMResponse
from app.main import app


class _StubRouter:
    def __init__(self, reply: str | None = None, error: Exception | None = None, delay: float = 0.0):
        self.reply = reply
        self.error = error
        self.delay = delay
        self.calls: list[dict] = []

    async def generate_json(self, prompt: str, schema: dict, system: str = "", **kwargs) -> LLMResponse:
        self.calls.append({"prompt": prompt, "schema": schema, "system": system, **kwargs})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return LLMResponse(text=self.reply or "", provider="stub", model="stub")


@pytest.fixture
def stub_router(monkeypatch):
    def install(**kwargs) -> _StubRouter:
        router = _StubRouter(**kwargs)
        monkeypatch.setattr("app.llm.router.get_llm_router", lambda: router)
        return router
    return install


async def _post(body: dict):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/voice/intent", json=body)


@pytest.mark.asyncio
async def test_intent_requires_auth(app_client):
    resp = await _post({"text": "stop"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_intent_classifies_command(voice_user_override, stub_router):
    router = stub_router(reply=json.dumps({"intent": "next_page", "args": {}, "confidence": 0.93}))
    resp = await _post({"text": "adutha pakkathuku polaama", "lang": "tanglish", "page_title": "Variables",
                        "local_intent": "next_page", "local_confidence": 0.5})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"intent": "next_page", "args": {}, "confidence": 0.93, "source": "llm"}
    call = router.calls[0]
    assert "adutha pakkathuku polaama" in call["prompt"]
    assert "Variables" in call["prompt"]
    assert call["max_tokens"] <= 100 and call["temperature"] == 0.0
    assert "next_page" in call["schema"]["properties"]["intent"]["enum"]


@pytest.mark.asyncio
async def test_intent_goto_args_are_validated(voice_user_override, stub_router):
    stub_router(reply='Sure! {"intent": "goto_chapter", "args": {"chapter": "3", "page": 2, "evil": "x"}, "confidence": 2}')
    body = (await _post({"text": "moonavathu chapter rendavathu page"})).json()
    assert body["intent"] == "goto_chapter"
    assert body["args"] == {"chapter": 3, "page": 2}
    assert body["confidence"] == 1.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        "not json at all",
        json.dumps({"intent": "delete_everything", "confidence": 0.9}),
        json.dumps({"intent": "goto_page", "args": {}, "confidence": 0.9}),
        json.dumps(["stop"]),
    ],
)
async def test_intent_bad_llm_reply_falls_back_to_question(voice_user_override, stub_router, reply):
    stub_router(reply=reply)
    body = (await _post({"text": "hmm stop maybe"})).json()
    assert body == {"intent": "question", "args": {}, "confidence": 0.0, "source": "fallback"}


@pytest.mark.asyncio
async def test_intent_llm_failure_falls_back(voice_user_override, stub_router):
    from app.llm.router import LLMError

    stub_router(error=LLMError("all exhausted", provider="all", is_exhausted=True))
    body = (await _post({"text": "konjam wait pannunga"})).json()
    assert body["intent"] == "question" and body["source"] == "fallback"


@pytest.mark.asyncio
async def test_intent_llm_timeout_falls_back(voice_user_override, stub_router, monkeypatch):
    monkeypatch.setattr("app.api.voice.INTENT_TIMEOUT_S", 0.05)
    stub_router(reply=json.dumps({"intent": "stop", "confidence": 1}), delay=1.0)
    body = (await _post({"text": "stop"})).json()
    assert body["intent"] == "question" and body["source"] == "fallback"


@pytest.mark.asyncio
async def test_intent_validates_input(voice_user_override, stub_router):
    stub_router(reply="{}")
    assert (await _post({"text": ""})).status_code == 422
    assert (await _post({"text": "x" * 301})).status_code == 422
    assert (await _post({"text": "   "})).status_code == 400


@pytest.mark.asyncio
async def test_intent_route_is_rate_limited(app_client, voice_user_override, stub_router, monkeypatch):
    from app.config import get_settings
    from app.middleware.rate_limit import limiter

    stub_router(reply=json.dumps({"intent": "stop", "confidence": 0.9}))
    limiter.reset()
    monkeypatch.setattr(limiter, "enabled", True)
    monkeypatch.setattr(get_settings(), "rate_limit_voice_per_minute", 2)
    try:
        codes = [(await _post({"text": "stop"})).status_code for _ in range(3)]
    finally:
        limiter.reset()
    assert codes == [200, 200, 429]
