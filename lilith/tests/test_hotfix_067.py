"""Гварды 0.6.7 — «окно-питомец» (пожелания архитектора, пункты 1–6).

Замер Д закрыл этап 6 полностью: стекло принято (DwmExtendFrameIntoClientArea hr=0,
ключ #FF00FF принят, F7 живьём без ребилда), Ctrl+Alt+Q закрывает, shutdown чистый,
``--speak`` 20/20 дважды. Дальше архитектор прислал список «окно — питомец»:

1. драг за любую точку на Ctrl+Alt (``WM_NCHITTEST`` → ``HTCAPTION``);
2. Ctrl+Alt+стрелки — пошаговое смещение (``windowMoveStep``, дефолт 32 px);
3. F10 — цикл углов дока BR → BL → TR → TL, F9 прежний (правый нижний);
4. персист ручной позиции в ``data/window_state.json`` с приоритетом над
   ``dockBottomRight`` до первого F9/F10;
5. глобальные хоткеи ``RegisterHotKey`` (закрытие из игры без фокуса);
6. **критично**: F11 click-through (``WS_EX_TRANSPARENT``) — ТОЛЬКО в связке с п.5,
   иначе окно станет неубиваемым призраком.

Что из этого НЕ видно из песочницы (судит только сборка F7, правило ADR-025):
поведение Win32 живьём. Что видно и потому здесь защищено:

* **константы Windows** — VK-коды, WM-сообщения, стили: значения сверяются с
  документированной таблицей, а не на глаз. Именно так пойман баг этой саги:
  ``(uint)KeyCode.Q`` = 113 (Unity: ASCII строчных), а ``VK_Q`` = 81 (ASCII
  заглавных) — RegisterHotKey зарегистрировал бы НЕ ТУ клавишу, и это не видно
  ни чекеру (типов не знает), ни Editor (ветка вырезана). Теперь есть конвертер
  ``VirtualKeyOf`` и питоновская модель конвертера как контрольный выстрел;
* **чистая геометрия и JSON** — ``WindowStateStore.cs`` намеренно без Win32:
  углы дока, цикл, кламп и разбор файла проверяются структурно + мутантами;
* **дисциплина оконной процедуры** — никаких Unity API и диска из WndProc
  (урок 0.6.5: чужой такт), делегат в поле (собранный GC делегат = падение),
  снятие подкласса и UnregisterHotKey в OnDestroy;
* **предохранитель призрака** — click-through включается только когда ядро
  глобальных хоткеев живо И F11 зарегистрирован И конфиг разрешает; в файл
  состояния click-through не пишется НИКОГДА (каждый запуск — кликабельным);
* **опасный дефолт выключен** — ``globalArrowHotkeys = false`` (урок ADR-006:
  глобальные стрелки забирают сочетание у всей системы, игра их не получит).

Каждый критический гвард сопровождается контрольным выстрелом: тот же гвард
обязан падать на мутанте (чекер не всегда зелёный — иначе он бутафория).
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "unity-client" / "Assets" / "LilithFace" / "Scripts"
TRANSPARENT_WINDOW = SCRIPTS_DIR / "TransparentWindow.cs"
WINDOW_STORE = SCRIPTS_DIR / "WindowStateStore.cs"
CLIENT_CONFIG = SCRIPTS_DIR / "LilithClientConfig.cs"
FACE_CLIENT = SCRIPTS_DIR / "LilithFaceClient.cs"
ALL_CS = sorted(SCRIPTS_DIR.glob("*.cs"))
SCENE_MD = PROJECT_ROOT / "unity-client" / "Assets" / "LilithFace" / "SCENE.md"
UNITY_README = PROJECT_ROOT / "unity-client" / "Assets" / "LilithFace" / "README.md"
HANDOVER_MD = PROJECT_ROOT.parent / "HANDOVER.md"
RELEASE_MD = PROJECT_ROOT / "RELEASE_0.6.7.md"
DECISIONS_MD = PROJECT_ROOT / "docs" / "DECISIONS.md"
CHANGELOG_MD = PROJECT_ROOT / "CHANGELOG.md"
README_MD = PROJECT_ROOT / "README.md"


# --------------------------------------------------------------------------- #
#  Помощники 0.6.6 — переиспользуем, а не копируем (одна реализация на серию)
# --------------------------------------------------------------------------- #
def _load_helpers():
    path = PROJECT_ROOT / "tests" / "test_hotfix_066.py"
    spec = importlib.util.spec_from_file_location("lilith_tests_066_helpers", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


h066 = _load_helpers()
read = h066.read
code_lines = h066.code_lines
mask_literals = h066.mask_literals
block_of = h066.block_of
assert_logged = h066.assert_logged
flip_player_branch = h066._flip_player_branch
_load_script = h066._load_script


def squash(text: str) -> str:
    """Убрать все пробелы (сравниваем выражения, а не вёрстку)."""
    return re.sub(r"\s+", "", text)


def expr(text: str) -> str:
    """Текст без литералов/комментариев и без пробелов: чистые выражения C#."""
    return squash(mask_literals(text))


def mutant(text: str, old: str, new: str) -> str:
    """Контрольный выстрел: подменить ``old`` на ``new`` (и упасть, если игла уплыла)."""
    assert old in text, f"игла контрольного выстрела не найдена: {old[:60]!r}"
    return text.replace(old, new, 1)


def version_tuple(value: str) -> tuple[int, ...]:
    """``"0.6.7"`` → ``(0, 6, 7)``."""
    return tuple(int(part) for part in value.split("."))


TW = read(TRANSPARENT_WINDOW)
WS = read(WINDOW_STORE)
CFG = read(CLIENT_CONFIG)
FC = read(FACE_CLIENT)
TW_E = expr(TW)
WS_E = expr(WS)


# --------------------------------------------------------------------------- #
#  Таблицы истины: значения Windows и Unity, сверенные с документацией
# --------------------------------------------------------------------------- #
#: Виртуальные клавиши Windows (WinUser.h). F1=0x70, дальше подряд.
VK_TRUTH = {
    "VK_LEFT": "0x25",
    "VK_UP": "0x26",
    "VK_RIGHT": "0x27",
    "VK_DOWN": "0x28",
    "VK_F1": "0x70",
    "VK_F9": "0x78",
    "VK_F10": "0x79",
    "VK_F11": "0x7A",
}

#: Сообщения и константы оконных процедур (WinUser.h).
WM_TRUTH = {
    "WM_HOTKEY": "0x0312",
    "WM_NCHITTEST": "0x0084",
    "WM_EXITSIZEMOVE": "0x0232",
    "HTCAPTION": "2",
    "GWL_WNDPROC": "-4",
    "GWLP_WNDPROC": "-4",
}

#: Расширенные стили окна (WinUser.h).
EXSTYLE_TRUTH = {
    "WS_EX_TRANSPARENT": "0x00000020",
    "WS_EX_LAYERED": "0x00080000",
}

#: Модификаторы RegisterHotKey (WinUser.h): ALT=1, CONTROL=2.
MOD_TRUTH = {"MOD_ALT": "0x0001", "MOD_CONTROL": "0x0002"}

#: Unity KeyCode: буквы — ASCII строчных, F1 = 282, цифры — ASCII цифр.
UNITY_KEYCODES = {"A": 97, "Z": 122, "Q": 113, "Alpha0": 48, "Alpha9": 57, "F1": 282, "F15": 296}


def vk_of(keycode: int) -> int:
    """Питоновская модель ``VirtualKeyOf``: Unity KeyCode → VK Windows."""
    if UNITY_KEYCODES["A"] <= keycode <= UNITY_KEYCODES["Z"]:
        return keycode - 32  # 'a'(97) → 'A'(65) = VK_A
    if UNITY_KEYCODES["F1"] <= keycode <= UNITY_KEYCODES["F15"]:
        return keycode - UNITY_KEYCODES["F1"] + 0x70
    return keycode  # цифры и прочее совпадают без сдвига


