"""Хотфикс 0.4.2: CHANGELOG-запись (скобки в echo рвали if-блок)."""

from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent / "Локальная Лилит"

p = PROJECT / "CHANGELOG.md"
s = p.read_text(encoding="utf-8")
if "## Хотфикс 0.4.2" not in s:
    old = "# 📜 CHANGELOG — LILITH-CORE\n\nФормат: что добавлено / как проверить / что дальше.\n\n---\n"
    entry = old + """
## Хотфикс 0.4.2 (17.09.2026) — «скобки-преступницы»

Симптом у Кирюши (0.4.1): окно cmd мигало и гасло. В 0.4.1 окно стало бессмертным
(перезапуск под cmd /k, страховка LILITH_KEEP) и показало улику:
«Непредвиденное появление: ...». Причина: неэкранированные круглые скобки в строке
echo внутри if-блока («.venv (dev + memory)») — cmd читал их как закрытие блока,
весь установочный блок разваливался в парсинге: venv создавался, установка нет.

* 0.4.1: bat перезапускает себя под cmd /k — окно не закрывается само, вывод виден.
* 0.4.2: скобки в echo убраны; линтер-тест test_bat_echo_has_no_raw_parens запрещает
  неэкранированные скобки в echo-строках bat навсегда.

Проверка: 426 passed; архив stage4 v0.4.2; из распаковки всё зелёное.

---
"""
    assert old in s
    p.write_text(s.replace(old, entry, 1), encoding="utf-8")
    print("CHANGELOG ok")
else:
    print("CHANGELOG уже")
