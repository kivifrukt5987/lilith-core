"""Bake-off голосовых движков (ревизия ADR-027, решение Q-B от 28.09.2026).

Назначение: один RU-текст (4 фразы: нейтральная, эмоциональная, скороговорка,
интимная — со стресс-ловушками вроде «Кирюша») × один JP-референс (LilithGolos.wav,
~8–9 с, чистый) → wav'ы от каждого кандидата → СЛЕПОЕ прослушивание Архитектором.
Спор «CosyVoice vs Fish vs RVC» снимается ушами, не аргументами.

Кандидаты (порядок из директивы Q-B):
  cosyvoice3  Fun-CosyVoice3-0.5B-2512 (Apache-2.0; RU в 9 языках; bi-streaming 150мс;
              conda py3.10 — запускать ВНУТРИ их окружения, скрипт standalone)
  fish        Fish Audio S2 (fish-speech main; CLI-пайплайн dac→text2semantic→dac).
              ВНИМАНИЕ: их дока рекомендует ≥24GB VRAM для S2-Pro (4B) — на 3060 12GB
              запускать с --fish-half и готовностью к OOM; если не встанет — кандидат
              от Fish в bake-off'е отсутствует, фиксируем skip в манифесте (S1-mini —
              отдельный релизный тег fish-speech, адаптер настраивается --fish-* опциями).
  xtts        Coqui XTTS v2 (референс «как у Нейроны», NEURONA_NOTES §3.4;
              pip install coqui-tts; лицензия Coqui Public Model License — некоммерческая)
  qwen3       Qwen3-TTS 0.6B-Base (КОНТРОЛЬ: RU-просодия признана неподходящей для
              короны — полевой вердикт 28.09; в bake-off'е — нижняя планка и
              будущий генератор JP-датасета)

Запуск (из lilith/, LLM/Unity/браузер выключены):
    # основное окружение (fish/xtts/qwen3 — кто установлен):
    python scripts/voice_bakeoff.py --engine all --run-dir voices/bakeoff/run1
    # окружение conda cosyvoice (тот же run-dir — манифест дополняется):
    conda activate cosyvoice && python <путь>/scripts/voice_bakeoff.py \
        --engine cosyvoice3 --run-dir voices/bakeoff/run1 \
        --cosyvoice-repo <путь>/CosyVoice --cosyvoice-model pretrained_models/Fun-CosyVoice3-0.5B
    # слепая раскладка (после всех прогонов):
    python scripts/voice_bakeoff.py --blind-only --run-dir voices/bakeoff/run1

Референс: --ref voices/bakeoff/LilithGolos.wav; JP-транскрипция — соседний
LilithGolos.txt (нужна для ICL-клона qwen3/cosyvoice3/fish; xtts работает без неё).
Нет транскрипции → qwen3 деградирует в xvec_only (пометим в манифесте),
cosyvoice3/fish — честный skip с причиной.

Результаты в <run-dir>: wav'ы `<engine>_<phrase>.wav`, manifest.yaml (статусы,
тайминги, RTF, VRAM-пик где измерим), при --blind — blind/B##.wav + blind_key.yaml
(ключ НЕ показывать слушателю до проставления оценок).

build_parser() вынесен для гвардов (tests/test_voice_bakeoff.py); тяжёлые импорты
ленивые — скрипт импортируется и тестируется без GPU и движков.
"""

from __future__ import annotations

import argparse
import random
import shutil
import subprocess
import sys
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Фразы (стресс-ловушки просодии — по жалобам полевого вердикта Q-A)          #
# --------------------------------------------------------------------------- #
DEFAULT_PHRASES: list[tuple[str, str]] = [
    ("neutral",
     "Привет, Кирюша. Кофе остыл, суббота на дворе, а у нас с тобой опять планы "
     "на весь вечер."),
    ("emotional",
     "Да ладно?! Оно заработало — само, с первого раза! Ну всё, я сегодня гений, "
     "не спорь, я это заслужила."),
    ("tongue_twister",
     "Шла Саша по шоссе и сосала сушку; съел молодец тридцать три пирога "
     "с пирогами."),
    ("intimate",
     "Три часа ночи, а ты всё ещё не спишь? Так, марш в кровать. Ну ладно, "
     "пять минут — и выключаешь."),
]

