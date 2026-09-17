"""Mock-мозг для тестов и демо без модели (этап 2).

Включается профилем с ``provider: mock`` (или флагом ``--mock-brain`` в CLI).
Детерминированный, считает вызовы, умеет имитировать стриминг по словам
и падать по ключевому слову — ровно то, что нужно тестам шины и панели.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from .llm import BrainError, CompletionResult, TokenCallback, Usage, estimate_tokens
from ..config import ResolvedProfile

__all__ = ["MockBrain"]


@dataclass(slots=True)
class MockBrain:
    """Детерминированная заглушка мозга с тем же интерфейсом, что у :class:`LLMClient`."""

    latency: float = 0.0
    #: Если эта подстрока есть в реплике пользователя — мозг «падает» (для тестов ошибок).
    fail_on: str | None = None
    #: Префикс ответа; по умолчанию собирается из имени профиля.
    prefix: str | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    # -- интерфейс клиента --------------------------------------------------- #
    async def health(self, profile: ResolvedProfile, timeout: float = 3.0) -> dict[str, Any]:
        """Mock всегда «здоров»."""
        return {"ok": True, "error": None, "latency_ms": 0.0, "models": [f"mock-{profile.name}"]}

    async def complete(
        self,
        profile: ResolvedProfile,
        messages: list[dict[str, Any]],
        on_token: TokenCallback | None = None,
    ) -> CompletionResult:
        """Генерирует детерминированный ответ: «мок-<профиль>: слышу «...» и мурчу»."""
        user_text = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                user_text = str(message.get("content") or "")
                break

        self.calls.append({"profile": profile.name, "text": user_text, "messages": len(messages)})

        if self.fail_on and self.fail_on in user_text:
            raise BrainError(f"mock-мозг упал по запросу (fail_on={self.fail_on!r})")

        if self.latency:
            await asyncio.sleep(self.latency)

        prefix = self.prefix or f"мок-{profile.name}"
        reply = f"{prefix}: слышу «{user_text}» и мурчу в ответ. 🦇"

        started = asyncio.get_event_loop().time()
        if on_token is not None:
            words = reply.split(" ")
            for i, word in enumerate(words):
                await on_token(word if i == len(words) - 1 else word + " ")
        elapsed_ms = (asyncio.get_event_loop().time() - started) * 1000 + self.latency * 1000

        usage = Usage(
            prompt_tokens=sum(estimate_tokens(str(m.get("content") or "")) for m in messages),
            completion_tokens=estimate_tokens(reply),
            estimated=True,
        )
        usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
        tok_per_sec = usage.completion_tokens / (elapsed_ms / 1000) if elapsed_ms > 0 else None

        return CompletionResult(
            text=reply,
            profile=profile.name,
            model=f"mock-{profile.model}",
            usage=usage,
            latency_ms=elapsed_ms,
            first_token_ms=elapsed_ms / 2 if on_token else elapsed_ms,
            tok_per_sec=tok_per_sec,
            finish_reason="stop",
        )

    # -- удобства для тестов -------------------------------------------------- #
    @property
    def call_count(self) -> int:
        """Сколько раз вызывали генерацию."""
        return len(self.calls)

    def last_call(self) -> dict[str, Any]:
        """Последний вызов (profile/text/messages)."""
        return self.calls[-1]
