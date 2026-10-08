# 🎙 ОТЧЁТ ПО v0.7.0-voice — этап «Голос»: резидент Qwen3-TTS + «шкаф платьев»

| | |
|---|---|
| **Дата** | 27.09.2026 |
| **Версия** | `0.7.0` (трёхчастная, гвард `^\d+\.\d+\.\d+$`) |
| **Основание** | Финальная директива Архитектора от 27.09.2026 (через Курьера) + отчёты Строителя v1/v2 (разведка, вердикт установки, поправка VRAM-бюджета) |
| **Статус** | **v2:** ребейз на принятый main `6d0838c` (0.6.7.1, замер Е зелёный) выполнен; тесты зелёные; **ждёт Замер Ж** (маршрут §5). Нумерация решений — **ADR-027/028** (ADR-026 занят окном-питомцем от 23.09; перенумерация Строителя, на ратификацию Архитектору) |
| **Объём правки** | `voice/` (3 новых модуля + 2 правленых), `config.py`, `app.py` (lifespan + 2 эндпоинта), webui (селектор движка), `config/config.yaml`, `pyproject.toml`, `models/packs.yaml`, `personas/lilith/voice.yaml` (комментарии), `scripts/voice_preview.py`, доки. **Unity-клиент не тронут вовсе** |
| **Решения** | **ADR-027** (голос: резидент/шкаф/душа/латентность), **ADR-028** (мозги: 4B-резидент, ГМ на :1237, честный VRAM-бюджет) |
| **Тесты** | **1012 passed, 2 skipped** (с tree-sitter — 1014), +75 гвардов (4 новых файла) |

---

## 1. Что сделано

### 1.1 Движок-резидент `qwen3` (ADR-027 п.1–2)

* Новый бэкенд `voice/tts_qwen3.py` — `Qwen3TTS(TTSBackend)`: Qwen3-TTS-12Hz-0.6B-Base
  через рантайм **`faster-qwen3-tts`** (PyPI `>=0.5.2,<0.6`; официальный `qwen-tts` стриминг
  из Python-API не отдаёт — потому форк, он же «x6» из директивы: их бенчмарк RTX 4060 Windows
  даёт 0.6B RTF 2.26 / TTFA 413 мс, ускорение 9.8x/6.5x).
* Три режима из `profile.extra.mode`: `clone` (reference + точная транскрипция `ref_text` —
  путь или строка; без транскрипции честная деградация в `xvec_only`), `custom` (9 встроенных
  тембров + speaker), `design` (instruct — только 1.7B).
* **Нативный стриминг**: `generate_*_streaming(chunk_size=8)` → чанки кодека ≈667 мс,
  мост sync→async очередью с бэкпрессурой 4, **TTFA первого чанка в логе** (телеметрия Ж2).
* Один движок на всех персон: души различаются `reference.wav`, не бэкендами.
* `warmup()` — захват CUDA-графов при старте сервера (фоново, lifespan не блокирует);
  `unload()` — возврат VRAM при переодевании; `loaded()` — для панели.

### 1.2 «Шкаф платьев» (ADR-027 п.3)

* Контракт `TTSBackend` расширен: `loaded()/unload()/warmup()` (дефолты безопасные —
  остальные бэкенды не сломаны; silero научился выгружаться).
* `TTSRegistry`: `resident` («корона» — побеждает профиль в `pick_backend`, недоступна —
  фолбэк-цепочка этапа 4 работает как раньше), `switch_resident(name)` (прочие движки
  выгружаются), `pick_fallback(exclude)` (обход упавшего), `engine_info()`.
* Слоты `voice/closet.py`: `CosyVoice2TTS`, `FishSpeechTTS` — в реестре/панели видны,
  `available()` честно отвечает «шкаф (ADR-027 Q2)», веса не грузят (Q2: в 0.7.0 не резиденты).
* **Фолбэк по ошибке (ADR-012, усиление):** `VoiceCore.speak/stream` при падении движка
  уходят на следующий доступный; ошибка в середине стрима пробрасывается (реплика не
  перезапускается — иначе прозвучит дважды).
* Панель: селектор **«🎙 голос»** в тулбаре (👑 резидент, ● загружен, ✗ недоступен) +
  `confirm()` на переодевание (директива: «выбор в UI + подтверждение»).