#: Ключ blind-раскладки по умолчанию (воспроизводимо: тот же seed — тот же порядок).
DEFAULT_BLIND_SEED = 20260928


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="voice_bakeoff",
        description="Bake-off RU-короны: один текст × один JP-референс × кандидаты "
                    "(cosyvoice3/fish/xtts/qwen3) → wav + manifest + слепое прослушивание.",
    )
    p.add_argument("--engine", default="all",
                   choices=["all", "cosyvoice3", "fish", "xtts", "qwen3"],
                   help="кого печём (all = всех установленных, остальные — честный skip)")
    p.add_argument("--ref", default="voices/bakeoff/LilithGolos.wav",
                   help="JP-референс (8–9 с, чистый)")
    p.add_argument("--ref-text", default=None,
                   help="JP-транскрипция референса: путь к .txt или строка; "
                        "дефолт — <ref>.txt рядом с wav")
    p.add_argument("--text-file", default=None,
                   help="свои фразы: по одной на строку, формат 'id|текст' или просто текст")
    p.add_argument("--run-dir", default=None,
                   help="каталог прогона (существующий — дополнить; дефолт — "
                        "voices/bakeoff/bake_<штамп>)")
    p.add_argument("--outdir-root", default="voices/bakeoff",
                   help="корень для run-dir по умолчанию")
    p.add_argument("--blind", action="store_true",
                   help="после выпечки сделать слепую раскладку blind/B##.wav + blind_key.yaml")
    p.add_argument("--blind-only", action="store_true",
                   help="не печь, только собрать blind-раскладку из готового run-dir")
    p.add_argument("--blind-seed", type=int, default=DEFAULT_BLIND_SEED,
                   help="seed перемешивания (записывается в ключ)")
    # --- настройки кандидатов ------------------------------------------------ #
    p.add_argument("--cosyvoice-repo", default=None,
                   help="корень чекаута CosyVoice (или env COSYVOICE_REPO); нужен для "
                        "sys.path third_party/Matcha-TTS")
    p.add_argument("--cosyvoice-model", default="pretrained_models/Fun-CosyVoice3-0.5B",
                   help="model_dir для AutoModel (относительно их чекаута или абсолютный)")
    p.add_argument("--fish-repo", default=None,
                   help="корень чекаута fish-speech (или env FISH_REPO)")
    p.add_argument("--fish-checkpoints", default="checkpoints/s2-pro",
                   help="каталог весов S2 внутри fish-repo")
    p.add_argument("--fish-half", action="store_true",
                   help="--half для text2semantic (GPU без bf16 / мало VRAM)")
    p.add_argument("--xtts-model", default="tts_models/multilingual/multi-dataset/xtts_v2")
    p.add_argument("--qwen3-model", default="Qwen/Qwen3-TTS-12Hz-0.6B-Base")
    return p


# --------------------------------------------------------------------------- #
# Контекст прогона                                                            #
# --------------------------------------------------------------------------- #
@dataclass
class BakeContext:
    args: argparse.Namespace
    ref: Path
    ref_text: str | None
    ref_text_source: str
    phrases: list[tuple[str, str]]
    run_dir: Path
    files: list[dict[str, Any]] = field(default_factory=list)
    engines: dict[str, dict[str, Any]] = field(default_factory=dict)


def parse_text_file(path: str | Path) -> list[tuple[str, str]]:
    """Свои фразы: 'id|текст' или просто текст (id = line<N>)."""
    out: list[tuple[str, str]] = []
    for i, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            pid, text = line.split("|", 1)
            out.append((pid.strip(), text.strip()))
        else:
            out.append((f"line{i}", line))
    return out


def resolve_ref_text(args: argparse.Namespace, ref: Path) -> tuple[str | None, str]:
    """JP-транскрипция референса: CLI-строка > CLI-файл > соседний <ref>.txt."""
    spec = args.ref_text
    if spec:
        candidate = Path(spec)
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip(), f"file:{candidate}"
        return spec.strip(), "inline"
    sibling = ref.with_suffix(".txt")
    if sibling.is_file():
        return sibling.read_text(encoding="utf-8").strip(), f"file:{sibling}"
    return None, "missing"


def wav_duration(path: Path) -> tuple[float | None, int | None]:
    """Длительность и частота wav (stdlib; float-форматы — best effort)."""
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1), w.getframerate()
    except (wave.Error, EOFError):
        return None, None


