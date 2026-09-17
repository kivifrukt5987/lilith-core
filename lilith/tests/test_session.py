"""Тесты менеджера сессий."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from lilith_core.protocol import chat_message
from lilith_core.session import Session, SessionManager


class FakeWebSocket:
    """Мини-заглушка сокета: пишет всё отправленное в список."""

    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[str] = []
        self.fail = fail
        self.closed = False

    async def send_text(self, data: str) -> None:
        if self.fail:
            raise RuntimeError("socket is dead")
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
class TestSessionManager:
    """Регистрация, статистика, рассылка."""

    async def test_register_and_unregister(self) -> None:
        manager = SessionManager()
        ws = FakeWebSocket()

        session = await manager.register(ws, remote="127.0.0.1:1", user_agent="pytest")
        assert manager.active_count == 1
        assert manager.total_connections == 1
        assert session.remote == "127.0.0.1:1"
        assert session.user_agent == "pytest"

        removed = await manager.unregister(session.id)
        assert removed is session
        assert manager.active_count == 0
        assert await manager.unregister(session.id) is None

    async def test_max_sessions_limit(self) -> None:
        manager = SessionManager(max_sessions=2)
        await manager.register(FakeWebSocket())
        await manager.register(FakeWebSocket())

        with pytest.raises(RuntimeError, match="превышен лимит"):
            await manager.register(FakeWebSocket())

    async def test_send_increments_counters(self) -> None:
        manager = SessionManager()
        ws = FakeWebSocket()
        session = await manager.register(ws)

        ok = await manager.send(session, chat_message("привет"))
        assert ok is True
        assert session.messages_out == 1
        assert manager.total_messages_out == 1
        assert len(ws.sent) == 1
        assert '"привет"' in ws.sent[0]

    async def test_send_failure_unregisters_session(self) -> None:
        manager = SessionManager()
        session = await manager.register(FakeWebSocket(fail=True))

        ok = await manager.send(session, chat_message("ау"))
        assert ok is False
        assert manager.errors == 1
        assert manager.active_count == 0

    async def test_broadcast_skips_excluded(self) -> None:
        manager = SessionManager()
        ws_a, ws_b = FakeWebSocket(), FakeWebSocket()
        a = await manager.register(ws_a)
        await manager.register(ws_b)

        delivered = await manager.broadcast(chat_message("всем"), exclude=a.id)
        assert delivered == 1
        assert ws_a.sent == []
        assert len(ws_b.sent) == 1

    async def test_broadcast_counts_failures(self) -> None:
        manager = SessionManager()
        await manager.register(FakeWebSocket())
        await manager.register(FakeWebSocket(fail=True))

        delivered = await manager.broadcast(chat_message("всем"))
        assert delivered == 1
        assert manager.errors == 1

    async def test_note_incoming(self) -> None:
        manager = SessionManager()
        session = await manager.register(FakeWebSocket())

        manager.note_incoming(session)
        manager.note_incoming(session)

        assert session.messages_in == 2
        assert manager.total_messages_in == 2

    async def test_get_and_active(self) -> None:
        manager = SessionManager()
        session = await manager.register(FakeWebSocket())

        assert manager.get(session.id) is session
        assert manager.get("несуществует") is None
        assert manager.active == [session]

    async def test_stats_shape(self) -> None:
        manager = SessionManager()
        await manager.register(FakeWebSocket())
        stats = manager.stats()

        for key in ("active", "max_sessions", "total_connections", "total_messages_in",
                    "total_messages_out", "errors", "sessions", "server_time"):
            assert key in stats
        assert stats["active"] == 1
        assert isinstance(stats["sessions"], list)

    async def test_session_info_has_no_socket(self) -> None:
        session = Session(id="s1", websocket=FakeWebSocket(), remote="r")
        info = session.info()
        assert "websocket" not in info
        assert info["id"] == "s1"

    async def test_touch_updates_last_seen(self) -> None:
        session = Session(id="s1", websocket=FakeWebSocket())
        before = session.last_seen
        await asyncio.sleep(0.01)
        session.touch()
        assert session.last_seen > before

    async def test_concurrent_registration(self) -> None:
        manager = SessionManager(max_sessions=50)
        sessions = await asyncio.gather(*[manager.register(FakeWebSocket()) for _ in range(20)])
        assert len({s.id for s in sessions}) == 20
        assert manager.active_count == 20

    async def test_send_accepts_any_message(self) -> None:
        manager = SessionManager()
        ws: Any = FakeWebSocket()
        session = await manager.register(ws)
        await manager.send(session, chat_message("x"))
        assert ws.sent[0].startswith("{")
