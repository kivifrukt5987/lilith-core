"""Тесты горла: предложения, mock-TTS, реестр профилей и фолбэков."""

from __future__ import annotations

import pytest

from lilith_core.voice import (
    EdgeTTS,
    MockTTS,
    SileroTTS,
    TTSRegistry,
    VoiceCore,
    VoiceProfile,
    ZeroShotTTS,
    split_sentences,
)


def registry(default: str = "lilith") -> TTSRegistry:
    profiles = {
        "lilith": VoiceProfile(name="lilith", backend="silero", voice="kseniya"),
        "clone": VoiceProfile(name="clone", backend="zero-shot", reference="voices/x/reference.wav"),
        "cloud": VoiceProfile(name="cloud", backend="edge"),
        "mocky": VoiceProfile(name="mocky", backend="mock"),
    }
    return TTSRegistry([SileroTTS(), EdgeTTS(), ZeroShotTTS(), MockTTS()], profiles, default)


class TestSplitSentences:
    """Резка реплики для стриминга речи."""

    def test_basic(self) -> None:
        assert split_sentences("Привет! Как дела? Мур.") == ["Привет!", "Как дела?", "Мур."]

    def test_no_punctuation_single(self) -> None:
        assert split_sentences("просто текст") == ["просто текст"]

    def test_empty(self) -> None:
        assert split_sentences("   ") == []

    def test_ellipsis(self) -> None:
        assert split_sentences("Ну… ладно.") == ["Ну…", "ладно."]


@pytest.mark.asyncio
class TestMockTTS:
    """Mock-горло: wav-заголовок и стриминг по предложениям."""

    async def test_synthesize_wav(self) -> None:
        from lilith_core.voice import read_wav

        audio = await MockTTS().synthesize("привет", VoiceProfile(name="x", backend="mock"))
        pcm, rate = read_wav(audio)
        assert rate == 16000
        assert pcm

    async def test_stream_per_sentence(self) -> None:
        tts = MockTTS()
        chunks = [c async for c in tts.stream("Раз. Два. Три.", VoiceProfile(name="x", backend="mock"))]
        assert len(chunks) == 3
        assert tts.spoken == ["Раз.", "Два.", "Три."]

    async def test_deterministic(self) -> None:
        tts = MockTTS()
        profile = VoiceProfile(name="x", backend="mock")
        a = await tts.synthesize("один текст", profile)
        b = await tts.synthesize("один текст", profile)
        assert a == b


class TestTTSRegistry:
    """Профили, горячая замена, фолбэк цепочкой."""

    def test_profile_by_name_and_default(self) -> None:
        reg = registry()
        assert reg.profile().name == "lilith"
        assert reg.profile("clone").backend == "zero-shot"
        assert reg.profile("нету").name == "lilith"

    def test_pick_backend_falls_back_to_mock(self) -> None:
        reg = registry()
        backend = reg.pick_backend(reg.profile("lilith"))  # silero недоступен в песочнице
        assert backend.name == "mock"

    def test_pick_backend_honours_available(self) -> None:
        reg = registry()
        assert reg.pick_backend(reg.profile("mocky")).name == "mock"

    def test_pick_backend_raises_when_none(self) -> None:
        reg = TTSRegistry([SileroTTS(), EdgeTTS()], {"a": VoiceProfile(name="a", backend="silero")}, "a")
        with pytest.raises(RuntimeError, match="TTS"):
            reg.pick_backend(reg.profile("a"))

    def test_hot_profile_swap(self) -> None:
        """Смена профилей на лету: реестр перечитывает словарь без перезапуска."""
        reg = registry()
        assert reg.profile().backend == "silero"
        reg.profiles = {"lilith": VoiceProfile(name="lilith", backend="mock")}
        reg.default_profile = "lilith"
        assert reg.pick_backend(reg.profile()).name == "mock"

    def test_describe_lists_backends_and_profiles(self) -> None:
        rows = registry().describe()
        kinds = {row["kind"] for row in rows}
        # 0.7.0 (ADR-027): describe() дополнительно несёт строку резидента «tts-resident»
        assert kinds == {"tts", "tts-resident", "voice-profile"}
        names = {row["name"] for row in rows if row["kind"] == "voice-profile"}
        assert names == {"lilith", "clone", "cloud", "mocky"}
        default_row = next(r for r in rows if r["kind"] == "voice-profile" and r["name"] == "lilith")
        assert default_row["default"] is True


