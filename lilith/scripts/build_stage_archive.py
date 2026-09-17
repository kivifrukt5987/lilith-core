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
}
#: из models/ в архив едет только манифест, веса остаются на диске
MODELS_KEEP = {"packs.yaml"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".zip", ".db", ".sqlite3", ".log"}
EXCLUDE_NAMES = {".env"}


def build_archive(stage: int, out_dir: Path, day: str | None = None) -> Path:
    """Собирает архив проекта и возвращает путь к нему."""
    out_dir.mkdir(parents=True, exist_ok=True)
    day = day or date.today().isoformat()
    out_path = out_dir / f"LILITH-CORE_stage{stage}_{day}.zip"

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
    parser.add_argument("--out", default=None, help="каталог для архива (по умолчанию artifacts/)")
    args = parser.parse_args()

    out_dir = Path(args.out) if args.out else PROJECT_ROOT / "artifacts"
    build_archive(args.stage, out_dir, args.date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
