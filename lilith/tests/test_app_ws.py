"""Тесты WebSocket-шины (этап 1: эхо)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from lilith_core import __stage__, __version__
from lilith_core.app import create_app
from lilith_core.echo import build_reply_handler, echo_reply
from lilith_core.protocol import MsgType


class TestHandshake:
    """Подключение и рукопожатие."""

    def test_hello_on_connect(self, client: TestClient) -> None:
        with client.websocket_connect("/ws") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            assert hello["data"]["version"] == __version__
            assert hello["data"]["stage"] == __stage__
            assert hello["data"]["mode"] == "echo"
            assert hello["data"]["session_id"]

            system = ws.receive_json()
            assert system["type"] == "system"

    def test_hello_response_to_client_hello(self, client: TestClient) -> None:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()   # server hello
            ws.receive_json()   # system notice

            ws.send_json({"type": "hello", "data": {"client_id": "pytest"}})
            reply = ws.receive_json()

            assert reply["type"] == "system"
            assert reply["data"]["client_id"] == "pytest"
            assert reply["data"]["session_id"]

    def test_session_ids_are_unique(self, client: TestClient) -> None:
        ids = set()
        for _ in range(5):
            with client.websocket_connect("/ws") as ws:
                ids.add(ws.receive_json()["data"]["session_id"])
                ws.receive_json()
        assert len(ids) == 5


class TestEcho:
    """Основной сценарий этапа 1: сервер возвращает текст обратно."""

    def test_echo_returns_same_text(self, client: TestClient) -> None:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.receive_json()

            ws.send_json({"type": "chat", "text": "Лиль, ты тут?"})
            reply = ws.receive_json()

            assert reply["type"] == "chat"
            assert reply["text"] == "Лиль, ты тут?"
            assert reply["data"]["role"] == "assistant"
            assert reply["data"]["echo"] is True
            assert reply["data"]["mode"] == "echo"
            assert reply["data"]["source"] == "webui"
            assert reply["data"]["latency_ms"] >= 0

    def test_echo_preserves_unicode(self, client: TestClient) -> None:
        text = "мур-мур 🦇💖 ёЖИК"
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.receive_json()
            ws.send_json({"type": "chat", "text": text})
            assert ws.receive_json()["text"] == text

    def test_echo_long_text(self, client: TestClient) -> None:
        text = "а" * 5000
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.receive_json()
            ws.send_json({"type": "chat", "text": text})
            assert ws.receive_json()["text"] == text

    def test_source_is_forwarded(self, client: TestClient) -> None:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.receive_json()
            ws.send_json({"type": "chat", "text": "из дискорда", "data": {"source": "discord"}})
            assert ws.receive_json()["data"]["source"] == "discord"

    def test_empty_chat_is_rejected(self, client: TestClient) -> None:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.receive_json()
            ws.send_json({"type": "chat", "text": "   "})

            reply = ws.receive_json()
            assert reply["type"] == "error"
            assert reply["data"]["code"] == "empty_text"

    def test_missing_text_is_rejected(self, client: TestClient) -> None:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.receive_json()
            ws.send_json({"type": "chat"})
            assert ws.receive_json()["data"]["code"] == "empty_text"

    def test_many_messages_in_row(self, client: TestClient) -> None:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.receive_json()
            for i in range(20):
                ws.send_json({"type": "chat", "text": f"сообщение {i}"})
                assert ws.receive_json()["text"] == f"сообщение {i}"


class TestPing:
    """Проверка живости."""

    def test_pong(self, client: TestClient) -> None:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.receive_json()

            ws.send_json({"type": "ping", "id": "ping-1"})
            pong = ws.receive_json()

            assert pong["type"] == "pong"
            assert pong["data"]["ping_id"] == "ping-1"

    def test_multiple_pings(self, client: TestClient) -> None:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.receive_json()
            for _ in range(3):
                ws.send_json({"type": "ping"})
                assert ws.receive_json()["type"] == "pong"


class TestErrorHandling:
    """Кривые пакеты не должны рвать соединение."""

    def test_invalid_json(self, client: TestClient) -> None:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.receive_json()

            ws.send_text("{это не json")
            reply = ws.receive_json()

            assert reply["type"] == "error"
            assert "невалидный JSON" in reply["text"]

            # соединение живо
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"

    def test_unknown_type(self, client: TestClient) -> None:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.receive_json()
            ws.send_json({"type": "teleport"})

            reply = ws.receive_json()
            assert reply["type"] == "error"
            assert "неизвестный тип" in reply["text"]

    def test_missing_type(self, client: TestClient) -> None:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.receive_json()
            ws.send_json({"text": "без типа"})

            assert "'type' обязательно" in ws.receive_json()["text"]

    def test_empty_frame(self, client: TestClient) -> None:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.receive_json()
            ws.send_text("")
            assert "пустое" in ws.receive_json()["text"]

    def test_oversized_message(self, settings) -> None:
        settings.server.ws_max_message_size = 2048
        app = create_app(settings)
        with TestClient(app) as small_client:
            with small_client.websocket_connect("/ws") as ws:
                ws.receive_json()
                ws.receive_json()
                ws.send_json({"type": "chat", "text": "ы" * 3000})

                reply = ws.receive_json()
                assert reply["type"] == "error"
                assert reply["data"]["code"] == "message_too_large"

    def test_silent_types_do_not_answer(self, client: TestClient) -> None:
        """system/log/state/pong не требуют ответа — проверяем, что сокет не завис."""
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.receive_json()

            for silent in ("system", "log", "state", "pong"):
                ws.send_json({"type": silent, "text": "тихо"})

            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"


class TestStatistics:
    """Счётчики сессий должны сходиться с реальностью."""

    def test_counters_after_session(self, client: TestClient) -> None:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()   # hello
            ws.receive_json()   # system
            ws.send_json({"type": "chat", "text": "раз"})
            ws.receive_json()
            ws.send_json({"type": "ping"})
            ws.receive_json()

        stats = client.get("/api/state").json()
        assert stats["total_connections"] == 1
        assert stats["total_messages_in"] == 2      # chat + ping
        assert stats["total_messages_out"] == 4     # hello + system + echo + pong
        assert stats["errors"] == 0
        assert stats["active"] == 0

    def test_errors_counter_on_bad_packet(self, client: TestClient) -> None:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.receive_json()
            ws.send_text("мусор")
            ws.receive_json()

        # ошибка протокола — это не ошибка сокета
        assert client.get("/api/state").json()["errors"] == 0


class TestReplyHandlerPlumbing:
    """Заглушка-обработчик и его фабрика."""

    @pytest.mark.asyncio
    async def test_echo_reply_shape(self) -> None:
        message = await echo_reply("привет", {"session_id": "s1", "source": "vk"})
        assert message.type is MsgType.CHAT
        assert message.text == "привет"
        assert message.data["session_id"] == "s1"
        assert message.data["source"] == "vk"

    @pytest.mark.asyncio
    async def test_echo_reply_on_empty_text(self) -> None:
        message = await echo_reply("   ")
        assert "тишина" in message.text

    @pytest.mark.asyncio
    async def test_echo_reply_default_context(self) -> None:
        message = await echo_reply("x")
        assert message.data["source"] == "webui"
        assert message.data["session_id"] is None

    def test_build_reply_handler_returns_echo(self, settings) -> None:
        assert build_reply_handler(settings) is echo_reply

    def test_build_reply_handler_switches_to_brain(self, settings) -> None:
        """Этап 2: при включённом мозге фабрика возвращает обработчик мозга."""
        settings.features.brain_enabled = True
        settings.brain.defaults.provider = "mock"
        handler = build_reply_handler(settings)
        assert handler is not echo_reply

    @pytest.mark.asyncio
    async def test_custom_handler_is_used(self, settings) -> None:
        """Проверяем точку расширения: на этапе 2 сюда встанет мозг."""
        from lilith_core.protocol import chat_message

        async def fake_brain(text: str, context: dict) -> object:
            return chat_message(f"МОЗГ: {text.upper()}")

        app = create_app(settings)
        app.state.reply_handler = fake_brain  # type: ignore[assignment]

        with TestClient(app) as brain_client:
            with brain_client.websocket_connect("/ws") as ws:
                ws.receive_json()
                ws.receive_json()
                ws.send_json({"type": "chat", "text": "проверка"})
                reply = ws.receive_json()

        assert reply["text"] == "МОЗГ: ПРОВЕРКА"
        assert reply.get("data", {}).get("echo") is None

    @pytest.mark.asyncio
    async def test_handler_exception_becomes_error_message(self, settings) -> None:
        async def broken(text: str, context: dict) -> object:
            raise RuntimeError("мозг отвалился")

        app = create_app(settings)
        app.state.reply_handler = broken  # type: ignore[assignment]

        with TestClient(app) as broken_client:
            with broken_client.websocket_connect("/ws") as ws:
                ws.receive_json()
                ws.receive_json()
                ws.send_json({"type": "chat", "text": "ау"})
                reply = ws.receive_json()

        assert reply["type"] == "error"
        assert reply["data"]["code"] == "reply_failed"
        assert "мозг отвалился" in reply["text"]


class TestProtocolFormat:
    """Формат ответов соответствует задокументированной схеме."""

    def test_every_frame_has_id_type_ts(self, client: TestClient) -> None:
        with client.websocket_connect("/ws") as ws:
            frames = [ws.receive_json(), ws.receive_json()]
            ws.send_json({"type": "chat", "text": "x"})
            frames.append(ws.receive_json())

        for frame in frames:
            assert set(frame) >= {"id", "type", "ts"}
            assert isinstance(frame["id"], str) and frame["id"]
            assert frame["type"] in {t.value for t in MsgType}

    def test_raw_frames_are_json(self, client: TestClient) -> None:
        with client.websocket_connect("/ws") as ws:
            raw = ws.receive_text()
        assert json.loads(raw)["type"] == "hello"


class TestBrainIntegration:
    """Этап 2: мозг в mock-режиме сквозь WebSocket-шину."""

    @pytest.fixture
    def brain_app(self, settings):
        settings.features.brain_enabled = True
        settings.brain.defaults.provider = "mock"
        return create_app(settings)

    @pytest.fixture
    def brain_client(self, brain_app):
        with TestClient(brain_app) as client:
            yield client

    @staticmethod
    def _handshake(ws) -> None:
        ws.receive_json()
        ws.receive_json()

    @staticmethod
    def _final(ws):
        """Читает кадры до финального chat (частичные стрим-кадры пропускает)."""
        while True:
            frame = ws.receive_json()
            if frame["type"] in ("error",) or (
                frame["type"] == "chat" and not frame["data"].get("partial")
            ):
                return frame

    def test_brain_reply_replaces_echo(self, brain_client: TestClient) -> None:
        with brain_client.websocket_connect("/ws") as ws:
            self._handshake(ws)
            ws.send_json({"type": "chat", "text": "привет"})
            reply = self._final(ws)

        assert reply["type"] == "chat"
        assert reply["text"].startswith("мок-chat:")
        assert reply["data"].get("echo") is None
        assert reply["data"]["profile"] == "chat"
        assert reply["data"]["agent_id"] == "lilith"
        assert reply["data"]["usage"]["estimated"] is True
        assert reply["data"]["tok_per_sec"] is not None
        assert reply["data"]["stream_id"]

    def test_profile_routing_via_data(self, brain_client: TestClient) -> None:
        with brain_client.websocket_connect("/ws") as ws:
            self._handshake(ws)
            ws.send_json({"type": "chat", "text": "напиши код", "data": {"profile": "coder"}})
            reply = self._final(ws)

        assert reply["text"].startswith("мок-coder:")
        assert reply["data"]["profile"] == "coder"

    def test_unknown_profile_rejected(self, brain_client: TestClient) -> None:
        with brain_client.websocket_connect("/ws") as ws:
            self._handshake(ws)
            ws.send_json({"type": "chat", "text": "ау", "data": {"profile": "telepatia"}})
            reply = ws.receive_json()

        assert reply["type"] == "error"
        assert reply["data"]["code"] == "unknown_profile"
        assert "telepatia" in reply["text"]

    def test_stream_partial_frames_then_final(self, brain_client: TestClient) -> None:
        with brain_client.websocket_connect("/ws") as ws:
            self._handshake(ws)
            ws.send_json({"type": "chat", "text": "мур"})

            frames = []
            while True:
                frame = ws.receive_json()
                frames.append(frame)
                if frame["type"] == "chat" and not frame["data"].get("partial"):
                    break

        partials = [f for f in frames if f["data"].get("partial")]
        final = frames[-1]

        assert partials, "ожидали частичные кадры стриминга"
        stream_ids = {f["data"]["stream_id"] for f in partials} | {final["data"]["stream_id"]}
        assert len(stream_ids) == 1
        assert "".join(f["text"] for f in partials) == final["text"]

    def test_brain_error_becomes_error_frame(self, brain_app) -> None:
        brain_app.state.mock_brain.fail_on = "умри"
        with TestClient(brain_app) as client:
            with client.websocket_connect("/ws") as ws:
                self._handshake(ws)
                ws.send_json({"type": "chat", "text": "умри немедленно"})
                reply = ws.receive_json()

                assert reply["type"] == "error"
                assert reply["data"]["code"] == "brain_error"
                assert "mock-мозг упал" in reply["text"]

                # соединение живо
                ws.send_json({"type": "ping"})
                assert ws.receive_json()["type"] == "pong"

    def test_history_survives_between_messages(self, brain_client: TestClient) -> None:
        with brain_client.websocket_connect("/ws") as ws:
            self._handshake(ws)
            ws.send_json({"type": "chat", "text": "раз"})
            self._final(ws)
            ws.send_json({"type": "chat", "text": "два"})
            reply = self._final(ws)

        cycle = brain_client.app.state.chat_cycle
        key = cycle.history_key(
            {"agent_id": "lilith", "session_id": reply["data"]["session_id"], "source": "webui"}
        )
        roles = [role for role, _ in cycle.history._stores[key]]
        assert roles == ["user", "assistant", "user", "assistant"]

    def test_hello_announces_default_profile(self, brain_client: TestClient) -> None:
        with brain_client.websocket_connect("/ws") as ws:
            hello = ws.receive_json()
        assert hello["data"]["default_profile"] == "chat"
        assert hello["data"]["agent_id"] == "lilith"
