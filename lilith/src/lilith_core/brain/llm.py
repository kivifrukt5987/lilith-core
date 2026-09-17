"""OpenAI-совместимый клиент LLM («мозг», этап 2).

Говорит с любым сервером, понимающим OpenAI Chat Completions API:
LM Studio, llama.cpp server, Ollama, vLLM, OpenRouter, облачный OpenAI.
Реализован на ``httpx`` напрямую (без SDK ``openai``): меньше зависимостей,
полный контроль над стримингом SSE и таймаутами, одинаково работает в тестах
через ``httpx.MockTransport``.

Метрики, которые логируются и уходят в веб-панель:
время до первого токена, суммарная задержка, ток/сек, usage (или честная
пометка ``estimated=True``, если сервер не прислал usage в стриме).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

import httpx
from loguru import logger

from ..config import ResolvedProfile

__all__ = [
    "BrainError",
    "BrainConnectionError",
    "BrainHTTPError",
    "BrainTimeoutError",
    "Usage",
    "CompletionResult",
    "LLMClient",
    "estimate_tokens",
    "TokenCallback",
]

#: Колбэк стриминга: вызывается на каждом кусочке текста.
TokenCallback = Callable[[str], Awaitable[None]]


class BrainError(Exception):
    """Базовая ошибка слоя мозга."""


class BrainConnectionError(BrainError):
    """Сервер модели недоступен (не поднят, занят порт, неверный адрес)."""


class BrainTimeoutError(BrainError):
    """Сервер модели не ответил вовремя."""


class BrainHTTPError(BrainError):
    """Сервер модели вернул HTTP-ошибку."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


@dataclass(slots=True)
class Usage:
    """Токены запроса/ответа."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    #: True, если сервер не прислал usage и токены оценены грубо.
    estimated: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Словарь для WS-кадров и логов."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated": self.estimated,
        }


@dataclass(slots=True)
class CompletionResult:
    """Результат генерации с метриками."""

    text: str
    profile: str
    model: str
    usage: Usage
    latency_ms: float
    first_token_ms: float | None
    tok_per_sec: float | None
    finish_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Словарь для WS-кадров (data ответа)."""
        return {
            "profile": self.profile,
            "model": self.model,
            "usage": self.usage.as_dict(),
            "latency_ms": round(self.latency_ms, 2),
            "first_token_ms": round(self.first_token_ms, 2) if self.first_token_ms is not None else None,
            "tok_per_sec": round(self.tok_per_sec, 2) if self.tok_per_sec is not None else None,
            "finish_reason": self.finish_reason,
        }


