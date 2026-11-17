"""Гварды RVC-слоя 0.7.2-б (ADR-032): NullRvc, контекст/кроссфейд, фолбэк.

Всё герметично: весов RVC, GPU и сети нет. Реальный движок подключается после
пробы F7 (`scripts/rvc_probe.py`), поэтому здесь проверяется конвейер и контракт,
а не качество тембра.
"""

from __future__ import annotations

from array import array
from pathlib import Path

import pytest
import yaml

from lilith_core.config import RvcSettings
from lilith_core.voice import VoiceCore, VoiceProfile
from lilith_core.voice.rvc import (
    NullRvc,
    RvcLayer,
    RvcStream,
    crossfade,
)
from lilith_core.voice.stt import read_wav, write_wav

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RATE = 24000


def pcm(samples: int, value: int = 1000) -> bytes:
    """Ровный PCM16: для проверки длин и стыков достаточно константы."""
    return int(value).to_bytes(2, "little", signed=True) * samples


def wav(samples: int, value: int = 1000, rate: int = RATE) -> bytes:
    return write_wav(pcm(samples, value), rate)


def wav_samples(data: bytes) -> int:
    return len(read_wav(data)[0]) // 2


class _FakeRvc:
    """Фейковый движок: пишет входы, умеет падать и сдвигать амплитуду."""

    name = "fake"

    def __init__(self, *, fail: bool = False, shift: int = 0) -> None:
        self.fail = fail
        self.shift = shift
        self.inputs: list[tuple[int, int]] = []

    def available(self) -> tuple[bool, str]:
        return True, "fake"

    async def convert_pcm(self, samples: bytes, rate: int) -> bytes:
        self.inputs.append((len(samples), rate))
        if self.fail:
            raise RuntimeError("engine-down")
        if not self.shift:
            return samples
        values = array("h")
        values.frombytes(samples)
        shifted = array("h", (max(-32768, min(32767, value + self.shift)) for value in values))
        return shifted.tobytes()


