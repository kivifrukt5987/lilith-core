"""Тесты горла: предложения, mock-TTS, реестр профилей и фолбэков."""

from __future__ import annotations

import pytest

from lilith_core.voice import (
    EdgeTTS,
    MockTTS,
    SileroTTS,
    TTSRegistry,
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
        assert kinds == {"tts", "voice-profile"}
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
