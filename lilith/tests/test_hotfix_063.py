"""Тесты хотфикса 0.6.3: два красных Unity-сборки у Кирюши + стражи на будущее.

Приёмка (Windows, Unity 6000.0.84f1, UniVRM 0.131.2, embedded-пакеты UniGLTF/VRM/VRM-1.0)
дала две ошибки компиляции, обе в ``VrmLoader.cs``:

1. ``CS0246: The type or namespace name 'RuntimeOnlyAwaitCaller' could not be found`` —
   тип объявлен в неймспейсе **UniGLTF** (физически ``Packages/UniGLTF/Runtime/Utils/
   AwaitCaller/RuntimeOnlyAwaitCaller.cs``, сборка ``UniGLTF.Utils``), а ``using UniGLTF;``
   у меня не было: я проверил по исходникам UniVRM сам класс, но не его неймспейс.
2. ``CS4032: The 'await' operator can only be used within an async method`` —
   ``SwapRoutine`` это ``IEnumerator``-корутина, ``await`` в ней недопустим.

Здесь — стражи, чтобы ни то ни другое не вернулось, плюс проверка нового инструмента
``scripts/check_csharp_syntax.py`` (tree-sitter), который ловит такие вещи до Unity.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "unity-client" / "Assets" / "LilithFace" / "Scripts"
VRM_LOADER = SCRIPTS_DIR / "VrmLoader.cs"


def read_cs(name: str) -> str:
    """Прочесть C#-файл клиента."""
    return (SCRIPTS_DIR / name).read_text(encoding="utf-8")