def _vram_peak_gb(reset: bool = False) -> float | None:
    """Пик VRAM текущего процесса (GB), если torch+CUDA доступны; иначе None."""
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        if reset:
            torch.cuda.reset_peak_memory_stats()
            return None
        return round(torch.cuda.max_memory_allocated() / 2**30, 2)
    except Exception:  # noqa: BLE001 — телеметрия не должна ронять выпечку
        return None


def _package_version(dist: str) -> str | None:
    try:
        import importlib.metadata

        return importlib.metadata.version(dist)
    except Exception:  # noqa: BLE001
        return None


def _log(msg: str) -> None:
    print(f"[bakeoff] {msg}", flush=True)


# --------------------------------------------------------------------------- #
# Адаптеры кандидатов. Контракт: (ctx) -> None; пишут wav'ы в ctx.run_dir,     #
#append'ят ctx.files; ImportError/прочее → внешний раннер пишет skip/fail.     #
# --------------------------------------------------------------------------- #
def bake_qwen3(ctx: BakeContext) -> None:
    """Контроль (RU-просодия забракована полем — нижняя планка bake-off'а)."""
    import soundfile as sf
    from faster_qwen3_tts import FasterQwen3TTS

    note = f"model={ctx.args.qwen3_model}"
    if not ctx.ref_text:
        note += "; ref_text нет → xvec_only (клон бледнее)"
    _vram_peak_gb(reset=True)
    _log(f"qwen3: загружаю {ctx.args.qwen3_model}...")
    model = FasterQwen3TTS.from_pretrained(ctx.args.qwen3_model)
    model.warmup(prefill_len=100)
    for pid, text in ctx.phrases:
        kwargs: dict[str, Any] = {"language": "Russian", "ref_audio": str(ctx.ref)}
        if ctx.ref_text:
            kwargs["ref_text"] = ctx.ref_text
        else:
            kwargs["xvec_only"] = True
        t0 = time.perf_counter()
        audios, sr = model.generate_voice_clone(text=text, **kwargs)
        gen = time.perf_counter() - t0
        audio = audios[0] if isinstance(audios, (list, tuple)) else audios
        out = ctx.run_dir / f"qwen3_{pid}.wav"
        sf.write(out, audio, sr)
        dur = len(audio) / sr
        ctx.files.append({"engine": "qwen3", "phrase_id": pid, "file": out.name,
                          "sample_rate": sr, "audio_sec": round(dur, 2),
                          "gen_sec": round(gen, 2), "rtf": round(dur / gen, 2) if gen else None})
        _log(f"qwen3/{pid}: {dur:.1f}с аудио за {gen:.1f}с (RTF {dur / gen:.2f})")
    ctx.engines["qwen3"] = {"status": "ok", "note": note,
                            "pkg": _package_version("faster-qwen3-tts"),
                            "vram_peak_gb": _vram_peak_gb()}


def bake_xtts(ctx: BakeContext) -> None:
    """Референс «как у Нейроны» (NEURONA_NOTES §3.4): Coqui XTTS v2."""
    from TTS.api import Tts

    device = "cpu"
    try:
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        pass
    _vram_peak_gb(reset=True)
    _log(f"xtts: загружаю {ctx.args.xtts_model} на {device}...")
    tts = Tts(ctx.args.xtts_model).to(device)
    for pid, text in ctx.phrases:
        out = ctx.run_dir / f"xtts_{pid}.wav"
        t0 = time.perf_counter()
        tts.tts_to_file(text=text, speaker_wav=str(ctx.ref), language="ru",
                        file_path=str(out))
        gen = time.perf_counter() - t0
        dur, sr = wav_duration(out)
        ctx.files.append({"engine": "xtts", "phrase_id": pid, "file": out.name,
                          "sample_rate": sr, "audio_sec": round(dur, 2) if dur else None,
                          "gen_sec": round(gen, 2),
                          "rtf": round(dur / gen, 2) if dur and gen else None})
        _log(f"xtts/{pid}: {gen:.1f}с генерация")
    ctx.engines["xtts"] = {"status": "ok", "note": f"model={ctx.args.xtts_model}, device={device}",
                           "pkg": _package_version("coqui-tts") or _package_version("TTS"),
                           "vram_peak_gb": _vram_peak_gb()}


