"""Слой «голос» (этап 4): уши, горло, VAD, хоткей, паки — одним фасадом.

:class:`VoiceCore` собирается из конфига: реестры STT/TTS с **горячей** заменой
профилей (конфиг перечитывается на каждый вызов, перезапуск не нужен) и фолбэком
на дефолт при ошибке (ADR-012). В песочнице всё работает на mock-бэкендах.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator

from loguru import logger

from ..config import Settings, resolve_path
from .hotkey import MockPushToTalk, PushToTalk, parse_combo
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
    """Собирает реестр горла и voice-профили из конфига."""
    backends: list[TTSBackend] = [
        SileroTTS(device=settings.voice.tts_device),
        EdgeTTS(),
        ZeroShotTTS(voices_dir=resolve_path(settings.voice.voices_dir)),
        MockTTS(),
    ]
    profiles, default = build_voice_profiles(settings)
    return TTSRegistry(backends, profiles, default_profile=default)


class VoiceCore:
    """Фасад голоса: транскрибация, озвучка, паки, хоткей."""

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

    # -- горячая синхронизация с конфигом --------------------------------------- #
    def sync(self) -> None:
        """Подхватывает изменения конфига без перезапуска (ADR-012 hot-swap)."""
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
        """Полный wav реплики выбранным голосом (с фолбэком)."""
        voice_profile = self._profile(profile)
        backend = self.tts.pick_backend(voice_profile)
        return await backend.synthesize(text, voice_profile)

    async def stream(self, text: str, profile: str | None = None) -> AsyncIterator[bytes]:
        """Куски wav по предложениям: плеер говорит, не дожидаясь конца."""
        voice_profile = self._profile(profile)
        backend = self.tts.pick_backend(voice_profile)
        async for chunk in backend.stream(text, voice_profile):
            yield chunk

    # -- диагностика --------------------------------------------------------------- #
    def describe(self) -> dict[str, Any]:
        """Сводка для /api/voice/profiles и панели."""
        self.sync()
        return {
            "stt_default": self.stt.default,
            "tts_default_profile": self.tts.default_profile,
            "vad": self.vad.name,
            "hotkey": self.hotkey.combo,
            "backends": self.stt.describe() + self.tts.describe(),
            "packs": self.packs.list(),
        }
