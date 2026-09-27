"""Горло — движок Qwen3-TTS (этап «Голос», ADR-027: «одна корона»).

Резидентный TTS-бэкенд 0.7.0: Qwen3-TTS-12Hz-0.6B-Base через рантайм
``faster-qwen3-tts`` (PyPI, CUDA Graphs + нативный стриминг; официальный
``qwen-tts`` стриминг из Python-API не отдаёт — см. ADR-027).

Один движок обслуживает ВСЕХ персон (Лилит, Оля, будущие): разные души —
разные ``reference.wav`` + ``soul.yaml`` в ``personas/<id>/voice/``, корона одна.
«Шкаф платьев» (silero/cosyvoice2/fish/edge) — ленивый: переключение явным
действием Курьера (панель «🎙 голос» → подтверждение → POST /api/voice/engine),
при переключении этот резидент выгружается (:meth:`unload`).

Контракт — общий ``TTSBackend`` (voice/tts.py):
  * ``synthesize(text, profile) -> bytes wav``
  * ``stream(text, profile)`` — НАТИВНЫЙ стриминг: чанки кодека по
    ``chunk_size`` шагов (8 ≈ 667 мс), первый звук до конца синтеза (TTFA,
    цель Ж2: ≤700 мс на RTX 3060). Каждый чанк — самостоятельный wav
    (контракт горла: плеер склеивает wav-куски, так уже было с silero).

Профиль (config.yaml → voice.profiles.<имя>):
    backend: "qwen3"
    reference: "personas/lilith/voice/reference.wav"   # mode=clone
    extra:
      model: "Qwen/Qwen3-TTS-12Hz-0.6B-Base"  # HF id (авто-загрузка) или локальная папка
      mode: "clone"            # clone | custom | design
      ref_text: "personas/lilith/voice/reference.txt"  # путь ИЛИ строка транскрипции;
                               # нет → честная деградация в xvec_only (клон бледнее)
      language: "Russian"      # Russian | English | Auto | ...
      chunk_size: 8            # стриминг: 8 шагов ≈ 667 мс аудио на чанк
      xvec_only: false         # true — только speaker-эмбеддинг (ref_text не нужен)
      speaker: "Vivian"        # mode=custom (9 тембров, RU-нативных нет — RU путь: clone)
      instruct: "..."          # mode=design / custom (instruction control — только 1.7B)
      temperature: 0.8         # опц.: транзитом в generate

Зависимости: extras ``[voice-qwen]`` → ``faster-qwen3-tts>=0.5.2,<0.6`` (тянет
``qwen-tts-hf``; НЕ ставить рядом upstream ``qwen-tts`` — конфликт дистрибутивов),
``torch>=2.5.1`` (на ≤2.5.0 захват CUDA-графов нестабилен). Windows: только
Torch-бэкенд (GGML-колёса — macOS/Linux). flash-attn не нужен (SDPA).
Веса: Apache-2.0, авто-загрузка из HF при первом старте (~1.6 ГБ + кодек).

ADR-024: никаких ``.Result``/``.Wait`` на главном потоке — вся тяжесть в
``asyncio.to_thread``, блокирующая очередь опустошается тоже через ``to_thread``.
ADR-012: ошибки НЕ глотаются — ``VoiceCore`` при исключении фолбэчит на
следующий доступный движок (silero/edge/mock).
"""

from __future__ import annotations

import asyncio
import gc
import queue
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from loguru import logger

from .stt import write_wav
from .tts import TTSBackend, VoiceProfile, split_sentences

__all__ = ["Qwen3TTS"]

#: Резидент по умолчанию (ADR-027): 0.6B-Base — клон, ~2.5–3.5 ГБ VRAM.
DEFAULT_MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"


def _to_wav_bytes(audio: Any, sample_rate: int) -> bytes:
    """float-массив чанка → int16 LE wav (контракт горла: плеер склеивает wav-куски)."""
    import numpy as np

    samples = np.asarray(audio, dtype="float32").reshape(-1)
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    return write_wav(pcm.tobytes(), sample_rate)