class TestZeroShotSlot:
    """Слот клона: референс из папки, движок подключается отдельно."""

    def test_reference_path(self) -> None:
        slot = ZeroShotTTS(voices_dir="voices")
        profile = VoiceProfile(name="anya", backend="zero-shot")
        assert slot.reference_path(profile).parts[-2:] == ("anya", "reference.wav")

    def test_explicit_reference_wins(self) -> None:
        slot = ZeroShotTTS()
        profile = VoiceProfile(name="x", backend="zero-shot", reference="voices/custom/ref.wav")
        assert slot.reference_path(profile).as_posix().endswith("voices/custom/ref.wav")

    def test_unavailable_without_engine(self) -> None:
        ok, reason = ZeroShotTTS().available()
        assert ok is False
        assert "движок" in reason

    @pytest.mark.asyncio
    async def test_synthesize_without_engine_raises(self) -> None:
        with pytest.raises(RuntimeError, match="не подключён"):
            await ZeroShotTTS().synthesize("текст", VoiceProfile(name="x", backend="zero-shot"))


# --------------------------------------------------------------------------- #
# Фолбэк VoiceCore (правка 16.11, ратифицировано Архитектором)                 #
# --------------------------------------------------------------------------- #
class _BoomBackend:
    """Фейковое горло: падает в synthesize/stream — сразу или после кусков."""

    def __init__(self, name: str, error: Exception, chunks: tuple[bytes, ...] = ()) -> None:
        self.name = name
        self.error = error
        self.chunks = chunks
        self.synthesize_calls = 0
        self.stream_calls = 0

    async def synthesize(self, text: str, profile: VoiceProfile) -> bytes:
        self.synthesize_calls += 1
        raise self.error

    async def stream(self, text: str, profile: VoiceProfile):
        self.stream_calls += 1
        for chunk in self.chunks:
            yield chunk
        raise self.error


class _ChunkBackend:
    """Фейковое горло, которое честно отдаёт куски."""

    def __init__(self, name: str, chunks: tuple[bytes, ...] = (b"wav-1",)) -> None:
        self.name = name
        self.chunks = chunks
        self.stream_calls = 0

    async def synthesize(self, text: str, profile: VoiceProfile) -> bytes:
        return b"".join(self.chunks)

    async def stream(self, text: str, profile: VoiceProfile):
        self.stream_calls += 1
        for chunk in self.chunks:
            yield chunk


class _FakeTTS:
    """Мини-реестр горла: ровно два движка, без конфигов, сетей и моделей."""

    def __init__(self, primary, fallback) -> None:
        self.primary = primary
        self.fallback = fallback
        self.profiles: dict[str, VoiceProfile] = {}
        self.default_profile = "test"

    def profile(self, name: str | None) -> VoiceProfile:
        return VoiceProfile(name=name or "test", backend="fake")

    def pick_backend(self, profile: VoiceProfile):
        return self.primary

    def pick_fallback(self, profile: VoiceProfile, exclude: str | None = None):
        return self.fallback

    def describe(self) -> list[dict]:
        return []


@pytest.fixture
def voice_core(settings, tmp_project) -> VoiceCore:
    """VoiceCore на тестовом конфиге; движки подменяются в каждом тесте."""
    settings.features.voice_enabled = True
    settings.voice.packs_manifest = str(tmp_project / "models" / "packs.yaml")
    settings.voice.packs_root = str(tmp_project)
    (tmp_project / "models").mkdir(exist_ok=True)
    (tmp_project / "models" / "packs.yaml").write_text("packs: {}\n", encoding="utf-8")
    return VoiceCore(settings)


