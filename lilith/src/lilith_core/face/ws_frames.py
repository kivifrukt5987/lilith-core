"""Протокол лица для внешних потребителей (этап 6): плоские WS-кадры.

Решение архитектора **A4-а**: на ``/ws/face/producer`` и ``/ws/group`` ходят
**плоские** JSON-кадры ``{"type": ..., ...}`` — без общего конверта
``{id, type, text, data, ts}`` из :mod:`lilith_core.protocol`. Так C#-клиенту
не нужно разворачивать вложенность, а мусорные поля не едят трафик на каждом
из 12 кадров в секунду.

Основная шина ``/ws`` при этом **сохраняет** конверт и продолжает стримить
``face{kind:audio|viseme|done}`` в веб-панель (решение **A6.2**).

Направления (решение **A5**, двунаправленный сокет):

* **сервер → клиент:** ``hello``, ``audio``, ``viseme``, ``emotion``, ``persona``,
  ``focus``, ``stop``, ``done``, ``state``, ``error``, ``pong``;
* **клиент → сервер:** ``hello``, ``ready``, ``persona_request``, ``speak``,
  ``stats`` (раз в 5 с), ``ping``.
"""

from __future__ import annotations

import base64
from typing import Any

from ..voice.pcm import AudioChunk

__all__ = [
    "PRODUCER_NAME",
    "PRODUCER_PROTOCOL",
    "AUDIO_FORMAT",
    "SERVER_TYPES",
    "CLIENT_TYPES",
    "audio_frame",
    "done_frame",
    "emotion_frame",
    "error_frame",
    "focus_frame",
    "hello_frame",
    "persona_frame",
    "pong_frame",
    "state_frame",
    "stop_frame",
    "viseme_frame",
]

#: Имя продюсера в кадре ``hello``.
PRODUCER_NAME = "lilith-face"

#: Версия контракта продюсера (поднимается при несовместимых изменениях).
PRODUCER_PROTOCOL = "1.0"

#: Формат аудио (A1-а): raw PCM int16 mono little-endian.
AUDIO_FORMAT = "pcm_s16le"

#: Типы кадров, которые шлёт сервер.
SERVER_TYPES: tuple[str, ...] = (
    "hello",
    "audio",
    "viseme",
    "emotion",
    "persona",
    "focus",
    "stop",
    "done",
    "state",
    "error",
    "pong",
)

#: Типы кадров, которые сервер принимает от клиента.
CLIENT_TYPES: tuple[str, ...] = (
    "hello",
    "ready",
    "persona_request",
    "speak",
    "stats",
    "ping",
)


def hello_frame(
    *,
    sample_rate: int,
    chunk_bytes: int,
    persona: str,
    server_version: str,
    personas: list[str] | None = None,
    format: str = AUDIO_FORMAT,  # noqa: A002 - имя поля зафиксировано контрактом
) -> dict[str, Any]:
    """Рукопожатие сервер → клиент: всё, что нужно для инициализации плеера."""
    frame: dict[str, Any] = {
        "type": "hello",
        "producer": PRODUCER_NAME,
        "protocol": PRODUCER_PROTOCOL,
        "sample_rate": sample_rate,
        "format": format,
        "chunk_bytes": chunk_bytes,
        "persona": persona,
        "server_version": server_version,
    }
    if personas is not None:
        frame["personas"] = list(personas)
    return frame


def audio_frame(
    chunk: AudioChunk,
    *,
    utterance_id: str,
    persona: str | None = None,
) -> dict[str, Any]:
    """Кадр аудио: ровно ``chunk_bytes`` raw PCM в base64 (A1-а, A3.2)."""
    frame: dict[str, Any] = {
        "type": "audio",
        "data": base64.b64encode(chunk.data).decode("ascii"),
        "bytes": len(chunk.data),
        "seq": chunk.seq,
        "offset_ms": chunk.offset_ms,
        "utterance_id": utterance_id,
        "sample_rate": chunk.sample_rate,
        "final": chunk.final,
    }
    if persona is not None:
        frame["persona"] = persona
    return frame


