"""Разовая докатка доков после этапа 2: CHANGELOG, PLAN, ARCHITECTURE."""

from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent / "Локальная Лилит"
LILITH = HERE.parent / "lilith"

# --- CHANGELOG: запись этапа 2
p = PROJECT / "CHANGELOG.md"
s = p.read_text(encoding="utf-8")
old = "# 📜 CHANGELOG — LILITH-CORE\n\nФормат: что добавлено / как проверить / что дальше.\n\n---\n"
new = old + """
## Этап 2 — «Мозг» (v0.2.0, 17.09.2026)

Команда Кирюши: «дальше». Мозг подключён, эхо ушло в отставку.

### Добавлено

* `brain/llm.py` — OpenAI-совместимый клиент на httpx: разовый запрос, SSE-стриминг,
  таймауты, ошибки с подсказками, метрики (первый токен, мс, ток/сек, usage/estimated),
  healthcheck профиля (`GET /models`).
* `brain/chat.py` — чат-цикл: persona.md как system, кольцевая история с токен-бюджетом
  (`context_window`), ответ с метриками и стримингом; интерфейс истории готов к замене
  на aiosqlite-журнал этапа 3.
* `brain/mock.py` — mock-мозг: детерминированный, стримит по словам, падает по `fail_on`.
* Шина: `data.profile` (маршрутизация), `data.agent_id` (проводка мультиагентности),
  частичные кадры стриминга с единым `stream_id`, ошибки `brain_error`/`unknown_profile`
  без разрыва сокета.
* `GET /api/brain/profiles` — реестр профилей с healthcheck и кэшем 10 с.
* Панель: селектор профилей со статусами ok/offline/mock, «печатает…», метрики в мете.
* CLI: `--mock-brain` — демо мозга без модели.
* Конфиг: `app.agent_id`, `brain.defaults.context_window`, подсказки в `features`.
* `docs/DECISIONS.md` — журнал архитектурных решений (ADR-001…008).

### Проверка

* pytest: 292 passed (плюс 40 новых тестов мозга и интеграций).
* Живой прогон `--mock-brain`: 12 partial-кадров → финал с метриками; маршрутизация
  в профиль `coder` через `data.profile`.

### Дальше

Этап 3 «Память» по плану: aiosqlite-журнал, chromadb-RAG, саммаризатор каждые N
сообщений через профиль `summarizer`. Жду «дальше».

---
"""
if "Этап 2 — «Мозг» (v0.2.0" not in s:
    assert old in s
    p.write_text(s.replace(old, new, 1), encoding="utf-8")
    print("CHANGELOG ok")
else:
    print("CHANGELOG уже")

# --- PLAN.md: статус этапа 2
p = LILITH / "PLAN.md"
s = p.read_text(encoding="utf-8")
old = "| 2 | Мозг | `brain/llm.py` — OpenAI-совместимый клиент, чат-цикл с `persona.md` как системным промтом, логирование ток/сек; mock-мозг в тестах | ⏳ жду «дальше» | — |"
new = "| 2 | Мозг | `brain/llm.py` — OpenAI-совместимый клиент, чат-цикл с `persona.md` как системным промтом, логирование ток/сек; mock-мозг в тестах | ✅ **ГОТОВО**<br>292 теста зелёные | `artifacts/LILITH-CORE_stage2_v0.2.0.zip` |"
if old in s:
    s = s.replace(old, new)
    old2 = "| — | Отправлен отчёт `STAGE1_REPORT.md` + архив. **Жду команду «дальше»** |"
    new2 = ("| 17.09.2026 ~17:40 | **ЭТАП 2 ЗАВЕРШЁН** по команде «дальше»: клиент LLM (httpx+SSE), "
            "чат-цикл с persona.md и токен-бюджетом, mock-мозг, стриминг partial-кадрами, "
            "`agent_id` и `data.profile` в шине, `/api/brain/profiles` с healthcheck, селектор "
            "профилей в панели, `--mock-brain`. 292 теста. Отчёт `STAGE2_REPORT.md`. "
            "**Жду «дальше» для этапа 3** |\n"
            "| — | Отправлен отчёт `STAGE1_REPORT.md` + архив. **Жду команду «дальше»** |")
    assert old2 in s
    s = s.replace(old2, new2)
    p.write_text(s, encoding="utf-8")
    print("PLAN ok")
else:
    print("PLAN уже")

# --- ARCHITECTURE: чек-лист этапа 2 выполнен
p = PROJECT / "docs" / "ARCHITECTURE.md"
s = p.read_text(encoding="utf-8")
old = "## 9. Что делать на этапе 2 (чек-лист)"
new = "## 9. Чек-лист этапа 2 — **ВЫПОЛНЕН** (v0.2.0; детали в `STAGE2_REPORT.md`)"
if old in s:
    p.write_text(s.replace(old, new), encoding="utf-8")
    print("ARCHITECTURE ok")
else:
    print("ARCHITECTURE уже")
