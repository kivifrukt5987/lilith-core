"""Скопировать архив этапа в корневой artifacts/ и проверить его распаковкой + прогоном тестов."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
SRC_DIR = WORKSPACE / "Локальная Лилит" / "artifacts"
DST_DIR = WORKSPACE / "artifacts"
DST_DIR.mkdir(exist_ok=True)

zips = sorted(SRC_DIR.glob("LILITH-CORE_stage*_*.zip"))
if not zips:
    raise SystemExit("в artifacts/ проекта нет ни одного zip-архива")
SRC_ZIP = zips[-1]
DST_ZIP = DST_DIR / SRC_ZIP.name

shutil.copy2(SRC_ZIP, DST_ZIP)
print("скопировано:", DST_ZIP, f"({DST_ZIP.stat().st_size / 1024:.1f} КБ)")

tmp = Path(tempfile.mkdtemp(prefix="lilith-verify-"))
with zipfile.ZipFile(DST_ZIP) as zf:
    names = zf.namelist()
    zf.extractall(tmp)
print("файлов в архиве:", len(names))

project = tmp / "Локальная Лилит"
print("корень проекта найден:", project.is_dir())

result = subprocess.run(
    [sys.executable, "-m", "pytest", "-q", "--no-header"],
    cwd=project,
    env={**dict(__import__("os").environ), "PYTHONPATH": str(project / "src")},
    capture_output=True,
    text=True,
    timeout=600,
)
tail = [line for line in result.stdout.strip().splitlines() if line.strip()][-1:]
print("pytest из распакованного архива:", tail[0] if tail else result.stdout[-500:])
print("код возврата:", result.returncode)

shutil.rmtree(tmp, ignore_errors=True)
