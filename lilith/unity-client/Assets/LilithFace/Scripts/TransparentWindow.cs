// LILITH-CORE · Unity-клиент лица (этап 6)
// Прозрачное окно под OBS (решение C4-г: переключатель в инспекторе, дефолт DWM).
//
// Требование архитектора: окно живёт на рабочем столе СПРАВА СНИЗУ и обязано быть
// прозрачным не только в OBS, но и на десктопе. Поэтому режимы:
//   Dwm             — DwmExtendFrameIntoClientArea + margin(-1): нативная альфа, дефолт;
//   LayeredColorKey — WS_EX_LAYERED + SetLayeredWindowAttributes: цветовой ключ (фолбэк);
//   Off             — ничего не делаем: OBS сам вырежет фон Color Key'ем.
//
// Работает только в Windows Standalone (не в Editor: там окно — Game View).
// В Editor компонент просто логирует, что прозрачность включится в билде.
//
// 0.6.7 — окно-питомец (ADR-026): «сидит там, куда положил хозяин, и не мешает ему играть».
//   • драг за любую точку окна на Ctrl+Alt (WM_NCHITTEST → HTCAPTION);
//   • Ctrl+Alt+стрелки — пошаговое смещение (windowMoveStep);
//   • F9 — в правый нижний угол (прежнее), F10 — цикл углов BR → BL → TR → TL;
//   • ручная позиция переживает перезапуск: data/window_state.json;
//   • глобальные хоткеи (RegisterHotKey): работают БЕЗ фокуса, то есть из игры;
//   • F11 — click-through (WS_EX_TRANSPARENT): клики проходят сквозь окно в игру.
//     Включается ТОЛЬКО если глобальные хоткеи зарегистрированы, иначе окно
//     стало бы неубиваемым призраком (его нечем ни выключить, ни закрыть).

using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using UnityEngine;

namespace Lilith.Face
{
    /// <summary>
    /// Делает окно билда прозрачным и ставит его в правый нижний угол экрана.
    /// </summary>
    public class TransparentWindow : MonoBehaviour
    {
        [Tooltip("Конфиг клиента (режим прозрачности, размер, отступы).")]
        public LilithClientConfig config;

        [Tooltip("Применять прозрачность на старте.")]
        public bool applyOnStart = true;

        [Tooltip("Сделать окно «всегда поверх» (удобно для стрима).")]
        public bool topmost = true;

        [Tooltip("Убрать рамку окна (borderless): OBS захватывает чистый квадрат.")]
        public bool borderless = true;

        [Tooltip("Камера, у которой чистим фон (нужна для DWM/color key).")]
        public Camera transparentCamera;

        private IntPtr _hwnd = IntPtr.Zero;
        private bool _applied;

        // -- состояние окна (0.6.7) ------------------------------------------- //
        // Поля объявлены ВНЕ #if-ветки: их читают Describe() и Update(), которые
        // компилируются и в Editor. В Editor они просто остаются дефолтными.

        /// <summary>Текущий угол дока (F10 крутит его, F9 всегда ставит BottomRight).</summary>
        private DockCorner _corner = DockCorner.BottomRight;

        /// <summary>Ручная позиция из data/window_state.json (null — файла нет/он битый).</summary>
        private WindowState _state;

        /// <summary>Позиция взята из файла, а не из дока конфига (для оверлея и лога).</summary>
        private bool _positionFromState;

        /// <summary>В этой сессии нажимали F9/F10 — значит хозяин положил окно сам.</summary>
        private bool _dockPressedThisSession;

        /// <summary>Клики проходят сквозь окно (WS_EX_TRANSPARENT).</summary>
        private bool _clickThrough;

        /// <summary>Глобальные хоткеи «ядра» зарегистрированы (без них click-through запрещён).</summary>
        private bool _globalHotkeysOk;

        /// <summary>F11 доехал до RegisterHotKey (или click-through запрещён конфигом).</summary>
        private bool _clickThroughAvailable;

        /// <summary>Глобальный хоткей попросил выход: Quit выполняется из Update (главный поток).</summary>
        private bool _quitRequested;

        /// <summary>WM_EXITSIZEMOVE пришёл: позицию сохраним в Update, не из оконной процедуры.</summary>
        private bool _saveRequested;

        /// <summary>Глобальные хоткеи просят действие: очередь разбирается в Update.</summary>
        private readonly Queue<HotkeyAction> _pending = new Queue<HotkeyAction>();

        /// <summary>Момент последнего срабатывания глобального хоткея (анти-двойное нажатие).</summary>
        private float _lastGlobalAt = -999f;

        /// <summary>Какое действие последний раз выполнено глобально (анти-двойное нажатие).</summary>
        private HotkeyAction _lastGlobalAction = HotkeyAction.None;

        /// <summary>Действия хоткеев. Ноль — заглушка «ничего не просили».</summary>
        private enum HotkeyAction
        {
            None = 0,
            Quit = 1,
            DockBottomRight = 2,
            CycleDock = 3,
            ToggleClickThrough = 4,
            MoveLeft = 5,
            MoveRight = 6,
            MoveUp = 7,
            MoveDown = 8,
        }

        /// <summary>Сколько секунд действие глобального хоткея считается «уже выполненным».</summary>
        private const float GlobalDedupSeconds = 0.4f;

        /// <summary>Окно «проглотило» действие глобально — Unity Input его повторять не должен.</summary>
        private bool HandledGloballyRecently(HotkeyAction action)
        {
            return _lastGlobalAction == action
                   && Time.realtimeSinceStartup - _lastGlobalAt < GlobalDedupSeconds;
        }

#if UNITY_STANDALONE_WIN && !UNITY_EDITOR
        private struct Margins
        {
            public int cxLeftWidth;
            public int cxRightWidth;
            public int cyTopHeight;
            public int cyBottomHeight;
        }

        private const int GWL_STYLE = -16;
        private const int GWL_EXSTYLE = -20;
        private const int GWL_WNDPROC = -4;

        /// <summary>64-битный аналог GWL_WNDPROC: указатель на процедуру окна не помещается
        /// в 32 бита, а ``ToInt32()`` на x64 молча отрезал бы старшую половину.</summary>
        private const int GWLP_WNDPROC = -4;

        private const uint WS_POPUP = 0x80000000;
        private const uint WS_VISIBLE = 0x10000000;
        private const uint WS_BORDER = 0x00800000;
        private const uint WS_DLGFRAME = 0x00400000;
        private const uint WS_CAPTION = WS_BORDER | WS_DLGFRAME;
        private const uint WS_THICKFRAME = 0x00040000;
        private const uint WS_MAXIMIZEBOX = 0x00010000;
        private const uint WS_MINIMIZEBOX = 0x00020000;

        private const uint WS_EX_LAYERED = 0x00080000;
        private const uint WS_EX_TRANSPARENT = 0x00000020;
        private const uint WS_EX_TOPMOST = 0x00000008;
        private const uint WS_EX_TOOLWINDOW = 0x00000080;
        private const uint WS_EX_APPWINDOW = 0x00040000;

