"""Тесты персоны (системного промта)."""

from __future__ import annotations

from pathlib import Path

import lilith_core
from lilith_core.config import load_settings
from lilith_core.persona import WEEKDAYS_RU, Persona, build_system_prompt, load_persona


class TestLoadPersona:
    """Загрузка и подстановка плейсхолдеров."""

    def test_loads_and_substitutes(self, persona_file: Path, sample_config: Path) -> None:
        settings = load_settings(sample_config, env_file=None)
        persona = load_persona(persona_file, settings=settings)

        assert persona.is_empty is False
        assert "ТестЛилит" in persona.text          # $persona_name
        assert "Тестовый Кирюша" in persona.text    # $user_name
        assert f"v{lilith_core.__version__}" in persona.text  # $version
        assert f"этап {lilith_core.__stage__}" in persona.text  # $stage
        assert "$placeholder_ostavlen_kak_est" in persona.text  # неизвестный плейсхолдер не ломает загрузку

    def test_weekday_is_russian(self, persona_file: Path, sample_config: Path) -> None:
        settings = load_settings(sample_config, env_file=None)
        persona = load_persona(persona_file, settings=settings)
        assert any(day in persona.text for day in WEEKDAYS_RU)

    def test_custom_variables_override(self, persona_file: Path, sample_config: Path) -> None:
        settings = load_settings(sample_config, env_file=None)
        persona = load_persona(persona_file, settings=settings, variables={"user_name": "Особый"})
        assert "Особый" in persona.text

    def test_missing_file_returns_empty_persona(self, tmp_path: Path, sample_config: Path) -> None:
        settings = load_settings(sample_config, env_file=None)
        persona = load_persona(tmp_path / "no_such_persona.md", settings=settings)

        assert persona.is_empty is True
        assert persona.char_count == 0
        assert persona.info()["exists"] is False

    def test_path_from_settings(self, sample_config: Path, tmp_project: Path) -> None:
        settings = load_settings(sample_config, env_file=None)
        settings.app.persona_path = str(tmp_project / "persona.md")
        (tmp_project / "persona.md").write_text("Привет от $user_name", encoding="utf-8")

        persona = load_persona(settings=settings)
        assert persona.text == "Привет от Тестовый Кирюша"

    def test_info_dict(self, persona_file: Path, sample_config: Path) -> None:
        settings = load_settings(sample_config, env_file=None)
        info = load_persona(persona_file, settings=settings).info()
        assert set(info) == {"path", "exists", "chars", "empty"}
        assert info["exists"] is True
        assert info["chars"] > 0


class TestBuildSystemPrompt:
    """Сборка итогового системного промта."""

    def test_fallback_when_persona_missing(self, tmp_path: Path, sample_config: Path) -> None:
        settings = load_settings(sample_config, env_file=None)
        settings.app.persona_path = str(tmp_path / "missing.md")

        prompt = build_system_prompt(settings=settings)
        assert "ТестЛилит" in prompt
        assert "Файл персоны не найден" in prompt

    def test_extra_appended(self, persona_file: Path, sample_config: Path) -> None:
        settings = load_settings(sample_config, env_file=None)
        prompt = build_system_prompt(settings=settings, persona_path=persona_file, extra="Отвечай кратко.")

        assert "Отвечай кратко." in prompt
        assert "---" in prompt
        assert prompt.index("ТестЛилит") < prompt.index("Отвечай кратко.")

    def test_extra_from_settings(self, persona_file: Path, sample_config: Path) -> None:
        settings = load_settings(sample_config, env_file=None)
        settings.brain.system_prompt_extra = "Дополнение из конфига"

        prompt = build_system_prompt(settings=settings, persona_path=persona_file)
        assert "Дополнение из конфига" in prompt

    def test_empty_extra_not_added(self, persona_file: Path, sample_config: Path) -> None:
        settings = load_settings(sample_config, env_file=None)
        prompt = build_system_prompt(settings=settings, persona_path=persona_file, extra="   ")
        assert "---" not in prompt


class TestPersonaDataclass:
    """Мелкие свойства датакласса."""

    def test_empty_flags(self) -> None:
        assert Persona(path=None, text="   ", variables={}).is_empty is True
        assert Persona(path=None, text="текст", variables={}).is_empty is False

    def test_char_count(self) -> None:
        assert Persona(path=None, text="абв", variables={}).char_count == 3
