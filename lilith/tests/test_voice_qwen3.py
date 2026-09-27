"""Гварды горла 0.7.0 (ADR-027): qwen3-резидент, «шкаф платьев», фолбэки ADR-012.

Всё на фейках: faster-qwen3-tts/torch в тестах НЕ нужны (импорты ленивые — контракт
модуля), GPU не требуется. Что охраняем:
  * _plan: режимы clone/custom/design, ref_text путь-или-строка, xvec-деградация;
  * synthesize/stream: контракт байтов (wav int16), нативные чанки, ошибки наружу;
  * unload/loaded/warmup: VRAM-гигиена «шкафа» и честный пропуск без CUDA;
  * TTSRegistry: корона побеждает профиль, switch_resident выгружает прочих,
    pick_fallback обходит упавшего;
  * VoiceCore: фолбэк по ошибке (ADR-012) — speak и stream (до первого куска);
  * start/stop: мёртвый резидент не роняет сервер;
  * дистрибутивы: qwen-tts и qwen-tts-hf не соседствуют (грабли зависимостей).
"""

from __future__ import annotations

import asyncio
import importlib.metadata
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

from lilith_core.voice import MockTTS, TTSRegistry, VoiceCore, VoiceProfile
from lilith_core.voice.stt import read_wav, write_wav
from lilith_core.voice.tts import TTSBackend
from lilith_core.voice.tts_qwen3 import DEFAULT_MODEL, Qwen3TTS, _to_wav_bytes

SR = 24000


def tone(seconds: float = 0.1, freq: float = 440.0) -> np.ndarray:
    t = np.arange(int(SR * seconds), dtype="float32") / SR
    return (0.3 * np.sin(2 * np.pi * freq * t)).astype("float32")


