"""Сборка zip-архива этапа LILITH-CORE.

Использование (из корня проекта)::

    python scripts/build_stage_archive.py --stage 1
    python scripts/build_stage_archive.py --stage 2 --date 2026-09-20 --out ../artifacts

Архив кладётся в ``artifacts/`` и содержит весь проект без кэшей, логов,
виртуального окружения, данных и секретов (``.env``).
"""

from __future__ import annotations

import argparse
import os
import zipfile
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

EXCLUDE_DIRS = {
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".venv", "venv",
    "node_modules", ".git", "data", "artifacts", "dist", "build",
    # Unity (unity-client/, этап 6): Кирюша собирает проект у себя, в архив едут
    # только Assets/, Packages/manifest.json и доки — без гигабайтов кэша.
    "Library", "Temp", "Logs", "UserSettings", "obj", ".vs", ".idea", "Recordings",
    # артефакты editable-установки: создаются pip'ом, в поставке не нужны
    "lilith_core.egg-info", ".ipynb_checkpoints",
}
#: из models/ в архив едет только манифест, веса остаются на диске
MODELS_KEEP = {"packs.yaml"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".zip", ".db", ".sqlite3", ".log", ".csproj", ".sln"}
EXCLUDE_NAMES = {".env"}


def build_archive(stage: int, out_dir: Path, day: str | None = None, version: str | None = None) -> Path:
    """Собирает архив проекта и возвращает путь к нему.

    :param stage: номер этапа.
    :param out_dir: каталог назначения.
    :param day: дата в имени файла (по умолчанию сегодня).
    :param version: если задан, имя файла ``LILITH-CORE_stage<N>_v<X>.zip``
        (соглашение этапов 2+), иначе ``..._stage<N>_<дата>.zip``.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    day = day or date.today().isoformat()
    name = f"LILITH-CORE_stage{stage}_v{version}.zip" if version else f"LILITH-CORE_stage{stage}_{day}.zip"
    out_path = out_dir / name

    count = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for root, dirs, files in os.walk(PROJECT_ROOT):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for name in sorted(files):
                if name in EXCLUDE_NAMES or Path(name).suffix in EXCLUDE_SUFFIX:
                    continue
                full = Path(root) / name
                rel_root = full.relative_to(PROJECT_ROOT)
                if rel_root.parts and rel_root.parts[0] == "models" and rel_root.name not in MODELS_KEEP:
                    continue
                # верхний уровень архива = имя каталога проекта
                rel = full.relative_to(PROJECT_ROOT.parent)
                zf.write(full, rel.as_posix())
                count += 1

    size_kb = out_path.stat().st_size / 1024
    print(f"[OK] {out_path}")
    print(f"     файлов: {count}, размер: {size_kb:.1f} КБ")
    return out_path


def main() -> int:
    """Точка входа скрипта."""
    parser = argparse.ArgumentParser(description="Сборка архива этапа LILITH-CORE")
    parser.add_argument("--stage", type=int, required=True, help="номер этапа")
    parser.add_argument("--date", default=None, help="дата в имени файла (YYYY-MM-DD)")
    parser.add_argument("--version", default=None, help="версия в имени файла, напр. 0.6.0")
    parser.add_argument("--out", default=None, help="каталог для архива (по умолчанию artifacts/)")
    args = parser.parse_args()

    out_dir = Path(args.out) if args.out else PROJECT_ROOT / "artifacts"
    build_archive(args.stage, out_dir, args.date, args.version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
