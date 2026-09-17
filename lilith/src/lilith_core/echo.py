"""Заглушка-обработчик реплик для этапа 1.

Сейчас «ответ персонажа» — это эхо. Модуль намеренно вынесен отдельно и повторяет
сигнатуру будущего :func:`lilith_core.brain.llm.reply`, поэтому на этапе 2 замена
сводится к одной строчке в ``app.py``:

.. code-block:: python

    handler = build_reply_handler(settings)   # эхо -> мозг

Так веб-панель и мосты не придётся переписывать под каждый новый слой.
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from loguru import logger

from .config import Settings
from .protocol import Message, MsgType, build_message

__all__ = ["ReplyHandler", "echo_reply", "build_reply_handler"]

#: Обработчик реплики: ``(текст пользователя, контекст) -> ответное сообщение``.
ReplyHandler = Callable[[str, dict[str, Any]], Awaitable[Message]]


async def echo_reply(text: str, context: dict[str, Any] | None = None) -> Message:
    """Возвращает полученный текст обратно (режим этапа 1).

    :param text: реплика пользователя.
    :param context: служебный словарь (session_id, source и т.п.) — прокидывается в ответ.
    """
    context = context or {}
    started = time.perf_counter()

    cleaned = (text or "").strip()
    if not cleaned:
        reply_text = "…тишина? Кирюша, я слышу только твоё сердце. Скажи что-нибудь. 🦇"
    else:
        reply_text = cleaned

    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.debug("ECHO: {} символ(ов), {:.3f} мс", len(cleaned), elapsed_ms)

    return build_message(
        MsgType.CHAT,
        text=reply_text,
        data={
            "role": "assistant",
            "mode": "echo",
            "echo": True,
            "latency_ms": round(elapsed_ms, 3),
            "source": context.get("source", "webui"),
            "session_id": context.get("session_id"),
            "note": "Этап 1: мозг ещё не подключён, сервер отвечает эхом.",
        },
    )


def build_reply_handler(settings: Settings, cycle: Any | None = None) -> ReplyHandler:
    """Фабрика обработчика реплик по текущему конфигу.

    Этап 1: эхо. Этап 2+: при ``features.brain_enabled`` возвращает обработчик
    мозга (:func:`lilith_core.brain.make_reply_handler`) с тем же контрактом.
    Импорт мозга ленивый, чтобы не словить циклический импорт (brain -> echo).
    """
    if settings.features.brain_enabled:
        from .brain import make_reply_handler  # noqa: WPS433 - ленивый импорт намеренный

        return make_reply_handler(settings, cycle=cycle)
    return echo_reply
