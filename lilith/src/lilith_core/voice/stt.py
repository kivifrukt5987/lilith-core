"""Уши (этап 4): реестр STT-бэкендов + VAD.

Контракт (совет принятой кухни, ADR-012): ``transcribe(audio) -> text``.
Реализации: faster-whisper (дефолт), whisper.cpp и vosk — резервные слоты
(объявлены, помечены недоступными до реализации), MockSTT для тестов и песочницы.
Бэкенды ленивые: тяжёлые импорты происходят при первом обращении, а не на старте ядра.
"""

from __future__ import annotations

import io
import math
import struct
import wave
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

__all__ = [
    "Transcript",
    "STTBackend",
    "FasterWhisperSTT",
    "WhisperCppSTT",
    "VoskSTT",
    "MockSTT",
    "STTRegistry",
    "VAD",
    "EnergyVAD",
    "SileroVAD",
    "read_wav",
    "write_wav",
]


@dataclass(slots=True)
class Transcript:
    """Результат распознавания."""

    text: str
    language: str = "ru"
    duration_s: float = 0.0
    backend: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


class STTBackend:
    """Базовый класс STT-бэкенда: контракт ``transcribe``."""

    name: str = "base"

    def available(self) -> tuple[bool, str]:
        """Доступен ли бэкенд (зависимости/веса) + причина."""
        return False, "не реализован"

    async def transcribe(self, audio: bytes, sample_rate: int = 16000) -> Transcript:
        """Распознаёт PCM16-байты и возвращает :class:`Transcript`."""
        raise NotImplementedError


class FasterWhisperSTT(STTBackend):
    """faster-whisper: модели small/medium/large-v3, GPU fp16 или CPU."""

    name = "faster-whisper"

    def __init__(self, model_size: str = "small", device: str = "auto", compute_type: str = "float16", language: str = "ru") -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self._model = None

    def available(self) -> tuple[bool, str]:
        import importlib.util

        if importlib.util.find_spec("faster_whisper") is None:
            return False, "faster-whisper не установлен (pip install -e .[voice])"
        return True, ""

    def _load(self) -> Any:
        if self._model is None:
            from faster_whisper import WhisperModel

            device = self.device
            if device == "auto":
                try:
                    import torch

                    device = "cuda" if torch.cuda.is_available() else "cpu"
                except Exception:  # noqa: BLE001
                    device = "cpu"
            compute = self.compute_type if device == "cuda" else "int8"
            logger.info("STT: загружаю faster-whisper {} на {} ({})", self.model_size, device, compute)
            self._model = WhisperModel(self.model_size, device=device, compute_type=compute)
        return self._model

    async def transcribe(self, audio: bytes, sample_rate: int = 16000) -> Transcript:
        import asyncio
        import numpy as np

        model = self._load()
        samples = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0

        def _run() -> tuple[str, float, str]:
            segments, info = model.transcribe(samples, language=self.language, vad_filter=True)
            text = " ".join(seg.text.strip() for seg in segments).strip()
            return text, float(info.duration or 0.0), str(info.language or self.language)

        text, duration, language = await asyncio.to_thread(_run)
        return Transcript(text=text, language=language, duration_s=duration, backend=self.name)


class WhisperCppSTT(STTBackend):
    """Резервный слот: whisper.cpp (бинарь/сервер). Объявлен, не реализован."""

    name = "whisper-cpp"

    def available(self) -> tuple[bool, str]:
        return False, "резервный бэкенд: реализация запланирована, когда понадобится headless-уши"


class VoskSTT(STTBackend):
    """Резервный слот: vosk (лёгкий офлайн-STT для слабого железа)."""

    name = "vosk"

    def available(self) -> tuple[bool, str]:
        return False, "резервный бэкенд: реализация запланирована для очень слабого железа"


class MockSTT(STTBackend):
    """Mock для тестов/песочницы: детерминированный текст из конфигурации."""

    name = "mock"

    def __init__(self, text: str = "мок-уши слышат тебя, Кирюша") -> None:
        self.text = text
        self.calls: list[int] = []

    def available(self) -> tuple[bool, str]:
        return True, ""

    async def transcribe(self, audio: bytes, sample_rate: int = 16000) -> Transcript:
        self.calls.append(len(audio))
        return Transcript(text=self.text, language="ru", duration_s=len(audio) / (sample_rate * 2), backend=self.name)


