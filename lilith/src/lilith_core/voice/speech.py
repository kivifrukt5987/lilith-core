"""Извлечение секции речи из ответа Лилит (0.7.2, патч A, ADR-031).

Модель отвечает не только речью: по формату `AGENTS.md`/`PERSONA.md` в ответе есть
каркас — ``[СИСТЕМНОЕ УВЕДОМЛЕНИЕ: …]``, ``АНАЛИЗ …``, ``МЫСЛИ ЛИЛИТ: *…*``,
служебные заголовки и теги ``[emotion: …]``. Панель и лицо этот каркас показывают,
**горло — нет**: 17.11 поле показало, что gemma в non-thinking-режиме отдаёт каркас
целиком, и TTS читала «системное уведомление, анализ действий…» вслух.

Философия — **консервативная**:

* текст без признаков каркаса возвращается как есть (обычная фраза «Привет!» или
  «БЫСТРО» не пострадает);
* если признак каркаса есть — вырезаем теги, служебные заголовки, их списки и
  блоки-размышления (курсив), оставляем речь;
* если после вычистки не осталось ничего — возвращаем пустую строку: молчать
  честнее, чем читать вслух каркас (вызывающий пишет warning в лог).
"""

from __future__ import annotations

import re

#: Служебные заголовки формата ответа (начало строки, регистр не важен).
#: Осторожно с короткими словами: «мысли» ловим только как «МЫСЛИ ЛИЛИТ» или
#: «МЫСЛИ:» — иначе речь вроде «Мысли путаются…» умолкла бы.
SCAFFOLD_PATTERNS: tuple[str, ...] = (
    r"системное уведомление",
    r"lilith\.exe",
    r"анализ\b",
    r"мысли лилит\b",
    r"мысли\s*[:：]",
    r"двойное мышление\b",
    r"ситуация\s*[:/]",
    r"опасность\s*[:/]",
    r"выбор\s*[:/]",
    r"решение\s*[:/]",
    r"речь вслух\b",
)

_SCAFFOLD_RE = re.compile("^(?:" + "|".join(SCAFFOLD_PATTERNS) + ")", re.IGNORECASE)

#: ``[emotion: joy]``, ``[laughs]``, ``[СИСТЕМНОЕ УВЕДОМЛЕНИЕ: …]`` — всё в скобках.
_TAG_RE = re.compile(r"\[[^\]]*\]")

#: Элемент списка под заголовком каркаса (``1.``, ``2)``, ``-``, ``•``).
_LIST_RE = re.compile(r"^\s*(?:\d+[.)]|[-•])\s+")

#: Строка целиком в markdown-акценте: ``*мысли*`` / ``**жирно**`` / ``_курсив_``.
_EMPHASIS_LINE_RE = re.compile(r"^\s*(\*\*|\*|__|_)(?P<body>.+?)\1\s*$")


def _strip_emphasis(line: str) -> str:
    """Снять обрамляющий ``*``/``**``/``_`` со строки (заголовки часто жирные)."""
    match = _EMPHASIS_LINE_RE.match(line)
    return match.group("body") if match else line


def _header_text(line: str) -> str:
    """Строка без markdown-обвязки — для распознавания заголовка.

    ``**АНАЛИЗ**:`` → ``АНАЛИЗ:``. Для вывода используется ``_strip_emphasis``:
    звёздочки в речи (``*смеётся*``) не вычищаются.
    """
    plain = line.replace("*", "").replace("_", "").strip()
    return plain.lstrip("#> ").strip()


def _is_scaffold_header(line: str) -> bool:
    """Служебный заголовок: известное начало строки (см. ``SCAFFOLD_PATTERNS``)."""
    plain = _header_text(line)
    return bool(plain) and bool(_SCAFFOLD_RE.match(plain))


def has_scaffold(text: str) -> bool:
    """Есть ли в тексте признаки каркаса (теги или служебный заголовок)."""
    if _TAG_RE.search(text):
        return True
    return any(_is_scaffold_header(line) for line in text.splitlines())


def extract_speech(text: str) -> str:
    """Ответ модели → только речь (текст для TTS).

    Идемпотентна: повторный проход не меняет результат.
    Пустая строка — в ответе одна «служебка», озвучивать нечего.
    """
    if not text or not text.strip():
        return ""
    if not has_scaffold(text):
        return text.strip()

    cleaned = _TAG_RE.sub(" ", text)
    blocks = re.split(r"\n\s*\n", cleaned)
    kept_blocks: list[str] = []
    in_scaffold = False

    for block in blocks:
        kept_lines: list[str] = []
        for line in block.splitlines():
            plain = _strip_emphasis(line).strip()
            if not plain:
                continue
            if _is_scaffold_header(line):
                in_scaffold = True
                continue
            if in_scaffold and (_LIST_RE.match(line) or _EMPHASIS_LINE_RE.match(line)):
                continue  # список/размышление под заголовком каркаса
            in_scaffold = False
            kept_lines.append(plain)
        if kept_lines:
            kept_blocks.append("\n".join(kept_lines))

    result = "\n\n".join(kept_blocks).strip()
    result = re.sub(r"[ \t]{2,}", " ", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result