def bake_cosyvoice3(ctx: BakeContext) -> None:
    """CosyVoice 3: запускать ВНУТРИ их conda-окружения (py3.10). API — example.py их репо."""
    import os

    if not ctx.ref_text:
        raise RuntimeError("cosyvoice3: нужен --ref-text (JP-транскрипция) — "
                           "inference_zero_shot без prompt_text не клонирует")
    repo = Path(ctx.args.cosyvoice_repo or os.environ.get("COSYVOICE_REPO") or ".").resolve()
    matcha = repo / "third_party" / "Matcha-TTS"
    if matcha.is_dir() and str(matcha) not in sys.path:
        sys.path.append(str(matcha))
    import torch  # noqa: PLC0415
    import torchaudio  # noqa: PLC0415
    from cosyvoice.cli.cosyvoice import AutoModel  # noqa: PLC0415 — их окружение

    _vram_peak_gb(reset=True)
    _log(f"cosyvoice3: AutoModel({ctx.args.cosyvoice_model})...")
    model = AutoModel(model_dir=ctx.args.cosyvoice_model)
    prompt = "You are a helpful assistant.<|endofprompt|>" + ctx.ref_text
    sr = getattr(model, "sample_rate", 24000)
    for pid, text in ctx.phrases:
        out = ctx.run_dir / f"cosyvoice3_{pid}.wav"
        t0 = time.perf_counter()
        parts = [j["tts_speech"] for j in model.inference_zero_shot(
            text, prompt, str(ctx.ref), stream=False)]
        gen = time.perf_counter() - t0
        wav_tensor = torch.cat(parts, dim=-1) if len(parts) > 1 else parts[0]
        torchaudio.save(str(out), wav_tensor, sr)
        dur = wav_tensor.shape[-1] / sr
        ctx.files.append({"engine": "cosyvoice3", "phrase_id": pid, "file": out.name,
                          "sample_rate": sr, "audio_sec": round(dur, 2),
                          "gen_sec": round(gen, 2), "rtf": round(dur / gen, 2) if gen else None})
        _log(f"cosyvoice3/{pid}: {dur:.1f}с аудио за {gen:.1f}с (RTF {dur / gen:.2f})")
    ctx.engines["cosyvoice3"] = {"status": "ok",
                                 "note": f"model_dir={ctx.args.cosyvoice_model}, repo={repo}",
                                 "vram_peak_gb": _vram_peak_gb()}


