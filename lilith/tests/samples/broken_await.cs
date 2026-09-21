// Тестовый образец для scripts/check_csharp_syntax.py.
// НЕ КОМПИЛИРУЕТСЯ НАМЕРЕННО: `await` внутри не-async метода — это CS4032,
// та самая ошибка из приёмки Unity-сборки v0.6.1 (хотфикс 0.6.3).
// Файл лежит в tests/samples/ и не входит в unity-client/, поэтому Unity его не видит.

using System.Threading.Tasks;

namespace Lilith.Face.Samples
{
    public class BrokenAwait
    {
        private System.Collections.IEnumerator Coroutine()
        {
            var task = Task.FromResult(1);
            var value = await task;
            yield return value;
        }
    }
}
