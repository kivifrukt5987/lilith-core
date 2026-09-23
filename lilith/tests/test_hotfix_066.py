"""Гварды 0.6.6 — стекло и полировка окна (ядро этапа 6 принято замером Г).

Замер Г закрыл дедлок: фазы 0…7 живьём, standalone-билд доехал, ``--speak`` 20/20 дважды.
Осталось стекло, и оно принесло три класса ошибок, которые **не видны из песочницы**:

1. **Ошибки компиляции в player-only ветках.** ``rect.Left`` (CS1061) и тернарник в
   интерполяции без скобок (CS8361) жили несколько релизов: в Editor ветка
   ``#if UNITY_STANDALONE_WIN && !UNITY_EDITOR`` вырезана, а синтаксический чекер типов
   не знает. Лечим дважды: правим код и учим инструмент (правило CS8361 в
   ``check_csharp_syntax.py`` + гвард регистра RECT здесь).
2. **Win32-вызовы каждый кадр.** Эксперимент Кирюши с ``Layered Color Key`` дал окно,
   которое «металось и рябило». Гвард: в ``Update()`` нет ни одного Win32-вызова,
   ``SetWindowPos`` не зовётся, если прямоугольник не изменился, а
   ``SetLayeredWindowAttributes`` встречается в файле ровно один раз.
3. **Мета VRM по памяти.** ``commercialUsage: "personal"`` — такого значения в
   спецификации VRM 1.0 нет (``personalNonProfit`` / ``personalProfit`` / ``corporation``).
   Гвард сверяет enum'ы с ``META_ENUMS`` и обязан падать на мусоре (контрольный выстрел).

Отдельно: прозрачность в Unity 6 — это **настройка плеера** (Fullscreen Window +
``Use Flip Model Swapchain = ❌``), а не Win32-код, поэтому гварды проверяют, что код
пишет эту подсказку в Player.log и что режим можно сменить живьём (F7) без ребилда.
"""

from __future__ import annotations

import importlib.util
import json
import re
import struct
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "unity-client" / "Assets" / "LilithFace" / "Scripts"
TRANSPARENT_WINDOW = SCRIPTS_DIR / "TransparentWindow.cs"
FACE_CLIENT = SCRIPTS_DIR / "LilithFaceClient.cs"
CLIENT_CONFIG = SCRIPTS_DIR / "LilithClientConfig.cs"
ALL_CS = sorted(SCRIPTS_DIR.glob("*.cs"))
SAMPLE_VRM = PROJECT_ROOT / "tests" / "samples" / "test_cube.vrm"
SCENE_MD = PROJECT_ROOT / "unity-client" / "Assets" / "LilithFace" / "SCENE.md"
UNITY_README = PROJECT_ROOT / "unity-client" / "Assets" / "LilithFace" / "README.md"
HANDOVER_MD = PROJECT_ROOT.parent / "HANDOVER.md"
RELEASE_MD = PROJECT_ROOT / "RELEASE_0.6.6.md"


def read(path: Path) -> str:
    """Прочесть файл проекта."""
    return path.read_text(encoding="utf-8")


def code_lines(text: str) -> list[str]:
    """Строки кода без комментариев (закомментированный вызов вызовом не считается)."""
    return [line.strip() for line in text.splitlines() if not line.strip().startswith(("//", "*"))]


def mask_literals(text: str) -> str:
    """Заменить содержимое строк, символов и комментариев пробелами (длину сохранить).

    Нужно, чтобы поиск границ оператора не спотыкался о ``;`` **внутри** строки лога:
    ``Debug.Log("… Fullscreen Window; " + "… Flip Model Swapchain …")`` — одна
    инструкция, а наивный ``rfind(";")`` режет её пополам и гвард теряет вызов.
    """
    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "/" and index + 1 < length and text[index + 1] == "/":
            while index < length and text[index] != "\n":
                out.append(" ")
                index += 1
            continue
        if char == "/" and index + 1 < length and text[index + 1] == "*":
            while index < length and not (text[index] == "*" and index + 1 < length and text[index + 1] == "/"):
                out.append("\n" if text[index] == "\n" else " ")
                index += 1
            out.append("  ")
            index += 2
            continue
        if char in "\"'":
            out.append(char)
            index += 1
            while index < length:
                if text[index] == "\\":
                    out.append("  ")
                    index += 2
                    continue
                if text[index] == char:
                    out.append(char)
                    index += 1
                    break
                out.append("\n" if text[index] == "\n" else " ")
                index += 1
            continue
        out.append(char)
        index += 1
    return "".join(out)


