"""PCM-конвейер лица (этап 6): ресемплинг, нормализация чанков, смещения.

Контракт аудио для ``/ws/face/producer`` зафиксирован архитектором (блок A):

* **raw PCM int16 mono**, little-endian, без контейнера WAV;
* чанк ровно :data:`CHUNK_BYTES` = 2048 байт (1024 сэмпла);
* sample rate **фиксируется на сервере** (``face.producer_sample_rate``, 24 kHz)
  и объявляется клиенту в кадре ``hello``; сервер **не** предполагает, что TTS
  уже отдаёт 24 kHz — он приводит поток к целевой частоте сам;
* паки голосов стандартизуются до 24 kHz в ``personas/<id>/voice.yaml``.

Unity-клиент кладёт байты в ``AudioClip.SetData`` и **сам** считает виземы из PCM
(``VisemeDriver``); серверная разметка визем уходит только тем, кто попросил её
в ``hello`` полем ``want_server_visemes`` (фолбэк для веб-панели).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterator

from .stt import read_wav

__all__ = [
    "CHUNK_BYTES",
    "DEFAULT_SAMPLE_RATE",
    "AudioChunk",
    "PcmChunker",
    "resample_pcm16",
    "wav_to_pcm",
]

#: Размер аудио-чанка в байтах (ровно столько уходит в одном кадре ``audio``).
CHUNK_BYTES: int = 2048

#: Целевая частота дискретизации продюсера лица (A2: фиксируем 24 kHz).
DEFAULT_SAMPLE_RATE: int = 24000

#: Байт на сэмпл: int16 little-endian.
BYTES_PER_SAMPLE: int = 2


@dataclass(slots=True, frozen=True)
class AudioChunk:
    """Один аудио-чанк продюсера: данные + служебная нумерация.

    :param data: raw PCM int16 mono, ровно :data:`CHUNK_BYTES` (кроме хвоста).
    :param seq: порядковый номер чанка внутри реплики (с нуля).
    :param byte_offset: смещение чанка от начала реплики в байтах.
    :param offset_ms: то же в миллисекундах — для синхронизации визем и эмоций.
    :param final: ``True`` для последнего (возможно укороченного) чанка реплики.
    """

    data: bytes
    seq: int
    byte_offset: int
    offset_ms: int
    final: bool = False

    @property
    def samples(self) -> int:
        """Число сэмплов в чанке."""
        return len(self.data) // BYTES_PER_SAMPLE

    @property
    def duration_ms(self) -> int:
        """Длительность чанка в миллисекундах (округление вниз)."""
        if self.sample_rate <= 0:
            return 0
        return self.samples * 1000 // self.sample_rate

    #: Частота нужна только для ``duration_ms``; хранится отдельно, чтобы
    #: ``AudioChunk`` оставался дешёвым значением.
    sample_rate: int = DEFAULT_SAMPLE_RATE


def resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Линейный ресемплинг PCM16 mono.

    Намеренно без numpy/scipy: слой лица обязан работать в голом ``.[dev]``-окружении,
    а качество «для шевеления рта» линейная интерполяция даёт достаточное.

    :param pcm: исходные байты int16 little-endian.
    :param src_rate: исходная частота.
    :param dst_rate: целевая частота.
    :return: байты int16 little-endian на целевой частоте.
    """
    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError("частоты дискретизации должны быть положительными")
    if src_rate == dst_rate or not pcm:
        return pcm

    n_src = len(pcm) // BYTES_PER_SAMPLE
    if n_src < 2:
        return pcm[: n_src * BYTES_PER_SAMPLE]

    samples = struct.unpack(f"<{n_src}h", pcm[: n_src * BYTES_PER_SAMPLE])
    ratio = src_rate / dst_rate
    n_dst = max(1, int(round(n_src / ratio)))
    last = n_src - 1
    out: list[int] = []
    for i in range(n_dst):
        pos = i * ratio
        idx = int(pos)
        if idx >= last:
            out.append(samples[last])
            continue
        frac = pos - idx
        value = samples[idx] * (1.0 - frac) + samples[idx + 1] * frac
        clamped = -32768 if value < -32768 else (32767 if value > 32767 else int(round(value)))
        out.append(clamped)
    return struct.pack(f"<{len(out)}h", *out)