# --------------------------------------------------------------------------- #
#  A. Пункт 1 — драг за любую точку на Ctrl+Alt (WM_NCHITTEST → HTCAPTION)
# --------------------------------------------------------------------------- #
def guard_subclass_installed(text: str) -> None:
    """Подкласс ставится в Apply, делегат держится в ПОЛЕ, снимается в OnDestroy."""
    apply_block = expr(block_of(text, "public void Apply()"))
    assert "SubclassWindow();" in apply_block, "Apply() не подклассирует окно"

    sub = expr(block_of(text, "private void SubclassWindow()"))
    assert "_wndProc=WndProc;" in sub, (
        "делегат WndProc не присвоен в поле: собранный GC делегат = падение процесса"
    )
    assert "GetWindowPointer(_hwnd,GWLP_WNDPROC)" in sub
    assert "Marshal.GetFunctionPointerForDelegate(_wndProc)" in sub
    assert re.search(r"private\s+WndProcDelegate\s+_wndProc\s*;", text), "поля делегата нет"

    unregister = expr(block_of(text, "private void UnregisterGlobalHotkeys()"))
    assert "SetWindowPointer(_hwnd,GWLP_WNDPROC,_originalWndProc)" in unregister, (
        "прежняя оконная процедура не возвращается — после смерти делегата процесс упадёт"
    )
    assert "_subclassed=false" in unregister
    destroy = expr(block_of(text, "private void OnDestroy()"))
    assert "UnregisterGlobalHotkeys();" in destroy, "OnDestroy не снимает хоткеи/подкласс"


def guard_drag_hit_test(text: str) -> None:
    """HTCAPTION — только когда держат ОБА модификатора, и не вместо служебных hit'ов."""
    wnd = expr(block_of(text, "private IntPtr WndProc"))
    assert "caseWM_NCHITTEST:" in wnd
    assert "varhit=CallWindowProc(_originalWndProc,hWnd,msg,wParam,lParam);" in wnd, (
        "сначала спрашиваем прежнюю процедуру — без модификаторов ответ штатный"
    )
    assert "if(HitTestIsCaption(hit)){returnnewIntPtr(HTCAPTION);}" in wnd

    hit = expr(block_of(text, "private bool HitTestIsCaption"))
    assert "if(_clickThrough||config==null||!config.dragWithCtrlAlt)" in hit, (
        "драг не gated конфигом/click-through"
    )
    assert "hit==newIntPtr(-1)||hit==IntPtr.Zero" in hit, (
        "HTTRANSPARENT/HTNOWHERE перехватывать нельзя — это не «внутри окна»"
    )
    assert "GetAsyncKeyState(0x11" in hit and "GetAsyncKeyState(0x12" in hit, (
        "состояние Ctrl/Alt спрашивается у системы (0x11=VK_CONTROL, 0x12=VK_MENU): "
        "у окна может не быть фокуса, Input.GetKey не годится"
    )
    assert "&0x8000" in hit, "не проверен старший бит GetAsyncKeyState (клавиша нажата)"
    assert "returncontrol&&alt;" in hit, "нужны ОБА модификатора: && а не ||"


def guard_wndproc_discipline(text: str) -> None:
    """Правила оконной процедуры: нет Unity API, нет диска, default — прежней процедуре."""
    for signature in ("private IntPtr WndProc", "private void OnGlobalHotkey"):
        # expr() гасит комментарии: упоминание Debug.Log в пояснении правилом не считается
        block = expr(block_of(text, signature))
        for banned in ("Debug.", "Input.", "Application.", "File.", "WindowStateStore."):
            assert banned not in block, f"{signature}: Unity API/диск из чужого такта ({banned})"
    wnd = expr(block_of(text, "private IntPtr WndProc"))
    assert "default:returnCallWindowProc(_originalWndProc,hWnd,msg,wParam,lParam);" in wnd, (
        "необработанные сообщения обязаны уходить прежней процедуре (IME, фокус, мышь)"
    )
    assert "caseWM_HOTKEY:OnGlobalHotkey(wParam.ToInt32());returnIntPtr.Zero;" in wnd
    # WM_EXITSIZEMOVE: только флаг, сохранение — в кадре Unity
    assert "_saveRequested=true;" in wnd
    assert "SavePositionNow(" not in wnd, "запись на диск из оконной процедуры запрещена"
    hotkey = expr(block_of(text, "private void OnGlobalHotkey"))
    assert "_pending.Enqueue(HotkeyAction." in hotkey, "WM_HOTKEY обязан только в очередь"
    assert "Application.Quit" not in hotkey


def guard_deferred_save_drained(text: str) -> None:
    """Флаг WM_EXITSIZEMOVE разбирается в Update (главный поток, кадр Unity)."""
    update = expr(block_of(text, "private void Update()"))
    assert "DrainDeferredSave();" in update
    drain = expr(block_of(text, "private void DrainDeferredSave()"))
    assert "if(!_saveRequested){return;}" in drain
    assert "_saveRequested=false;" in drain
    assert "SavePositionNow(" in drain


class TestDragByAnyPoint:
    """Пункт 1 пожеланий + дисциплина оконной процедуры (урок 0.6.5)."""

    def test_subclass_installed_and_removed(self):
        guard_subclass_installed(TW)

    def test_control_shot_local_delegate_would_die(self):
        """Контрольный выстрел: делегат локальной переменной — гвард падает."""
        broken = mutant(TW, "_wndProc = WndProc; // поле, а не локальная переменная: собранный GC делегат = падение процесса",
                        "var local = WndProc; _wndProc = _wndProc;")
        # после мутации присвоения поля нет — guard обязан упасть
        with pytest.raises(AssertionError):
            guard_subclass_installed(broken)

    def test_drag_needs_both_modifiers(self):
        guard_drag_hit_test(TW)

    def test_control_shot_or_instead_of_and(self):
        """Контрольный выстрел: ``||`` вместо ``&&`` — драг без Alt — гвард падает."""
        with pytest.raises(AssertionError):
            guard_drag_hit_test(mutant(TW, "return control && alt;", "return control || alt;"))

    def test_control_shot_httransparent_hijack(self):
        """Контрольный выстрел: убрана защита HTTRANSPARENT/HTNOWHERE — гвард падает."""
        broken = mutant(TW, """            if (hit == new IntPtr(-1) || hit == IntPtr.Zero)
            {
                return false;
            }

""", "")
        with pytest.raises(AssertionError):
            guard_drag_hit_test(broken)

    def test_htcaption_value_is_two(self):
        assert re.search(r"private\s+const\s+int\s+HTCAPTION\s*=\s*2\s*;", TW), (
            "HTCAPTION в WinUser.h = 2"
        )

    def test_wndproc_has_no_unity_api(self):
        guard_wndproc_discipline(TW)

    def test_control_shot_debug_log_in_wndproc(self):
        """Контрольный выстрел: Debug.Log внутри WndProc — гвард падает."""
        broken = mutant(TW, "                    OnGlobalHotkey(wParam.ToInt32());",
                        '                    Debug.Log("hotkey");\n                    OnGlobalHotkey(wParam.ToInt32());')
        with pytest.raises(AssertionError):
            guard_wndproc_discipline(broken)

    def test_control_shot_direct_save_from_wndproc(self):
        """Контрольный выстрел: SavePositionNow прямо из WM_EXITSIZEMOVE — гвард падает."""
        broken = mutant(TW, "                    _saveRequested = true;",
                        '                    SavePositionNow("из процедуры");')
        with pytest.raises(AssertionError):
            guard_wndproc_discipline(broken)

    def test_exitsizemove_save_is_drained_in_update(self):
        guard_deferred_save_drained(TW)

    def test_drag_config_default_on(self):
        assert re.search(r"public\s+bool\s+dragWithCtrlAlt\s*=\s*true\s*;", CFG)


# --------------------------------------------------------------------------- #
#  B. Пункт 2 — Ctrl+Alt+стрелки: пошаговое смещение
# --------------------------------------------------------------------------- #
def guard_nudge(text: str) -> None:
    """Шаг из конфига, кламп по рабочей области, сохранение позиции."""
    nudge = expr(block_of(text, "private void Nudge"))
    assert "Mathf.Max(1,config.windowMoveStep)" in nudge, "шаг из конфига, минимум 1 px"
    assert "WindowStateStore.MoveBy(current,dx*step,dy*step)" in nudge
    assert "ClampToScreen(moved,area.X,area.Y,area.Width,area.Height,KeepVisiblePx)" in nudge, (
        "после шага окно не выпускается за экран (абсолютные координаты монитора)"
    )
    assert "MoveWindowTo(clamped,true);" in nudge, (
        "стрелки — такое же ручное перемещение, как драг: позиция обязана сохраниться"
    )


