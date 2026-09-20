"""Продюсер лица (этап 6): один источник речи — много внешних потребителей.

``/ws/face/producer`` — сокет для Unity-клиента (и любого другого внешнего лица:
второй монитор, OBS-оверлей, телефон). Он **не** заменяет основную шину ``/ws``:
веб-панель по-прежнему получает ``face{kind:audio|viseme|done}`` в общем конверте
(решение **A6.2**), а продюсер шлёт плоские кадры из :mod:`lilith_core.face.ws_frames`.

:class:`FaceProducerHub` владеет:

* реестром подключений (каждое — со своим ``want_server_visemes``, **A3.1**);
* конвейером реплики: TTS → raw PCM 24 kHz → чанки по 2048 байт → ``audio``-кадры;
* прерыванием (``stop``-кадр + отмена задачи, **A3.4**);
* глобальным свопом персоны (кадр ``persona``, **D9**) и фокусом (**E2-а**).

За пределами песочницы хаб ничем не отличается от боевого: TTS-бэкенд выбирается
тем же ``VoiceCore``, поэтому ``--mock-brain --with-voice`` прогоняет весь тракт.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket
from loguru import logger

from ..voice import VoiceCore, read_wav
from ..voice.pcm import CHUNK_BYTES, DEFAULT_SAMPLE_RATE, PcmChunker, resample_pcm16
from . import ws_frames as frames
from .lora import PersonaLoraManager
from .personas import Persona, PersonaRegistry
from .visemes import WINDOW_MS, extract_visemes

__all__ = ["ProducerClient", "FaceProducerHub", "Utterance"]


@dataclass(slots=True)
class ProducerClient:
    """Одно подключение продюсера.

    :param id: короткая метка для логов.
    :param want_server_visemes: клиент попросил серверную разметку визем (**A3.1**).
    :param client: имя/версия клиента из его ``hello`` (``unity/0.1``).
    :param ready: клиент прислал ``ready`` — можно лить аудио.
    :param stats: последний кадр ``stats`` от клиента (fps, dropped), **A5**.
    """

    id: str
    socket: WebSocket
    want_server_visemes: bool = False
    client: str = ""
    #: По умолчанию готовы принимать аудио: ``ready`` — это сигнал клиента
    #: «я инициализировался», а не разрешение на вещание (иначе первый же
    #: ответ ушёл бы в никуда, пока Unity грузит сцену).
    ready: bool = True
    stats: dict[str, Any] = field(default_factory=dict)

    async def send(self, frame: dict[str, Any]) -> bool:
        """Отправить кадр; ``False``, если сокет умер (клиент будет снят с реестра)."""
        try:
            await self.socket.send_text(_dumps(frame))
            return True
        except Exception as exc:  # noqa: BLE001 - сокет мог закрыться в любой момент
            logger.debug("Продюсер[{}]: не отправить кадр {}: {!r}", self.id, frame.get("type"), exc)
            return False


def _dumps(frame: dict[str, Any]) -> str:
    """JSON кадра: кириллица читаема, без лишних пробелов (трафик на каждом кадре)."""
    import json

    return json.dumps(frame, ensure_ascii=False, separators=(",", ":"))


@dataclass(slots=True)
class Utterance:
    """Реплика в полёте: нужна, чтобы её можно было прервать (``stop``)."""

    id: str
    text: str
    persona: str
    task: asyncio.Task | None = None
    chunks: int = 0
    stopped: bool = False


class FaceProducerHub:
    """Хаб продюсера: подключения + конвейер реплик + своп персоны."""

    def __init__(
        self,
        *,
        registry: PersonaRegistry,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        chunk_bytes: int = CHUNK_BYTES,
        server_version: str = "",
        lora: PersonaLoraManager | None = None,
    ) -> None:
        self.registry = registry
        self.sample_rate = sample_rate
        self.chunk_bytes = chunk_bytes
        self.server_version = server_version
        self.lora = lora
        self._clients: dict[str, ProducerClient] = {}
        self._utterances: dict[str, Utterance] = {}
        self._lock = asyncio.Lock()

    # -- подключения ------------------------------------------------------------ #
    @property
    def clients(self) -> int:
        """Сколько продюсеров подключено."""
        return len(self._clients)

    def client_ids(self) -> list[str]:
        """Метки подключений (для ``state``)."""
        return sorted(self._clients)

    async def register(self, socket: WebSocket, *, client_id: str = "") -> ProducerClient:
        """Зарегистрировать подключение и отправить ``hello``."""
        cid = client_id or uuid.uuid4().hex[:8]
        client = ProducerClient(id=cid, socket=socket)
        self._clients[cid] = client
        await client.send(self.hello_frame())
        logger.info("Продюсер[{}]: подключён (всего {})", cid, self.clients)
        return client

    async def unregister(self, client: ProducerClient) -> None:
        """Снять подключение с реестра."""
        if self._clients.pop(client.id, None) is not None:
            logger.info("Продюсер[{}]: отключён (осталось {})", client.id, self.clients)

    def hello_frame(self) -> dict[str, Any]:
        """``hello`` сервера: частота, формат, размер чанка, активная персона (A3)."""
        return frames.hello_frame(
            sample_rate=self.sample_rate,
            chunk_bytes=self.chunk_bytes,
            persona=self.registry.active,
            server_version=self.server_version,
            personas=self.registry.ids(),
        )

    async def broadcast(self, frame: dict[str, Any], *, persona: str | None = None) -> int:
        """Разослать кадр всем; мёртвые сокеты снимаются с реестра."""
        delivered = 0
        for client in list(self._clients.values()):
            if await client.send(frame):
                delivered += 1
            else:
                await self.unregister(client)
        return delivered

    def mark_ready(self, client: ProducerClient, ready: bool = True) -> None:
        """Отметить, что клиент инициализировался (кадр ``ready``, A5)."""
        client.ready = ready

    async def send_state(self) -> None:
        """Служебный ``state``: кто подключён, кто активен."""
        await self.broadcast(
            frames.state_frame(
                producer=self.clients,
                clients=self.client_ids(),
                persona=self.registry.active,
                utterances=len(self._utterances),
                sample_rate=self.sample_rate,
                chunk_bytes=self.chunk_bytes,
            )
        )

    # -- реплика ------------------------------------------------------------------ #
    async def speak(
        self,
        text: str,
        *,
        voice_core: VoiceCore | None,
        persona_id: str = "",
        voice_profile: str | None = None,
    ) -> Utterance:
        """Начать реплику: создать задачу и вернуть её описание.

        :param text: что сказать (теги эмоций уже вырезаны шиной).
        :param voice_core: горло; ``None`` → кадр ``error`` всем.
        :param persona_id: от чьего имени (по умолчанию активная персона).
        :param voice_profile: явный профиль голоса (перекрывает ``voice.yaml``).
        """
        persona = persona_id or self.registry.active
        utterance = Utterance(id=f"u-{uuid.uuid4().hex[:8]}", text=text, persona=persona)
        if voice_core is None:
            await self.broadcast(
                frames.error_frame("голос выключен: features.voice_enabled или --with-voice", code="voice_disabled")
            )
            return utterance
        self._utterances[utterance.id] = utterance
        utterance.task = asyncio.create_task(self._run(utterance, voice_core, persona, voice_profile))
        return utterance

    async def _run(
        self,
        utterance: Utterance,
        voice_core: VoiceCore,
        persona: str,
        voice_profile: str | None,
    ) -> None:
        """Конвейер одной реплики: TTS → PCM → чанки → кадры."""
        chunker = PcmChunker(
            sample_rate=self.sample_rate,
            chunk_bytes=self.chunk_bytes,
            utterance_id=utterance.id,
        )
        viseme_seq = 0
        profile = voice_profile or self._voice_profile_for(persona)
        try:
            async for chunk_wav in voice_core.stream(utterance.text, profile):
                if utterance.stopped:
                    return
                pcm = self._to_pcm(chunk_wav)
                for chunk in chunker.push(pcm):
                    await self._emit_audio(utterance, chunk, persona)
                    if self._wants_visemes():
                        viseme_seq = await self._emit_visemes(utterance, chunk, persona, viseme_seq)
                if utterance.stopped:
                    return
            for chunk in chunker.close():
                await self._emit_audio(utterance, chunk, persona)
                if self._wants_visemes():
                    viseme_seq = await self._emit_visemes(utterance, chunk, persona, viseme_seq)
            await self.broadcast(
                frames.done_frame(utterance.id, chunks=utterance.chunks, persona=persona)
            )
        except asyncio.CancelledError:
            logger.info("Реплика {} отменена", utterance.id)
            raise
        except Exception as exc:  # noqa: BLE001 - клиент должен узнать, почему тишина
            logger.exception("Реплика {} упала: {!r}", utterance.id, exc)
            await self.broadcast(
                frames.error_frame(f"озвучка не удалась: {exc!r}", code="tts_failed", utterance_id=utterance.id)
            )
            await self.broadcast(frames.done_frame(utterance.id, reason="error", persona=persona))
        finally:
            self._utterances.pop(utterance.id, None)

    def _to_pcm(self, chunk_wav: bytes) -> bytes:
        """WAV TTS → raw PCM int16 mono на целевой частоте продюсера (A1/A2)."""
        try:
            pcm, rate = read_wav(chunk_wav)
        except Exception:  # noqa: BLE001 - mp3 от edge-tts: отдаём как есть, клиент разберётся
            logger.warning("Продюсер: чанк не моно-WAV16 — отдаю байты как есть")
            return chunk_wav
        return resample_pcm16(pcm, rate, self.sample_rate)

    def _voice_profile_for(self, persona_id: str) -> str | None:
        """Профиль голоса персоны из ``voice.yaml`` (D3) или ``None`` = дефолт конфига."""
        persona: Persona | None = self.registry.get(persona_id)
        if persona is None:
            return None
        return persona.voice_spec.pack or None

    def _wants_visemes(self) -> bool:
        """Нужна ли серверная разметка визем хоть кому-то (A3.1)."""
        return any(client.want_server_visemes for client in self._clients.values())

    async def _emit_audio(self, utterance: Utterance, chunk: Any, persona: str) -> None:
        """Один ``audio``-кадр всем подключённым продюсерам."""
        utterance.chunks += 1
        await self.broadcast(frames.audio_frame(chunk, utterance_id=utterance.id, persona=persona))

    async def _emit_visemes(
        self, utterance: Utterance, chunk: Any, persona: str, viseme_seq: int
    ) -> int:
        """Разметить чанк на виземы и разослать тем, кто просил; вернуть новый seq.

        Смещение каждой виземы = начало чанка + номер окна внутри чанка (60 мс),
        поэтому таймлайн визем непрерывен на всех чанках одной реплики.
        """
        window_start = viseme_seq
        for index, frame in enumerate(extract_visemes(chunk.data, self.sample_rate)):
            await self.broadcast(
                frames.viseme_frame(
                    frame.viseme,
                    frame.intensity,
                    offset_ms=chunk.offset_ms + index * WINDOW_MS,
                    utterance_id=utterance.id,
                    seq=window_start + index,
                    persona=persona,
                )
            )
            viseme_seq += 1
        return viseme_seq

    # -- прерывание (A3.4) -------------------------------------------------------- #
    async def stop(self, utterance_id: str = "", *, reason: str = "barge_in") -> bool:
        """Прервать реплику: отменить задачу и разослать ``stop``.

        :param utterance_id: что прервать; пусто → текущую последнюю.
        :return: ``True``, если было что прерывать.
        """
        target = self._utterances.get(utterance_id)
        if target is None and not utterance_id and self._utterances:
            target = list(self._utterances.values())[-1]
        if target is None:
            return False
        target.stopped = True
        if target.task is not None and not target.task.done():
            target.task.cancel()
        await self.broadcast(frames.stop_frame(target.id, reason=reason, persona=target.persona))
        logger.info("Реплика {} прервана ({})", target.id, reason)
        return True

    # -- своп персоны (D9) --------------------------------------------------------- #
    async def swap_persona(self, persona_id: str, *, reason: str = "swap") -> dict[str, Any]:
        """Переключить активную персону и разослать кадр ``persona``.

        :return: кадр, который ушёл клиентам (или ``error``, если персоны нет).
        """
        async with self._lock:
            self.registry.reload()
            persona = self.registry.set_active(persona_id)
            if persona is None:
                frame = frames.error_frame(
                    f"персона '{persona_id}' не найдена (доступны: {', '.join(self.registry.ids())})",
                    code="unknown_persona",
                )
                await self.broadcast(frame)
                return frame
            if self.lora is not None:
                await self.lora.apply(persona)
            frame = frames.persona_frame(
                persona.id,
                vrm=f"/api/face/personas/{persona.id}/model.vrm" if persona.vrm_available else None,
                voice=persona.voice_spec.as_dict(),
                card=persona.card.public_dict(),
                face=persona.face_spec.as_dict(),
                swap=True,
                reason=reason,
            )
        await self.broadcast(frame)
        await self.broadcast(frames.focus_frame(persona.id, reason="persona_swap"))
        logger.info("Своп персоны: '{}' ({})", persona_id, reason)
        return frame

    # -- фокус (E2-а) -------------------------------------------------------------- #
    async def focus(self, persona_id: str, *, group: str = "", reason: str = "speaking") -> None:
        """Перевести камеру Unity на персону."""
        await self.broadcast(frames.focus_frame(persona_id, group=group, reason=reason))

    # -- статистика ---------------------------------------------------------------- #
    def update_stats(self, client: ProducerClient, stats: dict[str, Any]) -> None:
        """Запомнить ``stats`` клиента (fps, dropped) — раз в 5 с по контракту A5."""
        client.stats = dict(stats)

    def describe(self) -> dict[str, Any]:
        """Сводка для ``/api/face/state`` и ``state``-кадра."""
        return {
            "producer": {
                "clients": self.clients,
                "ids": self.client_ids(),
                "sample_rate": self.sample_rate,
                "chunk_bytes": self.chunk_bytes,
                "protocol": frames.PRODUCER_PROTOCOL,
                "format": frames.AUDIO_FORMAT,
                "active_persona": self.registry.active,
                "utterances": len(self._utterances),
                "clients_stats": {cid: c.stats for cid, c in self._clients.items()},
                "want_server_visemes": [cid for cid, c in self._clients.items() if c.want_server_visemes],
            }
        }
