"""Проба RVC-движка на живой машине (F7) — 0.7.2-б, ADR-032.

Зачем: в песочнице нет ни GPU, ни весов Эсдес, поэтому выбор движка
(``rvc-python`` / ``Aplio``) делается здесь — на машине Курьера, до включения
флага ``voice.rvc.enabled``. Скрипт гоняет **конвейер** (``RvcStream``) на реальном
звуке и печатает RTF, число вызовов и окружение (torch/CUDA/VRAM).

Контракт движка — один вызов::

    convert(pcm: bytes, rate: int) -> bytes      # PCM16 mono, та же частота

Движок передаётся строкой ``module:function`` или ``module:Class``. Класс
инстанцируется, у него вызывается ``convert_pcm``. Обёртка-адаптер под конкретный
пакет (rvc-python/Aplio) пишется здесь же и **не попадает в ядро**, пока проба
не покажет, что он живой.

Примеры (Windows, на машине Курьера)::

    .venv\\Scripts\\python -m scripts.rvc_probe --selftest
    .venv\\Scripts\\python -m scripts.rvc_probe --wav voices/probe.wav ^
        --engine my_rvc_adapter:convert --out data/rvc_probe.wav --json data/rvc_probe.json

``--selftest`` работает где угодно: проверяет только конвейер на NullRvc.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import struct
import sys
import time
from pathlib import Path

# Скрипт лежит в lilith/scripts/, пакет — в lilith/src.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lilith_core.voice.rvc import NullRvc, RvcBackend, RvcStream  # noqa: E402
from lilith_core.voice.stt import read_wav, write_wav  # noqa: E402

RATE = 24000


def synthetic_wav(seconds: float = 2.0, rate: int = RATE) -> bytes:
    """Синус 220 Гц — предсказуемый вход без файлов."""
    frames = int(rate * seconds)
    samples = b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * 220 * i / rate)))
        for i in range(frames)
    )
    return write_wav(samples, rate)


class _CallableRvc:
    """Адаптер «просто функция» вокруг контракта RvcBackend."""

    name = "probe"

    def __init__(self, func) -> None:
        self._func = func
        self.calls = 0

    def available(self) -> tuple[bool, str]:
        return True, "проба"

    async def convert_pcm(self, pcm: bytes, rate: int) -> bytes:
        self.calls += 1
        return self._func(pcm, rate)


def load_engine(spec: str | None) -> RvcBackend:
    """``module:func``/``module:Class`` → RvcBackend. None → NullRvc."""
    if not spec:
        return NullRvc()
    module_name, _, attr = spec.partition(":")
    if not attr:
        raise SystemExit(f"движок задаётся как module:function, получено: {spec}")
    module = importlib.import_module(module_name)
    target = getattr(module, attr)
    if isinstance(target, type):
        target = target()
    func = getattr(target, "convert_pcm", target)
    engine = _CallableRvc(func)
    if hasattr(target, "name"):
        engine.name = str(getattr(target, "name"))
    return engine


def environment_report() -> dict:
    """Что за железо: torch/CUDA/VRAM, если стоят; иначе честно «нет»."""
    report: dict = {"python": sys.version.split()[0]}
    try:
        import torch  # noqa: PLC0415 — опционально, только для пробы
    except Exception as exc:  # noqa: BLE001
        report.update(torch=None, reason=f"torch не установлен: {exc}")
        return report
    report["torch"] = torch.__version__
    report["cuda_available"] = bool(torch.cuda.is_available())
    if torch.cuda.is_available():
        free, total = torch.cuda.mem_get_info()
        report.update(
            device=torch.cuda.get_device_name(0),
            vram_total_gb=round(total / 1024**3, 2),
            vram_free_gb=round(free / 1024**3, 2),
        )
    return report


async def run_probe(wav_bytes: bytes, engine: RvcBackend, *, context_ms: int, crossfade_ms: int) -> dict:
    """Прогнать звук через конвейер и замерить RTF."""
    stream = RvcStream(engine, context_ms=context_ms, crossfade_ms=crossfade_ms)
    started = time.perf_counter()
    out: list[bytes] = []
    out.extend(await stream.push(wav_bytes))
    out.extend(await stream.flush())
    elapsed = time.perf_counter() - started

    pcm, rate = read_wav(wav_bytes)
    audio_seconds = len(pcm) / 2 / rate
    result_wav = b"".join(out)
    result_pcm, result_rate = read_wav(result_wav)
    return {
        "engine": engine.name,
        "audio_seconds": round(audio_seconds, 3),
        "wall_seconds": round(elapsed, 3),
        "rtf": round(elapsed / audio_seconds, 3) if audio_seconds else None,
        "out_seconds": round(len(result_pcm) / 2 / result_rate, 3),
        "wav": result_wav,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проба RVC-движка (F7, 0.7.2-б)")
    parser.add_argument("--wav", type=Path, help="входной WAV (моно 16 бит); нет — синтетика")
    parser.add_argument("--engine", help="module:function|module:Class; нет — NullRvc")
    parser.add_argument("--out", type=Path, default=Path("data/rvc_probe.wav"))
    parser.add_argument("--json", type=Path, help="куда положить отчёт")
    parser.add_argument("--context-ms", type=int, default=400)
    parser.add_argument("--crossfade-ms", type=int, default=50)
    parser.add_argument("--selftest", action="store_true", help="только конвейер на NullRvc")
    args = parser.parse_args(argv)

    if args.selftest:
        args.engine = None
        args.wav = None

    source = args.wav.read_bytes() if args.wav else synthetic_wav()
    try:
        engine = load_engine(args.engine)
    except Exception as exc:  # noqa: BLE001
        print(f"ДВИЖОК НЕ ЗАГРУЖЕН: {type(exc).__name__}: {exc}")
        return 2

    import asyncio

    try:
        result = asyncio.run(
            run_probe(source, engine, context_ms=args.context_ms, crossfade_ms=args.crossfade_ms)
        )
    except Exception as exc:  # noqa: BLE001
        print(f"КОНВЕРСИЯ УПАЛА: {type(exc).__name__}: {exc}")
        return 3

    out_wav = result.pop("wav")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(out_wav)
    report = {**result, "environment": environment_report(), "out": str(args.out)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