class FakeQwenModel:
    """Мини-двойник FasterQwen3TTS: те же сигнатуры, ноль VRAM."""

    def __init__(self, stream_chunks: int = 2, fail_stream: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.stream_chunks = stream_chunks
        self.fail_stream = fail_stream

    def _record(self, method: str, text: str, kwargs: dict[str, Any]) -> None:
        self.calls.append((method, {"text": text, **kwargs}))

    def generate_voice_clone(self, text: str, **kw: Any) -> tuple[list[np.ndarray], int]:
        self._record("clone", text, kw)
        return [tone()], SR

    def generate_custom_voice(self, text: str, **kw: Any) -> tuple[list[np.ndarray], int]:
        self._record("custom", text, kw)
        return [tone()], SR

    def generate_voice_design(self, text: str, **kw: Any) -> tuple[list[np.ndarray], int]:
        self._record("design", text, kw)
        return [tone()], SR

    def generate_voice_clone_streaming(self, text: str, chunk_size: int = 8, **kw: Any):
        self._record("clone_stream", text, {"chunk_size": chunk_size, **kw})
        for i in range(self.stream_chunks):
            if self.fail_stream and i == self.stream_chunks - 1:
                raise RuntimeError("CUDA OOM (имитация)")
            yield tone(0.05), SR, {"step": (i + 1) * chunk_size}

    def generate_custom_voice_streaming(self, text: str, chunk_size: int = 8, **kw: Any):
        self._record("custom_stream", text, {"chunk_size": chunk_size, **kw})
        yield tone(0.05), SR, {"step": chunk_size}

    def generate_voice_design_streaming(self, text: str, chunk_size: int = 8, **kw: Any):
        self._record("design_stream", text, {"chunk_size": chunk_size, **kw})
        yield tone(0.05), SR, {"step": chunk_size}


@pytest.fixture
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> tuple[Qwen3TTS, FakeQwenModel]:
    """Qwen3TTS с подменённой загрузкой: фейк вместо faster-qwen3-tts."""
    backend = Qwen3TTS()
    model = FakeQwenModel()
    monkeypatch.setattr(backend, "_load_sync", lambda model_id: model)
    return backend, model


def clone_profile(reference: str = "personas/lilith/voice/reference.wav", **extra: Any) -> VoiceProfile:
    spec: dict[str, Any] = {"model": DEFAULT_MODEL, "mode": "clone", "language": "Russian", **extra}
    return VoiceProfile(name="lilith-soul", backend="qwen3", reference=reference, extra=spec)


class TestToWavBytes:
    def test_roundtrip(self) -> None:
        wav = _to_wav_bytes(tone(0.05), SR)
        pcm, rate = read_wav(wav)
        assert rate == SR
        assert len(pcm) == int(SR * 0.05) * 2  # int16 mono

    def test_clipping(self) -> None:
        wav = _to_wav_bytes(np.array([2.0, -2.0], dtype="float32"), SR)
        pcm, _ = read_wav(wav)
        assert pcm == b"\xff\x7f\x01\x80"  # clamp к ±32767 без переполнения (LE int16)


class TestPlan:
    def test_clone_with_ref_text_file(self, tmp_path: Path) -> None:
        ref_txt = tmp_path / "reference.txt"
        ref_txt.write_text("Точная транскрипция души.\n", encoding="utf-8")
        plan = Qwen3TTS._plan(clone_profile(ref_text=str(ref_txt)))
        assert plan["method"] == "generate_voice_clone"
        assert plan["method_stream"] == "generate_voice_clone_streaming"
        assert plan["kwargs"]["ref_text"] == "Точная транскрипция души."
        assert "xvec_only" not in plan["kwargs"]
        assert plan["kwargs"]["language"] == "Russian"

    def test_clone_ref_text_inline(self) -> None:
        plan = Qwen3TTS._plan(clone_profile(ref_text="строка прямо в конфиге"))
        assert plan["kwargs"]["ref_text"] == "строка прямо в конфиге"

    def test_clone_without_ref_text_degrades_to_xvec(self) -> None:
        plan = Qwen3TTS._plan(clone_profile())
        assert plan["kwargs"].get("xvec_only") is True

    def test_clone_xvec_explicit(self) -> None:
        plan = Qwen3TTS._plan(clone_profile(ref_text="транскрипция", xvec_only=False))
        assert plan["kwargs"]["xvec_only"] is False

    def test_clone_missing_reference_raises(self) -> None:
        with pytest.raises(ValueError, match="без reference"):
            Qwen3TTS._plan(clone_profile(reference=""))

    def test_custom_mode(self) -> None:
        profile = VoiceProfile(
            name="cv", backend="qwen3", voice="Sohee",
            extra={"mode": "custom", "speaker": "Vivian", "instruct": "злая"},
        )
        plan = Qwen3TTS._plan(profile)
        assert plan["method"] == "generate_custom_voice"
        assert plan["kwargs"]["speaker"] == "Vivian"
        assert plan["kwargs"]["instruct"] == "злая"

    def test_custom_speaker_from_voice_field(self) -> None:
        profile = VoiceProfile(name="cv", backend="qwen3", voice="Sohee", extra={"mode": "custom"})
        assert Qwen3TTS._plan(profile)["kwargs"]["speaker"] == "Sohee"

    def test_design_requires_instruct(self) -> None:
        profile = VoiceProfile(name="d", backend="qwen3", extra={"mode": "design"})
        with pytest.raises(ValueError, match="без instruct"):
            Qwen3TTS._plan(profile)

    def test_unknown_mode_raises(self) -> None:
        profile = VoiceProfile(name="x", backend="qwen3", extra={"mode": "karaoke"})
        with pytest.raises(ValueError, match="неизвестный mode"):
            Qwen3TTS._plan(profile)

    def test_sampling_kwargs_transit(self) -> None:
        plan = Qwen3TTS._plan(clone_profile(ref_text="т", temperature=0.7, top_p=0.9, chunk_size=4))
        assert plan["kwargs"]["temperature"] == 0.7
        assert plan["kwargs"]["top_p"] == 0.9
        assert plan["chunk_size"] == 4


class TestAvailable:
    def test_graceful_without_package(self) -> None:
        """Без faster-qwen3-tts/CUDA available() честно объясняется, а не падает."""
        ok, reason = Qwen3TTS().available()
        assert isinstance(ok, bool)
        if not ok:
            assert reason  # причина для панели и фолбэк-цепочки


@pytest.mark.asyncio
class TestSynthesizeStream:
    async def test_synthesize_clone_wav(self, fake_backend: tuple[Qwen3TTS, FakeQwenModel]) -> None:
        backend, model = fake_backend
        audio = await backend.synthesize("Привет, Кирюша.", clone_profile(ref_text="т"))
        pcm, rate = read_wav(audio)
        assert rate == SR and pcm
        assert model.calls[0][0] == "clone"

    async def test_stream_native_chunks(self, fake_backend: tuple[Qwen3TTS, FakeQwenModel]) -> None:
        backend, model = fake_backend
        chunks = [c async for c in backend.stream("Раз. Два. Три.", clone_profile(ref_text="т"))]
        assert len(chunks) == model.stream_chunks  # нативные чанки, НЕ по предложениям
        for chunk in chunks:
            pcm, rate = read_wav(chunk)
            assert rate == SR and pcm
        assert model.calls[0][0] == "clone_stream"
        assert model.calls[0][1]["chunk_size"] == 8

    async def test_stream_error_surfaces(self, monkeypatch: pytest.MonkeyPatch) -> None:
        backend = Qwen3TTS()
        model = FakeQwenModel(stream_chunks=3, fail_stream=True)
        monkeypatch.setattr(backend, "_load_sync", lambda model_id: model)
        got: list[bytes] = []
        with pytest.raises(RuntimeError, match="OOM"):
            async for chunk in backend.stream("текст", clone_profile(ref_text="т")):
                got.append(chunk)
        assert len(got) == 2  # ошибка на последнем чанке — первые доехали

    async def test_stream_sentences_fallback_path(self, fake_backend: tuple[Qwen3TTS, FakeQwenModel]) -> None:
        backend, model = fake_backend
        chunks = [c async for c in backend.stream_sentences("Раз. Два.", clone_profile(ref_text="т"))]
        assert len(chunks) == 2
        assert [c[0] for c in model.calls] == ["clone", "clone"]


class TestUnloadWarmup:
    def test_loaded_and_unload(self, fake_backend: tuple[Qwen3TTS, FakeQwenModel]) -> None:
        backend, _ = fake_backend
        assert backend.loaded() is False
        backend._models[DEFAULT_MODEL] = object()
        assert backend.loaded() is True
        backend.unload()
        assert backend.loaded() is False
        backend.unload()  # идемпотентно

    @pytest.mark.asyncio
    async def test_warmup_skips_gracefully_without_gpu(self) -> None:
        """Нет CUDA/пакета — warmup логирует пропуск и НЕ бросается (старт сервера жив)."""
        await Qwen3TTS().warmup()  # не должно raising

    @pytest.mark.asyncio
    async def test_warmup_loads_when_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        backend = Qwen3TTS()
        model = FakeQwenModel()

        def fake_load(model_id: str):
            backend._models[model_id] = model  # повторяет поведение настоящей _load_sync
            return model

        monkeypatch.setattr(backend, "available", lambda: (True, ""))
        monkeypatch.setattr(backend, "_load_sync", fake_load)
        await backend.warmup("custom/model-id")
        assert backend._models.get("custom/model-id") is model
        assert backend.loaded() is True


class StubBackend(TTSBackend):
    """Учётный двойник для реестра: доступность/загрузка/выгрузка под контролем."""

    def __init__(self, name: str, ok: bool = True, is_loaded: bool = False) -> None:
        self.name = name
        self._ok = ok
        self._loaded = is_loaded
        self.unload_calls = 0

    def available(self) -> tuple[bool, str]:
        return (self._ok, "" if self._ok else f"{self.name} недоступен")

    def loaded(self) -> bool:
        return self._loaded

    def unload(self) -> None:
        self.unload_calls += 1
        self._loaded = False

    async def synthesize(self, text: str, profile: VoiceProfile) -> bytes:
        return write_wav(b"\x00\x00" * 100, 16000)


class FailingBackend(StubBackend):
    async def synthesize(self, text: str, profile: VoiceProfile) -> bytes:
        raise RuntimeError("движок упал в бою")

    async def stream(self, text: str, profile: VoiceProfile):
        raise RuntimeError("движок упал в бою")
        yield b""  # pragma: no cover — чтобы оставаться генератором


def stub_registry(resident: str = "crown") -> tuple[TTSRegistry, dict[str, StubBackend]]:
    crown = StubBackend("crown", ok=True, is_loaded=True)
    dress = StubBackend("dress", ok=True, is_loaded=True)
    dead = StubBackend("dead", ok=False)
    profiles = {
        "p-dress": VoiceProfile(name="p-dress", backend="dress"),
        "p-dead": VoiceProfile(name="p-dead", backend="dead"),
    }
    registry = TTSRegistry([crown, dress, dead, MockTTS()], profiles, "p-dress", resident=resident)
    return registry, {"crown": crown, "dress": dress, "dead": dead}


class TestRegistryResident:
    def test_crown_wins_over_profile(self) -> None:
        registry, stubs = stub_registry("crown")
        assert registry.pick_backend(registry.profile("p-dress")).name == "crown"

    def test_crown_unavailable_falls_to_profile_chain(self) -> None:
        registry, stubs = stub_registry("dead")  # резидент назначен, но недоступен
        assert registry.pick_backend(registry.profile("p-dress")).name == "dress"

    def test_unknown_resident_dropped(self) -> None:
        registry, _ = stub_registry("crown")
        registry2 = TTSRegistry(
            [MockTTS()], {"m": VoiceProfile(name="m", backend="mock")}, "m", resident="ghost"
        )
        assert registry2.resident == ""

    def test_switch_resident_unloads_others(self) -> None:
        registry, stubs = stub_registry("crown")
        info = registry.switch_resident("dress")
        assert info["resident"] == "dress"
        assert registry.resident == "dress"
        assert stubs["crown"].unload_calls == 1
        assert stubs["dress"].unload_calls == 0  # новую корону не выгружаем
        assert info["available"] is True

    def test_switch_to_unknown_raises(self) -> None:
        registry, _ = stub_registry("crown")
        with pytest.raises(KeyError, match="не в реестре"):
            registry.switch_resident("gucci")

    def test_switch_to_none_removes_crown(self) -> None:
        registry, stubs = stub_registry("crown")
        info = registry.switch_resident("none")
        assert info["resident"] is None and registry.resident == ""
        assert stubs["crown"].unload_calls == 1

    def test_engine_info(self) -> None:
        registry, _ = stub_registry("crown")
        info = registry.engine_info()
        assert info["resident"] == "crown"
        assert info["engines"]["crown"] == {"available": True, "reason": None, "loaded": True}
        assert info["engines"]["dead"]["available"] is False

    def test_pick_fallback_excludes_failed(self) -> None:
        registry, _ = stub_registry("crown")
        fallback = registry.pick_fallback(registry.profile("p-dress"), exclude="crown")
        assert fallback is not None and fallback.name == "dress"
        assert registry.pick_fallback(registry.profile("p-dead"), exclude="dead") is not None

    def test_describe_contains_resident_kind(self) -> None:
        registry, _ = stub_registry("crown")
        kinds = {row["kind"] for row in registry.describe()}
        assert "tts-resident" in kinds


class TestCloset:
    def test_closet_slots_honest(self) -> None:
        from lilith_core.voice.closet import CosyVoice2TTS, FishSpeechTTS

        for stub in (CosyVoice2TTS(), FishSpeechTTS()):
            ok, reason = stub.available()
            assert ok is False
            assert "шкаф" in reason and "ADR-027" in reason
            assert stub.loaded() is False
            stub.unload()  # не бросается


@pytest.fixture
def voice_settings(settings, tmp_project):
    """Настройки с включённым голосом и локальным манифестом паков (паттерн test_voice_app)."""
    settings.features.voice_enabled = True
    settings.voice.packs_manifest = str(tmp_project / "models" / "packs.yaml")
    settings.voice.packs_root = str(tmp_project)
    (tmp_project / "models").mkdir(exist_ok=True)
    (tmp_project / "models" / "packs.yaml").write_text("packs: {}\n", encoding="utf-8")
    return settings


@pytest.mark.asyncio
class TestVoiceCoreFallback:
    async def test_speak_falls_back_on_error(self, voice_settings) -> None:
        voice_settings.voice.profiles = {"boom": {"backend": "qwen3"}}
        core = VoiceCore(voice_settings)
        failing = FailingBackend("qwen3")
        core.tts._backends["qwen3"] = failing  # noqa: SLF001 — инъекция в гварде
        audio = await core.speak("живы?", "boom")
        pcm, rate = read_wav(audio)  # доехал mock-фолбэк (ADR-012)
        assert rate == 16000 and pcm

    async def test_stream_falls_back_before_first_chunk(self, voice_settings) -> None:
        voice_settings.voice.profiles = {"boom": {"backend": "qwen3"}}
        core = VoiceCore(voice_settings)
        core.tts._backends["qwen3"] = FailingBackend("qwen3")  # noqa: SLF001
        chunks = [c async for c in core.stream("живы?", "boom")]
        assert chunks and all(read_wav(c)[1] == 16000 for c in chunks)

    async def test_stream_midway_error_propagates(self, voice_settings) -> None:
        """После первого куска реплика НЕ перезапускается (иначе скажет дважды)."""

        class MidwayBackend(StubBackend):
            async def stream(self, text: str, profile: VoiceProfile):
                yield write_wav(b"\x00\x00" * 100, 16000)
                raise RuntimeError("середина реплики")

        voice_settings.voice.profiles = {"mid": {"backend": "qwen3"}}
        core = VoiceCore(voice_settings)
        core.tts._backends["qwen3"] = MidwayBackend("qwen3")  # noqa: SLF001
        with pytest.raises(RuntimeError, match="середина"):
            async for _ in core.stream("текст", "mid"):
                pass

    async def test_start_stop_with_dead_resident(self, voice_settings) -> None:
        """Мёртвый резидент (нет GPU/пакета) не роняет ни старт, ни остановку."""
        voice_settings.voice.tts_resident = "qwen3"
        core = VoiceCore(voice_settings)
        await core.start()
        await asyncio.sleep(0)  # дать фоновой задаче ход
        await core.stop()


class TestVoiceCoreEngine:
    def test_switch_engine_and_status(self, voice_settings) -> None:
        core = VoiceCore(voice_settings)
        info = core.switch_engine("mock")
        assert info["resident"] == "mock"
        status = core.engine_status()
        assert status["resident"] == "mock"
        assert status["configured"] in (None, "")
        assert status["engines"]["mock"]["available"] is True


class TestDualDistributionGuard:
    def test_no_dual_qwen_tts_distribution(self) -> None:
        """qwen-tts и qwen-tts-hf дают один пакет qwen_tts — соседство ломает импорты."""
        installed = {
            (dist.metadata["Name"] or "").lower()
            for dist in importlib.metadata.distributions()
            if dist.metadata.get("Name")
        }
        assert not ({"qwen-tts", "qwen-tts-hf"} <= installed), (
            "в окружении стоят и qwen-tts, и qwen-tts-hf — оставь одно "
            "(faster-qwen3-tts тянет qwen-tts-hf; upstream qwen-tts не нужен)"
        )


class TestConfigSurface:
    def test_voice_settings_has_resident_field(self) -> None:
        from lilith_core.config import VoiceSettings

        assert VoiceSettings().tts_resident == ""

    def test_shipped_config_declares_qwen3_resident(self) -> None:
        """config/config.yaml: резидент и души объявлены (ADR-027 в конфиге, не только в коде)."""
        project_root = Path(__file__).resolve().parents[1]
        data = yaml.safe_load((project_root / "config" / "config.yaml").read_text(encoding="utf-8"))
        voice = data["voice"]
        assert voice["tts_resident"] == "qwen3"
        assert voice["profiles"]["lilith-soul"]["backend"] == "qwen3"
        assert voice["profiles"]["lilith-soul"]["reference"] == "personas/lilith/voice/reference.wav"
        assert voice["profiles"]["olya-soul"]["backend"] == "qwen3"
        brain = data["brain"]["profiles"]
        assert brain["gm"]["base_url"].endswith(":1237/v1")  # ADR-028
