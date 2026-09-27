"""Слой «голос» (этап 4): уши, горло, VAD, хоткей, паки — одним фасадом.

:class:`VoiceCore` собирается из конфига: реестры STT/TTS с **горячей** заменой
профилей (конфиг перечитывается на каждый вызов, перезапуск не нужен) и фолбэком
на дефолт при ошибке (ADR-012). В песочнице всё работает на mock-бэкендах.

0.7.0 (этап «Голос», ADR-027): горло — «одна корона + шкаф платьев». Резидент
``voice.tts_resident`` (qwen3) прогревается при старте (``VoiceCore.start`` из
lifespan), обслуживает всех персон и выгружается при явном переодевании
(``switch_engine``); альтернативы ленивы. Нонвербалика (Q7-б) — wav-пакеты
через ``stream_mixed``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from loguru import logger

from ..config import Settings, resolve_path
from .closet import CosyVoice2TTS, FishSpeechTTS
from .hotkey import MockPushToTalk, PushToTalk, parse_combo
from .nonverbal import Segment, nonverbal_pack_path, nonverbal_tags, split_nonverbal
from .packs import Pack, PackError, PackManager
from .pcm import (
    CHUNK_BYTES,
    DEFAULT_SAMPLE_RATE,
    AudioChunk,
    PcmChunker,
    resample_pcm16,
    wav_to_pcm,
)
from .stt import (
    EnergyVAD,
    FasterWhisperSTT,
    MockSTT,
    SileroVAD,
    STTBackend,
    STTRegistry,
    Transcript,
    VoskSTT,
    WhisperCppSTT,
    read_wav,
    write_wav,
)
from .tts import (
    EdgeTTS,
    MockTTS,
    SileroTTS,
    TTSBackend,
    TTSRegistry,
    VoiceProfile,
    ZeroShotTTS,
    split_sentences,
)
from .tts_qwen3 import Qwen3TTS

__all__ = [
    "VoiceCore",
    "VoiceProfile",
    "Transcript",
    "STTRegistry",
    "TTSRegistry",
    "STTBackend",
    "TTSBackend",
    "PackManager",
    "Pack",
    "PackError",
    "PushToTalk",
    "MockPushToTalk",
    "parse_combo",
    "split_sentences",
    "split_nonverbal",
    "nonverbal_tags",
    "nonverbal_pack_path",
    "Segment",
    "read_wav",
    "write_wav",
    "AudioChunk",
    "PcmChunker",
    "CHUNK_BYTES",
    "DEFAULT_SAMPLE_RATE",
    "resample_pcm16",
    "wav_to_pcm",
    "FasterWhisperSTT",
    "WhisperCppSTT",
    "VoskSTT",
    "MockSTT",
    "SileroTTS",
    "EdgeTTS",
    "ZeroShotTTS",
    "MockTTS",
    "Qwen3TTS",
    "CosyVoice2TTS",
    "FishSpeechTTS",
    "EnergyVAD",
    "SileroVAD",
    "build_stt_registry",
    "build_tts_registry",
    "build_vad",
    "build_voice_profiles",
]


def build_stt_registry(settings: Settings) -> STTRegistry:
    """Собирает реестр ушей из конфига."""
    voice = settings.voice
    backends: list[STTBackend] = [
        FasterWhisperSTT(
            model_size=voice.stt_model,
            device=voice.stt_device,
            compute_type=voice.stt_compute_type,
            language=voice.stt_language,
        ),
        WhisperCppSTT(),
        VoskSTT(),
        MockSTT(),
    ]
    names = {b.name for b in backends}
    default = voice.stt_backend if voice.stt_backend in names else "mock"
    return STTRegistry(backends, default=default)


def build_vad(settings: Settings):
    """VAD по конфигу: silero (если torch есть), иначе чистый energy-vad."""
    if settings.voice.vad_backend == "silero":
        vad = SileroVAD(threshold=settings.voice.vad_threshold)
        ok, _reason = vad.available()
        if ok:
            return vad
        logger.warning("silero-vad недоступен, фолбэк на energy-vad")
    return EnergyVAD(threshold=settings.voice.vad_energy_threshold)


def build_voice_profiles(settings: Settings) -> tuple[dict[str, VoiceProfile], str]:
    """Voice-профили из конфига (живьём, для горячей замены)."""
    profiles: dict[str, VoiceProfile] = {}
    for name, spec in settings.voice.profiles.items():
        profiles[name] = VoiceProfile(
            name=name,
            backend=spec.get("backend", "silero"),
            voice=spec.get("voice", "kseniya"),
            reference=spec.get("reference", ""),
            speed=float(spec.get("speed", 1.0)),
            note=spec.get("note", ""),
            extra=spec.get("extra", {}) or {},
        )
    if not profiles:
        profiles = {"lilith": VoiceProfile(name="lilith", backend="mock", note="дефолт без конфига")}
    default = settings.voice.default_voice
    if default not in profiles:
        default = next(iter(profiles))
    return profiles, default


def build_tts_registry(settings: Settings) -> TTSRegistry:
    """Собирает реестр горла и voice-профили из конфига (ADR-027: резидент + шкаф)."""
    backends: list[TTSBackend] = [
        Qwen3TTS(device=settings.voice.tts_device),   # корона (резидент 0.7.0)
        SileroTTS(device=settings.voice.tts_device),
        EdgeTTS(),
        CosyVoice2TTS(),                              # шкаф платьев (слоты, Q2)
        FishSpeechTTS(),
        ZeroShotTTS(voices_dir=resolve_path(settings.voice.voices_dir)),
        MockTTS(),
    ]
    profiles, default = build_voice_profiles(settings)
    return TTSRegistry(backends, profiles, default_profile=default, resident=settings.voice.tts_resident)


class VoiceCore:
    """Фасад голоса: транскрибация, озвучка, паки, хоткей, движок-резидент."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.stt = build_stt_registry(settings)
        self.tts = build_tts_registry(settings)
        self.vad = build_vad(settings)
        self.packs = PackManager(
            resolve_path(settings.voice.packs_manifest),
            root=resolve_path(settings.voice.packs_root or "."),
        )
        self.hotkey = MockPushToTalk(combo=settings.voice.push_to_talk_key)
        self._warmup_task: asyncio.Task | None = None

    # -- жизненный цикл (ADR-027: резидент грузится при старте) ----------------- #
    async def start(self) -> None:
        """Прогрев резидента фоном: сервер стартует сразу, corona готовится параллельно."""
        resident = self.settings.voice.tts_resident
        if not resident:
            return
        self._warmup_task = asyncio.create_task(self._warmup_resident(resident))

    async def _warmup_resident(self, resident: str) -> None:
        backend = self.tts._backends.get(resident)  # noqa: SLF001 — свой реестр, один фасад
        if backend is None:
            logger.warning("резидент '{}' не в реестре горла — прогрев отменён", resident)
            return
        try:
            await backend.warmup(self._resident_model_id(resident))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — мёртвый резидент не должен ронять сервер
            logger.error("прогрев резидента '{}' не удался: {} (фолбэк-цепочка ADR-012 жива)", resident, exc)

    def _resident_model_id(self, resident: str) -> str | None:
        """Model id резидента из первого профиля его бэкенда (иначе дефолт движка)."""
        for profile in self.tts.profiles.values():
            if profile.backend == resident:
                model = (profile.extra or {}).get("model")
                if model:
                    return str(model)
        return None

    async def stop(self) -> None:
        """Остановка: снять прогрев, выгрузить все движки (VRAM на стол)."""
        if self._warmup_task is not None and not self._warmup_task.done():
            self._warmup_task.cancel()
            try:
                await self._warmup_task
            except asyncio.CancelledError:
                pass
        self._warmup_task = None
        for name in self.tts.backend_names():
            backend = self.tts._backends[name]  # noqa: SLF001
            if backend.loaded():
                try:
                    backend.unload()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("движок '{}' не выгрузился при остановке: {}", name, exc)

    # -- движок («шкаф платьев», ADR-027) ---------------------------------------- #
    def switch_engine(self, name: str) -> dict[str, Any]:
        """Явное переодевание: выбранный движок — корона, прочие выгружаются."""
        return self.tts.switch_resident(name)

    def engine_status(self) -> dict[str, Any]:
        """Резидент + доступность/загруженность движков (GET /api/voice/engine, панель)."""
        info = self.tts.engine_info()
        info["configured"] = self.settings.voice.tts_resident or None
        return info

    # -- горячая синхронизация с конфигом --------------------------------------- #
    def sync(self) -> None:
        """Подхватывает изменения конфига без перезапуска (ADR-012 hot-swap).

        Резидент намеренно НЕ синхронизируется отсюда: config.yaml задаёт корону
        на старте, а переключение в панели — сессионный оверрайд (иначе sync()
        отменял бы выбор Курьера на каждой реплике).
        """
        voice = self.settings.voice
        if voice.stt_backend in self.stt.names():
            self.stt.default = voice.stt_backend
        profiles, default = build_voice_profiles(self.settings)
        self.tts.profiles = profiles
        self.tts.default_profile = default

    # -- уши ------------------------------------------------------------------- #
    async def transcribe(self, audio: bytes, sample_rate: int = 16000, backend: str | None = None) -> Transcript:
        """Обрезать тишину и распознать (горячий выбор бэкенда + фолбэк)."""
        self.sync()
        trimmed = self.vad.trim(audio, sample_rate) if self.settings.voice.vad_enabled else audio
        engine = self.stt.pick_available(backend)
        return await engine.transcribe(trimmed, sample_rate)

    # -- горло ------------------------------------------------------------------- #
    def _profile(self, name: str | None) -> VoiceProfile:
        self.sync()
        return self.tts.profile(name)

    async def speak(self, text: str, profile: str | None = None) -> bytes:
        """Полный wav реплики выбранным голосом (фолбэк по доступности И по ошибке)."""
        voice_profile = self._profile(profile)
        backend = self.tts.pick_backend(voice_profile)
        try:
            return await backend.synthesize(text, voice_profile)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — ADR-012: упавший движок не роняет разговор
            fallback = self.tts.pick_fallback(voice_profile, exclude=backend.name)
            if fallback is None:
                raise
            logger.error("TTS '{}' упал на реплике ({}), ADR-012 фолбэк на '{}'",
                         backend.name, exc, fallback.name)
            return await fallback.synthesize(text, voice_profile)

    async def stream(self, text: str, profile: str | None = None) -> AsyncIterator[bytes]:
        """Куски wav по мере готовности: плеер говорит, не дожидаясь конца.

        Фолбэк по ошибке (ADR-012) — только если не отдано ни одного куска:
        перезапускать реплику с середины значит заставить её говорить дважды.
        """
        voice_profile = self._profile(profile)
        backend = self.tts.pick_backend(voice_profile)
        yielded = False
        try:
            async for chunk in backend.stream(text, voice_profile):
                yielded = True
                yield chunk
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            if yielded:
                raise
            fallback = self.tts.pick_fallback(voice_profile, exclude=backend.name)
            if fallback is None:
                raise
            logger.error("TTS '{}' упал до первого куска ({}), ADR-012 фолбэк на '{}'",
                         backend.name, exc, fallback.name)
            async for chunk in fallback.stream(text, voice_profile):
                yield chunk

    async def stream_mixed(
        self,
        text: str,
        profile: str | None = None,
        nonverbal: dict[str, Any] | None = None,
        voice_dir: str | Path | None = None,
    ) -> AsyncIterator[bytes]:
        """Реплика с нонвербаликой (ADR-027 Q7-б): текст → горло, теги → wav-пакеты.

        ``nonverbal`` — слот из voice.yaml персоны, ``voice_dir`` — её папка
        (personas/<id>/voice): ассеты в ``<voice_dir>/nonverbal/<ключ>.wav``.
        Нет ассета — тег вырезан, жёлтый лог, разговор продолжается.
        """
        tags = nonverbal_tags(nonverbal)
        for kind, value in split_nonverbal(text, tags):
            if kind == "text":
                async for chunk in self.stream(value, profile):
                    yield chunk
            else:
                pack = nonverbal_pack_path(voice_dir, value)
                if pack is not None:
                    yield pack.read_bytes()

    # -- диагностика --------------------------------------------------------------- #
    def describe(self) -> dict[str, Any]:
        """Сводка для /api/voice/profiles и панели."""
        self.sync()
        return {
            "stt_default": self.stt.default,
            "tts_default_profile": self.tts.default_profile,
            "tts_resident": self.tts.resident or None,
            "tts_engines": self.tts.engine_info()["engines"],
            "vad": self.vad.name,
            "hotkey": self.hotkey.combo,
            "backends": self.stt.describe() + self.tts.describe(),
            "packs": self.packs.list(),
        }
