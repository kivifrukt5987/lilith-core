"""Настройка логирования на loguru.

Весь проект логирует **только** через ``loguru.logger``. Стандартный ``logging``
(uvicorn, fastapi, сторонние библиотеки) перехватывается :class:`InterceptHandler`
и прокидывается в тот же стек, поэтому в файле лога видна вся картина.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from .config import LoggingSettings, Settings, get_settings, resolve_path

__all__ = ["InterceptHandler", "setup_logging", "logger", "LOG_FORMAT", "LOG_FILE_FORMAT"]

#: Формат для консоли: цветной, компактный.
LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> — <level>{message}</level>"
)

#: Формат для файла: без разметки, с PID и именем процесса.
LOG_FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {process} | "
    "{name}:{function}:{line} | {message}"
)

#: Какие сторонние логгеры перехватываем.
_INTERCEPTED_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi", "asyncio", "websockets")

_CONFIGURED = False


class InterceptHandler(logging.Handler):
    """Мост из стандартного ``logging`` в ``loguru``."""

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        # Определяем соответствующий уровень loguru; для нестандартных уровней
        # падаем в WARNING, чтобы ничего не потерялось.
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Поднимаемся по стеку, чтобы loguru показал реальное место вызова.
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging(
    settings: Settings | LoggingSettings | None = None,
    *,
    force: bool = False,
    base_dir: Path | str | None = None,
) -> Any:
    """Инициализирует логирование. Повторные вызовы безопасны (идемпотентно).

    :param settings: :class:`~lilith_core.config.Settings` целиком или только секцию
        ``LoggingSettings``. Если ``None`` — берётся глобальный конфиг.
    :param force: пересоздать обработчики, даже если логирование уже настроено
        (используется в тестах).
    :param base_dir: базовая директория для относительного пути к логам.
    :return: настроенный ``loguru.logger``.
    """
    global _CONFIGURED

    if _CONFIGURED and not force:
        return logger

    if isinstance(settings, LoggingSettings):
        log_cfg = settings
    elif settings is None:
        log_cfg = get_settings().logging
    else:
        log_cfg = settings.logging

    logger.remove()

    if log_cfg.console:
        logger.add(
            sys.stderr,
            level=log_cfg.level.upper(),
            format=LOG_FORMAT,
            colorize=True,
            backtrace=log_cfg.backtrace,
            diagnose=log_cfg.diagnose,
            enqueue=False,
        )

    log_dir = Path(log_cfg.dir).expanduser()
    if not log_dir.is_absolute():
        # Явно переданный base_dir важнее эвристик с текущей директорией —
        # иначе относительный "logs" прилип бы к cwd процесса.
        base = Path(base_dir) if base_dir else None
        log_dir = resolve_path(log_dir, base=base)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / log_cfg.file_name

    logger.add(
        str(log_file),
        level=log_cfg.level.upper(),
        format=LOG_FILE_FORMAT,
        rotation=log_cfg.rotation,
        retention=log_cfg.retention,
        compression=log_cfg.compression or None,
        encoding="utf-8",
        backtrace=log_cfg.backtrace,
        diagnose=log_cfg.diagnose,
        # enqueue=False -> запись синхронная и детерминированная (важно для тестов).
        # Для продакшена с несколькими процессами можно включить очередь:
        # тогда добавьте enqueue=True и вызывайте logger.complete() перед выходом.
        enqueue=False,
    )

    # Перехватываем стандартный logging.
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    for name in _INTERCEPTED_LOGGERS:
        std_logger = logging.getLogger(name)
        std_logger.handlers = [InterceptHandler()]
        std_logger.propagate = False

    _CONFIGURED = True
    logger.debug(
        "Логирование настроено: level={} file={}",
        log_cfg.level.upper(),
        log_file,
    )
    return logger


def is_configured() -> bool:
    """Было ли логирование инициализировано хотя бы один раз."""
    return _CONFIGURED


def banner(text: str = "LILITH.EXE") -> None:
    """Печатает стартовый баннер в лог."""
    line = "═" * 62
    logger.info(line)
    for chunk in text.splitlines() or [text]:
        logger.info("  {}", chunk)
    logger.info(line)