class STTRegistry:
    """Реестр ушей: выбор бэкенда по имени + горячая замена + фолбэк."""

    def __init__(self, backends: list[STTBackend], default: str) -> None:
        self._backends = {b.name: b for b in backends}
        self.default = default if default in self._backends else backends[0].name

    def names(self) -> list[str]:
        """Все зарегистрированные имена."""
        return sorted(self._backends)

    def get(self, name: str | None = None) -> STTBackend:
        """Бэкенд по имени (или дефолт). Горячо: резолвим на каждый вызов."""
        return self._backends.get(name or self.default, self._backends[self.default])

    def pick_available(self, name: str | None = None) -> STTBackend:
        """Первый доступный по цепочке: запрошенный → дефолт → любой доступный.

        :raises RuntimeError: если ушей нет вообще.
        """
        chain = [name or self.default, self.default, *self.names()]
        seen: set[str] = set()
        for candidate in chain:
            if candidate in seen:
                continue
            seen.add(candidate)
            backend = self._backends.get(candidate)
            if backend is None:
                continue
            ok, _reason = backend.available()
            if ok:
                if candidate != (name or self.default):
                    logger.warning("STT: '{}' недоступен, фолбэк на '{}'", name or self.default, candidate)
                return backend
        raise RuntimeError("ни один STT-бэкенд не доступен: поставь voice-зависимости или используй mock")

    def describe(self) -> list[dict[str, Any]]:
        """Сводка для /api/voice/profiles и панели."""
        out = []
        for name in self.names():
            ok, reason = self._backends[name].available()
            out.append({"kind": "stt", "name": name, "available": ok, "reason": reason or None})
        return out


# --------------------------------------------------------------------------- #
#  VAD: обрезка тишины
# --------------------------------------------------------------------------- #
class VAD:
    """Контракт VAD: обрезать тишину по краям."""

    name = "base"

    def available(self) -> tuple[bool, str]:
        return False, "не реализован"

    def trim(self, audio: bytes, sample_rate: int = 16000) -> bytes:
        raise NotImplementedError


class EnergyVAD(VAD):
    """Чистый Python: обрезка краёв по RMS-энергии кадров. Ноль зависимостей."""

    name = "energy"

    def __init__(self, threshold: float = 0.02, frame_ms: int = 30) -> None:
        self.threshold = threshold
        self.frame_ms = frame_ms

    def available(self) -> tuple[bool, str]:
        return True, ""

    def trim(self, audio: bytes, sample_rate: int = 16000) -> bytes:
        """Убирает краевую тишину; если всё тихо — возвращает пустоту."""
        frame = max(160, sample_rate * self.frame_ms // 1000)
        values = struct.unpack(f"<{len(audio) // 2}h", audio[: len(audio) // 2 * 2])
        frames = [values[i : i + frame] for i in range(0, len(values) - frame + 1, frame)]
        if not frames:
            return audio

        def rms(fr: tuple[int, ...]) -> float:
            return math.sqrt(sum(x * x for x in fr) / len(fr)) / 32768.0

        loud = [i for i, fr in enumerate(frames) if rms(fr) >= self.threshold]
        if not loud:
            return b""
        start = loud[0] * frame * 2
        end = (loud[-1] + 1) * frame * 2
        return audio[start:end]


class SileroVAD(VAD):
    """silero-vad (torch): точная обрезка. Ленивый импорт."""

    name = "silero"

    def __init__(self, threshold: float = 0.5) -> None:
        self.threshold = threshold
        self._model = None

    def available(self) -> tuple[bool, str]:
        import importlib.util

        if importlib.util.find_spec("torch") is None:
            return False, "torch не установлен (pip install -e .[voice])"
        return True, ""

    def trim(self, audio: bytes, sample_rate: int = 16000) -> bytes:
        """Обрезает тишу по вероятностям речи silero (упрощённо: по кадрам)."""
        import torch

        if self._model is None:
            self._model, _ = torch.hub.load(
                "snakers4/silero-vad", "silero_vad", trust_repo=True
            )
        samples = torch.frombuffer(bytearray(audio), dtype=torch.int16).float() / 32768.0
        probs = self._model(samples, sample_rate)
        frame = 512 if sample_rate == 16000 else 256
        loud = [i for i, p in enumerate(probs) if float(p) >= self.threshold]
        if not loud:
            return b""
        return audio[loud[0] * frame * 2 : (loud[-1] + 1) * frame * 2]


# --------------------------------------------------------------------------- #
#  WAV-помощники (тесты, HTTP-слой, mock)
# --------------------------------------------------------------------------- #
def read_wav(data: bytes) -> tuple[bytes, int]:
    """Читает WAV: возвращает (PCM16-байты, sample_rate)."""
    with wave.open(io.BytesIO(data), "rb") as wf:
        if wf.getsampwidth() != 2 or wf.getnchannels() != 1:
            raise ValueError("нужен моно-WAV 16 бит")
        return wf.readframes(wf.getnframes()), wf.getframerate()


def write_wav(pcm: bytes, sample_rate: int = 16000) -> bytes:
    """Пакует PCM16 в WAV-байты."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()
