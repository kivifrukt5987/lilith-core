"""Рантайм-настройки памяти: меняются из программы и переживают перезапуск.

Кирюша попросил: «саммари не на каждые 40, а чтобы настраивать самому внутри
программы». Реализация: ограниченный набор полей ``settings.memory`` можно
менять через ``POST /api/memory/settings`` (и шестерёнку в панели); значения
сохраняются в ``data/runtime_settings.json`` и применяются при старте поверх YAML.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import ValidationError

from ..config import Settings, resolve_path

__all__ = ["RUNTIME_FIELDS", "runtime_path", "load_runtime_settings", "save_runtime_settings", "apply_runtime_update"]

#: Поля памяти, которые можно менять на лету из программы.
RUNTIME_FIELDS: tuple[str, ...] = (
    "summarize_every_n",
    "top_k",
    "rag_enabled",
    "auto_summarize",
)


def runtime_path(settings: Settings) -> Path:
    """Путь к файлу рантайм-настроек памяти."""
    return resolve_path(settings.memory.runtime_settings_path)


def load_runtime_settings(settings: Settings) -> dict[str, Any]:
    """Применяет сохранённые рантайм-значения поверх конфига при старте."""
    path = runtime_path(settings)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Рантайм-настройки памяти нечитаемы ({}), игнорирую", exc)
        return {}

    applied: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in RUNTIME_FIELDS:
            logger.debug("Рантайм: неизвестное поле {} пропущено", key)
            continue
        try:
            setattr(settings.memory, key, value)
            applied[key] = value
        except ValidationError as exc:
            logger.warning("Рантайм: значение {}={} отклонено: {}", key, value, exc.errors()[:1])
    if applied:
        logger.info("Рантайм-настройки памяти применены при старте: {}", applied)
    return applied


def save_runtime_settings(settings: Settings) -> Path:
    """Сохраняет текущие рантайм-значения в JSON (переживают перезапуск)."""
    path = runtime_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: getattr(settings.memory, key) for key in RUNTIME_FIELDS}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Рантайм-настройки памяти сохранены: {}", path)
    return path


def apply_runtime_update(settings: Settings, patch: dict[str, Any]) -> dict[str, Any]:
    """Применяет частичное обновление рантайм-полей и сохраняет его.

    :raises ValueError: если пришло поле вне :data:`RUNTIME_FIELDS`.
    :raises ValidationError: если значение не прошло валидацию pydantic.
    """
    unknown = sorted(set(patch) - set(RUNTIME_FIELDS))
    if unknown:
        raise ValueError(f"настраиваемые поля памяти только {RUNTIME_FIELDS}, чужие: {unknown}")

    for key, value in patch.items():
        setattr(settings.memory, key, value)  # validate_assignment отработает

    save_runtime_settings(settings)
    return {key: getattr(settings.memory, key) for key in RUNTIME_FIELDS}
