"""Тесты журнала памяти (aiosqlite)."""

from __future__ import annotations

import asyncio

import pytest

from lilith_core.memory import Journal


@pytest.fixture
async def journal(tmp_path):
    """Открытый журнал во временной папке."""
    db = Journal(tmp_path / "data" / "test.db", max_messages=100)
    await db.start()
    yield db
    await db.stop()


@pytest.mark.asyncio
class TestJournalMessages:
    """Сообщения: запись, порядок, лимиты, изоляция."""

    async def test_add_and_recent(self, journal: Journal) -> None:
        first = await journal.add_message("lilith", "user", "привет")
        await journal.add_message("lilith", "assistant", "мур")

        recent = await journal.recent_messages("lilith")
        assert [m.id for m in recent] == [first, first + 1]
        assert [m.role for m in recent] == ["user", "assistant"]
        assert recent[0].content == "привет"
        assert recent[0].source == "webui"
        assert recent[0].ts

    async def test_recent_limit_and_order(self, journal: Journal) -> None:
        for i in range(10):
            await journal.add_message("lilith", "user", f"msg {i}")
        recent = await journal.recent_messages("lilith", limit=3)
        assert [m.content for m in recent] == ["msg 7", "msg 8", "msg 9"]

    async def test_agent_isolation(self, journal: Journal) -> None:
        await journal.add_message("lilith", "user", "моё")
        await journal.add_message("другая", "user", "чужое")

        mine = await journal.recent_messages("lilith")
        assert [m.content for m in mine] == ["моё"]

    async def test_messages_after(self, journal: Journal) -> None:
        a = await journal.add_message("lilith", "user", "раз")
        await journal.add_message("lilith", "user", "два")
        await journal.add_message("lilith", "user", "три")

        after = await journal.messages_after("lilith", a)
        assert [m.content for m in after] == ["два", "три"]

    async def test_prune_keeps_max(self, tmp_path) -> None:
        db = Journal(tmp_path / "small.db", max_messages=5)
        await db.start()
        try:
            for i in range(12):
                await db.add_message("lilith", "user", f"m{i}")
            assert await db.count_messages("lilith") == 5
            recent = await journal_recent(db)
            assert recent[0] == "m7"
        finally:
            await db.stop()

    async def test_count_messages(self, journal: Journal) -> None:
        await journal.add_message("lilith", "user", "а")
        await journal.add_message("lilith", "assistant", "б")
        await journal.add_message("другая", "user", "в")

        assert await journal.count_messages("lilith") == 2
        assert await journal.count_messages() == 3

    async def test_concurrent_writes(self, journal: Journal) -> None:
        await asyncio.gather(*[journal.add_message("lilith", "user", f"c{i}") for i in range(20)])
        assert await journal.count_messages("lilith") == 20


async def journal_recent(db: Journal) -> list[str]:
    """Содержимое недавних сообщений."""
    return [m.content for m in await db.recent_messages("lilith", limit=100)]


@pytest.mark.asyncio
class TestJournalFacts:
    """Факты и саммари."""

    async def test_add_and_list(self, journal: Journal) -> None:
        await journal.add_fact("lilith", "fact", "Кирюша любит чизкейк")
        await journal.add_fact("lilith", "summary", "Выжимка дня")
        await journal.add_fact("другая", "fact", "чужой факт")

        all_facts = await journal.facts("lilith")
        assert [f["kind"] for f in all_facts] == ["fact", "summary"]
        summaries = await journal.facts("lilith", kind="summary")
        assert [f["content"] for f in summaries] == ["Выжимка дня"]
        assert await journal.count_facts("lilith") == 2
        assert await journal.count_facts() == 3

    async def test_facts_order_oldest_first(self, journal: Journal) -> None:
        await journal.add_fact("lilith", "fact", "первый")
        await journal.add_fact("lilith", "fact", "второй")
        facts = await journal.facts("lilith")
        assert [f["content"] for f in facts] == ["первый", "второй"]


@pytest.mark.asyncio
class TestJournalMeta:
    """Служебные метки (граница саммаризации и пр.)."""

    async def test_get_set(self, journal: Journal) -> None:
        assert await journal.get_meta("lilith", "summarized_upto", "0") == "0"
        await journal.set_meta("lilith", "summarized_upto", "42")
        assert await journal.get_meta("lilith", "summarized_upto") == "42"
        await journal.set_meta("lilith", "summarized_upto", "43")
        assert await journal.get_meta("lilith", "summarized_upto") == "43"

    async def test_meta_per_agent(self, journal: Journal) -> None:
        await journal.set_meta("lilith", "k", "1")
        assert await journal.get_meta("другая", "k", "нет") == "нет"


class TestJournalLifecycle:
    """Открытие/закрытие и защита от использования до старта."""

    @pytest.mark.asyncio
    async def test_closed_raises(self, tmp_path) -> None:
        db = Journal(tmp_path / "x.db")
        assert db.closed is True
        with pytest.raises(RuntimeError, match="start"):
            await db.add_message("lilith", "user", "а")

    @pytest.mark.asyncio
    async def test_reopen_keeps_data(self, tmp_path) -> None:
        db = Journal(tmp_path / "x.db")
        await db.start()
        await db.add_message("lilith", "user", "переживу перезапуск")
        await db.stop()

        db2 = Journal(tmp_path / "x.db")
        await db2.start()
        try:
            recent = await db2.recent_messages("lilith")
            assert [m.content for m in recent] == ["переживу перезапуск"]
        finally:
            await db2.stop()
