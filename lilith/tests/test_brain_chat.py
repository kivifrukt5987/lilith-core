"""Тесты чат-цикла: системный промт, история, бюджет, метрики, mock-мозг."""

from __future__ import annotations

import pytest

from lilith_core.brain import BrainError, ChatCycle, MockBrain
from lilith_core.config import Settings, load_settings
from lilith_core.protocol import MsgType


@pytest.fixture
def brain_settings(sample_config) -> Settings:
    """Настройки с включённым мозгом в mock-режиме."""
    settings = load_settings(sample_config, env_file=None)
    settings.features.brain_enabled = True
    settings.brain.defaults.provider = "mock"
    return settings


@pytest.fixture
def cycle(brain_settings) -> ChatCycle:
    """Чат-цикл с mock-мозгом."""
    return ChatCycle(brain_settings, mock=MockBrain())


def ctx(**overrides) -> dict:
    """Типовой контекст реплики."""
    base = {"session_id": "s1", "source": "webui", "agent_id": "lilith"}
    base.update(overrides)
    return base


class TestBuildMessages:
    """Сборка контекста для модели."""

    def test_system_first_user_last(self, cycle: ChatCycle) -> None:
        messages = cycle.build_messages("привет", ctx())

        assert messages[0]["role"] == "system"
        assert "ТестЛилит" in messages[0]["content"]      # persona.md подставлен
        assert messages[-1] == {"role": "user", "content": "привет"}

    def test_history_in_between(self, cycle: ChatCycle) -> None:
        cycle.history.append(cycle.history_key(ctx()), "user", "раз")
        cycle.history.append(cycle.history_key(ctx()), "assistant", "два")

        messages = cycle.build_messages("три", ctx())

        assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
        assert messages[1]["content"] == "раз"

    def test_history_trimmed_by_count(self, brain_settings) -> None:
        brain_settings.brain.defaults.history_max_messages = 2
        chat = ChatCycle(brain_settings, mock=MockBrain())
        key = chat.history_key(ctx())
        for i in range(10):
            chat.history.append(key, "user", f"реплика {i}")

        messages = chat.build_messages("новая", ctx())

        history_part = messages[1:-1]
        assert len(history_part) == 2
        assert history_part[-1]["content"] == "реплика 9"  # свежее важнее

    def test_history_trimmed_by_token_budget(self, brain_settings) -> None:
        brain_settings.brain.defaults.context_window = 512
        brain_settings.brain.resolve().max_tokens
        chat = ChatCycle(brain_settings, mock=MockBrain())
        key = chat.history_key(ctx())
        for i in range(6):
            chat.history.append(key, "user", "слово " * 200)  # ~1300 «токенов» каждая

        messages = chat.build_messages("короткий вопрос", ctx())

        assert len(messages) == 2  # system + user: история не влезла в бюджет

    def test_history_keys_are_isolated(self, cycle: ChatCycle) -> None:
        cycle.history.append(cycle.history_key(ctx(agent_id="lilith")), "user", "моё")
        cycle.history.append(cycle.history_key(ctx(agent_id="другая")), "user", "чужое")

        messages = cycle.build_messages("x", ctx(agent_id="lilith"))
        joined = " ".join(m["content"] for m in messages)

        assert "моё" in joined
        assert "чужое" not in joined


