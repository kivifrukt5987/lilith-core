"""Тесты OpenAI-совместимого клиента мозга (на httpx.MockTransport, без сети)."""

from __future__ import annotations

import json

import httpx
import pytest

from lilith_core.brain.llm import (
    BrainConnectionError,
    BrainHTTPError,
    BrainTimeoutError,
    LLMClient,
    Usage,
    estimate_tokens,
)
from lilith_core.config import ResolvedProfile


def make_profile(**overrides) -> ResolvedProfile:
    """Типовой профиль для тестов."""
    base = dict(
        name="chat",
        provider="openai_compatible",
        base_url="http://mocked/v1",
        model="test-model",
        temperature=0.5,
        max_tokens=100,
        top_p=0.9,
        request_timeout_sec=5.0,
        max_retries=0,
        stream=True,
        history_max_messages=10,
        api_key_env="LILITH_NOPE_KEY",
        note="тест",
    )
    base.update(overrides)
    return ResolvedProfile(**base)


MESSAGES = [{"role": "system", "content": "ты — Лилит"}, {"role": "user", "content": "привет"}]


def json_handler(body: dict) -> httpx.Response:
    """Обработчик MockTransport: отдаёт готовый JSON chat/completions."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, json=body)

    return handler


def sse_handler(lines: list[str], status: int = 200) -> httpx.Handler:
    """Обработчик MockTransport: отдаёт SSE-поток из строк ``data: ...``."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = "".join(f"data: {line}\n\n" for line in lines) + "data: [DONE]\n\n"
        return httpx.Response(status, text=payload, headers={"Content-Type": "text/event-stream"})

    return handler


