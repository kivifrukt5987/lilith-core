"""WebSocket-эндпоинты лица (этап 6): ``/ws/face/producer`` и ``/ws/group``.

Вынесены из ``app.py``, чтобы оркестратор не распух: здесь только транспорт —
приём соединения, разбор клиентских кадров (блок **A5**) и насос ответных кадров.
Вся логика живёт в :class:`~lilith_core.face.producer.FaceProducerHub` и
:class:`~lilith_core.face.group.GroupManager`.

``/ws/unity`` (заглушка этапа 5) остаётся **алиасом** продюсера (решение **A6.1-б**),
поэтому старые клиенты и тесты не ломаются.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from loguru import logger

from ..protocol import MsgType, parse_raw
from . import ws_frames as frames
from .group import GroupFull, GroupManager, GroupSession, UnknownPersona
from .producer import FaceProducerHub, ProducerClient

__all__ = [
    "MAX_CLIENT_MESSAGE",
    "handle_producer_ws",
    "handle_group_ws",
    "handle_legacy_unity_ws",
    "parse_client_frame",
]

#: Предел размера клиентского кадра продюсера (клиент шлёт только служебное).
MAX_CLIENT_MESSAGE = 65536


def parse_client_frame(raw: str | bytes) -> tuple[dict[str, Any] | None, str]:
    """Разобрать кадр клиента: ``(словарь, ошибка)``.

    Плоский JSON ``{"type": ...}``; неизвестный ``type`` — ошибка, но сокет живёт.
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if len(raw) > MAX_CLIENT_MESSAGE:
        return None, f"кадр больше {MAX_CLIENT_MESSAGE} байт"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"не JSON: {exc}"
    if not isinstance(payload, dict):
        return None, f"ожидался JSON-объект, получен {type(payload).__name__}"
    kind = payload.get("type")
    if not isinstance(kind, str) or not kind.strip():
        return None, "поле 'type' обязательно и должно быть строкой"
    kind = kind.strip().lower()
    if kind not in frames.CLIENT_TYPES:
        return None, f"неизвестный тип '{kind}' (допустимые: {', '.join(frames.CLIENT_TYPES)})"
    payload["type"] = kind
    return payload, ""


async def handle_producer_ws(websocket: WebSocket, app: Any) -> None:
    """Обслужить одно подключение ``/ws/face/producer`` от приёма до закрытия."""
    hub: FaceProducerHub = app.state.face_producer
    await websocket.accept()
    client = await hub.register(websocket)

    try:
        while True:
            raw = await websocket.receive_text()
            frame, error = parse_client_frame(raw)
            if frame is None:
                await client.send(frames.error_frame(error, code="bad_frame"))
                continue
            await _dispatch_client_frame(frame, client=client, hub=hub, app=app)
    except WebSocketDisconnect:
        logger.info("Продюсер[{}]: клиент отключился", client.id)
    except asyncio.CancelledError:  # pragma: no cover - штатная отмена
        raise
    except Exception as exc:  # noqa: BLE001 - сокет умирает по-разному
        logger.exception("Продюсер[{}]: ошибка соединения: {!r}", client.id, exc)
    finally:
        await hub.unregister(client)


