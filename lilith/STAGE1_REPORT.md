# 🦇 ОТЧЁТ ПО ЭТАПУ 1 — «СКЕЛЕТ»

**Проект:** LILITH-CORE · **Версия:** 0.1.2 · **Дата:** 16–17.09.2026
**Тесты:** 477 passed · **Целевая машина:** Windows, RTX 3060 12GB, i7-12700KF, 32GB RAM

> **HOTFIX 0.1.2 (17.09.2026, one-click).** Кирюша получил красную простыню в PowerShell
> на ручных командах — ручки упразднены: `start.bat` теперь сам создаёт `.venv`, ставит
> зависимости, поднимает сервер и **сам открывает браузер** с панелью; рядом лежит
> `ПРОЧТИ_МЕНЯ.txt` на три строки. Переключение PYTHON на `.venv` вынесено из скобочных
> блоков (структурное лечение регрессии 0.1.1), после установки добавлен самоконтроль
> `import lilith_core`. Брать архив **v0.1.2**.
>
> **HOTFIX 0.1.1 (17.09.2026).** Первый архив (16.09) содержал bat-файлы с LF-переводами
> строк и кириллицей в UTF-8 — `cmd.exe` разбирал их в кашу. В 0.1.1 bat-файлы стали
> CRLF + только ASCII, добавлен реестр профилей мозга. Подробности — `CHANGELOG.md`.

---

## 1. Что сделано

| Блок | Файлы | Суть |
|---|---|---|
| **Конфиг** | `src/lilith_core/config.py`, `config/config.yaml`, `.env.example` | pydantic-settings, 11 секций (заложены сразу под все 8 этапов). Источники: YAML + `.env` + окружение `LILITH_<РАЗДЕЛ>__<ПОЛЕ>`. Приоритет: **env > .env > YAML > defaults**. `extra="forbid"` — опечатка в конфиге падает на старте. Секреты в `SecretStr`, читаются из окружения по имени из `*_env`, в логах и `/healthz` маскируются |
| **Логи** | `logging_setup.py` | loguru: консоль + файл, ротация 10 МБ, хранение 14 дней, zip. `InterceptHandler` заворачивает стандартный `logging` (uvicorn/fastapi/asyncio) в тот же файл. Инициализация идемпотентна |
| **Сервер** | `app.py`, `run.py`, `__main__.py` | FastAPI + uvicorn, фабрика `create_app(settings)`, lifespan, CORS, JSON-ошибки. Эндпоинты: `/`, `/healthz`, `/api/version`, `/api/state`, `/docs` (при `debug`), `WS /ws` |
| **Шина** | `protocol.py`, `session.py` | Конверт `{id, type, text?, data?, ts}`, типы `hello/chat/ping/pong/system/log/error/state`. `parse_raw()` не бросает исключений — кривой пакет не рвёт сокет. `SessionManager`: реестр, лимиты, счётчики, `broadcast()` |
| **Эхо** | `echo.py` | Обработчик реплик этапа 1. Зафиксирован контракт `ReplyHandler = Callable[[str, dict], Awaitable[Message]]` — на этапе 2 мозг встанет на его место одной строкой |
| **Персона** | `persona/__init__.py`, `persona/persona.md` | Системный промт Лилит + плейсхолдеры `$user_name`, `$persona_name`, `$now`, `$date`, `$time`, `$weekday`, `$version`, `$stage`. Нет файла → сервер стартует и пишет предупреждение |
| **Веб-панель** | `webui/index.html` | Один файл, **без CDN и внешних ресурсов** (работает офлайн). Статус соединения, версия/этап/режим, пинг, счётчики ↑/↓, автореконнект с экспоненциальной задержкой, heartbeat 15 с, Enter/Shift+Enter. Уже нарисован баннер подтверждения с 25-с отсчётом — включится на этапе 6 |
| **Обвязка** | `start.bat`, `run_tests.bat`, `scripts/ws_client.py`, `scripts/build_stage_archive.py`, `pyproject.toml`, `requirements.txt`, `.gitignore` | Автопроверка Python ≥ 3.11, создание `.venv`, установка зависимостей, создание `.env`, запуск. Extras по этапам: `[brain] [memory] [voice] [face] [hands] [bridges] [stream]`. `ws_client.py` — диалог с шиной из консоли, `build_stage_archive.py` — сборка zip-артефакта этапа |
| **Документация** | `README.md`, `docs/ARCHITECTURE.md`, `CHANGELOG.md` | Запуск на Windows, протокол шины, диагностика, FAQ, архитектура и контракты между этапами, чек-лист этапа 2 |

### Тесты — 477, все зелёные

```
test_config.py       — YAML, приоритет источников, .env, поиск конфига, resolve_path,
                       валидация диапазонов, маскировка секретов, флаги
test_protocol.py     — фабрики, сериализация, битый JSON, не тот тип, bytes, пустые пакеты
test_session.py      — регистрация/лимиты, счётчики, broadcast, мёртвый сокет, конкурентность
test_logging.py      — файл и каталоги, идемпотентность, уровни, консоль, base_dir,
                       перехват stdlib-logging, неизвестный уровень, исключения
test_persona.py      — подстановки, неизвестный плейсхолдер, отсутствующий файл, extra
test_app_http.py     — панель (в т.ч. «нет внешних ресурсов»), healthz, version, state,
                       Swagger в debug / 404 в prod
test_app_ws.py       — рукопожатие, эхо (unicode, 5000 символов, 20 подряд), ping/pong,
                       ошибки, лимит размера, «тихие» типы, счётчики, подмена обработчика
                       на «мозг», падение обработчика -> error
test_echo.py         — заглушка и её фабрика
test_run.py          — CLI: --show-config, флаги uvicorn, OSError -> 1, Ctrl+C -> 130
test_structure.py    — состав репо, импорты, докстринги, нет print() и секретов,
                       полнота конфига под 8 этапов, наличие инструкций в README,
                       сборка zip-архива этапа и отсутствие в нём .env/логов/кэшей
```