        private const uint LWA_COLORKEY = 0x00000001;
        private const uint SWP_NOSIZE = 0x0001;
        private const uint SWP_NOMOVE = 0x0002;
        private const uint SWP_FRAMECHANGED = 0x0020;
        private const uint SWP_NOACTIVATE = 0x0010;

        private const int HWND_TOPMOST = -1;
        private const int SM_CXSCREEN = 0;
        private const int SM_CYSCREEN = 1;

        // -- сообщения и константы оконных процедур (0.6.7) -------------------- //
        private const int WM_HOTKEY = 0x0312;
        private const int WM_NCHITTEST = 0x0084;
        private const int WM_EXITSIZEMOVE = 0x0232;
        private const int HTCAPTION = 2;

        private const uint MOD_ALT = 0x0001;
        private const uint MOD_CONTROL = 0x0002;

        private const int VK_LEFT = 0x25;
        private const int VK_UP = 0x26;
        private const int VK_RIGHT = 0x27;
        private const int VK_DOWN = 0x28;
        private const int VK_F1 = 0x70;
        private const int VK_F9 = 0x78;
        private const int VK_F10 = 0x79;
        private const int VK_F11 = 0x7A;

        private const int HotkeyQuit = 1;
        private const int HotkeyDockCorner = 2;
        private const int HotkeyCycleDock = 3;
        private const int HotkeyClickThrough = 4;
        private const int HotkeyArrowsBase = 10;

        /// <summary>Сколько пикселей окна обязано оставаться на экране после шага стрелками.</summary>
        private const int KeepVisiblePx = 48;

        [StructLayout(LayoutKind.Sequential)]
        private struct MONITORINFO
        {
            public int cbSize;
            public RECT rcMonitor;
            public RECT rcWork;
            public uint dwFlags;
        }

        /// <summary>Процедура окна. Делегат держим в поле: собранный GC = падение процесса.</summary>
        private delegate IntPtr WndProcDelegate(IntPtr hWnd, int msg, IntPtr wParam, IntPtr lParam);

        private WndProcDelegate _wndProc;
        private IntPtr _originalWndProc = IntPtr.Zero;
        private bool _subclassed;
        private readonly List<int> _registeredHotkeys = new List<int>();

        [DllImport("user32.dll")]
        private static extern IntPtr GetActiveWindow();

        [DllImport("user32.dll")]
        private static extern int GetWindowLong(IntPtr hWnd, int nIndex);

        [DllImport("user32.dll")]
        private static extern int SetWindowLong(IntPtr hWnd, int nIndex, int dwNewLong);

        [DllImport("user32.dll")]
        private static extern IntPtr CallWindowProc(IntPtr lpPrevWndFunc, IntPtr hWnd, int msg, IntPtr wParam, IntPtr lParam);

        [DllImport("user32.dll", EntryPoint = "GetWindowLongPtr")]
        private static extern IntPtr GetWindowLongPtr64(IntPtr hWnd, int nIndex);

        [DllImport("user32.dll", EntryPoint = "SetWindowLongPtr")]
        private static extern IntPtr SetWindowLongPtr64(IntPtr hWnd, int nIndex, IntPtr dwNewLong);

        [DllImport("user32.dll")]
        private static extern short GetAsyncKeyState(int vKey);

        [DllImport("user32.dll")]
        private static extern bool RegisterHotKey(IntPtr hWnd, int id, uint fsModifiers, uint vk);

        [DllImport("user32.dll")]
        private static extern bool UnregisterHotKey(IntPtr hWnd, int id);

        [DllImport("user32.dll")]
        private static extern IntPtr MonitorFromWindow(IntPtr hWnd, uint dwFlags);

        [DllImport("user32.dll")]
        private static extern bool GetMonitorInfo(IntPtr hMonitor, ref MONITORINFO lpmi);

        [DllImport("kernel32.dll")]
        private static extern int GetLastError();

        [DllImport("user32.dll")]
        private static extern bool SetLayeredWindowAttributes(IntPtr hWnd, uint crKey, byte bAlpha, uint dwFlags);

        [DllImport("user32.dll")]
        private static extern bool SetWindowPos(IntPtr hWnd, int hWndInsertAfter, int x, int y, int cx, int cy, uint uFlags);

        [DllImport("user32.dll")]
        private static extern int GetSystemMetrics(int nIndex);

        [DllImport("user32.dll")]
        private static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);

        [DllImport("dwmapi.dll")]
        private static extern int DwmExtendFrameIntoClientArea(IntPtr hWnd, ref Margins margins);

        [StructLayout(LayoutKind.Sequential)]
        /// <summary>
        /// Win32 RECT. Хотфикс 0.6.6: поля в ЕДИНОМ нижнем регистре — раньше было
        /// ``left / Top / Right / Bottom``, и в player-only ветке писали ``rect.left``,
        /// чего у структуры нет. Это **CS1061**, который не виден ни в Editor, ни
        /// синтаксическому чекеру (он типов не знает) — пойман только сборкой F7.
        /// Гвард: ``tests/test_hotfix_066.py::TestRectRegister``.
        /// </summary>
        private struct RECT
        {
            public int left;
            public int top;
            public int right;
            public int bottom;
        }
#endif

        private void Start()
        {
            if (transparentCamera != null)
            {
                PrepareCamera(transparentCamera);
            }

            if (applyOnStart)
            {
                Apply();
            }
        }