def guard_arrow_mapping(text: str) -> None:
    """Направления стрелок: экранный y вниз, поэтому «вверх» = -1."""
    update = expr(block_of(text, "private void Update()"))
    expected = {
        "LeftArrow": ("MoveLeft", "MoveBy(-1,0);"),
        "RightArrow": ("MoveRight", "MoveBy(1,0);"),
        "UpArrow": ("MoveUp", "MoveBy(0,-1);"),
        "DownArrow": ("MoveDown", "MoveBy(0,1);"),
    }
    for key, (action, call) in expected.items():
        pattern = f"Input.GetKeyDown(KeyCode.{key})&&!HandledGloballyRecently(HotkeyAction.{action})"
        assert pattern in update, f"{key}: нет ветки с дедупликацией глобального хоткея"
        at = update.index(pattern)
        tail = update[at:at + len(pattern) + 40]
        assert call in tail, f"{key}: направление перепутано (ждём {call})"


class TestStepMove:
    """Пункт 2 пожеланий: windowMoveStep, дефолт 32 px."""

    def test_step_default_is_32(self):
        assert re.search(r"public\s+int\s+windowMoveStep\s*=\s*32\s*;", CFG), (
            "требование архитектора: дефолт 32 px"
        )

    def test_nudge_moves_clamps_and_saves(self):
        guard_nudge(TW)

    def test_control_shot_nudge_without_save(self):
        """Контрольный выстрел: стрелки не сохраняют позицию — гвард падает.

        Ровно этот баг и жил в первой редакции 0.6.7: ``MoveWindowTo(clamped, false)``
        при докстринге «и запомнить позицию». Пойман собственным гвардом.
        """
        with pytest.raises(AssertionError):
            guard_nudge(mutant(TW, "MoveWindowTo(clamped, true);", "MoveWindowTo(clamped, false);"))

    def test_arrow_directions_match_screen_axes(self):
        guard_arrow_mapping(TW)

    def test_control_shot_up_arrow_flipped(self):
        """Контрольный выстрел: «вверх» двигает вниз — гвард падает."""
        with pytest.raises(AssertionError):
            guard_arrow_mapping(mutant(TW, """                if (Input.GetKeyDown(KeyCode.UpArrow) && !HandledGloballyRecently(HotkeyAction.MoveUp))
                {
                    MoveBy(0, -1);
                }""", """                if (Input.GetKeyDown(KeyCode.UpArrow) && !HandledGloballyRecently(HotkeyAction.MoveUp))
                {
                    MoveBy(0, 1);
                }"""))

    def test_arrows_need_ctrl_alt_in_focus_path(self):
        update = block_of(TW, "private void Update()")
        assert "Input.GetKey(KeyCode.LeftControl) || Input.GetKey(KeyCode.RightControl)" in update
        assert "Input.GetKey(KeyCode.LeftAlt) || Input.GetKey(KeyCode.RightAlt)" in update

    def test_keep_visible_constant(self):
        assert re.search(r"private\s+const\s+int\s+KeepVisiblePx\s*=\s*48\s*;", TW), (
            "минимум 48 px окна на экране: невидимое окно нельзя ни перетащить, ни нажать"
        )

    def test_move_by_pure_function_keeps_size(self):
        """MoveBy не меняет размер — Reposition не увидит «изменения» и не дёрнет лишнего."""
        move = expr(block_of(WS, "public static WindowRect MoveBy"))
        assert "returnnewWindowRect(rect.X+dx,rect.Y+dy,rect.Width,rect.Height);" in move


# --------------------------------------------------------------------------- #
#  C. Пункт 3 — F10 цикл углов, F9 прежний; пункт 4 — приоритет файла
# --------------------------------------------------------------------------- #
def guard_corner_enum(text: str) -> None:
    """Порядок enum = порядок цикла F10 (требование: BR → BL → TR → TL)."""
    for name, value in (("BottomRight", 0), ("BottomLeft", 1), ("TopRight", 2), ("TopLeft", 3)):
        assert re.search(rf"{name}\s*=\s*{value}\s*,", text), (
            f"DockCorner.{name} обязан быть = {value}: порядок enum'а и есть цикл F10"
        )


def guard_dock_rect_math(text: str) -> None:
    """Геометрия углов: чистая функция, числа сверяются с моделью."""
    block = expr(block_of(text, "public static WindowRect DockRect"))
    assert "varw=Math.Max(MinSize,width);" in block and "varh=Math.Max(MinSize,height);" in block
    assert "varm=Math.Max(0,margin);" in block
    assert "varright=screenW-w-m;" in block and "varbottom=screenH-h-m;" in block
    corners = {
        "DockCorner.BottomLeft": "returnnewWindowRect(m,bottom,w,h);",
        "DockCorner.TopRight": "returnnewWindowRect(right,m,w,h);",
        "DockCorner.TopLeft": "returnnewWindowRect(m,m,w,h);",
    }
    for corner, ret in corners.items():
        at = block.index(f"case{corner}:")
        assert ret in block[at:at + 80], f"{corner}: прямоугольник не тот"
    at = block.index("default:")
    assert "returnnewWindowRect(right,bottom,w,h);" in block[at:at + 80], (
        "BottomRight (default) — правый нижний"
    )


def guard_dock_rect_model(text: str) -> None:
    """Контрольная модель: четыре угла на числах 1920×1080, окно 512×640, margin 24."""
    screen_w, screen_h, w, h, m = 1920, 1080, 512, 640, 24
    right, bottom = screen_w - w - m, screen_h - h - m
    model = {
        "BottomRight": (right, bottom),
        "BottomLeft": (m, bottom),
        "TopRight": (right, m),
        "TopLeft": (m, m),
    }
    block = expr(block_of(text, "public static WindowRect DockRect"))
    argmap = {"m": m, "bottom": bottom, "right": right, "w": w, "h": h}
    for corner, (want_x, want_y) in model.items():
        if corner == "BottomRight":
            at = block.index("default:")
        else:
            at = block.index(f"caseDockCorner.{corner}:")
        ret = re.search(r"returnnewWindowRect\((\w+),(\w+),(\w+),(\w+)\);", block[at:at + 90])
        assert ret, f"{corner}: return не найден"
        got = tuple(argmap[g] for g in ret.groups()[:2])
        assert got == (want_x, want_y), f"{corner}: ждали {want_x},{want_y}, в коде {got}"


def guard_next_corner_wraps(text: str) -> None:
    block = expr(block_of(text, "public static DockCorner NextCorner"))
    assert "Enum.GetValues" in block
    assert "returnvalues[(index+1)%values.Length];" in block, "цикл обязан замкнуться TL → BR"
    assert "if(index<0)" in block and "index=0;" in block, "мусорный угол → BR, а не исключение"


def guard_dock_hotkeys(text: str) -> None:
    """F9 — всегда BR; F10 — следующий угол; оба ставят флаг «хозяин положил сам»."""
    f9 = expr(block_of(text, "public void DockBottomRight()"))
    assert "DockTo(DockCorner.BottomRight,true);" in f9

    f10 = expr(block_of(text, "public void CycleDockCorner()"))
    assert "WindowStateStore.NextCorner(_corner)" in f10
    assert "DockTo(next,true);" in f10
    assert_logged(block_of(text, "public void CycleDockCorner()"), "F10: угол дока", "лог цикла углов")

    dock = expr(block_of(text, "private void DockTo"))
    assert "_corner=corner;" in dock
    assert "_dockPressedThisSession=true;" in dock, (
        "после F9/F10 файл больше не приоритет — хозяин положил окно сам (пункт 4)"
    )
    assert "WindowStateStore.DockRect(corner,area.Width,area.Height,width,height,config.windowMargin)" in dock
    assert "Absolute(relative,area)" in dock, "без сдвига на начало рабочей области док сломается на втором мониторе"
    assert "if(force){SavePositionNow(" in dock

    update = expr(block_of(text, "private void Update()"))
    assert ("Input.GetKeyDown(KeyCode.F9)&&!HandledGloballyRecently(HotkeyAction.DockBottomRight)"
            in update)
    assert "Input.GetKeyDown(KeyCode.F10)&&!HandledGloballyRecently(HotkeyAction.CycleDock)" in update


def guard_target_rect_priority(text: str) -> None:
    """Пункт 4: файл > dockBottomRight, но только до первого F9/F10 в этой сессии."""
    target = expr(block_of(text, "private WindowRect TargetRect"))
    assert ("_state!=null&&_state.HasPosition&&!_dockPressedThisSession"
            "&&(config==null||config.restoreWindowPosition)") in target, (
        "приоритет сохранённой позиции сломан"
    )
    assert "ClampToScreen(manual,area.X,area.Y,area.Width,area.Height,KeepVisiblePx)" in target, (
        "позиция из файла прогоняется через кламп: монитор мог стать другим"
    )
    assert "if(!dockBottomRight){returnCurrentRect();}" in target.replace(" ", ""), (
        "поведение 0.6.6 сохранено: док выключен — окно остаётся где стоит"
    )
    assert "WindowStateStore.DockRect(_corner," in target and "Absolute(corner,area)" in target


