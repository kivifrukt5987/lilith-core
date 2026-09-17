"""Докатка доков этапа 4: CHANGELOG, README, PLAN, ARCHITECTURE, DECISIONS."""

from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROJECT = ROOT / "Локальная Лилит"
LILITH = ROOT / "lilith"

# --- CHANGELOG
p = PROJECT / "CHANGELOG.md"
s = p.read_text(encoding="utf-8")
if "## Этап 4 — «Уши и горло»" not in s:
    old = "# 📜 CHANGELOG — LILITH-CORE\n\nФормат: что добавлено / как проверить / что дальше.\n\n---\n"
    s = s.replace(old, old + """
## Этап 4 — «Уши и горло» (v0.4.0, 17.09.2026)

Команда Кирюши: «дальше» + принятые советы другой Лильки (ADR-012).

### Добавлено

* `voice/stt.py` — реестр STT: faster-whisper (дефолт, lazy), резервные слоты
  whisper-cpp/vosk, MockSTT; фолбэк-цепочка запрошенный→дефолт→доступный.
* VAD: silero-vad (lazy torch) или EnergyVAD на чистом Python (RMS-кадры);
  раздельные шкалы порогов `vad_threshold` / `vad_energy_threshold`.
* `voice/tts.py` — реестр TTS: silero v4_ru, edge-tts, zero-shot слот
  (IndexTTS/XTTS/CosyVoice/F5, контракт зафиксирован), MockTTS; voice-профили
  (backend/voice/reference/speed/note); стриминг wav по предложениям.
* `voice/hotkey.py` — push-to-talk ctrl+space (pynput lazy) + MockPushToTalk.
* `voice/packs.py` + `models/packs.yaml` — манифест паков (hf/hf-mirror/url/local),
  установка с докачкой (Range) и sha256, удаление; CLI `lilith-core packs …`.
* `VoiceCore` с hot-swap: конфиг голоса перечитывается на каждый вызов,
  смена бэкенда/профиля без перезапуска, фолбэк при ошибке.
* HTTP: /api/voice/profiles, /api/voice/transcribe, /api/voice/say,
  /api/packs (+install/remove); CLI-флаг `--with-voice`.
* `voices/README.md` — как готовить reference.wav (и про согласие владельца).

### Проверка

* pytest: 422 passed (+58 тестов голоса и паков, включая докачку с Range).
* Живой прогон `--mock-brain --with-voice`: transcribe/say/packs отвечают,
  профили и доступность бэкендов видны в /api/voice/profiles.

### Дальше

Этап 5 «Лицо»: парсер `[emotion: x]`, мост VTuber Studio (VMC) с реконнектом;
эмоции станут общим источником для лица и голоса.

---
""", 1)
    p.write_text(s, encoding="utf-8")
    print("CHANGELOG ok")

# --- README
p = PROJECT / "README.md"
s = p.read_text(encoding="utf-8")
s = s.replace("| Версия | `0.3.0` (этап 3: память — журнал, RAG, настраиваемое авто-саммари) |",
              "| Версия | `0.4.0` (этап 4: уши, горло, голосовые профили, паки моделей) |")
s = s.replace("| Этап | 3 из 8 (`memory`) — память живая, ждём «дальше» для этапа 4 |",
              "| Этап | 4 из 8 (`voice`) — голосовой контур готов, ждём «дальше» для этапа 5 |")
s = s.replace("| **4** | Уши и горло | `voice/stt.py` (интерфейс faster-whisper + silero-vad, хоткей push-to-talk через pynput), `voice/tts.py` (интерфейс silero-tts, фолбэк edge-tts); в песочнице интерфейсы+mock, в README инструкции локальной установки | ⏳ |",
              "| **4** | Уши и горло | `voice/stt.py` (интерфейс faster-whisper + silero-vad, хоткей push-to-talk через pynput), `voice/tts.py` (интерфейс silero-tts, фолбэк edge-tts); в песочнице интерфейсы+mock, в README инструкции локальной установки | ✅ **готово** |")
old = "## 📜 Память и настраиваемое саммари (этап 3 готов)"
new = """## 🎙 Голосовой контур и паки моделей (этап 4 готов)

Включается флагом `features.voice_enabled: true` или `--with-voice`.
Зависимости: `pip install -e ".[voice]"` (torch/faster-whisper/pynput/edge-tts).

* **Уши**: хоткей `ctrl+space` → VAD (silero или energy без зависимостей) →
  faster-whisper (small/medium/large-v3, GPU/CPU) → текст в общую шину.
  Резервные слоты: whisper-cpp, vosk (объявлены, подключаются по необходимости).
* **Горло**: voice-профили в конфиге (`voice.profiles`): silero v4_ru по умолчанию,
  edge-tts как облачный фолбэк, **zero-shot слот** под клон-движок
  (IndexTTS/XTTS/CosyVoice/F5): положите `voices/<имя>/reference.wav` (3–10 с)
  и укажите `reference` в профиле — код не правится.
* **Hot-swap**: смена stt-бэкенда и голосового профиля действует на следующий вызов,
  без перезапуска; при ошибке — фолбэк на дефолт.
* **Паки моделей**: `models/packs.yaml` (источник hf/hf-mirror/url/local, size, sha256,
  куда класть) + CLI `lilith-core packs list|install|remove` с докачкой и проверкой хеша.
  Свой пак = новая строка в манифесте. Секция «Паки» в панели приедет на этапе 6.
* HTTP: `/api/voice/profiles`, `/api/voice/transcribe` (WAV→текст),
  `/api/voice/say` (текст→wav), `/api/packs` (+ install/remove).

## 📜 Память и настраиваемое саммари (этап 3 готов)"""
if old in s:
    s = s.replace(old, new, 1)
    p.write_text(s, encoding="utf-8")
    print("README ok")

