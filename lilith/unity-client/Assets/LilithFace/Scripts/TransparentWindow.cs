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

using System;
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

        [DllImport("user32.dll")]
        private static extern IntPtr GetActiveWindow();

        [DllImport("user32.dll")]
        private static extern int GetWindowLong(IntPtr hWnd, int nIndex);

        [DllImport("user32.dll")]
        private static extern int SetWindowLong(IntPtr hWnd, int nIndex, int dwNewLong);

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

            ApplyStyles(mode);
            ApplyTransparency(mode);
            Reposition(true);
            _applied = true;
            Debug.Log($"[Lilith] прозрачность окна: {mode} (hwnd={_hwnd.ToInt64()})"
                      + $" · toolWindow={(config == null || config.windowToolWindow)}"
                      + $" · frame={(config != null && config.showWindowFrame)}"
                      + " · хоткей закрытия Ctrl+Alt+Q · F7 смена режима · F8 вкл/выкл · F9 в угол");
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

            if (mode == TransparencyMode.LayeredColorKey)
            {
                exStyle |= WS_EX_LAYERED;
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
        /// </summary>
        private void Reposition(bool force)
        {
            if (config == null)
            {
                return;
            }

            RECT rect;
            GetWindowRect(_hwnd, out rect);
            var screenW = GetSystemMetrics(SM_CXSCREEN);
            var screenH = GetSystemMetrics(SM_CYSCREEN);
            var width = Mathf.Max(64, (int)config.windowSize.x);
            var height = Mathf.Max(64, (int)config.windowSize.y);
            var x = config.dockBottomRight ? screenW - width - config.windowMargin : rect.left;
            var y = config.dockBottomRight ? screenH - height - config.windowMargin : rect.top;

            if (!force && rect.left == x && rect.top == y
                && (rect.right - rect.left) == width && (rect.bottom - rect.top) == height)
            {
                return; // уже там — не дёргаем окно
            }

            SetWindowPos(_hwnd, topmost ? HWND_TOPMOST : 0, x, y, width, height, SWP_NOACTIVATE);
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
#if UNITY_STANDALONE_WIN && !UNITY_EDITOR
            if (_hwnd == IntPtr.Zero)
            {
                return;
            }

            var exStyle = (uint)GetWindowLong(_hwnd, GWL_EXSTYLE);
            exStyle &= ~(WS_EX_LAYERED | WS_EX_TOPMOST);
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

            Reposition(true);
#endif
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

        private void Update()
        {
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

            if (Input.GetKeyDown(KeyCode.F9))
            {
                DockBottomRight();
            }

            if (Input.GetKeyDown(KeyCode.F7))
            {
                CycleMode();
            }

            // Окно без рамок не имеет крестика и (в режиме tool window) не видно в Alt+Tab,
            // поэтому закрывать его должен хоткей. По умолчанию Ctrl+Alt+Q.
            if (config != null && config.closeHotkeyEnabled
                && (Input.GetKey(KeyCode.LeftControl) || Input.GetKey(KeyCode.RightControl))
                && (Input.GetKey(KeyCode.LeftAlt) || Input.GetKey(KeyCode.RightAlt))
                && Input.GetKeyDown(config.closeHotkey))
            {
                Debug.Log($"[Lilith] хоткей закрытия (Ctrl+Alt+{config.closeHotkey}) — выходим");
                Application.Quit();
            }
        }
    }
}