# --------------------------------------------------------------------------- #
# 1. NullRvc и стык кусков                                                      #
# --------------------------------------------------------------------------- #
class TestNullRvc:
    async def test_single_chunk_is_bit_exact(self) -> None:
        source = wav(RATE // 10)          # 100 мс
        stream = RvcStream(NullRvc())
        out = await stream.push(source) + await stream.flush()
        assert b"".join(read_wav(chunk)[0] for chunk in out) == read_wav(source)[0]

    async def test_two_chunks_lose_exactly_one_crossfade(self) -> None:
        stream = RvcStream(NullRvc(), crossfade_ms=50)
        out = await stream.push(wav(RATE // 4)) + await stream.push(wav(RATE // 4))
        out += await stream.flush()
        total = sum(wav_samples(chunk) for chunk in out)
        expected = (RATE // 4) * 2 - int(RATE * 50 / 1000)
        assert total == pytest.approx(expected, abs=2)

    def test_crossfade_equal_power_edges(self) -> None:
        first = pcm(100, 1000)
        second = pcm(100, -1000)
        mixed = crossfade(first, second)
        values = array("h")
        values.frombytes(mixed)
        assert values[0] == 1000          # начало — прошлый кусок
        assert values[-1] == -1000        # конец — новый
        assert all(-1000 <= value <= 1000 for value in values)


# --------------------------------------------------------------------------- #
# 2. Контекст, режим «целиком», защита от простыни                              #
# --------------------------------------------------------------------------- #
class TestPipeline:
    async def test_context_is_prepended_to_engine_input(self) -> None:
        engine = _FakeRvc()
        stream = RvcStream(engine, context_ms=400)
        await stream.push(wav(RATE // 2))
        await stream.push(wav(RATE // 2))
        context_samples = int(RATE * 400 / 1000)
        assert engine.inputs[1][0] == (RATE // 2 + context_samples) * 2
        assert engine.inputs[1][1] == RATE

    async def test_whole_utterance_single_engine_call(self) -> None:
        engine = _FakeRvc()
        stream = RvcStream(engine, whole_utterance=True)
        await stream.push(wav(RATE // 4))
        await stream.push(wav(RATE // 4))
        assert engine.inputs == []              # ничего не конвертировали до flush
        out = await stream.flush()
        assert len(out) == 1 and wav_samples(out[0]) == RATE // 2
        assert engine.inputs[0][0] == (RATE // 2) * 2

    async def test_max_chunk_splits_long_input(self) -> None:
        engine = _FakeRvc()
        stream = RvcStream(engine, max_chunk_seconds=0.01)
        await stream.push(wav(RATE // 4))
        await stream.flush()
        assert len(engine.inputs) > 1

    async def test_rate_change_resets_state(self) -> None:
        engine = _FakeRvc()
        stream = RvcStream(engine)
        await stream.push(wav(RATE // 4, rate=16000))
        await stream.push(wav(RATE // 4, rate=24000))
        assert [rate for _, rate in engine.inputs[:2]] == [16000, 24000]


# --------------------------------------------------------------------------- #
# 3. Фолбэк: движок упал — голос остаётся сырым, но звучит                       #
# --------------------------------------------------------------------------- #
class TestFallback:
    async def test_failed_engine_returns_raw_audio(self) -> None:
        engine = _FakeRvc(fail=True)
        stream = RvcStream(engine)
        source = wav(RATE // 10)
        out = await stream.push(source)
        assert out and read_wav(out[0])[0] == read_wav(source)[0]
        assert await stream.flush() == []

    async def test_failure_does_not_repeat_engine_calls(self) -> None:
        engine = _FakeRvc(fail=True)
        stream = RvcStream(engine)
        await stream.push(wav(RATE // 10))
        await stream.push(wav(RATE // 10))
        assert len(engine.inputs) == 1          # после падения движок не дёргаем

    async def test_second_failure_flushes_held_tail_first(self) -> None:
        engine = _FakeRvc()
        stream = RvcStream(engine, crossfade_ms=50)
        await stream.push(wav(RATE // 4))
        engine.fail = True
        out = await stream.push(wav(RATE // 4))
        assert len(out) == 2                    # придержанный хвост + сырой кусок
        assert stream._pending == b""  # noqa: SLF001 — состояние потока проверяем намеренно


# --------------------------------------------------------------------------- #
# 4. VoiceCore: конверсия до выдачи кусков (значит, до визем)                    #
# --------------------------------------------------------------------------- #
class _FakeBackend:
    name = "fake-tts"

    def __init__(self) -> None:
        self.chunks = [wav(RATE // 20)]

    async def synthesize(self, text: str, profile) -> bytes:
        return self.chunks[0]

    async def stream(self, text: str, profile):
        for chunk in self.chunks:
            yield chunk


class _FakeTTS:
    def __init__(self, backend: _FakeBackend) -> None:
        self.backend = backend
        self.profiles: dict = {}
        self.default_profile = "test"

    def profile(self, name):
        return VoiceProfile(name=name or "test", backend="fake")

    def pick_backend(self, profile):
        return self.backend

    def pick_fallback(self, profile, exclude=None):
        return None

    def describe(self):
        return []


@pytest.fixture
def core(settings, tmp_project: Path) -> VoiceCore:
    settings.features.voice_enabled = True
    settings.voice.packs_manifest = str(tmp_project / "models" / "packs.yaml")
    settings.voice.packs_root = str(tmp_project)
    (tmp_project / "models").mkdir(exist_ok=True)
    (tmp_project / "models" / "packs.yaml").write_text("packs: {}\n", encoding="utf-8")
    return VoiceCore(settings)


class TestVoiceCoreIntegration:
    async def test_disabled_rvc_is_transparent(self, core: VoiceCore) -> None:
        backend = _FakeBackend()
        core.tts = _FakeTTS(backend)
        chunks = [chunk async for chunk in core.stream("привет")]
        assert chunks == backend.chunks

    async def test_enabled_rvc_converts_stream_chunks(self, core: VoiceCore) -> None:
        backend = _FakeBackend()
        core.tts = _FakeTTS(backend)
        core.rvc = RvcLayer(core.settings)
        core.rvc.enabled = True
        core.rvc.engine = _FakeRvc(shift=500)

        chunks = [chunk async for chunk in core.stream("привет")]
        converted = read_wav(chunks[0])[0]
        raw = read_wav(backend.chunks[0])[0]
        assert converted != raw
        assert array("h", converted)[0] == 1500        # 1000 + 500

    async def test_speak_also_gets_timbre(self, core: VoiceCore) -> None:
        backend = _FakeBackend()
        core.tts = _FakeTTS(backend)
        core.rvc = RvcLayer(core.settings)
        core.rvc.enabled = True
        core.rvc.engine = _FakeRvc(shift=-300)

        audio = await core.speak("привет")
        assert array("h", read_wav(audio)[0])[0] == 700

    def test_describe_reports_rvc(self, core: VoiceCore) -> None:
        info = core.describe()["rvc"]
        assert info["enabled"] is False
        assert info["engine"] == "null"
        assert info["context_ms"] == 400
        assert info["crossfade_ms"] == 50


# --------------------------------------------------------------------------- #
# 5. Конфиг                                                                     #
# --------------------------------------------------------------------------- #
class TestConfig:
    def test_defaults_are_safe(self) -> None:
        defaults = RvcSettings()
        assert defaults.enabled is False
        assert defaults.backend == "null"
        assert defaults.context_ms == 400
        assert defaults.crossfade_ms == 50
        assert defaults.whole_utterance is False

    @pytest.mark.parametrize("name", ["config.yaml", "local.yaml"])
    def test_yaml_has_section(self, name: str) -> None:
        data = yaml.safe_load((PROJECT_ROOT / "config" / name).read_text(encoding="utf-8"))
        rvc = data["voice"]["rvc"]
        assert rvc["enabled"] is False
        assert rvc["backend"] == "null"
        assert rvc["context_ms"] == 400
        assert rvc["crossfade_ms"] == 50