        /// <summary>
        /// Применить прозрачность и позицию. Безопасно вызывать повторно.
        /// </summary>
        public void Apply()
        {
            if (_applied)
            {
                return;
            }

            var mode = config != null ? config.transparency : TransparencyMode.Dwm;

            // MSAA размывает альфу по краям — для прозрачного окна он вреден всегда
            // (и это же лечит «кайму антиалиасинга» на цветовом ключе, пункт 6 донесения).
            QualitySettings.antiAliasing = 0;
            PrepareCamera(transparentCamera);

#if UNITY_STANDALONE_WIN && !UNITY_EDITOR
            if (_hwnd == IntPtr.Zero)
            {
                _hwnd = GetActiveWindow();
            }

            if (_hwnd == IntPtr.Zero)
            {
                Debug.LogWarning("[Lilith] не получил HWND окна — прозрачность не применена");
                return;
            }

            SubclassWindow();
            RestorePersisted();
            ApplyStyles(mode);
            ApplyTransparency(mode);
            Reposition(true);
            RegisterGlobalHotkeys();
            _applied = true;
            Debug.Log($"[Lilith] прозрачность окна: {mode} (hwnd={_hwnd.ToInt64()})"
                      + $" · toolWindow={(config == null || config.windowToolWindow)}"
                      + $" · frame={(config != null && config.showWindowFrame)}"
                      + " · хоткей закрытия Ctrl+Alt+Q · F7 смена режима · F8 вкл/выкл · F9 в угол"
                      + " · F10 цикл углов · F11 click-through · Ctrl+Alt+стрелки шаг · Ctrl+Alt+драг");
            Debug.Log($"[Lilith] окно: {Describe()}");
            LogModeHints(mode);
#else
            _applied = true;
            Debug.Log($"[Lilith] прозрачность окна ({mode}) применяется только в Windows-билде; " +
                      "в Editor/Game View её не увидеть — собери Standalone.");
#endif
        }

#if UNITY_STANDALONE_WIN && !UNITY_EDITOR
        /// <summary>
        /// Стили окна — единственное место, где мы их трогаем (0.6.6).
        /// ``windowToolWindow``: ✅ — окна нет в Alt+Tab и таскбаре (рабочий инструмент);
        /// ❌ — WS_EX_APPWINDOW, окно переключается как обычное приложение.
        /// ``showWindowFrame``: ✅ — оставляем заголовок/рамку/крестик (прозрачность при этом не работает).
        /// 0.6.7: ``clickThrough`` переживает смену режима (F7) — флаг возвращается здесь же,
        /// иначе после цикла режимов окно начало бы снова ловить клики.
        /// </summary>
        private void ApplyStyles(TransparencyMode mode)
        {
            var style = (uint)GetWindowLong(_hwnd, GWL_STYLE);
            var wantFrame = config != null && config.showWindowFrame;
            if (wantFrame)
            {
                style |= WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX;
            }
            else if (borderless)
            {
                style &= ~(WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX);
            }

            style |= WS_VISIBLE | WS_POPUP;

            var exStyle = (uint)GetWindowLong(_hwnd, GWL_EXSTYLE);
            exStyle &= ~WS_EX_TRANSPARENT; // 0.6.7: click-through ставится явно, а не наследуется
            exStyle &= ~(WS_EX_TOOLWINDOW | WS_EX_APPWINDOW | WS_EX_LAYERED);
            exStyle |= (config == null || config.windowToolWindow) ? WS_EX_TOOLWINDOW : WS_EX_APPWINDOW;

            if (topmost)
            {
                exStyle |= WS_EX_TOPMOST;
            }
            else
            {
                exStyle &= ~WS_EX_TOPMOST;
            }

            // WS_EX_LAYERED нужен и цветовому ключу, и click-through: без него
            // WS_EX_TRANSPARENT мышь не пропускает (проверенное сообществом поведение).
            if (mode == TransparencyMode.LayeredColorKey || _clickThrough)
            {
                exStyle |= WS_EX_LAYERED;
            }

            if (_clickThrough)
            {
                exStyle |= WS_EX_TRANSPARENT;
            }

            SetWindowLong(_hwnd, GWL_STYLE, (int)style);
            SetWindowLong(_hwnd, GWL_EXSTYLE, (int)exStyle);
        }

        /// <summary>
        /// Собственно прозрачность. Вызывается только при смене режима —
        /// ``SetLayeredWindowAttributes``/``DwmExtendFrameIntoClientArea`` раз в кадр
        /// дают рябь и «метание» окна (так выглядел эксперимент Кирюши с Color Key).
        /// </summary>
        private void ApplyTransparency(TransparencyMode mode)
        {
            if (mode == TransparencyMode.Dwm)
            {
                var margins = new Margins { cxLeftWidth = -1, cxRightWidth = -1, cyTopHeight = -1, cyBottomHeight = -1 };
                var hr = DwmExtendFrameIntoClientArea(_hwnd, ref margins);
                Debug.Log($"[Lilith] DwmExtendFrameIntoClientArea: hr=0x{hr:X8} (0 = успех)");
            }
            else if (mode == TransparencyMode.LayeredColorKey)
            {
                var key = config != null ? config.colorKey : new Color32(255, 0, 255, 255);
                var crKey = (uint)(key.r | ((uint)key.g << 8) | ((uint)key.b << 16));
                var ok = SetLayeredWindowAttributes(_hwnd, crKey, 255, LWA_COLORKEY);
                Debug.Log($"[Lilith] SetLayeredWindowAttributes: ключ #{key.r:X2}{key.g:X2}{key.b:X2} · ok={ok}");
            }

            SetWindowPos(_hwnd, topmost ? HWND_TOPMOST : 0, 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED);
        }

        /// <summary>
        /// Позиция/размер окна. Вызывает ``SetWindowPos`` ТОЛЬКО если целевой прямоугольник
        /// отличается от текущего — повторяющиеся вызовы и дрались за окно в Color Key.
        /// 0.6.7: целевой прямоугольник считает <see cref="TargetRect"/>, потому что
        /// «в угол» теперь означает один из четырёх углов, а сохранённая вручную позиция
        /// имеет приоритет над ``dockBottomRight`` до первого нажатия F9/F10.
        /// </summary>
        private void Reposition(bool force)
        {
            if (config == null)
            {
                return;
            }

            RECT current;
            GetWindowRect(_hwnd, out current);
            var width = Mathf.Max(64, (int)config.windowSize.x);
            var height = Mathf.Max(64, (int)config.windowSize.y);
            var dock = config.dockBottomRight;
            var target = TargetRect(width, height, dock);
            var x = target.X;
            var y = target.Y;
            width = target.Width;
            height = target.Height;
            var rect = current;

            if (!force && rect.left == x && rect.top == y
                && (rect.right - rect.left) == width && (rect.bottom - rect.top) == height)
            {
                return; // уже там — не дёргаем окно
            }

            SetWindowPos(_hwnd, topmost ? HWND_TOPMOST : 0, x, y, width, height, SWP_NOACTIVATE);
        }

        /// <summary>
        /// Целевой прямоугольник окна: сохранённая позиция (если она есть и хозяин
        /// ещё не нажимал F9/F10 в этой сессии) → иначе угол дока.
        /// </summary>
        private WindowRect TargetRect(int configWidth, int configHeight, bool dockBottomRight)
        {
            var area = WorkArea();
            if (_state != null && _state.HasPosition && !_dockPressedThisSession
                && (config == null || config.restoreWindowPosition))
            {
                var width = _state.ResolveWidth(configWidth);
                var height = _state.ResolveHeight(configHeight);
                var manual = new WindowRect(_state.X, _state.Y, width, height);
                return WindowStateStore.ClampToScreen(manual, area.X, area.Y, area.Width, area.Height, KeepVisiblePx);
            }

            if (!dockBottomRight)
            {
                // Поведение 0.6.6 сохранено: док выключен — окно остаётся где стоит.
                return CurrentRect();
            }

            var corner = WindowStateStore.DockRect(_corner, area.Width, area.Height,
                                                   configWidth, configHeight,
                                                   config != null ? config.windowMargin : 24);
            return Absolute(corner, area);
        }

        /// <summary>
        /// Перевод прямоугольника из системы отсчёта рабочей области (где левый верхний
        /// угол — 0,0) в абсолютные экранные координаты, которые понимает ``SetWindowPos``.
        /// Отдельная функция потому, что ошибка здесь стоит окна на чужом мониторе:
        /// на втором мониторе слева координаты отрицательные, и «просто сложить» без
        /// начала области нельзя.
        /// </summary>
        private static WindowRect Absolute(WindowRect relative, WindowRect area)
        {
            return new WindowRect(relative.X + area.X, relative.Y + area.Y,
                                  relative.Width, relative.Height);
        }

