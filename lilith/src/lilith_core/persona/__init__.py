"""Загрузка и подготовка системного промта персонажа.

На этапе 1 модуль просто читает ``persona.md`` и подставляет плейсхолдеры.
На этапе 2 результат функции :func:`build_system_prompt` станет системным
сообщением в запросе к LLM.

Поддерживаемые плейсхолдеры (``string.Template``, синтаксис ``$имя``)::

    $user_name, $persona_name, $now, $date, $time, $weekday, $version, $stage
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Any

from loguru import logger

from lilith_core import __stage__, __version__
from ..config import Settings, get_settings, resolve_path

__all__ = ["Persona", "load_persona", "build_system_prompt", "WEEKDAYS_RU"]

#: Названия дней недели по-русски (индекс = ``datetime.weekday()``).
WEEKDAYS_RU = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")


@dataclass(slots=True)
class Persona:
    """Загруженный системный промт персонажа."""

    path: Path | None
    text: str
    variables: dict[str, str]

    @property
    def is_empty(self) -> bool:
        """True, если файл персоны не найден или пуст."""
        return not self.text.strip()

    @property
    def char_count(self) -> int:
        """Длина текста в символах (грубая оценка размера промта)."""
        return len(self.text)

    def info(self) -> dict[str, Any]:
        """Сводка для логов и healthz."""
        return {
            "path": str(self.path) if self.path else None,
            "exists": bool(self.path and self.path.is_file()),
            "chars": self.char_count,
            "empty": self.is_empty,
        }


def _variables(settings: Settings) -> dict[str, str]:
    """Формирует словарь подстановок для шаблона персоны."""
    now = datetime.now()
    return {
        "user_name": settings.app.user_name,
        "persona_name": settings.app.persona_name,
        "now": now.strftime("%Y-%m-%d %H:%M"),
        "date": now.strftime("%d.%m.%Y"),
        "time": now.strftime("%H:%M"),
        "weekday": WEEKDAYS_RU[now.weekday()],
        "version": __version__,
        "stage": str(__stage__),
    }


def load_persona(
    path: str | Path | None = None,
    *,
    settings: Settings | None = None,
    variables: dict[str, str] | None = None,
) -> Persona:
    """Читает ``persona.md`` и подставляет переменные.

    :param path: явный путь к файлу; если ``None`` — берётся из конфига.
    :param settings: готовый объект настроек (иначе загружается глобальный).
    :param variables: переопределение переменных подстановки.
    :return: :class:`Persona`; при отсутствии файла — пустой персонаж (без падения).
    """
    settings = settings or get_settings()
    raw_path = Path(path) if path else resolve_path(settings.app.persona_path)
    merged = {**_variables(settings), **(variables or {})}

    if not raw_path.is_file():
        logger.warning("Persona: файл не найден — {}: работаем без системного промта", raw_path)
        return Persona(path=raw_path, text="", variables=merged)

    text = raw_path.read_text(encoding="utf-8")
    # safe_substitute: неизвестный плейсхолдер не ломает загрузку, остаётся как есть.
    rendered = Template(text).safe_substitute(**merged)
    logger.debug("Persona: загружено {} ({} символов)", raw_path, len(rendered))
    return Persona(path=raw_path, text=rendered, variables=merged)


def build_system_prompt(
    *,
    settings: Settings | None = None,
    persona_path: str | Path | None = None,
    extra: str | None = None,
) -> str:
    """Собирает итоговый системный промт: персона + технические примечания.

    :param extra: дополнительный текст (по умолчанию ``brain.system_prompt_extra``).
    """
    settings = settings or get_settings()
    persona = load_persona(persona_path, settings=settings)
    parts: list[str] = []
    if persona.text.strip():
        parts.append(persona.text.strip())
    else:
        parts.append(
            f"Ты — {settings.app.persona_name}, ИИ-компаньон. Файл персоны не найден, "
            f"веди себя дружелюбно и по делу."
        )

    extra_text = settings.brain.system_prompt_extra if extra is None else extra
    if extra_text and extra_text.strip():
        parts.append(extra_text.strip())

    return "\n\n---\n\n".join(parts)
