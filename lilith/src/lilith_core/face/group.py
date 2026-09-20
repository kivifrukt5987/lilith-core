"""Групповые сцены (этап 6, серверная часть): несколько персон в одном кадре.

Решения архитектора (блок E):

* **E1** — участников может быть N, но с потолком ``face.group_max_participants`` (4);
* **E2-а** — фокус задаёт **сервер** кадром ``focus``, Unity только плавно доводит камеру;
* **E3** — **один** WebSocket на группу, кадры мультиплексируются полем ``persona``;
* **E4** — рассадка берётся из ``personas/<id>/face.yaml: slot/position``, сервер
  отдаёт её в ``hello`` группы (плюс ``group.yaml`` поверх, если лежит);
* **E5** — каждая персона получает **свои** аудио/виземы/эмоции со своим
  ``utterance_id``; говорит только активная (фокусная) — «хор» запрещён оркестратором;
* **E6** — камера живёт в Unity; OBS-сцены это этап 8;
* **E7** — в этапе 6 только контракт и серверная часть, Unity-сцена группы позже.

Класс :class:`GroupSession` держит участников и их слоты, :class:`GroupManager` —
реестр сессий по имени группы.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from .personas import Persona, PersonaRegistry

__all__ = ["GroupMember", "GroupSession", "GroupManager", "GROUP_FILE", "GroupFull", "UnknownPersona"]

#: Файл рассадки группы поверх ``face.yaml`` (E4).
GROUP_FILE = "group.yaml"


class GroupFull(RuntimeError):
    """В группе уже максимум участников (E1)."""


class UnknownPersona(RuntimeError):
    """Персоны нет в реестре."""


@dataclass(slots=True)
class GroupMember:
    """Участник группы: персона + её слот/позиция в сцене."""

    persona_id: str
    slot: int = 0
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)

    def as_dict(self) -> dict[str, Any]:
        """Словарь для ``hello`` группы."""
        return {
            "persona": self.persona_id,
            "slot": self.slot,
            "position": list(self.position),
        }


@dataclass(slots=True)
class GroupSession:
    """Одна групповая сцена.

    :param name: имя группы (``main`` по умолчанию).
    :param max_participants: потолок участников (**E1**).
    :param queues: очереди кадров по подключениям (мультиплексирование, **E3**).
    """

    name: str
    max_participants: int = 4
    members: list[GroupMember] = field(default_factory=list)
    focus: str = ""
    queues: set[asyncio.Queue] = field(default_factory=set)
    stats: dict[str, Any] = field(default_factory=dict)

    # -- участники ----------------------------------------------------------------- #
    def persona_ids(self) -> list[str]:
        """Участники в порядке слотов."""
        return [m.persona_id for m in sorted(self.members, key=lambda m: m.slot)]

    def has(self, persona_id: str) -> bool:
        """Участник уже в сцене?"""
        return any(m.persona_id == persona_id for m in self.members)

    def join(self, persona: Persona, *, slot: int | None = None) -> GroupMember:
        """Добавить персону в сцену; слот — из ``face.yaml``, если не задан.

        :raises GroupFull: достигнут потолок участников.
        """
        if self.has(persona.id):
            return next(m for m in self.members if m.persona_id == persona.id)
        if len(self.members) >= self.max_participants:
            raise GroupFull(
                f"в группе '{self.name}' уже {len(self.members)} участника "
                f"(потолок face.group_max_participants={self.max_participants})"
            )
        used = {m.slot for m in self.members}
        chosen = persona.face_spec.slot if slot is None else int(slot)
        if chosen in used:
            chosen = next((s for s in range(self.max_participants) if s not in used), len(self.members))
        member = GroupMember(persona_id=persona.id, slot=chosen, position=persona.face_spec.position)
        self.members.append(member)
        if not self.focus:
            self.focus = persona.id
        logger.info("Группа '{}': + '{}' (слот {})", self.name, persona.id, chosen)
        return member

    def leave(self, persona_id: str) -> bool:
        """Убрать персону из сцены; фокус переезжает на соседа."""
        before = len(self.members)
        self.members = [m for m in self.members if m.persona_id != persona_id]
        if len(self.members) == before:
            return False
        if self.focus == persona_id:
            self.focus = self.persona_ids()[0] if self.members else ""
        logger.info("Группа '{}': − '{}' (осталось {})", self.name, persona_id, len(self.members))
        return True

    # -- рассылка (E3: один сокет на группу, кадры с полем persona) ---------------- #
    def subscribe(self, maxsize: int = 256) -> asyncio.Queue:
        """Очередь нового подключения."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self.queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Отписать подключение."""
        self.queues.discard(queue)

    @property
    def subscribers(self) -> int:
        """Сколько подключений слушают группу."""
        return len(self.queues)

    async def publish(self, frame: dict[str, Any]) -> int:
        """Разослать кадр; переполненная очередь кадр пропускает (не блокируем TTS)."""
        delivered = 0
        for queue in list(self.queues):
            try:
                queue.put_nowait(frame)
                delivered += 1
            except asyncio.QueueFull:
                continue
        return delivered

    # -- сводка -------------------------------------------------------------------- #
    def layout(self) -> dict[str, Any]:
        """``hello`` группы: состав, рассадка, фокус, потолок (E4)."""
        return {
            "group": self.name,
            "max_participants": self.max_participants,
            "participants": [m.as_dict() for m in sorted(self.members, key=lambda m: m.slot)],
            "focus": self.focus,
            "subscribers": self.subscribers,
        }


