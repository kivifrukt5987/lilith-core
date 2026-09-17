"""Шина face-кадров (этап 5 v2): один источник анимации — много потребителей.

Панель получает кадры напрямую в свой WS; :class:`FaceBus` раздаёт те же кадры
внешним подписчикам — зарезервированному ``/ws/unity`` (Unity+VRM+SALSA) и будущей
OBS-сцене этапа 8. Кадр — простой словарь: ``{"kind": "emotion"|"viseme"|"audio"|"done", ...}``.
"""

from __future__ import annotations

import asyncio
from typing import Any

__all__ = ["FaceBus"]


class FaceBus:
    """Веер face-кадров по подписчикам (asyncio-очереди)."""

    def __init__(self, maxsize: int = 256) -> None:
        self.maxsize = maxsize
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        """Новая очередь подписчика."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.maxsize)
        self._subs.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Отписать очередь."""
        self._subs.discard(queue)

    @property
    def subscribers(self) -> int:
        """Число активных подписчиков."""
        return len(self._subs)

    async def publish(self, frame: dict[str, Any]) -> int:
        """Разослать кадр; переполненная очередь пропускает кадр, не блокируя."""
        delivered = 0
        for queue in list(self._subs):
            try:
                queue.put_nowait(frame)
                delivered += 1
            except asyncio.QueueFull:
                continue
        return delivered