def viseme_frame(
    code: str,
    intensity: float,
    *,
    offset_ms: int,
    utterance_id: str,
    seq: int = 0,
    persona: str | None = None,
) -> dict[str, Any]:
    """Серверная разметка визем (A3.1: только если клиент попросил в ``hello``)."""
    frame: dict[str, Any] = {
        "type": "viseme",
        "code": code,
        "intensity": round(float(intensity), 3),
        "offset_ms": offset_ms,
        "utterance_id": utterance_id,
        "seq": seq,
    }
    if persona is not None:
        frame["persona"] = persona
    return frame


def emotion_frame(
    tag: str,
    *,
    intensity: float = 1.0,
    ttl_ms: int = 4000,
    utterance_id: str = "",
    persona: str | None = None,
) -> dict[str, Any]:
    """Эмоция: тег + интенсивность + время жизни (A3.3: сброс по ``ttl_ms`` в Unity)."""
    frame: dict[str, Any] = {
        "type": "emotion",
        "tag": tag,
        "intensity": round(float(intensity), 3),
        "ttl_ms": int(ttl_ms),
    }
    if utterance_id:
        frame["utterance_id"] = utterance_id
    if persona is not None:
        frame["persona"] = persona
    return frame


def persona_frame(
    persona_id: str,
    *,
    vrm: str | None = None,
    voice: dict[str, Any] | None = None,
    card: dict[str, Any] | None = None,
    face: dict[str, Any] | None = None,
    swap: bool = True,
    reason: str = "swap",
) -> dict[str, Any]:
    """Смена персоны: Unity грузит ``vrm``, горло переключает голос (D9)."""
    frame: dict[str, Any] = {"type": "persona", "id": persona_id, "swap": swap, "reason": reason}
    if vrm is not None:
        frame["vrm"] = vrm
    if voice is not None:
        frame["voice"] = voice
    if card is not None:
        frame["card"] = card
    if face is not None:
        frame["face"] = face
    return frame


def focus_frame(persona_id: str, *, group: str = "", reason: str = "speaking") -> dict[str, Any]:
    """Фокус говорящей: Unity переводит камеру на персону (E2-а)."""
    frame: dict[str, Any] = {"type": "focus", "persona": persona_id, "reason": reason}
    if group:
        frame["group"] = group
    return frame


def stop_frame(utterance_id: str, *, reason: str = "barge_in", persona: str | None = None) -> dict[str, Any]:
    """Прервать реплику: клиент обязан дропнуть чанки с этим ``utterance_id`` (A3.4)."""
    frame: dict[str, Any] = {"type": "stop", "utterance_id": utterance_id, "reason": reason}
    if persona is not None:
        frame["persona"] = persona
    return frame


def done_frame(utterance_id: str, *, reason: str = "eof", chunks: int = 0, persona: str | None = None) -> dict[str, Any]:
    """Конец реплики: больше аудио с этим ``utterance_id`` не будет."""
    frame: dict[str, Any] = {"type": "done", "utterance_id": utterance_id, "reason": reason}
    if chunks:
        frame["chunks"] = chunks
    if persona is not None:
        frame["persona"] = persona
    return frame


def state_frame(**fields: Any) -> dict[str, Any]:
    """Служебное состояние сервера (подписчики, активная персона, режимы)."""
    return {"type": "state", **fields}


def error_frame(detail: str, *, code: str = "error", **fields: Any) -> dict[str, Any]:
    """Ошибка продюсера: соединение при этом живёт."""
    return {"type": "error", "code": code, "detail": detail, **fields}


def pong_frame(ping_id: str | None = None) -> dict[str, Any]:
    """Ответ на клиентский ``ping``."""
    frame: dict[str, Any] = {"type": "pong"}
    if ping_id:
        frame["ping_id"] = ping_id
    return frame
