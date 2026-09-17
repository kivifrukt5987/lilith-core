"""Менеджер WebSocket-сессий.

Держит реестр подключений (веб-панель, мосты, отладочные клиенты), считает
статистику и умеет рассылать сообщения всем сразу — это понадобится на этапе 6,
когда сервер будет пушить в панель баннеры подтверждения инструментов.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from .protocol import Message, new_id, now_iso

__all__ = ["Session", "SessionManager"]


@dataclass(slots=True)
class Session:
    """Одно клиентское подключение."""

    id: str
    websocket: Any  # starlette.websockets.WebSocket
    remote: str = "unknown"
    user_agent: str = ""
    connected_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    messages_in: int = 0
    messages_out: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def uptime_sec(self) -> float:
        """Сколько секунд живёт подключение."""
        return round(time.time() - self.connected_at, 3)

    def touch(self) -> None:
        """Обновляет отметку «последней активности»."""
        self.last_seen = time.time()

    def info(self) -> dict[str, Any]:
        """Короткая сводка о сессии (без объекта сокета) — для логов и API."""
        return {
            "id": self.id,
            "remote": self.remote,
            "connected_at": self.connected_at,
            "uptime_sec": self.uptime_sec,
            "messages_in": self.messages_in,
            "messages_out": self.messages_out,
            "user_agent": self.user_agent,
            "meta": self.meta,
        }


class SessionManager:
    """Реестр активных подключений + накопительная статистика."""

    def __init__(self, *, max_sessions: int = 64) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()
        self.max_sessions = max_sessions
        self.total_connections = 0
        self.total_messages_in = 0
        self.total_messages_out = 0
        self.errors = 0

    # -- жизненный цикл ---------------------------------------------------- #
    async def register(self, websocket: Any, *, remote: str = "unknown", user_agent: str = "") -> Session:
        """Добавляет подключение в реестр и возвращает сессию."""
        async with self._lock:
            if len(self._sessions) >= self.max_sessions:
                raise RuntimeError(f"превышен лимит подключений ({self.max_sessions})")
            session = Session(id=new_id(), websocket=websocket, remote=remote, user_agent=user_agent)
            self._sessions[session.id] = session
            self.total_connections += 1
        logger.info("WS: подключение #{} ({}) — активно {}", session.id, remote, len(self._sessions))
        return session

    async def unregister(self, session_id: str) -> Session | None:
        """Убирает подключение из реестра."""
        async with self._lock:
            session = self._sessions.pop(session_id, None)
        if session:
            logger.info(
                "WS: отключение #{} (жило {} c, принято {}, отправлено {})",
                session_id,
                session.uptime_sec,
                session.messages_in,
                session.messages_out,
            )
        return session

    # -- доступ ------------------------------------------------------------ #
    def get(self, session_id: str) -> Session | None:
        """Сессия по id или ``None``."""
        return self._sessions.get(session_id)

    @property
    def active(self) -> list[Session]:
        """Список активных сессий."""
        return list(self._sessions.values())

    @property
    def active_count(self) -> int:
        """Число активных подключений."""
        return len(self._sessions)

    # -- обмен ------------------------------------------------------------- #
    async def send(self, session: Session, message: Message) -> bool:
        """Отправляет сообщение конкретной сессии. Возвращает успех."""
        try:
            await session.websocket.send_text(message.to_json())
        except Exception as exc:  # noqa: BLE001 - сокет мог закрыться в любой момент
            logger.warning("WS: не удалось отправить в сессию {}: {!r}", session.id, exc)
            self.errors += 1
            await self.unregister(session.id)
            return False
        session.messages_out += 1
        self.total_messages_out += 1
        session.touch()
        return True

    async def broadcast(self, message: Message, *, exclude: str | None = None) -> int:
        """Рассылает сообщение всем активным сессиям. Возвращает число доставок."""
        delivered = 0
        for session in self.active:
            if exclude and session.id == exclude:
                continue
            if await self.send(session, message):
                delivered += 1
        return delivered

    def note_incoming(self, session: Session) -> None:
        """Отмечает входящее сообщение в статистике."""
        session.messages_in += 1
        self.total_messages_in += 1
        session.touch()

    def stats(self) -> dict[str, Any]:
        """Сводка для ``/healthz`` и веб-панели."""
        return {
            "active": self.active_count,
            "max_sessions": self.max_sessions,
            "total_connections": self.total_connections,
            "total_messages_in": self.total_messages_in,
            "total_messages_out": self.total_messages_out,
            "errors": self.errors,
            "sessions": [s.info() for s in self.active],
            "server_time": now_iso(),
        }
