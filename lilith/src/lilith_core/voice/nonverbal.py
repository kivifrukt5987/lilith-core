"""Нонвербалика (ADR-020.3 → ADR-027 Q7-б): смех, вздох, хмыканье — wav-пакеты.

Решение Архитектора (Q7): в бой идут ПРЕДРЕНДЕРЕННЫЕ wav-пакеты (надёжно,
ноль VRAM), теги вида ``[laughs]`` в тексте — эксперимент Ж1 (модель Qwen3-TTS
заявлена робастной к шумному тексту, но 0.6B не имеет instruct-контроля).

Механика:
* персона в ``personas/<id>/voice.yaml`` объявляет слот ``nonverbal``
  (схема ADR-020.3): ``{laugh: {tag: "[laughs]", weight: 1.0}, ...}``;
* ассеты живут в ``personas/<id>/voice/nonverbal/<ключ>.wav`` — их кладёт
  Курьер (запись/рендер); нет ассета → тег молча вырезается + жёлтый лог
  (разговор не рвётся);
* :func:`split_nonverbal` режет реплику на сегменты text/event — теги в
  синтезируемый текст НЕ попадают (иначе TTS попытается их произнести);
* :meth:`VoiceCore.stream_mixed` interleaves: текстовые сегменты → горло
  (стрим), события → байты wav-пакета в тот же поток. Плеер/продюсер лица
  разницы не замечают: на входе те же wav-куски.

Проводка протокола (кто и когда шлёт теги: LLM-промпт → чат → stream_mixed) —
0.7.1, после результатов эксперимента Ж1-а. Здесь — чистый механизм + тесты.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from loguru import logger

__all__ = ["nonverbal_tags", "split_nonverbal", "nonverbal_pack_path", "Segment"]

#: Сегмент реплики: ("text", кусок) | ("event", ключ нонвербалики).
Segment = tuple[str, str]


def nonverbal_tags(nonverbal: dict[str, Any] | None) -> dict[str, str]:
    """Ключ → тег из voice.yaml-слота персоны (пусто, если слот не объявлен).

    Схема ADR-020.3: ``nonverbal: {laugh: {tag: "[laughs]", weight: 1.0}}``.
    weight в 0.7.0 не используется (детерминированное воспроизведение) —
    поле останется для будущей вероятностной логики промпта.
    """
    if not nonverbal:
        return {}
    tags: dict[str, str] = {}
    for key, spec in nonverbal.items():
        tag = ""
        if isinstance(spec, dict):
            tag = str(spec.get("tag") or "")
        elif isinstance(spec, str):  # упрощённая запись: laugh: "[laughs]"
            tag = spec
        if tag:
            tags[str(key)] = tag
    return tags


def split_nonverbal(text: str, tags: dict[str, str]) -> list[Segment]:
    """Режет реплику на текстовые сегменты и события нонвербалики.

    Теги ищутся дословно (регистр важен: это контракт промпта, не естественный
    текст). Пустые текстовые сегменты выбрасываются. Без тегов — один сегмент.
    """
    if not tags:
        return [("text", text)] if text else []
    escaped = {key: re.escape(tag) for key, tag in tags.items()}
    pattern = re.compile("|".join(f"(?P<{key}>{pat})" for key, pat in escaped.items()))
    segments: list[Segment] = []
    cursor = 0
    for match in pattern.finditer(text):
        before = text[cursor : match.start()]
        if before.strip():
            segments.append(("text", before.strip()))
        key = match.lastgroup or ""
        segments.append(("event", key))
        cursor = match.end()
    tail = text[cursor:]
    if tail.strip():
        segments.append(("text", tail.strip()))
    return segments


def nonverbal_pack_path(voice_dir: str | Path | None, key: str) -> Path | None:
    """Путь wav-пакета события: ``<voice_dir>/nonverbal/<ключ>.wav``.

    Возвращает путь, только если файл существует; иначе None + жёлтый лог
    (один раз на вызов — спама в бою не будет, реплика продолжается).
    """
    if not voice_dir:
        logger.warning("нонвербалика '{}': voice_dir персоны не передан — ассеты не найти", key)
        return None
    path = Path(voice_dir) / "nonverbal" / f"{key}.wav"
    if not path.is_file():
        logger.warning("нонвербалика '{}': пакет не найден ({}) — тег вырезан, говорим дальше", key, path)
        return None
    return path