        /// <summary>
        /// Рабочая область монитора, на котором стоит окно (без панели задач),
        /// В АБСОЛЮТНЫХ экранных координатах: ``X``/``Y`` — начало области,
        /// ``Width``/``Height`` — её размер. Если монитор получить не удалось —
        /// метрики основного экрана. Абсолютные координаты нужны, чтобы док работал
        /// и на втором мониторе слева/сверху (там x/y отрицательные).
        /// </summary>
        private WindowRect WorkArea()
        {
            var info = new MONITORINFO();
            info.cbSize = Marshal.SizeOf(typeof(MONITORINFO));
            var monitor = MonitorFromWindow(_hwnd, 1 /* MONITOR_DEFAULTTONEAREST */);
            if (monitor != IntPtr.Zero && GetMonitorInfo(monitor, ref info))
            {
                var originX = info.rcWork.left;
                var originY = info.rcWork.top;
                return new WindowRect(originX, originY,
                                      info.rcWork.right - originX,
                                      info.rcWork.bottom - originY);
            }

            return new WindowRect(0, 0, GetSystemMetrics(SM_CXSCREEN), GetSystemMetrics(SM_CYSCREEN));
        }

        /// <summary>Текущий прямоугольник окна (или пустой, если HWND ещё нет).</summary>
        private WindowRect CurrentRect()
        {
            RECT current;
            if (!GetWindowRect(_hwnd, out current))
            {
                return new WindowRect(0, 0, 0, 0);
            }

            return new WindowRect(current.left, current.top,
                                  current.right - current.left, current.bottom - current.top);
        }

        /// <summary>
        /// Подкласс окна: единственный способ увидеть WM_NCHITTEST (драг) и
        /// WM_EXITSIZEMOVE (конец перетаскивания → сохранить позицию).
        /// Ставится один раз на старте и снимается в OnDestroy — иначе после
        /// смерти делегата Windows вызовет собранный GC callback и процесс упадёт.
        /// </summary>
        private void SubclassWindow()
        {
            if (_subclassed || _hwnd == IntPtr.Zero)
            {
                return;
            }

            _wndProc = WndProc; // поле, а не локальная переменная: собранный GC делегат = падение процесса
            _originalWndProc = GetWindowPointer(_hwnd, GWLP_WNDPROC);
            var installed = SetWindowPointer(_hwnd, GWLP_WNDPROC,
                                             Marshal.GetFunctionPointerForDelegate(_wndProc));
            _subclassed = installed != IntPtr.Zero && _originalWndProc != IntPtr.Zero;
            if (!_subclassed)
            {
                Debug.LogWarning($"[Lilith] не смог подклассить окно (GetLastError={GetLastError()}) — "
                                 + "драг за Ctrl+Alt и сохранение позиции после перетаскивания работать не будут");
                return;
            }

            Debug.Log("[Lilith] оконная процедура подклассирована: WM_NCHITTEST (драг Ctrl+Alt), "
                      + "WM_EXITSIZEMOVE (сохранить позицию), WM_HOTKEY (глобальные хоткеи)");
        }

        /// <summary>
        /// Наша оконная процедура. Правил три, и они важнее самого кода:
        ///   1. НЕ вызывать Unity API (Debug.Log в том числе) — сообщение может прийти
        ///      вне кадра Unity;
        ///   2. НЕ выходить из процесса здесь — только флаг, Quit делает Update;
        ///   3. всё остальное — в прежнюю процедуру, иначе сломаются IME, фокус, мышь.
        /// </summary>
        private IntPtr WndProc(IntPtr hWnd, int msg, IntPtr wParam, IntPtr lParam)
        {
            switch (msg)
            {
                case WM_HOTKEY:
                    OnGlobalHotkey(wParam.ToInt32());
                    return IntPtr.Zero;

                case WM_NCHITTEST:
                {
                    var hit = CallWindowProc(_originalWndProc, hWnd, msg, wParam, lParam);
                    if (HitTestIsCaption(hit))
                    {
                        return new IntPtr(HTCAPTION);
                    }

                    return hit;
                }

                case WM_EXITSIZEMOVE:
                {
                    var result = CallWindowProc(_originalWndProc, hWnd, msg, wParam, lParam);
                    // Не SavePositionNow напрямую: там Debug.Log и запись на диск, а из
                    // оконной процедуры нельзя ни того ни другого (правило 1 WndProc).
                    // Флаг разбирает Update — сохранение происходит в кадре, через полтакта.
                    _saveRequested = true;
                    return result;
                }

                default:
                    return CallWindowProc(_originalWndProc, hWnd, msg, wParam, lParam);
            }
        }

        /// <summary>
        /// Драг за любую точку окна (пункт 1 пожеланий): пока держат Ctrl+Alt, окно
        /// сообщает системе «это заголовок», и Windows тащит его сам — со своей
        /// инерцией, snap'ом к краям и WM_EXITSIZEMOVE в конце.
        /// Без модификаторов возвращается прежний ответ, то есть клики работают штатно.
        /// ``GetAsyncKeyState`` (а не ``Input.GetKey``) — потому что состояние клавиш
        /// нужно спрашивать у системы: у окна может не быть фокуса.
        /// </summary>
        private bool HitTestIsCaption(IntPtr hit)
        {
            if (_clickThrough || config == null || !config.dragWithCtrlAlt)
            {
                return false;
            }

            // HTTRANSPARENT (-1) и HTNOWHERE (0) трогать нельзя: это не «внутри окна».
            if (hit == new IntPtr(-1) || hit == IntPtr.Zero)
            {
                return false;
            }

            var control = (GetAsyncKeyState(0x11 /* VK_CONTROL */) & 0x8000) != 0;
            var alt = (GetAsyncKeyState(0x12 /* VK_MENU */) & 0x8000) != 0;
            return control && alt;
        }

        /// <summary>
        /// Unity ``KeyCode`` → виртуальная клавиша Windows (VK). Прямой каст
        /// ``(uint)KeyCode.Q`` дал бы 113, а ``VK_Q`` = 81: буквы в Unity — ASCII
        /// строчных ('a' = 97), а VK — ASCII заглавных ('A' = 65). Без конвертера
        /// RegisterHotKey молча зарегистрировал бы не ту клавишу — ошибку не видно
        /// ни чекеру (типов он не знает), ни Editor (ветка вырезана).
        /// Гвард: ``tests/test_hotfix_067.py::TestVirtualKeys``.
        /// </summary>
        private static uint VirtualKeyOf(KeyCode key)
        {
            var code = (int)key;
            if (code >= (int)KeyCode.A && code <= (int)KeyCode.Z)
            {
                return (uint)(code - 32); // 'a' (97) → 'A' (65) = VK_A
            }

            if (code >= (int)KeyCode.F1 && code <= (int)KeyCode.F15)
            {
                return (uint)(code - (int)KeyCode.F1 + VK_F1); // F1: 282 → 0x70
            }

            // Цифры совпадают без сдвига: KeyCode.Alpha0 = 48 = VK_0.
            return (uint)code;
        }

