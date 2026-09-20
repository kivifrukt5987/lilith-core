"""Журнал памяти на aiosqlite: сообщения, факты, саммари, служебные метки.

Один файл БД на весь проект (``memory.db_path``), изоляция по ``agent_id``.
Интерфейс синхронен с будущими потребностями этапа 3+: ``recent_messages``
отдаёт историю чат-циклу, ``facts``/саммари кормят RAG и контекст.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite
from loguru import logger

__all__ = ["Journal", "JournalMessage"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    TEXT NOT NULL,
    session_id  TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'webui',
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    profile     TEXT,
    persona_id  TEXT NOT NULL DEFAULT '',   -- этап 6 (D7-б): от чьего лица запись
    ts          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_messages_agent ON messages (agent_id, id);
CREATE INDEX IF NOT EXISTS ix_messages_persona ON messages (persona_id, id);

CREATE TABLE IF NOT EXISTS facts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    TEXT NOT NULL,
    kind        TEXT NOT NULL,           -- 'fact' | 'summary'
    content     TEXT NOT NULL,
    ts          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_facts_agent ON facts (agent_id, id);

CREATE TABLE IF NOT EXISTS meta (
    agent_id    TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    PRIMARY KEY (agent_id, key)
);
"""


@dataclass(slots=True)
class JournalMessage:
    """Строка журнала сообщений."""

    id: int
    agent_id: str
    session_id: str
    source: str
    role: str
    content: str
    profile: str | None
    ts: str
    #: Этап 6 (D7-б): персона, от чьего лица сделана запись (пусто = agent_id).
    persona_id: str = ""