def estimate_tokens(text: str) -> int:
    """Грубая оценка токенов: ~3 символа на токен (с запасом для русского)."""
    return max(1, len(text) // 3) if text else 0


class LLMClient:
    """Клиент OpenAI-совместимого chat-completions endpoint'а."""

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float | None = None,
    ) -> None:
        self._transport = transport
        self._timeout = timeout

    # -- служебное ----------------------------------------------------------- #
    def _client(self, profile: ResolvedProfile) -> httpx.AsyncClient:
        """Собирает httpx-клиент под профиль (таймаут и заголовки)."""
        timeout = self._timeout or profile.request_timeout_sec
        headers = {"Content-Type": "application/json"}
        key = profile.resolved_api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return httpx.AsyncClient(
            base_url=profile.base_url,
            headers=headers,
            timeout=timeout,
            transport=self._transport,
        )

    def _payload(
        self,
        profile: ResolvedProfile,
        messages: list[dict[str, Any]],
        *,
        stream: bool,
    ) -> dict[str, Any]:
        """Тело запроса chat/completions."""
        payload: dict[str, Any] = {
            "model": profile.model,
            "messages": messages,
            "temperature": profile.temperature,
            "max_tokens": profile.max_tokens,
            "top_p": profile.top_p,
            "stream": stream,
        }
        return payload

    @staticmethod
    def _wrap_connection_error(profile: ResolvedProfile, exc: Exception) -> BrainConnectionError:
        """Человеческое сообщение для «сервер не поднялся»."""
        return BrainConnectionError(
            f"профиль '{profile.name}': не могу достучаться до {profile.base_url} "
            f"(модель {profile.model!r}). Проверь, поднят ли сервер модели "
            f"(LM Studio / llama.cpp / Ollama), и не занят ли порт. ({exc.__class__.__name__})"
        )

    # -- здоровье ------------------------------------------------------------ #
    async def health(self, profile: ResolvedProfile, timeout: float = 3.0) -> dict[str, Any]:
        """Проверяет доступность сервера профиля: ``GET /models``.

        Возвращает словарь ``{ok, error, latency_ms, models}`` — никогда не бросает.
        """
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                base_url=profile.base_url,
                timeout=timeout,
                transport=self._transport,
            ) as client:
                resp = await client.get("/models")
            latency = (time.perf_counter() - started) * 1000
            if resp.status_code >= 400:
                return {
                    "ok": False,
                    "error": f"HTTP {resp.status_code}",
                    "latency_ms": round(latency, 2),
                    "models": [],
                }
            models: list[str] = []
            try:
                data = resp.json()
                models = [m.get("id", "") for m in data.get("data", []) if isinstance(m, dict)]
            except Exception:  # noqa: BLE001 - сервер мог ответить не-JSON
                models = []
            return {"ok": True, "error": None, "latency_ms": round(latency, 2), "models": models}
        except httpx.TimeoutException as exc:
            return {
                "ok": False,
                "error": f"таймаут {timeout} c: {exc.__class__.__name__}",
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "models": [],
            }
        except Exception as exc:  # noqa: BLE001 - любая сетевая смерть = «недоступен»
            return {
                "ok": False,
                "error": f"{exc.__class__.__name__}: сервер не отвечает",
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "models": [],
            }

    # -- генерация ----------------------------------------------------------- #
    async def complete(
        self,
        profile: ResolvedProfile,
        messages: list[dict[str, Any]],
        on_token: TokenCallback | None = None,
    ) -> CompletionResult:
        """Полный цикл генерации: стриминг (если включён и есть колбэк) или разовый ответ."""
        use_stream = profile.stream and on_token is not None
        started = time.perf_counter()
        first_token_ms: float | None = None
        chunks: list[str] = []
        usage = Usage()
        finish_reason: str | None = None

        try:
            if use_stream:
                async for piece in self._stream(profile, messages):
                    if piece.text:
                        if first_token_ms is None:
                            first_token_ms = (time.perf_counter() - started) * 1000
                        chunks.append(piece.text)
                        await on_token(piece.text)
                    if piece.usage is not None:
                        usage = piece.usage
                    if piece.finish_reason:
                        finish_reason = piece.finish_reason
            else:
                async with self._client(profile) as client:
                    resp = await client.post("/chat/completions", json=self._payload(profile, messages, stream=False))
                if resp.status_code >= 400:
                    raise BrainHTTPError(resp.status_code, resp.text[:300])
                data = resp.json()
                choice = (data.get("choices") or [{}])[0]
                text = (choice.get("message") or {}).get("content") or ""
                finish_reason = choice.get("finish_reason")
                raw_usage = data.get("usage") or {}
                usage = Usage(
                    prompt_tokens=int(raw_usage.get("prompt_tokens") or 0),
                    completion_tokens=int(raw_usage.get("completion_tokens") or 0),
                    total_tokens=int(raw_usage.get("total_tokens") or 0),
                )
                chunks.append(text)
                if text and on_token is not None:
                    await on_token(text)
                first_token_ms = (time.perf_counter() - started) * 1000
        except httpx.TimeoutException as exc:
            raise BrainTimeoutError(
                f"профиль '{profile.name}': сервер молчит дольше "
                f"{profile.request_timeout_sec} c ({exc.__class__.__name__})"
            ) from exc
        except httpx.ConnectError as exc:
            raise self._wrap_connection_error(profile, exc) from exc
        except httpx.HTTPError as exc:
            raise BrainConnectionError(
                f"профиль '{profile.name}': сетевая ошибка {exc.__class__.__name__}: {exc}"
            ) from exc

        text = "".join(chunks)
        latency_ms = (time.perf_counter() - started) * 1000

        if usage.completion_tokens == 0 and text:
            usage = Usage(
                prompt_tokens=estimate_tokens(json.dumps(messages, ensure_ascii=False)),
                completion_tokens=estimate_tokens(text),
                total_tokens=0,
                estimated=True,
            )
            usage.total_tokens = usage.prompt_tokens + usage.completion_tokens

        gen_sec = (latency_ms - (first_token_ms or 0.0)) / 1000.0 if use_stream else latency_ms / 1000.0
        tok_per_sec = usage.completion_tokens / gen_sec if gen_sec > 0 and usage.completion_tokens else None

        logger.info(
            "BRAIN[{}]: {} токенов ответа, {:.1f} мс, первый токен {}, {:.1f} ток/сек{}",
            profile.name,
            usage.completion_tokens,
            latency_ms,
            f"{first_token_ms:.0f} мс" if first_token_ms is not None else "—",
            tok_per_sec or 0.0,
            " (оценка)" if usage.estimated else "",
        )
        return CompletionResult(
            text=text,
            profile=profile.name,
            model=profile.model,
            usage=usage,
            latency_ms=latency_ms,
            first_token_ms=first_token_ms,
            tok_per_sec=tok_per_sec,
            finish_reason=finish_reason,
        )

    async def _stream(
        self,
        profile: ResolvedProfile,
        messages: list[dict[str, Any]],
    ) -> AsyncIterator["_StreamPiece"]:
        """Читает SSE-поток ``data: {...}`` до ``[DONE]``."""
        async with self._client(profile) as client:
            async with client.stream(
                "POST",
                "/chat/completions",
                json=self._payload(profile, messages, stream=True),
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", errors="replace")[:300]
                    raise BrainHTTPError(resp.status_code, body)
                async for line in resp.aiter_lines():
                    piece = _parse_sse_line(line)
                    if piece is None:
                        continue
                    if piece.done:
                        return
                    yield piece


@dataclass(slots=True)
class _StreamPiece:
    """Один кусочек SSE-потока."""

    text: str = ""
    usage: Usage | None = None
    finish_reason: str | None = None
    done: bool = False


def _parse_sse_line(line: str) -> _StreamPiece | None:
    """Разбирает одну строку SSE. ``None`` — строку можно игнорировать."""
    line = line.strip()
    if not line or line.startswith(":"):
        return None
    if not line.startswith("data:"):
        return None
    payload = line[len("data:") :].strip()
    if payload == "[DONE]":
        return _StreamPiece(done=True)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        logger.debug("SSE: не-JSON строка потока: {!r}", line[:120])
        return None

    piece = _StreamPiece()
    choices = data.get("choices") or []
    if choices:
        choice = choices[0]
        delta = choice.get("delta") or {}
        piece.text = delta.get("content") or ""
        piece.finish_reason = choice.get("finish_reason")
    raw_usage = data.get("usage")
    if raw_usage:
        piece.usage = Usage(
            prompt_tokens=int(raw_usage.get("prompt_tokens") or 0),
            completion_tokens=int(raw_usage.get("completion_tokens") or 0),
            total_tokens=int(raw_usage.get("total_tokens") or 0),
        )
    return piece