def wav_to_pcm(chunk_wav: bytes, target_rate: int = DEFAULT_SAMPLE_RATE) -> tuple[bytes, int]:
    """WAV-кусок TTS → (raw PCM int16 mono на целевой частоте, целевая частота).

    :param chunk_wav: моно-WAV 16 бит от TTS-бэкенда.
    :param target_rate: к какой частоте приводить поток.
    :raises ValueError: если WAV не моно-16бит (см. :func:`read_wav`).
    """
    pcm, rate = read_wav(chunk_wav)
    return resample_pcm16(pcm, rate, target_rate), target_rate


class PcmChunker:
    """Режет поток PCM на ровные чанки по :data:`CHUNK_BYTES` байт.

    Живёт в пределах одной реплики (``utterance``): счётчики ``seq`` и
    ``byte_offset`` общие для всего потока, поэтому Unity может восстанавливать
    порядок даже если кадры придут пачкой.

    Пример::

        chunker = PcmChunker(utterance_id="u-1")
        for chunk in chunker.push(pcm_from_tts):
            send(audio_frame(chunk))
        send(audio_frame(chunker.flush()))   # хвост
    """

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        chunk_bytes: int = CHUNK_BYTES,
        utterance_id: str = "",
    ) -> None:
        if chunk_bytes <= 0 or chunk_bytes % BYTES_PER_SAMPLE:
            raise ValueError("chunk_bytes должен быть положительным и кратным 2")
        self.sample_rate = sample_rate
        self.chunk_bytes = chunk_bytes
        self.utterance_id = utterance_id
        self._buf = bytearray()
        self._seq = 0
        self._emitted = 0

    # -- состояние ------------------------------------------------------------ #
    @property
    def pending(self) -> int:
        """Сколько байт ещё не ушло чанками."""
        return len(self._buf)

    @property
    def emitted_bytes(self) -> int:
        """Сколько байт уже отправлено (для ``offset_ms``)."""
        return self._emitted

    @property
    def chunks_sent(self) -> int:
        """Сколько чанков уже выдано (для кадра ``done``)."""
        return self._seq

    def _offset_ms(self, byte_offset: int) -> int:
        """Байты от начала реплики → миллисекунды."""
        samples = byte_offset // BYTES_PER_SAMPLE
        return samples * 1000 // self.sample_rate if self.sample_rate else 0

    def _make(self, data: bytes, final: bool) -> AudioChunk:
        """Собрать чанк и сдвинуть счётчики."""
        byte_offset = self._emitted
        chunk = AudioChunk(
            data=data,
            seq=self._seq,
            byte_offset=byte_offset,
            offset_ms=self._offset_ms(byte_offset),
            final=final,
            sample_rate=self.sample_rate,
        )
        self._seq += 1
        self._emitted += len(data)
        return chunk

    # -- работа --------------------------------------------------------------- #
    def push(self, pcm: bytes) -> Iterator[AudioChunk]:
        """Добавить PCM и выдать все наполнившиеся чанки (лениво)."""
        if pcm:
            self._buf.extend(pcm)
        while len(self._buf) >= self.chunk_bytes:
            data = bytes(self._buf[: self.chunk_bytes])
            del self._buf[: self.chunk_bytes]
            yield self._make(data, final=False)

    def flush(self) -> AudioChunk | None:
        """Отдать хвост (короче ``chunk_bytes``) или ``None``, если буфер пуст."""
        if not self._buf:
            return None
        data = bytes(self._buf)
        self._buf.clear()
        return self._make(data, final=True)

    def close(self) -> Iterator[AudioChunk]:
        """``flush`` итератором — удобно в ``yield from``."""
        tail = self.flush()
        if tail is not None:
            yield tail
