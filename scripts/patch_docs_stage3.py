"""Докатка доков этапа 3: CHANGELOG, README, PLAN, ARCHITECTURE, DECISIONS."""

from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROJECT = ROOT / "Локальная Лилит"
LILITH = ROOT / "lilith"

# --- CHANGELOG
p = PROJECT / "CHANGELOG.md"
s = p.read_text(encoding="utf-8")
if "## Этап 3 — «Память»" not in s:
    old = "# 📜 CHANGELOG — LILITH-CORE\n\nФормат: что добавлено / как проверить / что дальше.\n\n---\n"
    s = s.replace(old, old + """
## Этап 3 — «Память» (v0.3.0, 17.09.2026)

Команда Кирюши: «дальше» + «саммари настраиваемое из программы».

### Добавлено

* `memory/journal.py` — aiosqlite-журнал: messages/facts/meta, изоляция по agent_id,
  кольцевой лимит, метка summarized_upto; данные переживают перезапуск.
* `memory/rag.py` — chromadb-RAG (add/find, cosine): HashEmbedder по умолчанию
  (детерминированный, без моделей и GPU) + опциональный SentenceEmbedder (CPU);
  нет chromadb — слой вежливо отключается.
* `memory/summarizer.py` — авто-саммаризатор: каждые N сообщений через профиль
  `summarizer`; выжимка в facts и RAG; порог читается в момент вызова.
* `memory/runtime.py` — настройки памяти из программы: шестерёнка в панели и
  `POST /api/memory/settings` (summarize_every_n, top_k, rag_enabled, auto_summarize);
  персист в `data/runtime_settings.json`, применение при старте поверх YAML.
* Шина: воспоминания вторым system-сообщением перед ответом; remember_turn после;
  системный кадр «📜 саммари создано» после финального кадра.
* HTTP: `GET/POST /api/memory/settings`, `GET /api/memory/state`, memory-блок в healthz.
* CLI: `--with-memory`.
* Панель: drawer «📜 ПАМЯТЬ · НАСТРОЙКИ».

### Проверка

* pytest: 362 passed (+61 тест памяти и интеграций).
* Живой прогон `--mock-brain --with-memory`: саммари-уведомление после порога,
  персист настроек и журнала в `data/`.

### Дальше

Этап 4 «Уши и горло»: voice/stt.py (faster-whisper + silero-vad + push-to-talk),
voice/tts.py (silero, фолбэк edge-tts); в песочнице интерфейсы + mock.

---
""", 1)
    p.write_text(s, encoding="utf-8")
    print("CHANGELOG ok")

# --- README
p = PROJECT / "README.md"
s = p.read_text(encoding="utf-8")
s = s.replace("| Версия | `0.2.1` (этап 2 + горячая перечитка персоны и счётчик токенов) |",
              "| Версия | `0.3.0` (этап 3: память — журнал, RAG, настраиваемое авто-саммари) |")
s = s.replace("| Этап | 2 из 8 (`brain`) — мозг работает, ждём «дальше» для этапа 3 |",
              "| Этап | 3 из 8 (`memory`) — память живая, ждём «дальше» для этапа 4 |")
s = s.replace("| **3** | Память | aiosqlite-журнал (сообщения, факты), chromadb-RAG (добавить/найти), саммаризатор каждые N сообщений через интерфейс мозга; тесты на mock | ⏳ |",
              "| **3** | Память | aiosqlite-журнал (сообщения, факты), chromadb-RAG (добавить/найти), саммаризатор каждые N сообщений через интерфейс мозга; тесты на mock | ✅ **готово** |")
old = "## 🧠 Мозг включается здесь (этап 2 готов)"
new = """## 📜 Память и настраиваемое саммари (этап 3 готов)

Память включается флагом `features.memory_enabled: true` или `--with-memory`.

* **Журнал** `data/lilith.db` (aiosqlite): все реплики и ответы, факты и саммари,
  изоляция по агентам, переживает перезапуски.
* **RAG** `data/chroma/` (chromadb): перед ответом релевантные воспоминания
  подмешиваются в контекст вторым системным сообщением.
* **Авто-саммари**: каждые N сообщений журнал жуётся в выжимку профилем `summarizer`.
  **N настраивается из программы**: шестерёнка «⚙ память» в панели или
  `POST /api/memory/settings` (`{"summarize_every_n": 25}`); там же `top_k`,
  `auto_summarize`, `rag_enabled`. Значения живут в `data/runtime_settings.json`
  и переживают перезапуск. YAML трогать не нужно.
* Диагностика: `GET /api/memory/state`, блок `memory` в `/healthz`.

## 🧠 Мозг включается здесь (этап 2 готов)"""
if old in s:
    s = s.replace(old, new, 1)
    p.write_text(s, encoding="utf-8")
    print("README ok")

