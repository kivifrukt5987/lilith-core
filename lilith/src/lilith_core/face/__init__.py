"""Слой «лицо» (этапы 5–6): эмоции, виземы, персоны, продюсер для Unity.

Этап 5 дал парсер тегов ``[emotion: …]``, мост в VTuber Studio и VRM в веб-панели.
Этап 6 (**пивот**: лицо = Unity-клиент, three-vrm больше не основной путь) добавляет:

* :mod:`~lilith_core.face.ws_frames` — плоский WS-протокол продюсера;
* :mod:`~lilith_core.face.producer` — хаб подключений и конвейер реплики
  (TTS → raw PCM 24 kHz → чанки 2048 байт → кадры ``audio``);
* :mod:`~lilith_core.face.personas` — реестр персон-агентов v2
  (``card.yaml`` / ``voice.yaml`` / ``face.yaml``, legacy-фолбэк на ``profile.yaml``);
* :mod:`~lilith_core.face.lora` — LoRA-слот (этап 6: prompt-only);
* :mod:`~lilith_core.face.group` — групповые сцены (серверная часть);
* :mod:`~lilith_core.face.endpoints` — WS-обработчики ``/ws/face/producer`` и ``/ws/group``.

:class:`FaceCore` по-прежнему связывает реплики с аватаром: из текста вынимаются
теги эмоций, события уходят в мост и копятся в состоянии для панели и голоса.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from ..config import Settings
from .emotions import (
    EMOTIONS,
    EMOTION_ALIASES,
    EMOTION_TO_BLENDSHAPES,
    EMOTION_TO_PROSODY,
    EmotionEvent,
    blends_for,
    describe as describe_emotions,
    guess_emotion,
    parse_emotions,
    prosody_for,
    strip_emotion_tags,
)
from .bus import FaceBus
from .group import GroupFull, GroupManager, GroupMember, GroupSession, UnknownPersona
from .lora import (
    LlamaCppLora,
    LMStudioLora,
    LocalAiLora,
    LoraBackend,
    LoraState,
    PersonaLoraManager,
    PromptOnlyLora,
    build_lora_manager,
)
from .personas import (
    CARD_FILE,
    FACE_FILE,
    VOICE_FILE,
    IdleSpec,
    LoraSpec,
    Persona,
    PersonaCard,
    PersonaFace,
    PersonaRegistry,
    PersonaVoice,
    WindowSpec,
)
from .producer import FaceProducerHub, ProducerClient, Utterance
from . import ws_frames
from .visemes import VISEME_TO_VRM, VISEME_WINDOWS, VisemeFrame, analyze_window, extract_visemes
from .vtuber_bridge import FaceBridge, MockFaceBridge, NullFaceBridge, VmcBridge, VTuberStudioBridge

__all__ = [
    "FaceCore",
    "FaceBridge",
    "VTuberStudioBridge",
    "MockFaceBridge",
    "NullFaceBridge",
    "VmcBridge",
    "EmotionEvent",
    "EMOTIONS",
    "EMOTION_ALIASES",
    "EMOTION_TO_BLENDSHAPES",
    "EMOTION_TO_PROSODY",
    "parse_emotions",
    "strip_emotion_tags",
    "guess_emotion",
    "blends_for",
    "prosody_for",
    "describe_emotions",
    "build_face_bridge",
    "FaceBus",
    "Persona",
    "PersonaRegistry",
    "PersonaCard",
    "PersonaVoice",
    "PersonaFace",
    "WindowSpec",
    "LoraSpec",
    "IdleSpec",
    "CARD_FILE",
    "VOICE_FILE",
    "FACE_FILE",
    "FaceProducerHub",
    "ProducerClient",
    "Utterance",
    "ws_frames",
    "LoraBackend",
    "PromptOnlyLora",
    "LocalAiLora",
    "LMStudioLora",
    "LlamaCppLora",
    "LoraState",
    "PersonaLoraManager",
    "build_lora_manager",
    "GroupManager",
    "GroupSession",
    "GroupMember",
    "GroupFull",
    "UnknownPersona",
    "VisemeFrame",
    "VISEME_WINDOWS",
    "VISEME_TO_VRM",
    "analyze_window",
    "extract_visemes",
]


def build_face_bridge(settings: Settings) -> FaceBridge:
    """Мост по конфигу: VTuber Studio, если включён; иначе пустышка."""
    face = settings.face
    if not face.vtuber_studio_enabled:
        return NullFaceBridge()
    return VTuberStudioBridge(
        url=face.vtuber_studio_url,
        plugin=face.vtuber_studio_plugin,
        developer=face.vtuber_studio_developer,
        token_env=face.vtuber_studio_token_env,
        hotkeys=dict(face.hotkeys),
        reconnect_delay_sec=face.reconnect_delay_sec,
        emotion_default=face.emotion_default,
    )


class FaceCore:
    """Фасад лица: применить события эмоций, состояние, жизненный цикл моста."""

    def __init__(self, settings: Settings, bridge: FaceBridge | None = None) -> None:
        self.settings = settings
        self.bridge = bridge or build_face_bridge(settings)
        self.history: list[dict[str, Any]] = []

    # -- жизненный цикл ------------------------------------------------------ #
    async def start(self) -> None:
        """Поднять мост (если он не пустышка)."""
        await self.bridge.start()
        logger.info("Лицо включено: мост {}", self.bridge.name)

    async def stop(self) -> None:
        """Остановить мост."""
        await self.bridge.stop()

    # -- эмоции ---------------------------------------------------------------- #
    def clean(self, text: str) -> tuple[str, list[EmotionEvent]]:
        """Очистить реплику от тегов и вернуть события (учитывает конфиг)."""
        clean, events = strip_emotion_tags(text)
        if not events and self.settings.face.keyword_fallback:
            guessed = guess_emotion(clean)
            if guessed:
                events = [EmotionEvent(name=guessed, raw=guessed, position=0)]
        return clean, events

    async def apply(self, events: list[EmotionEvent]) -> int:
        """Наложить события на аватар; возвращает число доставленных."""
        delivered = 0
        for event in events:
            self.history.append({"name": event.name, "position": event.position})
            if await self.bridge.set_emotion(event.name):
                delivered += 1
        if events:
            logger.debug("Лицо: эмоции {} (доставлено {})", [e.name for e in events], delivered)
        return delivered

    async def process_reply(self, text: str) -> tuple[str, list[EmotionEvent], int]:
        """Полный цикл реплики: (чистый текст, события, доставлено эмоций)."""
        clean, events = self.clean(text)
        delivered = await self.apply(events)
        return clean, events, delivered

    # -- диагностика ----------------------------------------------------------- #
    def describe(self) -> dict[str, Any]:
        """Сводка для /api/face/state и панели."""
        return {
            "enabled": self.settings.features.face_enabled,
            "bridge": self.bridge.state(),
            "keyword_fallback": self.settings.face.keyword_fallback,
            "hotkeys": dict(self.settings.face.hotkeys),
            "history_tail": self.history[-8:],
            "emotions": describe_emotions(),
        }
