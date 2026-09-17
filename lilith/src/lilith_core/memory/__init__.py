"""Слой «память» (этап 3): журнал, RAG, авто-саммаризатор, рантайм-настройки.

:class:`MemoryCore` связывает всё вместе и живёт в ``app.state.memory``:
пишет реплики в журнал и в RAG, достаёт релевантные воспоминания перед ответом
и жуёт журнал в саммари каждые N сообщений (N настраивается из программы).
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from ..brain.llm import LLMClient
from ..brain.mock import MockBrain
from ..config import Settings
from .journal import Journal, JournalMessage
from .rag import ChromaRag, Embedder, HashEmbedder, RagHit, SentenceEmbedder
from .runtime import (
    RUNTIME_FIELDS,
    apply_runtime_update,
    load_runtime_settings,
    runtime_path,
    save_runtime_settings,
)
from .summarizer import SUMMARY_PROMPT, Summarizer

__all__ = [
    "MemoryCore",
    "Journal",
    "JournalMessage",
    "ChromaRag",
    "Embedder",
    "HashEmbedder",
    "SentenceEmbedder",
    "RagHit",
    "Summarizer",
    "SUMMARY_PROMPT",
    "RUNTIME_FIELDS",
    "apply_runtime_update",
    "load_runtime_settings",
    "save_runtime_settings",
    "runtime_path",
]


def build_embedder(settings: Settings) -> Embedder:
    """Эмбеддер по конфигу: sentence-transformers, если пакет стоит, иначе хеш.

    Проверка лёгкая (``find_spec``): модели не качаются до первого запроса,
    а отсутствие пакета не роняет ядро.
    """
    name = settings.memory.embedding_model
    if name and name != "hash":
        import importlib.util

        if importlib.util.find_spec("sentence_transformers") is not None:
            return SentenceEmbedder(name)
        logger.warning("sentence-transformers не установлен -> HashEmbedder (pip install -e .[memory])")
    return HashEmbedder()


class MemoryCore:
    """Оркестратор памяти: журнал + RAG + саммаризатор."""

    def __init__(
        self,
        settings: Settings,
        *,
        llm: LLMClient | None = None,
        mock: MockBrain | None = None,
    ) -> None:
        self.settings = settings
        self.journal = Journal(
            settings.memory.db_path,
            max_messages=settings.memory.journal_max_messages,
        )
        self.rag = ChromaRag(
            settings.memory.chroma_path,
            collection=settings.memory.chroma_collection,
            embedder=build_embedder(settings),
            enabled=settings.memory.rag_enabled,
        )
        self.summarizer = Summarizer(settings, self.journal, self.rag, llm=llm, mock=mock)

    # -- жизненный цикл ------------------------------------------------------ #
    async def start(self) -> None:
        """Открывает журнал и RAG."""
        await self.journal.start()
        self.rag.open()
        logger.info(
            "Память включена: журнал {}, RAG {}, саммари каждые {} сообщений",
            self.journal.db_path,
            "вкл" if self.rag.available else "выкл",
            self.settings.memory.summarize_every_n,
        )

    async def stop(self) -> None:
        """Закрывает журнал и RAG."""
        self.rag.close()
        await self.journal.stop()

    # -- обращение к памяти ---------------------------------------------------- #
    async def retrieve(self, agent_id: str, query: str, top_k: int | None = None) -> list[str]:
        """Тексты релевантных воспоминаний для подмешивания в контекст."""
        if not self.rag.available:
            return []
        hits: list[RagHit] = self.rag.find(
            agent_id, query, top_k or self.settings.memory.top_k
        )
        return [hit.text for hit in hits if hit.text.strip()]

    async def remember_turn(
        self,
        agent_id: str,
        user_text: str,
        reply_text: str,
        *,
        session_id: str = "",
        source: str = "webui",
        profile: str | None = None,
    ) -> str | None:
        """Пишет ход диалога в журнал и RAG, затем дёргает саммаризатор.

        Возвращает текст саммари, если в этом ходе он был создан.
        """
        user_id = await self.journal.add_message(
            agent_id, "user", user_text, session_id=session_id, source=source, profile=profile
        )
        await self.journal.add_message(
            agent_id, "assistant", reply_text, session_id=session_id, source=source, profile=profile
        )
        meta = {"session_id": session_id, "source": source, "profile": profile}
        self.rag.add(agent_id, user_text, {**meta, "role": "user", "msg_id": user_id})
        self.rag.add(agent_id, reply_text, {**meta, "role": "assistant"})
        return await self.summarizer.maybe_summarize(agent_id)

    # -- диагностика ----------------------------------------------------------- #
    async def describe(self) -> dict[str, Any]:
        """Сводка для healthz и панели."""
        return {
            "enabled": True,
            "journal": {
                "path": str(self.journal.db_path),
                "messages": await self.journal.count_messages(),
                "facts": await self.journal.count_facts(),
            },
            "rag": {
                "available": self.rag.available,
                "records": self.rag.count(),
                "embedder": type(self.rag.embedder).__name__,
            },
            "summarizer": self.summarizer.describe(),
            "runtime_fields": {
                key: getattr(self.settings.memory, key) for key in RUNTIME_FIELDS
            },
        }
