// LILITH-CORE · Unity-клиент лица (0.6.7)
// Состояние окна: геометрия дока по углам + персист ручной позиции.
//
// Почему отдельным файлом и БЕЗ Win32 внутри:
//   1. Окно — «питомец»: оно обязано сидеть там, куда его положил хозяин, и переживать
//      перезапуск. Значит нужна чистая функция «угол + размер экрана → прямоугольник».
//   2. Чистую функцию можно судить гвардами и контрольными выстрелами в песочнице,
//      а P/Invoke — только сборкой F7 на машине Кирюши (правило ADR-025).
//      Поэтому вся арифметика и весь JSON живут здесь, а Win32 — в TransparentWindow.
//   3. Файл состояния — data/window_state.json (имя задаёт конфиг). Путь по умолчанию:
//      Application.persistentDataPath, то есть
//      %USERPROFILE%\AppData\LocalLow\<Company>\<Product>\data\window_state.json.
//      Если хочется держать файл рядом с сервером — задай Window State Path в инспекторе.
//
// Формат файла (version 1, числа всегда в инвариантной культуре — иначе запятая
// вместо точки на русской локали ломала бы разбор):
//   {
//     "version": 1,
//     "x": 1400,
//     "y": 380,
//     "width": 512,
//     "height": 640,
//     "dockCorner": "BottomRight",
//     "savedAtUtc": "2026-09-23T18:11:00Z"
//   }

using System;
using System.Globalization;
using System.IO;
using System.Text;
using UnityEngine;

namespace Lilith.Face
{
    /// <summary>
    /// Угол экрана, к которому прижато окно. Порядок значений = порядок цикла F10
    /// (требование архитектора: BR → BL → TR → TL).
    /// </summary>
    public enum DockCorner
    {
        /// <summary>Правый нижний (дефолт этапа 6, хоткей F9).</summary>
        BottomRight = 0,

        /// <summary>Левый нижний.</summary>
        BottomLeft = 1,

        /// <summary>Правый верхний.</summary>
        TopRight = 2,

        /// <summary>Левый верхний.</summary>
        TopLeft = 3,
    }

    /// <summary>Прямоугольник окна в экранных координатах (пиксели, y вниз).</summary>
    public struct WindowRect
    {
        public int X;
        public int Y;
        public int Width;
        public int Height;

        public WindowRect(int x, int y, int width, int height)
        {
            X = x;
            Y = y;
            Width = width;
            Height = height;
        }

        public override string ToString()
        {
            return string.Format(CultureInfo.InvariantCulture, "{0}×{1} @ {2},{3}", Width, Height, X, Y);
        }
    }

    /// <summary>
    /// Сохранённое состояние окна. <see cref="HasPosition"/> — единственный признак
    /// того, что файлу можно верить: без него «прочитали мусор» и «файла нет»
    /// выглядели бы одинаково.
    /// </summary>
    public sealed class WindowState
    {
        public const int CurrentVersion = 1;

        public int Version;
        public int X;
        public int Y;

        /// <summary>Ширина из файла; <c>int.MinValue</c> — в файле её не было, берём из конфига.</summary>
        public int Width;

        /// <summary>Высота из файла; <c>int.MinValue</c> — в файле её не было, берём из конфига.</summary>
        public int Height;

        public DockCorner Corner = DockCorner.BottomRight;

        /// <summary>Единственный признак того, что файлу можно верить.</summary>
        public bool HasPosition;

        /// <summary>Размер окна: из файла, а если его там не было — из конфига.</summary>
        public int ResolveWidth(int fallback)
        {
            return Width >= 64 ? Width : fallback;
        }

        /// <summary>Высота окна: из файла, а если её там не было — из конфига.</summary>
        public int ResolveHeight(int fallback)
        {
            return Height >= 64 ? Height : fallback;
        }

        public override string ToString()
        {
            return HasPosition
                ? string.Format(CultureInfo.InvariantCulture, "x={0} y={1} {2}×{3} угол={4} v{5}",
                                X, Y, ResolveWidth(0), ResolveHeight(0), Corner, Version)
                : "нет сохранённой позиции";
        }
    }

    /// <summary>
    /// Геометрия дока, цикл углов и чтение/запись файла состояния. Всё — чистые функции,
    /// кроме <see cref="Save"/>/<see cref="Load"/>, которые работают с диском.
    /// </summary>
    public static class WindowStateStore
    {
        /// <summary>Имя файла состояния (относительно каталога данных).</summary>
        public const string FileName = "window_state.json";

        /// <summary>Подкаталог данных: совпадает с ``data/`` проекта.</summary>
        public const string DataDir = "data";

        private const int MinSize = 64;

        // -- геометрия ---------------------------------------------------------- //

