"""Тесты подсистемы конфигурации (YAML + .env + переменные окружения)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lilith_core.config import (
    CONFIG_ENV_VAR,
    BrainProfile,
    BrainSettings,
    FeatureFlags,
    Settings,
    YamlConfigSource,
    find_config_file,
    load_settings,
    resolve_config_path,
    resolve_path,
)


class TestDefaults:
    """Значения по умолчанию должны быть рабочими «из коробки»."""

    def test_settings_created_without_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
        settings = load_settings(config_path=None, env_file=None)

        assert settings.app.name == "LILITH-CORE"
        assert settings.app.user_name == "Кирюша"
        assert settings.server.port == 8765
        assert settings.server.ws_path == "/ws"
        assert settings.features.echo_mode is True
        assert settings.features.brain_enabled is False

    def test_defaults_are_validated(self) -> None:
        settings = Settings()
        assert settings.brain.defaults.temperature == pytest.approx(0.8)
        assert settings.hands.confirm_timeout_sec == pytest.approx(25.0)
        assert settings.memory.summarize_every_n == 40

    def test_port_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError):
            Settings(server={"port": 70000})


class TestYamlSource:
    """Чтение config/config.yaml."""

    def test_yaml_overrides_defaults(self, sample_config: Path) -> None:
        settings = load_settings(sample_config, env_file=None)

        assert settings.app.user_name == "Тестовый Кирюша"
        assert settings.app.persona_name == "ТестЛилит"
        assert settings.app.debug is True
        assert settings.server.port == 9999
        assert settings.logging.level == "DEBUG"
        assert settings.brain.resolve().model == "test-model"
        assert settings.brain.resolve("chat").base_url == "http://127.0.0.1:1/v1"

    def test_unknown_key_in_yaml_raises(self, tmp_project: Path) -> None:
        path = tmp_project / "config" / "bad.yaml"
        path.write_text("app:\n  user_name: X\n  nesuschestvuet: 1\n", encoding="utf-8")

        with pytest.raises(ValueError):
            load_settings(path, env_file=None)

    def test_yaml_source_returns_empty_when_no_file(self, tmp_path: Path) -> None:
        source = YamlConfigSource(Settings, tmp_path / "nope.yaml")
        assert source() == {}

    def test_yaml_source_rejects_non_mapping(self, tmp_path: Path) -> None:
        path = tmp_path / "list.yaml"
        path.write_text("- one\n- two\n", encoding="utf-8")
        source = YamlConfigSource(Settings, path)

        with pytest.raises(ValueError, match="словарь верхнего уровня"):
            source()

    def test_explicit_missing_config_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_settings(tmp_path / "does_not_exist.yaml", env_file=None)


class TestEnvPrecedence:
    """Переменные окружения важнее YAML."""

    def test_env_overrides_yaml(self, sample_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LILITH_SERVER__PORT", "1234")
        monkeypatch.setenv("LILITH_APP__USER_NAME", "КирюшаИзEnv")
        monkeypatch.setenv("LILITH_BRAIN__DEFAULTS__MODEL", "env-model")

        settings = load_settings(sample_config, env_file=None)

        assert settings.server.port == 1234          # env > yaml (9999)
        assert settings.app.user_name == "КирюшаИзEnv"
        assert settings.brain.defaults.model == "env-model"

    def test_deep_nested_env(self, sample_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LILITH_VOICE__STT_COMPUTE_TYPE", "int8")
        monkeypatch.setenv("LILITH_FACE__EMOTION_DEFAULT", "smug")

        settings = load_settings(sample_config, env_file=None)

        assert settings.voice.stt_compute_type == "int8"
        assert settings.face.emotion_default == "smug"

    def test_dotenv_file_is_read(self, tmp_project: Path, sample_config: Path) -> None:
        (tmp_project / ".env").write_text(
            "LILITH_BRAIN__API_KEY=sk-secret-from-dotenv\nLILITH_APP__ENV=prod\n",
            encoding="utf-8",
        )

        settings = load_settings(sample_config, env_file=tmp_project / ".env")

        assert settings.brain.api_key.get_secret_value() == "sk-secret-from-dotenv"
        assert settings.app.env == "prod"
        assert settings.is_production is True

    def test_env_wins_over_dotenv(self, tmp_project: Path, sample_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_project / ".env").write_text("LILITH_APP__USER_NAME=ИзDotenv\n", encoding="utf-8")
        monkeypatch.setenv("LILITH_APP__USER_NAME", "ИзEnv")

        settings = load_settings(sample_config, env_file=tmp_project / ".env")
        assert settings.app.user_name == "ИзEnv"


class TestConfigDiscovery:
    """Поиск файла конфигурации."""

    def test_env_var_points_to_config(self, tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_project / "config" / "custom.yaml"
        path.write_text("app:\n  user_name: Кастомный\n", encoding="utf-8")
        monkeypatch.setenv(CONFIG_ENV_VAR, str(path))

        assert resolve_config_path() == path.resolve()
        assert find_config_file() == path.resolve()
        assert load_settings(env_file=None).app.user_name == "Кастомный"

    def test_default_discovery_in_cwd(self, tmp_project: Path, sample_config: Path) -> None:
        found = resolve_config_path()
        assert found is not None
        assert Path(found).name == "config.yaml"

    def test_find_config_returns_none_when_absent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "missing.yaml"))
        assert find_config_file() is None


class TestPathHelpers:
    """resolve_path: относительные пути от cwd или от корня репозитория."""

    def test_absolute_path_untouched(self, tmp_path: Path) -> None:
        assert resolve_path(tmp_path / "x.txt") == tmp_path / "x.txt"

    def test_relative_resolved_from_cwd(self, tmp_project: Path) -> None:
        (tmp_project / "persona.md").write_text("x", encoding="utf-8")
        assert resolve_path("persona.md") == tmp_project / "persona.md"

    def test_explicit_base_dir(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "a.txt").write_text("a", encoding="utf-8")
        assert resolve_path("a.txt", base=sub) == sub / "a.txt"

    def test_explicit_base_dir_wins_even_for_missing_file(self, tmp_path: Path) -> None:
        """base задан явно -> никакой эвристики с cwd и корнем репозитория."""
        base = tmp_path / "fresh"
        assert resolve_path("logs", base=base) == base / "logs"


class TestSecrets:
    """Секреты не должны протекать в логи и healthz."""

    def test_api_key_from_env_var_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LILITH_BRAIN_API_KEY", "sk-from-env-var")
        brain = BrainSettings()
        assert brain.resolved_api_key() == "sk-from-env-var"

    def test_explicit_api_key_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LILITH_BRAIN_API_KEY", "sk-from-env-var")
        brain = BrainSettings(api_key="sk-explicit")
        assert brain.resolved_api_key() == "sk-explicit"

    def test_masked_hides_key(self) -> None:
        brain = BrainSettings(api_key="sk-supersecretkey123")
        masked = brain.masked()
        assert "supersecretkey123" not in masked["api_key"]
        assert masked["api_key"].startswith("sk-")

    def test_masked_when_key_absent(self) -> None:
        assert BrainSettings(api_key="").masked()["api_key"] == "не задан"

    def test_public_dict_has_no_raw_secret(self, sample_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LILITH_BRAIN__API_KEY", "sk-leak-check-1234567890")
        settings = load_settings(sample_config, env_file=None)
        dumped = str(settings.public_dict())
        assert "leak-check-1234567890" not in dumped


class TestFeatureFlags:
    """Список включённых подсистем."""

    def test_enabled_lists_only_true(self) -> None:
        flags = FeatureFlags(web_panel=True, echo_mode=True, brain_enabled=False)
        assert flags.enabled() == ["echo_mode", "web_panel"]

    def test_enabled_empty(self) -> None:
        assert FeatureFlags(**{name: False for name in FeatureFlags.model_fields}).enabled() == []


class TestBrainProfiles:
    """Реестр профилей мозга: слияние, валидация, переопределения."""

    def test_resolve_merges_defaults(self, settings) -> None:
        chat = settings.brain.resolve("chat")

        assert chat.name == "chat"
        assert chat.model == "test-model"                 # из профиля
        assert chat.base_url == "http://127.0.0.1:1/v1"   # из профиля
        assert chat.temperature == pytest.approx(0.8)     # унаследовано из defaults
        assert chat.max_tokens == 1024                    # унаследовано
        assert chat.stream is True

    def test_resolve_profile_overrides_defaults(self, settings) -> None:
        coder = settings.brain.resolve("coder")

        assert coder.temperature == pytest.approx(0.2)    # переопределено профилем
        assert coder.base_url == "http://127.0.0.1:2/v1"
        assert coder.model == "local-model"               # модель не задана -> defaults

    def test_resolve_default_profile_when_none(self, settings) -> None:
        assert settings.brain.resolve().name == settings.brain.default_profile == "chat"

    def test_resolve_unknown_profile_raises(self, settings) -> None:
        with pytest.raises(KeyError, match="не найден"):
            settings.brain.resolve("telepathy")

    def test_profile_names_sorted(self, settings) -> None:
        assert settings.brain.profile_names() == ["chat", "coder"]

    def test_default_profile_must_exist(self) -> None:
        with pytest.raises(ValueError, match="не найден среди"):
            BrainSettings(default_profile="ghost", profiles={"chat": BrainProfile()})

    def test_empty_profiles_rejected(self) -> None:
        with pytest.raises(ValueError, match="хотя бы один профиль"):
            BrainSettings(profiles={})

    def test_default_factory_has_chat(self) -> None:
        brain = BrainSettings()
        assert "chat" in brain.profiles
        assert brain.resolve().model == "local-model"

    def test_env_overrides_profile_deeply(self, sample_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LILITH_BRAIN__PROFILES__CHAT__MODEL", "env-qwen35-9b")
        monkeypatch.setenv("LILITH_BRAIN__PROFILES__CHAT__MAX_TOKENS", "2048")

        from lilith_core.config import load_settings

        brain = load_settings(sample_config, env_file=None).brain
        chat = brain.resolve("chat")

        assert chat.model == "env-qwen35-9b"
        assert chat.max_tokens == 2048
        assert brain.resolve("coder").model == "local-model"  # соседний профиль не задет

    def test_env_can_add_new_profile(self, sample_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Экспериментальная модель добавляется без правки yaml — переменными окружения."""
        monkeypatch.setenv("LILITH_BRAIN__PROFILES__EXPERIMENT__MODEL", "qwen4-2b-test")
        monkeypatch.setenv("LILITH_BRAIN__PROFILES__EXPERIMENT__BASE_URL", "http://127.0.0.1:9/v1")

        from lilith_core.config import load_settings

        brain = load_settings(sample_config, env_file=None).brain
        assert "experiment" in brain.profile_names()
        assert brain.resolve("experiment").model == "qwen4-2b-test"

    def test_env_switches_default_profile(self, sample_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LILITH_BRAIN__DEFAULT_PROFILE", "coder")

        from lilith_core.config import load_settings

        brain = load_settings(sample_config, env_file=None).brain
        assert brain.resolve().name == "coder"

    def test_api_key_env_per_profile(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_CLOUD_KEY", "sk-cloud-123456")
        brain = BrainSettings(
            default_profile="cloud",
            profiles={"cloud": BrainProfile(api_key_env="MY_CLOUD_KEY")},
        )
        assert brain.resolve("cloud").resolved_api_key() == "sk-cloud-123456"
        assert brain.resolved_api_key("cloud") == "sk-cloud-123456"

    def test_explicit_key_wins_over_profile_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_CLOUD_KEY", "sk-cloud-123456")
        brain = BrainSettings(
            api_key="sk-explicit-999",
            default_profile="cloud",
            profiles={"cloud": BrainProfile(api_key_env="MY_CLOUD_KEY")},
        )
        assert brain.resolved_api_key("cloud") == "sk-explicit-999"

    def test_masked_lists_profiles_without_secrets(self) -> None:
        brain = BrainSettings(api_key="sk-supersecretkey123")
        masked = brain.masked()

        assert masked["default_profile"] == "chat"
        assert "supersecretkey123" not in str(masked)
        assert masked["api_key"].startswith("sk-")
        assert "chat" in masked["profiles"]
        assert masked["profiles"]["chat"]["model"] == "local-model"

    def test_masked_note_visible(self) -> None:
        brain = BrainSettings(profiles={"chat": BrainProfile(note="малышка для болтовни")})
        assert brain.masked()["profiles"]["chat"]["note"] == "малышка для болтовни"

    def test_resolved_profile_masked_shape(self, settings) -> None:
        data = settings.brain.resolve("chat").masked()
        for key in ("provider", "base_url", "model", "temperature", "max_tokens", "stream",
                    "history_max_messages", "note"):
            assert key in data