class GroupManager:
    """Реестр групповых сцен и загрузка рассадки из ``group.yaml``."""

    def __init__(self, registry: PersonaRegistry, *, max_participants: int = 4, group_file: str | Path = "") -> None:
        self.registry = registry
        self.max_participants = max_participants
        self.group_file = Path(group_file) if group_file else None
        self._sessions: dict[str, GroupSession] = {}

    # -- сессии ---------------------------------------------------------------------- #
    def get(self, name: str = "main", *, create: bool = True) -> GroupSession | None:
        """Сессия по имени (создаётся лениво)."""
        session = self._sessions.get(name)
        if session is None and create:
            session = GroupSession(name=name, max_participants=self.max_participants)
            self._sessions[name] = session
        return session

    def names(self) -> list[str]:
        """Имена известных групп."""
        return sorted(self._sessions)

    def drop(self, name: str) -> bool:
        """Забыть пустую группу."""
        session = self._sessions.get(name)
        if session is None:
            return False
        if session.members or session.subscribers:
            return False
        del self._sessions[name]
        return True

    # -- состав ------------------------------------------------------------------------ #
    def default_layout(self, name: str = "main") -> list[str]:
        """Кто входит в группу по умолчанию: ``group.yaml`` или все персоны (до потолка)."""
        spec = self._load_group_file()
        declared = spec.get(name) if isinstance(spec, dict) else None
        if isinstance(declared, dict):
            participants = declared.get("participants") or []
            if isinstance(participants, list) and participants:
                return [str(p) for p in participants][: self.max_participants]
        return self.registry.ids()[: self.max_participants]

    def _load_group_file(self) -> dict[str, Any]:
        """Прочитать ``group.yaml`` (не найден/битый → пусто)."""
        if self.group_file is None or not self.group_file.is_file():
            return {}
        try:
            data = yaml.safe_load(self.group_file.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            logger.warning("{} битый: {}", self.group_file.name, exc)
            return {}
        return data if isinstance(data, dict) else {}

    def ensure(self, name: str = "main", *, participants: list[str] | None = None) -> GroupSession:
        """Собрать группу: участники из аргумента, иначе из ``default_layout``.

        :raises UnknownPersona: запрошенной персоны нет в реестре.
        """
        session = self.get(name) or GroupSession(name=name, max_participants=self.max_participants)
        self._sessions[name] = session
        wanted = list(participants) if participants else self.default_layout(name)
        for persona_id in wanted:
            if session.has(persona_id):
                continue
            persona = self.registry.get(persona_id)
            if persona is None:
                raise UnknownPersona(f"персона '{persona_id}' не найдена в реестре")
            try:
                session.join(persona)
            except GroupFull as exc:
                logger.warning("Группа '{}': {}", name, exc)
                break
        if not session.focus and session.members:
            session.focus = session.persona_ids()[0]
        return session

    # -- фокус (E2-а) ------------------------------------------------------------------- #
    async def set_focus(self, name: str, persona_id: str) -> bool:
        """Перевести фокус на участника группы; ``False``, если его там нет."""
        session = self._sessions.get(name)
        if session is None or not session.has(persona_id):
            return False
        session.focus = persona_id
        return True

    def describe(self) -> dict[str, Any]:
        """Сводка всех групп для ``/api/face/groups`` и ``state``."""
        return {name: session.layout() for name, session in sorted(self._sessions.items())}
