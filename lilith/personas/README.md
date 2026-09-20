# 👤 Персоны-агенты

Каждая папка = персона. Этап 6 перевёл реестр на три YAML-документа (решение D1-б:
новый формат основной, старый `profile.yaml` продолжает читаться как legacy-фолбэк).

```
personas/<id>/
├── card.yaml       # кто она: id, display_name, brain_profile, greeting, tags,
│                   #         lora{path,trigger_word,scale,autoload}, tools[], memory_scope
├── voice.yaml      # как говорит: pack/speaker (из voice.profiles), sample_rate 24000,
│                   #              speed, pitch_shift, fallback_pack, reference_wav
├── face.yaml       # как выглядит: vrm_path (тело ВНЕ репо, D5.4), fallback, slot/position,
│                   #                idle{blink_freq,breath_amp,look_speed}, оверрайды эмоций/визем
├── persona.md      # душа: системный промт (плейсхолдеры $user_name, $now, …)
├── model.vrm       # тело VRM 1.0 (опционально; можно не класть — хватит vrm_path)
└── fallback.jpg    # статичный аватар, пока тела нет
```

Папки с именем на `_` или `.` реестр **не считает** персонами: `_template` — это
заготовка, скопируй её в `personas/<новый_id>/`.

## Как это видит система

| Куда | Что |
|---|---|
| `GET /api/face/personas` | публичная сводка (D8): `id, display_name, vrm, has_vrm, voice{pack,speaker}, fallback, active, lora.state, tools, memory_scope, greeting, tags, face, source`. **`card` целиком не отдаётся.** |
| `GET /api/personas` | алиас этапа 5 (старый формат сводки) |
| `GET /api/face/personas/<id>/model.vrm` | отдаёт тело по пути из `face.yaml` (модель может лежать вне репозитория) |
| `POST /api/face/personas/<id>/activate` | глобальный своп персоны (D9): рассылает кадр `persona` всем продюсерам |
| `WS /ws/face/producer` | кадр `{"type":"persona","id":"<id>"}` — своп из Unity-клиента |
| `WS /ws` | `{"type":"persona","data":{"id":"<id>"}}` — своп из веб-панели |

Реестр перечитывается **горячо**: новая папка появляется без перезапуска сервера.

## Тело (VRM)

Настоящее тело Лилит Кирюша печатает в VRoid Studio (C5.1) и кладёт **вне** репозитория,
прописывая путь в `face.yaml: vrm_path`. Формат — **VRM 1.0** (C5.2).

Пока тела нет, для проверки пайплайна есть процедурный тест-куб (C5.3):

```bat
python scripts\make_test_vrm.py --out personas\lilith\model.vrm --name Lilith
```

Он даёт полный humanoid-скелет, 11 морф-таргетов и все нужные экспрессии
(`aa ih ou ee oh`, `happy angry sad relaxed surprised`, `blink`) — рот шевелится,
моргание работает, эмоции видны. ~21 КБ, поэтому в отличие от настоящих тел его
можно держать в `tests/samples/test_cube.vrm`.
