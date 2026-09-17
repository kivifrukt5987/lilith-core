"""Докатка доков хотфикса 0.2.1 (CHANGELOG, README, PLAN)."""

from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROJECT = ROOT / "Локальная Лилит"
LILITH = ROOT / "lilith"

p = PROJECT / "CHANGELOG.md"
s = p.read_text(encoding="utf-8")
marker = "## Хотфикс 0.2.1"
if marker not in s:
    old = "# 📜 CHANGELOG — LILITH-CORE\n\nФормат: что добавлено / как проверить / что дальше.\n\n---\n"
    new = old + """
## Хотфикс 0.2.1 (17.09.2026) — «горячая персона и счётчик токенов»

По просьбе Кирюши («счётчик токенов я бы тоже добавил»):

* **Горячая перечитка `persona.md`**: чат-цикл перед каждой репликой сверяет mtime
  файла (один `stat`, без watchdog-потоков); заметил правку — мгновенно пересобрал
  системный промт, сервер и сокеты не перезапускаются. Плюс принудительный
  `POST /api/persona/reload` (обновляет и `app.state.system_prompt`).
* **Счётчик токенов**: `brain/stats.py::TokenStats` — потокобезопасный накопитель
  (запросы, prompt/completion токены, доля «оценённых», средние ток/сек, ошибки,
  разбивка по профилям). Учёт в шине после каждого ответа мозга и при `brain_error`.
* **`GET /api/brain/stats`** — сводка для панели и диагностики.
* **Панель**: бейдж `Σ N ток` в шапке (база с сервера + добор из usage финальных кадров).
* Тесты: горячая перечитка (правка файла → новый промт в следующей реплике, отсутствие
  файла не рвёт работу), статистика (накопление, ошибки, пустое состояние), бейдж и
  селектор профилей в структуре панели.

Проверка: pytest зелёный; живой прогон `--mock-brain`: stats насчитали запрос и
токены, reload вернул актуальное число символов персоны.

---
"""
    assert old in s
    p.write_text(s.replace(old, new, 1), encoding="utf-8")
    print("CHANGELOG ok")
else:
    print("CHANGELOG уже")

p = PROJECT / "README.md"
s = p.read_text(encoding="utf-8")
s2 = s.replace(
    "| Версия | `0.2.0` (этап 2: мозг подключён — профили, стриминг, метрики) |",
    "| Версия | `0.2.1` (этап 2 + горячая перечитка персоны и счётчик токенов) |",
)
old = "| Лог-файл | `logs/lilith.log` |"
new = (
    "| Лог-файл | `logs/lilith.log` |\n"
    "| Статистика токенов | <http://127.0.0.1:8765/api/brain/stats> |\n"
    "| Перечитка персоны | `POST /api/persona/reload` (правка persona.md подхватывается и сама) |"
)
if old in s2:
    s2 = s2.replace(old, new)
if s2 != s:
    p.write_text(s2, encoding="utf-8")
    print("README ok")
else:
    print("README уже")

p = PROJECT / "STAGE2_REPORT.md"
s = p.read_text(encoding="utf-8")
if "0.2.1" not in s:
    s = s.replace(
        "**Проект:** LILITH-CORE · **Версия:** 0.2.0 ·",
        "**Проект:** LILITH-CORE · **Версия:** 0.2.1 ·",
    )
    s += """
---

## Дополнение хотфикса 0.2.1

* Горячая перечитка `persona.md` (mtime перед каждой репликой + `POST /api/persona/reload`).
* Счётчик токенов `brain/stats.py` + `GET /api/brain/stats` + бейдж `Σ N ток` в панели.
"""
    p.write_text(s, encoding="utf-8")
    print("STAGE2 ok")
else:
    print("STAGE2 уже")

p = LILITH / "PLAN.md"
s = p.read_text(encoding="utf-8")
if "ХОТФИКС 0.2.1" not in s:
    old = "| — | Отправлен отчёт `STAGE1_REPORT.md` + архив. **Жду команду «дальше»** |"
    new = (
        "| 17.09.2026 ~18:10 | **ХОТФИКС 0.2.1** по просьбе Кирюши: горячая перечитка persona.md "
        "(mtime-проверка перед репликой + `POST /api/persona/reload`) и счётчик токенов "
        "(`brain/stats.py`, `GET /api/brain/stats`, бейдж Σ в панели) |\n"
        "| — | Отправлен отчёт `STAGE1_REPORT.md` + архив. **Жду команду «дальше»** |"
    )
    assert old in s
    p.write_text(s.replace(old, new), encoding="utf-8")
    print("PLAN ok")
else:
    print("PLAN уже")