def bake_fish(ctx: BakeContext) -> None:
    """Fish Audio S2: документированный CLI-пайплайн (dac → text2semantic → dac).

    ВНИМАНИЕ: их дока рекомендует ≥24GB VRAM для S2-Pro (4B). На 12GB — только
    с --fish-half и без гарантий; OOM/падение → статус failed в манифесте, bake-off
    продолжается остальными. Скрипты зовём subprocess'ом из их чекаута (PYTHONPATH=repo),
    выходные файлы — в нашем run-dir.
    """
    import os

    if not ctx.ref_text:
        raise RuntimeError("fish: нужен --ref-text (JP-транскрипция) для --prompt-text")
    repo = Path(ctx.args.fish_repo or os.environ.get("FISH_REPO") or ".").resolve()
    dac = repo / "fish_speech" / "models" / "dac" / "inference.py"
    t2s = repo / "fish_speech" / "models" / "text2semantic" / "inference.py"
    codec_ckpt = repo / ctx.args.fish_checkpoints / "codec.pth"
    for required, hint in ((dac, "dac/inference.py"), (t2s, "text2semantic/inference.py")):
        if not required.is_file():
            raise RuntimeError(f"fish: в чекауте {repo} нет {hint} — поставь fish-speech "
                               "и веса (hf download fishaudio/s2-pro --local-dir "
                               f"{ctx.args.fish_checkpoints}); дока: ≥24GB VRAM!")
    env = {**os.environ, "PYTHONPATH": str(repo)}
    work = ctx.run_dir / "_fish_work"
    work.mkdir(exist_ok=True)

    def run_cli(cmd: list[str], cwd: Path, what: str) -> None:
        _log(f"fish: {what}...")
        proc = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip()[-500:]
            raise RuntimeError(f"fish: {what} → код {proc.returncode}: {tail}")

    # шаг 1: VQ-токены референса (один раз на прогон)
    t0 = time.perf_counter()
    run_cli([sys.executable, str(dac), "-i", str(ctx.ref.resolve()),
             "--checkpoint-path", str(codec_ckpt)], work, "кодек-токены референса")
    ref_tokens = work / "fake.npy"
    if not ref_tokens.is_file():
        raise RuntimeError(f"fish: {ref_tokens} не появился после шага 1")
    ref_prep = time.perf_counter() - t0

    for pid, text in ctx.phrases:
        phrase_dir = work / f"p_{pid}"
        phrase_dir.mkdir(exist_ok=True)
        shutil.copyfile(ref_tokens, phrase_dir / "prompt.npy")
        t2s_cmd = [sys.executable, str(t2s), "--text", text,
                   "--prompt-text", ctx.ref_text, "--prompt-tokens", "prompt.npy"]
        if ctx.args.fish_half:
            t2s_cmd.append("--half")
        t0 = time.perf_counter()
        run_cli(t2s_cmd, phrase_dir, f"text2semantic/{pid}")
        codes = phrase_dir / "codes_0.npy"
        if not codes.is_file():
            raise RuntimeError(f"fish: codes_0.npy не появился для {pid}")
        run_cli([sys.executable, str(dac), "-i", "codes_0.npy",
                 "--checkpoint-path", str(codec_ckpt)], phrase_dir, f"вокодер/{pid}")
        gen = time.perf_counter() - t0
        baked = phrase_dir / "fake.wav"
        if not baked.is_file():
            raise RuntimeError(f"fish: fake.wav не появился для {pid}")
        out = ctx.run_dir / f"fish_{pid}.wav"
        shutil.move(str(baked), str(out))
        dur, sr = wav_duration(out)
        ctx.files.append({"engine": "fish", "phrase_id": pid, "file": out.name,
                          "sample_rate": sr, "audio_sec": round(dur, 2) if dur else None,
                          "gen_sec": round(gen, 2),
                          "rtf": round(dur / gen, 2) if dur and gen else None})
        _log(f"fish/{pid}: готово за {gen:.1f}с")
    ctx.engines["fish"] = {"status": "ok",
                           "note": f"repo={repo}, ckpt={ctx.args.fish_checkpoints}, "
                                   f"half={bool(ctx.args.fish_half)}, ref_prep_sec={round(ref_prep, 1)}",
                           "vram_peak_gb": _vram_peak_gb()}


ENGINE_ADAPTERS: dict[str, Callable[[BakeContext], None]] = {
    "cosyvoice3": bake_cosyvoice3,
    "fish": bake_fish,
    "xtts": bake_xtts,
    "qwen3": bake_qwen3,
}
ENGINE_ORDER = ["cosyvoice3", "fish", "xtts", "qwen3"]


# --------------------------------------------------------------------------- #
# Манифест, blind-раскладка, раннер                                           #
# --------------------------------------------------------------------------- #
def load_manifest(run_dir: Path) -> dict[str, Any]:
    import yaml

    manifest_path = run_dir / "manifest.yaml"
    if manifest_path.is_file():
        return yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    return {}


