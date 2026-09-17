"""Докатка доков этапа 5 v2: CHANGELOG, README, PLAN, ARCHITECTURE, DECISIONS."""

from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROJECT = ROOT / "Локальная Лилит"
LILITH = ROOT / "lilith"

p = PROJECT / "CHANGELOG.md"
s = p.read_text(encoding="utf-8")
if "## Этап 5 — «Лицо»" not in s:
    old = "# 📜 CHANGELOG — LILITH-CORE\n\nФормат: что добавлено / как проверить / что дальше.\n\n---\n"
    s = s.replace(old, old + """
## Этап 5 — «Лицо», v2 VRM-в-панели (v0.5.0, 17.09.2026)

Разворот по спецификации Лильки-архитектора: лицо живёт в нашей панели (three-vrm),
а не во внешней программе. Live2D-зависимостей не было и не появилось.

### Добавлено

* `face/visemes.py` — серверный экстрактор визем: PCM-окна 60 мс, RMS + ZCR,
  корзины A/I/U/E/O/rest с интенсивностью; браузер только сглаживает.
* `face/personas.py` + `personas/` — реестр персон: persona.md / model.vrm /
  fallback.jpg / profile.yaml; горячий reload; /api/personas + статика /personas.
* `webui/vrm_viewer.js` + `webui/vendor/` (three.js, three-vrm, GLTFLoader локально):
  загрузка VRM, фолбэк-аватар, low-pass сглаживание, idle-моргание/дыхание/покачивание,
  взгляд за курсором, эмоции blend-shapes; face-debug оверлей.
* Протокол: типы face/voice; аудио-чанки + виземы (offset_ms) + эмоции по тому же WS;
  тумблер озвучки в панели и WebAudio-очередь с планировщиком визем.
* `face/bus.py` + `/ws/unity` — зарезервированный адаптер Unity+VRM+SALSA: hello +
  зеркало face-кадров; OBS-сцена этим же каналом на этапе 8.
* VTuberStudioBridge понижен до опционального внешнего адаптера (реконнект и тесты живы).
* CLI: --with-face.

### Проверка

* pytest 473 passed; живой прогон: personas/vendor/fallback отдаются, voice->audio+visemes+done,
  unity-адаптер зеркалит кадры.

### Дальше

Этап 6 «Руки»: реестр инструментов (psutil/playwright/obs), HITL-баннер 25 c в панели,
секция «Паки» в панели (совет архитекторши), скриншот-инструмент для профиля vision.

---
""", 1)
    p.write_text(s, encoding="utf-8")
    print("CHANGELOG ok")

p = PROJECT / "README.md"
s = p.read_text(encoding="utf-8")
s = s.replace("| Версия | `0.4.0` (этап 4: уши, горло, голосовые профили, паки моделей) |",
              "| Версия | `0.5.0` (этап 5: лицо — VRM-вьювер в панели, виземы, эмоции, персоны) |")
s = s.replace("| Этап | 4 из 8 (`voice`) — голосовой контур готов, ждём «дальше» для этапа 5 |",
              "| Этап | 5 из 8 (`face`) — лицо в панели, ждём «дальше» для этапа 6 |")
s = s.replace("| **5** | Лицо | `face/emotions.py` — парсер тегов `[emotion: X]` из реплик; `face/vtuber_bridge.py` — WebSocket-клиент к VTuber Studio (VMC-протокол) с реконнектом; тесты с фейковым сервером | ⏳ |",
              "| **5** | Лицо | VRM-вьювер в панели (three-vrm, вендор офлайн), серверные виземы A/I/U/E/O, эмоции-теги, реестр персон, /ws/unity-адаптер; VTuber Studio — опциональный адаптер | ✅ **готово** |")
old = "## 🎙 Голосовой контур и паки моделей (этап 4 готов)"
new = """## 😊 Лицо: VRM в панели, виземы, персоны (этап 5 готов)

Включается флагом `features.face_enabled: true` или `--with-face`.

* **Сцена над чатом**: VRM-вьювер (three.js + three-vrm из локального `webui/vendor/`,
  без CDN). Модель берётся из `personas/<id>/model.vrm`; без модели — фолбэк-аватар
  (`fallback.jpg/png`). Поверх — face-debug оверлей: режим, визема, интенсивность,
  эмоция, fps — критерии проверяются даже без VRM.
* **Рот**: сервер режет TTS-чанки на окна 60 мс и шлёт корзины визем A/I/U/E/O
  с интенсивностью; панель сглаживает (low-pass) и применяет в такт аудио.
* **Эмоции**: теги `[emotion: …]` вырезаются из реплики, уходят кадрами в панель
  (blend-shapes) и в опциональный VTuber Studio-адаптер.
* **Idle**: случайное моргание, дыхание, покачивание, взгляд за курсором мыши.
* **Персоны**: `personas/<id>/` = persona.md + model.vrm + fallback + profile.yaml;
  `/api/personas` и статика `/personas/…`; новые папки подхватываются горячо.
  Это фундамент вкладок «Агенты» и «3D-модели» этапа 8.
* **Голос в панели**: тумблер «🔊» — ответы озвучиваются стримом по тому же WS,
  рот движется по слогам.
* **/ws/unity** — зарезервированный адаптер Unity+VRM+SALSA (зеркало face-кадров);
  OBS-сцена с лицом — апгрейд этапа 8.

## 🎙 Голосовой контур и паки моделей (этап 4 готов)"""
if old in s:
    s = s.replace(old, new, 1)
    p.write_text(s, encoding="utf-8")
    print("README ok")