def guard_work_area_absolute(text: str) -> None:
    """Рабочая область — в АБСОЛЮТНЫХ координатах (второй монитор слева: x отрицательный)."""
    work = expr(block_of(text, "private WindowRect WorkArea"))
    assert "MonitorFromWindow(_hwnd,1" in work, "MONITOR_DEFAULTTONEAREST = 1"
    assert "GetMonitorInfo(monitor,refinfo)" in work
    assert "info.rcWork.left" in work and "info.rcWork.top" in work
    assert "info.rcWork.right-originX" in work and "info.rcWork.bottom-originY" in work
    assert "GetSystemMetrics(SM_CXSCREEN)" in work, "фолбэк на метрики основного экрана"
    absolute = expr(block_of(text, "private static WindowRect Absolute"))
    assert "returnnewWindowRect(relative.X+area.X,relative.Y+area.Y,relative.Width,relative.Height);" in absolute


class TestDockCorners:
    """Пункты 3 и 4 пожеланий: углы, цикл, приоритет сохранённой позиции."""

    def test_enum_order_is_the_f10_cycle(self):
        guard_corner_enum(WS)

    def test_control_shot_enum_reordered(self):
        """Контрольный выстрел: порядок enum сбит — цикл F10 пошёл не по спеке."""
        with pytest.raises(AssertionError):
            guard_corner_enum(mutant(WS, "BottomLeft = 1,", "BottomLeft = 5,"))

    def test_dock_rect_math(self):
        guard_dock_rect_math(WS)

    def test_dock_rect_matches_model(self):
        guard_dock_rect_model(WS)

    def test_control_shot_top_right_at_bottom(self):
        """Контрольный выстрел: TopRight поставлен вниз — модель ловит."""
        broken = mutant(WS, "return new WindowRect(right, m, w, h);",
                        "return new WindowRect(right, bottom, w, h);")
        with pytest.raises(AssertionError):
            guard_dock_rect_model(broken)

    def test_next_corner_wraps_around(self):
        guard_next_corner_wraps(WS)

    def test_control_shot_cycle_does_not_wrap(self):
        broken = mutant(WS, "return values[(index + 1) % values.Length];",
                        "return values[index + 1];")
        with pytest.raises(AssertionError):
            guard_next_corner_wraps(broken)

    def test_f9_bottom_right_f10_cycle(self):
        guard_dock_hotkeys(TW)

    def test_target_rect_priority_over_dock(self):
        guard_target_rect_priority(TW)

    def test_control_shot_priority_lost(self):
        """Контрольный выстрел: флаг сессии убран — файл правит вечно, F9/F10 сломаны."""
        broken = mutant(TW, "_state != null && _state.HasPosition && !_dockPressedThisSession",
                        "_state != null && _state.HasPosition")
        with pytest.raises(AssertionError):
            guard_target_rect_priority(broken)

    def test_work_area_is_absolute(self):
        guard_work_area_absolute(TW)

    def test_initial_corner_config(self):
        assert re.search(r"public\s+DockCorner\s+initialDockCorner\s*=\s*DockCorner\.BottomRight\s*;", CFG)


# --------------------------------------------------------------------------- #
#  D. Пункт 4 — персист: data/window_state.json (чистые функции + JSON)
# --------------------------------------------------------------------------- #
def guard_store_is_pure(text: str) -> None:
    """WindowStateStore — без Win32 и MonoBehaviour: его судит песочница, а не F7."""
    assert "DllImport" not in text, "в WindowStateStore завёлся P/Invoke — чистота сломана"
    assert "user32" not in text and "kernel32" not in text
    assert "staticclassWindowStateStore" in expr(text)
    assert "MonoBehaviour" not in text


def guard_clamp_math(text: str) -> None:
    block = expr(block_of(text, "public static WindowRect ClampToScreen"))
    assert "varkeep=Math.Max(16,keepVisible);" in block
    assert "varminX=originX+keep-rect.Width;" in block, "кламп без начала экрана ломает второй монитор"
    assert "varmaxX=originX+screenW-keep;" in block
    assert "varminY=originY+keep-rect.Height;" in block
    assert "varmaxY=originY+screenH-keep;" in block
    assert "varx=Math.Min(Math.Max(rect.X,minX),Math.Max(minX,maxX));" in block, (
        "внутренний Math.Max(minX, maxX) — защита от окна больше экрана"
    )


def guard_state_path(text: str) -> None:
    assert 'public const string FileName = "window_state.json";' in text
    assert 'public const string DataDir = "data";' in text
    default_path = expr(block_of(text, "public static string DefaultPath"))
    assert "if(!string.IsNullOrEmpty(explicitPath)){returnexplicitPath;}" in default_path, (
        "явный путь из конфига важнее каталога Unity"
    )
    assert "Application.persistentDataPath" in default_path
    assert "Path.Combine(Path.Combine(root,DataDir),FileName)" in default_path


def guard_json_shape(text: str) -> None:
    """Схема version=1, инвариантная культура (русская локаль: запятая вместо точки)."""
    to_json = block_of(text, "public static string ToJson").replace('\\"', '"')
    for key in ('"version": ', '"x": ', '"y": ', '"width": ', '"height": ',
                '"dockCorner": "', '"savedAtUtc": "'):
        assert key in to_json, f"в JSON нет поля {key}"
    assert to_json.count("CultureInfo.InvariantCulture") >= 5, (
        "числа и дата пишутся в инвариантной культуре — иначе ToString на русской "
        "локали даст запятую и файл не разберётся"
    )
    assert re.search(r"public\s+const\s+int\s+CurrentVersion\s*=\s*1\s*;", text)


def guard_parse_is_defensive(text: str) -> None:
    """Любая беда → null: окно не имеет права не запускаться из-за своего же файла."""
    parse = expr(block_of(text, "public static WindowState Parse"))
    # NB: mask_literals гасит содержимое символьных литералов: '{' → ''
    assert "if(string.IsNullOrEmpty(json)||json.IndexOf('')<0){returnnull;}" in parse
    assert "if(version!=WindowState.CurrentVersion){returnnull;}" in parse, (
        "чужая версия схемы не угадывается — начинаем с чистого листа"
    )
    assert "if(x==int.MinValue||y==int.MinValue){returnnull;}" in parse, (
        "нет позиции — доверять нечему (HasPosition отличает «мусор» от «нет файла»)"
    )
    assert "Enum.Parse(typeof(DockCorner),cornerName,true)" in parse
    assert "catch(ArgumentException)" in parse and "corner=DockCorner.BottomRight;" in parse

    load = expr(block_of(text, "public static WindowState Load"))
    assert "File.Exists(path)" in load and "catch(Exception" in load
    save = expr(block_of(text, "public static bool Save"))
    assert "!state.HasPosition" in save, "позиции нет — писать нечего"
    assert "Directory.CreateDirectory(dir)" in save
    assert "newUTF8Encoding(false)" in save, "без BOM: лишние байты сломают разбор"
    assert "catch(Exception" in save and "returnfalse;" in save


def guard_key_lookup_needs_colon(text: str) -> None:
    """``"x"`` не должно находиться внутри ``"maxWidth"``: ищем пару ключ+двоеточие."""
    lookup = block_of(text, "private static int IndexOfKey")
    assert re.search(r"json\[cursor\]\s*==\s*':'", lookup), (
        "без проверки двоеточия FindInt найдёт хвост чужого ключа"
    )


def guard_click_through_never_persisted(store_text: str, tw_text: str) -> None:
    """Сквозной режим в файл не пишется НИКОГДА: каждый запуск начинается кликабельным."""
    assert "clickthrough" not in store_text.lower().replace("-", "").replace("_", ""), (
        "в WindowStateStore проник click-through"
    )
    save_now = block_of(tw_text, "private void SavePositionNow")
    assert "_clickThrough" not in save_now, "SavePositionNow пишет click-through в файл"
    state_fields = block_of(store_text, "public sealed class WindowState")
    assert "ClickThrough" not in state_fields


