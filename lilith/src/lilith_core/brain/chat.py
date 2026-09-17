"""Чат-цикл этапа 2: системный промт + история + поход в мозг + метрики.

История пока живёт в памяти процесса (кольцевой буфер на ключ сессии);
на этапе 3 она переедет в aiosqlite-журнал, не меняя внешний контракт.
"""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from loguru import logger

from ..config import Settings
from ..persona import build_system_prompt
from ..protocol import Message, MsgType, build_message
from .llm import LLMClient, estimate_tokens
from .mock import MockBrain

__all__ = ["ChatCycle", "InMemoryHistory", "PushCallback", "build_client_for"]

#: Колбэк для стриминга частичных кадров в шину.
PushCallback = Callable[[str], Awaitable[None]]


@dataclass
class InMemoryHistory:
    """Кольцевая история реплик на ключ (агент+сессия+источник).

    На этапе 3 заменяется журналом в aiosqlite с тем же интерфейсом
    (``append`` / ``recent``), поэтому чат-цикл переписывать не придётся.
    """

    max_messages: int = 40
    _stores: dict[str, deque[tuple[str, str]]] = field(default_factory=dict)

    def _store(self, key: str) -> deque[tuple[str, str]]:
        if key not in self._stores:
            self._stores[key] = deque(maxlen=self.max_messages)
        return self._stores[key]

    def append(self, key: str, role: str, text: str) -> None:
        """Добавляет реплику в историю ключа."""
        self._store(key).append((role, text))

    def recent(self, key: str) -> list[dict[str, str]]:
        """История в формате сообщений OpenAI (``role``/``content``)."""
        return [{"role": role, "content": text} for role, text in self._store(key)]

    def clear(self, key: str) -> None:
        """Очищает историю ключа."""
        self._store(key).clear()


def build_client_for(profile_provider: str, *, llm: LLMClient | None = None, mock: MockBrain | None = None):
    """Выбирает клиент по ``provider`` профиля: ``mock`` → MockBrain, иначе LLMClient."""
    if profile_provider == "mock":
        return mock if mock is not None else MockBrain()
    return llm if llm is not None else LLMClient()


class ChatCycle:
    """Сборка контекста и поход в мозг с метриками и стримингом."""

    def __init__(self, settings: Settings, *, llm: LLMClient | None = None, mock: MockBrain | None = None) -> None:
        self.settings = settings
        self.llm = llm or LLMClient()
        self.mock = mock or MockBrain()
        self.history = InMemoryHistory(max_messages=settings.brain.defaults.history_max_messages)
        self._system_prompt = build_system_prompt(settings=settings)
        self._persona_path = settings.persona_file
        self._persona_mtime = self._safe_mtime()

    # -- контекст ------------------------------------------------------------- #
    @property
    def system_prompt(self) -> str:
        """Текущий системный промт (persona.md + дополнения)."""
        return self._system_prompt

    def reload_system_prompt(self) -> str:
        """Перечитать persona.md (после правки файла без перезапуска)."""
        self._system_prompt = build_system_prompt(settings=self.settings)
        self._persona_mtime = self._safe_mtime()
        logger.info("Персона перечитана: {} символов", len(self._system_prompt))
        return self._system_prompt

    def _safe_mtime(self) -> float:
        """Время изменения persona.md; -1.0, если файла нет."""
        try:
            return self._persona_path.stat().st_mtime
        except OSError:
            return -1.0

    def maybe_reload_persona(self) -> bool:
        """Горячая перечитка: заметить правку persona.md без перезапуска сервера.

        Дёшево (один ``stat``) и безопасно: вызывается перед каждой репликой.
        Возвращает True, если промт был перечитан.
        """
        mtime = self._safe_mtime()
        if mtime != self._persona_mtime:
            logger.info("persona.md изменился на диске — перечитываю горячо")
            self.reload_system_prompt()
            return True
        return False

    @staticmethod
    def history_key(context: dict[str, Any]) -> str:
        """Ключ истории: агент + сессия + источник."""
        return "|".join(
            str(context.get(k) or "-") for k in ("agent_id", "session_id", "source")
        )

    def build_messages(self, user_text: str, context: dict[str, Any]) -> list[dict[str, Any]]:
        """Собирает список сообщений: system + урезанная история + user.

        Урезание двухступенчатое: по числу реплик (``history_max_messages``)
        и по грубому токен-бюджету (``context_window`` минус резерв под ответ
        и системный промт). Системный промт и реплика пользователя не режутся.
        """
        profile = self.settings.brain.resolve(context.get("profile"))
        system_tokens = estimate_tokens(self._system_prompt)
        user_tokens = estimate_tokens(user_text)
        budget = max(
            256,
            self.settings.brain.defaults.context_window
            - profile.max_tokens
            - system_tokens
            - user_tokens,
        )

        history = self.history.recent(self.history_key(context))[-profile.history_max_messages :]

        messages: list[dict[str, Any]] = [{"role": "system", "content": self._system_prompt}]
        hits = context.get("memory_hits") or []
        if hits:
            lines = "\n".join(f"- {hit}" for hit in hits)
            messages.append(
                {
                    "role": "system",
                    "content": f"ВОСПОМНИНАНИЯ (релевантные записи памяти, используй к месту):\n{lines}",
                }
            )
        used = 0
        kept: list[dict[str, str]] = []
        for message in reversed(history):  # режем с хвоста, свежее важнее
            cost = estimate_tokens(message["content"])
            if used + cost > budget:
                break
            kept.append(message)
            used += cost
        messages.extend(reversed(kept))
        messages.append({"role": "user", "content": user_text})

        if len(kept) < len(history):
            logger.debug(
                "История урезана: {} из {} реплик (бюджет ~{} токенов)",
                len(kept),
                len(history),
                budget,
            )
        return messages

    # -- генерация ------------------------------------------------------------- #
    async def reply(self, text: str, context: dict[str, Any], push: PushCallback | None = None) -> Message:
        """Полный цикл: история → мозг → ответное сообщение шины.

        :param text: реплика пользователя.
        :param context: ``session_id``, ``source``, ``agent_id``, ``profile``.
        :param push: колбэк стриминга частичных кадров (если сервер и клиент умеют).
        :raises lilith_core.brain.llm.BrainError: при любой проблеме мозга
            (шина превратит это в ``error``-кадр, соединение выживет).
        """
        self.maybe_reload_persona()
        profile = self.settings.brain.resolve(context.get("profile"))
        client = build_client_for(profile.provider, llm=self.llm, mock=self.mock)

        key = self.history_key(context)
        self.history.append(key, "user", text)
        messages = self.build_messages(text, context)

        stream_id = str(context.get("stream_id") or uuid.uuid4().hex[:10])

        async def _push(chunk: str) -> None:
            if push is not None:
                await push(chunk)

        result = await client.complete(profile, messages, on_token=_push if push else None)

        self.history.append(key, "assistant", result.text)

        data: dict[str, Any] = {
            "role": "assistant",
            "agent_id": context.get("agent_id") or self.settings.app.agent_id,
            "stream_id": stream_id,
            "partial": False,
            "source": context.get("source", "webui"),
            "session_id": context.get("session_id"),
        }
        data.update(result.as_dict())
        return build_message(MsgType.CHAT, text=result.text, data=data)