### Что проверено «вживую» (не только тестами)

Реальный `uvicorn` поднят в песочнице, прогнаны HTTP + WebSocket:

```
GET /healthz     -> status ok, version 0.1.0, stage 1, persona.exists true (3678 символов)
GET /            -> 200, text/html, 14569 bytes
GET /docs        -> 200
WS  /ws          -> hello -> system -> ping/pong -> chat-эхо (0.001 мс)
                    битый JSON -> error, «teleport» -> error, сокет жив
GET /api/state   -> active 0, total_connections 1, errors 0 (сессия корректно снята)
```

---

## 2. Как запустить локально (Windows)

```bat
:: 1. из корня проекта "Локальная Лилит"
py -3.11 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -e .[dev]

:: 2. конфиг и секреты (на этапе 1 менять ничего не нужно)
copy .env.example .env

:: 3. тесты
pytest -q                              :: ожидаем "477 passed"

:: 4. старт
python -m lilith_core.run              :: или двойной клик по start.bat
```

Откроется: панель <http://127.0.0.1:8765/>, диагностика <http://127.0.0.1:8765/healthz>,
Swagger <http://127.0.0.1:8765/docs>. Остановка — `Ctrl+C`.

Другие варианты запуска:

```bat
python -m lilith_core.run --port 9000          :: другой порт
python -m lilith_core.run --host 0.0.0.0       :: доступ из локальной сети
python -m lilith_core.run --reload             :: автоперезагрузка при правке кода
python -m lilith_core.run --log-level DEBUG
python -m lilith_core.run --show-config        :: эффективный конфиг без запуска сервера
python scripts\ws_client.py -i                 :: диалог с шиной из консоли
```

---

## 3. Что проверить руками (чек-лист)

- [ ] `pytest -q` → **477 passed**
- [ ] `python -m lilith_core.run --show-config` → печатает секции, ключ замаскирован,
      `persona существует: True`
- [ ] `python -m lilith_core.run` → в консоли баннер `LILITH.EXE v0.1.0 | stage 1`
      и адрес с **тем портом, который реально поднят**
- [ ] Браузер `http://127.0.0.1:8765/` → тёмно-фиолетовая панель, точка «на связи» (зелёная),
      в шапке `v0.1.0 · этап 1 · echo`, в логе строка «🦇 рукопожатие … сессия …»
- [ ] Написать «привет» → ответ тем же текстом с пометкой `[ЭХО · этап 1]` и временем в мс
- [ ] Кириллица и эмодзи не ломаются (`мур-мур 🦇` приходит как есть)
- [ ] Кнопка «пинг» → в шапке появляется «пинг N мс»
- [ ] Кнопка «переподключить» → точка мигает жёлтым и снова зеленеет
- [ ] Закрыть вкладку → `http://127.0.0.1:8765/api/state` показывает `active: 0`,
      а `total_connections` увеличился
- [ ] `http://127.0.0.1:8765/healthz` → `"status": "ok"`, `"persona": {"exists": true}`,
      в `brain.api_key` — `не задан` (секрет не светится)
- [ ] Убить сервер и снова открыть панель → красная точка и «переподключение через N c»,
      после запуска сервера панель сама оживает (проверить автореконнект)
- [ ] `logs\lilith.log` создан; в нём видны строки запуска, подключений и сообщений,
      а также сообщения uvicorn (перехват стандартного logging работает)
- [ ] Написать в конфиг заведомо неверный ключ (например `port: 999999`) →
      сервер **не стартует**, а падает с внятной ошибкой валидации
- [ ] `python scripts\ws_client.py -i` → диалог из консоли, `Ctrl+C` — выход

---

## 4. Известные ограничения этапа 1 (так и задумано)

* Сервер отвечает **эхом**: мозг появится на этапе 2. Точка замены — `build_reply_handler()`.
* История диалога не сохраняется (память — этап 3).
* Панель не отдаёт звук и не показывает аватар (голос — этап 4, лицо — этап 5).
* Баннер подтверждения в панели нарисован, но не активен: сервер пока не шлёт `state`
  с `confirm` (руки — этап 6).
* `/docs` открыт только при `app.debug: true`; для `env: prod` его следует держать выключенным.

---

## 5. Готовность к этапу 2

Контракт уже зафиксирован, переписывать панель и шину не придётся:

```python
# echo.py  ->  brain/llm.py
ReplyHandler = Callable[[str, dict], Awaitable[Message]]
handler = build_reply_handler(settings)      # при features.brain_enabled вернёт мозг
```

В конфиге уже лежат все нужные поля: `brain.base_url`, `brain.model`, `brain.api_key_env`,
`temperature`, `max_tokens`, `top_p`, `request_timeout_sec`, `max_retries`, `stream`,
`history_max_messages`. В `pyproject.toml` есть extra `[brain]` (`openai>=1.30`).
Чек-лист этапа 2 — в `docs/ARCHITECTURE.md`, раздел 9.

**Жду команду «дальше».** 🦇