class TestPersistence:
    """Пункт 4 пожеланий: чистая геометрия, JSON version=1, никаких призраков в файле."""

    def test_store_is_pure(self):
        guard_store_is_pure(WS)

    def test_clamp_keeps_window_on_screen(self):
        guard_clamp_math(WS)

    def test_control_shot_clamp_without_origin(self):
        """Контрольный выстрел: кламп забыл начало экрана — второй монитор сломан."""
        with pytest.raises(AssertionError):
            guard_clamp_math(mutant(WS, "var minX = originX + keep - rect.Width;",
                                    "var minX = keep - rect.Width;"))

    def test_state_file_name_and_path(self):
        guard_state_path(WS)

    def test_json_schema_and_culture(self):
        guard_json_shape(WS)

    def test_parse_rejects_garbage(self):
        guard_parse_is_defensive(WS)

    def test_control_shot_parse_trusts_any_version(self):
        broken = mutant(WS, "            if (version != WindowState.CurrentVersion)",
                        "            if (false)")
        with pytest.raises(AssertionError):
            guard_parse_is_defensive(broken)

    def test_key_lookup_is_colon_anchored(self):
        guard_key_lookup_needs_colon(WS)

    def test_click_through_never_persisted(self):
        guard_click_through_never_persisted(WS, TW)

    def test_control_shot_clickthrough_in_state(self):
        """Контрольный выстрел: кто-то добавил click-through в состояние — гвард падает."""
        broken = mutant(WS, "        public DockCorner Corner = DockCorner.BottomRight;",
                        "        public DockCorner Corner = DockCorner.BottomRight;\n\n        public bool ClickThrough;")
        with pytest.raises(AssertionError):
            guard_click_through_never_persisted(broken, TW)

    def test_restore_reads_file_once_at_start(self):
        restore = expr(block_of(TW, "private void RestorePersisted()"))
        assert "WindowStateStore.Load(path)" in restore
        assert "if(config==null||!config.restoreWindowPosition){return;}" in restore
        assert "_corner=_state.Corner;" in restore and "_positionFromState=true;" in restore
        assert "_corner=config.initialDockCorner;" in restore, "нет файла — угол из конфига"
        apply_block = expr(block_of(TW, "public void Apply()"))
        assert "RestorePersisted();" in apply_block

    def test_save_called_on_every_manual_move(self):
        """Позиция сохраняется В МОМЕНТ перемещения: драг, стрелки, F9/F10."""
        assert "SavePositionNow(" in expr(block_of(TW, "private void DrainDeferredSave()"))
        move_to = expr(block_of(TW, "private void MoveWindowTo"))
        assert "if(save){SavePositionNow(" in move_to
        assert "MoveWindowTo(clamped,true);" in expr(block_of(TW, "private void Nudge"))
        assert "if(force){SavePositionNow(" in expr(block_of(TW, "private void DockTo"))

    def test_save_gated_by_config_and_size(self):
        save_now = expr(block_of(TW, "private void SavePositionNow"))
        assert "!config.saveWindowPosition" in save_now
        assert "current.Width<8||current.Height<8" in save_now, (
            "HWND ещё не обрёл размер — нечего сохранять"
        )
        state = block_of(TW, "private void SavePositionNow")
        for field in ("Version = WindowState.CurrentVersion", "X = current.X", "Y = current.Y",
                      "Width = current.Width", "Height = current.Height",
                      "Corner = _corner", "HasPosition = true"):
            assert field in state, f"в сохраняемом состоянии нет {field}"

    def test_resolve_size_falls_back_to_config(self):
        state_cls = expr(block_of(WS, "public sealed class WindowState"))
        assert "returnWidth>=64?Width:fallback;" in state_cls
        assert "returnHeight>=64?Height:fallback;" in state_cls

    def test_save_failure_does_not_crash_client(self):
        assert_logged(block_of(WS, "public static bool Save"), "window_state.json не записан",
                      "лог ошибки записи")
        assert_logged(block_of(WS, "public static WindowState Load"), "window_state.json не прочитан",
                      "лог ошибки чтения")


# --------------------------------------------------------------------------- #
#  E. Пункт 5 — глобальные хоткеи RegisterHotKey
# --------------------------------------------------------------------------- #
def guard_win32_tables(text: str) -> None:
    """Константы Windows сверяются с документированной таблицей, а не на глаз."""
    flat = expr(text)
    for name, value in VK_TRUTH.items():
        assert re.search(rf"privateconstint{name}={re.escape(value)};", flat), (
            f"{name} обязан быть {value} (WinUser.h)"
        )
    for name, value in WM_TRUTH.items():
        assert re.search(rf"privateconstint{name}={re.escape(value)};", flat), (
            f"{name} обязан быть {value} (WinUser.h)"
        )
    for name, value in EXSTYLE_TRUTH.items():
        assert re.search(rf"privateconstuint{name}={re.escape(value)};", flat), (
            f"{name} обязан быть {value} (WinUser.h)"
        )
    for name, value in MOD_TRUTH.items():
        assert re.search(rf"privateconstuint{name}={re.escape(value)};", flat), (
            f"{name} обязан быть {value} (WinUser.h: ALT=1, CONTROL=2)"
        )


def guard_virtual_key_converter(text: str) -> None:
    """KeyCode → VK: буквы -32, F-ряд через VK_F1, прямой каст запрещён."""
    conv = expr(block_of(text, "private static uint VirtualKeyOf"))
    assert "code>=(int)KeyCode.A&&code<=(int)KeyCode.Z" in conv
    assert "return(uint)(code-32);" in conv, (
        "KeyCode — ASCII строчных ('a'=97), VK — заглавных ('A'=65): без -32 "
        "RegisterHotKey регистрирует не ту клавишу"
    )
    assert "code>=(int)KeyCode.F1&&code<=(int)KeyCode.F15" in conv
    assert "return(uint)(code-(int)KeyCode.F1+VK_F1);" in conv
    register = expr(block_of(text, "private void RegisterGlobalHotkeys"))
    assert "VirtualKeyOf(config.closeHotkey)" in register
    assert "(uint)config.closeHotkey" not in register, "прямой каст KeyCode вернулся"


def guard_hotkey_registration(text: str) -> None:
    """Ядро (выход + два дока) всегда; F11 — по конфигу; стрелки — только если попросят."""
    block = expr(block_of(text, "private void RegisterGlobalHotkeys"))
    assert block.index("_registeredHotkeys.Count>0") < block.index("!config.useGlobalHotkeys"), (
        "повторный Apply (F7/F8) молча выходит РАНЬШЕ проверки конфига — иначе лог "
        "врал бы «выключены конфигом» при уже зарегистрированных хоткеях"
    )
    assert "TryRegisterHotkey(HotkeyQuit,controlAlt,VirtualKeyOf(config.closeHotkey)" in block
    assert "TryRegisterHotkey(HotkeyDockCorner,0,VK_F9" in block, "F9 — без модификаторов"
    assert "TryRegisterHotkey(HotkeyCycleDock,0,VK_F10" in block, "F10 — без модификаторов"
    assert "varclickOk=!config.allowClickThrough||TryRegisterHotkey(HotkeyClickThrough,0,VK_F11" in block
    assert "if(config.globalArrowHotkeys)" in block, (
        "стрелки глобальны только по явному желанию (урок ADR-006)"
    )
    for i, vk in enumerate(("VK_LEFT", "VK_UP", "VK_RIGHT", "VK_DOWN")):
        assert f"TryRegisterHotkey(HotkeyArrowsBase+{i},controlAlt,{vk}" in block
    assert "varok=coreOk;" in block and "_globalHotkeysOk=ok;" in block, (
        "разрешение click-through даёт только ЯДРО: занятые стрелки/F11 не отнимают выход"
    )
    assert "_clickThroughAvailable=clickOk;" in block

    try_block = expr(block_of(text, "private bool TryRegisterHotkey"))
    assert "RegisterHotKey(_hwnd,id,modifiers,vk)" in try_block
    assert "_registeredHotkeys.Add(id);" in try_block
    assert_logged(block_of(text, "private bool TryRegisterHotkey"), "GetLastError",
                  "честный лог с кодом ошибки Windows")


