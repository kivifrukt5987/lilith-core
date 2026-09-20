"""Виземы (этап 5 v2): фонемные корзины A/I/U/E/O из PCM-чанков TTS.

Серверная половина анимации рта: каждый аудио-чанк режется на окна ~60 мс,
из окна берутся амплитуда (RMS) и яркость спектра (zero-crossing rate + доля
высокочастотной энергии), и окно попадает в одну из корзин визем VRM
(`aa/ih/ou/ee/oh`) с интенсивностью 0..1. Браузер лишь сглаживает и применяет.

Это фонемные **корзины**, а не фонетика: задача — believable-шевеление рта,
а не распознавание гласных.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

__all__ = ["VisemeFrame", "VISEME_WINDOWS", "WINDOW_MS", "analyze_window", "extract_visemes", "VISEME_TO_VRM"]

#: Окно анализа, мс.
WINDOW_MS = 60

#: Имена корзин (человеческие) и их соответствие blend-именам VRM.
VISEME_WINDOWS: tuple[str, ...] = ("A", "I", "U", "E", "O", "rest")
VISEME_TO_VRM: dict[str, str] = {
    "A": "aa",
    "I": "ih",
    "U": "ou",
    "E": "ee",
    "O": "oh",
    "rest": "aa",  # rest гасится интенсивностью 0
}


@dataclass(slots=True)
class VisemeFrame:
    """Кадр виземы: корзина + интенсивность (0..1)."""

    viseme: str
    intensity: float


def _pcm_samples(chunk: bytes) -> list[int]:
    """PCM16 little-endian -> список отсчётов."""
    n = len(chunk) // 2
    return list(struct.unpack(f"<{n}h", chunk[: n * 2]))


def _rms(samples: list[int]) -> float:
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768.0


def _zcr(samples: list[int]) -> float:
    """Zero-crossing rate: доля смен знака — грубая мера «яркости» звука."""
    if len(samples) < 2:
        return 0.0
    crossings = sum(1 for a, b in zip(samples, samples[1:]) if (a < 0) != (b < 0))
    return crossings / (len(samples) - 1)


def analyze_window(samples: list[int]) -> VisemeFrame:
    """Одно окно PCM -> корзина виземы + интенсивность."""
    amp = _rms(samples)
    if amp < 0.015:  # тишина/пауза: рот закрыт
        return VisemeFrame(viseme="rest", intensity=0.0)

    zcr = _zcr(samples)
    intensity = min(1.0, amp / 0.35)

    # Корзины: открытость по амплитуде, «яркость» по ZCR.
    if zcr < 0.10:
        viseme = "A" if amp > 0.16 else "O"      # низкая яркость: открытые/округлые
    elif zcr < 0.20:
        viseme = "E"                              # средняя яркость
    elif zcr < 0.32:
        viseme = "I" if amp < 0.18 else "E"       # ярко и негромко -> I
    else:
        viseme = "I"                              # очень ярко (сибилянты) -> узкий рот
    if amp > 0.10 and 0.06 < zcr < 0.12:
        viseme = "U"                              # округлое средне-яркое
    return VisemeFrame(viseme=viseme, intensity=round(intensity, 3))


def extract_visemes(pcm: bytes, sample_rate: int = 16000, window_ms: int = WINDOW_MS) -> list[VisemeFrame]:
    """PCM-чанк -> последовательность кадров визем."""
    window = max(64, sample_rate * window_ms // 1000)
    samples = _pcm_samples(pcm)
    frames: list[VisemeFrame] = []
    for i in range(0, len(samples) - window + 1, window):
        frames.append(analyze_window(samples[i : i + window]))
    if not frames and samples:
        frames.append(analyze_window(samples))
    return frames
