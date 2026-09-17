"""Тесты заглушки-обработчика реплик (этап 1)."""

from __future__ import annotations

import pytest

from lilith_core.echo import build_reply_handler, echo_reply
from lilith_core.protocol import MsgType


class TestEchoReply:
    """Поведение эха."""

    @pytest.mark.asyncio
    async def test_returns_same_text(self) -> None:
        message = await echo_reply("привет")
        assert message.type is MsgType.CHAT
        assert message.text == "привет"

    @pytest.mark.asyncio
    async def test_marks_itself_as_echo(self) -> None:
        data = (await echo_reply("x")).data
        assert data["echo"] is True
        assert data["mode"] == "echo"
        assert data["role"] == "assistant"
        assert "Этап 1" in data["note"]

    @pytest.mark.asyncio
    async def test_latency_measured(self) -> None:
        data = (await echo_reply("x")).data
        assert isinstance(data["latency_ms"], float)
        assert data["latency_ms"] >= 0.0

    @pytest.mark.asyncio
    async def test_empty_text_gets_caring_reply(self) -> None:
        message = await echo_reply("   ")
        assert message.text.strip()
        assert message.text != ""
        assert "🦇" in message.text

    @pytest.mark.asyncio
    async def test_none_text_does_not_crash(self) -> None:
        message = await echo_reply(None)  # type: ignore[arg-type]
        assert isinstance(message.text, str)

    @pytest.mark.asyncio
    async def test_context_defaults(self) -> None:
        data = (await echo_reply("x")).data
        assert data["source"] == "webui"
        assert data["session_id"] is None

    @pytest.mark.asyncio
    async def test_context_forwarded(self) -> None:
        data = (await echo_reply("x", {"source": "twitch", "session_id": "abc"})).data
        assert data["source"] == "twitch"
        assert data["session_id"] == "abc"

    @pytest.mark.asyncio
    async def test_serializable(self) -> None:
        import json

        message = await echo_reply("проверка сериализации")
        payload = json.loads(message.to_json())
        assert payload["text"] == "проверка сериализации"


class TestBuildReplyHandler:
    """Фабрика обработчика — точка расширения под этап 2."""

    def test_returns_echo_on_stage1(self, settings) -> None:
        assert build_reply_handler(settings) is echo_reply

    def test_returns_brain_handler_when_brain_flag_on(self, settings) -> None:
        """Этап 2: флаг brain_enabled переключает фабрику на мозг (mock-провайдер)."""
        settings.features.brain_enabled = True
        settings.brain.defaults.provider = "mock"
        assert build_reply_handler(settings) is not echo_reply

    @pytest.mark.asyncio
    async def test_handler_is_awaitable_callable(self, settings) -> None:
        handler = build_reply_handler(settings)
        message = await handler("ау", {"session_id": "s"})
        assert message.text == "ау"