@pytest.mark.asyncio
class TestReply:
    """Полный цикл реплики через mock-мозг."""

    async def test_reply_shape_and_metrics(self, cycle: ChatCycle) -> None:
        message = await cycle.reply("привет", ctx())

        assert message.type is MsgType.CHAT
        assert message.text.startswith("мок-chat:")
        data = message.data
        assert data["role"] == "assistant"
        assert data["agent_id"] == "lilith"
        assert data["profile"] == "chat"
        assert data["partial"] is False
        assert data["stream_id"]
        assert data["usage"]["estimated"] is True         # mock честно признаётся
        assert data["tok_per_sec"] is not None
        assert data["model"].startswith("mock-")

    async def test_push_receives_stream_chunks(self, cycle: ChatCycle) -> None:
        chunks: list[str] = []

        async def push(piece: str) -> None:
            chunks.append(piece)

        message = await cycle.reply("мур", ctx(), push=push)

        assert chunks
        assert "".join(chunks) == message.text

    async def test_no_push_no_problem(self, cycle: ChatCycle) -> None:
        message = await cycle.reply("без стрима", ctx())
        assert message.text

    async def test_history_grows_both_sides(self, cycle: ChatCycle) -> None:
        await cycle.reply("раз", ctx())
        await cycle.reply("два", ctx())

        key = cycle.history_key(ctx())
        roles = [role for role, _ in cycle.history._stores[key]]
        assert roles == ["user", "assistant", "user", "assistant"]

    async def test_profile_routing(self, cycle: ChatCycle) -> None:
        message = await cycle.reply("код", ctx(profile="coder"))
        assert message.text.startswith("мок-coder:")
        assert message.data["profile"] == "coder"

    async def test_fail_on_raises_brain_error(self, brain_settings) -> None:
        chat = ChatCycle(brain_settings, mock=MockBrain(fail_on="умри"))
        with pytest.raises(BrainError):
            await chat.reply("умри немедленно", ctx())

    async def test_system_prompt_reload(self, cycle: ChatCycle) -> None:
        assert cycle.reload_system_prompt().strip()
        assert cycle.system_prompt == cycle.reload_system_prompt()


class TestMockBrain:
    """Сама заглушка: детерминизм, счётчики, здоровье."""

    @pytest.mark.asyncio
    async def test_call_counter(self) -> None:
        from lilith_core.config import ResolvedProfile

        mock = MockBrain()

        profile = ResolvedProfile(
            name="chat", provider="mock", base_url="x", model="m", temperature=0.1,
            max_tokens=10, top_p=0.9, request_timeout_sec=1.0, max_retries=0,
            stream=False, history_max_messages=5, api_key_env="X", note="",
        )
        await mock.complete(profile, [{"role": "user", "content": "а"}])
        await mock.complete(profile, [{"role": "user", "content": "б"}])

        assert mock.call_count == 2
        assert mock.last_call()["text"] == "б"

    @pytest.mark.asyncio
    async def test_health_always_ok(self) -> None:
        from lilith_core.config import ResolvedProfile

        mock = MockBrain()
        profile = ResolvedProfile(
            name="chat", provider="mock", base_url="x", model="m", temperature=0.1,
            max_tokens=10, top_p=0.9, request_timeout_sec=1.0, max_retries=0,
            stream=False, history_max_messages=5, api_key_env="X", note="",
        )
        health = await mock.health(profile)
        assert health["ok"] is True
        assert health["models"] == ["mock-chat"]


@pytest.mark.asyncio
class TestPersonaHotReload:
    """Горячая перечитка persona.md без перезапуска сервера."""

    async def test_detects_file_change_on_reply(self, brain_settings, persona_file) -> None:
        import os
        import time

        brain_settings.app.persona_path = str(persona_file)
        chat = ChatCycle(brain_settings, mock=MockBrain())

        await chat.reply("раз", ctx())
        assert "НОВАЯ_ЛИЛИТ" not in chat.system_prompt

        persona_file.write_text("Ты — НОВАЯ_ЛИЛИТ v2. Зовут $user_name.", encoding="utf-8")
        os.utime(persona_file, (time.time() + 5, time.time() + 5))  # страховка от совпадения mtime

        await chat.reply("два", ctx())
        assert "НОВАЯ_ЛИЛИТ" in chat.system_prompt
        assert "ТестЛилит" not in chat.system_prompt

    async def test_no_reload_when_untouched(self, brain_settings) -> None:
        chat = ChatCycle(brain_settings, mock=MockBrain())
        assert chat.maybe_reload_persona() is False

    async def test_missing_file_keeps_old_prompt(self, brain_settings, tmp_path) -> None:
        brain_settings.app.persona_path = str(tmp_path / "ghost.md")
        chat = ChatCycle(brain_settings, mock=MockBrain())
        before = chat.system_prompt
        assert chat.maybe_reload_persona() is False
        assert chat.system_prompt == before
