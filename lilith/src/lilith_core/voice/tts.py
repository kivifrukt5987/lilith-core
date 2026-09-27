"""Горло (этап 4): реестр TTS-бэкендов, voice-профили, стриминг по предложениям.

Контракт: бэкенд умеет ``synthesize(text, profile) -> bytes wav`` и
``stream(text, profile)`` — асинхронный итератор кусков wav по предложениям
(панель/плеер начинают говорить, не дожидаясь конца реплики).

0.7.0 (ADR-027, «одна корона + шкаф платьев»): резидентный движок
``voice.tts_resident`` (qwen3 — Qwen3-TTS 0.6B-Base, voice/tts_qwen3.py)
обслуживает ВСЕ профили/персон, грузится при старте (``warmup``) и живёт в
памяти; альтернативы (silero/edge/cosyvoice2/fish/zero-shot/mock) — «шкаф»:
веса не грузят, пока Курьер явно не переключит движок (панель «🎙 голос» +
подтверждение → ``TTSRegistry.switch_resident`` — прочие выгружаются через
``unload()``, VRAM возвращается). Резидент недоступен (нет GPU/зависимостей) —
фолбэк-цепочка работает как раньше (ADR-012).

Бэкенды: qwen3 (резидент), silero (локально), edge-tts (облако), слоты шкафа
CosyVoice 2 / Fish Speech (voice/closet.py), zero-shot слот
(IndexTTS/XTTS/CosyVoice/F5 — папка ``voices/<имя>/reference.wav`` + запись в
конфиге), MockTTS для тестов и песочницы. Горячая замена: профили резолвятся
на каждый вызов, при ошибке — фолбэк на дефолт (ADR-012).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import struct
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from .stt import write_wav

__all__ = [
    "VoiceProfile",
    "TTSBackend",
    "SileroTTS",
    "EdgeTTS",
    "ZeroShotTTS",
    "MockTTS",
    "TTSRegistry",
    "split_sentences",
]

_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+")


def split_sentences(text: str) -> list[str]:
    """Режет реплику на предложения для стриминга речи."""
    parts = [p.strip() for p in _SENTENCE_RE.split(text.strip()) if p.strip()]
    return parts or ([text.strip()] if text.strip() else [])


@dataclass(slots=True)
class VoiceProfile:
    """Именованный голос: бэкенд + тембр + референс + просодия."""

    name: str
    backend: str = "silero"
    voice: str = "kseniya"
    #: Путь к reference.wav для zero-shot-клонов (voices/<имя>/reference.wav).
    reference: str = ""
    speed: float = 1.0
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class TTSBackend:
    """Контракт TTS-бэкенда."""

    name = "base"

    def available(self) -> tuple[bool, str]:
        return False, "не реализован"

    def loaded(self) -> bool:
        """Веса в памяти? Панель «🎙 голос» и switch_resident смотрят сюда (ADR-027)."""
        return False

    def unload(self) -> None:
        """Выгрузить веса / вернуть VRAM (переключение движка, ADR-027).

        Дефолт: нечего выгружать. Обязан быть идемпотентным и не бросаться.
        """

    async def warmup(self) -> None:
        """Прогрев при старте сервера (резидент). Дефолт: ничего не делать."""

    async def synthesize(self, text: str, profile: VoiceProfile) -> bytes:
        """Полный wav реплики."""
        raise NotImplementedError

    async def stream(self, text: str, profile: VoiceProfile) -> AsyncIterator[bytes]:
        """Куски wav по предложениям (дефолт: один кусок = весь текст)."""
        yield await self.synthesize(text, profile)


class SileroTTS(TTSBackend):
    """silero-tts (torch): локальные русские голоса, быстро, CPU/GPU."""

    name = "silero"

    def __init__(self, device: str = "auto") -> None:
        self.device = device
        self._model = None

    def available(self) -> tuple[bool, str]:
        import importlib.util

        if importlib.util.find_spec("torch") is None:
            return False, "torch не установлен (pip install -e .[voice])"
        return True, ""

    def loaded(self) -> bool:
        return self._model is not None

    def unload(self) -> None:
        """Отпустить hub-модель (шкаф платьев: silero выгружается при смене короны)."""
        if self._model is None:
            return
        self._model = None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
        logger.info("TTS silero: выгружена — VRAM возвращён")

    async def warmup(self) -> None:
        """Прогрев: заранее стянуть hub-веса (дёшево, ~60 МБ)."""
        ok, reason = self.available()
        if not ok:
            logger.info("TTS silero: прогрев пропущен — {}", reason)
            return
        await asyncio.to_thread(self._load)

    def _load(self) -> Any:
        if self._model is None:
            import torch

            device = self.device
            if device == "auto":
                device = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info("TTS: загружаю silero на {}", device)
            self._model = torch.hub.load(
                "snakers4/silero-models", "silero_tts", language="ru", speaker="v4_ru",
                trust_repo=True,
            )
            self._model.to(device)
        return self._model

    async def synthesize(self, text: str, profile: VoiceProfile) -> bytes:
        import asyncio

        model = self._load()

        def _run() -> bytes:
            audio = model.apply_text(
                text, speaker=profile.voice or "kseniya", sample_rate=48000
            )
            samples = audio[0] if isinstance(audio, list) else audio
            pcm = (samples.clamp(-1, 1).numpy() * 32767).astype("<i2")
            return write_wav(pcm.tobytes(), 48000)

        return await asyncio.to_thread(_run)

    async def stream(self, text: str, profile: VoiceProfile) -> AsyncIterator[bytes]:
        for sentence in split_sentences(text):
            yield await self.synthesize(sentence, profile)


class EdgeTTS(TTSBackend):
    """edge-tts: голоса Microsoft, ноль локального веса, но облако (нужен интернет)."""

    name = "edge"

    def available(self) -> tuple[bool, str]:
        import importlib.util

        if importlib.util.find_spec("edge_tts") is None:
            return False, "edge-tts не установлен (pip install -e .[voice])"
        return True, ""

    async def synthesize(self, text: str, profile: VoiceProfile) -> bytes:
        import edge_tts

        voice = profile.extra.get("edge_voice", "ru-RU-SvetlanaNeural")
        communicate = edge_tts.Communicate(text, voice)
        chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)  # mp3: панель/плеер переварят


class ZeroShotTTS(TTSBackend):
    """Слот zero-shot-клонов: reference.wav из папки voices/<имя>/.

    Дырка под любой клон-движок (IndexTTS / XTTS / CosyVoice / F5): конкретная
    реализация подставляется подменой ``_engine`` (или расширением на этапе,
    когда Кирюша выберет движок). Контракт уже зафиксирован.
    """

    name = "zero-shot"

    def __init__(self, voices_dir: str | Path = "voices") -> None:
        self.voices_dir = Path(voices_dir)
        self._engine = None

    def available(self) -> tuple[bool, str]:
        if self._engine is None:
            return False, "клон-движок не выбран (IndexTTS/XTTS/CosyVoice/F5): слот готов, движок подключается отдельно"
        return True, ""

    def reference_path(self, profile: VoiceProfile) -> Path:
        """Путь к reference.wav профиля."""
        return Path(profile.reference) if profile.reference else self.voices_dir / profile.name / "reference.wav"

    async def synthesize(self, text: str, profile: VoiceProfile) -> bytes:
        if self._engine is None:
            raise RuntimeError("zero-shot движок не подключён")
        reference = self.reference_path(profile)
        if not reference.is_file():
            raise FileNotFoundError(f"reference.wav не найден: {reference}")
        return await self._engine(text, reference, profile)  # type: ignore[operator]


class MockTTS(TTSBackend):
    """Mock: детерминированный «пик» по хешу текста. Для тестов и песочницы."""

    name = "mock"

    def __init__(self, sample_rate: int = 16000, ms_per_sentence: int = 60) -> None:
        self.sample_rate = sample_rate
        self.ms_per_sentence = ms_per_sentence
        self.spoken: list[str] = []

    def available(self) -> tuple[bool, str]:
        return True, ""

    def _beep(self, text: str) -> bytes:
        digest = hashlib.md5(text.encode("utf-8")).digest()
        n = max(160, self.sample_rate * self.ms_per_sentence // 1000)
        seed = digest[0]
        pattern = struct.pack("<h", (seed - 128) * 40)
        return write_wav((pattern * (n // 2 + 1))[: n * 2], self.sample_rate)

    async def synthesize(self, text: str, profile: VoiceProfile) -> bytes:
        self.spoken.append(text)
        return self._beep(text)

    async def stream(self, text: str, profile: VoiceProfile) -> AsyncIterator[bytes]:
        for sentence in split_sentences(text):
            self.spoken.append(sentence)
            yield self._beep(sentence)
            await asyncio.sleep(0)


class TTSRegistry:
    """Реестр горла: профили голосов + бэкенды + резидент + горячая замена + фолбэк.

    ADR-027: ``resident`` — «одна корона». Если задан и доступен, он обслуживает
    любой профиль (персоны различаются душами-reference, не движками); недоступен —
    фолбэк-цепочка профиля работает как в этапе 4. ``switch_resident`` — явное
    переодевание: прочие движки выгружаются (``unload``), VRAM возвращается.
    """

    def __init__(
        self,
        backends: list[TTSBackend],
        profiles: dict[str, VoiceProfile],
        default_profile: str,
        resident: str = "",
    ) -> None:
        self._backends = {b.name: b for b in backends}
        self.profiles = profiles
        self.default_profile = default_profile if default_profile in profiles else next(iter(profiles))
        if resident and resident not in self._backends:
            logger.warning("TTS: резидент '{}' не в реестре ({}) — работаю без короны",
                           resident, ", ".join(sorted(self._backends)))
            resident = ""
        self.resident = resident

    def backend_names(self) -> list[str]:
        return sorted(self._backends)

    def profile(self, name: str | None = None) -> VoiceProfile:
        """Профиль голоса по имени (горячо: на каждый вызов)."""
        return self.profiles.get(name or self.default_profile, self.profiles[self.default_profile])

    def pick_backend(self, profile: VoiceProfile) -> TTSBackend:
        """Бэкенд профиля с фолбэком: резидент → профиль → дефолт-профиль → первый доступный."""
        chain = [self.resident, profile.backend, self.profile().backend, *self.backend_names()]
        seen: set[str] = set()
        for candidate in chain:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            backend = self._backends.get(candidate)
            if backend is None:
                continue
            ok, _reason = backend.available()
            if ok:
                if candidate not in (self.resident, profile.backend):
                    logger.warning("TTS: бэкенд '{}' недоступен, фолбэк на '{}'", profile.backend, candidate)
                return backend
        raise RuntimeError("ни один TTS-бэкенд не доступен: поставь voice-зависимости или используй mock")

    def pick_fallback(self, profile: VoiceProfile, exclude: str) -> TTSBackend | None:
        """Следующий доступный движок в обход упавшего (ADR-012: фолбэк по ошибке)."""
        chain = [self.resident, profile.backend, self.profile().backend, *self.backend_names()]
        seen: set[str] = {exclude}
        for candidate in chain:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            backend = self._backends.get(candidate)
            if backend is not None and backend.available()[0]:
                return backend
        return None

    def switch_resident(self, name: str) -> dict[str, Any]:
        """Явное переодевание (панель «🎙 голос» + подтверждение → POST /api/voice/engine).

        ``name`` = "none"/"" — без короны (полностью ленивый режим этапа 4).
        Все прочие движки выгружаются: 12 ГБ бережём для игр и сюжетных LLM.
        """
        target = "" if name.strip().lower() in ("", "none") else name.strip()
        if target and target not in self._backends:
            raise KeyError(f"движок '{target}' не в реестре: {', '.join(self.backend_names())}")
        unloaded: list[str] = []
        for backend_name, backend in self._backends.items():
            if backend_name == target:
                continue
            was_loaded = backend.loaded()
            try:
                backend.unload()
            except Exception as exc:  # noqa: BLE001 — выгрузка не должна ломать переодевание
                logger.warning("TTS: '{}' не выгрузился: {}", backend_name, exc)
            if was_loaded:
                unloaded.append(backend_name)
        self.resident = target
        ok, reason = self._backends[target].available() if target else (True, "")
        logger.info("TTS: корона — {} (выгружены: {})", target or "нет", ", ".join(unloaded) or "ничего")
        return {"resident": target or None, "unloaded": unloaded, "available": ok, "reason": reason or None}

    def engine_info(self) -> dict[str, Any]:
        """Сводка движков для GET /api/voice/engine и панели."""
        engines: dict[str, Any] = {}
        for name in self.backend_names():
            ok, reason = self._backends[name].available()
            engines[name] = {
                "available": ok, "reason": reason or None, "loaded": self._backends[name].loaded()
            }
        return {"resident": self.resident or None, "engines": engines}

    def describe(self) -> list[dict[str, Any]]:
        """Сводка бэкендов и профилей для /api/voice/profiles."""
        out = [
            {"kind": "tts", "name": name, "available": ok, "reason": reason or None}
            for name in self.backend_names()
            for ok, reason in [self._backends[name].available()]
        ]
        out.append({"kind": "tts-resident", "name": self.resident or None})
        for name, profile in sorted(self.profiles.items()):
            out.append(
                {
                    "kind": "voice-profile",
                    "name": name,
                    "backend": profile.backend,
                    "voice": profile.voice,
                    "reference": profile.reference,
                    "default": name == self.default_profile,
                    "note": profile.note,
                }
            )
        return out