class Journal:
    """Асинхронный журнал памяти (aiosqlite, один connection на экземпляр)."""

    def __init__(self, db_path: str | Path, *, max_messages: int = 5000) -> None:
        self.db_path = Path(db_path)
        self.max_messages = max_messages
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    # -- жизненный цикл ------------------------------------------------------ #
    async def start(self) -> None:
        """Создаёт каталоги, открывает соединение и схему."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        async with self._lock:
            await self._db.executescript(_SCHEMA)
            await self._migrate_locked()
            await self._db.commit()
        logger.info("Журнал памяти открыт: {}", self.db_path)

    async def _migrate_locked(self) -> None:
        """Дотягивает старую БД до схемы этапа 6 (колонка ``persona_id``, D7-б).

        ``CREATE TABLE IF NOT EXISTS`` не добавляет колонки в существующую таблицу,
        поэтому старые ``data/lilith.db`` доводятся ``ALTER TABLE`` — идемпотентно.
        """
        cursor = await self._conn().execute("PRAGMA table_info(messages)")
        columns = {str(row[1]) for row in await cursor.fetchall()}
        if "persona_id" not in columns:
            await self._conn().execute(
                "ALTER TABLE messages ADD COLUMN persona_id TEXT NOT NULL DEFAULT ''"
            )
            await self._conn().execute(
                "CREATE INDEX IF NOT EXISTS ix_messages_persona ON messages (persona_id, id)"
            )
            await self._conn().execute("UPDATE messages SET persona_id = agent_id WHERE persona_id = ''")
            logger.info("Журнал: миграция — добавлена колонка persona_id")

    async def stop(self) -> None:
        """Закрывает соединение."""
        if self._db is not None:
            await self._db.close()
            self._db = None
            logger.info("Журнал памяти закрыт: {}", self.db_path)

    @property
    def closed(self) -> bool:
        """True, если соединение не открыто."""
        return self._db is None

    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Journal не запущен: вызови start()")
        return self._db

    # -- сообщения ----------------------------------------------------------- #
    async def add_message(
        self,
        agent_id: str,
        role: str,
        content: str,
        *,
        session_id: str = "",
        source: str = "webui",
        profile: str | None = None,
        persona_id: str = "",
    ) -> int:
        """Пишет реплику в журнал и возвращает её id.

        :param persona_id: от чьего лица запись (этап 6, D7-б); пусто → ``agent_id``.
        """
        async with self._lock:
            cur = await self._conn().execute(
                "INSERT INTO messages (agent_id, session_id, source, role, content, profile, persona_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (agent_id, session_id, source, role, content, profile, persona_id or agent_id),
            )
            await self._conn().commit()
            new_id = int(cur.lastrowid or 0)
            await self._prune_locked()
            return new_id

    async def _prune_locked(self) -> None:
        """Держит журнал в рамках ``max_messages`` на агента (старые уходят)."""
        await self._conn().execute(
            """
            DELETE FROM messages WHERE id NOT IN (
                SELECT id FROM messages AS m2
                WHERE m2.agent_id = messages.agent_id
                ORDER BY id DESC LIMIT ?
            )
            """,
            (self.max_messages,),
        )
        await self._conn().commit()

    async def recent_messages(self, agent_id: str, limit: int = 40) -> list[JournalMessage]:
        """Последние сообщения агента (по возрастанию времени)."""
        cur = await self._conn().execute(
            "SELECT * FROM messages WHERE agent_id = ? ORDER BY id DESC LIMIT ?",
            (agent_id, limit),
        )
        rows = await cur.fetchall()
        return [self._row_to_message(row) for row in reversed(rows)]

    async def messages_after(self, agent_id: str, after_id: int) -> list[JournalMessage]:
        """Сообщения строго после ``after_id`` (для саммаризатора)."""
        cur = await self._conn().execute(
            "SELECT * FROM messages WHERE agent_id = ? AND id > ? ORDER BY id",
            (agent_id, after_id),
        )
        rows = await cur.fetchall()
        return [self._row_to_message(row) for row in rows]

    async def count_messages(self, agent_id: str | None = None) -> int:
        """Число сообщений (всех или по агенту)."""
        if agent_id is None:
            cur = await self._conn().execute("SELECT COUNT(*) AS c FROM messages")
        else:
            cur = await self._conn().execute(
                "SELECT COUNT(*) AS c FROM messages WHERE agent_id = ?", (agent_id,)
            )
        row = await cur.fetchone()
        return int(row["c"])

    # -- факты и саммари ------------------------------------------------------ #
    async def add_fact(self, agent_id: str, kind: str, content: str) -> int:
        """Пишет факт или саммари; возвращает id."""
        async with self._lock:
            cur = await self._conn().execute(
                "INSERT INTO facts (agent_id, kind, content) VALUES (?, ?, ?)",
                (agent_id, kind, content),
            )
            await self._conn().commit()
            return int(cur.lastrowid or 0)

    async def facts(self, agent_id: str, kind: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Факты/саммари агента, свежие в конце."""
        if kind:
            cur = await self._conn().execute(
                "SELECT * FROM facts WHERE agent_id = ? AND kind = ? ORDER BY id DESC LIMIT ?",
                (agent_id, kind, limit),
            )
        else:
            cur = await self._conn().execute(
                "SELECT * FROM facts WHERE agent_id = ? ORDER BY id DESC LIMIT ?",
                (agent_id, limit),
            )
        rows = await cur.fetchall()
        return [dict(row) for row in reversed(rows)]

    async def count_facts(self, agent_id: str | None = None) -> int:
        """Число фактов/саммари."""
        if agent_id is None:
            cur = await self._conn().execute("SELECT COUNT(*) AS c FROM facts")
        else:
            cur = await self._conn().execute(
                "SELECT COUNT(*) AS c FROM facts WHERE agent_id = ?", (agent_id,)
            )
        row = await cur.fetchone()
        return int(row["c"])

    # -- мета-метки ----------------------------------------------------------- #
    async def get_meta(self, agent_id: str, key: str, default: str = "") -> str:
        """Служебная метка агента (например, граница саммаризации)."""
        cur = await self._conn().execute(
            "SELECT value FROM meta WHERE agent_id = ? AND key = ?", (agent_id, key)
        )
        row = await cur.fetchone()
        return str(row["value"]) if row else default

    async def set_meta(self, agent_id: str, key: str, value: str) -> None:
        """Ставит служебную метку агента."""
        async with self._lock:
            await self._conn().execute(
                "INSERT INTO meta (agent_id, key, value) VALUES (?, ?, ?) "
                "ON CONFLICT(agent_id, key) DO UPDATE SET value = excluded.value",
                (agent_id, key, value),
            )
            await self._conn().commit()

    # -- служебное ------------------------------------------------------------- #
    @staticmethod
    def _row_to_message(row: aiosqlite.Row) -> JournalMessage:
        return JournalMessage(
            id=int(row["id"]),
            agent_id=str(row["agent_id"]),
            session_id=str(row["session_id"]),
            source=str(row["source"]),
            role=str(row["role"]),
            content=str(row["content"]),
            profile=row["profile"] if row["profile"] is not None else None,
            ts=str(row["ts"]),
            persona_id=str(row["persona_id"]) if "persona_id" in row.keys() else "",
        )