def guard_hotkey_dispatch(text: str) -> None:
    """WM_HOTKEY → очередь; действия — в Update, в главном потоке, внутри кадра."""
    on_hotkey = expr(block_of(text, "private void OnGlobalHotkey"))
    cases = {
        "HotkeyQuit": "Quit", "HotkeyDockCorner": "DockBottomRight",
        "HotkeyCycleDock": "CycleDock", "HotkeyClickThrough": "ToggleClickThrough",
        "HotkeyArrowsBase+0": "MoveLeft", "HotkeyArrowsBase+1": "MoveUp",
        "HotkeyArrowsBase+2": "MoveRight", "HotkeyArrowsBase+3": "MoveDown",
    }
    for case, action in cases.items():
        assert f"case{case}:_pending.Enqueue(HotkeyAction.{action});" in on_hotkey.replace(" ", ""), (
            f"WM_HOTKEY id {case} не отображён в {action}"
        )

    drain = expr(block_of(text, "private void DrainGlobalHotkeys()"))
    assert "while(processed<16&&_pending.Count>0)" in drain, "очередь разбирается с пределом за кадр"
    assert "_lastGlobalAction=action;" in drain and "_lastGlobalAt=Time.realtimeSinceStartup;" in drain
    assert "RunHotkeyAction(action);" in drain
    update = expr(block_of(text, "private void Update()"))
    assert update.index("DrainGlobalHotkeys();") < update.index("Input.GetKeyDown"), (
        "сначала разбираем глобальные просьбы, потом опрашиваем Input"
    )

    run = expr(block_of(text, "private void RunHotkeyAction"))
    assert "caseHotkeyAction.Quit:RequestQuit();" in run
    assert "caseHotkeyAction.ToggleClickThrough:ToggleClickThrough();" in run


def guard_no_double_fire(text: str) -> None:
    """Окно в фокусе: глобальный хоткей уже сработал — Input-ветка обязана промолчать."""
    dedup = expr(block_of(text, "private bool HandledGloballyRecently"))
    assert ("return_lastGlobalAction==action&&Time.realtimeSinceStartup-_lastGlobalAt"
            "<GlobalDedupSeconds;") in dedup
    assert re.search(r"privateconstfloatGlobalDedupSeconds=0\.4f;", expr(text))
    update = expr(block_of(text, "private void Update()"))
    assert update.count("!HandledGloballyRecently(HotkeyAction.") == 8, (
        "все 8 Input-веток (F9, F10, F11, 4 стрелки, выход) защищены дедупликацией"
    )
    assert "varfocused=Application.isFocused;" in update


def guard_unregister_on_destroy(text: str) -> None:
    block = expr(block_of(text, "private void UnregisterGlobalHotkeys"))
    assert "foreach(varidin_registeredHotkeys){UnregisterHotKey(_hwnd,id);}" in block
    assert "_registeredHotkeys.Clear();" in block
    assert "_globalHotkeysOk=false;" in block, (
        "после снятия хоткеев click-through снова обязан стать недоступным"
    )


class TestGlobalHotkeys:
    """Пункт 5 пожеланий: RegisterHotKey — закрытие/док из игры без фокуса."""

    def test_win32_constants_match_documentation(self):
        guard_win32_tables(TW)

    def test_control_shot_wrong_vk_f11(self):
        """Контрольный выстрел: VK_F11 сдвинут на единицу — гвард падает."""
        with pytest.raises(AssertionError):
            guard_win32_tables(mutant(TW, "private const int VK_F11 = 0x7A;",
                                      "private const int VK_F11 = 0x7B;"))

    def test_virtual_key_converter(self):
        guard_virtual_key_converter(TW)

    def test_converter_model_matches_windows(self):
        """Питоновская модель конвертера: Q→81, A→65, F9→0x78, цифры без сдвига."""
        assert vk_of(UNITY_KEYCODES["Q"]) == 81 == 0x51  # VK_Q
        assert vk_of(UNITY_KEYCODES["A"]) == 65 == 0x41  # VK_A
        assert vk_of(UNITY_KEYCODES["Z"]) == 90 == 0x5A  # VK_Z
        assert vk_of(UNITY_KEYCODES["F1"]) == 0x70
        assert vk_of(UNITY_KEYCODES["F1"] + 8) == 0x78   # F9
        assert vk_of(UNITY_KEYCODES["F15"]) == 0x7E
        assert vk_of(UNITY_KEYCODES["Alpha0"]) == 48 == 0x30
        # и сам баг, ради которого конвертер существует:
        assert vk_of(UNITY_KEYCODES["Q"]) != UNITY_KEYCODES["Q"], (
            "прямой каст KeyCode.Q (113) ≠ VK_Q (81) — именно это и ломало регистрацию"
        )

    def test_control_shot_raw_cast_returns(self):
        """Контрольный выстрел: ``(uint)config.closeHotkey`` вернулся — гвард падает."""
        broken = mutant(TW, "VirtualKeyOf(config.closeHotkey)", "(uint)config.closeHotkey")
        with pytest.raises(AssertionError):
            guard_virtual_key_converter(broken)

    def test_registration_shape(self):
        guard_hotkey_registration(TW)

    def test_control_shot_arrows_global_by_default(self):
        """Контрольный выстрел: стрелки регистрируются без спроса — гвард падает."""
        broken = mutant(TW, "            if (config.globalArrowHotkeys)",
                        "            if (true)")
        with pytest.raises(AssertionError):
            guard_hotkey_registration(broken)

    def test_dispatch_is_queue_only(self):
        guard_hotkey_dispatch(TW)

    def test_control_shot_quit_from_wndproc(self):
        """Контрольный выстрел: Quit прямо из WM_HOTKEY — гвард падает (урок 0.6.5)."""
        broken = mutant(TW, "                case HotkeyQuit:\n                    _pending.Enqueue(HotkeyAction.Quit);",
                        "                case HotkeyQuit:\n                    Application.Quit();")
        with pytest.raises(AssertionError):
            guard_wndproc_discipline(broken)

    def test_no_double_fire(self):
        guard_no_double_fire(TW)

    def test_unregister_and_restore_on_destroy(self):
        guard_unregister_on_destroy(TW)

    def test_hotkey_ids_are_distinct(self):
        flat = expr(TW)
        ids = dict(re.findall(r"privateconstint(Hotkey\w+)=(\d+);", flat))
        assert ids == {"HotkeyQuit": "1", "HotkeyDockCorner": "2", "HotkeyCycleDock": "3",
                       "HotkeyClickThrough": "4", "HotkeyArrowsBase": "10"}, ids
        # id стрелок = база + 0..3: не пересекаются с ядром (1..4)

    def test_quit_happens_exactly_once_in_request_quit(self):
        assert len(re.findall(r"Application\.Quit\(", TW)) == 1, (
            "Application.Quit ровно один — в RequestQuit; остальные пути идут через него"
        )
        assert "Application.Quit()" in block_of(TW, "private void RequestQuit()")

    def test_use_global_hotkeys_default_on(self):
        assert re.search(r"public\s+bool\s+useGlobalHotkeys\s*=\s*true\s*;", CFG), (
            "решение архитектора: глобальные хоткеи дефолтно ВКЛ (стрелки — ВЫКЛ)"
        )

    def test_arrow_hotkeys_default_off_with_reason(self):
        assert re.search(r"public\s+bool\s+globalArrowHotkeys\s*=\s*false\s*;", CFG)
        assert "ADR-006" in CFG, "опасный дефолт выключен — и причина записана в тултипе"


# --------------------------------------------------------------------------- #
#  F. Пункт 6 — click-through: двойной предохранитель от неубиваемого призрака
# --------------------------------------------------------------------------- #
def guard_ghost_safety(text: str) -> None:
    """КРИТИЧНО: click-through только когда ядро хоткеев живо И F11 зарегистрирован."""
    block = expr(block_of(text, "private void ApplyClickThrough"))
    assert "if(want&&(config==null||!config.allowClickThrough))" in block, (
        "запрет конфигом проверяется в единственной воронке — иначе F11 в фокусе "
        "включит сквозной режим при Allow Click Through = ❌"
    )
    assert "if(want&&(!_globalHotkeysOk||!_clickThroughAvailable))" in block, (
        "двойной предохранитель: ядро хоткеев И регистрация F11"
    )
    assert_logged(block_of(text, "private void ApplyClickThrough"), "неубиваемым призраком",
                  "честное объяснение отказа в Player.log")


def guard_clickthrough_style_math(text: str) -> None:
    block = expr(block_of(text, "private void ApplyClickThrough"))
    assert "if(_clickThrough==want){return;" in block, (
        "Win32 только по изменению состояния (правило 0.6.6 — иначе рябь)"
    )
    assert "exStyle&=~WS_EX_TRANSPARENT;" in block
    assert "if(want){exStyle|=WS_EX_LAYERED|WS_EX_TRANSPARENT;}" in block, (
        "без WS_EX_LAYERED флаг WS_EX_TRANSPARENT мышь не пропускает"
    )
    assert "SetWindowPos(" in block and "SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE|SWP_FRAMECHANGED" in block