        /// <summary>
        /// Куда встанет окно указанного размера в указанном углу экрана.
        /// Чистая функция: гварды считают углы на числах, а не на глаз.
        /// </summary>
        public static WindowRect DockRect(DockCorner corner, int screenW, int screenH,
                                          int width, int height, int margin)
        {
            var w = Math.Max(MinSize, width);
            var h = Math.Max(MinSize, height);
            var m = Math.Max(0, margin);
            var right = screenW - w - m;
            var bottom = screenH - h - m;
            switch (corner)
            {
                case DockCorner.BottomLeft:
                    return new WindowRect(m, bottom, w, h);
                case DockCorner.TopRight:
                    return new WindowRect(right, m, w, h);
                case DockCorner.TopLeft:
                    return new WindowRect(m, m, w, h);
                default:
                    return new WindowRect(right, bottom, w, h);
            }
        }

        /// <summary>Следующий угол по часовой стрелке: BR → BL → TR → TL → BR.</summary>
        public static DockCorner NextCorner(DockCorner corner)
        {
            var values = (DockCorner[])Enum.GetValues(typeof(DockCorner));
            var index = Array.IndexOf(values, corner);
            if (index < 0)
            {
                index = 0;
            }

            return values[(index + 1) % values.Length];
        }

        /// <summary>
        /// Шаг стрелками: новая позиция = старая + дельта. Размер окна не меняется,
        /// поэтому <see cref="Reposition"/> не увидит «изменения размера» и не дёрнет
        /// лишнего.
        /// </summary>
        public static WindowRect MoveBy(WindowRect rect, int dx, int dy)
        {
            return new WindowRect(rect.X + dx, rect.Y + dy, rect.Width, rect.Height);
        }

        /// <summary>
        /// Не выпускать окно за пределы экрана: невидимое окно нельзя ни перетащить,
        /// ни нажать (а F9 его всё равно вернёт в угол — это аварийный выход).
        /// Правило: в пределах экрана обязан оставаться хотя бы
        /// <paramref name="keepVisible"/> px окна по каждой оси.
        /// Экран задан началом (<paramref name="originX"/>, <paramref name="originY"/>)
        /// и размером, потому что на втором мониторе слева координаты отрицательные.
        /// </summary>
        public static WindowRect ClampToScreen(WindowRect rect, int originX, int originY,
                                               int screenW, int screenH, int keepVisible)
        {
            var keep = Math.Max(16, keepVisible);
            var minX = originX + keep - rect.Width;
            var maxX = originX + screenW - keep;
            var minY = originY + keep - rect.Height;
            var maxY = originY + screenH - keep;
            var x = Math.Min(Math.Max(rect.X, minX), Math.Max(minX, maxX));
            var y = Math.Min(Math.Max(rect.Y, minY), Math.Max(minY, maxY));
            return new WindowRect(x, y, rect.Width, rect.Height);
        }

        // -- файл состояния ----------------------------------------------------- //

        /// <summary>
        /// Путь к файлу состояния. Явный путь из конфига важнее каталога данных Unity;
        /// ``baseDir`` пустой — берём <see cref="Application.persistentDataPath"/>.
        /// </summary>
        public static string DefaultPath(string explicitPath, string baseDir)
        {
            if (!string.IsNullOrEmpty(explicitPath))
            {
                return explicitPath;
            }

            var root = string.IsNullOrEmpty(baseDir) ? Application.persistentDataPath : baseDir;
            return Path.Combine(Path.Combine(root, DataDir), FileName);
        }

        /// <summary>Сериализовать состояние в JSON (инвариантная культура, без зависимостей).</summary>
        public static string ToJson(WindowState state)
        {
            if (state == null)
            {
                return "{}";
            }

            var sb = new StringBuilder(192);
            sb.Append('{');
            sb.Append("\"version\": ").Append(state.Version.ToString(CultureInfo.InvariantCulture)).Append(", ");
            sb.Append("\"x\": ").Append(state.X.ToString(CultureInfo.InvariantCulture)).Append(", ");
            sb.Append("\"y\": ").Append(state.Y.ToString(CultureInfo.InvariantCulture)).Append(", ");
            sb.Append("\"width\": ").Append(state.Width.ToString(CultureInfo.InvariantCulture)).Append(", ");
            sb.Append("\"height\": ").Append(state.Height.ToString(CultureInfo.InvariantCulture)).Append(", ");
            sb.Append("\"dockCorner\": \"").Append(state.Corner.ToString()).Append("\", ");
            sb.Append("\"savedAtUtc\": \"")
              .Append(DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture))
              .Append('"');
            sb.Append('}');
            return sb.ToString();
        }

