"""Тесты авто-саммаризатора и рантайм-настроек памяти."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from lilith_core.config import load_settings
from lilith_core.memory import (
    RUNTIME_FIELDS,
    ChromaRag,
    HashEmbedder,
    Journal,
    Summarizer,
    apply_runtime_update,
    load_runtime_settings,
    runtime_path,
)
from lilith_core.brain import MockBrain


@pytest.fixture
async def memory_pair(tmp_path):
    """Пара журнал+RAG во временных каталогах."""
    journal = Journal(tmp_path / "db.sqlite")
    await journal.start()
    rag = ChromaRag(tmp_path / "chroma", collection="sumtest", embedder=HashEmbedder(dim=64))
    rag.open()
    yield journal, rag
    rag.close()
    await journal.stop()


def make_summarizer(settings, journal, rag, **mock_kwargs) -> Summarizer:
    """Саммаризатор с mock-мозгом."""
    return Summarizer(settings, journal, rag, mock=MockBrain(**mock_kwargs))


async def fill(journal: Journal, agent: str, n: int) -> None:
    """n пар сообщений в журнал."""
    for i in range(n):
        await journal.add_message(agent, "user", f"вопрос {i}")
        await journal.add_message(agent, "assistant", f"ответ {i}")


@pytest.mark.asyncio
class TestSummarizer:
    """Порог, выжимка, метки, отключение."""

    async def test_below_threshold_no_summary(self, memory_settings, memory_pair) -> None:
        journal, rag = memory_pair
        memory_settings.memory.summarize_every_n = 4
        summ = make_summarizer(memory_settings, journal, rag)

        await fill(journal, "lilith", 1)  # 2 сообщения при пороге 4
        assert await summ.pending_count("lilith") == 2
        assert await summ.maybe_summarize("lilith") is None
        assert await journal.count_facts("lilith") == 0

    async def test_at_threshold_makes_summary(self, memory_settings, memory_pair) -> None:
        journal, rag = memory_pair
        memory_settings.memory.summarize_every_n = 4
        summ = make_summarizer(memory_settings, journal, rag)

        await fill(journal, "lilith", 2)  # 4 сообщения
        summary = await summ.maybe_summarize("lilith")

        assert summary
        facts = await journal.facts("lilith", kind="summary")
        assert len(facts) == 1
        assert facts[0]["content"] == summary
        # граница сдвинулась: новых сообщений нет -> повторный вызов молчит
        assert await summ.maybe_summarize("lilith") is None
        assert summ.summaries_made == 1
        # саммари ушло и в RAG
        assert rag.count("lilith") >= 1

    async def test_threshold_is_read_live(self, memory_settings, memory_pair) -> None:
        """Порог читается в момент вызова: смена настройки действует сразу."""
        journal, rag = memory_pair
        summ = make_summarizer(memory_settings, journal, rag)

        await fill(journal, "lilith", 1)  # 2 сообщения
        memory_settings.memory.summarize_every_n = 10
        assert await summ.maybe_summarize("lilith") is None
        memory_settings.memory.summarize_every_n = 2
        assert await summ.maybe_summarize("lilith") is not None

    async def test_auto_summarize_off(self, memory_settings, memory_pair) -> None:
        journal, rag = memory_pair
        memory_settings.memory.auto_summarize = False
        memory_settings.memory.summarize_every_n = 2
        summ = make_summarizer(memory_settings, journal, rag)

        await fill(journal, "lilith", 3)
        assert await summ.maybe_summarize("lilith") is None
        assert await journal.count_facts("lilith") == 0

    async def test_chunks_do_not_overlap(self, memory_settings, memory_pair) -> None:
        journal, rag = memory_pair
        memory_settings.memory.summarize_every_n = 4
        summ = make_summarizer(memory_settings, journal, rag)

        await fill(journal, "lilith", 4)  # 8 сообщений = два цикла
        first = await summ.maybe_summarize("lilith")
        second = await summ.maybe_summarize("lilith")

        assert first and second
        facts = await journal.facts("lilith", kind="summary")
        assert len(facts) == 2
        assert summ.summaries_made == 2

    async def test_missing_summarizer_profile_falls_back(self, memory_settings, memory_pair) -> None:
        journal, rag = memory_pair
        memory_settings.brain.profiles.pop("summarizer", None)
        summ = make_summarizer(memory_settings, journal, rag)
        memory_settings.memory.summarize_every_n = 2
        await fill(journal, "lilith", 1)

        assert await summ.maybe_summarize("lilith") is not None
        assert summ.describe()["profile"] == memory_settings.brain.default_profile

    async def test_describe_shape(self, memory_settings, memory_pair) -> None:
        journal, rag = memory_pair
        summ = make_summarizer(memory_settings, journal, rag)
        data = summ.describe()
        assert set(data) >= {"auto_summarize", "summarize_every_n", "summaries_made", "profile"}


class TestRuntimeSettings:
    """Настройки памяти меняются из программы и переживают перезапуск."""

    def test_fields_list(self) -> None:
        assert set(RUNTIME_FIELDS) == {"summarize_every_n", "top_k", "rag_enabled", "auto_summarize"}

    def test_apply_and_persist(self, memory_settings, tmp_project) -> None:
        result = apply_runtime_update(memory_settings, {"summarize_every_n": 7, "auto_summarize": False})

        assert result["summarize_every_n"] == 7
        assert memory_settings.memory.summarize_every_n == 7
        assert memory_settings.memory.auto_summarize is False

        path = runtime_path(memory_settings)
        assert path.is_file()
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["summarize_every_n"] == 7
        assert saved["auto_summarize"] is False

    def test_unknown_field_rejected(self, memory_settings) -> None:
        with pytest.raises(ValueError, match="чужие"):
            apply_runtime_update(memory_settings, {"telepathy": True})

    def test_invalid_value_rejected(self, memory_settings) -> None:
        with pytest.raises(ValidationError):
            apply_runtime_update(memory_settings, {"summarize_every_n": 0})
        with pytest.raises(ValidationError):
            apply_runtime_update(memory_settings, {"top_k": -5})

    def test_load_applies_over_yaml(self, memory_settings, tmp_project) -> None:
        apply_runtime_update(memory_settings, {"summarize_every_n": 13, "top_k": 2})

        fresh = load_settings(tmp_project / "config" / "config.yaml", env_file=None)
        fresh.memory.runtime_settings_path = memory_settings.memory.runtime_settings_path
        applied = load_runtime_settings(fresh)

        assert applied["summarize_every_n"] == 13
        assert fresh.memory.summarize_every_n == 13
        assert fresh.memory.top_k == 2

    def test_load_ignores_broken_file(self, memory_settings, tmp_project) -> None:
        path = runtime_path(memory_settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{сломанный json", encoding="utf-8")

        assert load_runtime_settings(memory_settings) == {}

    def test_load_ignores_unknown_keys(self, memory_settings) -> None:
        path = runtime_path(memory_settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"telepathy": 1, "top_k": 3}), encoding="utf-8")

        applied = load_runtime_settings(memory_settings)
        assert applied == {"top_k": 3}