        /// <summary>
        /// Глобальные хоткеи (пункт 5 пожеланий). Unity Input слушает только окно в фокусе,
        /// а в полноэкранной игре фокуса нет: без RegisterHotKey закрыть или сдвинуть окно
        /// было нечем. Регистрация может не выйти (хоткей занят другой программой) —
        /// тогда остаётся прежний путь через Input, и click-through не включится вовсе.
        /// </summary>
        private void RegisterGlobalHotkeys()
        {
            if (_registeredHotkeys.Count > 0)
            {
                return; // повторный Apply (F7/F8): хоткеи уже зарегистрированы, не дублируем
            }

            if (config == null || !config.useGlobalHotkeys)
            {
                Debug.Log("[Lilith] глобальные хоткеи выключены конфигом (Use Global Hotkeys = ❌) — "
                          + "хоткеи работают только когда окно в фокусе, click-through (F11) недоступен");
                return;
            }

            var controlAlt = MOD_CONTROL | MOD_ALT;
            // «Ядро» — выход и два дока. От него зависит разрешение click-through:
            // если нечем закрыть окно, сквозной режим делать нельзя (пункт 6 пожеланий).
            var coreOk = true;
            coreOk &= TryRegisterHotkey(HotkeyQuit, controlAlt, VirtualKeyOf(config.closeHotkey),
                                        $"Ctrl+Alt+{config.closeHotkey} (выход)");
            coreOk &= TryRegisterHotkey(HotkeyDockCorner, 0, VK_F9, "F9 (в правый нижний угол)");
            coreOk &= TryRegisterHotkey(HotkeyCycleDock, 0, VK_F10, "F10 (цикл углов)");

            // «Дополнения» регистрируем независимо: занятый F11 или стрелки не должны
            // отнимать у хозяина выход из окна.
            var clickOk = !config.allowClickThrough
                          || TryRegisterHotkey(HotkeyClickThrough, 0, VK_F11, "F11 (click-through)");
            var arrowsOk = true;
            if (config.globalArrowHotkeys)
            {
                arrowsOk &= TryRegisterHotkey(HotkeyArrowsBase + 0, controlAlt, VK_LEFT, "Ctrl+Alt+← (шаг влево)");
                arrowsOk &= TryRegisterHotkey(HotkeyArrowsBase + 1, controlAlt, VK_UP, "Ctrl+Alt+↑ (шаг вверх)");
                arrowsOk &= TryRegisterHotkey(HotkeyArrowsBase + 2, controlAlt, VK_RIGHT, "Ctrl+Alt+→ (шаг вправо)");
                arrowsOk &= TryRegisterHotkey(HotkeyArrowsBase + 3, controlAlt, VK_DOWN, "Ctrl+Alt+↓ (шаг вниз)");
            }

            var ok = coreOk;
            _clickThroughAvailable = clickOk;
            _globalHotkeysOk = ok;
            if (ok)
            {
                Debug.Log("[Lilith] глобальные хоткеи зарегистрированы: работают БЕЗ фокуса окна (из игры). "
                          + $"click-through {(clickOk ? "доступен (F11)" : "НЕдоступен: F11 не зарегистрирован")}. "
                          + (config.globalArrowHotkeys
                              ? $"Стрелки глобальны: {(arrowsOk ? "да" : "часть не зарегистрирована")}. "
                                + "Внимание: Ctrl+Alt+стрелки перехватываются системой и до игры не дойдут — "
                                + "сними Global Arrow Hotkeys, если они нужны игре."
                              : "Стрелки остались только в окне с фокусом (Global Arrow Hotkeys = ❌)."));
            }
            else
            {
                Debug.LogWarning("[Lilith] часть глобальных хоткеев НЕ зарегистрирована (заняты другой программой?) — "
                                 + "работают только в окне с фокусом, а click-through (F11) будет отклонён: "
                                 + "без глобального хоткея окно-призрака нечем закрыть");
            }
        }

        /// <summary>Одна регистрация + честный лог с кодом ошибки Windows.</summary>
        private bool TryRegisterHotkey(int id, uint modifiers, uint vk, string what)
        {
            if (RegisterHotKey(_hwnd, id, modifiers, vk))
            {
                _registeredHotkeys.Add(id);
                return true;
            }

            Debug.LogWarning($"[Lilith] RegisterHotKey не удался: {what} · GetLastError={GetLastError()}");
            return false;
        }

        /// <summary>
        /// WM_HOTKEY → действие в очередь. Никаких Unity API здесь: сообщение приходит
        /// вне кадра, а Application.Quit из чужого такта — это лотерея.
        /// </summary>
        private void OnGlobalHotkey(int id)
        {
            switch (id)
            {
                case HotkeyQuit:
                    _pending.Enqueue(HotkeyAction.Quit);
                    break;
                case HotkeyDockCorner:
                    _pending.Enqueue(HotkeyAction.DockBottomRight);
                    break;
                case HotkeyCycleDock:
                    _pending.Enqueue(HotkeyAction.CycleDock);
                    break;
                case HotkeyClickThrough:
                    _pending.Enqueue(HotkeyAction.ToggleClickThrough);
                    break;
                case HotkeyArrowsBase + 0:
                    _pending.Enqueue(HotkeyAction.MoveLeft);
                    break;
                case HotkeyArrowsBase + 1:
                    _pending.Enqueue(HotkeyAction.MoveUp);
                    break;
                case HotkeyArrowsBase + 2:
                    _pending.Enqueue(HotkeyAction.MoveRight);
                    break;
                case HotkeyArrowsBase + 3:
                    _pending.Enqueue(HotkeyAction.MoveDown);
                    break;
                default:
                    break;
            }
        }

        /// <summary>
        /// Чтение указателя из окна: на x64 — ``GetWindowLongPtr``, на x86 его в
        /// user32.dll **нет вовсе**, поэтому там честный ``GetWindowLong``. Без этой
        /// развилки 32-битный билд упал бы с EntryPointNotFoundException на старте.
        /// </summary>
        private IntPtr GetWindowPointer(IntPtr hWnd, int index)
        {
            if (IntPtr.Size == 8)
            {
                return GetWindowLongPtr64(hWnd, index);
            }

            return new IntPtr(GetWindowLong(hWnd, index));
        }

        /// <summary>Запись указателя в окно: та же развилка x64/x86, что и при чтении.</summary>
        private IntPtr SetWindowPointer(IntPtr hWnd, int index, IntPtr value)
        {
            if (IntPtr.Size == 8)
            {
                return SetWindowLongPtr64(hWnd, index, value);
            }

            return new IntPtr(SetWindowLong(hWnd, index, value.ToInt32()));
        }