class TestVoiceCoreFallback:
    """ADR-012: упавший фолбэк не маскирует ошибку первичного движка.

    Ратифицированная развилка (16.11): фолбэк не отдал ни куска → наружу первичная
    ошибка (``__cause__`` — ошибка фолбэка); фолбэк отдал куски и умер → наружу
    его собственная ошибка, реплика оборвана.
    """

    async def test_speak_fallback_failure_surfaces_primary(self, voice_core: VoiceCore) -> None:
        primary_err = RuntimeError("primary-engine-down")
        fallback_err = RuntimeError("fallback-engine-down")
        primary = _BoomBackend("fake-primary", primary_err)
        fallback = _BoomBackend("fake-fallback", fallback_err)
        voice_core.tts = _FakeTTS(primary, fallback)

        with pytest.raises(RuntimeError) as caught:
            await voice_core.speak("привет")

        assert caught.value is primary_err
        assert caught.value.__cause__ is fallback_err
        assert (primary.synthesize_calls, fallback.synthesize_calls) == (1, 1)

    async def test_stream_fallback_failure_before_chunks_surfaces_primary(
        self, voice_core: VoiceCore
    ) -> None:
        primary_err = RuntimeError("primary-engine-down")
        fallback_err = RuntimeError("fallback-engine-down")
        primary = _BoomBackend("fake-primary", primary_err)
        fallback = _BoomBackend("fake-fallback", fallback_err)
        voice_core.tts = _FakeTTS(primary, fallback)

        with pytest.raises(RuntimeError) as caught:
            async for _chunk in voice_core.stream("привет"):
                pass

        assert caught.value is primary_err
        assert caught.value.__cause__ is fallback_err
        assert (primary.stream_calls, fallback.stream_calls) == (1, 1)

    async def test_stream_fallback_failure_after_chunks_is_its_own(
        self, voice_core: VoiceCore
    ) -> None:
        primary_err = RuntimeError("primary-engine-down")
        fallback_err = RuntimeError("fallback-died-midstream")
        primary = _BoomBackend("fake-primary", primary_err)
        fallback = _BoomBackend("fake-fallback", fallback_err, chunks=(b"wav-1", b"wav-2"))
        voice_core.tts = _FakeTTS(primary, fallback)

        chunks: list[bytes] = []
        with pytest.raises(RuntimeError) as caught:
            async for chunk in voice_core.stream("привет"):
                chunks.append(chunk)

        assert chunks == [b"wav-1", b"wav-2"]     # сказанное уже прозвучало
        assert caught.value is fallback_err       # честная ошибка — его собственная
        assert caught.value.__cause__ is None     # первичная не подшита

    async def test_primary_midstream_failure_does_not_restart_fallback(
        self, voice_core: VoiceCore
    ) -> None:
        primary_err = RuntimeError("primary-died-midstream")
        primary = _BoomBackend("fake-primary", primary_err, chunks=(b"wav-1",))
        fallback = _ChunkBackend("fake-fallback")
        voice_core.tts = _FakeTTS(primary, fallback)

        chunks: list[bytes] = []
        with pytest.raises(RuntimeError) as caught:
            async for chunk in voice_core.stream("привет"):
                chunks.append(chunk)

        assert chunks == [b"wav-1"]
        assert caught.value is primary_err
        assert fallback.stream_calls == 0         # реплику с середины не перезапускаем

    async def test_stream_fallback_success_plays_all_chunks(self, voice_core: VoiceCore) -> None:
        primary = _BoomBackend("fake-primary", RuntimeError("primary-engine-down"))
        fallback = _ChunkBackend("fake-fallback", chunks=(b"wav-1", b"wav-2", b"wav-3"))
        voice_core.tts = _FakeTTS(primary, fallback)

        chunks = [chunk async for chunk in voice_core.stream("привет")]

        assert chunks == [b"wav-1", b"wav-2", b"wav-3"]

    async def test_speak_fallback_success_returns_fallback_audio(
        self, voice_core: VoiceCore
    ) -> None:
        primary = _BoomBackend("fake-primary", RuntimeError("primary-engine-down"))
        fallback = _ChunkBackend("fake-fallback", chunks=(b"wav-a", b"wav-b"))
        voice_core.tts = _FakeTTS(primary, fallback)

        assert await voice_core.speak("привет") == b"wav-awav-b"
