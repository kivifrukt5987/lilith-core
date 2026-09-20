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
        private struct RECT
        {
            public int left;
            public int Top;
            public int Right;
            public int Bottom;
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

#if UNITY_STANDALONE_WIN && !UNITY_EDITOR
            _hwnd = GetActiveWindow();
            if (_hwnd == IntPtr.Zero)
            {
                Debug.LogWarning("[Lilith] не получил HWND окна — прозрачность не применена");
                return;
            }

            var style = (uint)GetWindowLong(_hwnd, GWL_STYLE);
            if (borderless)
            {
                style &= ~(WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX);
            }

            style |= WS_VISIBLE | WS_POPUP;

            var exStyle = (uint)GetWindowLong(_hwnd, GWL_EXSTYLE);
            if (topmost)
            {
                exStyle |= WS_EX_TOPMOST;
            }

            // Скрываем окно из Alt+Tab: оно рабочий инструмент, а не приложение.
            exStyle |= WS_EX_TOOLWINDOW;

            if (mode == TransparencyMode.LayeredColorKey)
            {
                exStyle |= WS_EX_LAYERED;
            }

            SetWindowLong(_hwnd, GWL_STYLE, (int)style);
            SetWindowLong(_hwnd, GWL_EXSTYLE, (int)exStyle);

            if (mode == TransparencyMode.Dwm)
            {
                var margins = new Margins { cxLeftWidth = -1, cxRightWidth = -1, cyTopHeight = -1, cyBottomHeight = -1 };
                DwmExtendFrameIntoClientArea(_hwnd, ref margins);
            }
            else if (mode == TransparencyMode.LayeredColorKey)
            {
                var key = config != null ? config.colorKey : new Color32(255, 0, 255, 255);
                var crKey = (uint)(key.r | (key.g << 8) | (key.b << 16));
                SetLayeredWindowAttributes(_hwnd, crKey, 0, LWA_COLORKEY);
            }

            SetWindowLong(_hwnd, GWL_STYLE, (int)style);
            SetWindowPos(
                _hwnd,
                topmost ? HWND_TOPMOST : 0,
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED);

            DockBottomRight();
            _applied = true;
            Debug.Log($"[Lilith] прозрачность окна: {mode} (hwnd={_hwnd.ToInt64()})");
#else
            _applied = true;
            Debug.Log($"[Lilith] прозрачность окна ({mode}) применяется только в Windows-билде; " +
                      "в Editor/Game View её не увидеть — собери Standalone.");
#endif
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

            RECT rect;
            GetWindowRect(_hwnd, out rect);
            var screenW = GetSystemMetrics(SM_CXSCREEN);
            var screenH = GetSystemMetrics(SM_CYSCREEN);
            var width = Mathf.Max(64, config.windowSize.x);
            var height = Mathf.Max(64, config.windowSize.y);
            var x = config.dockBottomRight ? screenW - width - config.windowMargin : rect.Left;
            var y = config.dockBottomRight ? screenH - height - config.windowMargin : rect.Top;
            SetWindowPos(_hwnd, topmost ? HWND_TOPMOST : 0, x, y, width, height, SWP_NOACTIVATE);
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

            var screenW = GetSystemMetrics(SM_CXSCREEN);
            var screenH = GetSystemMetrics(SM_CYSCREEN);

            RECT rect;
            GetWindowRect(_hwnd, out rect);
            var width = rect.Right - rect.Left;
            var height = rect.Bottom - rect.Top;
            if (width <= 0 || height <= 0)
            {
                width = config.windowSize.x;
                height = config.windowSize.y;
            }

            var x = screenW - width - config.windowMargin;
            var y = screenH - height - config.windowMargin;
            SetWindowPos(_hwnd, topmost ? HWND_TOPMOST : 0, x, y, width, height, SWP_NOACTIVATE);
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
            else
            {
                // DWM/Off: полностью прозрачный фон (альфа 0)
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
        }
    }
}