def save_manifest(ctx: BakeContext) -> None:
    import yaml

    existing = load_manifest(ctx.run_dir)
    engines = {**(existing.get("engines") or {}), **ctx.engines}
    known = {(f.get("engine"), f.get("phrase_id")) for f in ctx.files}
    files = [f for f in (existing.get("files") or [])
             if (f.get("engine"), f.get("phrase_id")) not in known] + ctx.files
    manifest = {
        "created": existing.get("created") or datetime.now().isoformat(timespec="seconds"),
        "updated": datetime.now().isoformat(timespec="seconds"),
        "ref": str(ctx.ref),
        "ref_text": ctx.ref_text,
        "ref_text_source": ctx.ref_text_source,
        "phrases": [{"id": pid, "text": text} for pid, text in ctx.phrases],
        "engines": engines,
        "files": files,
    }
    if existing.get("blind"):
        manifest["blind"] = existing["blind"]  # пересоберётся при --blind
    (ctx.run_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")


def make_blind(run_dir: Path, seed: int) -> dict[str, Any]:
    """Слепая раскладка: B##.wav вперемешку + blind_key.yaml (ключ — после оценок!)."""
    import yaml

    manifest = load_manifest(run_dir)
    files = [f for f in (manifest.get("files") or []) if (run_dir / f["file"]).is_file()]
    if not files:
        raise SystemExit("[bakeoff] в манифесте нет файлов — нечего ослеплять")
    rng = random.Random(seed)
    order = sorted(files, key=lambda f: (f["engine"], f["phrase_id"]))
    rng.shuffle(order)
    blind_dir = run_dir / "blind"
    if blind_dir.exists():
        shutil.rmtree(blind_dir)
    blind_dir.mkdir(parents=True)
    key: list[dict[str, str]] = []
    for i, entry in enumerate(order, 1):
        code = f"B{i:02d}"
        shutil.copyfile(run_dir / entry["file"], blind_dir / f"{code}.wav")
        key.append({"code": code, "engine": entry["engine"],
                    "phrase_id": entry["phrase_id"], "file": entry["file"]})
    info = {"seed": seed, "created": datetime.now().isoformat(timespec="seconds"),
            "count": len(key), "key": key}
    (run_dir / "blind_key.yaml").write_text(
        yaml.safe_dump(info, allow_unicode=True, sort_keys=False), encoding="utf-8")
    manifest["blind"] = {"enabled": True, "seed": seed, "count": len(key),
                         "dir": "blind", "key_file": "blind_key.yaml"}
    (run_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    _log(f"blind: {len(key)} файлов в {blind_dir} (ключ: blind_key.yaml — не открывать до оценок!)")
    return manifest["blind"]


def bake(ctx: BakeContext) -> None:
    wanted = ENGINE_ORDER if ctx.args.engine == "all" else [ctx.args.engine]
    for engine in wanted:
        adapter = ENGINE_ADAPTERS[engine]
        _log(f"=== {engine} ===")
        try:
            adapter(ctx)
        except ImportError as exc:
            ctx.engines[engine] = {"status": "skipped",
                                   "reason": f"не установлен в этом окружении: {exc}"}
            _log(f"{engine}: SKIP — {exc}")
        except Exception as exc:  # noqa: BLE001 — один сгоревший кандидат не срывает bake-off
            ctx.engines[engine] = {"status": "failed", "reason": str(exc)[:500]}
            _log(f"{engine}: FAILED — {exc}")
        save_manifest(ctx)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_dir = Path(args.run_dir) if args.run_dir else \
        Path(args.outdir_root) / f"bake_{datetime.now():%Y%m%d_%H%M%S}"

    if args.blind_only:
        if not run_dir.is_dir():
            raise SystemExit(f"[bakeoff] run-dir не найден: {run_dir}")
        make_blind(run_dir, args.blind_seed)
        return 0

    ref = Path(args.ref)
    if not ref.is_file():
        raise SystemExit(f"[bakeoff] референс не найден: {ref} "
                         "(ждём JP-запись ~8–9 с; положи рядом <имя>.txt с транскрипцией)")
    ref_text, ref_text_source = resolve_ref_text(args, ref)
    if ref_text is None:
        _log("ВНИМАНИЕ: JP-транскрипция референса не найдена — qwen3 уйдёт в xvec_only, "
             "cosyvoice3/fish будут пропущены (xtts работает без транскрипции)")
    phrases = parse_text_file(args.text_file) if args.text_file else list(DEFAULT_PHRASES)
    if not phrases:
        raise SystemExit("[bakeoff] пустой набор фраз")

    run_dir.mkdir(parents=True, exist_ok=True)
    ctx = BakeContext(args=args, ref=ref, ref_text=ref_text,
                      ref_text_source=ref_text_source, phrases=phrases, run_dir=run_dir)
    _log(f"run-dir: {run_dir.resolve()} · фраз: {len(phrases)} · референс: {ref}")
    bake(ctx)
    if args.blind:
        make_blind(run_dir, args.blind_seed)

    ok = [e for e, info in ctx.engines.items() if info.get("status") == "ok"]
    skipped = [e for e, info in ctx.engines.items() if info.get("status") != "ok"]
    _log(f"готово: испеклось {ok or 'ничего'}; skip/fail: {skipped or '—'}")
    _log(f"дальше: прослушать {run_dir} (или --blind), цифры — в manifest.yaml, "
         "вердикт — в ревизию ADR-027")
    return 0 if ok or not skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
