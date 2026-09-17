"""Реестр персон (этап 5 v2): ``personas/<id>/`` — дом для тела, души и голоса.

Структура папки персоны::

    personas/lilith/
    ├── persona.md      # системный промт (душа)
    ├── model.vrm       # 3D-тело (опционально; без него — фолбэк)
    ├── fallback.jpg    # статичный аватар (png/jpg)
    └── profile.yaml    # опционально: голос/brain-профиль персоны

Реестр отдаёт панели список персон и статику (``/personas/<id>/…``), а на этапе 8
становится источником вкладок «Агенты» и «3D-модели».
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

__all__ = ["Persona", "PersonaRegistry"]

_FALLBACK_NAMES = ("fallback.png", "fallback.jpg", "fallback.jpeg", "avatar.png", "avatar.jpg")


@dataclass(slots=True)
class Persona:
    """Описание персоны: что нашлось в её папке."""

    id: str
    path: Path
    has_persona_md: bool = False
    has_vrm: bool = False
    fallback: str | None = None  # имя файла картинки внутри папки
    voice: str = ""
    brain: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Словарь для /api/personas и панели."""
        return {
            "id": self.id,
            "has_persona_md": self.has_persona_md,
            "has_vrm": self.has_vrm,
            "fallback": f"/personas/{self.id}/{self.fallback}" if self.fallback else None,
            "vrm": f"/personas/{self.id}/model.vrm" if self.has_vrm else None,
            "voice": self.voice,
            "brain": self.brain,
        }


class PersonaRegistry:
    """Сканирует каталог персон и держит их описание горячим."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._personas: dict[str, Persona] = {}
        self.reload()

    def reload(self) -> None:
        """Перечитывает каталог (новые персоны подхватываются без перезапуска)."""
        self._personas = {}
        if not self.root.is_dir():
            logger.warning("Каталог персон не найден: {}", self.root)
            return
        for folder in sorted(p for p in self.root.iterdir() if p.is_dir()):
            persona = Persona(id=folder.name, path=folder)
            persona.has_persona_md = (folder / "persona.md").is_file()
            persona.has_vrm = (folder / "model.vrm").is_file()
            for name in _FALLBACK_NAMES:
                if (folder / name).is_file():
                    persona.fallback = name
                    break
            profile_file = folder / "profile.yaml"
            if profile_file.is_file():
                try:
                    spec = yaml.safe_load(profile_file.read_text(encoding="utf-8")) or {}
                    persona.voice = str(spec.get("voice", "") or "")
                    persona.brain = str(spec.get("brain", "") or "")
                    persona.extra = {k: v for k, v in spec.items() if k not in ("voice", "brain")}
                except yaml.YAMLError as exc:
                    logger.warning("profile.yaml персоны {} битый: {}", folder.name, exc)
            self._personas[persona.id] = persona
        logger.info("Реестр персон: {} ({})", len(self._personas), ", ".join(self._personas) or "пусто")

    def ids(self) -> list[str]:
        """Идентификаторы персон по алфавиту."""
        return sorted(self._personas)

    def get(self, persona_id: str) -> Persona | None:
        """Персона по id или None."""
        return self._personas.get(persona_id)

    def list(self) -> list[dict[str, Any]]:
        """Сводка для /api/personas."""
        return [self._personas[i].as_dict() for i in self.ids()]