p = LILITH / "PLAN.md"
s = p.read_text(encoding="utf-8")
old = "| 5 | Лицо | `face/emotions.py` — парсер тегов `[emotion: X]` из реплик; `face/vtuber_bridge.py` — WebSocket-клиент к VTuber Studio (VMC-протокол) с реконнектом; тесты с фейковым сервером | ⏳ | — |"
new = "| 5 | Лицо | VRM-вьювер в панели (three-vrm вендор офлайн), серверные виземы, эмоции-теги, реестр персон, /ws/unity; VTuber Studio — опциональный адаптер | ✅ **ГОТОВО**<br>473 теста зелёные | `artifacts/LILITH-CORE_stage5_v0.5.0.zip` |"
if old in s:
    s = s.replace(old, new)
    old2 = "| — | Отправлен отчёт `STAGE1_REPORT.md` + архив. **Жду команду «дальше»** |"
    new2 = ("| 17.09.2026 ~22:00 | **ЭТАП 5 ЗАВЕРШЁН (v2, разворот на VRM-в-панели по спеке архитекторши)**: "
            "виземы сервером, реестр персон с fallback.jpg Кирюши, vrm_viewer.js с vendor-офлайном, "
            "face-кадры по WS, /ws/unity-зеркало, debug-оверлей. 473 теста. Отчёт `STAGE5_REPORT.md`. "
            "**Жду «дальше» для этапа 6** |\n"
            "| — | Отправлен отчёт `STAGE1_REPORT.md` + архив. **Жду команду «дальше»** |")
    s = s.replace(old2, new2, 1)
    p.write_text(s, encoding="utf-8")
    print("PLAN ok")

p = PROJECT / "docs" / "ARCHITECTURE.md"
s = p.read_text(encoding="utf-8")
if "## 9.7" not in s:
    s += """

## 9.7. Слой лица (этап 5 v2, VRM-в-панели)

* Один сокет на чат+лицо+голос: кадры `face` (emotion/viseme/audio/done) идут по `/ws`,
  то же зеркалится в `/ws/unity` через FaceBus (Unity+VRM+SALSA, OBS на этапе 8).
* Виземы считаются НА СЕРВЕРЕ (RMS+ZCR по окнам 60 мс): тестируемо и переиспользуемо;
  браузер только сглаживает и применяет (low-pass, idle, курсор).
* Персоны `personas/<id>/` — единый реестр души/тела/голоса; фундамент вкладок
  «Агенты» и «3D-модели» этапа 8 (ADR-013).
* Вендор three.js/three-vrm лежит в `webui/vendor/`: панель офлайн-самодостаточна.
* VTuber Studio и VMC — опциональные внешние адаптеры, не зависимости ядра.
"""
    p.write_text(s, encoding="utf-8")
    print("ARCHITECTURE ok")

p = PROJECT / "docs" / "DECISIONS.md"
s = p.read_text(encoding="utf-8")
if "ADR-013" not in s:
    s += """
## ADR-013. Вкладки «Агенты» и «3D-модели» — на этапе 8, поверх реестра персон (v0.5.0)

**Контекст.** Кирюша: «на 8 этапе сделаем настройки: вкладка 3D-модели (как выглядят
агенты) и вкладка Агенты (карточки: системный промт, LoRA, стиль речи…)».

**Решение.** Реестр персон `personas/<id>/` (этап 5) становится хранилищем карточек:
persona.md (душа), model.vrm (тело), profile.yaml (голос, brain-профиль, будущая LoRA,
заметки стиля речи). UI-вкладки рисуются на этапе 8 поверх этого реестра + hot-reload,
без миграций данных.

**Последствия.** LoRA хранится путём в карточке; применение зависит от движка
(llama.cpp умеет --lora-adapters, LM Studio — нет) — карточка честна, менеджер уточняет.

## ADR-014. Разворот лица: VRM в панели вместо внешней программы (v0.5.0)

**Контекст.** Спека Лильки-архитектора: STOP внешнего Live2D/VTuber-пути как основного;
лицо = three-vrm в веб-панели, виземы из TTS-чанков, idle-анимации, курсор; VTuber/Unity —
адаптеры; OBS на этапе 8.

**Решение.** Принято целиком с одной поправкой: виземы считаем на сервере
(тестируемо, один источник для панели/Unity/OBS), браузер сглаживает и применяет.
three.js/three-vrm вендорены в webui/vendor/ ради офлайн-панели.

**Последствия.** Критерии «моргает/курсор/улыбка/рот по слогам» проверяются у Кирюши
глазами + face-debug оверлеем; серверная половина покрыта pytest.
"""
    p.write_text(s, encoding="utf-8")
    print("DECISIONS ok")