        /// <summary>Снять регистрацию и вернуть прежнюю оконную процедуру.</summary>
        private void UnregisterGlobalHotkeys()
        {
            foreach (var id in _registeredHotkeys)
            {
                UnregisterHotKey(_hwnd, id);
            }

            _registeredHotkeys.Clear();
            _globalHotkeysOk = false;

            if (_subclassed && _originalWndProc != IntPtr.Zero)
            {
                SetWindowPointer(_hwnd, GWLP_WNDPROC, _originalWndProc);
                _subclassed = false;
            }
        }

        /// <summary>
        /// click-through (пункт 6 пожеланий, «критично для игр»): WS_EX_TRANSPARENT +
        /// WS_EX_LAYERED — клики проходят СКВОЗЬ окно в игру, курсор в правом нижнем углу
        /// больше не попадает по модельке.
        ///
        /// Гвард безопасности: включается ТОЛЬКО если глобальные хоткеи живы. Иначе окно
        /// станет неубиваемым призраком — его не закрыть (Ctrl+Alt+Q не дойдёт без фокуса)
        /// и не выключить (F11 тоже не дойдёт). Требование архитектора, пункт 6.
        /// </summary>
        private void ApplyClickThrough(bool want)
        {
            if (_hwnd == IntPtr.Zero)
            {
                return;
            }

            if (want && (config == null || !config.allowClickThrough))
            {
                // Единственная воронка: и глобальный F11, и F11 в фокусе проходят здесь,
                // поэтому запрет конфигом нельзя обойти «просто нажав в окне».
                // ВЫКЛЮЧИТЬ click-through конфиг запретить не может: снятие флага —
                // всегда в сторону кликабельности (безопасное направление).
                Debug.LogWarning("[Lilith] click-through ОТКЛОНЁН: запрещён конфигом (Allow Click Through = ❌)");
                return;
            }

            if (want && (!_globalHotkeysOk || !_clickThroughAvailable))
            {
                Debug.LogWarning("[Lilith] click-through ОТКЛОНЁН: нет глобальных хоткеев "
                                 + $"(ядро={_globalHotkeysOk}, F11={_clickThroughAvailable}). "
                                 + "Иначе окно стало бы неубиваемым призраком: его нечем ни выключить, "
                                 + "ни закрыть. Смотри строки RegisterHotKey выше и Use Global Hotkeys в конфиге.");
                return;
            }

            if (_clickThrough == want)
            {
                return; // Win32 только по изменению состояния (правило 0.6.6)
            }

            _clickThrough = want;

            var exStyle = (uint)GetWindowLong(_hwnd, GWL_EXSTYLE);
            exStyle &= ~WS_EX_TRANSPARENT;
            if (want)
            {
                exStyle |= WS_EX_LAYERED | WS_EX_TRANSPARENT;
            }
            else
            {
                var mode = config != null ? config.transparency : TransparencyMode.Dwm;
                if (mode != TransparencyMode.LayeredColorKey)
                {
                    // DWM-стекло не требует WS_EX_LAYERED; снимем и переприменим режим,
                    // чтобы стекло точно вернулось (проверить на замере Е).
                    exStyle &= ~WS_EX_LAYERED;
                    ApplyTransparency(mode);
                }
            }

            SetWindowLong(_hwnd, GWL_EXSTYLE, (int)exStyle);
            SetWindowPos(_hwnd, topmost ? HWND_TOPMOST : 0, 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED);
            Debug.Log($"[Lilith] F11: click-through = {(want ? "ВКЛ (клики проходят сквозь окно в игру)" : "ВЫКЛ (окно снова ловит клики)")}");
        }

        /// <summary>Шаг стрелками: сдвинуть окно на <c>windowMoveStep</c> px и запомнить позицию.</summary>
        private void Nudge(int dx, int dy)
        {
            if (_hwnd == IntPtr.Zero)
            {
                return;
            }

            var step = config != null ? Mathf.Max(1, config.windowMoveStep) : 32;
            var area = WorkArea();
            var current = CurrentRect();
            var moved = WindowStateStore.MoveBy(current, dx * step, dy * step);
            var clamped = WindowStateStore.ClampToScreen(moved, area.X, area.Y, area.Width, area.Height, KeepVisiblePx);
            // save: true — стрелки такое же ручное перемещение, как драг: хозяин
            // положил окно, окно запомнило (иначе позиция стрелок терялась бы при
            // перезапуске, а обещание «персист ручной позиции» — нарушено).
            MoveWindowTo(clamped, true);
        }

        /// <summary>Один ``SetWindowPos`` на перемещение + (опционально) запись в файл состояния.</summary>
        private void MoveWindowTo(WindowRect target, bool save)
        {
            if (_hwnd == IntPtr.Zero)
            {
                return;
            }

            SetWindowPos(_hwnd, topmost ? HWND_TOPMOST : 0, target.X, target.Y, target.Width, target.Height,
                         SWP_NOACTIVATE);
            if (save)
            {
                SavePositionNow("ручное перемещение");
            }
        }

        /// <summary>Прижать окно к указанному углу и запомнить это как ручную позицию.</summary>
        private void DockTo(DockCorner corner, bool force)
        {
            if (_hwnd == IntPtr.Zero || config == null)
            {
                return;
            }

            _corner = corner;
            _dockPressedThisSession = true;
            var area = WorkArea();
            var width = Mathf.Max(64, (int)config.windowSize.x);
            var height = Mathf.Max(64, (int)config.windowSize.y);
            var relative = WindowStateStore.DockRect(corner, area.Width, area.Height, width, height, config.windowMargin);
            var target = Absolute(relative, area);
            SetWindowPos(_hwnd, topmost ? HWND_TOPMOST : 0,
                         target.X, target.Y, target.Width, target.Height, SWP_NOACTIVATE);
            if (force)
            {
                SavePositionNow($"док {corner}");
            }
        }

        /// <summary>
        /// Прочитать data/window_state.json один раз на старте.
        /// Файл битый/чужой версии → null, стартуем с доком из конфига: окно не имеет
        /// права не запускаться из-за собственного файла состояния.
        /// </summary>
        private void RestorePersisted()
        {
            _state = null;
            _positionFromState = false;
            if (config == null || !config.restoreWindowPosition)
            {
                return;
            }

            var path = WindowStatePath();
            _state = WindowStateStore.Load(path);
            if (_state != null && _state.HasPosition)
            {
                _corner = _state.Corner;
                _positionFromState = true;
                Debug.Log($"[Lilith] позиция окна из файла {path}: {_state} "
                          + "(приоритет над Dock Bottom Right до первого F9/F10)");
                return;
            }

            _corner = config.initialDockCorner;
            Debug.Log($"[Lilith] файла позиции нет или он не читается ({path}) — "
                      + $"стартуем с доком из конфига (угол {_corner})");
        }

        /// <summary>Полный путь к файлу состояния окна.</summary>
        private string WindowStatePath()
        {
            var explicitPath = config != null ? config.windowStatePath : null;
            return WindowStateStore.DefaultPath(explicitPath, null);
        }