def guard_clickthrough_survives_mode_cycle(text: str) -> None:
    """F7 пересобирает стили: click-through не теряется и не наследуется случайно."""
    styles = expr(block_of(text, "private void ApplyStyles"))
    assert "exStyle&=~WS_EX_TRANSPARENT;" in styles, "сброс перед явной установкой"
    assert "if(mode==TransparencyMode.LayeredColorKey||_clickThrough){exStyle|=WS_EX_LAYERED;}" in styles
    assert "if(_clickThrough){exStyle|=WS_EX_TRANSPARENT;}" in styles


def guard_no_ghost_on_exit_paths(text: str) -> None:
    """Все пути «прочь из сквозного режима»: Quit и F8 снимают click-through."""
    request_quit = expr(block_of(text, "private void RequestQuit()"))
    assert "if(_clickThrough){ApplyClickThrough(false);}" in request_quit
    assert request_quit.index("ApplyClickThrough(false)") < request_quit.index("Application.Quit()"), (
        "сначала снять сквозной режим, потом выходить: Quit может быть отложен"
    )
    revert = expr(block_of(text, "public void Revert()"))
    assert "_clickThrough=false;" in revert, "F8 = снять всё: окно не должно остаться некликабельным"
    assert "exStyle&=~(WS_EX_LAYERED|WS_EX_TOPMOST|WS_EX_TRANSPARENT);" in revert


def guard_clickthrough_entry_points(text: str) -> None:
    toggle = expr(block_of(text, "public void ToggleClickThrough()"))
    assert "ApplyClickThrough(!_clickThrough);" in toggle
    update = expr(block_of(text, "private void Update()"))
    assert ("Input.GetKeyDown(KeyCode.F11)&&!HandledGloballyRecently(HotkeyAction.ToggleClickThrough)"
            in update)
    assert "focused&&Input.GetKeyDown(KeyCode.F11)" in update


class TestClickThroughSafety:
    """Пункт 6 пожеланий — «критично для игр», цена ошибки = неубиваемое окно."""

    def test_double_safety_gate(self):
        guard_ghost_safety(TW)

    def test_control_shot_core_gate_removed(self):
        """Контрольный выстрел: убрано ядро хоткеев из условия — призрак возможен."""
        broken = mutant(TW, "if (want && (!_globalHotkeysOk || !_clickThroughAvailable))",
                        "if (want && (!_globalHotkeysOk || !_clickThroughAvailable) && false")
        with pytest.raises(AssertionError):
            guard_ghost_safety(broken)

    def test_control_shot_f11_gate_removed(self):
        """Контрольный выстрел: проверка регистрации F11 убрана — гвард падает."""
        broken = mutant(TW, "if (want && (!_globalHotkeysOk || !_clickThroughAvailable))",
                        "if (want && !_globalHotkeysOk)")
        with pytest.raises(AssertionError):
            guard_ghost_safety(broken)

    def test_control_shot_config_gate_removed(self):
        """Контрольный выстрел: Allow Click Through = ❌ обходится — гвард падает."""
        broken = mutant(TW, "if (want && (config == null || !config.allowClickThrough))",
                        "if (want && false)")
        with pytest.raises(AssertionError):
            guard_ghost_safety(broken)

    def test_style_math_and_state_change_only(self):
        guard_clickthrough_style_math(TW)

    def test_survives_f7_mode_cycle(self):
        guard_clickthrough_survives_mode_cycle(TW)

    def test_exit_paths_leave_no_ghost(self):
        guard_no_ghost_on_exit_paths(TW)

    def test_control_shot_revert_keeps_transparent(self):
        """Контрольный выстрел: F8 оставил WS_EX_TRANSPARENT — мини-призрак."""
        broken = mutant(TW, "exStyle &= ~(WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TRANSPARENT);",
                        "exStyle &= ~(WS_EX_LAYERED | WS_EX_TOPMOST);")
        with pytest.raises(AssertionError):
            guard_no_ghost_on_exit_paths(broken)

    def test_entry_points(self):
        guard_clickthrough_entry_points(TW)

    def test_drag_disabled_while_click_through(self):
        """Сквозное окно не тащится: HitTestIsCaption закрыт флагом _clickThrough."""
        assert "_clickThrough||" in expr(block_of(TW, "private bool HitTestIsCaption"))

    def test_allow_click_through_default_on(self):
        assert re.search(r"public\s+bool\s+allowClickThrough\s*=\s*true\s*;", CFG)

    def test_describe_reports_state_honestly(self):
        describe = block_of(TW, "public string Describe()")
        for needle in ("угол", "click-through", "глоб. хоткеи"):
            assert needle in describe, f"оверлей молчит про {needle}"


# --------------------------------------------------------------------------- #
#  G. x86: GetWindowLongPtr в 32-битной user32.dll НЕ существует
# --------------------------------------------------------------------------- #
class TestX86Pointers:
    """Урок саги: на x86 ``GetWindowLongPtr`` нет вовсе — EntryPointNotFoundException."""

    def test_wrappers_branch_on_pointer_size(self):
        for signature, long_ptr, fallback in (
            ("private IntPtr GetWindowPointer", "GetWindowLongPtr64(hWnd,index)",
             "returnnewIntPtr(GetWindowLong(hWnd,index));"),
            ("private IntPtr SetWindowPointer", "SetWindowLongPtr64(hWnd,index,value)",
             "returnnewIntPtr(SetWindowLong(hWnd,index,value.ToInt32()));"),
        ):
            block = expr(block_of(TW, signature))
            assert "if(IntPtr.Size==8)" in block, f"{signature}: нет развилки x64/x86"
            assert long_ptr in block and fallback in block

    def test_entry_points_declared(self):
        assert 'EntryPoint = "GetWindowLongPtr"' in TW
        assert 'EntryPoint = "SetWindowLongPtr"' in TW

    def test_wndproc_uses_wrappers_not_raw(self):
        """Подкласс и снятие идут через развилку, а не прямым 64-битным вызовом."""
        for signature in ("private void SubclassWindow", "private void UnregisterGlobalHotkeys"):
            block = expr(block_of(TW, signature))
            assert "GetWindowLongPtr64(" not in block and "SetWindowLongPtr64(" not in block, (
                f"{signature}: прямой вызов …Ptr64 убьёт 32-битный билд"
            )
        assert "GetWindowPointer(" in expr(block_of(TW, "private void SubclassWindow"))

    def test_gwlp_wndproc_value(self):
        assert re.search(r"private\s+const\s+int\s+GWLP_WNDPROC\s*=\s*-4\s*;", TW)


