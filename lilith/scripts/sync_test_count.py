"""Служебный скрипт: синхронизирует **число тестов** в живой документации.

Запуск (из корня проекта — там, где лежит ``pyproject.toml``)::

    python scripts/sync_test_count.py                # прогнать pytest и вписать число
    python scripts/sync_test_count.py --count 764    # не гонять pytest, вписать число
    python scripts/sync_test_count.py --dry-run      # показать, что изменилось бы

Хирургия (урок 18.09.2026, держится тестами ``TestDocsHygiene``)
---------------------------------------------------------------
Скрипт правит **только маркерные строки в живых доках**:

* ``PLAN.md`` — строку ``ТЕСТЫ СЕЙЧАС: N passed …``;
* ``README.md`` — строку таблицы ``| Тесты | **N passed, …** … с tree-sitter — M |``.

Исторические документы не трогаются **никогда**: ``CHANGELOG.md``, отчёты этапов
``STAGEn_REPORT.md`` и ``RELEASE_*.md``. Числа прошлых этапов — это факты
(«477 passed» на этапе 1 был правдой и останется), а не устаревшие данные.
Раньше скрипт заменял все вхождения «N passed» во всех доках подряд и превратил
историю в кашу; список запрещённых файлов теперь проверяется и в рантайме.

Пути
----
Всё определяется от расположения самого скрипта (``parents[1]`` = корень проекта),
поэтому он работает одинаково в git-репозитории, в распакованном архиве этапа и в
гибридной папке Кирюши. ``PLAN.md`` — файл уровня workspace, поэтому ищется в
нескольких местах: ``lilith/PLAN.md``, ``память и личность/PLAN.md`` (так он лежит
в git-репо), ``PLAN.md`` рядом с проектом и в корне проекта. Не нашёлся — не ошибка:
скрипт сообщает об этом и обновляет то, что нашёл.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path

#: Корень проекта (``lilith/`` в git-репо, папка проекта в распакованном архиве).
PROJECT = Path(__file__).resolve().parents[1]

#: Уровень workspace: в git-репо это корень репозитория.
WORKSPACE = PROJECT.parent

#: Маркерная строка PLAN.md, которую скрипт имеет право править.
PLAN_MARKER = "ТЕСТЫ СЕЙЧАС:"

#: Документы, которые скрипт не трогает НИКОГДА (урок 18.09.2026).
FORBIDDEN_DOCS = ("CHANGELOG.md", "STAGE*_REPORT.md", "RELEASE_*.md")

#: Сколько тестов добавляет установленный tree-sitter (чеки C#-синтаксиса):
#: без него они скипаются, поэтому «с tree-sitter» число всегда на это больше.
TREE_SITTER_TESTS = 2

#: Где искать PLAN.md (порядок важен: первый найденный и правим).
PLAN_CANDIDATES = (
    WORKSPACE / "lilith" / "PLAN.md",
    WORKSPACE / "память и личность" / "PLAN.md",
    WORKSPACE / "PLAN.md",
    PROJECT / "PLAN.md",
)

#: Живая документация проекта.
README = PROJECT / "README.md"


def run_tests(project: Path | None = None) -> int:
    """Прогнать pytest и вернуть число passed (или умереть с внятным текстом)."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header"],
        cwd=PROJECT if project is None else project,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    tail = (result.stdout or "").strip().splitlines()
    last = tail[-1] if tail else ""
    match = re.search(r"(\d+) passed", last)
    if not match:
        raise SystemExit(f"не удалось разобрать вывод pytest:\n{(result.stdout or '')[-2000:]}")
    if result.returncode != 0:
        raise SystemExit(f"тесты не зелёные (код {result.returncode}): не синхронизирую\n{last}")
    return int(match.group(1))


def find_plan(candidates: tuple[Path, ...] | None = None) -> Path | None:
    """Первый существующий PLAN.md (или ``None``).

    ``candidates=None`` → берём глобальный :data:`PLAN_CANDIDATES` **в момент вызова**,
    а не дефолтным аргументом: дефолт привязывается один раз при определении функции,
    и тесты (которые подменяют дерево через monkeypatch) молча правили бы живой PLAN.md
    репозитория. Так мы их поймали и починили.
    """
    for path in PLAN_CANDIDATES if candidates is None else candidates:
        if path.is_file():
            return path
    return None


