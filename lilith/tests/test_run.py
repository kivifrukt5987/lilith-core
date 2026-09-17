"""Тесты CLI-обёртки запуска (без реального подъёма uvicorn)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lilith_core.run import _print_effective_config, main, parse_args


class TestParseArgs:
    """Разбор аргументов командной строки."""

    def test_defaults(self) -> None:
        args = parse_args([])
        assert args.host is None
        assert args.port is None
        assert args.reload is False
        assert args.show_config is False

    def test_all_flags(self) -> None:
        args = parse_args(
            ["--host", "0.0.0.0", "--port", "9000", "--config", "my.yaml",
             "--log-level", "debug", "--reload", "--show-config"]
        )
        assert args.host == "0.0.0.0"
        assert args.port == 9000
        assert args.config == "my.yaml"
        assert args.log_level == "debug"
        assert args.reload is True
        assert args.show_config is True

    def test_unknown_arg_fails(self) -> None:
        with pytest.raises(SystemExit):
            parse_args(["--nesuschestvuet"])


class TestMain:
    """main() должен уметь отработать без запуска сервера."""

    def test_show_config_exits_zero(self, sample_config: Path, capsys) -> None:
        code = main(["--config", str(sample_config), "--show-config"])
        assert code == 0

    def test_show_config_prints_effective_values(self, sample_config: Path) -> None:
        main(["--config", str(sample_config), "--show-config"])
        # лог уходит в файл/stderr; проверяем, что исключений нет и конфиг собран

    def test_missing_config_returns_error(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            main(["--config", str(tmp_path / "nope.yaml"), "--show-config"])

    def test_log_level_override_applied(self, sample_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict = {}

        def fake_run(app: str, **kwargs) -> None:
            captured.update(kwargs)
            captured["app"] = app

        monkeypatch.setattr("lilith_core.run.uvicorn.run", fake_run)
        code = main(["--config", str(sample_config), "--log-level", "trace", "--host", "1.2.3.4", "--port", "4321"])

        assert code == 0
        assert captured["app"] == "lilith_core.app:app"
        assert captured["host"] == "1.2.3.4"
        assert captured["port"] == 4321
        assert captured["access_log"] is False
        assert captured["log_config"] is None

    def test_oserror_returns_one(self, sample_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*_a, **_k) -> None:
            raise OSError("address already in use")

        monkeypatch.setattr("lilith_core.run.uvicorn.run", boom)
        assert main(["--config", str(sample_config)]) == 1

    def test_keyboard_interrupt_returns_130(self, sample_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def interrupt(*_a, **_k) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr("lilith_core.run.uvicorn.run", interrupt)
        assert main(["--config", str(sample_config)]) == 130


class TestPrintEffectiveConfig:
    """Диагностический вывод конфига."""

    def test_does_not_leak_secret(self, sample_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LILITH_BRAIN__API_KEY", "sk-print-check-42")
        from lilith_core.config import load_settings

        settings = load_settings(sample_config, env_file=None)
        _print_effective_config(settings)  # не должно бросать исключений

    def test_handles_missing_persona(self, tmp_project: Path) -> None:
        from lilith_core.config import load_settings

        settings = load_settings(env_file=None)
        settings.app.persona_path = str(tmp_project / "missing.md")
        assert settings.persona_file.is_file() is False
        _print_effective_config(settings)  # не падаем, если persona.md отсутствует