        /// <summary>Записать текущую позицию окна в файл (после драга, шага, дока).</summary>
        private void SavePositionNow(string why)
        {
            if (_hwnd == IntPtr.Zero || config == null || !config.saveWindowPosition)
            {
                return;
            }

            var current = CurrentRect();
            if (current.Width < 8 || current.Height < 8)
            {
                return; // HWND ещё не обрёл размер — нечего сохранять
            }

            var state = new WindowState
            {
                Version = WindowState.CurrentVersion,
                // Координаты абсолютные: при следующем старте монитор может быть другим,
                // а TargetRect всё равно прогонит позицию через ClampToScreen.
                X = current.X,
                Y = current.Y,
                Width = current.Width,
                Height = current.Height,
                Corner = _corner,
                HasPosition = true,
            };

            var path = WindowStatePath();
            if (WindowStateStore.Save(path, state))
            {
                _state = state;
                Debug.Log($"[Lilith] позиция окна сохранена ({why}): {state} → {path}");
            }
        }

        /// <summary>Подсказка в Player.log: что включить в Player Settings, если фон чёрный.</summary>
        private void LogModeHints(TransparencyMode mode)
        {
            if (mode != TransparencyMode.Dwm)
            {
                return;
            }

            Debug.Log("[Lilith] DWM-стекло в Unity 6 работает только при двух настройках плеера: "
                      + "Player Settings → Resolution and Presentation → Fullscreen Mode = Fullscreen Window; "
                      + "Player Settings → Other Settings → Use Flip Model Swapchain = ❌ (без этого "
                      + "flip-model swapchain отдаёт DWM непрозрачный кадр — отсюда чёрный фон на Win10 19045).");
        }
#endif

        /// <summary>
        /// 0.6.6: сменить режим прозрачности ЖИВЬЁМ (F7) — чтобы сравнивать режимы
        /// в одном билде, а не пересобирать проект под каждый эксперимент.
        /// </summary>
        public void CycleMode()
        {
            var values = (TransparencyMode[])System.Enum.GetValues(typeof(TransparencyMode));
            var current = config != null ? config.transparency : TransparencyMode.Dwm;
            var index = System.Array.IndexOf(values, current);
            var next = values[(index + 1) % values.Length];
            if (config != null)
            {
                config.transparency = next;
            }

            _applied = false;
            Debug.Log($"[Lilith] F7: режим прозрачности {current} → {next}");
            Apply();
        }

        /// <summary>Снять прозрачность (для отладки в окне).</summary>
        public void Revert()
        {
            _applied = false;
            // F8 = «снять всё, что применили»: click-through тоже. Иначе окно осталось бы
            // на вид обычным, но некликабельным — мини-призрак (дух пункта 6 пожеланий).
            _clickThrough = false;
#if UNITY_STANDALONE_WIN && !UNITY_EDITOR
            if (_hwnd == IntPtr.Zero)
            {
                return;
            }

            var exStyle = (uint)GetWindowLong(_hwnd, GWL_EXSTYLE);
            exStyle &= ~(WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TRANSPARENT);
            SetWindowLong(_hwnd, GWL_EXSTYLE, (int)exStyle);
            SetWindowPos(_hwnd, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED);
#endif
        }

        /// <summary>
        /// Применить размер окна из конфига (Q5: 512×640 портрет по умолчанию,
        /// персона может переопределить в ``face.yaml: window``).
        /// </summary>
        public void ApplyWindowSize()
        {
#if UNITY_STANDALONE_WIN && !UNITY_EDITOR
            if (_hwnd == IntPtr.Zero || config == null)
            {
                return;
            }

            Reposition(false);
#endif
        }

        /// <summary>Передвинуть окно в правый нижний угол рабочего стола.</summary>
        public void DockBottomRight()
        {
            if (config == null || !config.dockBottomRight)
            {
                return;
            }

#if UNITY_STANDALONE_WIN && !UNITY_EDITOR
            if (_hwnd == IntPtr.Zero)
            {
                return;
            }

            DockTo(DockCorner.BottomRight, true);
#endif
        }

        /// <summary>
        /// F10 — цикл углов дока: BR → BL → TR → TL → BR (пункт 3 пожеланий).
        /// Каждый угол логируется и сохраняется: хозяин положил окно — окно запомнило.
        /// </summary>
        public void CycleDockCorner()
        {
            var next = WindowStateStore.NextCorner(_corner);
#if UNITY_STANDALONE_WIN && !UNITY_EDITOR
            if (_hwnd == IntPtr.Zero)
            {
                return;
            }

            var previous = _corner;
            DockTo(next, true);
            Debug.Log($"[Lilith] F10: угол дока {previous} → {next}");
#else
            var previous = _corner;
            _corner = next;
            Debug.Log($"[Lilith] F10: угол дока {previous} → {next} (только в Windows-билде)");
#endif
        }

        /// <summary>
        /// F11 — click-through: клики проходят сквозь окно в игру (пункт 6 пожеланий).
        /// В Editor ветка вырезана, как и весь Win32: проверяется только сборкой.
        /// </summary>
        public void ToggleClickThrough()
        {
#if UNITY_STANDALONE_WIN && !UNITY_EDITOR
            ApplyClickThrough(!_clickThrough);
#else
            Debug.Log("[Lilith] click-through доступен только в Windows-билде");
#endif
        }

        /// <summary>Сдвинуть окно на шаг стрелками (пункт 2 пожеланий).</summary>
        public void MoveBy(int dx, int dy)
        {
#if UNITY_STANDALONE_WIN && !UNITY_EDITOR
            Nudge(dx, dy);
#else
            Debug.Log($"[Lilith] шаг стрелками ({dx}, {dy}) доступен только в Windows-билде");
#endif
        }

        /// <summary>Выход: вызывается и по Ctrl+Alt+Q в окне, и по глобальному хоткею.</summary>
        private void RequestQuit()
        {
            Debug.Log($"[Lilith] хоткей закрытия (Ctrl+Alt+{(config != null ? config.closeHotkey : KeyCode.Q)}) — выходим");
#if UNITY_STANDALONE_WIN && !UNITY_EDITOR
            if (_clickThrough)
            {
                // Страховка от призрака: снимаем click-through до выхода, чтобы окно
                // не осталось висеть прозрачным и некликабельным, если Quit отложен.
                ApplyClickThrough(false);
            }
#endif
            Application.Quit();
        }

        /// <summary>
        /// Настроить камеру под прозрачность: фон выключен, для color key — нужный цвет.
        /// </summary>
        public void PrepareCamera(Camera cam)
        {
            if (cam == null)
            {
                return;
            }

            cam.allowMSAA = false; // MSAA ломает color key по краям
            cam.clearFlags = CameraClearFlags.SolidColor;

            var mode = config != null ? config.transparency : TransparencyMode.Dwm;
            if (mode == TransparencyMode.LayeredColorKey && config != null)
            {
                var key = config.colorKey;
                cam.backgroundColor = new Color(key.r / 255f, key.g / 255f, key.b / 255f, 1f);
            }
            else if (mode == TransparencyMode.Off)
            {
                // 0.6.6: «Off» — прозрачности нет вовсе, поэтому альфа 0 дала бы чёрную
                // дыру, и цикл F7 выглядел бы как поломка. Делаем фон честным: тёмный
                // непрозрачный, окно видно и в нём понятно, что режим выключен.
                cam.backgroundColor = new Color(0.08f, 0.06f, 0.10f, 1f);
            }
            else
            {
                // DWM: полностью прозрачный фон (альфа 0) — DWM покажет рабочий стол
                cam.backgroundColor = new Color(0f, 0f, 0f, 0f);
            }
        }