def check_not_forbidden(paths: list[Path]) -> None:
    """Страховка в рантайме: исторические доки в список правки попасть не должны."""
    for path in paths:
        for pattern in FORBIDDEN_DOCS:
            if fnmatch.fnmatch(path.name, pattern):
                raise SystemExit(f"{path.name} — исторический документ ({pattern}), не трогаю")


def update_plan(path: Path, count: int, *, dry_run: bool = False) -> bool:
    """Обновить маркерную строку ``ТЕСТЫ СЕЙЧАС: N passed …`` в PLAN.md."""
    text = path.read_text(encoding="utf-8")
    changed = False
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if PLAN_MARKER not in line:
            continue
        new_line = re.sub(r"(\d+)\s+passed", f"{count} passed", line, count=1)
        # В маркерной строке PLAN.md рядом живёт «(с tree-sitter — M)»: без него
        # строка разъезжается сама с собой (M = count + TREE_SITTER_TESTS).
        new_line = re.sub(
            r"(с tree-sitter —\s*)\d+", rf"\g<1>{count + TREE_SITTER_TESTS}", new_line, count=1
        )
        if new_line != line:
            lines[index] = new_line
            changed = True
    if changed and not dry_run:
        path.write_text("".join(lines), encoding="utf-8")
    return changed


def update_readme(path: Path, count: int, *, dry_run: bool = False) -> bool:
    """Обновить строку таблицы ``| Тесты | **N passed … с tree-sitter — M |``."""
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    changed = False
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("| Тесты |"):
            continue
        # Важно: сравниваем ТЕКСТ до/после, а не число замен из re.subn —
        # иначе dry-run рапортует «будет обновлён» для уже правильного числа.
        new_line = re.sub(r"\*\*(\d+)\s+passed", f"**{count} passed", line, count=1)
        new_line = re.sub(
            r"(с tree-sitter —\s*)\d+", rf"\g<1>{count + TREE_SITTER_TESTS}", new_line, count=1
        )
        if new_line != line:
            lines[index] = new_line
            changed = True
    if changed and not dry_run:
        path.write_text("".join(lines), encoding="utf-8")
    return changed


def sync(count: int, *, dry_run: bool = False) -> int:
    """Вписать ``count`` в живые доки; вернуть число изменённых файлов."""
    targets: list[Path] = [README]
    plan = find_plan()
    if plan is not None and PROJECT not in plan.parents and WORKSPACE not in plan.parents:
        # Страховка: PLAN.md чужого дерева (например, реального репо при подменённых
        # в тестах путях) не правим вовсе.
        print("  (PLAN.md вне текущего дерева — пропускаю)", plan.name)
        plan = None
    if plan is not None:
        targets.append(plan)
    check_not_forbidden(targets)

    changed = 0
    if plan is None:
        print("  (PLAN.md не найден — обновляю только README)")
    for path in targets:
        if not path.is_file():
            print("  (нет файла)", path.name)
            continue
        if path is plan:
            touched = update_plan(path, count, dry_run=dry_run)
        else:
            touched = update_readme(path, count, dry_run=dry_run)
        state = "обновлён" if touched else "без изменений"
        if dry_run and touched:
            state = "БУДЕТ обновлён"
        print(f"  {state}: {path.relative_to(WORKSPACE) if WORKSPACE in path.parents else path.name}")
        changed += int(touched)
    return changed


def main(argv: list[str] | None = None) -> int:
    """Точка входа: ``--count`` (не гонять pytest) и ``--dry-run`` (не писать)."""
    parser = argparse.ArgumentParser(description="Синхронизация числа тестов в живой документации")
    parser.add_argument("--count", type=int, default=None, help="число passed (иначе берётся из pytest)")
    parser.add_argument("--dry-run", action="store_true", help="только показать, что изменилось бы")
    args = parser.parse_args(argv)

    if not PROJECT.is_dir():  # pragma: no cover - скрипт лежит внутри проекта
        raise SystemExit(f"каталог проекта не найден: {PROJECT}")

    if args.count is None:
        count = run_tests()
        print(f"pytest: {count} passed")
    else:
        count = args.count
        print(f"число из аргумента: {count} passed")

    changed = sync(count, dry_run=args.dry_run)
    print(f"готово: изменено файлов — {changed}" + (" (dry-run, ничего не записано)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