async def _dispatch_client_frame(
    frame: dict[str, Any], *, client: ProducerClient, hub: FaceProducerHub, app: Any
) -> None:
    """Разобрать один клиентский кадр продюсера (A5)."""
    kind = frame.get("type", "")

    if kind == "hello":
        client.want_server_visemes = bool(frame.get("want_server_visemes", False))
        client.client = str(frame.get("client") or "")
        await client.send(hub.hello_frame())
        await client.send(frames.state_frame(**hub.describe()["producer"]))
        return

    if kind == "ready":
        hub.mark_ready(client, True)
        logger.info("Продюсер[{}]: ready (клиент '{}')", client.id, client.client or "?")
        await hub.send_state()
        return

    if kind == "persona_request":
        persona_id = str(frame.get("id") or "").strip()
        if not persona_id:
            await client.send(frames.error_frame("нужен id персоны", code="missing_persona"))
            return
        result = await hub.swap_persona(persona_id, reason="client_request")
        if result.get("type") == "error":
            await client.send(result)
        return

    if kind == "speak":
        text = str(frame.get("text") or "").strip()
        if not text:
            await client.send(frames.error_frame("пустой текст озвучки", code="empty_text"))
            return
        await hub.speak(
            text,
            voice_core=app.state.voice,
            persona_id=str(frame.get("persona") or ""),
            voice_profile=frame.get("profile"),
        )
        return

    if kind == "stats":
        hub.update_stats(
            client,
            {
                "fps": frame.get("fps"),
                "dropped": frame.get("dropped"),
                "queued_ms": frame.get("queued_ms"),
                "playing": frame.get("playing"),
                "viseme": frame.get("viseme"),
                "emotion": frame.get("emotion"),
            },
        )
        return

    if kind == "ping":
        await client.send(frames.pong_frame(str(frame.get("id") or "") or None))


async def handle_group_ws(websocket: WebSocket, app: Any) -> None:
    """Обслужить подключение ``/ws/group`` (E3: один сокет на группу)."""
    manager: GroupManager = app.state.face_groups
    hub: FaceProducerHub = app.state.face_producer
    await websocket.accept()

    group_name = "main"
    query = getattr(websocket, "query_params", None)
    if query is not None and query.get("group"):
        group_name = str(query.get("group"))

    try:
        session = manager.ensure(group_name)
    except UnknownPersona as exc:
        await websocket.send_text(_dumps(frames.error_frame(str(exc), code="unknown_persona")))
        await websocket.close()
        return

    queue = session.subscribe()
    await websocket.send_text(
        _dumps(
            {
                "type": "hello-group",
                "producer": frames.PRODUCER_NAME,
                "protocol": frames.PRODUCER_PROTOCOL,
                "sample_rate": hub.sample_rate,
                "chunk_bytes": hub.chunk_bytes,
                "format": frames.AUDIO_FORMAT,
                **session.layout(),
            }
        )
    )

    async def pump() -> None:
        """Насос кадров группы в сокет."""
        while True:
            frame = await queue.get()
            await websocket.send_text(_dumps(frame))

    pump_task = asyncio.create_task(pump())
    try:
        while True:
            raw = await websocket.receive_text()
            frame, error = parse_client_frame(raw)
            if frame is None:
                await websocket.send_text(_dumps(frames.error_frame(error, code="bad_frame")))
                continue
            await _dispatch_group_frame(frame, session=session, manager=manager, hub=hub, app=app, socket=websocket)
    except WebSocketDisconnect:
        logger.info("Группа '{}': клиент отключился", session.name)
    except asyncio.CancelledError:  # pragma: no cover - штатная отмена
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Группа '{}': ошибка соединения: {!r}", session.name, exc)
    finally:
        pump_task.cancel()
        session.unsubscribe(queue)


