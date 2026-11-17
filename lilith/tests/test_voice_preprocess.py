"""Гварды препроцессинга 0.7.2-а (ADR-030): ударения и протяжки до TTS.

Все герметичны: ни GPU, ни сети, ни моделей — только текст и словари.
Ратифицировано Архитектором 17.11: acute U+0301 до Ж7-б, хранение номером
ударной гласной, личный словарь персоны главнее общего, протяжки — только
между одинаковыми буквами, даты/URL/``[...]`` не трогаем.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lilith_core.config import PreprocessSettings
from lilith_core.voice import VoiceCore, VoiceProfile
from lilith_core.voice.preprocess import (
    STRESS_MARK,
    StressDictionary,
    TextPreprocessor,
    apply_accent,
    collapse_drawl,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_dict(path: Path, words: dict[str, int]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"words": words}, allow_unicode=True), encoding="utf-8")
    return path


@pytest.fixture
def pre(settings, tmp_project: Path) -> TextPreprocessor:
    """Препроцессор на временном общем словаре и временной папке персон."""
    settings.voice.preprocess.enabled = True
    settings.voice.preprocess.drawl_enabled = True
    settings.voice.preprocess.stress_mode = "acute"
    settings.voice.preprocess.stress_dict = str(tmp_project / "stress_ru.yaml")
    settings.app.personas_dir = str(tmp_project / "personas")
    _write_dict(tmp_project / "stress_ru.yaml", {"кирюха": 2, "лилит": 2})
    return TextPreprocessor(settings)


# --------------------------------------------------------------------------- #
# 1. Режимы маркера (Ж7-б выбирает один, код не меняется)                      #
# --------------------------------------------------------------------------- #
class TestStressModes:
    def test_acute_after_stressed_vowel(self, pre: TextPreprocessor) -> None:
        assert pre.apply("Кирюха пришла") == f"Кирю{STRESS_MARK}ха пришла"

    def test_caps_mode(self, pre: TextPreprocessor) -> None:
        pre.mode = "caps"
        assert pre.apply("Кирюха") == "КирЮха"

    def test_plus_mode(self, pre: TextPreprocessor) -> None:
        pre.mode = "plus"
        assert pre.apply("Кирюха") == "Кирю+ха"

    def test_off_leaves_text_alone(self, pre: TextPreprocessor) -> None:
        pre.mode = "off"
        assert pre.apply("Кирюха, ба-а-ам") == "Кирюха, бааам"

    def test_unknown_mode_falls_back_to_acute(self, settings) -> None:
        settings.voice.preprocess.stress_mode = "teleport"
        assert TextPreprocessor(settings).mode == "acute"

    def test_apply_accent_out_of_range_is_noop(self) -> None:
        assert apply_accent("кирюха", 9, "acute") == "кирюха"


# --------------------------------------------------------------------------- #
# 2. Словарь: номер гласной, регистр, приоритет личного, устойчивость к мусору #
# --------------------------------------------------------------------------- #
class TestStressDictionary:
    def test_vowel_number_not_ready_string(self, tmp_project: Path) -> None:
        """Храним номер: смена режима не переписывает словарь."""
        path = _write_dict(tmp_project / "d.yaml", {"кирюха": 2})
        dictionary = StressDictionary()
        assert dictionary.load(path) == 1
        assert dictionary.words == {"кирюха": 2}

    def test_case_is_preserved(self, pre: TextPreprocessor) -> None:
        assert pre.apply("КИРЮХА") == f"КИРЮ{STRESS_MARK}ХА"
        assert pre.apply("кирюха") == f"кирю{STRESS_MARK}ха"

    def test_unknown_word_untouched(self, pre: TextPreprocessor) -> None:
        assert pre.apply("привет, мир") == "привет, мир"

    def test_idempotent(self, pre: TextPreprocessor) -> None:
        once = pre.apply("Кирюха и Лилит")
        assert pre.apply(once) == once

    def test_personal_dictionary_wins(self, pre: TextPreprocessor, tmp_project: Path) -> None:
        _write_dict(tmp_project / "personas" / "lilith" / "voice" / "stress.yaml",
                    {"кирюха": 1})
        assert pre.apply("Кирюха", persona="lilith") == f"Ки{STRESS_MARK}рюха"
        assert pre.apply("Кирюха") == f"Кирю{STRESS_MARK}ха"      # общий — без персоны

    def test_foreign_word_and_bad_index_are_skipped(self, tmp_project: Path) -> None:
        path = tmp_project / "bad.yaml"
        path.write_text(
            "words:\n  qwen: 1\n  кирюха: 99\n  лилит: не-число\n", encoding="utf-8"
        )
        dictionary = StressDictionary()
        assert dictionary.load(path) == 0

    def test_missing_file_is_not_fatal(self, tmp_project: Path) -> None:
        dictionary = StressDictionary()
        assert dictionary.load(tmp_project / "нет-такого.yaml") == 0


# --------------------------------------------------------------------------- #
# 3. Протяжки: дефис и многоточие, только одинаковые буквы                      #
# --------------------------------------------------------------------------- #
class TestDrawl:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("ба-а-ам", "бааам"),
            ("нее-ет", "нееет"),
            ("ба...ам", "баам"),
            ("ну-у-у", "нууу"),
        ],
    )
    def test_collapses_identical_letters(self, raw: str, expected: str) -> None:
        assert collapse_drawl(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "кто-то",
            "ой-ой",
            "по-моему",
            "что-либо",
            "2026-11-16",
            "x-y-z",
            "Привет... как дела?",
        ],
    )
    def test_legit_text_untouched(self, raw: str) -> None:
        assert collapse_drawl(raw) == raw

    def test_idempotent(self) -> None:
        once = collapse_drawl("ба-а-ам")
        assert collapse_drawl(once) == once

    def test_url_and_date_survive_pipeline(self, pre: TextPreprocessor) -> None:
        text = "Смотри https://a-a.ru/x-a-a и дату 2026-11-16, ба-а-ам"
        assert pre.apply(text) == f"Смотри https://a-a.ru/x-a-a и дату 2026-11-16, бааам"


# --------------------------------------------------------------------------- #
# 4. Теги и нонвербалика: защищены, содержимое не трогаем                       #
# --------------------------------------------------------------------------- #
class TestTagSafety:
    def test_emotion_tag_content_untouched(self, pre: TextPreprocessor) -> None:
        assert pre.apply("[emotion: joy] Кирюха, ба-а-ам!") == (
            f"[emotion: joy] Кирю{STRESS_MARK}ха, бааам!"
        )

    def test_nonverbal_tag_intact(self, pre: TextPreprocessor) -> None:
        assert pre.apply("[laughs] ну-у-у Кирюха") == f"[laughs] нууу Кирю{STRESS_MARK}ха"

    def test_off_switch_returns_raw_text(self, pre: TextPreprocessor) -> None:
        pre.enabled = False
        assert pre.apply("Кирюха, ба-а-ам") == "Кирюха, ба-а-ам"


# --------------------------------------------------------------------------- #
# 5. Конвейер: продюсер/панель получают уже обработанный текст                   #
# --------------------------------------------------------------------------- #
class _CapturingTTS:
    """Мини-реестр горла: запоминает текст, который дошёл до движка."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self.profiles: dict = {}
        self.default_profile = "test"

    def profile(self, name):
        return VoiceProfile(name=name or "test", backend="fake")

    def pick_backend(self, profile):
        return _CapturingBackend(self)

    def pick_fallback(self, profile, exclude=None):
        return None

    def describe(self):
        return []


