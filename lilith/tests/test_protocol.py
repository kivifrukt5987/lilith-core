"""Тесты протокола сообщений шины."""

from __future__ import annotations

import json

import pytest

from lilith_core.protocol import (
    Message,
    MsgType,
    build_message,
    chat_message,
    error_message,
    hello_message,
    new_id,
    now_iso,
    parse_raw,
    pong_message,
    system_message,
)


class TestFactories:
    """Фабрики сообщений."""

    def test_new_id_unique_and_short(self) -> None:
        ids = {new_id() for _ in range(500)}
        assert len(ids) == 500
        assert all(len(i) == 12 for i in ids)

    def test_now_iso_parsable(self) -> None:
        from datetime import datetime

        assert datetime.fromisoformat(now_iso()) is not None

    def test_chat_message_roundtrip(self) -> None:
        msg = chat_message("привет, Лиль")
        restored = parse_raw(msg.to_json())

        assert restored.ok is True
        assert restored.message is not None
        assert restored.message.type is MsgType.CHAT
        assert restored.message.text == "привет, Лиль"

    def test_cyrillic_not_escaped(self) -> None:
        raw = chat_message("мур-мур 🦇").to_json()
        assert "мур-мур" in raw
        assert "\\u" not in raw

    def test_error_message_carries_code(self) -> None:
        msg = error_message("сломалось", code="boom", detail=42)
        assert msg.data["code"] == "boom"
        assert msg.data["detail"] == 42

    def test_system_message(self) -> None:
        assert system_message("старт").type is MsgType.SYSTEM

    def test_hello_message_fields(self) -> None:
        msg = hello_message(name="X", version="1.2.3", session_id="abc", extra={"mode": "echo"})
        assert msg.data == {"name": "X", "version": "1.2.3", "session_id": "abc", "mode": "echo"}

    def test_pong_message(self) -> None:
        msg = pong_message("ping-1", latency_ms=1.23456)
        assert msg.data["ping_id"] == "ping-1"
        assert msg.data["latency_ms"] == 1.235

    def test_build_message_accepts_string_type(self) -> None:
        assert build_message("CHAT", text="x").type is MsgType.CHAT

    def test_build_message_unknown_type_raises(self) -> None:
        with pytest.raises(ValueError):
            build_message("teleport")

    def test_to_dict_omits_empty_fields(self) -> None:
        payload = Message(type=MsgType.PING).to_dict()
        assert "text" not in payload
        assert "data" not in payload
        assert payload["type"] == "ping"

    def test_message_is_mutable_dataclass(self) -> None:
        msg = chat_message("a")
        msg.text = "b"
        assert msg.to_dict()["text"] == "b"


class TestParse:
    """Разбор входящих пакетов: никакая дичь не должна ронять сервер."""

    def test_valid_full_message(self) -> None:
        raw = json.dumps({"id": "x1", "type": "chat", "text": "хай", "data": {"source": "discord"}, "ts": "t"})
        parsed = parse_raw(raw)

        assert parsed.ok
        assert parsed.message is not None
        assert parsed.message.id == "x1"
        assert parsed.message.text == "хай"
        assert parsed.message.data["source"] == "discord"
        assert parsed.message.ts == "t"

    def test_minimal_message_gets_generated_id(self) -> None:
        parsed = parse_raw('{"type":"ping"}')
        assert parsed.ok
        assert parsed.message is not None
        assert len(parsed.message.id) == 12

    def test_type_case_insensitive_and_trimmed(self) -> None:
        assert parse_raw('{"type":" CHAT ","text":"x"}').message.type is MsgType.CHAT

    def test_bytes_input(self) -> None:
        parsed = parse_raw('{"type":"chat","text":"байты"}'.encode("utf-8"))
        assert parsed.ok
        assert parsed.message.text == "байты"

    def test_invalid_utf8_bytes(self) -> None:
        parsed = parse_raw(b"\xff\xfe\x00bad")
        assert not parsed.ok
        assert "не utf-8" in parsed.error

    @pytest.mark.parametrize(
        "raw",
        ["", "   ", "\n\t "],
    )
    def test_empty_input(self, raw: str) -> None:
        parsed = parse_raw(raw)
        assert not parsed.ok
        assert "пустое" in parsed.error

    @pytest.mark.parametrize(
        "raw",
        ["не json", "{", '{"type":}', "[1,2,3]"],
    )
    def test_malformed_json(self, raw: str) -> None:
        parsed = parse_raw(raw)
        assert not parsed.ok
        assert parsed.message is None

    def test_non_object_json(self) -> None:
        parsed = parse_raw('{"type":"chat"}')
        assert parsed.ok
        parsed2 = parse_raw("123")
        assert not parsed2.ok
        assert "JSON-объект" in parsed2.error

    def test_missing_type(self) -> None:
        parsed = parse_raw('{"text":"без типа"}')
        assert not parsed.ok
        assert "'type' обязательно" in parsed.error

    def test_non_string_type(self) -> None:
        assert not parse_raw('{"type":42}').ok

    def test_unknown_type_lists_allowed(self) -> None:
        parsed = parse_raw('{"type":"teleport"}')
        assert not parsed.ok
        assert "chat" in parsed.error
        assert "ping" in parsed.error

    def test_non_string_text_coerced(self) -> None:
        parsed = parse_raw('{"type":"chat","text":123}')
        assert parsed.ok
        assert parsed.message.text == "123"

    def test_non_dict_data_wrapped(self) -> None:
        parsed = parse_raw('{"type":"chat","text":"x","data":"строка"}')
        assert parsed.ok
        assert parsed.message.data == {"value": "строка"}

    def test_null_text_allowed(self) -> None:
        parsed = parse_raw('{"type":"ping","text":null}')
        assert parsed.ok
        assert parsed.message.text is None


class TestMsgType:
    """Перечисление типов."""

    def test_values(self) -> None:
        values = MsgType.values()
        for expected in ("hello", "chat", "ping", "pong", "system", "log", "error", "state"):
            assert expected in values

    def test_is_str_enum(self) -> None:
        assert MsgType.CHAT == "chat"
