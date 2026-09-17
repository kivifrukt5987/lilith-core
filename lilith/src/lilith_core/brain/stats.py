"""Накопительная статистика токенов (счётчик для панели и /api/brain/stats)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

__all__ = ["TokenStats"]


@dataclass
class _ProfileStats:
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_tokens: int = 0
    tok_per_sec_sum: float = 0.0
    tok_per_sec_count: int = 0
    errors: int = 0


@dataclass
class TokenStats:
    """Потокобезопасный счётчик токенов и запросов по профилям мозга."""

    started_at: float = field(default_factory=time.time)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _profiles: dict[str, _ProfileStats] = field(default_factory=dict)

    def _get(self, profile: str) -> _ProfileStats:
        if profile not in self._profiles:
            self._profiles[profile] = _ProfileStats()
        return self._profiles[profile]

    def record(
        self,
        profile: str,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        estimated: bool = False,
        tok_per_sec: float | None = None,
    ) -> None:
        """Учитывает одну успешную генерацию."""
        with self._lock:
            stats = self._get(profile)
            stats.requests += 1
            stats.prompt_tokens += prompt_tokens
            stats.completion_tokens += completion_tokens
            if estimated:
                stats.estimated_tokens += prompt_tokens + completion_tokens
            if tok_per_sec:
                stats.tok_per_sec_sum += tok_per_sec
                stats.tok_per_sec_count += 1

    def record_error(self, profile: str) -> None:
        """Учитывает отказ мозга по профилю."""
        with self._lock:
            self._get(profile).errors += 1

    def snapshot(self) -> dict[str, Any]:
        """Сводка для ``/api/brain/stats`` и веб-панели."""
        with self._lock:
            profiles: dict[str, Any] = {}
            total_requests = 0
            total_prompt = 0
            total_completion = 0
            total_estimated = 0
            tps_sum = 0.0
            tps_count = 0
            total_errors = 0
            for name, stats in sorted(self._profiles.items()):
                avg_tps = (
                    round(stats.tok_per_sec_sum / stats.tok_per_sec_count, 2)
                    if stats.tok_per_sec_count
                    else None
                )
                profiles[name] = {
                    "requests": stats.requests,
                    "prompt_tokens": stats.prompt_tokens,
                    "completion_tokens": stats.completion_tokens,
                    "total_tokens": stats.prompt_tokens + stats.completion_tokens,
                    "estimated_tokens": stats.estimated_tokens,
                    "avg_tok_per_sec": avg_tps,
                    "errors": stats.errors,
                }
                total_requests += stats.requests
                total_prompt += stats.prompt_tokens
                total_completion += stats.completion_tokens
                total_estimated += stats.estimated_tokens
                tps_sum += stats.tok_per_sec_sum
                tps_count += stats.tok_per_sec_count
                total_errors += stats.errors
            return {
                "uptime_sec": round(time.time() - self.started_at, 1),
                "total_requests": total_requests,
                "total_prompt_tokens": total_prompt,
                "total_completion_tokens": total_completion,
                "total_tokens": total_prompt + total_completion,
                "estimated_tokens": total_estimated,
                "avg_tok_per_sec": round(tps_sum / tps_count, 2) if tps_count else None,
                "total_errors": total_errors,
                "profiles": profiles,
            }