def _load_checker():
    """Загрузить модуль чекера синтаксиса (лежит в scripts/, не в пакете)."""
    path = PROJECT_ROOT / "scripts" / "check_csharp_syntax.py"
    spec = importlib.util.spec_from_file_location("check_csharp_syntax", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
#  Красный №1 — CS0246: RuntimeOnlyAwaitCaller
# --------------------------------------------------------------------------- #
class TestAwaitCallerNamespace:
    """Тип берётся из UniGLTF, и это зафиксировано в файле."""

    def test_using_unigltf_present(self) -> None:
        text = VRM_LOADER.read_text(encoding="utf-8")
        assert re.search(r"^using UniGLTF;$", text, re.M), "нет using UniGLTF; — CS0246 вернётся"

    def test_using_is_inside_unicrm_guard(self) -> None:
        """using UniGLTF обязан быть под #if, иначе проект не соберётся без UniVRM."""
        text = VRM_LOADER.read_text(encoding="utf-8")
        guard = text.split("#if LILITH_UNIVRM", 1)[1].split("#endif", 1)[0]
        assert "using UniGLTF;" in guard

    def test_constructor_takes_timeout(self) -> None:
        text = VRM_LOADER.read_text(encoding="utf-8")
        assert "new RuntimeOnlyAwaitCaller(awaitTimeoutSeconds)" in text
        assert re.search(r"public float awaitTimeoutSeconds\s*=\s*0\.001f;", text)

    def test_play_mode_only_limitation_documented(self) -> None:
        """RuntimeOnlyAwaitCaller вне Play Mode бросает NotSupportedException."""
        text = VRM_LOADER.read_text(encoding="utf-8")
        assert "NotSupportedException" in text
        assert "Play Mode" in text

    def test_await_caller_named_argument(self) -> None:
        """Параметр LoadBytesAsync называется awaitCaller (сверено по исходникам 0.131.2)."""
        text = VRM_LOADER.read_text(encoding="utf-8")
        assert "awaitCaller:" in text
        assert "Vrm10.LoadBytesAsync(" in text


# --------------------------------------------------------------------------- #
#  Красный №2 — CS4032: await в корутине
# --------------------------------------------------------------------------- #
class TestNoAwaitInCoroutine:
    """``SwapRoutine`` остаётся корутиной: ни одного ``await`` (CS4032).

    .. note::
        В 0.6.3 здесь жил гвард ``assert "yield return loadTask;" in text`` — он
        держал **неверную** инвариантность: Unity 6000.0 не умеет ждать ``Task``
        через ``yield return`` (Manual «Write and run coroutines»: из корутины
        поддерживается ``Awaitable``, а generic ``Awaitable<T>`` — явно нет).
        На приёмке F7 это дало дедлок редактора (``.Result`` у незавершённой задачи
        блокирует главный поток, на котором же висит ``NextFrameTaskScheduler``).
        С 0.6.5 гвард перевёрнут: ``yield return loadTask`` **запрещён**, а неблокирующее
        ожидание проверяется в ``tests/test_hotfix_065.py``.
    """

    def test_swap_routine_is_coroutine(self) -> None:
        text = VRM_LOADER.read_text(encoding="utf-8")
        assert re.search(r"private IEnumerator SwapRoutine\(", text)

    def test_task_is_not_yielded_directly(self) -> None:
        """0.6.5: ``yield return task`` НЕ ждёт завершения — это и был дедлок F7.

        Проверяем только строки кода: в комментариях конструкция обязана
        упоминаться — там объяснено, почему её больше нет.
        """
        text = VRM_LOADER.read_text(encoding="utf-8")
        offenders = [
            line.strip()
            for line in text.splitlines()
            if "yield return loadTask" in line and not line.strip().startswith(("//", "*"))
        ]
        assert offenders == [], (
            "вернулся yield return loadTask: Unity не ждёт Task в корутине, "
            f"а .Result у незавершённой задачи вешает редактор намертво → {offenders}"
        )

    def test_no_bare_await_in_vrm_loader(self) -> None:
        """Ни одного ``await`` в файле: загрузка идёт через Task + yield."""
        text = VRM_LOADER.read_text(encoding="utf-8")
        code_lines = [
            line for line in text.splitlines()
            if "await" in line and not line.strip().startswith(("//", "*"))
        ]
        offenders = [line.strip() for line in code_lines if re.search(r"\bawait\s", line)]
        assert offenders == [], f"await в корутине вернёт CS4032: {offenders}"

    def test_faulted_and_canceled_are_handled(self) -> None:
        """Исключение из Task достаём явно, иначе оно утонет в UnobservedTaskException."""
        text = VRM_LOADER.read_text(encoding="utf-8")
        assert "loadTask.IsFaulted" in text
        assert "GetBaseException()" in text
        assert "loadTask.IsCanceled" in text

    def test_generation_recheck_after_load(self) -> None:
        """Пока тело грузилось, мог прийти новый своп — результат надо выбросить."""
        text = VRM_LOADER.read_text(encoding="utf-8")
        after = text.split("instance = loadTask.Result;", 1)[1]
        assert "generation != _generation" in after
        assert "Destroy(instance.gameObject)" in after

    def test_async_methods_only_in_ws_client(self) -> None:
        """Во всём клиенте async живут только в транспорте (там они и нужны)."""
        for path in sorted(SCRIPTS_DIR.glob("*.cs")):
            text = path.read_text(encoding="utf-8")
            has_async = "async " in text
            if path.name == "LilithWSClient.cs":
                assert has_async
            else:
                assert not has_async, f"в {path.name} появился async-метод — проверь, нужен ли он"


# --------------------------------------------------------------------------- #
#  Жёлтый CS0067 — Loaded is never used
# --------------------------------------------------------------------------- #
class TestEventHasLegalCallSite:
    """События имеют точку вызова вне #if — предупреждение CS0067 закрыто по существу."""

    def test_notify_loaded_exists(self) -> None:
        text = VRM_LOADER.read_text(encoding="utf-8")
        assert "public void NotifyLoaded(" in text
        assert "Loaded?.Invoke(" in text

    def test_notify_failed_exists(self) -> None:
        text = VRM_LOADER.read_text(encoding="utf-8")
        assert "public void NotifyFailed(" in text
        assert "Failed?.Invoke(" in text

    def test_notify_methods_are_outside_guard(self) -> None:
        """Вырезаем ТОЛЬКО блоки #if LILITH_UNIVRM, с учётом вложенных #if."""
        text = VRM_LOADER.read_text(encoding="utf-8")
        outside: list[str] = []
        inside = 0
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#if LILITH_UNIVRM"):
                inside += 1
                continue
            if inside and stripped.startswith("#if"):
                inside += 1
                continue
            if inside and stripped.startswith("#endif"):
                inside -= 1
                continue
            if not inside:
                outside.append(line)
        assert "NotifyLoaded" in "\n".join(outside)


# --------------------------------------------------------------------------- #
#  .asmdef: символ LILITH_UNIVRM для embedded-пакетов
# --------------------------------------------------------------------------- #
class TestAsmdefVersionDefines:
    """У Кирюши UniVRM стоит embedded-пакетами — символ должен определиться и так."""

    @pytest.fixture(scope="class")
    def asmdef(self) -> dict:
        path = PROJECT_ROOT / "unity-client" / "Assets" / "LilithFace" / "LilithFace.asmdef"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_covers_all_univrm_package_names(self, asmdef: dict) -> None:
        names = {entry["name"] for entry in asmdef["versionDefines"]}
        assert {"com.vrmc.vrm", "com.vrmc.vrm10", "com.vrmc.gltf"} <= names

    def test_all_define_lilith_univrm(self, asmdef: dict) -> None:
        assert {entry["define"] for entry in asmdef["versionDefines"]} == {"LILITH_UNIVRM"}

    def test_references_stay_empty_in_repo(self, asmdef: dict) -> None:
        """В репозитории ссылки пустые: проект должен собираться ДО импорта UniVRM.

        У Кирюши ссылки проставлены вручную (UniGLTF, UniHumanoid, VRM10, UniGLTF.Utils) —
        при переезде копируется только ``Scripts/``, так что её .asmdef не затирается.
        """
        assert asmdef["references"] == []

    def test_readme_explains_manual_references(self) -> None:
        text = (PROJECT_ROOT / "unity-client" / "Assets" / "LilithFace" / "README.md").read_text(encoding="utf-8")
        assert "UniGLTF.Utils" in text


# --------------------------------------------------------------------------- #
#  Чекер синтаксиса: селектор веток условной компиляции
# --------------------------------------------------------------------------- #
class TestBranchSelector:
    """``select_branches`` — чистый Python, работает без tree-sitter."""

    @pytest.fixture(scope="class")
    def checker(self):
        return _load_checker()

    def test_if_true_branch(self, checker) -> None:
        source = "a\n#if X\nb\n#endif\nc\n"
        assert checker.select_branches(source, {"X"}).splitlines() == ["a", "", "b", "", "c"]

    def test_if_false_branch_blank(self, checker) -> None:
        source = "a\n#if X\nb\n#endif\nc\n"
        assert checker.select_branches(source, set()).splitlines() == ["a", "", "", "", "c"]

    def test_else_branch(self, checker) -> None:
        # NB: маркеры без form-feed (\x0c) — str.splitlines() считает его разделителем
        source = "#if X\n TAKEN \n#else\n OTHER \n#endif\n"
        assert checker.select_branches(source, {"X"}).splitlines() == ["", " TAKEN ", "", ""]
        assert checker.select_branches(source, set()).splitlines() == ["", "", "", " OTHER "]

    def test_negation_and_logic(self, checker) -> None:
        source = "#if !X\n notx \n#endif\n#if X && Y\n both \n#endif\n#if X || Y\n either \n#endif\n"
        without = checker.select_branches(source, set())
        with_x = checker.select_branches(source, {"X"})
        with_xy = checker.select_branches(source, {"X", "Y"})
        # `!X` пишется без пробела — регрессия токенизатора, найденная этим тестом
        assert "notx" in without
        assert "notx" not in with_x
        assert "both" in with_xy
        assert "both" not in with_x
        assert "either" in checker.select_branches(source, {"Y"})
        assert "either" in with_x

    def test_nested(self, checker) -> None:
        source = "#if A\n#if B\ninner\n#endif\nouter\n#endif\ntail\n"
        assert "inner" in checker.select_branches(source, {"A", "B"})
        assert "inner" not in checker.select_branches(source, {"A"})
        assert "outer" not in checker.select_branches(source, {"B"})
        assert "tail" in checker.select_branches(source, set())

    def test_line_numbers_preserved(self, checker) -> None:
        """Неактивные ветки заменяются пустыми строками — номера строк не едут."""
        source = "1\n2\n#if X\n4\n#endif\n6\n"
        assert checker.select_branches(source, set()).splitlines()[5] == "6"


def _tree_sitter_available() -> bool:
    """Доступен ли tree-sitter с C#-грамматикой (ставится отдельно, см. чекер)."""
    try:
        return (
            importlib.util.find_spec("tree_sitter") is not None
            and importlib.util.find_spec("tree_sitter_c_sharp") is not None
        )
    except (ImportError, ValueError):
        return False


@pytest.mark.skipif(
    not _tree_sitter_available(),
    reason="нет tree-sitter/tree-sitter-c-sharp: поставь .venv-ts (см. scripts/check_csharp_syntax.py)",
)
class TestCSharpSyntaxChecker:
    """Полный прогон чекера: обе ветки, все файлы клиента."""

    def test_all_scripts_parse(self) -> None:
        checker = _load_checker()
        import tree_sitter
        import tree_sitter_c_sharp

        if hasattr(tree_sitter, "Language"):
            parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_c_sharp.language()))
        else:  # pragma: no cover - старый API
            parser = tree_sitter.Parser()
            parser.set_language(tree_sitter_c_sharp.language())

        for defined in (set(), {"LILITH_UNIVRM"}):
            for path in sorted(SCRIPTS_DIR.glob("*.cs")):
                problems = checker.check_file(path, parser, defined)
                assert problems == [], f"{path.name} (define={defined or '—'}): {problems}"

    def test_await_misuse_is_detected(self) -> None:
        """Контрольный выстрел: чекер обязан ловить CS4032-подобный код."""
        checker = _load_checker()
        import tree_sitter
        import tree_sitter_c_sharp

        if hasattr(tree_sitter, "Language"):
            parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_c_sharp.language()))
        else:  # pragma: no cover
            parser = tree_sitter.Parser()
            parser.set_language(tree_sitter_c_sharp.language())

        broken = PROJECT_ROOT / "tests" / "samples" / "broken_await.cs"
        assert broken.is_file(), "положи образец: tests/samples/broken_await.cs"
        problems = checker.check_file(broken, parser, set())
        assert any("CS4032" in p for p in problems), problems
