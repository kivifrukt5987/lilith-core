"""Voice Design: выбор «души» голоса для персон (этап «Голос», ADR-027).

v0.7.0 — в составе пакета 0.7.0-voice (ADR-027 §4: душа живёт в
personas/<id>/voice/). Гварды: tests/test_voice_preview.py.

Что делает:
  1. ГЕНЕРИРУЕТ N вариаций голоса из текстового описания (instruct) + seed'ов
     моделью Qwen3-TTS-12Hz-1.7B-VoiceDesign (офлайн-инструмент, НЕ резидент).
     Это генератор, а не плеер: каждый запуск — новая речь из описания души.
  2. Раскладывает вариации в wav + manifest.yaml («паспорт души»: instruct, seed'ы,
     текст, тайминги) — воспроизводимо, можно перегенерировать.
  3. --adopt N: усыновляет выбранную вариацию как референс персоны —
     personas/<id>/voice/reference.wav + reference.txt (точная транскрипция)
     + soul.yaml (путь по ADR-027 §4; старый voices/ — через --adopt-dir).
  4. --verify-clone: контрольный выстрел — клонирует душу на резидентной
     0.6B-Base и пишет adopt_check.wav (проверяем, что тембр переживает клон,
     ведь в проде говорит 0.6B-Base, а не VoiceDesign).

Запуск (из папки lilith/, офлайн: LLM/STT/Unity выключены — 1.7B ест до ~6.7GB VRAM):
    pip install -e .[voice-qwen]
    python scripts/voice_preview.py --persona lilith --count 12 \
        --instruct "нежный женский, анимешный, с придыханием"
    python scripts/voice_preview.py --persona lilith --count 15 --random-seeds
    python scripts/voice_preview.py --persona lilith --adopt 7 --verify-clone

build_parser() вынесен отдельно для будущих тестов (принцип check_csharp_syntax.py, 0.6.6).
ADR-024 помним: скрипт однопоточный и сам себе хозяин, главный поток чужих приложений
не блокирует — heavy-вызовы только в этом процессе.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------- #
# Пресеты-затравки (DRAFT: «душу» всё равно выбирает Курьер, это лишь старт)   #
# --------------------------------------------------------------------------- #
PRESET_INSTRUCTS: dict[str, str] = {
    "lilith": (
        "Молодой женский голос, тёплый и чуть хрипловатый, уверенный, "
        "с лукавой игривой интонацией; говорит расслабленно, с выразительными "
        "паузами и улыбкой в голосе."
    ),
    "olya": (
        "Молодой женский голос, светлый и энергичный, быстрая речь, искренний "
        "энтузиазм; интонация прыгает от нежности к азарту."
    ),
}

#: Дефолтный тестовый текст: ~9-11 секунд русской речи — достаточно, чтобы
#: услышать характер, и годится как reference.wav (требование: 5-10+ сек).
DEFAULT_TEXT = (
    "Ну привет, Кирюша. Я тут подумала: если ты опять забудешь поесть, "
    "я лично приду и буду стоять над душой, пока всё не доешь. "
    "А теперь рассказывай, как прошёл день?"
)

#: Текст контрольного клона (--verify-clone): короткий, нейтральный.
VERIFY_TEXT = "Раз-два-три. Душа на месте, голос мой. Работаем."

DEFAULT_DESIGN_MODEL = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
DEFAULT_CLONE_MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"


def build_parser() -> argparse.ArgumentParser:
    """CLI-контракт скрипта (вынесено для тестов)."""
    p = argparse.ArgumentParser(
        prog="voice_preview",
        description="Qwen3-TTS Voice Design: N вариаций голоса из описания + seed'ы, "
                    "затем усыновление выбранной как reference персоны.",
    )
    p.add_argument("--persona", default="lilith",
                   help="имя персоны (lilith | olya | любое) — влияет на папку и пресет")
    p.add_argument("--instruct", default=None,
                   help="текстовое описание души голоса; без него — пресет из PRESET_INSTRUCTS")
    p.add_argument("--text", default=DEFAULT_TEXT,
                   help="тестовая фраза для всех вариаций (одинаковая — честное сравнение)")
    p.add_argument("--language", default="Russian",
                   help="язык генерации (Russian | English | Auto | ...)")
    p.add_argument("--count", type=int, default=12,
                   help="сколько вариаций сгенерировать (ТЗ: 10-15)")
    p.add_argument("--seed-base", type=int, default=20260927,
                   help="база seed'ов: seed_i = seed_base + i*1000 (пишется в manifest)")
    p.add_argument("--random-seeds", action="store_true",
                   help="взять базу seed'ов из энтропии ОС (фактические seed'ы — в manifest)")
    p.add_argument("--model", default=DEFAULT_DESIGN_MODEL,
                   help="VoiceDesign-модель (HF id или локальная папка)")
    p.add_argument("--clone-model", default=DEFAULT_CLONE_MODEL,
                   help="резидентная Base-модель для --verify-clone")
    p.add_argument("--outdir-root", default="voices/preview",
                   help="корень для папок preview (по умолчанию voices/preview/)")
    p.add_argument("--adopt", type=int, default=None, metavar="N",
                   help="усыновить вариацию №N (1-based) из последней папки preview персоны")
    p.add_argument("--adopt-dir", default=None,
                   help="куда усыновлять (дефолт ADR-027: personas/<persona>/voice/; "
                        "для старой схемы voices/ передай --adopt-dir voices/<persona>)")
    p.add_argument("--preview-dir", default=None,
                   help="явная папка preview для --adopt (иначе ищем свежайшую)")
    p.add_argument("--verify-clone", action="store_true",
                   help="после усыновления: контрольный клон на 0.6B-Base -> adopt_check.wav")
    p.add_argument("--backend", default="torch", choices=["torch", "ggml"],
                   help="torch = CUDA Graphs (наш путь под Windows); ggml — не для Win, оставлен на вырост")
    return p


# --------------------------------------------------------------------------- #
# Вспомогательное                                                             #
# --------------------------------------------------------------------------- #
def _check_gpu(min_free_gb: float = 8.0) -> None:
    """Мягкий страж VRAM: 1.7B-VoiceDesign хочет ~6.7GB пиком (замер T4)."""
    import torch

    if not torch.cuda.is_available():
        sys.exit("[voice_preview] CUDA недоступна. Нужен GPU (RTX 3060) и torch>=2.5.1. "
                 "Проверь: nvidia-smi; pip install -e .[voice-qwen]")
    free, total = torch.cuda.mem_get_info()
    free_gb, total_gb = free / 2**30, total / 2**30
    print(f"[voice_preview] GPU: {torch.cuda.get_device_name(0)}, "
          f"VRAM свободно {free_gb:.1f} / {total_gb:.1f} GB")
    if free_gb < min_free_gb:
        print(f"[voice_preview] ВНИМАНИЕ: свободно < {min_free_gb} GB. "
              "Выключи LLM (LM Studio/llama.cpp), Unity-тело и браузер, иначе OOM. "
              "Продолжаю на твой страх и риск...")


def _load_model(model_id: str, backend: str):
    """Грузим FasterQwen3TTS лениво (импорт torch-free на уровне модуля)."""
    from faster_qwen3_tts import FasterQwen3TTS

    print(f"[voice_preview] загружаю {model_id} (backend={backend})...")
    t0 = time.perf_counter()
    model = FasterQwen3TTS.from_pretrained(model_id)
    # warmup захватывает CUDA-графы: первая генерация без него была бы медленной
    model.warmup(prefill_len=100)
    print(f"[voice_preview] модель горячая за {time.perf_counter() - t0:.1f} с")
    return model


def _generate_design(model, text: str, language: str, instruct: str, seed: int, **kwargs):
    """Одна вариация = один seed. Возвращает (audio_float_array, sample_rate, gen_seconds).

    kwargs (temperature/top_p и т.п.) транслируются в generate; если обёртка их
    не принимает — ретрай без них (защита от смены сигнатуры в новых версиях).
    """
    import torch

    torch.manual_seed(seed)  # seed на все устройства — вариативность тембра при том же instruct
    t0 = time.perf_counter()
    try:
        audios, sr = model.generate_voice_design(
            text=text, language=language, instruct=instruct, **kwargs
        )
    except TypeError:
        audios, sr = model.generate_voice_design(text=text, language=language, instruct=instruct)
    gen_s = time.perf_counter() - t0
    audio = audios[0] if isinstance(audios, (list, tuple)) else audios
    return audio, sr, gen_s


def _latest_preview_dir(persona: str, outdir_root: Path) -> Path | None:
    candidates = sorted(outdir_root.glob(f"{persona}_*"), reverse=True)
    return candidates[0] if candidates else None


# --------------------------------------------------------------------------- #
# Режим 1: генерация вариаций                                                 #
# --------------------------------------------------------------------------- #
def run_preview(args: argparse.Namespace) -> Path:
    instruct = args.instruct or PRESET_INSTRUCTS.get(args.persona)
    if not instruct:
        sys.exit(f"[voice_preview] нет --instruct и нет пресета для персоны '{args.persona}'. "
                 "Опиши душу голоса текстом (см. PRESET_INSTRUCTS как пример).")

    seed_base = args.seed_base
    if args.random_seeds:
        import secrets

        seed_base = secrets.randbelow(2**31 - 1) + 1
        print(f"[voice_preview] --random-seeds: база seed'ов из энтропии = {seed_base} "
              "(запишется в manifest — души воспроизводимы)")

    outdir = Path(args.outdir_root) / f"{args.persona}_{datetime.now():%Y%m%d_%H%M%S}"
    outdir.mkdir(parents=True, exist_ok=True)

    _check_gpu()
    model = _load_model(args.model, args.backend)

    import soundfile as sf
    import yaml

    manifest: dict = {
        "persona": args.persona,
        "created": datetime.now().isoformat(timespec="seconds"),
        "model": args.model,
        "backend": args.backend,
        "instruct": instruct,
        "text": args.text,
        "language": args.language,
        "seed_base": seed_base,
        "random_seeds": bool(args.random_seeds),
        "variants": [],
    }

    print(f"[voice_preview] генерирую {args.count} вариаций -> {outdir}")
    for i in range(args.count):
        seed = seed_base + i * 1000
        audio, sr, gen_s = _generate_design(model, args.text, args.language, instruct, seed)
        dur_s = len(audio) / sr
        fname = f"preview_{i + 1:02d}_seed{seed}.wav"
        sf.write(outdir / fname, audio, sr)
        manifest["variants"].append({
            "n": i + 1, "file": fname, "seed": seed,
            "audio_sec": round(dur_s, 2), "gen_sec": round(gen_s, 2),
            "rtf": round(dur_s / gen_s, 2) if gen_s > 0 else None,
        })
        print(f"  [{i + 1:02d}/{args.count}] seed={seed} audio={dur_s:.1f}с "
              f"gen={gen_s:.1f}с RTF={dur_s / gen_s:.2f} -> {fname}")

    with open(outdir / "manifest.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(manifest, f, allow_unicode=True, sort_keys=False)
    print(f"[voice_preview] готово. Слушай {outdir.resolve()} "
          f"(Windows: start {outdir.resolve()})")
    print(f"[voice_preview] выбрал? ->  python scripts/voice_preview.py "
          f"--persona {args.persona} --adopt <N> [--verify-clone]")
    return outdir


# --------------------------------------------------------------------------- #
# Режим 2: усыновление вариации (design -> reference персоны)                 #
# --------------------------------------------------------------------------- #
def run_adopt(args: argparse.Namespace) -> None:
    import yaml

    outdir_root = Path(args.outdir_root)
    preview_dir = (
        Path(args.preview_dir) if args.preview_dir
        else _latest_preview_dir(args.persona, outdir_root)
    )
    if preview_dir is None or not preview_dir.is_dir():
        sys.exit(f"[voice_preview] папка preview для '{args.persona}' не найдена в {outdir_root}. "
                 "Сначала сгенерируй вариации.")

    manifest_path = preview_dir / "manifest.yaml"
    if not manifest_path.is_file():
        sys.exit(f"[voice_preview] {manifest_path} не найден — нечего усыновлять.")
    with open(manifest_path, encoding="utf-8") as f:
        manifest = yaml.safe_load(f)

    variant = next((v for v in manifest["variants"] if v["n"] == args.adopt), None)
    if variant is None:
        sys.exit(f"[voice_preview] вариация №{args.adopt} не найдена в манифесте "
                 f"(есть 1..{len(manifest['variants'])}).")

    src = preview_dir / variant["file"]
    if not src.is_file():
        sys.exit(f"[voice_preview] {src} отсутствует на диске.")

    # Референс персоны (ADR-027 §4): personas/<persona>/voice/reference.wav +
    # reference.txt (точная транскрипция — обязательна для ICL-клона; у нас она
    # известна: это text манифеста). Старая схема voices/ — через --adopt-dir.
    adopt_dir = Path(args.adopt_dir) if args.adopt_dir else Path("personas") / args.persona / "voice"
    adopt_dir.mkdir(parents=True, exist_ok=True)
    ref_wav = adopt_dir / "reference.wav"
    ref_txt = adopt_dir / "reference.txt"
    shutil.copyfile(src, ref_wav)
    ref_txt.write_text(manifest["text"].strip() + "\n", encoding="utf-8")

    # Паспорт души рядом с референсом — чтобы через год было понятно, откуда она.
    soul = {
        "persona": args.persona,
        "adopted": datetime.now().isoformat(timespec="seconds"),
        "source_preview": str(preview_dir),
        "variant_n": variant["n"],
        "seed": variant["seed"],
        "instruct": manifest["instruct"],
        "ref_text": manifest["text"].strip(),
        "model": manifest["model"],
    }
    with open(adopt_dir / "soul.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(soul, f, allow_unicode=True, sort_keys=False)

    print(f"[voice_preview] душа усыновлена: {ref_wav} + {ref_txt} + soul.yaml")
    print("[voice_preview] следующие шаги (отчёт Архитектору v2, раздел 4):")
    print(f"  1. config.yaml: профиль {args.persona}-soul "
          f"{{backend: qwen3, reference: {ref_wav.as_posix()}, "
          f"extra.ref_text: {ref_txt.as_posix()}}}")
    print(f"  2. personas/{args.persona}/voice.yaml: pack: {args.persona}-soul, "
          f"reference_wav: {ref_wav.as_posix()}")
    print("  3. tts_enabled: true — и mock-«прык» официально мёртв.")

    if args.verify_clone:
        run_verify_clone(args, ref_wav, ref_txt)


def run_verify_clone(args: argparse.Namespace, ref_wav: Path, ref_txt: Path) -> None:
    """Контрольный выстрел: клон души на резидентной 0.6B-Base.

    Зачем: в проде говорит Base-клон, а не VoiceDesign. Если тембр при клоне
    рассыпается — лучше узнать это здесь, чем в бою.
    """
    import soundfile as sf

    _check_gpu(min_free_gb=4.0)
    model = _load_model(args.clone_model, args.backend)
    ref_text = ref_txt.read_text(encoding="utf-8").strip()

    print(f"[voice_preview] клонирую {ref_wav.name} на {args.clone_model}...")
    audios, sr = model.generate_voice_clone(
        text=VERIFY_TEXT, language=args.language,
        ref_audio=str(ref_wav), ref_text=ref_text,
    )
    audio = audios[0] if isinstance(audios, (list, tuple)) else audios
    out = ref_wav.parent / "adopt_check.wav"
    sf.write(out, audio, sr)
    print(f"[voice_preview] слушай и сравнивай: {out.resolve()} vs {ref_wav.resolve()}")
    print("[voice_preview] если тембр совпал — душа переживёт прод. Если нет — "
          "пробуй xvec_only: false->true в профиле или возьми другой seed.")


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.adopt is not None:
        run_adopt(args)
    else:
        run_preview(args)


if __name__ == "__main__":
    main()
