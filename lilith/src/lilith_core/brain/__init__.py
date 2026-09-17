"""Слой «мозг» (этап 2): клиент, чат-цикл, mock и фабрика обработчика.

Точка входа для ядра — :func:`make_reply_handler`: возвращает обработчик
с тем же контрактом ``ReplyHandler``, что и эхо этапа 1, поэтому шина,
панель и будущие мосты не меняются.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from ..config import Settings
from ..echo import ReplyHandler, echo_reply
from ..protocol import Message
from .chat import ChatCycle, InMemoryHistory, PushCallback, build_client_for
from .llm import (
    BrainConnectionError,
    BrainError,
    BrainHTTPError,
    BrainTimeoutError,
    CompletionResult,
    LLMClient,
    Usage,
    estimate_tokens,
)
from .mock import MockBrain
from .stats import TokenStats

__all__ = [
    "BrainConnectionError",
    "BrainError",
    "BrainHTTPError",
    "BrainTimeoutError",
    "ChatCycle",
    "CompletionResult",
    "InMemoryHistory",
    "LLMClient",
    "MockBrain",
    "PushCallback",
    "TokenStats",
    "Usage",
    "build_client_for",
    "estimate_tokens",
    "make_reply_handler",
]


def make_reply_handler(settings: Settings, *, cycle: ChatCycle | None = None) -> ReplyHandler:
    """Собирает обработчик реплик: мозг, если включён, иначе эхо этапа 1.

    :param settings: конфигурация приложения.
    :param cycle: готовый :class:`ChatCycle` (в тестах — с mock-транспортом).
    """
    if not settings.features.brain_enabled:
        return echo_reply

    chat_cycle = cycle or ChatCycle(settings)

    async def brain_reply(text: str, context: dict[str, Any]) -> Message:
        """Прогоняет реплику через чат-цикл; ошибки мозга уходят наверх в шину."""
        push = context.get("push")
        return await chat_cycle.reply(text, context, push=push)

    logger.info(
        "Мозг включён: профиль по умолчанию '{}', провайдер '{}'",
        settings.brain.default_profile,
        settings.brain.resolve().provider,
    )
    return brain_reply