        /// <summary>
        /// Строка состояния окна для оверлея (собирается в ``Update`` клиента, а не в
        /// ``OnGUI`` — правило 0.6.6: текст один раз за кадр, рисуем только снимок).
        /// Работает и в Editor: глобальные хоткеи там всегда «нет», и это честно.
        /// </summary>
        public string Describe()
        {
            var corner = _positionFromState && !_dockPressedThisSession ? $"{_corner} (из файла)" : _corner.ToString();
            var click = _clickThrough ? "ВКЛ" : "выкл";
            var hotkeys = _globalHotkeysOk ? "да" : "нет";
            return $"окно: угол {corner} · click-through {click} · глоб. хоткеи {hotkeys}";
        }

        private void Update()
        {
            DrainGlobalHotkeys();
            DrainDeferredSave();

            // F8 — быстрый переключатель прозрачности (удобно при настройке OBS).
            if (Input.GetKeyDown(KeyCode.F8))
            {
                if (_applied)
                {
                    Revert();
                }
                else
                {
                    Apply();
                }
            }

            // Хоткеи, которые дублируются глобальными, опрашиваем ТОЛЬКО при фокусе:
            // глобальный хоткей Windows и так не пускает клавишу в приложение, а двойной
            // опрос дал бы два срабатывания на одно нажатие (F11 щёлкал бы туда-сюда).
            var focused = Application.isFocused;

            if (focused && Input.GetKeyDown(KeyCode.F9) && !HandledGloballyRecently(HotkeyAction.DockBottomRight))
            {
                DockBottomRight();
            }

            if (focused && Input.GetKeyDown(KeyCode.F10) && !HandledGloballyRecently(HotkeyAction.CycleDock))
            {
                CycleDockCorner();
            }

            if (focused && Input.GetKeyDown(KeyCode.F11) && !HandledGloballyRecently(HotkeyAction.ToggleClickThrough))
            {
                ToggleClickThrough();
            }

            if (focused && Input.GetKeyDown(KeyCode.F7))
            {
                CycleMode();
            }

            if (focused && (Input.GetKey(KeyCode.LeftControl) || Input.GetKey(KeyCode.RightControl))
                && (Input.GetKey(KeyCode.LeftAlt) || Input.GetKey(KeyCode.RightAlt)))
            {
                if (Input.GetKeyDown(KeyCode.LeftArrow) && !HandledGloballyRecently(HotkeyAction.MoveLeft))
                {
                    MoveBy(-1, 0);
                }

                if (Input.GetKeyDown(KeyCode.RightArrow) && !HandledGloballyRecently(HotkeyAction.MoveRight))
                {
                    MoveBy(1, 0);
                }

                if (Input.GetKeyDown(KeyCode.UpArrow) && !HandledGloballyRecently(HotkeyAction.MoveUp))
                {
                    MoveBy(0, -1);
                }

                if (Input.GetKeyDown(KeyCode.DownArrow) && !HandledGloballyRecently(HotkeyAction.MoveDown))
                {
                    MoveBy(0, 1);
                }
            }

            // Окно без рамок не имеет крестика и (в режиме tool window) не видно в Alt+Tab,
            // поэтому закрывать его должен хоткей. По умолчанию Ctrl+Alt+Q.
            if (config != null && config.closeHotkeyEnabled
                && (Input.GetKey(KeyCode.LeftControl) || Input.GetKey(KeyCode.RightControl))
                && (Input.GetKey(KeyCode.LeftAlt) || Input.GetKey(KeyCode.RightAlt))
                && Input.GetKeyDown(config.closeHotkey)
                && !HandledGloballyRecently(HotkeyAction.Quit))
            {
                RequestQuit();
            }
        }

        /// <summary>
        /// Разобрать просьбы глобальных хоткеев. Действия выполняются здесь, в главном
        /// потоке и внутри кадра Unity: из оконной процедуры трогать Unity API нельзя
        /// (урок 0.6.5 — главный поток не блокируется и не дёргается из чужого такта).
        /// </summary>
        private void DrainGlobalHotkeys()
        {
            if (_quitRequested)
            {
                _quitRequested = false;
                RequestQuit();
                return;
            }

            var processed = 0;
            while (processed < 16 && _pending.Count > 0)
            {
                processed++;
                var action = _pending.Dequeue();
                _lastGlobalAction = action;
                _lastGlobalAt = Time.realtimeSinceStartup;
                RunHotkeyAction(action);
            }
        }

        /// <summary>
        /// Отложенное сохранение позиции: WM_EXITSIZEMOVE из оконной процедуры ставит
        /// флаг, а пишем мы здесь — в главном потоке и внутри кадра Unity. Из WndProc
        /// нельзя ни Debug.Log, ни диск (правило 1 оконной процедуры).
        /// </summary>
        private void DrainDeferredSave()
        {
            if (!_saveRequested)
            {
                return;
            }

            _saveRequested = false;
            #if UNITY_STANDALONE_WIN && !UNITY_EDITOR
            SavePositionNow("конец перетаскивания/изменения размера");
            #endif
        }

        /// <summary>Одно действие хоткея. Отдельным методом — чтобы его видели гварды и оверлей.</summary>
        private void RunHotkeyAction(HotkeyAction action)
        {
            switch (action)
            {
                case HotkeyAction.Quit:
                    RequestQuit();
                    break;
                case HotkeyAction.DockBottomRight:
                    DockBottomRight();
                    break;
                case HotkeyAction.CycleDock:
                    CycleDockCorner();
                    break;
                case HotkeyAction.ToggleClickThrough:
                    ToggleClickThrough();
                    break;
                case HotkeyAction.MoveLeft:
                    MoveBy(-1, 0);
                    break;
                case HotkeyAction.MoveRight:
                    MoveBy(1, 0);
                    break;
                case HotkeyAction.MoveUp:
                    MoveBy(0, -1);
                    break;
                case HotkeyAction.MoveDown:
                    MoveBy(0, 1);
                    break;
                default:
                    break;
            }
        }

        private void OnDestroy()
        {
#if UNITY_STANDALONE_WIN && !UNITY_EDITOR
            UnregisterGlobalHotkeys();
#endif
        }

        // Позиция сохраняется В МОМЕНТ перемещения (WM_EXITSIZEMOVE / стрелки / F9 / F10),
        // а не на выходе: запись в OnApplicationQuit потеряла бы позицию при аварийном
        // завершении, то есть ровно тогда, когда она нужнее всего.
    }
}