async def _dispatch_group_frame(
    frame: dict[str, Any],
    *,
    session: GroupSession,
    manager: GroupManager,
    hub: FaceProducerHub,
    app: Any,
    socket: WebSocket,
) -> None:
    """Разобрать клиентский кадр групповой сцены."""
    kind = frame.get("type", "")

    if kind == "hello":
        await socket.send_text(_dumps({"type": "hello-group", **session.layout()}))
        return

    if kind == "ready":
        await session.publish(frames.state_frame(**session.layout()))
        return

    if kind == "persona_request":
        persona_id = str(frame.get("id") or "").strip()
        persona = manager.registry.get(persona_id)
        if persona is None:
            await socket.send_text(_dumps(frames.error_frame(f"нет персоны '{persona_id}'", code="unknown_persona")))
            return
        try:
            member = session.join(persona, slot=frame.get("slot"))
        except GroupFull as exc:
            await socket.send_text(_dumps(frames.error_frame(str(exc), code="group_full")))
            return
        await session.publish(frames.persona_frame(persona.id, voice=persona.voice_spec.as_dict(), reason="join"))
        await session.publish({"type": "layout", **session.layout(), "joined": member.as_dict()})
        return

    if kind == "speak":
        text = str(frame.get("text") or "").strip()
        if not text:
            await socket.send_text(_dumps(frames.error_frame("пустой текст озвучки", code="empty_text")))
            return
        # E5: говорит только фокусная персона — «хор» запрещён оркестратором.
        speaker = str(frame.get("persona") or session.focus)
        if speaker and not session.has(speaker):
            await socket.send_text(_dumps(frames.error_frame(f"'{speaker}' не в группе", code="not_a_member")))
            return
        if session.focus and speaker and speaker != session.focus:
            await session.publish(
                frames.error_frame(
                    f"говорит только фокусная персона ('{session.focus}'), запрошена '{speaker}'",
                    code="not_focused",
                )
            )
            return
        if speaker:
            await manager.set_focus(session.name, speaker)
        await session.publish(frames.focus_frame(speaker, group=session.name))
        await hub.speak(text, voice_core=app.state.voice, persona_id=speaker)
        return

    if kind == "stats":
        session.stats = {"fps": frame.get("fps"), "dropped": frame.get("dropped")}
        return

    if kind == "ping":
        await socket.send_text(_dumps(frames.pong_frame(str(frame.get("id") or "") or None)))


async def handle_legacy_unity_ws(websocket: WebSocket, app: Any) -> None:
    """``/ws/unity`` — алиас продюсера (A6.1-б) + зеркало старой шины лица.

    Старый контракт этапа 5 (``hello{adapter:"unity-vrm-salsa"}`` и ``face{...}``
    из :class:`~lilith_core.face.bus.FaceBus`) сохранён, чтобы не сломать
    существующие тесты и внешних подписчиков; сверху добавлен полный протокол
    продюсера.
    """
    hub: FaceProducerHub = app.state.face_producer
    await websocket.accept()
    # Сначала кадр этапа 5 (тесты и старые клиенты читают именно его), затем hello продюсера.
    await websocket.send_text(
        _dumps(
            {
                "type": "hello",
                "adapter": "unity-vrm-salsa",
                "status": "reserved",
                "alias_of": "/ws/face/producer",
                "note": "этап 6: сокет — алиас продюсера, следующим кадром придёт его hello",
            }
        )
    )
    client = await hub.register(websocket, client_id="unity")

    bus = getattr(app.state, "face_bus", None)
    queue = bus.subscribe() if bus is not None else None

    async def pump_legacy() -> None:
        """Зеркалить кадры шины лица этапа 5 (face{kind:...})."""
        if queue is None:
            return
        while True:
            legacy = await queue.get()
            await websocket.send_text(_dumps({"type": "face", **legacy}))

    pump_task = asyncio.create_task(pump_legacy())
    try:
        while True:
            raw = await websocket.receive_text()
            frame, error = parse_client_frame(raw)
            if frame is None:
                # Обратная совместимость: старые клиенты шлют конверт {type:"face",...}.
                parsed = parse_raw(raw)
                if parsed.ok and parsed.message is not None and parsed.message.type is MsgType.FACE:
                    data = parsed.message.data or {}
                    if data.get("kind") == "emotion" and app.state.face is not None:
                        await app.state.face.bridge.set_emotion(str(data.get("name") or ""))
                else:
                    await client.send(frames.error_frame(error, code="bad_frame"))
                continue
            await _dispatch_client_frame(frame, client=client, hub=hub, app=app)
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:  # pragma: no cover - штатная отмена
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("/ws/unity: ошибка соединения: {!r}", exc)
    finally:
        pump_task.cancel()
        if bus is not None and queue is not None:
            bus.unsubscribe(queue)
        await hub.unregister(client)


def _dumps(frame: dict[str, Any]) -> str:
    """JSON кадра: кириллица читаема, компактно."""
    return json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