# --------------------------------------------------------------------------- #
#  H. Конфиг: восемь новых полей, дефолты и достижимость каждого
# --------------------------------------------------------------------------- #
class TestConfigShape:
    """Пункты 1–6 в инспекторе: дефолты по решению архитектора."""

    @pytest.mark.parametrize(
        "field,default",
        [
            ("windowMoveStep", "32"),
            ("dragWithCtrlAlt", "true"),
            ("useGlobalHotkeys", "true"),
            ("globalArrowHotkeys", "false"),
            ("allowClickThrough", "true"),
            ("saveWindowPosition", "true"),
            ("restoreWindowPosition", "true"),
            ("windowStatePath", '""'),
            ("initialDockCorner", "DockCorner.BottomRight"),
        ],
    )
    def test_field_default(self, field, default):
        assert re.search(
            rf"public\s+[\w<>]+\s+{field}\s*=\s*{re.escape(default)}\s*;", CFG
        ), f"поле {field} отсутствует или дефолт не {default}"

    @pytest.mark.parametrize(
        "field,consumer",
        [
            ("dragWithCtrlAlt", "private bool HitTestIsCaption"),
            ("windowMoveStep", "private void Nudge"),
            ("useGlobalHotkeys", "private void RegisterGlobalHotkeys"),
            ("globalArrowHotkeys", "private void RegisterGlobalHotkeys"),
            ("allowClickThrough", "private void ApplyClickThrough"),
            ("saveWindowPosition", "private void SavePositionNow"),
            ("restoreWindowPosition", "private void RestorePersisted()"),
            ("windowStatePath", "private string WindowStatePath()"),
            ("initialDockCorner", "private void RestorePersisted()"),
        ],
    )
    def test_field_actually_reaches_code(self, field, consumer):
        """Поле в инспекторе обязано что-то делать: мертвые настройки — обман хозяина."""
        assert f"config.{field}" in block_of(TW, consumer), (
            f"{field} не используется в {consumer}"
        )

    def test_state_field_declarations_outside_player_branch(self):
        """Поля состояния объявлены ДО ``#if``: Describe()/Update() живут и в Editor."""
        first_if = TW.index("#if UNITY_STANDALONE_WIN && !UNITY_EDITOR")
        head = TW[:first_if]
        for field in ("_corner", "_state", "_positionFromState", "_dockPressedThisSession",
                      "_clickThrough", "_globalHotkeysOk", "_clickThroughAvailable",
                      "_quitRequested", "_saveRequested", "_pending",
                      "_lastGlobalAt", "_lastGlobalAction"):
            assert re.search(rf"private\s+(?:readonly\s+)?[\w<>]+\s+{field}\b", head), (
                f"поле {field} спрятано в player-only ветку — Editor-код его не увидит"
            )

    def test_editor_branch_sees_no_win32(self):
        """Вторая ветка (то, что компилирует Editor): весь Win32 вырезан."""
        flipped = expr(flip_player_branch(TW))
        assert "DllImport" not in flipped
        assert "RegisterHotKey" not in flipped
        assert "publicstringDescribe()" in flipped, "Describe() обязан работать в Editor"
        assert "privatevoidUpdate()" in flipped
        assert "DrainGlobalHotkeys();" in flipped


# --------------------------------------------------------------------------- #
#  I. Клиент и оверлей: строка состояния окна
# --------------------------------------------------------------------------- #
class TestClientOverlay:
    def test_component_is_found_not_created(self):
        assert "GetComponent<TransparentWindow>()" in FC
        assert "AddComponent<TransparentWindow>" not in FC, (
            "компонент принадлежит сцене: клиент не порождает окно самовольно"
        )

    def test_overlay_line_from_describe(self):
        assert "transparentWindow.Describe()" in FC

    def test_describe_compiles_in_editor(self):
        flipped = flip_player_branch(TW)
        assert "угол" in flipped and "click-through" in flipped, (
            "Describe() собирается из полей вне #if — иначе Editor-оверлей не соберётся"
        )


# --------------------------------------------------------------------------- #
#  J. Чекер: все 12 файлов чисты во всех четырёх ветках условной компиляции
# --------------------------------------------------------------------------- #
class TestCheckerBranches:
    """Player-only ветки судит сборка F7, но синтаксис — чекер во всех ветках (ADR-025)."""

    @pytest.fixture(scope="class")
    def checker(self):
        try:
            import tree_sitter  # noqa: F401
            import tree_sitter_c_sharp  # noqa: F401
        except ImportError as exc:  # pragma: no cover - зависит от окружения
            pytest.skip(f"tree-sitter не установлен: {exc}")
        return _load_script("check_csharp_syntax", "067chk")

    def test_new_file_is_in_scope(self):
        names = [p.name for p in ALL_CS]
        assert "WindowStateStore.cs" in names
        assert len(names) == 12, f"ожидали 12 файлов Scripts/, нашли {len(names)}: {names}"

    def test_repo_clean_under_all_four_defines(self, checker):
        parser = checker.build_parser()
        problems = []
        for path in ALL_CS:
            for defined in h066.TestCheckerRuleCs8361.DEFINE_SETS:
                problems.extend(f"[{','.join(sorted(defined)) or 'пусто'}] {p}"
                                for p in checker.check_file(path, parser, set(defined)))
        assert not problems, "чекер нашёл ошибки:\n" + "\n".join(problems)

    def test_no_cs8361_in_new_code(self):
        """Независимый сканер 0.6.6: тернарник в интерполяции без скобок."""
        for path in (TRANSPARENT_WINDOW, WINDOW_STORE, CLIENT_CONFIG, FACE_CLIENT):
            found = h066.find_cs8361(read(path))
            assert not found, f"{path.name}: CS8361 → {found}"


# --------------------------------------------------------------------------- #
#  K. Документация и оформление релиза
# --------------------------------------------------------------------------- #
class TestDocs:
    """Хоткеи и поля, которых нет в доках, для Кирюши не существуют."""

    def test_scene_documents_hotkeys_and_fields(self):
        scene = read(SCENE_MD)
        for needle in ("F10", "F11", "Ctrl+Alt+стрелки", "Window Move Step",
                       "Use Global Hotkeys", "Global Arrow Hotkeys", "Allow Click Through",
                       "Save Window Position", "Restore Window Position", "Initial Dock Corner",
                       "window_state.json", "HTCAPTION"):
            assert needle in scene, f"в SCENE.md не описано: {needle}"

    def test_unity_readme_lists_new_features(self):
        text = read(UNITY_README)
        for needle in ("0.6.7", "click-through", "RegisterHotKey"):
            assert needle in text, f"в unity-client README нет: {needle}"

    def test_root_readme_bumped(self):
        text = read(README_MD)
        assert "0.6.7" in text
        for needle in ("F10", "F11"):
            assert needle in text, f"в README нет хоткея {needle}"

    def test_handover_mentions_release(self):
        assert "0.6.7" in read(HANDOVER_MD)

    def test_decisions_has_adr_026(self):
        text = read(DECISIONS_MD)
        assert "ADR-026" in text
        head = text[text.index("ADR-026"):]
        for needle in ("RegisterHotKey", "WS_EX_TRANSPARENT", "неубиваем",
                       "window_state.json", "HTCAPTION", "globalArrowHotkeys"):
            assert needle in head, f"в ADR-026 нет: {needle}"


class TestReleaseShape067:
    """Отчёт, версия, CHANGELOG — по процессным правилам архитектора."""

    def test_version_is_three_part_and_bumped(self):
        sys.path.insert(0, str(PROJECT_ROOT / "src"))
        import lilith_core  # noqa: PLC0415

        assert re.match(r"^\d+\.\d+\.\d+$", lilith_core.__version__)
        # Монотонно, без пина «==»: пин краснеет на каждом следующем релизе (урок 0.6.6)
        assert version_tuple(lilith_core.__version__) >= (0, 6, 7)

    def test_changelog_has_the_entry(self):
        changelog = read(CHANGELOG_MD)
        assert "## 0.6.7" in changelog
        head = changelog.split("## 0.6.6")[0]
        for needle in ("windowMoveStep", "RegisterHotKey", "WS_EX_TRANSPARENT",
                       "window_state.json", "HTCAPTION", "VirtualKeyOf", "F10", "F11"):
            assert needle in head, f"в записи 0.6.7 нет: {needle}"

    def test_release_report_has_required_sections(self):
        assert RELEASE_MD.exists(), "нет RELEASE_0.6.7.md"
        report = read(RELEASE_MD)
        for needle in ("Причина", "Что сделано", "Тесты", "файл",
                       "Что делать Кирюше", "Что дальше"):
            assert needle in report, f"в отчёте нет раздела «{needle}»"
        assert "| " in report, "тесты должны быть таблицей"

    def test_release_report_explains_the_vk_bug(self):
        """Баг VK-кодов — лицо этого хотфикса: он обязан быть в отчёте честно."""
        report = read(RELEASE_MD)
        for needle in ("VK_Q", "113", "81"):
            assert needle in report, f"в отчёте не объяснён баг конвертера ({needle})"

    def test_artifact_manifest_includes_store(self):
        script = read(PROJECT_ROOT / "scripts" / "verify_stage_artifact.py")
        assert "WindowStateStore.cs" in script, (
            "новый обязательный файл не добавлен в манифест артефакта"
        )

    def test_structure_requires_release_file(self):
        script = read(PROJECT_ROOT / "tests" / "test_structure.py")
        assert "RELEASE_0.6.7.md" in script

    def test_server_python_untouched(self):
        """Баг клиентский — серверный Python не тронут (процессное правило)."""
        # Ни один новый импорт/модуль сервера в этом хотфиксе не появился:
        # проверяем, что face-пакет не ссылается на оконные сущности клиента.
        face_dir = PROJECT_ROOT / "src" / "lilith_core" / "face"
        for path in sorted(face_dir.glob("*.py")):
            text = read(path)
            assert "window_state" not in text, (
                f"{path.name}: сервер знает про файл окна клиента — связность нарушена"
            )