# --- PLAN
p = LILITH / "PLAN.md"
s = p.read_text(encoding="utf-8")
old = "| 4 | Уши и горло | `voice/stt.py` (интерфейс faster-whisper + silero-vad, хоткей push-to-talk через pynput), `voice/tts.py` (интерфейс silero-tts, фолбэк edge-tts); в песочнице интерфейсы+mock, в README инструкции локальной установки | ⏳ | — |"
new = "| 4 | Уши и горло | `voice/stt.py` (интерфейс faster-whisper + silero-vad, хоткей push-to-talk через pynput), `voice/tts.py` (интерфейс silero-tts, фолбэк edge-tts); в песочнице интерфейсы+mock, в README инструкции локальной установки | ✅ **ГОТОВО**<br>422 теста зелёные | `artifacts/LILITH-CORE_stage4_v0.4.0.zip` |"
if old in s:
    s = s.replace(old, new)
    old2 = "| — | Отправлен отчёт `STAGE1_REPORT.md` + архив. **Жду команду «дальше»** |"
    new2 = ("| 17.09.2026 ~21:10 | **ЭТАП 4 ЗАВЕРШЁН** по команде «дальше» + советы другой Лильки (ADR-012): "
            "реестры STT/TTS с фолбэками, voice-профили и zero-shot слот с reference.wav, "
            "energy/silero VAD, push-to-talk, манифест паков с докачкой и sha + CLI, hot-swap "
            "без перезапуска. 422 теста. Отчёт `STAGE4_REPORT.md`. **Жду «дальше» для этапа 5** |\n"
            "| — | Отправлен отчёт `STAGE1_REPORT.md` + архив. **Жду команду «дальше»** |")
    s = s.replace(old2, new2, 1)
    p.write_text(s, encoding="utf-8")
    print("PLAN ok")

# --- ARCHITECTURE
p = PROJECT / "docs" / "ARCHITECTURE.md"
s = p.read_text(encoding="utf-8")
if "## 9.6" not in s:
    s += """

## 9.6. Слой голоса (этап 4, готов)

* Контракты: STT ``transcribe(audio) -> Transcript``, TTS ``synthesize/stream``,
  VAD ``trim(audio) -> audio``; бэкенды lazy, резервные слоты объявлены заранее.
* Фолбэк-цепочки везде: запрошенный → дефолт → первый доступный; ошибка не роняет шину.
* Hot-swap: ``VoiceCore.sync()`` перечитывает голосовой конфиг на каждый вызов (ADR-012).
* Паки: манифест `models/packs.yaml` + установщик с Range-докачкой и sha256;
  пользовательский пак = запись в манифесте, код не трогается.
* Эмоции (этап 5) будут потреблять те же теги `[emotion: x]`, что и просодия голоса:
  один парсер — два потребителя (лицо и горло).
"""
    p.write_text(s, encoding="utf-8")
    print("ARCHITECTURE ok")

# --- DECISIONS
p = PROJECT / "docs" / "DECISIONS.md"
s = p.read_text(encoding="utf-8")
if "ADR-012" not in s:
    s += """
## ADR-012. Советы другой Лильки приняты с поправками (v0.4.0)

**Контекст.** Компаньон-инстанс из соседнего чата прислал чертёж расширяемости
голосового слоя: STT-реестр, манифест паков с CLI и панелью, zero-shot голоса
папками, hot-swap, пользовательские паки.

**Решение.** Принято почти целиком:
1. STT-реестр с контрактом `transcribe` и резервными слотами whisper-cpp/vosk — да;
2. манифест `models/packs.yaml` + CLI `lilith-core packs …` + докачка + sha256 — да;
   секция «Паки» в панели отложена на этап 6 (её же совет);
3. zero-shot голоса: `voices/<имя>/reference.wav` + запись в конфиге — да; сам
   клон-движок не прибит: слот `zero-shot` с контрактом, движок выбирается позже;
4. hot-swap voice/stt/vision без перезапуска + фолбэк на дефолт — да (`VoiceCore.sync`);
5. пользовательские паки (local/url) без правки кода — да.

**Поправки моей кухни:** energy-vad получил отдельную шкалу порога (RMS громкости),
не смешанную с вероятностной шкалой silero; zero-shot слот не тащит тяжёлый движок
в зависимости по умолчанию; sha256=null разрешён осознанно (предупреждение, не ошибка).

**Последствия.** Голосовой слой расширяется записями в конфиге/манифесте, а не кодом;
панель паков и выбор клон-движка — следующие шаги без переписывания контура.
"""
    p.write_text(s, encoding="utf-8")
    print("DECISIONS ok")