# --- PLAN
p = LILITH / "PLAN.md"
s = p.read_text(encoding="utf-8")
old = "| 3 | Память | aiosqlite-журнал (сообщения, факты), chromadb-RAG (добавить/найти), саммаризатор каждые N сообщений через интерфейс мозга; тесты на mock | ⏳ | — |"
new = "| 3 | Память | aiosqlite-журнал (сообщения, факты), chromadb-RAG (добавить/найти), саммаризатор каждые N сообщений через интерфейс мозга; тесты на mock | ✅ **ГОТОВО**<br>362 теста зелёные | `artifacts/LILITH-CORE_stage3_v0.3.0.zip` |"
if old in s:
    s = s.replace(old, new)
    old2 = "| — | Отправлен отчёт `STAGE1_REPORT.md` + архив. **Жду команду «дальше»** |"
    new2 = ("| 17.09.2026 ~19:20 | **ЭТАП 3 ЗАВЕРШЁН** по команде «дальше»: aiosqlite-журнал, chromadb-RAG "
            "(HashEmbedder по умолчанию), авто-саммаризатор с порогом, настраиваемым из программы "
            "(шестерёнка панели + POST /api/memory/settings + персист), воспоминания в контексте, "
            "--with-memory. 362 теста. Отчёт `STAGE3_REPORT.md`. **Жду «дальше» для этапа 4** |\n"
            "| — | Отправлен отчёт `STAGE1_REPORT.md` + архив. **Жду команду «дальше»** |")
    s = s.replace(old2, new2, 1)
    p.write_text(s, encoding="utf-8")
    print("PLAN ok")

# --- ARCHITECTURE + DECISIONS
p = PROJECT / "docs" / "ARCHITECTURE.md"
s = p.read_text(encoding="utf-8")
if "## 9.5" not in s:
    s += """

## 9.5. Слой памяти (этап 3, готов)

* `MemoryCore` живёт в `app.state.memory` (или None, если выключен).
* Поток реплики: `retrieve()` → второе system-сообщение «ВОСПОМНИНАНИЯ» → ответ мозга →
  `remember_turn()` (журнал + RAG) → при пороге `summarizer.maybe_summarize()`.
* Граница саммаризации — `meta.summarized_upto` на агента; циклы не перекрываются.
* Рантайм-настройки памяти (порог, top_k, тумблеры) меняются из программы и переживают
  перезапуск (`data/runtime_settings.json`), см. ADR-010.
* Эмбеддер по умолчанию HashEmbedder; sentence-transformers — опция конфига (ADR-011).
"""
    p.write_text(s, encoding="utf-8")
    print("ARCHITECTURE ok")

p = PROJECT / "docs" / "DECISIONS.md"
s = p.read_text(encoding="utf-8")
if "ADR-010" not in s:
    s += """
## ADR-010. Настройки памяти меняются из программы и переживают перезапуск (v0.3.0)

**Контекст.** Кирюша: «сделай саммари не на каждые 40, а чтобы настраивать самому
внутри программы». Правка YAML + перезапуск для такого параметра — плохой UX.

**Решение.** Ограниченный набор полей (`summarize_every_n`, `top_k`, `rag_enabled`,
`auto_summarize`) меняется через `POST /api/memory/settings` и шестерёнку панели;
значения сохраняются в `data/runtime_settings.json` и применяются при старте поверх YAML.

**Последствия.** Хозяин крутит поведение памяти на лету; конфиг остаётся источником
«формы», рантайм-файл — источником «поведения сейчас».

## ADR-011. Эмбеддер по умолчанию — HashEmbedder, chromadb ленивый (v0.3.0)

**Контекст.** Sentence-transformers и модели chromadb по умолчанию тянут сотни МБ
и/или сеть при первом запуске; на машине Кирюши CPU/GPU бюджет отдан LLM и голосу.

**Решение.** Дефолт — детерминированный HashEmbedder без моделей; sentence-transformers
включается именем в конфиге; chromadb импортируется лениво и при отсутствии вежливо
отключает RAG, не роняя ядро.

**Последствия.** Память работает из коробки на любом железе; качество поиска
улучшается одной строкой конфига, без переписывания кода.
"""
    p.write_text(s, encoding="utf-8")
    print("DECISIONS ok")