        /// <summary>
        /// Разобрать JSON состояния. Любая беда (мусор, пустой файл, чужая версия,
        /// битые числа) → <c>null</c>, а не исключение: окно не имеет права не
        /// запускаться из-за кривого файла, который оно само же и пишет.
        /// </summary>
        public static WindowState Parse(string json)
        {
            if (string.IsNullOrEmpty(json) || json.IndexOf('{') < 0)
            {
                return null;
            }

            var version = FindInt(json, "version");
            if (version != CurrentVersion)
            {
                // Чужая версия схемы: не угадываем смысл полей, начинаем с чистого листа.
                return null;
            }

            var x = FindInt(json, "x");
            var y = FindInt(json, "y");
            var width = FindInt(json, "width");
            var height = FindInt(json, "height");
            if (x == int.MinValue || y == int.MinValue)
            {
                // Позиции в файле нет — значит и доверять нечему (размер при этом
                // может отсутствовать законно: его возьмём из конфига/персоны).
                return null;
            }

            var corner = DockCorner.BottomRight;
            var cornerName = FindString(json, "dockCorner");
            if (!string.IsNullOrEmpty(cornerName))
            {
                try
                {
                    corner = (DockCorner)Enum.Parse(typeof(DockCorner), cornerName, true);
                }
                catch (ArgumentException)
                {
                    corner = DockCorner.BottomRight;
                }
            }

            return new WindowState
            {
                Version = version,
                X = x,
                Y = y,
                Width = width,
                Height = height,
                Corner = corner,
                HasPosition = true,
            };
        }

        /// <summary>Прочитать состояние с диска. Файла нет / не читается → <c>null</c>.</summary>
        public static WindowState Load(string path)
        {
            if (string.IsNullOrEmpty(path))
            {
                return null;
            }

            try
            {
                if (!File.Exists(path))
                {
                    return null;
                }

                return Parse(File.ReadAllText(path, Encoding.UTF8));
            }
            catch (Exception error)
            {
                Debug.LogWarning($"[Lilith] window_state.json не прочитан ({error.GetType().Name}) — стартуем с доком из конфига");
                return null;
            }
        }

        /// <summary>
        /// Записать состояние на диск. Ошибка записи не должна ронять клиент:
        /// возвращаем <c>false</c>, а причина уходит в Player.log.
        /// </summary>
        public static bool Save(string path, WindowState state)
        {
            if (string.IsNullOrEmpty(path) || state == null || !state.HasPosition)
            {
                return false;
            }

            try
            {
                var dir = Path.GetDirectoryName(path);
                if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
                {
                    Directory.CreateDirectory(dir);
                }

                File.WriteAllText(path, ToJson(state), new UTF8Encoding(false));
                return true;
            }
            catch (Exception error)
            {
                Debug.LogWarning($"[Lilith] window_state.json не записан: {error.GetType().Name}: {error.Message}");
                return false;
            }
        }

        /// <summary>Целое поле JSON по имени (первое вхождение, без вложенных объектов).</summary>
        private static int FindInt(string json, string key)
        {
            var raw = FindRaw(json, key);
            if (string.IsNullOrEmpty(raw))
            {
                return int.MinValue;
            }

            int value;
            return int.TryParse(raw, NumberStyles.Integer, CultureInfo.InvariantCulture, out value)
                ? value
                : int.MinValue;
        }

        /// <summary>Строковое поле JSON по имени.</summary>
        private static string FindString(string json, string key)
        {
            var start = IndexOfKey(json, key);
            if (start < 0)
            {
                return null;
            }

            var open = json.IndexOf('"', start);
            if (open < 0)
            {
                return null;
            }

            var close = json.IndexOf('"', open + 1);
            if (close < 0)
            {
                return null;
            }

            return json.Substring(open + 1, close - open - 1);
        }

        /// <summary>Сырое значение числового поля (до запятой/скобки/пробела).</summary>
        private static string FindRaw(string json, string key)
        {
            var start = IndexOfKey(json, key);
            if (start < 0)
            {
                return null;
            }

            var end = start;
            while (end < json.Length && (char.IsDigit(json[end]) || json[end] == '-' || json[end] == '+'))
            {
                end++;
            }

            return end > start ? json.Substring(start, end - start) : null;
        }

        /// <summary>
        /// Позиция значения поля ``"key":``. Ищем именно пару «ключ + двоеточие»,
        /// чтобы ``"x"`` не совпало с хвостом ``"maxWidth"``.
        /// </summary>
        private static int IndexOfKey(string json, string key)
        {
            var needle = "\"" + key + "\"";
            var from = 0;
            while (from < json.Length)
            {
                var at = json.IndexOf(needle, from, StringComparison.Ordinal);
                if (at < 0)
                {
                    return -1;
                }

                var cursor = at + needle.Length;
                while (cursor < json.Length && char.IsWhiteSpace(json[cursor]))
                {
                    cursor++;
                }

                if (cursor < json.Length && json[cursor] == ':')
                {
                    cursor++;
                    while (cursor < json.Length && (char.IsWhiteSpace(json[cursor]) || json[cursor] == '"'))
                    {
                        cursor++;
                    }

                    return cursor < json.Length ? cursor : -1;
                }

                from = at + needle.Length;
            }

            return -1;
        }
    }
}
