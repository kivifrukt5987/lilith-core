"""Препроцессинг текста перед горлом (0.7.2-а, ADR-030).

Два преобразования, оба — **до токенизации TTS** (Г-2/Г-3 Архитектора, 17.11):

1. **Ударения.** qwen3 ошибается в русских ударениях («КирюхА»), а acute U+0301
   она понимает. Режим переключаем (`acute` до эксперимента Ж7-б; `caps`/`plus`/`off`
   тоже поддержаны) — поэтому словарь хранит **номер ударной гласной**, а не готовую
   строку: смена маркера не требует переписывать словарь.
2. **Протяжки.** Дефис или многоточие между **одинаковыми** буквами — это попытка
   написать «ба-а-ам»; TTS читает такое по буквам. Схлопываем итеративно
   (`ба-а-ам → бааам`). Легитимные «кто-то», «ой-ой», даты, URL и `[...]`-теги
   не трогаются **по построению**: разные буквы либо защищённые маски.

Оба преобразования идемпотентны: повторный проход текст не меняет.

Словари (приоритет снизу вверх):

* ``config/stress_ru.yaml`` — общий (наполняют Архитектор и Курьер);
* ``personas/<id>/voice/stress.yaml`` — личный, перекрывает общий (0.7.2-а;
  имя файла согласовано с Архитектором: ``stress.yaml`` замещает ``accents.yaml``).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from ..config import resolve_path

#: Комбинируемое острое ударение (U+0301) — ставится ПОСЛЕ ударной гласной.
STRESS_MARK = "\u0301"

#: Режимы маркера: решение Ж7-б выбирает один, код не меняется.
STRESS_MODES = ("acute", "caps", "plus", "off")

_VOWELS = "аеёиоуыэюя"
_LETTER = "А-Яа-яЁё"

#: Слово для словаря ударений: только кириллица, без цифр и дефисов.
_WORD_RE = re.compile(rf"[{_LETTER}]+")

#: «Одинаковая буква, разделитель, та же буква». Дефис ИЛИ многоточие.
#: Итеративно: «ба-а-ам» → «баа-ам» → «бааам».
_DRAWL_RE = re.compile(rf"([{_LETTER}])(?:-|\.{{2,}}|…)(\1)")

#: Защищённые фрагменты: их содержимое препроцессор не видит.
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_TAG_RE = re.compile(r"\[[^\]]*\]")
_DATE_RE = re.compile(r"\d{1,4}[-./]\d{1,2}[-./]\d{1,4}")

#: Маска защищённого фрагмента (Private Use Area — в тексте реплик не встречается).
_MASK_OPEN = "\ue000"
_MASK_CLOSE = "\ue001"
_MASK_RE = re.compile(rf"{_MASK_OPEN}(\d+){_MASK_CLOSE}")

#: Предел итераций схлопывания — защита от бесконечного цикла на битой строке.
_MAX_DRAWL_PASSES = 16


def _protect(text: str) -> tuple[str, list[str]]:
    """Спрятать URL, ``[...]``-теги и даты под маски; вернуть (текст, оригиналы)."""
    spans: list[str] = []

    def repl(match: re.Match[str]) -> str:
        spans.append(match.group(0))
        return f"{_MASK_OPEN}{len(spans) - 1}{_MASK_CLOSE}"

    for pattern in (_URL_RE, _TAG_RE, _DATE_RE):
        text = pattern.sub(repl, text)
    return text, spans


def _restore(text: str, spans: list[str]) -> str:
    """Вернуть защищённые фрагменты на место."""
    return _MASK_RE.sub(lambda m: spans[int(m.group(1))], text)


def collapse_drawl(text: str) -> str:
    """Схлопнуть протяжки: дефис/многоточие между одинаковыми буквами.

    ``ба-а-ам → бааам``, ``нее-ет → нееет``. Легитимные «кто-то», «ой-ой»,
    «по-моему» не меняются: буквы по разные стороны разные.
    """
    for _ in range(_MAX_DRAWL_PASSES):
        collapsed = _DRAWL_RE.sub(r"\1\2", text)
        if collapsed == text:
            return text
        text = collapsed
    return text


def _vowel_positions(word: str) -> list[int]:
    """Индексы гласных в слове (для номера ударной)."""
    return [i for i, char in enumerate(word) if char.lower() in _VOWELS]


def apply_accent(word: str, index: int, mode: str) -> str:
    """Поставить маркер ударения ``index``-й гласной (с 1) в выбранном режиме."""
    if mode == "off":
        return word
    positions = _vowel_positions(word)
    if not 1 <= index <= len(positions):
        return word
    at = positions[index - 1]
    if mode == "acute":
        return f"{word[: at + 1]}{STRESS_MARK}{word[at + 1:]}"
    if mode == "caps":
        return f"{word[:at]}{word[at].upper()}{word[at + 1:]}"
    if mode == "plus":
        return f"{word[: at + 1]}+{word[at + 1:]}"
    return word


class StressDictionary:
    """Словарь «слово → номер ударной гласной» (1 = первая гласная)."""

    def __init__(self) -> None:
        self.words: dict[str, int] = {}
        self.sources: list[str] = []

    def load(self, path: Path) -> int:
        """Догрузить словарь из YAML. Ошибки — жёлтый лог, не падение.

        Формат: ``words: {кирюха: 2}`` либо плоская карта ``{кирюха: 2}``.
        Загруженное перекрывает прежние записи (личный словарь главнее общего).
        """
        if not path.is_file():
            return 0
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("Словарь ударений '{}' не прочитан: {}", path, exc)
            return 0

        if isinstance(data, dict) and isinstance(data.get("words"), dict):
            data = data["words"]
        if not isinstance(data, dict):
            logger.warning("Словарь ударений '{}': ожидалась карта слово→номер, получено {}",
                           path, type(data).__name__)
            return 0

        loaded = 0
        for raw_word, raw_index in data.items():
            word = str(raw_word).strip().lower()
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                logger.warning("Словарь ударений '{}': у слова '{}' номер '{}' — не число",
                               path, raw_word, raw_index)
                continue
            if not word or not _WORD_RE.fullmatch(word):
                logger.warning("Словарь ударений '{}': слово '{}' не кириллическое — пропуск",
                               path, raw_word)
                continue
            if not 1 <= index <= len(_vowel_positions(word)):
                logger.warning("Словарь ударений '{}': '{}' → гласная {} вне диапазона (их {})",
                               path, word, index, len(_vowel_positions(word)))
                continue
            self.words[word] = index
            loaded += 1
        if loaded:
            self.sources.append(str(path))
        return loaded


class TextPreprocessor:
    """Текст реплики → текст для TTS (ударения + протяжки).

    Словари кэшируются по mtime: поправил YAML — следующий вызов его увидит,
    перезапуск сервера не нужен (ADR-012 hot-swap по духу).
    """

    def __init__(self, settings: Any) -> None:
        cfg = settings.voice.preprocess
        self.enabled: bool = bool(cfg.enabled)
        self.mode: str = cfg.stress_mode if cfg.stress_mode in STRESS_MODES else "acute"
        if cfg.stress_mode not in STRESS_MODES:
            logger.warning("voice.preprocess.stress_mode='{}' неизвестен — работаю как 'acute'",
                           cfg.stress_mode)
        self.drawl_enabled: bool = bool(cfg.drawl_enabled)
        self.shared_path: Path = resolve_path(cfg.stress_dict)
        self.personas_dir: Path = resolve_path(settings.app.personas_dir)
        #: persona → (mtime-ключ, словарь)
        self._cache: dict[str | None, tuple[tuple[float, ...], StressDictionary]] = {}

    # -- словари --------------------------------------------------------------- #
    def personal_path(self, persona: str | None) -> Path | None:
        """Личный словарь персоны (``personas/<id>/voice/stress.yaml``), если есть."""
        if not persona:
            return None
        return self.personas_dir / persona / "voice" / "stress.yaml"

    def _mtimes(self, persona: str | None) -> tuple[float, ...]:
        paths = [self.shared_path, self.personal_path(persona)]
        return tuple(p.stat().st_mtime if p is not None and p.is_file() else 0.0 for p in paths)

    def dictionary(self, persona: str | None = None) -> StressDictionary:
        """Словарь персоны: общий, поверх — личный (личный главнее)."""
        signature = self._mtimes(persona)
        cached = self._cache.get(persona)
        if cached is not None and cached[0] == signature:
            return cached[1]
        dictionary = StressDictionary()
        dictionary.load(self.shared_path)
        personal = self.personal_path(persona)
        if personal is not None:
            dictionary.load(personal)
        self._cache[persona] = (signature, dictionary)
        return dictionary

    def describe(self) -> dict[str, Any]:
        """Сводка для панели/диагностики: включено ли, режим, размеры словарей."""
        return {
            "enabled": self.enabled,
            "stress_mode": self.mode,
            "drawl_enabled": self.drawl_enabled,
            "stress_words": len(self.dictionary(None).words),
            "stress_dict": str(self.shared_path),
        }

    # -- преобразование -------------------------------------------------------- #
    def apply(self, text: str, persona: str | None = None) -> str:
        """Прогнать текст реплики: протяжки → ударения, поверх защищённых фрагментов."""
        if not self.enabled or not text:
            return text
        masked, spans = _protect(text)
        if self.drawl_enabled:
            masked = collapse_drawl(masked)
        if self.mode != "off":
            masked = self._stress(masked, persona)
        return _restore(masked, spans)

    def _stress(self, text: str, persona: str | None) -> str:
        words = self.dictionary(persona).words
        if not words:
            return text

        def repl(match: re.Match[str]) -> str:
            word = match.group(0)
            if STRESS_MARK in word:  # уже размечено — не трогаем (идемпотентность)
                return word
            index = words.get(word.lower())
            if index is None:
                return word
            return apply_accent(word, index, self.mode)

        return _WORD_RE.sub(repl, text)