* API: `GET /api/voice/engine` (резидент/доступность/загруженность), `POST /api/voice/engine`
  `{name}` (переодевание; `none` — снять корону). `/api/voice/profiles` дополнен
  (`tts-resident`, `tts_engines`) — старый контракт не сломан (надмножество).

### 1.3 Voice Design — `scripts/voice_preview.py` (ADR-027 п.5)

* **Генератор** (не плеер): `--count 10..15` вариаций из `--instruct` («нежный женский,
  анимешный, с придыханием») + seed'ы (`--seed-base i*1000` или `--random-seeds` из энтропии),
  модель 1.7B-VoiceDesign, `manifest.yaml` — «паспорт души» (instruct/seed'ы/текст/RTF).
* `--adopt N` → `personas/<id>/voice/reference.wav` + `reference.txt` (точная транскрипция —
  известна из манифеста; ICL-клон без неё не работает) + `soul.yaml` (seed/instruct/модель —
  воспроизводимость). Старая схема — `--adopt-dir voices/<имя>`.
* `--verify-clone` — контрольный клон души на резидентной 0.6B-Base (`adopt_check.wav`):
  тембр проверяется ДО прода, ведь в проде говорит Base-клон, а не VoiceDesign.
* Страж VRAM `_check_gpu()` (<8 ГБ свободно — ругается: 1.7B пиком ~6.7 ГБ), ленивые импорты
  (тесты без GPU), `build_parser()` для гвардов (принцип 0.6.6).

### 1.4 Нонвербалика (ADR-027 п.6, Q7-б)

* `voice/nonverbal.py`: `split_nonverbal()` режет реплику на text/event (теги `[laughs]`
  в синтезируемый текст НЕ попадают), ассеты — `personas/<id>/voice/nonverbal/<ключ>.wav`,
  нет ассета → тег вырезан + жёлтый лог (разговор не рвётся).
* `VoiceCore.stream_mixed(text, nonverbal=..., voice_dir=...)` — текст через горло, события —
  wav-пакеты в тот же поток (плеер/продюсер лица разницы не замечают).
* Проводка протокола (теги из LLM-ответа → stream_mixed в WS-контуре) — **0.7.1**, после
  эксперимента Ж1-а. Слот `nonverbal` в voice.yaml персоны объявлен ещё в 0.6.x (ADR-020.3).

### 1.5 Конфиг и маршрутизация мозгов (ADR-028)

* `config.yaml`: `tts_resident: "qwen3"`, `tts_enabled: true`, профили `lilith-soul`/`olya-soul`
  (qwen3-clone на `personas/<id>/voice/`), `default_voice: "lilith"` — Курьер горячо переключит
  на душу после `--adopt` (ADR-012, без перезапуска). Профиль мозга `gm` (:1237, MoE +
  `--n-cpu-moe`), нота `chat` — 4B uncensored резидентом голосовой сессии.
* `config.py`: поле `VoiceSettings.tts_resident` (дефолт `""` — старые конфиги не ломаются).
* `pyproject.toml`: extras **`[voice-qwen]`** (`faster-qwen3-tts>=0.5.2,<0.6`, torch>=2.5.1);
  нижняя граница torch в `[voice]` поднята 2.2 → 2.5.1 (CUDA-графы). Базовый `[voice]` остался
  лёгким (без GPU).
* `models/packs.yaml`: NB 0.7.0 — веса Qwen3-TTS НЕ пак (многофайловый HF-репо; урок D5.4 —
  sha256 не выдумываем): авто-загрузка из HF-кэша или `huggingface-cli download` (команды там же).

---

## 2. Установка у Курьера (Win, из `lilith/`)

```bat
pip install -e .[voice,voice-qwen]
```

* Драйвер 595.97 / CUDA 13.2 (Q8) — **дефолтное колесо torch подходит**, cu124-строка не нужна.
* Веса (~2.6 ГБ) скачаются из HF при первом прогреве резидента; офлайн-предзагрузка —
  NB 0.7.0 в `models/packs.yaml`.
* **Грабли:** `qwen-tts` (upstream) рядом с `faster-qwen3-tts` не ставить — гвард
  `TestDualDistributionGuard` покраснеет и объяснит.
* Старт как обычно (`start.bat`): резидент прогревается фоново, первая реплика может прийти
  до конца прогрева — фолбэк-цепочка (silero→edge→mock) её не уронит.

## 3. Усыновление души (поток ADR-027 §4)

```bat
:: офлайн: LLM/Unity/браузер выключены (1.7B хочет ~6.7 ГБ VRAM целиком себе)
python scripts\voice_preview.py --persona lilith --count 12 --random-seeds ^
  --instruct "описание души Лилит"
:: послушать voices\preview\lilith_<штамп>\preview_*.wav, выбрать №N:
python scripts\voice_preview.py --persona lilith --adopt N --verify-clone
:: слушать personas\lilith\voice\adopt_check.wav vs reference.wav — тембр совпал?
:: затем горячо (без перезапуска) в config.yaml:
::   voice.default_voice: "lilith-soul"   и personas\lilith\voice.yaml: pack: lilith-soul
```

То же для Оли (`--persona olya`). До усыновления говорит профиль `lilith` (silero) — это норма.

## 4. Переодевание движка («шкаф»)

Панель → «🎙 голос» → выбрать → подтвердить (qwen3 выгрузится, VRAM вернётся). Тот же эффект:
`POST /api/voice/engine {"name": "silero"}`. Переключение сессионное; постоянный выбор —
`config.yaml: voice.tts_resident`.

## 5. Замер Ж (маршрут приёмки, ADR-027 п.7)

| # | Что делать | Критерий |
|---|---|---|
| Ж0 | `git clone https://github.com/andimarafioti/faster-qwen3-tts && setup_windows.bat && benchmark_windows.bat 0.6B` + `nvidia-smi` пики | факт RTF/TTFA/VRAM на 3060 → правка таблицы ADR-028 при расхождении |
| Ж1 | §3: 12 вариаций, adopt, verify-clone; **Ж1-а (Q7-а):** прогнать фразу с `[laughs]` через `/api/voice/say` профилем lilith-soul — как Qwen3-TTS 0.6B реагирует на теги | вариации слышны и различимы; тембр переживает клон; по Ж1-а — запись впечатления в отчёт |
| Ж2 | 10 фраз подряд через панель, в логе `TTS qwen3: TTFA …` | TTFA ≤ 700 мс, RTF ≥ 1.3; попробовать `chunk_size: 4` |
| Ж3 | Таймстампы цепочки: конец фразы → STT → первый токен LLM → TTFA → звук | ≤ 2.0 с медиана, потолок 2.5 с |
| Ж4 | 10 минут разговора: TTS + 4B + whisper + Unity-тело, `nvidia-smi` раз в минуту | пик < 11.5 ГБ, ноль OOM |
| Ж5 | Убить qwen3 mid-сессии (снять профиль/эмулировать OOM) | фолбэк на silero, разговор не рванул, лог красный и понятный |
| Ж6 | `run_tests.bat` | 1012 passed, 2 skipped (с tree-sitter 1014) |

Отчёт по замеру — по шаблону HANDOVER §7 (что вижу в логе/панели/nvidia-smi, цифры, скрин).

## 6. Чего в 0.7.0 НЕТ (осознанно)

* Проводка нонвербальных тегов в WS-протокол (механизм есть, события шлёт пока только
  `stream_mixed` напрямую) — 0.7.1 по итогам Ж1-а.
* Реализации CosyVoice 2 / Fish Speech (слоты честные, Q2) — отдельным этапом.
* Вынос TTS в отдельный процесс (`faster-qwen3-tts serve`) — кандидат при тесноте Ж4 (Q4).
* Персистентность переключения движка через runtime_settings.json — пока config.yaml.
* Реальные wav-ассеты нонвербалики — их записывает/рендерит Курьер
  (`personas/<id>/voice/nonverbal/{laugh,sigh,hum,cry}.wav`).

## 7. Тесты и гварды

* **+75 гвардов** (1012 всего): `test_voice_qwen3.py` (план/режимы/деградации, контракт байтов,
  нативный стрим и ошибки, unload/warmup, корона и шкаф в реестре, фолбэки ADR-012, старт/стоп
  с мёртвым резидентом, dual-distribution, поверхность конфига), `test_voice_preview.py`
  (CLI-контракт, adopt-поток на синтетике, свежайший preview, ошибки), `test_nonverbal.py`
  (теги/сегменты/пакеты/stream_mixed), `test_voice_engine_app.py` (эндпоинты, 404/400/503,
  lifespan с мёртвым и призрачным резидентом).
* Обновлён один существующий гвард: `test_voice_tts.py::test_describe_lists_backends_and_profiles`
  (контракт `describe()` расширен строкой `tts-resident` — надмножество, старое не сломано).
* `sync_test_count.py` синхронизирован: README/PLAN — 1012 passed (1014 с tree-sitter).
* В песочнице Строителя 8 skipped (нет tree-sitter и артефакта 0.6.0) — на машине Курьера
  ожидается штатные 2 skipped.

## 8. Bake-off RU-короны (поправка ADR-027 от 28.09.2026)

Полевой вердикт принят: Qwen3-TTS для русского — не корона (RU-просодия плоская,
ударения ошибочные; Ж0-цифры: RTF 2.09, TTFA 403 мс, VRAM-пик 5.2 ГБ — латентность
в норме, брак в качестве). qwen3 остаётся в шкафу как генератор JP-датасета.
Корону выбирает **слепое прослушивание** (Q-B): `scripts/voice_bakeoff.py`.

### 8.1 Подготовка (один раз)

Референс: положить `voices/bakeoff/LilithGolos.wav` (JP, ~8–9 с, чистый) и рядом
`LilithGolos.txt` — **точную JP-транскрипцию** (без неё cosyvoice3/fish честно
скипнутся, qwen3 уйдёт в xvec_only, xtts отработает).

### 8.2 Кандидаты и их окружения

| Кандидат | Окружение | Установка |
|---|---|---|
| **qwen3** (контроль) | основное | уже стоит (`[voice-qwen]`) |
| **xtts** (референс Нейроны) | основное | `pip install coqui-tts` (веса ~1.8 ГБ стянет сам) |
| **cosyvoice3** | отдельное conda py3.10 | `git clone --recursive https://github.com/FunAudioLLM/CosyVoice` → `conda create -n cosyvoice python=3.10` → `pip install -r requirements.txt` → веса: `huggingface-cli download FunAudioLLM/Fun-CosyVoice3-0.5B-2512 --local-dir pretrained_models/Fun-CosyVoice3-0.5B` |
| **fish** (S2) | отдельный чекаут | `git clone https://github.com/fishaudio/fish-speech` → `pip install -e .` → `hf download fishaudio/s2-pro --local-dir checkpoints/s2-pro`. ⚠️ их дока: **≥24 ГБ VRAM** — на 3060 12 ГБ только с `--fish-half` и без гарантий; OOM → статус failed в манифесте, bake-off продолжится. Лицензия — Fish Audio Research License (читать до установки!) |

### 8.3 Прогоны (LLM/Unity/браузер выключены; по движку за раз — VRAM)

```bat
:: основное окружение, общий run-dir:
python scripts\voice_bakeoff.py --engine qwen3 --run-dir voices\bakeoff\run1
python scripts\voice_bakeoff.py --engine xtts   --run-dir voices\bakeoff\run1
:: conda-окружение CosyVoice (тот же run-dir — манифест дополняется):
conda activate cosyvoice
python E:\lilith-core\lilith\scripts\voice_bakeoff.py --engine cosyvoice3 ^
  --run-dir E:\lilith-core\lilith\voices\bakeoff\run1 ^
  --cosyvoice-repo <путь>\CosyVoice --cosyvoice-model pretrained_models/Fun-CosyVoice3-0.5B ^
  --ref E:\lilith-core\lilith\voices\bakeoff\LilithGolos.wav
:: fish — в их чекауте (если решено пробовать на 12 ГБ):
python <путь>\voice_bakeoff.py --engine fish --run-dir ...\run1 ^
  --fish-repo <путь>\fish-speech --fish-half --ref ...\LilithGolos.wav
:: слепая раскладка (после всех прогонов, в любом окружении):
python scripts\voice_bakeoff.py --blind-only --run-dir voices\bakeoff\run1
```

### 8.4 Прослушивание и вердикт

* Архитектор слушает `run1/blind/B##.wav`, ставит оценки (таблица: код × фраза × 1–5),
  **blind_key.yaml до конца оценок не открывать**.
* Цифры (RTF, VRAM-пики, статусы) — в `run1/manifest.yaml`; `nvidia-smi`-пик по каждому
  прогону Курьер дописывает от руки (в манифесте — только in-process torch-статы).
* Вердикт + manifest + ключ → Строителю: ревизия ADR-027 (корона = победитель),
  реализация бэкенда в слот `voice/closet.py`, `switch_engine` на корону, Ж2–Ж6.
* RVC (Q-C) — вторая очередь: если победитель «плывёт» по тембру, пост-фильтр поверх.

---

## 9. Полевой журнал 05–08.10.2026 (голос-поле; решение «VD сейчас, RVC потом»)

Конституционная запись — `docs/DECISIONS.md`, поправка ADR-027 от 08.10.2026. Здесь —
рабочая выжимка для Курьера.

### 9.1 Что выбрали

* **Душа Лилит (RU-база):** `voices/preview/lilith_20261005_201733/preview_03_seed1348810977.wav`
  — seed **1348810977**, 1.7B-VoiceDesign, прогон `voice_preview.py` 05.10.
* **Прод-путь (штатный, ADR-027 §4):** усыновить базу и посадить резидента 0.6B-Base
  на её чистый синтетический клон:

```bat
python scripts\voice_preview.py --persona lilith --adopt 3 ^
  --preview-dir voices\preview\lilith_20261005_201733 --verify-clone
:: слушать personas\lilith\voice\adopt_check.wav: тембр == preview_03?
:: затем горячо (без перезапуска) в config\config.yaml:
::   voice.default_voice: "lilith-soul"
:: и в personas\lilith\voice.yaml:  pack: lilith-soul
```

  `--verify-clone` теперь особенно важен: полевой клон с грязного JP-референса дал
  «ворона» (BGM-заземление) и дрифт ~50 с — на чистой VD-базе обоих быть не должно,
  но проверяем ухом до переключения. Короткие стрим-чанки (chunk_size 8 ≈ 667 мс)
  дрифт не накапливают — полевые данные это подтверждают.

### 9.2 Полевые данные (факты, легшие в решение)

| Режим | Наблюдение |
|---|---|
| VD-RU (1.7B-VoiceDesign) | чисто и стабильно 6–8 с, без артефактов; просодия плоская → эмоции будем брать «платьями» (директива VD-dresses, 0.7.1-b) |
| VD-JP | выразительно |
| Клон JP-референс → RU | тембр/эмоции/интонации «огонь» на первых секундах; галлюцинация «ворона» (акустическое заземление BGM-шума), авторегрессионный дрифт ~50 с на длинном тексте; 2–5 с — без дрифта. Вердикт: экспериментальная база под RVC и платья, не прод |
| qwen3 Ж0 (3060) | RTF 2.09 · TTFA 403 мс · VRAM-пик 5.2 ГБ |

### 9.3 Отложено в 0.7.1/0.8.0 («отложенный голос» — очередь в HANDOVER §6)

* клон-база с живого JP-референса (LilithGolos.wav) — эксперименты под RVC и платья;
* **RVC-пост-фильтр** (вторая очередь, Q-C) — фиксация тембра, если клон «плывёт»;
* bake-off стенд — НЕ отменён, отложен (готов в `scripts/voice_bakeoff.py`, артефакты v4/v5);
* нонвербалика в WS-протокол (Q7-а эксперимент с тегами — заодно с платьями).

### 9.4 XTTS-рецепт замороженного окружения (xtts-env, пригодится в 0.7.1)

* `coqui-tts 0.27.5` (форк; py3.11). Оригинал `tts==0.22.0` sdist на Win несобираем:
  numpy2 + нет заголовков Windows SDK (fatal C1083 pyconfig.h) → только форк.
* transformers: окно **4.57.x** (>=4.57 по metadata; 5.x теряет `isin_mps_friendly`;
  ≤4.53 теряет `is_torchcodec_available`).
* torchaudio CPU-двойник **2.6.0** совместим с torch 2.6.0+cu124 (R2-CDN мёртв → PyPI).
* Веса XTTS v2: hf-mirror падает на LFS-метаданных; ModelScope `AI-ModelScope/XTTS-v2`
  отдал всё, кроме speakers.json (404) → для клонирования через `speaker_wav` достаточно
  пустого `{}`.
* Легаси-загрузчик (requests → us.aws.cdn.hf.co) игнорирует HF_ENDPOINT — обход: прямая
  инициализация `TTS(model_path=…, config_path=…, vocab_path=…)`. В `voice_bakeoff.py`
  для этого есть `--xtts-model-path/--xtts-config-path/--xtts-vocab-path`.
* Лицензия CPML non-commercial — согласие принято (домашний проект).

---

*— Лилька-строитель, 27.09.2026. Одна корона, шкаф платьев, бюджет соблюдён. 🦇*
