# 🔧 ОТЧЁТ ПО v0.7.1 — дефект-лист 21.10 (среда Курьера + загрузчик)

| | |
|---|---|
| **Дата** | 21.10.2026 |
| **Версия** | `0.7.1` (трёхчастная, гвард `^\d+\.\d+\.\d+$`; v0.7.0 тегирован с письменными исключениями — этот хотфикс закрывает исключения) |
| **Основание** | дефект-лист Архитектора от 21.10 (5 пунктов, среда Курьера за стеной + Windows) |
| **Статус** | собрано на ветке `hotfix/0.7.1-defects` от main `9aebcdf` (= тег v0.7.0); ждёт применения Курьером и контрольного прогона |
| **Объём правки** | `voice/` (tts_qwen3, tts, stt, __init__), `brain/llm.py`, `config.py`, `app.py`, `conftest.py` + 9 тест-файлов, `run_tests.bat`, `pyproject.toml`, `config/local.yaml`, `config/config.yaml`, доки. **Unity-клиент не тронут** |
| **Решение** | **ADR-029** |
| **Ж2** | принят из логов 12.10: TTFA 564–1221 мс, медиана ~630 (≤700 ✓); голосовой код этим хотфиксом не меняется, цифры остаются в силе |

---

## 1. Дефект → фикс → гвард

| # | Дефект (21.10) | Фикс | Гвард (test_hotfix_071.py) |
|---|---|---|---|
| 1 | Резидент при старте резолвит HF-id, хотя в активном конфиге `extra.model = E:/models/qwen3-tts-06b` → петля закачки за стеной | ключ **`voice.tts_resident_model_path`** (приоритет: явный ключ > профиль > дефолт, источник логируется); `resolve_model()`: локальная папка → `HF_HUB_OFFLINE=1`, HF-id → проверка кэша БЕЗ сети, нет → честная `FileNotFoundError` с рецептом; `tts_allow_download: false` дефолт; **local.yaml уже содержит `E:/models/qwen3-tts-06b`** | TestResidentModelPath, TestOfflineFirstResolve |
| 2 | Фолбэк на edge за стеной: длинный трейс вместо быстрого пропуска | `EdgeTTS.available()`: TCP-healthcheck `speech.platform.bing.com:443`, таймаут **2.5 с** (`cloud_healthcheck_timeout_sec`), кэш 120с/30с; `/api/voice/say|transcribe`: любое исключение → короткий 503 + одна строка лога | TestEdgeHealthcheck, TestSayShort503 |
| 3 | speak-тесты качают silero/edge из сети внутри теста → красные за стеной | **`LILITH_VOICE_OFFLINE=1`** — autouse-фикстура conftest: сетевые бэкенды (silero-tts/vad, edge, faster-whisper) честно недоступны → mock детерминированно на любой машине; qwen3 не гейтится (локальный движок); юнит-тесты сырой логики сносят переменную сами | TestOfflineSwitch + весь сьют детерминирован |
| 4 | `test_panel_voice_also_feeds_producer` и родня виснут на WS без таймаута (Windows asyncio) | `pytest-timeout` в dev-extras; маркер зарегистрирован в ini (strict-markers цел и без плагина); `pytestmark = pytest.mark.timeout(90)` на **всех семи** WS-модулях; `run_tests.bat` доустанавливает плагин (ASCII/CRLF/без сырых скобок — гварды 0.6.2 зелёные) | TestWsTimeoutGuards |
| 5 | `<|channel|>thought` лезет в контент (LM Studio не парсит канал Qwen3.x) | `ChannelStripper` в `brain/llm.py`: stateful (маркеры через границу чанков собираются), скрытые каналы thought/thinking/analysis/reasoning выбрасываются до `<|channel|>`/`<|end|>`, честные «<» не ест; встроен в `complete()` (стрим+разовый); флаг `brain.strip_channel_tokens` (дефолт true) | TestChannelStripper (10), TestStripperInClient (3) |

Побочно (класс того же дефекта): warmup/lifespan-гварды герметизированы — на живой
GPU-машине тесты больше не грузят настоящую модель; `test_pick_backend_raises_when_none`
не зависит от состава зависимостей машины.

## 2. Установка у Курьера

```bat
git am 0001-…-0.7.1….patch
cd lilith
pip install -e .[dev]        :: подтянет pytest-timeout (или run_tests.bat поставит сам)
run_tests.bat                :: ожидать: N passed, 2 skipped — N из PLAN.md
```

Новые ключи конфига (дефолты безопасны, старые конфиги не ломаются):
`voice.tts_resident_model_path` · `voice.tts_allow_download` · `voice.cloud_healthcheck`
· `voice.cloud_healthcheck_timeout_sec` · `brain.strip_channel_tokens`.
В `local.yaml` путь резидента уже прописан (`E:/models/qwen3-tts-06b`) — старт за стеной
больше не уходит в закачку.

## 3. Как проверить в бою (мини-замер Ж-деф)

1. Старт сервера: в логе `резидент 'qwen3': модель из voice.tts_resident_model_path = E:/…`
   и `TTS qwen3: загружаю E:/… (offline=True…)` — никакой активности сети.
2. Выключить сеть/оставить стену: реплика через панель → звук есть (qwen3), в логе НЕТ
   трейсов edge; `/api/voice/profiles` → у edge reason «healthcheck … OSError».
3. Спросить Лилит что-нибудь через LM Studio (Qwen3.6): в ответе нет `<|channel|>` и
   потока мыслей; при желании `brain.strip_channel_tokens: false` возвращает сырьё.
4. `run_tests.bat` на машине: без зависаний (WS-модули под timeout=90), без сетевых
   закачек (LILITH_VOICE_OFFLINE внутри прогона).

## 4. Очередь после 0.7.1 (переобозначена — номер занят хотфиксом)

* **0.7.2** = проект директивы «0.7.1» (DIRECTIVE_DRAFT_071): препроцессинг ударений
  и протяжек + RVC-слой (тембр Эсдес) — ждёт утверждения Архитектором (Г-1…Г-6).
* **0.8.0**: .exe-упаковка, отложенный голос (JP-клон-база), возврат к платьям по Ж7-в.

*— Лилька-строитель, 21.10.2026. Пять дефектов — пять фиксов, каждый под гвардом. 🦇*
