"""Реестр персон-агентов (этап 5 v2 → этап 6): ``personas/<id>/`` — дом тела, души и голоса.

Этап 6 расширяет папку персоны до формата архитектора (**D1-б**: новый формат
основной, старый ``profile.yaml`` читается как legacy-фолбэк)::

    personas/lilith/
    ├── card.yaml       # id, display_name, version, persona_md, brain_profile,
    │                   # greeting, tags, lora{...}, tools[], memory_scope
    ├── voice.yaml      # pack, speaker, sample_rate, speed, pitch_shift,
    │                   # fallback_pack, reference_wav
    ├── face.yaml       # vrm_path, fallback, slot, эмоции/виземы-оверрайды, idle{...}
    ├── persona.md      # душа (системный промт) — как было
    ├── model.vrm       # тело (опционально; D5.4 — допускается ВНЕ репозитория)
    └── fallback.jpg    # статичный аватар

**D5.4:** VRM тяжелее десятков мегабайт, поэтому git его не таскает — путь к модели
берётся из ``face.yaml: vrm_path`` (абсолютный или относительно папки персоны).
Если ``vrm_path`` не задан, ищем ``model.vrm`` рядом.

**D8:** наружу (``/api/face/personas``) отдаётся сводка ``describe()`` — ``card``
целиком **не** публикуется.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

__all__ = [
    "Persona",
    "PersonaCard",
    "PersonaFace",
    "WindowSpec",
    "PersonaRegistry",
    "PersonaVoice",
    "LoraSpec",
    "IdleSpec",
    "CARD_FILE",
    "VOICE_FILE",
    "FACE_FILE",
]

CARD_FILE = "card.yaml"
VOICE_FILE = "voice.yaml"
FACE_FILE = "face.yaml"
LEGACY_PROFILE_FILE = "profile.yaml"

_FALLBACK_NAMES = ("fallback.png", "fallback.jpg", "fallback.jpeg", "avatar.png", "avatar.jpg")

#: Ключ ``card.yaml``, которые не попадают в публичную сводку (D8).
_CARD_PRIVATE = ("persona_md",)


def _load_yaml(path: Path) -> dict[str, Any]:
    """Прочитать YAML-словарь; битый файл — предупреждение и пустой словарь."""
    if not path.is_file():
        return {}
    try:
        spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        logger.warning("{} битый: {}", path.name, exc)
        return {}
    if not isinstance(spec, dict):
        logger.warning("{} должен быть словарём, получен {}", path.name, type(spec).__name__)
        return {}
    return spec


@dataclass(slots=True)
class LoraSpec:
    """LoRA-слот персоны (**D5**: поля внутри ``card.yaml``).

    Этап 6 работает в режиме *prompt-only* (**D10-а**): слот описан и виден в API,
    но реально веса не грузятся — персону отличает системный промт.
    """

    path: str = ""
    trigger_word: str = ""
    scale: float = 1.0
    autoload: bool = False

    @classmethod
    def from_dict(cls, raw: Any) -> "LoraSpec":
        """Собрать из YAML: ``lora: null`` → пустой слот."""
        if not isinstance(raw, dict):
            return cls()
        return cls(
            path=str(raw.get("path") or ""),
            trigger_word=str(raw.get("trigger_word") or ""),
            scale=float(raw.get("scale", 1.0) or 1.0),
            autoload=bool(raw.get("autoload", False)),
        )

    def as_dict(self) -> dict[str, Any]:
        """Словарь для API (``lora.state`` по D8)."""
        return {
            "configured": bool(self.path),
            "path": self.path or None,
            "trigger_word": self.trigger_word or None,
            "scale": self.scale,
            "autoload": self.autoload,
            "mode": "prompt-only",
        }


@dataclass(slots=True)
class PersonaCard:
    """Карточка персоны (``card.yaml``, схема **D2**)."""

    id: str = ""
    display_name: str = ""
    version: int = 1
    persona_md: str = "persona.md"
    brain_profile: str = ""
    greeting: str = ""
    tags: list[str] = field(default_factory=list)
    lora: LoraSpec = field(default_factory=LoraSpec)
    #: Белый список инструментов (**D6**: пусто = запрещено всё).
    tools: list[str] = field(default_factory=list)
    #: Область памяти персоны (**D7**): колонка ``persona_id`` + префикс коллекций.
    memory_scope: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any], persona_id: str) -> "PersonaCard":
        """Собрать карточку из словаря, подставляя разумные дефолты."""
        tags = raw.get("tags") or []
        tools = raw.get("tools") or []
        return cls(
            id=str(raw.get("id") or persona_id),
            display_name=str(raw.get("display_name") or raw.get("id") or persona_id),
            version=int(raw.get("version", 1) or 1),
            persona_md=str(raw.get("persona_md") or "persona.md"),
            brain_profile=str(raw.get("brain_profile") or ""),
            greeting=str(raw.get("greeting") or ""),
            tags=[str(t) for t in tags] if isinstance(tags, list) else [],
            lora=LoraSpec.from_dict(raw.get("lora")),
            tools=[str(t) for t in tools] if isinstance(tools, list) else [],
            memory_scope=str(raw.get("memory_scope") or persona_id),
        )

    def public_dict(self) -> dict[str, Any]:
        """Поля карточки для клиента (без приватных ключей)."""
        return {
            "id": self.id,
            "display_name": self.display_name,
            "version": self.version,
            "brain_profile": self.brain_profile,
            "greeting": self.greeting,
            "tags": list(self.tags),
            "tools": list(self.tools),
            "memory_scope": self.memory_scope,
        }


@dataclass(slots=True)
class PersonaVoice:
    """Голос персоны (``voice.yaml``, схема **D3** + слот **ADR-020.3**).

    **A2:** ``sample_rate`` — частота, к которой пак стандартизуется; продюсер
    лица приводит поток к ``face.producer_sample_rate`` в любом случае.

    **ADR-020.3** (приём из «Нейроны», где XTTS v2 выдаёт смех/вздох/хмыканье/крик
    тегами в стиле Bark): поле :attr:`nonverbal` — **слот**. В этапе 6 он только
    объявлен и виден в API; сам синтез нонвербалики отложен до этапа голоса.
    Формат: ``{событие: {"tag": "[laughs]", "weight": 1.0, "pack": ""}}``.
    """

    pack: str = ""
    speaker: str = ""
    sample_rate: int = 24000
    speed: float = 1.0
    pitch_shift: float = 0.0
    fallback_pack: str = ""
    reference_wav: str | None = None
    #: Слот нонвербальных событий (ADR-020.3): laugh/sigh/hum/cry/…
    nonverbal: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PersonaVoice":
        """Собрать из словаря; ``reference_wav: null`` и пустой ``nonverbal`` допустимы."""
        reference = raw.get("reference_wav")
        nonverbal = raw.get("nonverbal") or {}
        return cls(
            pack=str(raw.get("pack") or ""),
            speaker=str(raw.get("speaker") or raw.get("voice") or ""),
            sample_rate=int(raw.get("sample_rate", 24000) or 24000),
            speed=float(raw.get("speed", 1.0) or 1.0),
            pitch_shift=float(raw.get("pitch_shift", 0.0) or 0.0),
            fallback_pack=str(raw.get("fallback_pack") or ""),
            reference_wav=str(reference) if reference else None,
            nonverbal=nonverbal if isinstance(nonverbal, dict) else {},
        )

    def as_dict(self) -> dict[str, Any]:
        """Словарь для API (D8: ``voice{pack, speaker}`` + прочее)."""
        return {
            "pack": self.pack,
            "speaker": self.speaker,
            "sample_rate": self.sample_rate,
            "speed": self.speed,
            "pitch_shift": self.pitch_shift,
            "fallback_pack": self.fallback_pack,
            "reference_wav": self.reference_wav,
            "nonverbal": dict(self.nonverbal),
        }


@dataclass(slots=True)
class IdleSpec:
    """Параметры «живости» (**D4**): моргание, дыхание, взгляд."""

    blink_freq: float = 0.28
    breath_amp: float = 0.5
    look_speed: float = 4.0

    @classmethod
    def from_dict(cls, raw: Any) -> "IdleSpec":
        """Собрать из ``face.yaml: idle``."""
        if not isinstance(raw, dict):
            return cls()
        return cls(
            blink_freq=float(raw.get("blink_freq", 0.28) or 0.28),
            breath_amp=float(raw.get("breath_amp", 0.5) or 0.5),
            look_speed=float(raw.get("look_speed", 4.0) or 4.0),
        )

    def as_dict(self) -> dict[str, Any]:
        """Словарь для API и кадра ``persona``."""
        return {"blink_freq": self.blink_freq, "breath_amp": self.breath_amp, "look_speed": self.look_speed}


@dataclass(slots=True)
class WindowSpec:
    """Размер окна клиента под персону (**Q5**, хотфикс 0.6.2).

    Дефолт архитектора — **512×640, портрет 4:5**: в OBS такой кадр не приходится
    ни тянуть, ни резать. Персоны с крупным планом лица могут задать свой размер.
    """

    width: int = 512
    height: int = 640

    @classmethod
    def from_dict(cls, raw: Any) -> "WindowSpec":
        """Собрать из ``face.yaml: window``; битые значения → дефолт."""
        if not isinstance(raw, dict):
            return cls()
        try:
            width = int(raw.get("width", 512) or 512)
            height = int(raw.get("height", 640) or 640)
        except (TypeError, ValueError):
            return cls()
        return cls(width=max(64, width), height=max(64, height))

    def as_dict(self) -> dict[str, Any]:
        """Словарь для API и кадра ``persona``."""
        return {"width": self.width, "height": self.height}


@dataclass(slots=True)
class PersonaFace:
    """Лицо персоны (``face.yaml``): тело, оверрайды словарей, слот в группе.

    **D4-гибрид:** глобальные словари живут в :mod:`lilith_core.face.emotions`,
    здесь — только per-persona override.
    """

    vrm_path: str = ""
    fallback: str = ""
    #: Слот/позиция в групповой сцене (**E4**).
    slot: int = 0
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    #: Размер окна клиента (**Q5**): 512×640 портрет, если не задан.
    window: WindowSpec = field(default_factory=WindowSpec)
    #: emotion_tag → {blendshape: weight}.
    emotion_overrides: dict[str, dict[str, float]] = field(default_factory=dict)
    #: viseme → {blendshape: weight}.
    viseme_overrides: dict[str, dict[str, float]] = field(default_factory=dict)
    idle: IdleSpec = field(default_factory=IdleSpec)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], fallback_name: str = "") -> "PersonaFace":
        """Собрать из словаря ``face.yaml``."""
        position = raw.get("position") or [0.0, 0.0, 0.0]
        if not isinstance(position, (list, tuple)) or len(position) < 3:
            position = [0.0, 0.0, 0.0]
        emotions = raw.get("emotions") or {}
        visemes = raw.get("visemes") or {}
        return cls(
            vrm_path=str(raw.get("vrm_path") or raw.get("vrm") or ""),
            fallback=str(raw.get("fallback") or fallback_name),
            slot=int(raw.get("slot", 0) or 0),
            window=WindowSpec.from_dict(raw.get("window")),
            position=(float(position[0]), float(position[1]), float(position[2])),
            emotion_overrides=emotions if isinstance(emotions, dict) else {},
            viseme_overrides=visemes if isinstance(visemes, dict) else {},
            idle=IdleSpec.from_dict(raw.get("idle")),
        )

    def as_dict(self) -> dict[str, Any]:
        """Словарь для API и кадра ``persona``."""
        return {
            "vrm_path": self.vrm_path or None,
            "fallback": self.fallback or None,
            "slot": self.slot,
            "position": list(self.position),
            "window": self.window.as_dict(),
            "emotion_overrides": dict(self.emotion_overrides),
            "viseme_overrides": dict(self.viseme_overrides),
            "idle": self.idle.as_dict(),
        }


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
    #: Этап 6: три yaml-документа персоны.
    card: PersonaCard = field(default_factory=PersonaCard)
    voice_spec: PersonaVoice = field(default_factory=PersonaVoice)
    face_spec: PersonaFace = field(default_factory=PersonaFace)
    #: Откуда прочитаны voice/brain: ``card`` (новый формат) или ``profile`` (legacy).
    source: str = "card"

    # -- тело ------------------------------------------------------------------ #
    def vrm_source(self) -> Path | None:
        """Откуда брать VRM: ``face.yaml: vrm_path`` или ``model.vrm`` в папке."""
        if self.face_spec.vrm_path:
            candidate = Path(self.face_spec.vrm_path).expanduser()
            if not candidate.is_absolute():
                candidate = self.path / candidate
            return candidate
        local = self.path / "model.vrm"
        return local

    @property
    def vrm_available(self) -> bool:
        """Лежит ли тело по реальному пути (D5.4: модель может быть вне репо)."""
        source = self.vrm_source()
        return bool(source and source.is_file())

    @property
    def persona_md_path(self) -> Path:
        """Путь к ``persona.md`` персоны (имя берём из карточки)."""
        return self.path / (self.card.persona_md or "persona.md")

    # -- сериализация ----------------------------------------------------------- #
    def as_dict(self) -> dict[str, Any]:
        """Обратная совместимость с этапом 5: сводка для ``/api/personas``."""
        return {
            "id": self.id,
            "has_persona_md": self.has_persona_md,
            "has_vrm": self.has_vrm,
            "fallback": f"/personas/{self.id}/{self.fallback}" if self.fallback else None,
            "vrm": f"/personas/{self.id}/model.vrm" if self.has_vrm else None,
            "voice": self.voice,
            "brain": self.brain,
        }

    def describe(self) -> dict[str, Any]:
        """Публичная сводка персоны для ``/api/face/personas`` (**D8**).

        ``card`` целиком не отдаётся — только выбранные поля.
        """
        return {
            "id": self.id,
            "display_name": self.card.display_name,
            "vrm": f"/api/face/personas/{self.id}/model.vrm" if self.vrm_available else None,
            "has_vrm": self.vrm_available,
            "voice": {"pack": self.voice_spec.pack, "speaker": self.voice_spec.speaker},
            "fallback": f"/personas/{self.id}/{self.fallback}" if self.fallback else None,
            "lora": {"state": "prompt-only", **self.card.lora.as_dict()},
            "tools": list(self.card.tools),
            "memory_scope": self.card.memory_scope,
            "greeting": self.card.greeting,
            "tags": list(self.card.tags),
            "face": self.face_spec.as_dict(),
            "source": self.source,
        }


class PersonaRegistry:
    """Сканирует каталог персон и держит их описание горячим.

    Этап 6 добавляет: три YAML-документа персоны, legacy-фолбэк на ``profile.yaml``,
    понятие **активной** персоны (глобальный своп, **D9**) и разрешение VRM-пути
    вне репозитория.
    """

    def __init__(self, root: str | Path, active: str = "") -> None:
        self.root = Path(root)
        self._personas: dict[str, Persona] = {}
        self._active: str = active
        self.reload()

    # -- сканирование ------------------------------------------------------------ #
    def reload(self) -> None:
        """Перечитывает каталог (новые персоны подхватываются без перезапуска)."""
        self._personas = {}
        if not self.root.is_dir():
            logger.warning("Каталог персон не найден: {}", self.root)
            return
        for folder in sorted(p for p in self.root.iterdir() if p.is_dir()):
            if folder.name.startswith(("_", ".")):
                continue  # служебные папки (например, _template) персонами не считаются
            self._personas[folder.name] = self._read(folder)
        if self._active not in self._personas:
            self._active = self.ids()[0] if self._personas else ""
        logger.info(
            "Реестр персон: {} ({}), активна '{}'",
            len(self._personas),
            ", ".join(self._personas) or "пусто",
            self._active or "—",
        )

    def _read(self, folder: Path) -> Persona:
        """Прочитать одну папку персоны (новый формат + legacy-фолбэк)."""
        persona = Persona(id=folder.name, path=folder)
        persona.has_persona_md = (folder / "persona.md").is_file()
        persona.has_vrm = (folder / "model.vrm").is_file()
        for name in _FALLBACK_NAMES:
            if (folder / name).is_file():
                persona.fallback = name
                break

        card_raw = _load_yaml(folder / CARD_FILE)
        voice_raw = _load_yaml(folder / VOICE_FILE)
        face_raw = _load_yaml(folder / FACE_FILE)
        legacy_raw = _load_yaml(folder / LEGACY_PROFILE_FILE)

        persona.card = PersonaCard.from_dict(card_raw, folder.name)
        persona.voice_spec = PersonaVoice.from_dict(voice_raw or {})
        persona.face_spec = PersonaFace.from_dict(face_raw or {}, fallback_name=persona.fallback or "")

        if voice_raw or card_raw:
            persona.source = "card"
            persona.voice = persona.voice_spec.pack or persona.voice_spec.speaker
            persona.brain = persona.card.brain_profile
        elif legacy_raw:
            # D1-б: legacy-фолбэк — старый profile.yaml продолжает работать.
            persona.source = "profile"
            persona.voice = str(legacy_raw.get("voice", "") or "")
            persona.brain = str(legacy_raw.get("brain", "") or "")
            persona.voice_spec.pack = persona.voice
            persona.card.brain_profile = persona.brain
            persona.extra = {k: v for k, v in legacy_raw.items() if k not in ("voice", "brain")}
        persona.has_persona_md = persona.has_persona_md or persona.persona_md_path.is_file()
        return persona

    # -- доступ -------------------------------------------------------------------- #
    def ids(self) -> list[str]:
        """Идентификаторы персон по алфавиту."""
        return sorted(self._personas)

    def get(self, persona_id: str) -> Persona | None:
        """Персона по id или ``None``."""
        return self._personas.get(persona_id)

    def list(self) -> list[dict[str, Any]]:
        """Сводка в формате этапа 5 (``/api/personas``-алиас)."""
        return [self._personas[i].as_dict() for i in self.ids()]

    def describe(self) -> list[dict[str, Any]]:
        """Публичная сводка в формате этапа 6 (``/api/face/personas``, **D8**)."""
        return [self._personas[i].describe() for i in self.ids()]

    def __len__(self) -> int:
        return len(self._personas)

    def __contains__(self, persona_id: object) -> bool:
        return persona_id in self._personas

    # -- активная персона (D9: глобальный своп в этапе 6) -------------------------- #
    @property
    def active(self) -> str:
        """Id активной персоны (пустая строка, если реестр пуст)."""
        if self._active not in self._personas:
            ids = self.ids()
            self._active = ids[0] if ids else ""
        return self._active

    def set_active(self, persona_id: str) -> Persona | None:
        """Переключить активную персону; возвращает её или ``None``, если нет такой."""
        persona = self._personas.get(persona_id)
        if persona is None:
            return None
        self._active = persona_id
        return persona

    def active_persona(self) -> Persona | None:
        """Активная персона целиком."""
        return self._personas.get(self.active) if self.active else None