class _CapturingBackend:
    def __init__(self, registry: _CapturingTTS) -> None:
        self.name = "fake"
        self.registry = registry

    async def synthesize(self, text: str, profile) -> bytes:
        self.registry.seen.append(text)
        return b"wav"

    async def stream(self, text: str, profile):
        self.registry.seen.append(text)
        yield b"wav-1"


@pytest.fixture
def core(settings, tmp_project: Path) -> VoiceCore:
    settings.features.voice_enabled = True
    settings.voice.packs_manifest = str(tmp_project / "models" / "packs.yaml")
    settings.voice.packs_root = str(tmp_project)
    (tmp_project / "models").mkdir(exist_ok=True)
    (tmp_project / "models" / "packs.yaml").write_text("packs: {}\n", encoding="utf-8")
    settings.voice.preprocess.stress_dict = str(tmp_project / "stress_ru.yaml")
    settings.app.personas_dir = str(tmp_project / "personas")
    _write_dict(tmp_project / "stress_ru.yaml", {"кирюха": 2})
    return VoiceCore(settings)


class TestPipelineOrder:
    """Текст попадает в TTS уже размеченным — на обоих путях (speak/stream)."""

    async def test_speak_feeds_stressed_text(self, core: VoiceCore) -> None:
        registry = _CapturingTTS()
        core.tts = registry
        await core.speak("Кирюха, ба-а-ам")
        assert registry.seen == [f"Кирю{STRESS_MARK}ха, бааам"]

    async def test_stream_feeds_stressed_text(self, core: VoiceCore) -> None:
        registry = _CapturingTTS()
        core.tts = registry
        chunks = [chunk async for chunk in core.stream("Кирюха")]
        assert chunks == [b"wav-1"]
        assert registry.seen == [f"Кирю{STRESS_MARK}ха"]

    async def test_persona_reaches_preprocessor(self, core: VoiceCore, tmp_project: Path) -> None:
        _write_dict(tmp_project / "personas" / "lilith" / "voice" / "stress.yaml",
                    {"кирюха": 1})
        registry = _CapturingTTS()
        core.tts = registry
        await core.speak("Кирюха", persona="lilith")
        assert registry.seen == [f"Ки{STRESS_MARK}рюха"]

    def test_describe_reports_preprocess(self, core: VoiceCore) -> None:
        info = core.describe()["preprocess"]
        assert info["enabled"] is True
        assert info["stress_mode"] == "acute"
        assert info["stress_words"] == 1


# --------------------------------------------------------------------------- #
# 6. Конфиг: безопасные дефолты и живые секции в обоих yaml                     #
# --------------------------------------------------------------------------- #
class TestConfig:
    def test_defaults(self) -> None:
        defaults = PreprocessSettings()
        assert defaults.enabled is True
        assert defaults.stress_mode == "acute"
        assert defaults.drawl_enabled is True
        assert defaults.stress_dict == "config/stress_ru.yaml"

    @pytest.mark.parametrize("name", ["config.yaml", "local.yaml"])
    def test_yaml_has_section(self, name: str) -> None:
        data = yaml.safe_load((PROJECT_ROOT / "config" / name).read_text(encoding="utf-8"))
        preprocess = data["voice"]["preprocess"]
        assert preprocess["enabled"] is True
        assert preprocess["stress_mode"] == "acute"
        assert preprocess["stress_dict"] == "config/stress_ru.yaml"

    def test_shared_dictionary_is_valid(self) -> None:
        data = yaml.safe_load(
            (PROJECT_ROOT / "config" / "stress_ru.yaml").read_text(encoding="utf-8")
        )
        words = data["words"]
        assert words["кирюха"] == 2          # поле 17.11: не «КирюхА»
        assert all(isinstance(index, int) and index >= 1 for index in words.values())
