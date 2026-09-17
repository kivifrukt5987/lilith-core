"""Служебный скрипт: синхронизирует число тестов в документации с реальным.

Запуск:  python3 scripts/sync_test_count.py   (из корня workspace)

Пути определяются от расположения самого скрипта — никаких хардкодных
абсолютных путей, чтобы скрипт работал и в песочнице, и на машине Кирюши.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
PROJECT = WORKSPACE / "Локальная Лилит"

DOCS = [
    PROJECT / "README.md",
    PROJECT / "CHANGELOG.md",
    PROJECT / "STAGE1_REPORT.md",
    PROJECT / "artifacts" / "README.md",
    WORKSPACE / "lilith" / "PLAN.md",
]


def run_tests() -> int:
    """Прогоняет pytest и возвращает число пройденных тестов."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header"],
        cwd=PROJECT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    lines = [line for line in (result.stdout or "").strip().splitlines() if line.strip()]
    tail = lines[-1] if lines else ""
    match = re.search(r"(\d+) passed", tail)
    if not match:
        raise SystemExit(f"не удалось разобрать вывод pytest:\n{(result.stdout or '')[-2000:]}")
    if result.returncode != 0:
        raise SystemExit(f"тесты не зелёные (код {result.returncode}):\n{(result.stdout or '')[-2000:]}")
    return int(match.group(1))


def main() -> int:
    """Обновляет все упоминания количества тестов в документации."""
    if not PROJECT.is_dir():
        raise SystemExit(f"каталог проекта не найден: {PROJECT}")

    count = run_tests()
    print(f"pytest: {count} passed")

    pattern = re.compile(r"\b\d{2,4}\b(?=\s*(?:passed|тестов|, все зелёные))")
    for doc in DOCS:
        if not doc.is_file():
            print("  (нет файла)", doc.name)
            continue
        text = doc.read_text(encoding="utf-8")
        updated = pattern.sub(str(count), text)
        if updated != text:
            doc.write_text(updated, encoding="utf-8")
            print("  обновлён:", doc.name)
        else:
            print("  без изменений:", doc.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