class Qwen3TTS(TTSBackend):
    """Qwen3-TTS через faster-qwen3-tts: клон/кастом/дизайн + нативный стриминг.

    Модель-синглтон на процесс (ключ — model id из ``extra.model``): грузится
    лениво (или прогревается при старте через :meth:`warmup`),
    ``warmup(prefill_len=100)`` захватывает CUDA-графы один раз.
    :meth:`unload` возвращает VRAM — его зовёт реестр при явном переключении
    движка («шкаф платьев»).
    """

    name = "qwen3"

    def __init__(self, device: str = "auto") -> None:
        self.device = device
        self._models: dict[str, Any] = {}
        self._lock = threading.Lock()  # от двойной загрузки (не от дедлока: ADR-024)

    # -- наличие --------------------------------------------------------------- #
    def available(self) -> tuple[bool, str]:
        import importlib.util

        if importlib.util.find_spec("faster_qwen3_tts") is None:
            return False, "faster-qwen3-tts не установлен (pip install -e .[voice-qwen])"
        try:
            import torch

            if not torch.cuda.is_available():
                return False, "CUDA недоступна: qwen3 требует GPU (реестр сфолбэчит по цепочке)"
        except ImportError:
            return False, "torch не установлен (pip install -e .[voice-qwen])"
        return True, ""

    def loaded(self) -> bool:
        """Веса в памяти? (панель «🎙 голос», ADR-027)."""
        return bool(self._models)

    # -- загрузка / выгрузка ----------------------------------------------------- #
    def _load_sync(self, model_id: str) -> Any:
        with self._lock:
            cached = self._models.get(model_id)
            if cached is not None:
                return cached
            from faster_qwen3_tts import FasterQwen3TTS

            logger.info("TTS qwen3: загружаю {} (warmup захватит CUDA-графы — первый раз долго)", model_id)
            t0 = time.perf_counter()
            model = FasterQwen3TTS.from_pretrained(model_id)
            model.warmup(prefill_len=100)
            self._models[model_id] = model
            logger.success(
                "TTS qwen3: {} горячая за {:.1f} с (резидент, ADR-027)",
                model_id, time.perf_counter() - t0,
            )
            return model

    async def _model(self, extra: dict[str, Any]) -> Any:
        model_id = str(extra.get("model") or DEFAULT_MODEL)
        return await asyncio.to_thread(self._load_sync, model_id)

    async def warmup(self, model_id: str | None = None) -> None:
        """Прогрев резидента при старте сервера (VoiceCore.start → lifespan)."""
        ok, reason = self.available()
        if not ok:
            logger.info("TTS qwen3: прогрев пропущен — {}", reason)
            return
        await asyncio.to_thread(self._load_sync, model_id or DEFAULT_MODEL)

    def unload(self) -> None:
        """Выгрузить веса и вернуть VRAM (переключение движка, ADR-027)."""
        with self._lock:
            names = list(self._models)
            self._models.clear()
        if not names:
            return
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
        logger.info("TTS qwen3: выгружена ({}) — VRAM возвращён", ", ".join(names))

    # -- план генерации по профилю ---------------------------------------------- #
    @staticmethod
    def _plan(profile: VoiceProfile) -> dict[str, Any]:
        """Собирает kwargs вызова из профиля. mode: clone (дефолт) | custom | design."""
        extra = dict(profile.extra or {})
        mode = str(extra.pop("mode", "clone")).lower()
        kwargs: dict[str, Any] = {"language": str(extra.pop("language", "Auto"))}
        for key in ("temperature", "top_p", "max_new_tokens"):  # транзитом в generate
            if key in extra:
                kwargs[key] = extra.pop(key)
        chunk_size = int(extra.pop("chunk_size", 8))

        if mode == "clone":
            if not profile.reference:
                raise ValueError(f"профиль '{profile.name}': mode=clone без reference (reference.wav)")
            kwargs["ref_audio"] = profile.reference
            ref_text_spec = str(extra.pop("ref_text", "") or "")
            if ref_text_spec:
                candidate = Path(ref_text_spec)
                if candidate.is_file():  # путь к файлу транскрипции (voice_preview пишет такой)
                    kwargs["ref_text"] = candidate.read_text(encoding="utf-8").strip()
                else:  # строка транскрипции прямо в конфиге
                    kwargs["ref_text"] = ref_text_spec
            xvec = extra.pop("xvec_only", None)
            if xvec is not None:
                kwargs["xvec_only"] = bool(xvec)
            elif "ref_text" not in kwargs:
                # ICL без транскрипции не работает — честная деградация вместо падения в бою
                logger.warning(
                    "профиль '{}': clone без ref_text — переключаю в xvec_only "
                    "(клон бледнее; положи reference.txt рядом с reference.wav)",
                    profile.name,
                )
                kwargs["xvec_only"] = True
            method, method_stream = "generate_voice_clone", "generate_voice_clone_streaming"
        elif mode == "custom":
            kwargs["speaker"] = str(extra.pop("speaker", profile.voice or "Vivian"))
            if extra.get("instruct"):
                kwargs["instruct"] = str(extra.pop("instruct"))
            method, method_stream = "generate_custom_voice", "generate_custom_voice_streaming"
        elif mode == "design":
            if not extra.get("instruct"):
                raise ValueError(f"профиль '{profile.name}': mode=design без instruct (описание души)")
            kwargs["instruct"] = str(extra.pop("instruct"))
            method, method_stream = "generate_voice_design", "generate_voice_design_streaming"
        else:
            raise ValueError(f"профиль '{profile.name}': неизвестный mode='{mode}' (clone|custom|design)")
        return {"method": method, "method_stream": method_stream, "kwargs": kwargs, "chunk_size": chunk_size}

    # -- полный wav -------------------------------------------------------------- #
    async def synthesize(self, text: str, profile: VoiceProfile) -> bytes:
        model = await self._model(profile.extra or {})
        plan = self._plan(profile)

        def _run() -> bytes:
            generate = getattr(model, plan["method"])
            audios, sr = generate(text=text, **plan["kwargs"])
            audio = audios[0] if isinstance(audios, (list, tuple)) else audios
            return _to_wav_bytes(audio, sr)

        return await asyncio.to_thread(_run)

    # -- нативный стриминг (чанки кодека, мельче предложений) ---------------------- #
    async def stream(self, text: str, profile: VoiceProfile) -> AsyncIterator[bytes]:
        """Куски wav по мере генерации: TTFA вместо time-to-full-audio.

        Мост sync-генератор (CUDA-графы, поток) → async (event loop): очередь с
        потолком 4 — генерация бежит впереди плеера, но память не бесконечна.
        Первый чанк логируется как TTFA — телеметрия Замера Ж2.
        """
        model = await self._model(profile.extra or {})
        plan = self._plan(profile)
        chunks: queue.Queue[Any] = queue.Queue(maxsize=4)
        t0 = time.perf_counter()

        def _produce() -> None:
            try:
                generate_stream = getattr(model, plan["method_stream"])
                first = True
                for item in generate_stream(text=text, chunk_size=plan["chunk_size"], **plan["kwargs"]):
                    audio, sr = item[0], item[1]  # (audio_chunk, sr, timing)
                    if first:
                        logger.info(
                            "TTS qwen3: TTFA {:.0f} мс (профиль '{}', chunk_size={})",
                            (time.perf_counter() - t0) * 1000, profile.name, plan["chunk_size"],
                        )
                        first = False
                    chunks.put(_to_wav_bytes(audio, sr))
            except BaseException as exc:  # noqa: BLE001 — ошибку обязан увидеть потребитель
                chunks.put(exc)
            finally:
                chunks.put(None)

        producer = asyncio.create_task(asyncio.to_thread(_produce))
        try:
            while True:
                item = await asyncio.to_thread(chunks.get)
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise item  # ADR-012: VoiceCore поймает и сфолбэчит
                yield item
        finally:
            await producer

    # -- стриминг по предложениям (страховка) ------------------------------------ #
    async def stream_sentences(self, text: str, profile: VoiceProfile) -> AsyncIterator[bytes]:
        """Предложный стриминг через synthesize — если нативный в новой версии обёртки сдуется."""
        for sentence in split_sentences(text):
            yield await self.synthesize(sentence, profile)