def statement_of(text: str, needle: str) -> str:
    """Оператор C#, в котором стоит ``needle`` (от предыдущей ``;`` до следующей).

    Гвард обязан проверять **вызов**, а не подстроку: урок 0.6.4 (закомментированный
    вызов) и 0.6.5 (``_ = ($"…")`` вместо ``Debug.Log``). Границы ищем по тексту
    с замаскированными литералами (``mask_literals``), возвращаем оригинальный срез.
    """
    index = text.index(needle)
    masked = mask_literals(text)
    start = masked.rfind(";", 0, index) + 1
    end = masked.find(";", index)
    return text[start : end if end != -1 else len(text)]


def py_function(source: str, name: str) -> str:
    """Тело питоновской функции по имени (для гвардов на сам инструмент)."""
    start = source.index(f"def {name}")
    rest = source[start:]
    lines = rest.splitlines()
    body = [lines[0]]
    for line in lines[1:]:
        if line.strip() and not line.startswith((" ", "\t")):
            break
        body.append(line)
    return "\n".join(body)


def assert_logged(text: str, needle: str, what: str) -> None:
    """Проверить, что ``needle`` стоит внутри реального вызова ``Debug.Log*``."""
    statement = statement_of(text, needle)
    assert re.search(r"Debug\.(Log|LogWarning|LogError)\(", statement), (
        f"{what}: строка есть, а вызова Debug.Log нет → {statement.strip()[:90]!r}"
    )


