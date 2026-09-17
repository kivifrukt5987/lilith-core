"""Хотфикс 0.4.1: CHANGELOG-запись о keep-alive окне."""

from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent / "Локальная Лилит"

p = PROJECT / "CHANGELOG.md"
s = p.read_text(encoding="utf-8")
if "## Хотфикс 0.4.1" not in s:
    old = "# 📜 CHANGELOG — LILITH-CORE\n\nФормат: что добавлено / как проверить / что дальше.\n\n---\n"
    s = s.replace(old, old + """
## Хотфикс 0.4.1 (17.09.2026) — «окно больше не умеет умирать молча»

Симптом у Кирюши: двойной клик по start.bat — окно cmd мигает и гаснет, ничего не видно.

* `start.bat` и `run_tests.bat` теперь **перезапускают себя под `cmd /k`**
  (страховка `LILITH_KEEP`): консольная сессия остаётся открытой при любом исходе —
  успех, ошибка, падение на старте — весь вывод виден, окно закрывается только руками.
* Тест-страховка `test_bat_window_never_self_closes`: bat без `cmd /k` больше не пройдёт.

Проверка: 424 passed; архив stage4 v0.4.1.

---
""", 1)
    p.write_text(s, encoding="utf-8")
    print("CHANGELOG ok")
else:
    print("CHANGELOG уже")
