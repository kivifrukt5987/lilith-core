"""«Шкаф платьев» (ADR-027, п.2): ленивые слоты альтернативных TTS-движков.

Корона одна — резидент ``qwen3`` (Qwen3-TTS 0.6B-Base). Остальные движки
присутствуют в реестре, конфиге и панели «🎙 голос» как ОПЦИИ, но веса НЕ
грузят и автоматически не выбираются:

* активация — только явным действием Курьера (выбор в панели + подтверждение,
  или POST /api/voice/engine);
* при активации резидент выгружается (``TTSRegistry.switch_resident`` →
  ``unload()`` у прочих) — VRAM (12 ГБ) бережётся для игр и сюжетных LLM;
* ``available()`` честно возвращает False с причиной, пока движок не подключён,
  поэтому ``pick_backend`` их никогда не выберет сам (фолбэк-цепочка ADR-012
  проходит мимо — и это правильно: платье из шкафа надевают осознанно).

CosyVoice 2 и Fish Speech (Q2 финальной директивы): в 0.7.0 — слоты без
реализации. Когда дойдут руки: наследовать TTSBackend, реализовать
synthesize/stream/unload, вернуть из available() настоящую проверку, и
подключить реализации в ``_engine``-слот (у ZeroShotTTS контракт уже готов).
"""

from __future__ import annotations

from .tts import TTSBackend

__all__ = ["CosyVoice2TTS", "FishSpeechTTS"]


class CosyVoice2TTS(TTSBackend):
    """Слот CosyVoice 2 (шкаф платьев): в 0.7.0 не резидент и не реализован."""

    name = "cosyvoice2"

    def available(self) -> tuple[bool, str]:
        return False, (
            "шкаф платьев (ADR-027 Q2): CosyVoice 2 в 0.7.0 не подключён — "
            "слот занят под будущее платье, корона у qwen3"
        )


class FishSpeechTTS(TTSBackend):
    """Слот Fish Speech (шкаф платьев): в 0.7.0 не резидент и не реализован."""

    name = "fish"

    def available(self) -> tuple[bool, str]:
        return False, (
            "шкаф платьев (ADR-027 Q2): Fish Speech в 0.7.0 не подключён — "
            "слот занят под будущее платье, корона у qwen3"
        )
