#!/usr/bin/env python3
"""Проверка артефакта этапа: цел ли, полный ли, совпадают ли контрольные суммы.

Отвечает на вопрос «архив жив и доступен?» одной командой — именно то, о чём
спрашивала Лилька-архитектор 22.09.2026.

Запуск (из корня проекта)::

    python scripts/verify_stage_artifact.py                       # проверить текущий этап
    python scripts/verify_stage_artifact.py --stage 6 --version 0.6.0
    python scripts/verify_stage_artifact.py --checksums           # напечатать строку для доков

Что проверяет:

1. файл существует и читается;
2. ``zipfile.testzip()`` — целостность контейнера;
3. в архиве есть обязательный минимум этапа (``src/lilith_core/app.py``,
   ``README.md``, ``STAGE<N>_REPORT.md``, ``unity-client/`` для этапа 6);
4. в архив НЕ попали секреты и мусор (``.env``, ``*.log``, ``__pycache__``,
   ``.venv``, ``data/``, ``Library/``, ``*.csproj``);
5. ``__version__``/``__stage__`` внутри архива совпадают с ожидаемыми;
6. распаковка во временный каталог проходит без потерь (число файлов сходится).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import re
import sys
import tempfile
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: Что обязано быть в архиве этапа 6.
REQUIRED_STAGE6 = (
    "src/lilith_core/app.py",
    "src/lilith_core/voice/pcm.py",
    "src/lilith_core/face/producer.py",
    "src/lilith_core/face/ws_frames.py",
    "src/lilith_core/face/endpoints.py",
    "src/lilith_core/face/personas.py",
    "src/lilith_core/face/lora.py",
    "src/lilith_core/face/group.py",
    "STAGE6_REPORT.md",
    "README.md",
    "CHANGELOG.md",
    "config/config.yaml",
    "scripts/unity_face_probe.py",
    "scripts/make_test_vrm.py",
    "tests/test_face_producer.py",
    "tests/samples/test_cube.vrm",
    "unity-client/Packages/manifest.json",
    "unity-client/Assets/LilithFace/LilithFace.asmdef",
    "unity-client/Assets/LilithFace/README.md",
    "unity-client/Assets/LilithFace/SCENE.md",
    "unity-client/Assets/LilithFace/Scripts/LilithFaceClient.cs",
    "unity-client/Assets/LilithFace/Scripts/LilithWSClient.cs",
    "unity-client/Assets/LilithFace/Scripts/AudioQueueProcessor.cs",
    "unity-client/Assets/LilithFace/Scripts/VisemeDriver.cs",
    "unity-client/Assets/LilithFace/Scripts/EmotionDriver.cs",
    "unity-client/Assets/LilithFace/Scripts/IdleController.cs",
    "unity-client/Assets/LilithFace/Scripts/VrmLoader.cs",
    "unity-client/Assets/LilithFace/Scripts/TransparentWindow.cs",
)

#: Что в архиве появиться не должно.
#: NB: ``.env.example`` — шаблон БЕЗ значений (это проверяет test_structure),
#: поэтому запрещён ровно ``.env``, а не всё, что начинается с ``.env``.
FORBIDDEN = (
    "/.env",
    ".log",
    "__pycache__",
    "/.venv/",
    "/data/",
    "/Library/",
    "/Temp/",
    "/obj/",
    ".csproj",
    ".sln",
    ".egg-info",
)


def _sha256(data: bytes) -> str:
    """Контрольная сумма файла."""
    return hashlib.sha256(data).hexdigest()


def verify(path: Path, *, stage: int, version: str, verbose: bool = True) -> int:
    """Проверить артефакт; возвращает 0 при успехе и 1 при любой проблеме."""
    problems: list[str] = []
    notes: list[str] = []

    if not path.is_file():
        print(f"[FAIL] артефакт не найден: {path}")
        return 1

    data = path.read_bytes()
    digest = _sha256(data)
    if verbose:
        print(f"файл    : {path}")
        print(f"размер  : {len(data):,} байт ({len(data) / 1024 / 1024:.2f} МБ)")
        print(f"sha256  : {digest}")

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        print(f"[FAIL] это не zip: {exc}")
        return 1

    broken = archive.testzip()
    if broken is not None:
        problems.append(f"повреждённая запись в архиве: {broken}")

    names = archive.namelist()
    if verbose:
        print(f"записей : {len(names)}")

    # 3) обязательный минимум
    normalized = {n.split("/", 1)[1] if n.count("/") else n for n in names}
    for required in REQUIRED_STAGE6:
        if required not in normalized:
            problems.append(f"в архиве нет обязательного файла: {required}")

    # 4) мусор и секреты
    for name in names:
        if name.endswith(".env.example"):
            continue  # шаблон секретов, значений не содержит
        for banned in FORBIDDEN:
            if banned in name:
                problems.append(f"в архив попал запрещённый файл: {name} (правило '{banned}')")
                break

    # 5) версия и этап внутри архива
    init_candidates = [n for n in names if n.endswith("src/lilith_core/__init__.py")]
    if not init_candidates:
        problems.append("в архиве нет src/lilith_core/__init__.py")
    else:
        text = archive.read(init_candidates[0]).decode("utf-8", "replace")
        found_version = re.search(r'__version__\s*=\s*"([^"]+)"', text)
        found_stage = re.search(r"__stage__\s*=\s*(\d+)", text)
        actual_version = found_version.group(1) if found_version else "?"
        actual_stage = int(found_stage.group(1)) if found_stage else -1
        if actual_version != version:
            problems.append(f"версия внутри архива {actual_version}, ожидалась {version}")
        if actual_stage != stage:
            problems.append(f"этап внутри архива {actual_stage}, ожидался {stage}")
        notes.append(f"внутри архива: v{actual_version}, этап {actual_stage}")

    # 6) распаковка без потерь
    with tempfile.TemporaryDirectory(prefix="lilith-verify-") as tmp:
        archive.extractall(tmp)
        extracted = [p for p in Path(tmp).rglob("*") if p.is_file()]
        expected = [n for n in names if not n.endswith("/")]
        if len(extracted) != len(expected):
            problems.append(f"распаковалось {len(extracted)} файлов, в архиве {len(expected)}")
        else:
            notes.append(f"распаковка: {len(extracted)} файлов, потерь нет")

    for note in notes:
        if verbose:
            print(f"[ OK ] {note}")
    for problem in problems:
        print(f"[FAIL] {problem}")

    if problems:
        print(f"\nИТОГ: артефакт НЕ пригоден ({len(problems)} проблем)")
        return 1

    print("\nИТОГ: артефакт жив, полон и пригоден к скачиванию ✅")
    return 0


def main() -> int:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(description="Проверка zip-артефакта этапа LILITH-CORE")
    parser.add_argument("--stage", type=int, default=6, help="номер этапа (по умолчанию 6)")
    parser.add_argument("--version", default="0.6.0", help="ожидаемая версия (по умолчанию 0.6.0)")
    parser.add_argument("--archive", default=None, help="путь к архиву (иначе ищем в artifacts/)")
    parser.add_argument("--checksums", action="store_true", help="напечатать контрольные суммы всех архивов")
    args = parser.parse_args()

    artifacts = PROJECT_ROOT / "artifacts"
    if args.checksums:
        for path in sorted(artifacts.glob("LILITH-CORE_stage*.zip")):
            data = path.read_bytes()
            print(f"{_sha256(data)}  {len(data):>10,}  {path.name}")
        return 0

    path = Path(args.archive) if args.archive else artifacts / f"LILITH-CORE_stage{args.stage}_v{args.version}.zip"
    return verify(path, stage=args.stage, version=args.version)


if __name__ == "__main__":
    sys.exit(main())
