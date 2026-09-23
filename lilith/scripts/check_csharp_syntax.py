#!/usr/bin/env python3
"""Проверка синтаксиса C#-файлов Unity-клиента парсером tree-sitter.

Зачем: Unity в песочнице нет, а отдавать Кирюше файл с незамеченной опечаткой —
роскошь (хотфикс 0.6.3 случился ровно из-за этого: ``await`` в корутине и
непроверенный неймспейс ``RuntimeOnlyAwaitCaller``). Парсер не заменяет компилятор
(типы он не проверяет), но ловит синтаксис: незакрытые скобки, ``await`` вне
метода, битые ``#if/#endif``, мусор вместо оператора.

Установка окружения (один раз, вне проекта — в архив этапа не попадает)::

    python3 -m venv .venv-ts && .venv-ts/bin/pip install tree-sitter tree-sitter-c-sharp

Запуск::

    .venv-ts/bin/python scripts/check_csharp_syntax.py
    .venv-ts/bin/python scripts/check_csharp_syntax.py --define LILITH_UNIVRM
    .venv-ts/bin/python scripts/check_csharp_syntax.py --root unity-client

По умолчанию проверяются **обе** ветки (без символа и с ``LILITH_UNIVRM``): проект
обязан компилироваться и до импорта UniVRM, и после.

Код возврата: 0 — ошибок нет, 1 — найдены ERROR-узлы.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Iterator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = PROJECT_ROOT / "unity-client"


def select_branches(text: str, defined: set[str]) -> str:
    """Применить условную компиляцию: оставить только строки активной ветки.

    Поддерживаются ``#if``, ``#elif``, ``#else``, ``#endif``, операторы ``!``,
    ``&&``, ``||`` и скобки. ``#define``/``#undef`` не моделируются (в unity-client
    их нет). Строки неактивных веток заменяются пустыми, чтобы номера строк в
    сообщениях об ошибках оставались настоящими.
    """
    out: list[str] = []
    # стек: (ветка_активна, какая_то_ветка_уже_сработала, родитель_активен)
    stack: list[tuple[bool, bool, bool]] = []

    def active() -> bool:
        return all(item[0] for item in stack) if stack else True

    def evaluate(expr: str) -> bool:
        """Вычислить условие ``#if`` для набора определённых символов.

        Токенизатор обязан отделять ``!`` от идентификатора: ``#if !X`` на практике
        пишут без пробела, и склейка ``"!X"`` превращалась в «неизвестный символ»
        → условие всегда ложно (баг, найденный тестом в хотфиксе 0.6.3).
        """
        expression = expr.strip()
        if not expression:
            return False
        for char in ("(", ")", "!", "&&", "||"):
            expression = expression.replace(char, f" {char} ")
        rebuilt: list[str] = []
        for token in expression.split():
            if token == "&&":
                rebuilt.append("and")
            elif token == "||":
                rebuilt.append("or")
            elif token == "!":
                rebuilt.append("not")
            elif token in {"(", ")"}:
                rebuilt.append(token)
            else:
                rebuilt.append(f"{token!r} in defined")
        try:
            return bool(eval(" ".join(rebuilt), {"defined": defined}))  # noqa: S307 - выражение из нашего же источника
        except SyntaxError:
            return False

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#if"):
            parent = active()
            taken = parent and evaluate(stripped[3:])
            stack.append((taken, taken, parent))
            out.append("")
        elif stripped.startswith("#elif"):
            if stack:
                taken_now, taken_before, parent = stack[-1]
                taken = parent and not taken_before and evaluate(stripped[5:])
                stack[-1] = (taken, taken_before or taken, parent)
            out.append("")
        elif stripped.startswith("#else"):
            if stack:
                _taken_now, taken_before, parent = stack[-1]
                stack[-1] = (parent and not taken_before, True, parent)
            out.append("")
        elif stripped.startswith("#endif"):
            if stack:
                stack.pop()
            out.append("")
        elif stripped.startswith("#"):
            out.append(line)  # остальные директивы (using, define) оставляем как есть
        else:
            out.append(line if active() else "")
    return "\n".join(out)


def iter_errors(node: Any) -> Iterator[Any]:
    """Обойти дерево и выдать все ERROR/MISSING-узлы."""
    if node.type == "ERROR" or node.is_missing:
        yield node
    for child in node.children:
        yield from iter_errors(child)


#: Узлы грамматики, внутри которых ``await`` законен только при модификаторе ``async``.
_FUNCTION_NODES = ("method_declaration", "local_function_statement", "lambda_expression",
                   "anonymous_method_expression", "constructor_declaration", "destructor_declaration")


def find_await_misuse(root: Any, source: bytes) -> list[str]:
    """Найти ``await`` вне async-метода — аналог CS4032 (ошибка приёмки 0.6.2).

    Парсер типов не знает, но структуру видит: обходим каждую функцию и проверяем,
    есть ли у неё модификатор ``async``. Отдельно ловим ``await`` на верхнем уровне
    (вне любой функции) — в C# так можно только в top-level statements.
    """
    problems: list[str] = []

    def walk(node: Any, inside_async: bool, inside_function: bool) -> None:
        if node.type in _FUNCTION_NODES:
            modifiers = ""
            for child in node.children:
                if child.type == "modifier":
                    modifiers += child.text.decode("utf-8", "replace") + " "
                elif child.type == "modifiers":
                    modifiers += child.text.decode("utf-8", "replace") + " "
            walk_children(node, "async" in modifiers, True)
            return
        walk_children(node, inside_async, inside_function)

    def walk_children(node: Any, inside_async: bool, inside_function: bool) -> None:
        for child in node.children:
            if child.type == "await_expression":
                if not inside_async:
                    where = "вне метода" if not inside_function else "в не-async методе"
                    snippet = " ".join(source[child.start_byte:child.end_byte].decode("utf-8", "replace").split())[:50]
                    problems.append(
                        f"{child.start_point[0] + 1}:{child.start_point[1] + 1} "
                        f"CS4032-подобное: `await` {where} → «{snippet}»"
                    )
                # вложенные лямбды внутри async-метода наследуют контекст только если сами async
                walk_children(child, inside_async, inside_function)
                continue
            walk(child, inside_async, inside_function)

    walk_children(root, False, False)
    return problems


def find_interpolation_misuse(root: Any, source: bytes) -> list[str]:
    """Найти тернарник внутри интерполяции без скобок — аналог **CS8361**.

    Полевая правка Кирюши на замере Г: `$"… {_client != null ? _client.OutgoingPending : -1} …"`
    не компилируется — C# требует скобок вокруг условного выражения в интерполяции.
    Парсер такое **синтаксически** принимает (узел `interpolation` → `conditional_expression`),
    поэтому ошибка невидима ни чекеру синтаксиса, ни мне в песочнице. Ловим структурно:
    если у узла `interpolation` прямой потомок `conditional_expression` (а не
    `parenthesized_expression`) — это красный CS8361 в Unity.
    """
    problems: list[str] = []

    def walk(node: Any) -> None:
        if node.type == "interpolation":
            for child in node.children:
                if child.type == "conditional_expression":
                    snippet = " ".join(
                        source[child.start_byte : child.end_byte].decode("utf-8", "replace").split()
                    )[:60]
                    problems.append(
                        f"{child.start_point[0] + 1}:{child.start_point[1] + 1} "
                        f"CS8361-подобное: тернарник в интерполяции без скобок → «{snippet}»"
                    )
        for child in node.children:
            walk(child)

    walk(root)
    return problems


def build_parser() -> Any:
    """Собрать парсер C# (учтён и новый, и старый API tree-sitter).

    Вынесено из ``main()`` в 0.6.6, чтобы тесты могли прогнать правило CS8361
    по отдельным файлам и по обеим веткам ``#if`` без запуска CLI.
    """
    import tree_sitter
    import tree_sitter_c_sharp

    if hasattr(tree_sitter, "Language"):
        return tree_sitter.Parser(tree_sitter.Language(tree_sitter_c_sharp.language()))
    parser = tree_sitter.Parser()  # pragma: no cover - старый API tree-sitter
    parser.set_language(tree_sitter_c_sharp.language())  # pragma: no cover
    return parser  # pragma: no cover


def check_file(path: Path, parser: Any, defined: set[str] | None = None) -> list[str]:
    """Распарсить файл (с учётом условной компиляции); вернуть список ошибок."""
    text = path.read_text(encoding="utf-8")
    if defined is not None:
        text = select_branches(text, defined)
    source = text.encode("utf-8")
    tree = parser.parse(source)
    problems: list[str] = []
    for node in iter_errors(tree.root_node):
        raw = source[node.start_byte : node.end_byte].decode("utf-8", "replace")
        snippet = " ".join(raw.split())[:60]
        problems.append(
            f"{path.name}:{node.start_point[0] + 1}:{node.start_point[1] + 1} "
            f"{'MISSING' if node.is_missing else 'ERROR'} {node.type} → «{snippet}»"
        )
    if not problems:
        # структурные проверки имеют смысл только на неповреждённом дереве
        for problem in find_await_misuse(tree.root_node, source):
            problems.append(f"{path.name}:{problem}")
        for problem in find_interpolation_misuse(tree.root_node, source):
            problems.append(f"{path.name}:{problem}")
    return problems


def main() -> int:
    """Точка входа CLI."""
    parser_arg = argparse.ArgumentParser(description="Синтаксическая проверка C#-файлов unity-client")
    parser_arg.add_argument("--root", default=str(DEFAULT_ROOT), help="где искать *.cs")
    parser_arg.add_argument(
        "--define",
        action="append",
        default=None,
        help="символ условной компиляции (можно несколько); без флага проверяются обе ветки",
    )
    args = parser_arg.parse_args()

    try:
        import tree_sitter
        import tree_sitter_c_sharp
    except ImportError:
        print("нужны пакеты: pip install tree-sitter tree-sitter-c-sharp", file=sys.stderr)
        return 2

    root = Path(args.root)
    files = sorted(root.rglob("*.cs"))
    if not files:
        print(f"в {root} нет ни одного .cs")
        return 2

    parser = build_parser()

    if args.define is None:
        # Обе ветки: до импорта UniVRM (символ не определён) и после.
        variants = [
            ("без LILITH_UNIVRM (проект до импорта UniVRM)", frozenset()),
            ("с LILITH_UNIVRM (проект после импорта UniVRM)", frozenset({"LILITH_UNIVRM"})),
        ]
    else:
        variants = [(f"define: {', '.join(args.define)}", frozenset(args.define))]

    total = 0
    for label, defined in variants:
        print(f"--- ветка: {label} ---")
        for path in files:
            problems = check_file(path, parser, set(defined))
            for problem in problems:
                print("  [FAIL]", problem)
            if not problems:
                print(f"  [ OK ] {path.relative_to(root.parent)}")
            total += len(problems)

    print(f"\nФайлов: {len(files)}, веток: {len(variants)}, ошибок: {total}")
    print("Проверено: синтаксис + `await` вне async-метода (CS4032) + тернарник в интерполяции (CS8361).")
    print("NB: типы, имена и неймспейсы парсер НЕ проверяет — это делает компилятор Unity.")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
