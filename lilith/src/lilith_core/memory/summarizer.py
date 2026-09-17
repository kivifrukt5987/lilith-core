"""Авто-саммаризатор журнала: каждые N сообщений (N настраивается в рантайме).

Дёргает мозг через профиль ``summarizer`` (если его нет — через профиль по
умолчанию), складывает выжимку в журнал (``facts.kind='summary'``) и в RAG,
двигает метку ``summarized_upto``. Порог и включённость берутся из
``settings.memory`` в момент вызова — то есть меняются на лету без перезапуска
(см. ``POST /api/memory/settings`` и шестерёнку в панели).
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from ..brain.llm import LLMClient
from ..brain.mock import MockBrain
from ..config import Settings
from .journal import Journal
from .rag import ChromaRag

__all__ = ["Summarizer", "SUMMARY_PROMPT"]

SUMMARY_PROMPT = (
    "Ты — саммаризатор памяти ИИ-компаньона. Сожми приведённый диалог в 3–6 коротких "
    "пунктов на русском. Сохраняй: имена, факты о пользователе, обещания, договорённости, "
    "эмоционально важные моменты, даты и числа. Не выдумывай. Без вступлений и комментариев."
)


class Summarizer:
    """Фоновая жувация журнала: диалог -> выжимка -> память."""

    def __init__(
        self,
        settings: Settings,
        journal: Journal,
        rag: ChromaRag,
        *,
        llm: LLMClient | None = None,
        mock: MockBrain | None = None,
    ) -> None:
        self.settings = settings
        self.journal = journal
        self.rag = rag
        self.llm = llm or LLMClient()
        self.mock = mock or MockBrain()
        self.summaries_made = 0

    def _profile(self):
        """Профиль саммаризации; если его нет в реестре — профиль по умолчанию."""
        brain = self.settings.brain
        name = "summarizer" if "summarizer" in brain.profiles else brain.default_profile
        return brain.resolve(name)

    async def pending_count(self, agent_id: str) -> int:
        """Сколько сообщений накопилось с последней саммаризации."""
        upto = int(await self.journal.get_meta(agent_id, "summarized_upto", "0") or 0)
        messages = await self.journal.messages_after(agent_id, upto)
        return len(messages)

    async def maybe_summarize(self, agent_id: str) -> str | None:
        """Саммаризирует, если накопилось >= ``summarize_every_n`` и включено.

        Возвращает текст саммари или ``None``, если жувать ещё рано.
        """
        memory_cfg = self.settings.memory
        if not memory_cfg.auto_summarize:
            return None

        threshold = memory_cfg.summarize_every_n
        upto = int(await self.journal.get_meta(agent_id, "summarized_upto", "0") or 0)
        messages = await self.journal.messages_after(agent_id, upto)
        if len(messages) < threshold:
            return None

        chunk = messages[:threshold]
        transcript = "\n".join(f"{m.role}: {m.content}" for m in chunk)
        prompt_messages = [
            {"role": "system", "content": SUMMARY_PROMPT},
            {"role": "user", "content": f"ДИАЛОГ:\n{transcript}"},
        ]

        profile = self._profile()
        client = self.mock if profile.provider == "mock" else self.llm
        result = await client.complete(profile, prompt_messages)
        summary = result.text.strip()
        if not summary:
            logger.warning("Саммаризатор вернул пустоту — пропускаю цикл")
            return None

        await self.journal.add_fact(agent_id, "summary", summary)
        await self.journal.set_meta(agent_id, "summarized_upto", str(chunk[-1].id))
        self.rag.add(agent_id, summary, {"kind": "summary", "profile": profile.name})
        self.summaries_made += 1
        logger.info(
            "Саммари готово (агент {}, сообщений {}..{}, профиль {}): {} символов",
            agent_id,
            chunk[0].id,
            chunk[-1].id,
            profile.name,
            len(summary),
        )
        return summary

    def describe(self) -> dict[str, Any]:
        """Сводка для healthz/панели."""
        return {
            "auto_summarize": self.settings.memory.auto_summarize,
            "summarize_every_n": self.settings.memory.summarize_every_n,
            "summaries_made": self.summaries_made,
            "profile": self._profile().name,
        }
