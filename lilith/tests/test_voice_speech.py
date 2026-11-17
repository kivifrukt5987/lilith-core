"""Гварды speech-extraction 0.7.2 / патч A (ADR-031): в TTS — только речь.

Поле 17.11: gemma в non-thinking-режиме отдавала каркас целиком, и горло читало
«системное уведомление, анализ действий…» вслух. Здесь — единица извлечения и
проводка в продюсере лица. Все тесты герметичны: ни GPU, ни сети, ни моделей.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lilith_core.config import VoiceSettings
from lilith_core.face import FaceProducerHub, PersonaRegistry
from lilith_core.face.producer import Utterance
from lilith_core.voice.speech import extract_speech, has_scaffold
from lilith_core.voice.stt import write_wav

PROJECT_ROOT = Path(__file__).resolve().parents[1]

FULL_REPLY = """[СИСТЕМНОЕ УВЕДОМЛЕНИЕ: LILITH.EXE - GREETING]

АНАЛИЗ ДЕЙСТВИЙ ОБЪЕКТА «КИРЮША»:
1. Вернулся.
2. Улыбается.

МЫСЛИ ЛИЛИТ:
*Он снова здесь. Мой.*

— Кирюша! — я улыбнулась. — Я скучала. [emotion: joy]
"""


# --------------------------------------------------------------------------- #
# 1. Извлечение: каркас вырезан, речь цела                                      #
# --------------------------------------------------------------------------- #
class TestExtractSpeech:
    def test_full_reply_keeps_only_speech(self) -> None:
        speech = extract_speech(FULL_REPLY)
        assert speech == "— Кирюша! — я улыбнулась. — Я скучала."
        for scaffold in ("СИСТЕМНОЕ", "АНАЛИЗ", "МЫСЛИ", "Вернулся", "emotion", "*"):
            assert scaffold not in speech, f"в озвучку просочился каркас: {scaffold}"

    def test_plain_text_is_untouched(self) -> None:
        assert extract_speech("Привет, Кирюша! Как дела?") == "Привет, Кирюша! Как дела?"

    def test_short_shout_without_scaffold_survives(self) -> None:
        assert extract_speech("БЫСТРО") == "БЫСТРО"

    def test_has_scaffold_detects_tags_and_headers(self) -> None:
        assert has_scaffold("[emotion: joy] привет") is True
        assert has_scaffold("АНАЛИЗ СРЕДЫ\nчто-то") is True
        assert has_scaffold("Привет, Кирюша!") is False

    def test_tag_only_reply_is_silent(self) -> None:
        assert extract_speech("[emotion: joy]") == ""

    def test_nonverbal_tag_stripped_but_laugh_text_kept(self) -> None:
        assert extract_speech("[laughs] хи-хи, Кирюша") == "хи-хи, Кирюша"

    def test_bold_headers_removed(self) -> None:
        text = "**АНАЛИЗ**:\n1. раз\n2. два\n\n**РЕЧЬ ВСЛУХ**\n— Привет!"
        assert extract_speech(text) == "— Привет!"

    def test_list_in_plain_text_is_not_scaffold(self) -> None:
        text = "1. купить торт\n2. обнять Кирюшу"
        assert extract_speech(text) == text

    def test_idempotent(self) -> None:
        once = extract_speech(FULL_REPLY)
        assert extract_speech(once) == once

    def test_empty_input(self) -> None:
        assert extract_speech("") == ""
        assert extract_speech("   \n\n ") == ""


# --------------------------------------------------------------------------- #
# 2. Проводка: продюсер лица озвучивает уже очищенный текст                     #
# --------------------------------------------------------------------------- #
class _RecordingVoice:
    """Фейковое горло: запоминает текст, отдаёт один валидный WAV."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self.seen: list[str] = []

    async def stream(self, text: str, profile, *, persona=None):
        self.seen.append(text)
        yield write_wav(b"\x00\x00" * 200, 24000)


@pytest.fixture
def hub(tmp_project: Path) -> FaceProducerHub:
    return FaceProducerHub(registry=PersonaRegistry(tmp_project / "personas"))


class TestProducerWiring:
    async def test_producer_speaks_only_speech(self, hub, settings) -> None:
        voice = _RecordingVoice(settings)
        utterance = Utterance(id="u-1", text=FULL_REPLY, persona="lilith")
        await hub._run(utterance, voice, "lilith", "mock")
        assert voice.seen == ["— Кирюша! — я улыбнулась. — Я скучала."]

    async def test_producer_silent_when_no_speech(self, hub, settings) -> None:
        voice = _RecordingVoice(settings)
        utterance = Utterance(id="u-2", text="[emotion: joy]", persona="lilith")
        await hub._run(utterance, voice, "lilith", "mock")
        assert voice.seen == []

    async def test_switch_off_passes_raw_text(self, hub, settings) -> None:
        settings.voice.speech_extraction = False
        voice = _RecordingVoice(settings)
        utterance = Utterance(id="u-3", text=FULL_REPLY, persona="lilith")
        await hub._run(utterance, voice, "lilith", "mock")
        assert voice.seen and "АНАЛИЗ" in voice.seen[0]


# --------------------------------------------------------------------------- #
# 3. Конфиг                                                                     #
# --------------------------------------------------------------------------- #
class TestConfig:
    def test_default_on(self) -> None:
        assert VoiceSettings().speech_extraction is True

    @pytest.mark.parametrize("name", ["config.yaml", "local.yaml"])
    def test_yaml_has_switch(self, name: str) -> None:
        data = yaml.safe_load((PROJECT_ROOT / "config" / name).read_text(encoding="utf-8"))
        assert data["voice"]["speech_extraction"] is True
