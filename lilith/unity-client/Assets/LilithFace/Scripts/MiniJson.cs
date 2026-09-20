// LILITH-CORE · Unity-клиент лица (этап 6)
// Минимальный JSON-сериализатор/парсер без зависимостей.
//
// Зачем свой, а не JsonUtility: кадры продюсера — это «плоские словари» с полями,
// которые заранее неизвестны (hello приносит sample_rate/chunk_bytes/personas,
// persona — вложенные voice/face/card, stats — произвольный набор). JsonUtility
// так не умеет, а тянуть Newtonsoft (com.unity.nuget.newtonsoft-json) ради этого
// не хочется: клиент должен собираться из коробки.
//
// Поддерживается: объекты, массивы, строки (с \uXXXX и экранированием), числа
// (long/double), true/false/null. Достаточно для всего протокола face-продюсера.

using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace Lilith.Face
{
    /// <summary>Парсер и сериализатор JSON «в лоб» (словари/списки/скаляры).</summary>
    public static class MiniJson
    {
        // -- сериализация --------------------------------------------------------- //
        /// <summary>Сериализовать словарь/список/скаляр в JSON-строку.</summary>
        public static string Serialize(object value)
        {
            var builder = new StringBuilder(256);
            WriteValue(builder, value);
            return builder.ToString();
        }

        private static void WriteValue(StringBuilder sb, object value)
        {
            switch (value)
            {
                case null:
                    sb.Append("null");
                    return;
                case string s:
                    WriteString(sb, s);
                    return;
                case bool b:
                    sb.Append(b ? "true" : "false");
                    return;
                case IDictionary<string, object> dict:
                    WriteObject(sb, dict);
                    return;
                case IEnumerable<object> list:
                    WriteArray(sb, list);
                    return;
                case float f:
                    sb.Append(f.ToString("R", CultureInfo.InvariantCulture));
                    return;
                case double d:
                    sb.Append(d.ToString("R", CultureInfo.InvariantCulture));
                    return;
                case decimal m:
                    sb.Append(m.ToString(CultureInfo.InvariantCulture));
                    return;
                case long l:
                    sb.Append(l.ToString(CultureInfo.InvariantCulture));
                    return;
                case int i:
                    sb.Append(i.ToString(CultureInfo.InvariantCulture));
                    return;
                default:
                    WriteString(sb, value.ToString());
                    return;
            }
        }

        private static void WriteObject(StringBuilder sb, IDictionary<string, object> dict)
        {
            sb.Append('{');
            var first = true;
            foreach (var pair in dict)
            {
                if (!first)
                {
                    sb.Append(',');
                }

                first = false;
                WriteString(sb, pair.Key);
                sb.Append(':');
                WriteValue(sb, pair.Value);
            }

            sb.Append('}');
        }

        private static void WriteArray(StringBuilder sb, IEnumerable<object> list)
        {
            sb.Append('[');
            var first = true;
            foreach (var item in list)
            {
                if (!first)
                {
                    sb.Append(',');
                }

                first = false;
                WriteValue(sb, item);
            }

            sb.Append(']');
        }

        private static void WriteString(StringBuilder sb, string text)
        {
            sb.Append('"');
            foreach (var ch in text)
            {
                switch (ch)
                {
                    case '"':
                        sb.Append("\\\"");
                        break;
                    case '\\':
                        sb.Append("\\\\");
                        break;
                    case '\n':
                        sb.Append("\\n");
                        break;
                    case '\r':
                        sb.Append("\\r");
                        break;
                    case '\t':
                        sb.Append("\\t");
                        break;
                    case '\b':
                        sb.Append("\\b");
                        break;
                    case '\f':
                        sb.Append("\\f");
                        break;
                    default:
                        if (ch < ' ')
                        {
                            sb.Append("\\u").Append(((int)ch).ToString("x4"));
                        }
                        else
                        {
                            sb.Append(ch);
                        }

                        break;
                }
            }

            sb.Append('"');
        }

        // -- разбор ------------------------------------------------------------------ //
        /// <summary>Разобрать JSON-текст в словари/списки/скаляры.</summary>
        public static object Deserialize(string json)
        {
            if (string.IsNullOrEmpty(json))
            {
                return null;
            }

            var index = 0;
            var value = ParseValue(json, ref index);
            return value;
        }

        private static object ParseValue(string json, ref int index)
        {
            SkipWhitespace(json, ref index);
            if (index >= json.Length)
            {
                return null;
            }

            switch (json[index])
            {
                case '{':
                    return ParseObject(json, ref index);
                case '[':
                    return ParseArray(json, ref index);
                case '"':
                    return ParseString(json, ref index);
                case 't':
                    index += 4;
                    return true;
                case 'f':
                    index += 5;
                    return false;
                case 'n':
                    index += 4;
                    return null;
                default:
                    return ParseNumber(json, ref index);
            }
        }

        private static Dictionary<string, object> ParseObject(string json, ref int index)
        {
            var result = new Dictionary<string, object>();
            index++; // '{'
            SkipWhitespace(json, ref index);
            if (index < json.Length && json[index] == '}')
            {
                index++;
                return result;
            }

            while (index < json.Length)
            {
                SkipWhitespace(json, ref index);
                var key = ParseString(json, ref index);
                SkipWhitespace(json, ref index);
                if (index < json.Length && json[index] == ':')
                {
                    index++;
                }

                result[key] = ParseValue(json, ref index);
                SkipWhitespace(json, ref index);
                if (index < json.Length && json[index] == ',')
                {
                    index++;
                    continue;
                }

                if (index < json.Length && json[index] == '}')
                {
                    index++;
                }

                break;
            }

            return result;
        }

        private static List<object> ParseArray(string json, ref int index)
        {
            var result = new List<object>();
            index++; // '['
            SkipWhitespace(json, ref index);
            if (index < json.Length && json[index] == ']')
            {
                index++;
                return result;
            }

            while (index < json.Length)
            {
                result.Add(ParseValue(json, ref index));
                SkipWhitespace(json, ref index);
                if (index < json.Length && json[index] == ',')
                {
                    index++;
                    continue;
                }

                if (index < json.Length && json[index] == ']')
                {
                    index++;
                }

                break;
            }

            return result;
        }

        private static string ParseString(string json, ref int index)
        {
            var sb = new StringBuilder(64);
            if (index >= json.Length || json[index] != '"')
            {
                return "";
            }

            index++;
            while (index < json.Length)
            {
                var ch = json[index++];
                if (ch == '"')
                {
                    break;
                }

                if (ch != '\\')
                {
                    sb.Append(ch);
                    continue;
                }

                if (index >= json.Length)
                {
                    break;
                }

                var esc = json[index++];
                switch (esc)
                {
                    case '"': sb.Append('"'); break;
                    case '\\': sb.Append('\\'); break;
                    case '/': sb.Append('/'); break;
                    case 'n': sb.Append('\n'); break;
                    case 'r': sb.Append('\r'); break;
                    case 't': sb.Append('\t'); break;
                    case 'b': sb.Append('\b'); break;
                    case 'f': sb.Append('\f'); break;
                    case 'u':
                        if (index + 4 <= json.Length &&
                            int.TryParse(json.Substring(index, 4), NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var code))
                        {
                            sb.Append((char)code);
                        }

                        index += 4;
                        break;
                    default:
                        sb.Append(esc);
                        break;
                }
            }

            return sb.ToString();
        }

        private static object ParseNumber(string json, ref int index)
        {
            var start = index;
            while (index < json.Length && "+-0123456789.eE".IndexOf(json[index]) >= 0)
            {
                index++;
            }

            var text = json.Substring(start, index - start);
            if (text.IndexOfAny(new[] { '.', 'e', 'E' }) >= 0)
            {
                return double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out var d) ? d : 0d;
            }

            return long.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out var l) ? l : 0L;
        }

        private static void SkipWhitespace(string json, ref int index)
        {
            while (index < json.Length && char.IsWhiteSpace(json[index]))
            {
                index++;
            }
        }
    }
}
