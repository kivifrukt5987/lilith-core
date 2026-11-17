"""RVC-пост-фильтр: тембр поверх голоса — до визем (0.7.2-б, ADR-032).

Зачем отдельный слой: qwen3 говорит чистым RU-голосом, а тембр Эсдес живёт в RVC
(.pth + .index). Конверсия обязана стоять **до** нарезки на чанки и подсчёта визем:
виземы считаются из того PCM, который реально слышит пользователь (инвариант
липсинка, Г-4 приёмки 17.11).

Порядок 0.7.2-б (ратифицирован): сначала **NullRvc + гварды без модели**, затем
проба движка на F7, затем включение флага. Поэтому здесь нет ни одной строки,
требующей весов: движки ``rvc-python``/``aplio`` подключаются после пробы
(``scripts/rvc_probe.py``), а пока честно работают заглушкой.

Контракт движка — один метод: ``convert_pcm(pcm: bytes, rate: int) -> bytes``
(сырой PCM16 mono на той же частоте).
"""

from __future__ import annotations

import sys
from array import array
from typing import Any, Protocol, runtime_checkable

from loguru import logger

from ..config import resolve_path
from .stt import read_wav, write_wav

__all__ = [
    "RvcBackend",
    "NullRvc",
    "RvcStream",
    "RvcLayer",
    "crossfade",
    "RvcError",
]


class RvcError(RuntimeError):
    """Конверсия невозможна: движок/модель/формат. Наружу — фолбэк на сырой голос."""


@runtime_checkable
class RvcBackend(Protocol):
    """Движок конверсии тембра."""

    name: str

    def available(self) -> tuple[bool, str]:
        """(готов, причина) — как у остальных бэкендов горла."""
        ...

    async def convert_pcm(self, pcm: bytes, rate: int) -> bytes:
        """PCM16 mono → PCM16 mono той же частоты."""
        ...


class NullRvc:
    """Заглушка: байты не меняются. Дефолт 0.7.2-б до пробы движка на F7."""

    name = "null"

    def available(self) -> tuple[bool, str]:
        return True, "заглушка (движок не подключён)"

    async def convert_pcm(self, pcm: bytes, rate: int) -> bytes:
        return pcm


# --------------------------------------------------------------------------- #
#  PCM-утилиты: кроссфейд между кусками (щелчки на стыках — главный враг)      #
# --------------------------------------------------------------------------- #
def _to_array(pcm: bytes) -> array:
    """Байты PCM16LE → массив int16 (порядок байт приводится к нативному)."""
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


def _to_bytes(samples: array) -> bytes:
    """Массив int16 → байты PCM16LE."""
    if sys.byteorder == "big":
        copy = array("h", samples)
        copy.byteswap()
        return copy.tobytes()
    return samples.tobytes()


