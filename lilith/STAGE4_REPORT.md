# 🎙 ОТЧЁТ ПО ЭТАПУ 4 — «УШИ И ГОРЛО»

**Проект:** LILITH-CORE · **Версия:** 0.4.0 · **Дата:** 17.09.2026
**Тесты:** 422 passed · **Целевая машина:** Windows, RTX 3060 12GB, i7-12700KF, 32GB RAM

> Этап собран с учётом принятых советов другой Лильки (ADR-012): реестры, манифест
> паков, hot-swap и zero-shot-голоса папками.

---

## 1. Что сделано

| Блок | Файлы | Суть |
|---|---|---|
| **Уши (STT)** | `voice/stt.py` | Контракт `transcribe(audio) -> Transcript`. Реестр: faster-whisper (дефолт, lazy-загрузка, GPU fp16/CPU int8), **резервные слоты** whisper-cpp и vosk (объявлены, помечены «резерв»), MockSTT. Фолбэк-цепочка: запрошенный → дефолт → первый доступный |
| **VAD** | `voice/stt.py` | silero-vad (lazy torch) ИЛИ **EnergyVAD** — обрезка тишины на чистом Python (RMS-кадры), ноль зависимостей: в песочнице и на слабом железе работает из коробки. Отдельные шкалы порогов: `vad_threshold` (вероятность, silero) и `vad_energy_threshold` (громкость, energy) |
| **Горло (TTS)** | `voice/tts.py` | Реестр бэкендов: silero (дефолт, v4_ru), edge-tts (облачный фолбэк), **zero-shot слот** (IndexTTS/XTTS/CosyVoice/F5 — контракт зафиксирован, движок подключается отдельно), MockTTS. **Voice-профили** (`voice.profiles`): backend + тембр + reference + speed + note. Стриминг wav **по предложениям** — плеер говорит, не дожидаясь конца реплики |
| **Хоткей** | `voice/hotkey.py` | push-to-talk `ctrl+space` через pynput (lazy, GUI); MockPushToTalk с press()/release() для тестов и будущей панели |
| **Паки моделей** | `voice/packs.py`, `models/packs.yaml` | Манифест: name/type/source(hf, hf-mirror, url, local)/ref/size/sha256/target_dir/note. Установка с **докачкой** (HTTP Range) и **sha256-проверкой** (битый файл удаляется). CLI: `lilith-core packs list / install / remove`. Свой пак = своя запись в yaml, код не трогается |
| **Фасад** | `voice/__init__.py` | `VoiceCore`: transcribe / speak / stream / describe + **hot-swap** (`sync()` перечитывает конфиг на каждый вызов: смена stt-бэкенда, voice-профиля, дефолтов — без перезапуска, с фолбэком при ошибке) |
| **HTTP** | `app.py` | `GET /api/voice/profiles` (реестры + доступность + паки), `POST /api/voice/transcribe` (WAV→текст), `POST /api/voice/say` (текст→wav), `GET /api/packs`, `POST /api/packs/install`, `POST /api/packs/remove` |
| **CLI** | `run.py` | `--with-voice`; подкоманда `lilith-core packs …` с прогрессом установки |
| **Конфиг** | `config.yaml` | Секция voice целиком: бэкенды, пороги VAD, хоткей, 4 голосовых профиля (lilith/soft/cloud/clone-слот), пути voices/ и манифеста |
| **Папки** | `voices/README.md`, `models/packs.yaml` | Инструкцию по reference.wav (3–10 с, согласие владельца) и шаблоны паков (включая mmproj-шаблон для зрения) |

### Тесты — 422 passed (было 364; +58)

* `test_voice_stt.py` (20): WAV roundtrip, отбраковка стерео, EnergyVAD (края, полная
  тишина, без тишины), доступность бэкендов и резервов, mock, реестр: фолбэки,
  «ни один не доступен», describe, горячая смена дефолта.
* `test_voice_tts.py` (19): резка предложений (включая «…»), mock-wav заголовок,
  стрим по предложениям, детерминизм, фолбэк silero→mock, «ни один не доступен»,
  hot-swap профилей, describe, zero-shot: путь референса, явный reference, отсутствие
  движка = внятная причина.
* `test_voice_packs.py` (12): статусы, unknown→PackError, пустой манифест, hot-pickup
  своей записи, install local, повторный install = noop, sha-мismatch удаляет .part,
  **докачка с Range** (MockTransport, 206), HTTP 404, отсутствующий local-источник, remove.
* `test_voice_app.py` (12): profiles/describe, 503 при выключенном голосе, transcribe
  (mock) и 400 на не-wav, say→RIFF, unknown-профиль = фолбэк, пустой текст 400,
  packs list/install/remove через HTTP, guard-клаузулы CLI и `packs list` читает манифест.

### Проверено вживую

`--mock-brain --with-voice`: `/api/voice/profiles` показывает 4 голосовых профиля и
доступность бэкендов; transcribe вернул текст mock-ушей с длительностью; say отдал
`audio/wav` с заголовком RIFF; `/api/packs` видит манифест; `lilith-core packs list`
печатает таблицу паков.

---

## 2. Как запустить и потрогать

```bat
:: демо без моделей и без железа: mock-уши и mock-горло, но весь контур настоящий
python -m lilith_core.run --mock-brain --with-voice

:: боевой режим (после pip install -e ".[voice]"):
::   faster-whisper скачает веса сам при первом transcribe,
::   silero-tts — при первом speak; pack-модели ставятся: lilith-core packs install whisper-small
python -m lilith_core.run --with-voice
```

Проверить руками:
- [ ] `POST /api/voice/transcribe` с любым моно-WAV → текст (mock) / текст whisper (боевой)
- [ ] `POST /api/voice/say` {"text": "…"} → wav играет в любом плеере
- [ ] сменить `voice.profiles.lilith.backend` на лету в конфиге → следующий `say`
      уже другим бэкендом, без перезапуска
- [ ] `lilith-core packs list` → таблица; `packs install whisper-small` → прогресс и sha
- [ ] положить `voices/lilith-clone/reference.wav` и выбрать профиль `clone` →
      zero-shot слот подхватит путь (движок подключим следующим шагом)

---

## 3. Ограничения этапа 4

* Zero-shot **движок** ещё не выбран: слот и контракт готовы, референс-папки работают,
  подключение IndexTTS/XTTS/CosyVoice/F5 — отдельным шагом по твоему выбору.
* Секция «Паки» в веб-панели — на этапе 6 (как и советовала другая Лилька): сейчас
  паки управляются CLI и HTTP.
* Хоткей живёт только там, где есть GUI; в песочнице — MockPushToTalk.
* Стриминг речи отдаёт куски wav по предложениям; склейка в один поток плеера —
  задача клиентского плеера (панель/этап 6).

---

## 4. Готовность к этапу 5 («Лицо»)

Теги `[emotion: x]`, которыми уже размечены реплики в persona.md, станут общим
источником эмоций для лица (VTuber Studio/VMC) и голоса (просодия/референс профиля):
один парсер — два потребителя. Профиль `vision` и шаблон mmproj-пака уже лежат
в конфиге и манифесте: глаза приедут без переписывания реестров.

**Жду команду «дальше».** 🦇🎙️
