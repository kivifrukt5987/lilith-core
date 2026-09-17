"""Тесты парсера эмоций и словарей согласования (лицо/голос)."""

from __future__ import annotations

from lilith_core.face import (
    EMOTIONS,
    blends_for,
    guess_emotion,
    parse_emotions,
    prosody_for,
    strip_emotion_tags,
)


class TestParseTags:
    """Теги [emotion: x]: события, позиции, алиасы, регистр."""

    def test_single_tag(self) -> None:
        events = parse_emotions("[emotion: joy] Привет, Кирюша!")
        assert [e.name for e in events] == ["joy"]
        assert events[0].position == 0

    def test_multiple_tags_positions(self) -> None:
        text = "[emotion: smug] Хи-хи. [emotion: love] Мой мальчик."
        events = parse_emotions(text)
        assert [e.name for e in events] == ["smug", "love"]
        assert events[1].position == text.index("[emotion: love]")

    def test_case_and_spaces(self) -> None:
        events = parse_emotions("[ Emotion : JOY ] ура")
        assert [e.name for e in events] == ["joy"]

    def test_aliases_russian(self) -> None:
        events = parse_emotions("[эмоция: злость] нет.")
        assert [e.name for e in events] == ["anger"]

    def test_unknown_tag_passes_raw_name(self) -> None:
        events = parse_emotions("[emotion: teleport] хм")
        assert [e.name for e in events] == ["teleport"]

    def test_no_tags_no_fallback(self) -> None:
        assert parse_emotions("просто текст без чувств", keyword_fallback=False) == []


class TestStrip:
    """Очистка текста: пользователь не видит скобки."""

    def test_strip_removes_tags(self) -> None:
        clean, events = strip_emotion_tags("[emotion: joy] Привет!  [emotion: smug] Мур.")
        assert clean == "Привет! Мур."
        assert [e.name for e in events] == ["joy", "smug"]

    def test_strip_keeps_rest_intact(self) -> None:
        clean, events = strip_emotion_tags("без тегов, [квадратные: скобки] не трогаем")
        assert clean == "без тегов, [квадратные: скобки] не трогаем"
        assert events == []

    def test_strip_collapses_spaces(self) -> None:
        clean, _ = strip_emotion_tags("[emotion: joy]    Привет")
        assert clean == "Привет"


class TestKeywordFallback:
    """Догадка по словам, когда модель забыла теги."""

    def test_vampire_laugh(self) -> None:
        assert guess_emotion("Кхххх, спички уже приготовлены, мой мальчик") == "evil"

    def test_laugh_smug(self) -> None:
        assert guess_emotion("хи-хи, попался") == "smug"

    def test_love(self) -> None:
        assert guess_emotion("я тебя люблю, мой сладкий") == "love"

    def test_neutral_text(self) -> None:
        assert guess_emotion("сервер поднялся на порту 8765") is None

    def test_fallback_in_parse(self) -> None:
        events = parse_emotions("мур-мур, обнимаю свою грелочку")
        assert [e.name for e in events] == ["love"]


class TestDictionaries:
    """Словари лицо/голос: одна эмоция — два потребителя."""

    def test_blends_known_and_unknown(self) -> None:
        assert blends_for("joy")["Joy"] == 1.0
        assert blends_for("teleport") == blends_for("neutral")

    def test_prosody_known_and_unknown(self) -> None:
        assert prosody_for("anger")["pitch"] < 1.0
        assert prosody_for("teleport") == prosody_for("neutral")

    def test_all_canonical_emotions_covered(self) -> None:
        from lilith_core.face import EMOTION_TO_BLENDSHAPES, EMOTION_TO_PROSODY

        for emotion in EMOTIONS:
            assert emotion in EMOTION_TO_BLENDSHAPES
            assert emotion in EMOTION_TO_PROSODY
            assert blends_for(emotion)
            assert prosody_for(emotion)
