"""Тесты ушей: VAD, WAV-хелперы, реестр STT с фолбэком."""

from __future__ import annotations

import math
import struct

import pytest

from lilith_core.voice import (
    EnergyVAD,
    FasterWhisperSTT,
    MockSTT,
    STTRegistry,
    VoskSTT,
    WhisperCppSTT,
    read_wav,
    write_wav,
)


def tone_pcm(ms: int = 200, freq: float = 440.0, rate: int = 16000, amp: int = 12000) -> bytes:
    """Синтетический «голос» (тон) в PCM16."""
    n = rate * ms // 1000
    return struct.pack(f"<{n}h", *[int(amp * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)])


def silence_pcm(ms: int = 300, rate: int = 16000) -> bytes:
    """Тишина в PCM16."""
    return b"\x00\x00" * (rate * ms // 1000)


class TestWavHelpers:
    """WAV туда-обратно."""

    def test_roundtrip(self) -> None:
        pcm = tone_pcm(100)
        wav = write_wav(pcm, 16000)
        back, rate = read_wav(wav)
        assert rate == 16000
        assert back == pcm

    def test_read_rejects_stereo(self) -> None:
        import io
        import wave

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00\x00" * 160)
        with pytest.raises(ValueError, match="моно"):
            read_wav(buf.getvalue())


class TestEnergyVAD:
    """Обрезка краевой тишины без зависимостей."""

    def test_trims_edges(self) -> None:
        audio = silence_pcm(300) + tone_pcm(200) + silence_pcm(300)
        trimmed = EnergyVAD(threshold=0.05).trim(audio)
        assert 0 < len(trimmed) < len(audio)
        assert len(trimmed) <= len(tone_pcm(200)) + 2 * 30 * 16000 // 1000 * 2

    def test_all_silence_gives_empty(self) -> None:
        assert EnergyVAD(threshold=0.05).trim(silence_pcm(500)) == b""

    def test_no_silence_untouched(self) -> None:
        audio = tone_pcm(300)
        trimmed = EnergyVAD(threshold=0.05).trim(audio)
        assert trimmed == audio[: len(trimmed)]
        assert len(trimmed) >= len(audio) - 2 * 30 * 16000 // 1000 * 2

    def test_available(self) -> None:
        assert EnergyVAD().available()[0] is True


class TestBackendsAvailability:
    """Доступность бэкендов в песочнице и резервные слоты."""

    def test_faster_whisper_reports_reason(self) -> None:
        ok, reason = FasterWhisperSTT().available()
        import importlib.util

        if importlib.util.find_spec("faster_whisper") is None:
            assert ok is False
            assert "faster-whisper" in reason
        else:
            assert ok is True

    def test_reserves_are_declared_but_unavailable(self) -> None:
        for backend in (WhisperCppSTT(), VoskSTT()):
            ok, reason = backend.available()
            assert ok is False
            assert "резерв" in reason

    def test_mock_always_available(self) -> None:
        assert MockSTT().available()[0] is True


@pytest.mark.asyncio
class TestMockSTT:
    """Mock-уши."""

    async def test_transcribe(self) -> None:
        stt = MockSTT(text="привет, это mock")
        transcript = await stt.transcribe(tone_pcm(100))
        assert transcript.text == "привет, это mock"
        assert transcript.backend == "mock"
        assert transcript.duration_s > 0
        assert stt.calls == [len(tone_pcm(100))]


class TestSTTRegistry:
    """Горячий выбор и фолбэк цепочкой."""

    def make(self, default: str = "mock") -> STTRegistry:
        return STTRegistry([FasterWhisperSTT(), WhisperCppSTT(), VoskSTT(), MockSTT()], default=default)

    def test_get_by_name_and_default(self) -> None:
        registry = self.make()
        assert registry.get().name == "mock"
        assert registry.get("vosk").name == "vosk"
        assert registry.get("нету").name == "mock"  # неизвестное имя -> дефолт

    def test_pick_available_falls_back(self) -> None:
        registry = self.make(default="mock")
        backend = registry.pick_available("vosk")  # резерв недоступен
        assert backend.name == "mock"

    def test_pick_available_prefers_requested(self) -> None:
        registry = self.make()
        assert registry.pick_available("mock").name == "mock"

    def test_pick_available_raises_when_none(self) -> None:
        registry = STTRegistry([WhisperCppSTT(), VoskSTT()], default="whisper-cpp")
        with pytest.raises(RuntimeError, match="STT"):
            registry.pick_available()

    def test_describe(self) -> None:
        rows = self.make().describe()
        names = {row["name"] for row in rows}
        assert {"faster-whisper", "whisper-cpp", "vosk", "mock"} <= names
        assert all({"kind", "available", "reason"} <= set(row) for row in rows)

    def test_hot_default_swap(self) -> None:
        registry = self.make(default="mock")
        registry.default = "vosk"
        assert registry.get().name == "vosk"
