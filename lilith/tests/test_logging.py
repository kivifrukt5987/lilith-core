"""Тесты настройки логирования (loguru + перехват стандартного logging)."""

from __future__ import annotations

import io
import logging
import sys
from pathlib import Path

from loguru import logger

from lilith_core.config import LoggingSettings, load_settings
from lilith_core.logging_setup import InterceptHandler, banner, is_configured, setup_logging


def _file_cfg(tmp_path: Path, name: str = "test.log", **kwargs) -> LoggingSettings:
    """Конфиг логирования, пишущий во временную директорию."""
    kwargs.setdefault("console", False)
    return LoggingSettings(dir=str(tmp_path / "logs"), file_name=name, **kwargs)


class TestSetupLogging:
    """Инициализация должна быть идемпотентной и создавать файл лога."""

    def test_creates_log_file(self, tmp_path: Path) -> None:
        setup_logging(_file_cfg(tmp_path, level="DEBUG"), force=True)
        logger.info("проверка записи в файл")

        log_file = tmp_path / "logs" / "test.log"
        assert log_file.is_file()
        assert "проверка записи в файл" in log_file.read_text(encoding="utf-8")

    def test_creates_missing_directories(self, tmp_path: Path) -> None:
        deep = tmp_path / "a" / "b" / "c"
        setup_logging(LoggingSettings(dir=str(deep), console=False), force=True)
        assert deep.is_dir()

    def test_idempotent_without_force(self, tmp_path: Path) -> None:
        cfg = _file_cfg(tmp_path)
        setup_logging(cfg, force=True)
        logger.info("первое")

        setup_logging(cfg)  # без force -> конфигурация не пересоздаётся
        logger.info("второе")

        content = (tmp_path / "logs" / "test.log").read_text(encoding="utf-8")
        assert content.count("первое") == 1
        assert content.count("второе") == 1  # не задублировалось -> sink один
        assert is_configured() is True

    def test_force_recreates_handlers(self, tmp_path: Path) -> None:
        cfg = _file_cfg(tmp_path)
        setup_logging(cfg, force=True)
        setup_logging(cfg, force=True)
        logger.info("после двух force")

        content = (tmp_path / "logs" / "test.log").read_text(encoding="utf-8")
        assert content.count("после двух force") == 1

    def test_returns_logger(self, tmp_path: Path) -> None:
        assert setup_logging(_file_cfg(tmp_path), force=True) is logger

    def test_accepts_full_settings(self, sample_config: Path, tmp_project: Path) -> None:
        settings = load_settings(sample_config, env_file=None)
        settings.logging.dir = str(tmp_project / "logs")
        settings.logging.console = False

        assert setup_logging(settings, force=True) is logger
        assert (tmp_project / "logs" / "lilith.log").is_file()

    def test_level_filter_respected(self, tmp_path: Path) -> None:
        setup_logging(_file_cfg(tmp_path, "lvl.log", level="ERROR"), force=True)
        logger.info("это не должно попасть в файл")
        logger.error("а это должно")

        content = (tmp_path / "logs" / "lvl.log").read_text(encoding="utf-8")
        assert "это не должно попасть в файл" not in content
        assert "а это должно" in content

    def test_console_sink_writes_to_stderr(self, tmp_path: Path, capsys) -> None:
        cfg = _file_cfg(tmp_path, "c.log", console=True, level="INFO")
        setup_logging(cfg, force=True)
        logger.info("видно в консоли")

        captured = capsys.readouterr()
        assert "видно в консоли" in captured.err

    def test_base_dir_argument(self, tmp_path: Path) -> None:
        setup_logging(LoggingSettings(dir="logs", console=False), force=True, base_dir=tmp_path)
        logger.info("относительный путь")
        assert (tmp_path / "logs" / "lilith.log").is_file()


class TestCapture:
    """Проверяем содержимое записей через перехват в StringIO."""

    def test_message_and_level_captured(self, tmp_path: Path) -> None:
        setup_logging(_file_cfg(tmp_path, "cap.log", level="DEBUG"), force=True)

        sink = io.StringIO()
        sink_id = logger.add(sink, level="DEBUG", format="{level}|{message}")
        logger.warning("тревога, Кирюша!")
        logger.remove(sink_id)

        assert "WARNING|тревога, Кирюша!" in sink.getvalue()

    def test_banner_writes_lines(self, tmp_path: Path) -> None:
        setup_logging(_file_cfg(tmp_path, "banner.log"), force=True)
        banner("LILITH.EXE v0.1.0\npanel -> http://127.0.0.1:8765/")

        content = (tmp_path / "logs" / "banner.log").read_text(encoding="utf-8")
        assert "LILITH.EXE v0.1.0" in content
        assert "http://127.0.0.1:8765/" in content

    def test_banner_default_text(self, tmp_path: Path) -> None:
        setup_logging(_file_cfg(tmp_path, "banner2.log"), force=True)
        banner()
        assert "LILITH.EXE" in (tmp_path / "logs" / "banner2.log").read_text(encoding="utf-8")

    def test_debug_helper_logs_kv(self, tmp_path: Path) -> None:
        setup_logging(_file_cfg(tmp_path, "kv.log", level="DEBUG"), force=True)
        logger.debug("ключ={} значение={}", "a", 1)
        assert "ключ=a значение=1" in (tmp_path / "logs" / "kv.log").read_text(encoding="utf-8")


class TestInterceptHandler:
    """Стандартный logging должен проваливаться в loguru."""

    def test_std_logging_intercepted(self, tmp_path: Path) -> None:
        setup_logging(_file_cfg(tmp_path, "intercept.log", level="DEBUG"), force=True)

        std = logging.getLogger("uvicorn.error")
        assert any(isinstance(h, InterceptHandler) for h in std.handlers)

        std.warning("сообщение из стандартного logging")
        content = (tmp_path / "logs" / "intercept.log").read_text(encoding="utf-8")
        assert "сообщение из стандартного logging" in content

    def test_root_logger_intercepted(self, tmp_path: Path) -> None:
        setup_logging(_file_cfg(tmp_path, "root.log"), force=True)
        logging.getLogger("какая_то_библиотека").info("привет из библиотеки")

        assert "привет из библиотеки" in (tmp_path / "logs" / "root.log").read_text(encoding="utf-8")

    def test_emit_with_unknown_level_falls_back(self, tmp_path: Path) -> None:
        setup_logging(_file_cfg(tmp_path, "odd.log", level="DEBUG"), force=True)

        record = logging.LogRecord("x", 21, "path", 1, "нестандартный уровень", None, None)
        record.levelname = "СОВСЕМ_НЕСТАНДАРТНЫЙ"
        InterceptHandler().emit(record)

        assert "нестандартный уровень" in (tmp_path / "logs" / "odd.log").read_text(encoding="utf-8")

    def test_emit_with_exception(self, tmp_path: Path) -> None:
        setup_logging(_file_cfg(tmp_path, "exc.log", level="DEBUG"), force=True)

        try:
            raise ValueError("баг")
        except ValueError:
            record = logging.LogRecord("x", logging.ERROR, "path", 1, "упало", None, sys.exc_info())
            InterceptHandler().emit(record)

        content = (tmp_path / "logs" / "exc.log").read_text(encoding="utf-8")
        assert "упало" in content
        assert "ValueError" in content
