"""Лицо (этап 5): парсер тегов эмоций из реплик + словари согласования.

Реплика модели может нести теги вида ``[emotion: joy]``. Парсер:

* вынимает теги в события :class:`EmotionEvent` (имя + позиция в тексте),
* возвращает чистый текст без тэгов (пользователь и TTS не должны видеть скобки),
* при желании угадывает эмоцию по ключевым словам, если тегов нет.

Словари согласования: одна и та же эмоция красит **лицо** (blend-shapes VMC /
хоткеи VTuber Studio) и **голос** (просодия) — один источник, два потребителя.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = [
    "EmotionEvent",
    "EMOTIONS",
    "EMOTION_ALIASES",
    "EMOTION_TO_BLENDSHAPES",
    "EMOTION_TO_PROSODY",
    "KEYWORD_FALLBACK",
    "parse_emotions",
    "strip_emotion_tags",
    "guess_emotion",
]

#: Базовый набор эмоций Лилит (расширяется конфигом, не кодом).
EMOTIONS: tuple[str, ...] = (
    "neutral",
    "joy",
    "smug",
    "love",
    "anger",
    "sadness",
    "surprise",
    "embarrassment",
    "sleepy",
    "evil",
)

#: Синонимы тегов: русские/альтернативные имена -> канон.
EMOTION_ALIASES: dict[str, str] = {
    "happy": "joy",
    "радость": "joy",
    "веселье": "joy",
    "smile": "joy",
    "smugface": "smug",
    "хитрость": "smug",
    "довольство": "smug",
    "любовь": "love",
    "нежность": "love",
    "heart": "love",
    "злость": "anger",
    "ярость": "anger",
    "rage": "anger",
    "грусть": "sadness",
    "печаль": "sadness",
    "sad": "sadness",
    "удивление": "surprise",
    "шок": "surprise",
    "смущение": "embarrassment",
    "stigma": "embarrassment",
    "сон": "sleepy",
    "усталость": "sleepy",
    "вампир": "evil",
    "злодейство": "evil",
    "нейтрально": "neutral",
}

_TAG_RE = re.compile(r"\[\s*(?:emotion|эмоция)\s*:\s*([A-Za-zА-Яа-яЁё_\- ]+?)\s*\]", re.IGNORECASE)


@dataclass(slots=True)
class EmotionEvent:
    """Событие эмоции: каноническое имя, сырое имя и позиция в тексте."""

    name: str
    raw: str
    position: int  # индекс символа в исходном тексте


def _canonical(raw: str) -> str:
    """Приводит сырое имя тега к канону: алиасы + регистр."""
    key = raw.strip().lower()
    if key in EMOTION_ALIASES:
        return EMOTION_ALIASES[key]
    return key if key in EMOTIONS else key  # неизвестные имена проходят как есть


def parse_emotions(text: str, *, keyword_fallback: bool = True) -> list[EmotionEvent]:
    """События эмоций из тегов; если тегов нет — догадка по ключевым словам."""
    events: list[EmotionEvent] = []
    for match in _TAG_RE.finditer(text):
        raw = match.group(1)
        events.append(EmotionEvent(name=_canonical(raw), raw=raw.strip(), position=match.start()))
    if not events and keyword_fallback:
        guessed = guess_emotion(text)
        if guessed:
            events.append(EmotionEvent(name=guessed, raw=guessed, position=0))
    return events


def strip_emotion_tags(text: str) -> tuple[str, list[EmotionEvent]]:
    """(чистый текст, события): теги вырезаются, пробелы схлопываются аккуратно."""
    events: list[EmotionEvent] = []

    def _sub(match: re.Match[str]) -> str:
        events.append(EmotionEvent(name=_canonical(match.group(1)), raw=match.group(1).strip(), position=match.start()))
        return ""

    clean = _TAG_RE.sub(_sub, text)
    clean = re.sub(r"[ \t]{2,}", " ", clean).strip()
    return clean, events


#: Ключевые слова догадки (рус/англ), порядок = приоритет.
KEYWORD_FALLBACK: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("evil", ("кхххх", "спички", "сжечь", "мое сокровище")),
    ("smug", ("хи-хи", "хех", "разумеется", "само собой")),
    ("love", ("люблю", "мой сладкий", "мой мальчик", "мур-мур", "обнимаю")),
    ("joy", ("ура", "класс", "здорово", "йей", "ураа")),
    ("anger", ("нет.", "хватит", "разозлил")),
    ("sadness", ("грустно", "увы", "жаль")),
    ("surprise", ("ого", "ничего себе", "-wow")),
    ("embarrassment", ("ой", "простии", "неловко")),
    ("sleepy", ("спать", "зев", "сон")),
)


def guess_emotion(text: str) -> str | None:
    """Догадка по ключевым словам; None, если эмоций не слышно."""
    lowered = text.lower()
    for emotion, words in KEYWORD_FALLBACK:
        if any(word in lowered for word in words):
            return emotion
    return None


#: Эмоция -> blend-shapes VMC (имена, которые понимает большинство VRM-аватаров).
EMOTION_TO_BLENDSHAPES: dict[str, dict[str, float]] = {
    "neutral": {"Joy": 0.0, "Angry": 0.0, "Sorrow": 0.0, "Fun": 0.0},
    "joy": {"Joy": 1.0, "Fun": 0.4},
    "smug": {"Joy": 0.6, "Fun": 0.7},
    "love": {"Joy": 0.8, "Fun": 0.3, "EyeClose": 0.4},
    "anger": {"Angry": 1.0},
    "sadness": {"Sorrow": 1.0},
    "surprise": {"Fun": 0.5, "EyeOpen": 1.0, "MouthOpen": 0.6},
    "embarrassment": {"Sorrow": 0.3, "Joy": 0.3, "EyesClose": 0.2},
    "sleepy": {"EyeClose": 0.8, "Sorrow": 0.2},
    "evil": {"Angry": 0.5, "Fun": 0.8},
}

#: Эмоция -> просодия голоса (множители скорости/высоты) для этапа 4+.
EMOTION_TO_PROSODY: dict[str, dict[str, float]] = {
    "neutral": {"speed": 1.0, "pitch": 1.0},
    "joy": {"speed": 1.08, "pitch": 1.1},
    "smug": {"speed": 0.95, "pitch": 1.05},
    "love": {"speed": 0.9, "pitch": 1.05},
    "anger": {"speed": 1.12, "pitch": 0.9},
    "sadness": {"speed": 0.85, "pitch": 0.92},
    "surprise": {"speed": 1.1, "pitch": 1.15},
    "embarrassment": {"speed": 0.95, "pitch": 1.08},
    "sleepy": {"speed": 0.8, "pitch": 0.95},
    "evil": {"speed": 0.9, "pitch": 0.85},
}


def blends_for(emotion: str) -> dict[str, float]:
    """Blend-shapes для эмоции; неизвестная -> neutral."""
    return dict(EMOTION_TO_BLENDSHAPES.get(emotion, EMOTION_TO_BLENDSHAPES["neutral"]))


def prosody_for(emotion: str) -> dict[str, float]:
    """Просодия для эмоции; неизвестная -> neutral."""
    return dict(EMOTION_TO_PROSODY.get(emotion, EMOTION_TO_PROSODY["neutral"]))


def describe() -> dict[str, Any]:
    """Сводка словаря для /api/face/state и будущей панели."""
    return {
        "emotions": list(EMOTIONS),
        "aliases": EMOTION_ALIASES,
        "blendshapes": {k: v for k, v in EMOTION_TO_BLENDSHAPES.items()},
        "prosody": {k: v for k, v in EMOTION_TO_PROSODY.items()},
    }