def crossfade(first: bytes, second: bytes) -> bytes:
    """Линейно смешать два равных по длине куска PCM (стык без щелчка)."""
    limit = min(len(first), len(second)) // 2
    if limit <= 0:
        return second
    a = _to_array(first[: limit * 2])
    b = _to_array(second[: limit * 2])
    if limit == 1:
        return second[:2]
    span = limit - 1  # i=0 → ровно первый, i=span → ровно второй
    mixed = array(
        "h",
        ((x * (span - i) + y * i) // span for i, (x, y) in enumerate(zip(a, b))),
    )
    return _to_bytes(mixed)


def _split_tail(data: bytes, tail_bytes: int) -> tuple[bytes, bytes]:
    """Отделить хвост, который ждёт следующего куска для кроссфейда."""
    if tail_bytes <= 0 or len(data) <= tail_bytes:
        return b"", data
    return data[:-tail_bytes], data[-tail_bytes:]


class RvcStream:
    """Фильтр одной реплики: покусочно с контекстом и кроссфейдом либо целиком.

    * ``push(wav)`` — кусок WAV от TTS → список готовых WAV-кусков (может быть пуст:
      последние ``crossfade_ms`` придерживаются до следующего куска);
    * ``flush()`` — добить придержанное в конце реплики.

    Ошибка движка не роняет реплику: помечаем поток сломанным и отдаём сырой голос
    (жёлтый лог); «немой» реплика не становится.
    """

    def __init__(
        self,
        engine: RvcBackend,
        *,
        context_ms: int = 400,
        crossfade_ms: int = 50,
        whole_utterance: bool = False,
        max_chunk_seconds: float = 30.0,
    ) -> None:
        self.engine = engine
        self.context_ms = max(0, int(context_ms))
        self.crossfade_ms = max(0, int(crossfade_ms))
        self.whole_utterance = bool(whole_utterance)
        self.max_chunk_seconds = float(max_chunk_seconds)
        self.calls = 0

        self._rate = 0
        self._prev_input_tail = b""
        self._pending = b""
        self._buffer: list[bytes] = []
        self._failed = False

    # -- служебное ------------------------------------------------------------- #
    def _reset_timing(self, rate: int) -> None:
        self._rate = rate
        self._prev_input_tail = b""
        self._pending = b""

    def _bytes_for(self, ms: int) -> int:
        return int(self._rate * ms / 1000) * 2 if self._rate else 0

    async def _convert(self, pcm: bytes, rate: int) -> bytes:
        """Конверсия с защитой от простыни: длинный кусок режем по ``max_chunk_seconds``."""
        limit = int(self.max_chunk_seconds * rate) * 2
        self.calls += 1
        if limit <= 0 or len(pcm) <= limit:
            return await self.engine.convert_pcm(pcm, rate)
        parts: list[bytes] = []
        for start in range(0, len(pcm), limit):
            parts.append(await self.engine.convert_pcm(pcm[start : start + limit], rate))
        return b"".join(parts)

    # -- конвейер -------------------------------------------------------------- #
    async def push(self, wav: bytes) -> list[bytes]:
        """Кусок WAV от TTS → готовые WAV-куски (или пусто, если придержали хвост)."""
        try:
            pcm, rate = read_wav(wav)
        except Exception as exc:  # noqa: BLE001 — не моно-WAV16: не наше дело
            logger.debug("RVC: кусок не PCM16/WAV ({}), пропускаю как есть", exc)
            return [wav]
        if not pcm:
            return []
        if self._rate and rate != self._rate:
            logger.warning("RVC: частота сменилась {} → {}, состояние потока сброшено",
                           self._rate, rate)
            self._reset_timing(rate)
        if not self._rate:
            self._rate = rate

        if self.whole_utterance:
            self._buffer.append(pcm)
            return []

        if self._failed:
            return [write_wav(pcm, rate)]

        try:
            return await self._push_chunked(pcm, rate)
        except Exception as exc:  # noqa: BLE001 — фолбэк на сырой голос, не тишина
            logger.error("RVC: конверсия упала ({}), дальше отдаю сырой голос", exc)
            self._failed = True
            held = [write_wav(self._pending, rate)] if self._pending else []
            self._pending = b""
            return held + [write_wav(pcm, rate)]

    async def _push_chunked(self, pcm: bytes, rate: int) -> list[bytes]:
        context = self._prev_input_tail[-self._bytes_for(self.context_ms) :] if self._bytes_for(self.context_ms) else b""
        converted = await self._convert(context + pcm, rate)
        # Отрезаем контекст: он был только «разгоном» для модели.
        if context and len(converted) >= len(pcm):
            converted = converted[-len(pcm):]

        fade = self._bytes_for(self.crossfade_ms)
        if self._pending and fade:
            head = converted[:fade]
            n = min(len(self._pending), len(head))
            mixed = crossfade(self._pending[:n], head[:n])
            body, tail = _split_tail(converted[n:], fade)
            self._pending = tail
            emit = mixed + body
        else:
            body, tail = _split_tail(converted, fade)
            self._pending = tail
            emit = body

        self._prev_input_tail = (context + pcm)[-self._bytes_for(self.context_ms) :] if self._bytes_for(self.context_ms) else b""
        return [write_wav(emit, rate)] if emit else []

    async def flush(self) -> list[bytes]:
        """Добить придержанный хвост (или сконвертировать буфер в режиме «целиком»)."""
        if self.whole_utterance:
            if not self._buffer or not self._rate:
                self._buffer.clear()
                return []
            pcm = b"".join(self._buffer)
            self._buffer.clear()
            try:
                converted = await self._convert(pcm, self._rate)
            except Exception as exc:  # noqa: BLE001
                logger.error("RVC: конверсия реплики упала ({}), отдаю сырой голос", exc)
                converted = pcm
            return [write_wav(converted, self._rate)]
        if self._pending and self._rate:
            tail, self._pending = self._pending, b""
            return [write_wav(tail, self._rate)]
        return []


class RvcLayer:
    """Держит движок и настройки; единая точка для VoiceCore."""

    def __init__(self, settings: Any) -> None:
        cfg = settings.voice.rvc
        self.cfg = cfg
        self.enabled: bool = bool(cfg.enabled)
        self.requested: str = cfg.backend
        self.model_dir = resolve_path(cfg.model_dir)
        self.engine: RvcBackend = NullRvc()
        self.reason = "выключен (voice.rvc.enabled=false)"
        if self.enabled:
            if cfg.backend in ("", "null"):
                self.reason = "заглушка NullRvc (backend=null)"
            else:
                self.reason = (
                    f"движок '{cfg.backend}' ещё не подключён — ждёт пробы F7 "
                    f"(scripts/rvc_probe.py); сейчас NullRvc"
                )
                logger.warning("RVC: {}", self.reason)
            if not self.model_dir.is_dir():
                logger.warning("RVC: папка модели '{}' не найдена", self.model_dir)

    def new_stream(self) -> RvcStream | None:
        """Потоковый фильтр одной реплики (None — фильтр выключен)."""
        if not self.enabled:
            return None
        return RvcStream(
            self.engine,
            context_ms=self.cfg.context_ms,
            crossfade_ms=self.cfg.crossfade_ms,
            whole_utterance=self.cfg.whole_utterance,
            max_chunk_seconds=self.cfg.max_chunk_seconds,
        )

    async def process_all(self, wav: bytes) -> bytes:
        """Один готовый WAV (путь ``speak``): сконвертировать целиком."""
        if not self.enabled:
            return wav
        stream = RvcStream(
            self.engine,
            context_ms=self.cfg.context_ms,
            crossfade_ms=self.cfg.crossfade_ms,
            whole_utterance=True,
            max_chunk_seconds=self.cfg.max_chunk_seconds,
        )
        await stream.push(wav)
        chunks = await stream.flush()
        return chunks[0] if chunks else wav

    def describe(self) -> dict[str, Any]:
        """Сводка для панели/диагностики."""
        available, why = self.engine.available()
        return {
            "enabled": self.enabled,
            "requested": self.requested,
            "engine": self.engine.name,
            "available": available,
            "reason": self.reason if self.enabled else why,
            "model_dir": str(self.model_dir),
            "context_ms": self.cfg.context_ms,
            "crossfade_ms": self.cfg.crossfade_ms,
            "whole_utterance": self.cfg.whole_utterance,
        }