def block_of(text: str, signature: str) -> str:
    """Тело метода по сигнатуре (со считанием фигурных скобок)."""
    start = text.index(signature)
    brace = text.index("{", start)
    depth = 0
    for index in range(brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[brace : index + 1]
    raise AssertionError(f"не нашлось закрывающей скобки для {signature!r}")


# --------------------------------------------------------------------------- #
#  Сканер интерполяций: независимая от tree-sitter реализация правила CS8361
# --------------------------------------------------------------------------- #
def interpolation_holes(text: str) -> list[tuple[int, str]]:
    """Дырки ``{…}`` внутри интерполированных строк ``$"…"`` (номер строки, содержимое).

    Написан deliberately другим способом, чем ``check_csharp_syntax.find_interpolation_misuse``
    (tree-sitter): две независимые реализации одного правила ловят друг друга.
    """
    holes: list[tuple[int, str]] = []
    index = 0
    length = len(text)
    while index < length:
        if text[index] != "$" or index + 1 >= length or text[index + 1] != '"':
            index += 1
            continue
        cursor = index + 2
        while cursor < length:
            char = text[cursor]
            if char == "\\" and cursor + 1 < length:
                cursor += 2
                continue
            if char == '"':
                break
            if char == "{":
                if cursor + 1 < length and text[cursor + 1] == "{":  # экранированная {{
                    cursor += 2
                    continue
                depth = 0
                hole_start = cursor + 1
                while cursor < length:
                    if text[cursor] == "{":
                        depth += 1
                    elif text[cursor] == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    cursor += 1
                holes.append((text.count("\n", 0, hole_start) + 1, text[hole_start:cursor]))
            cursor += 1
        index = cursor + 1
    return holes


def has_top_level_question(expr: str) -> bool:
    """Есть ли в выражении «настоящий» ``?`` тернарника (не ``?.``, не ``??``)."""
    depth = 0
    in_string = False
    for position, char in enumerate(expr):
        if in_string:
            if char == "\\":
                continue
            if char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char in "([":
            depth += 1
            continue
        if char in ")]":
            depth -= 1
            continue
        if char != "?" or depth != 0:
            continue
        previous = expr[position - 1] if position else ""
        following = expr[position + 1] if position + 1 < len(expr) else ""
        if previous == "?" or following in ".?":  # ?? и ?. — не тернарник
            continue
        return True
    return False


def find_cs8361(text: str) -> list[str]:
    """Все тернарники в интерполяциях без скобок (CS8361) — текстовым сканером."""
    problems = []
    for line, hole in interpolation_holes(text):
        stripped = hole.strip()
        if stripped.startswith("("):
            continue
        if has_top_level_question(stripped):
            problems.append(f"{line}: {stripped[:60]}")
    return problems


def _load_script(name: str, prefix: str):
    """Загрузить модуль из ``scripts/`` (без установки пакета)."""
    path = PROJECT_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"lilith_{prefix}_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


make_test_vrm = _load_script("make_test_vrm", "066")


# --------------------------------------------------------------------------- #
#  A. Полевые правки Кирюши закрыты и защищены
# --------------------------------------------------------------------------- #
class TestFieldFixes:
    """Три правки с реальной машины: каждая внесена в репо и закрыта гвардом."""

    def test_no_ternary_in_interpolation_anywhere(self):
        """CS8361: во всех 11 файлах (обе ветки) нет тернарника в интерполяции без скобок."""
        offenders = []
        for path in ALL_CS:
            text = read(path)
            for branch in (text, _flip_player_branch(text)):
                offenders.extend(f"{path.name}: {item}" for item in find_cs8361(branch))
        assert not offenders, "тернарники в интерполяции без скобок (CS8361):\n" + "\n".join(offenders)

    def test_control_shot_scanner_catches_the_bug(self):
        """Контрольный выстрел: сканер обязан поймать ровно ту конструкцию из поля."""
        broken = 'var s = $"в очереди {_client != null ? _client.OutgoingPending : -1}";'
        fixed = 'var s = $"в очереди {(_client != null ? _client.OutgoingPending : -1)}";'
        assert find_cs8361(broken), "сканер пропустил CS8361 — гвард ничего не стоит"
        assert not find_cs8361(fixed), "сканер ругается на правильные скобки"

    def test_control_shot_scanner_ignores_legal_cases(self):
        """Контрольный выстрел №2: ``?.``, ``??`` и формат-спецификатор — не тернарник."""
        assert not find_cs8361('var s = $"{loader?.LastBody}";')
        assert not find_cs8361('var s = $"{name ?? "нет"}";')
        assert not find_cs8361('var s = $"{ms:0.0} мс";')
        assert not find_cs8361('var s = $"{(ok ? "да" : "нет")}";')

    def test_the_fixed_line_is_the_one_from_the_field(self):
        """Строка 322 из донесения: скобки на месте, вызов лога не выродился."""
        text = read(FACE_CLIENT)
        assert_logged(text, "в очереди", "лог h-фазы с очередью")
        assert "{(_client != null ? _client.OutgoingPending : -1)}" in text

    def test_rect_fields_are_lowercase_and_complete(self):
        """RECT: единый нижний регистр, все четыре поля."""
        text = read(TRANSPARENT_WINDOW)
        match = re.search(r"struct RECT\s*\{(.*?)\}", text, re.S)
        assert match, "структура RECT пропала"
        fields = re.findall(r"public\s+int\s+(\w+);", match.group(1))
        assert fields == ["left", "top", "right", "bottom"], f"поля RECT: {fields}"

    def test_every_rect_access_is_a_declared_field(self):
        """CS1061: каждое ``rect.<member>`` существует в структуре (обе ветки)."""
        text = read(TRANSPARENT_WINDOW)
        declared = set(re.findall(r"public\s+int\s+(\w+);", re.search(r"struct RECT\s*\{(.*?)\}", text, re.S).group(1)))
        for branch_name, branch in (("player", text), ("editor", _flip_player_branch(text))):
            used = set(re.findall(r"\brect\.(\w+)", branch))
            assert used <= declared, f"ветка {branch_name}: обращения к несуществующим полям RECT: {sorted(used - declared)}"
            assert not re.search(r"\brect\.(Left|Top|Right|Bottom)\b", branch), f"ветка {branch_name}: старый регистр RECT"

    def test_control_shot_rect_guard_catches_capital(self):
        """Контрольный выстрел: ``rect.Left`` обязан быть пойман гвардом выше."""
        broken = "var x = rect.Left;"
        declared = {"left", "top", "right", "bottom"}
        used = set(re.findall(r"\brect\.(\w+)", broken))
        assert not used <= declared, "гвард RECT пропустил rect.Left"


def _flip_player_branch(text: str) -> str:
    """Вторая ветка ``#if UNITY_STANDALONE_WIN && !UNITY_EDITOR``: то, что видит Editor.

    Чекер прогоняет обе ветки через ``--define``; здесь то же самое для текстовых гвардов,
    чтобы player-only код не прятался за препроцессором.
    """
    out: list[str] = []
    skipping = False
    depth = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#if"):
            depth += 1
            skipping = "UNITY_STANDALONE_WIN" in stripped
            continue
        if stripped.startswith(("#else", "#elif")) and depth:
            skipping = not skipping
            continue
        if stripped.startswith("#endif") and depth:
            depth -= 1
            skipping = False
            continue
        out.append("" if skipping else line)
    return "\n".join(out)


# --------------------------------------------------------------------------- #
#  B. Правило CS8361 в чекере (инструмент, а не только код)
# --------------------------------------------------------------------------- #
class TestCheckerRuleCs8361:
    """``check_csharp_syntax.py`` обязан ловить CS8361 в обеих ветках."""

    @pytest.fixture(scope="class")
    def checker(self):
        try:
            import tree_sitter  # noqa: F401
            import tree_sitter_c_sharp  # noqa: F401
        except ImportError as exc:  # pragma: no cover - зависит от окружения
            pytest.skip(f"tree-sitter не установлен: {exc}")
        return _load_script("check_csharp_syntax", "066chk")

    def test_rule_exists_and_is_wired_into_check_file(self, checker):
        """Правило не просто объявлено, а вызывается из ``check_file``."""
        assert hasattr(checker, "find_interpolation_misuse")
        source = read(PROJECT_ROOT / "scripts" / "check_csharp_syntax.py")
        check_file_src = py_function(source, "check_file")
        assert "find_interpolation_misuse(tree.root_node" in check_file_src, (
            "правило CS8361 не вызывается из check_file"
        )
        assert "problems.append" in check_file_src, "правило не добавляет проблемы"

    def test_control_shot_rule_flags_broken_code(self, checker, tmp_path):
        """Контрольный выстрел: битый файл → FAIL, исправленный → OK."""
        broken = tmp_path / "Broken.cs"
        broken.write_text(
            'namespace X { class A { int? n = 1;\n'
            'void M() { var s = $"q {n != null ? n : -1}"; } } }\n',
            encoding="utf-8",
        )
        fixed = tmp_path / "Fixed.cs"
        fixed.write_text(
            'namespace X { class A { int? n = 1;\n'
            'void M() { var s = $"q {(n != null ? n : -1)}"; } } }\n',
            encoding="utf-8",
        )
        parser = checker.build_parser()
        assert checker.check_file(broken, parser), "чекер пропустил CS8361"
        assert not checker.check_file(fixed, parser), "чекер ругается на правильные скобки"

    def test_rule_sees_player_only_branch(self, checker, tmp_path):
        """Ошибка внутри ``#if UNITY_STANDALONE_WIN && !UNITY_EDITOR`` тоже видна."""
        path = tmp_path / "PlayerOnly.cs"
        path.write_text(
            "namespace X { class A { int? n = 1;\n"
            "void M() {\n"
            "#if UNITY_STANDALONE_WIN && !UNITY_EDITOR\n"
            'var s = $"q {n != null ? n : -1}";\n'
            "#endif\n"
            "} } }\n",
            encoding="utf-8",
        )
        parser = checker.build_parser()
        defined = {"UNITY_STANDALONE_WIN"}
        problems = checker.check_file(path, parser, defined)
        assert problems, "player-only ветка не проверена — именно там жил CS8361"

    #: Наборы символов, которыми чекер прогоняет файлы (две независимые оси ``#if``:
    #: Editor/плеер и «UniVRM ещё не импортирован / импортирован»).
    DEFINE_SETS = (
        frozenset(),
        frozenset({"LILITH_UNIVRM"}),
        frozenset({"UNITY_STANDALONE_WIN"}),
        frozenset({"UNITY_STANDALONE_WIN", "LILITH_UNIVRM"}),
    )

    def test_repo_is_clean_under_the_rule(self, checker):
        """Все 11 файлов чисты во всех четырёх ветках условной компиляции."""
        parser = checker.build_parser()
        problems = []
        for path in ALL_CS:
            for defined in self.DEFINE_SETS:
                problems.extend(f"[{','.join(sorted(defined)) or 'пусто'}] {p}"
                                for p in checker.check_file(path, parser, set(defined)))
        assert not problems, "чекер нашёл ошибки:\n" + "\n".join(problems)


# --------------------------------------------------------------------------- #
#  C. Прозрачность: подсказка в логе, MSAA, смена режима живьём
# --------------------------------------------------------------------------- #
class TestTransparency:
    """Пункт 1 донесения: рабочая прозрачность в билде дефолтом для Win10."""

    def test_dwm_hint_names_both_player_settings(self):
        """Код сам пишет в Player.log, какие два чекбокса лечат чёрный фон."""
        text = read(TRANSPARENT_WINDOW)
        hints = block_of(text, "private void LogModeHints")
        assert_logged(hints, "Use Flip Model Swapchain", "подсказка про flip-model swapchain")
        assert_logged(hints, "Fullscreen Window", "подсказка про Fullscreen Window")
        assert "LogModeHints(mode)" in block_of(text, "public void Apply()"), (
            "подсказка объявлена, но не вызывается"
        )

    def test_msaa_disabled_in_code(self):
        """MSAA размывает альфу (и даёт кайму на цветовом ключе) — выключаем сами."""
        apply_block = block_of(read(TRANSPARENT_WINDOW), "public void Apply()")
        assert "QualitySettings.antiAliasing = 0;" in apply_block
        camera_block = block_of(read(TRANSPARENT_WINDOW), "public void PrepareCamera")
        assert "cam.allowMSAA = false;" in camera_block

    def test_camera_cleared_with_zero_alpha(self):
        """Фон камеры — SolidColor с альфой 0, иначе DWM нечего показывать."""
        camera_block = block_of(read(TRANSPARENT_WINDOW), "public void PrepareCamera")
        assert "CameraClearFlags.SolidColor" in camera_block
        assert "new Color(0f, 0f, 0f, 0f)" in camera_block

    def test_dwm_margins_are_minus_one(self):
        """Стекло на всю клиентскую область: все четыре поля ``-1``."""
        block = block_of(read(TRANSPARENT_WINDOW), "private void ApplyTransparency")
        assert "cxLeftWidth = -1" in block and "cyBottomHeight = -1" in block
        assert "DwmExtendFrameIntoClientArea" in block

    def test_mode_line_is_logged_with_details(self):
        """Строку режима в Player.log сохраняем (просьба архитектора) + детали окна."""
        text = read(TRANSPARENT_WINDOW)
        assert_logged(text, "прозрачность окна:", "строка режима прозрачности")
        statement = statement_of(text, "прозрачность окна:")
        assert "toolWindow=" in statement and "frame=" in statement, (
            f"в строке режима нет деталей окна: {statement.strip()[:120]!r}"
        )

    def test_f7_cycles_modes_without_rebuild(self):
        """Прибор важнее гипотезы: режим меняется живьём, каждая смена логируется."""
        text = read(TRANSPARENT_WINDOW)
        update = block_of(text, "private void Update()")
        assert "CycleMode()" in update, "F7 не переключает режим"
        cycle = block_of(text, "public void CycleMode()")
        assert "config.transparency = next;" in cycle, "режим не сохраняется в конфиг"
        assert "_applied = false;" in cycle, "без сброса _applied Apply() выйдет сразу"
        assert "Apply();" in cycle, "новый режим не применяется"
        assert_logged(cycle, "F7: режим прозрачности", "лог смены режима")

    def test_apply_is_idempotent(self):
        """Повторный ``Apply()`` не дёргает Win32 (иначе рябь)."""
        apply_block = block_of(read(TRANSPARENT_WINDOW), "public void Apply()")
        assert apply_block.lstrip().startswith("{")
        assert "if (_applied)" in apply_block and "return;" in apply_block


# --------------------------------------------------------------------------- #
#  D. Win32 только по изменению состояния (лечение «металось и рябило»)
# --------------------------------------------------------------------------- #
class TestWin32Discipline:
    """Ни одного Win32-вызова из ``Update()``, ни одного лишнего ``SetWindowPos``."""

    WIN32_CALLS = (
        "SetWindowPos(",
        "SetWindowLong(",
        "SetLayeredWindowAttributes(",
        "DwmExtendFrameIntoClientArea(",
        "GetWindowRect(",
    )

    def test_update_calls_no_win32(self):
        update = block_of(read(TRANSPARENT_WINDOW), "private void Update()")
        offenders = [call for call in self.WIN32_CALLS if call in update]
        assert not offenders, f"Win32-вызовы в Update() (рябь/метание окна): {offenders}"

    def test_layered_attributes_appears_once(self):
        """``SetLayeredWindowAttributes`` — один раз на смену режима, не каждый кадр.

        Считаем **вызовы**: ``extern``-объявление P/Invoke вызовом не является.
        """
        calls = [
            line for line in code_lines(read(TRANSPARENT_WINDOW))
            if "SetLayeredWindowAttributes(" in line and "extern" not in line
        ]
        assert len(calls) == 1, f"вызовов SetLayeredWindowAttributes {len(calls)}: {calls}"
        assert "SetLayeredWindowAttributes(" in block_of(read(TRANSPARENT_WINDOW), "private void ApplyTransparency")

    def test_reposition_skips_when_rect_unchanged(self):
        """``SetWindowPos`` не вызывается, если целевой прямоугольник равен текущему."""
        block = block_of(read(TRANSPARENT_WINDOW), "private void Reposition")
        assert "!force" in block, "нет пути «не форсируем»"
        assert "rect.left == x" in block and "rect.top == y" in block, "нет сравнения позиции"
        assert "(rect.right - rect.left) == width" in block, "нет сравнения размера"
        assert block.index("return;") < block.index("SetWindowPos("), (
            "ранний выход должен стоять ДО SetWindowPos"
        )

    def test_styles_are_reset_before_being_set(self):
        """Стили не накапливаются: сначала сброс обоих флагов, потом ровно один."""
        block = block_of(read(TRANSPARENT_WINDOW), "private void ApplyStyles")
        assert "~(WS_EX_TOOLWINDOW | WS_EX_APPWINDOW | WS_EX_LAYERED)" in block, (
            "расширенные стили не сбрасываются — окно залипает в прежнем режиме"
        )
        assert "WS_EX_TOOLWINDOW : WS_EX_APPWINDOW" in block, (
            "windowToolWindow не выбирает между TOOLWINDOW и APPWINDOW"
        )

    def test_transparency_applied_only_from_apply(self):
        """``ApplyTransparency`` зовётся из ``Apply()`` (и больше ниоткуда)."""
        text = "\n".join(code_lines(read(TRANSPARENT_WINDOW)))
        assert text.count("ApplyTransparency(mode)") == 1, "ApplyTransparency вызывается не один раз"


# --------------------------------------------------------------------------- #
#  E. Конфиг окна: tool window, рамка, хоткей закрытия
# --------------------------------------------------------------------------- #
class TestWindowConfig:
    """Пункты 2 и 4 донесения: ``windowToolWindow`` и способ закрыть окно."""

    @pytest.mark.parametrize(
        "field,default",
        [
            ("windowToolWindow", "true"),
            ("showWindowFrame", "false"),
            ("closeHotkeyEnabled", "true"),
            ("closeHotkey", "KeyCode.Q"),
        ],
    )
    def test_field_default(self, field, default):
        text = read(CLIENT_CONFIG)
        assert re.search(rf"public\s+\w+\s+{field}\s*=\s*{re.escape(default)}\s*;", text), (
            f"поле {field} отсутствует или дефолт не {default}"
        )

    def test_tool_window_flag_reaches_the_style(self):
        block = block_of(read(TRANSPARENT_WINDOW), "private void ApplyStyles")
        assert "config.windowToolWindow" in block, "флаг windowToolWindow не используется"

    def test_frame_option_restores_caption(self):
        block = block_of(read(TRANSPARENT_WINDOW), "private void ApplyStyles")
        assert "config.showWindowFrame" in block, "флаг рамки не используется"
        assert "WS_CAPTION | WS_THICKFRAME" in block, "рамка не возвращается стилями"

    def test_close_hotkey_needs_ctrl_alt_and_quits(self):
        """Ctrl+Alt+Q: оба модификатора обязательны, действие — ``Application.Quit()``."""
        update = block_of(read(TRANSPARENT_WINDOW), "private void Update()")
        assert "config.closeHotkeyEnabled" in update
        assert "KeyCode.LeftControl" in update and "KeyCode.RightControl" in update
        assert "KeyCode.LeftAlt" in update and "KeyCode.RightAlt" in update
        assert "Input.GetKeyDown(config.closeHotkey)" in update
        quit_statement = statement_of(update, "Application.Quit()")
        assert "Application.Quit()" in quit_statement
        assert_logged(update, "хоткей закрытия", "лог закрытия по хоткею")

    def test_hotkeys_documented_in_scene(self):
        scene = read(SCENE_MD)
        for needle in ("F7", "F8", "F9", "Ctrl+Alt+Q", "Window Tool Window",
                       "Show Window Frame", "Close Hotkey"):
            assert needle in scene, f"в SCENE.md не описано: {needle}"


# --------------------------------------------------------------------------- #
#  F. Оверлей без дрожания (пункт 3 донесения)
# --------------------------------------------------------------------------- #
class TestOverlayStability:
    """``OnGUI`` вызывается несколько раз за кадр — текст собираем один раз."""

    def test_overlay_text_is_built_in_update(self):
        text = read(FACE_CLIENT)
        update = block_of(text, "private void Update()")
        assert "BuildOverlayText()" in update, "строка оверлея не собирается в Update"
        assert "showDebugOverlay" in update, "оверлей собирается и когда выключен"

    def test_ongui_draws_snapshot_only_on_repaint(self):
        text = read(FACE_CLIENT)
        ongui = block_of(text, "private void OnGUI()")
        assert "EventType.Repaint" in ongui, "нет фильтра по событию Repaint"
        assert "_overlayText" in ongui, "OnGUI рисует не снимок"
        assert "Describe()" not in ongui, "OnGUI снова считает текст сам — дрожание вернётся"
        assert ongui.count("GUI.Label") == 1

    def test_snapshot_field_exists(self):
        assert re.search(r"private\s+string\s+_overlayText\s*=\s*\"\";", read(FACE_CLIENT))

    def test_control_shot_guard_catches_inline_recompute(self):
        """Контрольный выстрел: если текст вернуть в OnGUI, гвард обязан покраснеть."""
        mutated = "private void OnGUI() { GUI.Label(new Rect(0, 0, 10, 10), vrmLoader.LastBody.Describe()); }"
        assert "Describe()" in mutated, "мутация не воспроизводит прежний баг"
        assert "EventType.Repaint" not in mutated


# --------------------------------------------------------------------------- #
#  G. Мета VRM 1.0: enum'ы по спецификации, а не по памяти
# --------------------------------------------------------------------------- #
class TestVrmMetaSpec:
    """Пункт 3 части 1 и хвост очереди: гвард на enum'ы меты + контрольный выстрел."""

    SPEC_ENUMS = {
        "avatarPermission": {"onlyAuthor", "onlySeparatelyLicensedPerson", "everyone"},
        "commercialUsage": {"personalNonProfit", "personalProfit", "corporation"},
        "creditNotation": {"required", "unnecessary"},
        "modification": {"prohibited", "allowModification", "allowModificationRedistribution"},
    }

    def cube_meta(self) -> dict:
        data = SAMPLE_VRM.read_bytes()
        json_length = struct.unpack("<I", data[12:16])[0]
        gltf = json.loads(data[20 : 20 + json_length].decode("utf-8"))
        return gltf["extensions"]["VRMC_vrm"]["meta"]

    def test_enum_table_matches_the_spec(self):
        """Таблица в генераторе совпадает со спецификацией VRM 1.0 (meta.md)."""
        assert make_test_vrm.META_ENUMS == self.SPEC_ENUMS

    def test_shipped_cube_is_valid(self):
        meta = self.cube_meta()
        make_test_vrm.validate_meta(meta)  # не должно поднять
        assert meta["commercialUsage"] == "personalNonProfit"
        assert meta["creditNotation"] == "required"
        assert meta["modification"] == "prohibited"
        assert meta["allowRedistribution"] is False

    def test_cube_passes_full_validate(self):
        info = make_test_vrm.validate(SAMPLE_VRM.read_bytes())
        assert info["commercial_usage"] == "personalNonProfit"
        assert info["nodes"] == 58 and info["human_bones"] == 55

    def test_control_shot_personal_is_rejected(self):
        """Контрольный выстрел архитектора: куб с ``"personal"`` ОБЯЗАН падать."""
        meta = self.cube_meta()
        meta["commercialUsage"] = "personal"
        with pytest.raises(ValueError, match="commercialUsage"):
            make_test_vrm.validate_meta(meta)

    @pytest.mark.parametrize("field", sorted(SPEC_ENUMS))
    def test_control_shot_every_enum_field_guards_garbage(self, field):
        meta = self.cube_meta()
        for garbage in ("personal", "disallow", "allow", "PersonalNonProfit", ""):
            if garbage in self.SPEC_ENUMS[field]:
                continue
            broken = dict(meta)
            broken[field] = garbage
            with pytest.raises(ValueError):
                make_test_vrm.validate_meta(broken)

    @pytest.mark.parametrize("field,empty", [("name", ""), ("authors", []), ("licenseUrl", "")])
    def test_required_fields_are_required(self, field, empty):
        meta = self.cube_meta()
        meta[field] = empty
        with pytest.raises(ValueError):
            make_test_vrm.validate_meta(meta)

    def test_boolean_fields_must_be_boolean(self):
        meta = self.cube_meta()
        meta["allowRedistribution"] = "true"
        with pytest.raises(ValueError, match="boolean"):
            make_test_vrm.validate_meta(meta)


# --------------------------------------------------------------------------- #
#  H. Хвосты очереди: silero, HANDOVER, доки
# --------------------------------------------------------------------------- #
class TestQueueTails:
    """Часть 3 донесения — хвосты, которые стояли в моей очереди."""

    def test_silero_is_a_pip_extra_not_an_invented_pack(self):
        pyproject = read(PROJECT_ROOT / "pyproject.toml")
        voice = re.search(r'^voice = \[(.*)\]$', pyproject, re.M)
        assert voice, "нет extras [voice]"
        assert '"silero>=0.5"' in voice.group(1), "silero не добавлен в extras"
        assert "CC BY-NC" in pyproject, "лицензия моделей Silero не записана"
        packs = read(PROJECT_ROOT / "models" / "packs.yaml")
        assert "silero" in packs, "в packs.yaml нет объяснения, почему silero не пак"

    def test_handover_has_the_player_only_rule(self):
        handover = read(HANDOVER_MD)
        lowered = re.sub(r"\s+", " ", handover).lower()
        assert "player-only" in lowered and "только сборкой f7" in lowered, (
            "в HANDOVER нет правила про player-only #if-ветки"
        )
        assert "CS1061" in handover and "CS8361" in handover, "не названы классы ошибок"

    def test_scene_documents_player_settings(self):
        scene = read(SCENE_MD)
        assert "Use Flip Model Swapchain" in scene, "нет главного чекбокса прозрачности"
        assert "Fullscreen Window" in scene
        assert "Player Settings" in scene
        assert "Show Splash Screen" in scene, "пункт 5 донесения (сплэш) не закрыт доками"

    def test_readme_troubleshooting_covers_black_background(self):
        readme = read(PROJECT_ROOT / "README.md")
        assert "чёрный непрозрачный" in readme and "Use Flip Model Swapchain" in readme
        assert "Ctrl+Alt+Q" in readme, "не описано, как закрыть окно"
        assert "21 572 Б" in readme, "размер пересобранного куба не обновлён"

    def test_unity_readme_lists_new_features(self):
        readme = read(UNITY_README)
        for needle in ("F7", "Ctrl+Alt+Q", "Window Tool Window", "Repaint"):
            assert needle in readme, f"в unity README нет: {needle}"


# --------------------------------------------------------------------------- #
#  I. Оформление релиза
# --------------------------------------------------------------------------- #
class TestReleaseShape:
    """Отчёт, версия, CHANGELOG — по процессным правилам архитектора."""

    def test_version_is_three_part_and_bumped(self):
        sys.path.insert(0, str(PROJECT_ROOT / "src"))
        import lilith_core  # noqa: PLC0415

        assert re.match(r"^\d+\.\d+\.\d+$", lilith_core.__version__)
        assert lilith_core.__version__ == "0.6.6"

    def test_changelog_has_the_entry(self):
        changelog = read(PROJECT_ROOT / "CHANGELOG.md")
        assert "## 0.6.6" in changelog
        head = changelog.split("## Хотфикс 0.6.5")[0]
        for needle in ("Use Flip Model Swapchain", "windowToolWindow", "Ctrl+Alt+Q",
                       "personalNonProfit", "silero"):
            assert needle in head, f"в записи 0.6.6 нет: {needle}"

    def test_release_report_has_required_sections(self):
        assert RELEASE_MD.exists(), "нет RELEASE_0.6.6.md"
        report = read(RELEASE_MD)
        for needle in ("Причина", "Что сделано", "Тесты", "файл", "Что делать Кирюше", "Что дальше"):
            assert needle in report, f"в отчёте нет раздела «{needle}»"
        assert "| " in report, "тесты должны быть таблицей"

    def test_no_blocking_calls_regressed(self):
        """Регрессия 0.6.5: в клиенте лица не бывает блокирующих вызовов."""
        # NB: ``.Result`` здесь не запрещён — в 0.6.5 он легален ПОСЛЕ цикла ожидания
        # завершения задачи (гвард на это есть в test_hotfix_065.wait_loop_block).
        patterns = (r"\.Wait\s*\(", r"Task\.Wait(All|Any)\s*\(", r"\.Join\s*\(",
                    r"Monitor\.Enter", r"Thread\.Sleep", r"\.GetAwaiter\(\)\.GetResult\(\)")
        for path in ALL_CS:
            for line in code_lines(read(path)):
                for pattern in patterns:
                    assert not re.search(pattern, line), f"{path.name}: блокирующий вызов → {line[:80]}"

    def test_no_cross_thread_lock_in_face_client(self):
        """Регрессия 0.6.5: кросс-поточных мониторов в клиенте нет.

        Ищем форму оператора ``lock (`` по строкам кода: слово встречается и в
        комментариях про прежний баг, и в тексте красного лога (``livelock``).
        """
        for path in ALL_CS:
            code = "\n".join(code_lines(read(path)))
            assert "lock (" not in code and "lock(" not in code, f"{path.name}: вернулся lock"