@pytest.mark.asyncio
class TestComplete:
    """Разовый (не stream) запрос."""

    async def test_plain_completion(self) -> None:
        body = {
            "choices": [{"message": {"content": "привет, Кирюша"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
        }
        client = LLMClient(transport=httpx.MockTransport(json_handler(body)))
        result = await client.complete(make_profile(stream=False), MESSAGES)

        assert result.text == "привет, Кирюша"
        assert result.finish_reason == "stop"
        assert result.usage.completion_tokens == 3
        assert result.usage.estimated is False
        assert result.tok_per_sec and result.tok_per_sec > 0
        assert result.profile == "chat"
        assert result.model == "test-model"

    async def test_http_error_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="внутренняя ошибка сервера модели")

        client = LLMClient(transport=httpx.MockTransport(handler))
        with pytest.raises(BrainHTTPError) as exc:
            await client.complete(make_profile(stream=False), MESSAGES)
        assert exc.value.status_code == 500
        assert "внутренняя ошибка" in exc.value.detail

    async def test_empty_choices_tolerated(self) -> None:
        client = LLMClient(transport=httpx.MockTransport(json_handler({"choices": []})))
        result = await client.complete(make_profile(stream=False), MESSAGES)
        assert result.text == ""


@pytest.mark.asyncio
class TestStream:
    """SSE-стриминг с колбэком токенов."""

    async def test_stream_chunks_and_usage(self) -> None:
        lines = [
            json.dumps({"choices": [{"delta": {"content": "привет, "}}]}),
            json.dumps({"choices": [{"delta": {"content": "Кирюша"}}]}),
            json.dumps(
                {
                    "choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13},
                }
            ),
        ]
        client = LLMClient(transport=httpx.MockTransport(sse_handler(lines)))

        chunks: list[str] = []

        async def on_token(piece: str) -> None:
            chunks.append(piece)

        result = await client.complete(make_profile(), MESSAGES, on_token=on_token)

        assert result.text == "привет, Кирюша"
        assert chunks == ["привет, ", "Кирюша"]
        assert result.first_token_ms is not None and result.first_token_ms >= 0
        assert result.usage.estimated is False
        assert result.finish_reason == "stop"

    async def test_stream_without_usage_is_estimated(self) -> None:
        lines = [json.dumps({"choices": [{"delta": {"content": "мур"}}]})]
        client = LLMClient(transport=httpx.MockTransport(sse_handler(lines)))
        result = await client.complete(make_profile(), MESSAGES, on_token=_noop)

        assert result.text == "мур"
        assert result.usage.estimated is True
        assert result.usage.completion_tokens == estimate_tokens("мур")

    async def test_stream_http_error(self) -> None:
        client = LLMClient(transport=httpx.MockTransport(sse_handler([], status=503)))
        with pytest.raises(BrainHTTPError) as exc:
            await client.complete(make_profile(), MESSAGES, on_token=_noop)
        assert exc.value.status_code == 503

    async def test_garbage_sse_lines_skipped(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            payload = ": comment\n\ndata: not-json\nndata: x\n" + 'data: {"choices":[{"delta":{"content":"ок"}}]}\n\n' + "data: [DONE]\n\n"
            return httpx.Response(200, text=payload)

        client = LLMClient(transport=httpx.MockTransport(handler))
        result = await client.complete(make_profile(), MESSAGES, on_token=_noop)
        assert result.text == "ок"


async def _noop(_piece: str = "") -> None:
    """Пустой колбэк стриминга."""


@pytest.mark.asyncio
class TestErrors:
    """Сетевые смерти превращаются в человекопонятные ошибки."""

    async def test_connection_refused(self) -> None:
        client = LLMClient(timeout=1.0)
        profile = make_profile(base_url="http://127.0.0.1:1/v1")
        with pytest.raises(BrainConnectionError) as exc:
            await client.complete(profile, MESSAGES)
        assert "профиль 'chat'" in str(exc.value)
        assert "127.0.0.1:1" in str(exc.value)

    async def test_timeout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("слишком долго")

        client = LLMClient(transport=httpx.MockTransport(handler))
        with pytest.raises(BrainTimeoutError):
            await client.complete(make_profile(), MESSAGES)

    async def test_generic_http_error_wrapped(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("dns умер")

        client = LLMClient(transport=httpx.MockTransport(handler))
        with pytest.raises(BrainConnectionError):
            await client.complete(make_profile(), MESSAGES)


@pytest.mark.asyncio
class TestHealth:
    """Healthcheck профиля для /api/brain/profiles."""

    async def test_health_ok_lists_models(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/models"
            return httpx.Response(200, json={"data": [{"id": "test-model"}, {"id": "other"}]})

        client = LLMClient(transport=httpx.MockTransport(handler))
        health = await client.health(make_profile())

        assert health["ok"] is True
        assert health["models"] == ["test-model", "other"]
        assert health["latency_ms"] >= 0

    async def test_health_http_error(self) -> None:
        client = LLMClient(transport=httpx.MockTransport(lambda _r: httpx.Response(401, json={})))
        health = await client.health(make_profile())
        assert health["ok"] is False
        assert "401" in health["error"]

    async def test_health_down_server(self) -> None:
        client = LLMClient(timeout=1.0)
        health = await client.health(make_profile(base_url="http://127.0.0.1:1/v1"), timeout=1.0)
        assert health["ok"] is False
        assert health["error"]


class TestMetricsHelpers:
    """Мелочь: оценка токенов и словари метрик."""

    def test_estimate_tokens(self) -> None:
        assert estimate_tokens("") == 0
        assert estimate_tokens("абв") == 1
        assert estimate_tokens("а" * 300) == 100

    def test_usage_dict(self) -> None:
        data = Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3).as_dict()
        assert data == {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3, "estimated": False}

    def test_completion_result_dict_rounds(self) -> None:
        from lilith_core.brain.llm import CompletionResult

        result = CompletionResult(
            text="x",
            profile="chat",
            model="m",
            usage=Usage(1, 2, 3),
            latency_ms=123.456,
            first_token_ms=12.345,
            tok_per_sec=45.678,
        )
        data = result.as_dict()
        assert data["latency_ms"] == 123.46
        assert data["first_token_ms"] == 12.35
        assert data["tok_per_sec"] == 45.68
