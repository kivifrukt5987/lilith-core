# Шаблон персоны

Скопируй эту папку в `personas/<твой_id>/` и заполни:

| Файл | Что внутри |
|---|---|
| `card.yaml` | id, display_name, brain_profile, greeting, tags, `lora` (слот), `tools` (белый список), `memory_scope` |
| `voice.yaml` | pack/speaker из `voice.profiles`, sample_rate (24000), speed, pitch_shift, fallback_pack, reference_wav |
| `face.yaml` | `vrm_path` (D5.4 — тело вне репо), fallback, slot/position для группы, `idle{blink_freq,breath_amp,look_speed}`, оверрайды эмоций/визем |
| `persona.md` | душа: системный промт |
| `fallback.jpg` | статичный аватар, пока нет VRM |

Тестовое тело вместо VRoid Studio: `python scripts/make_test_vrm.py --out personas/<id>/model.vrm --name <Id>`.
Реестр перечитывается горячо — перезапуск сервера не нужен.
