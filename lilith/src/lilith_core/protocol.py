"""Схема сообщений WebSocket-шины LILITH-CORE.

Клиент (веб-панель, мосты Discord/VK, отладочные скрипты) и сервер обмениваются
JSON-объектами одного формата::

    {
        "id":   "уникальный id сообщения",
        "type": "chat",                  # см. MsgType
        "text": "привет",                # опционально
        "data": { ... },                 # опциональный произвольный payload
        "ts":   "2026-09-16T19:38:00+03:00"
    }

Такая схема намеренно простая: на этапе 2 обработчик ``chat`` начнёт ходить в
мозг, на этапе 6 появятся типы ``tool_call`` / ``confirm`` — протокол при этом
меняться не будет.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

__all__ = [
    "MsgType",
    "Message",
    "ParsedMessage",
    "now_iso",
    "new_id",
    "build_message",
    "chat_message",
    "system_message",
    "error_message",
    "pong_message",
    "hello_message",
    "parse_raw",
]


class MsgType(str, Enum):
    """Известные типы сообщений шины."""

    HELLO = "hello"        #: рукопожатие (в обе стороны)
    CHAT = "chat"          #: реплика пользователя / ответ персонажа
    PING = "ping"          #: проверка живости
    PONG = "pong"          #: ответ на ping
    SYSTEM = "system"      #: служебное сообщение
    LOG = "log"            #: строка лога для панели
    ERROR = "error"        #: ошибка
    STATE = "state"        #: состояние сервера (этап 6: подтверждения инструментов)
    FACE = "face"          #: кадры лица: emotion / viseme / audio / done (этап 5 v2)
    VOICE = "voice"        #: запрос озвучки: клиент просит сказать текст (этап 5 v2)
    PERSONA = "persona"    #: своп активной персоны: {"type":"persona","data":{"id":...}} (этап 6)

    @classmethod
    def values(cls) -> list[str]:
        """Список строковых значений — удобно для документации и валидации."""
        return [item.value for item in cls]


def now_iso() -> str:
    """Текущее время в ISO-8601 с локальным смещением."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_id() -> str:
    """Короткий уникальный идентификатор сообщения."""
    return uuid.uuid4().hex[:12]


@dataclass(slots=True)
class Message:
    """Сообщение шины."""

    type: MsgType
    text: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=new_id)
    ts: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Сериализация в словарь для JSON."""
        payload: dict[str, Any] = {
            "id": self.id,
            "type": self.type.value if isinstance(self.type, MsgType) else str(self.type),
            "ts": self.ts,
        }
        if self.text is not None:
            payload["text"] = self.text
        if self.data:
            payload["data"] = self.data
        return payload

    def to_json(self) -> str:
        """Сериализация в JSON-строку (то, что уходит в сокет)."""
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass(slots=True)
class ParsedMessage:
    """Результат разбора входящей строки: либо сообщение, либо ошибка."""

    ok: bool
    message: Message | None = None
    error: str | None = None


# --------------------------------------------------------------------------- #
#  Фабрики сообщений
# --------------------------------------------------------------------------- #
def build_message(
    msg_type: MsgType | str,
    text: str | None = None,
    data: dict[str, Any] | None = None,
    *,
    id: str | None = None,  # noqa: A002 - совпадает с именем поля протокола
    ts: str | None = None,
) -> Message:
    """Собирает :class:`Message` из произвольного типа."""
    if isinstance(msg_type, MsgType):
        resolved = msg_type
    else:
        resolved = MsgType(str(msg_type).strip().lower())
    return Message(
        type=resolved,
        text=text,
        data=dict(data or {}),
        id=id or new_id(),
        ts=ts or now_iso(),
    )


def hello_message(
    *,
    name: str = "LILITH-CORE",
    version: str = "0.0.0",
    session_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Message:
    """Приветственное сообщение сервера."""
    data: dict[str, Any] = {"name": name, "version": version}
    if session_id:
        data["session_id"] = session_id
    if extra:
        data.update(extra)
    return build_message(MsgType.HELLO, text="connection established", data=data)


def chat_message(text: str, **data: Any) -> Message:
    """Чат-сообщение."""
    return build_message(MsgType.CHAT, text=text, data=data or None)


def system_message(text: str, **data: Any) -> Message:
    """Служебное сообщение (например, «brain подключён»)."""
    return build_message(MsgType.SYSTEM, text=text, data=data or None)


def error_message(text: str, code: str = "error", **data: Any) -> Message:
    """Сообщение об ошибке."""
    payload = {"code": code, **data}
    return build_message(MsgType.ERROR, text=text, data=payload)


def pong_message(ping_id: str | None = None, latency_ms: float | None = None) -> Message:
    """Ответ на ping, при желании — с измеренной задержкой."""
    data: dict[str, Any] = {}
    if ping_id:
        data["ping_id"] = ping_id
    if latency_ms is not None:
        data["latency_ms"] = round(latency_ms, 3)
    return build_message(MsgType.PONG, data=data or None)


# --------------------------------------------------------------------------- #
#  Разбор входящих сообщений
# --------------------------------------------------------------------------- #
def parse_raw(raw: str | bytes) -> ParsedMessage:
    """Разбирает входящую строку/байты в :class:`Message`.

    Никогда не бросает исключений: любая проблема возвращается как
    ``ParsedMessage(ok=False, error=...)``, чтобы один кривой пакет не рвал сокет.
    """
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            return ParsedMessage(ok=False, error=f"не utf-8: {exc}")

    raw = raw.strip()
    if not raw:
        return ParsedMessage(ok=False, error="пустое сообщение")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return ParsedMessage(ok=False, error=f"невалидный JSON: {exc.msg} (позиция {exc.pos})")

    if not isinstance(payload, dict):
        return ParsedMessage(ok=False, error=f"ожидался JSON-объект, получено {type(payload).__name__}")

    raw_type = payload.get("type")
    if not isinstance(raw_type, str) or not raw_type.strip():
        return ParsedMessage(ok=False, error="поле 'type' обязательно и должно быть строкой")

    try:
        msg_type = MsgType(raw_type.strip().lower())
    except ValueError:
        allowed = ", ".join(MsgType.values())
        return ParsedMessage(ok=False, error=f"неизвестный тип '{raw_type}' (допустимые: {allowed})")

    text = payload.get("text")
    if text is not None and not isinstance(text, str):
        text = str(text)

    data = payload.get("data")
    if data is not None and not isinstance(data, dict):
        data = {"value": data}

    message = Message(
        type=msg_type,
        text=text,
        data=data or {},
        id=str(payload.get("id") or new_id()),
        ts=str(payload.get("ts") or now_iso()),
    )
    return ParsedMessage(ok=True, message=message)
