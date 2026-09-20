"""Тесты исполнения ADR-020 (разбор «Нейроны» → четыре принятых приёма).

Проверяем то, что можно проверить без Unity:

1. **sample-accurate очередь визем** — опция объявлена в конфиге клиента,
   API очереди присутствует в ``AudioQueueProcessor``, ``offset_ms`` остался дефолтом;
2. **fallback-маппинг японских имён** — ``FaceRig`` знает あ/い/う/え/お и
   笑い/怒り/悲しみ, фолбэк идёт через ``ExpressionKey.CreateCustom``;
3. **nonverbal-слот** в ``voice.yaml`` — объявлен в ``PersonaVoice``, виден в API,
   реализация синтеза отсутствует (отложена до этапа голоса);
4. **спека этапа 7** — ``docs/STAGE7_HANDS_SPEC.md`` содержит US2 (sandbox + cleanup)
   и US3 (пермишены) в формате Given/When/Then.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from lilith_core.face import PersonaRegistry, PersonaVoice

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "unity-client" / "Assets" / "LilithFace" / "Scripts"


def read_cs(name: str) -> str:
    """Прочесть C#-файл клиента."""
    return (SCRIPTS_DIR / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
#  ADR-020 (1) — sample-accurate очередь визем
# --------------------------------------------------------------------------- #
class TestSampleAccurateVisemes:
    """Опция есть, дефолт — старый добрый offset_ms."""

    def test_config_flag_defaults_to_off(self) -> None:
        text = read_cs("LilithClientConfig.cs")
        assert "useSampleAccurateVisemes" in text
        assert re.search(r"public bool useSampleAccurateVisemes\s*=\s*false;", text), \
            "дефолт обязан быть false: offset_ms остаётся основным путём"

    def test_audio_processor_has_queue_api(self) -> None:
        text = read_cs("AudioQueueProcessor.cs")
        for member in (
            "struct QueuedViseme",
            "EnqueueVisemeAt",
            "EnqueueViseme",
            "DequeueDueVisemes",
            "PlayPositionSamples",
            "EnqueuedSamples",
        ):
            assert member in text, f"в AudioQueueProcessor нет {member}"

    def test_queue_is_sorted_and_dropped_on_stop(self) -> None:
        """Очередь сортируется по позиции и чистится при дропе реплики."""
        text = read_cs("AudioQueueProcessor.cs")
        assert "_visemeQueue.Sort(" in text
        assert "_visemeQueue.Clear();" in text

    def test_viseme_driver_consumes_queue(self) -> None:
        text = read_cs("VisemeDriver.cs")
        assert "TickFromQueue" in text
        assert "ApplyQueued" in text

    def test_client_switches_modes(self) -> None:
        text = read_cs("LilithFaceClient.cs")
        assert "config.useSampleAccurateVisemes" in text
        assert "Visemes.TickFromQueue(Audio.DequeueDueVisemes(), dt)" in text
        # серверные виземы в sample-accurate режиме идут в очередь, а не напрямую
        assert "Audio.EnqueueViseme(" in text

    def test_local_analysis_at_buffer_position(self) -> None:
        """Локальный анализ в момент приёма чанка знает абсолютную позицию."""
        text = read_cs("LilithFaceClient.cs")
        assert "QueueLocalVisemesForChunk" in text
        assert "EnqueuedSamples" in text


# --------------------------------------------------------------------------- #
#  ADR-020 (2) — японские имена блендшейпов
# --------------------------------------------------------------------------- #
class TestJapaneseBlendshapeFallback:
    """«え», «あ» и компания понимаются и на входе, и как фолбэк."""

    def test_viseme_aliases_accept_japanese(self) -> None:
        text = read_cs("FaceRig.cs")
        for name in ("あ", "い", "う", "え", "お"):
            assert f'{{ "{name}"' in text, f"в VisemeAliases нет {name}"

    def test_emotion_aliases_accept_japanese(self) -> None:
        text = read_cs("FaceRig.cs")
        for name in ("笑い", "怒り", "悲しみ", "驚き", "瞬き"):
            assert f'"{name}"' in text, f"в EmotionAliases нет {name}"

    def test_custom_fallback_tables_exist(self) -> None:
        text = read_cs("FaceRig.cs")
        assert "VisemeCustomFallback" in text
        assert "EmotionCustomFallback" in text
        assert "ExpressionKey.CreateCustom(" in text

    def test_fallback_is_not_blind(self) -> None:
        """Фолбэк сверяется со списком реально имеющихся экспрессий модели."""
        text = read_cs("FaceRig.cs")
        assert "CacheAvailableExpressions" in text
        assert "_expression.ExpressionKeys" in text
        assert "KeyFor(" in text

    def test_four_visemes_like_salsa(self) -> None:
        """У «Нейроны» SALSA count=4 (A/I/E+preview); наш набор — пять корзин сервера."""
        text = read_cs("FaceRig.cs")
        assert '"aa", "ih", "ou", "ee", "oh"' in text


# --------------------------------------------------------------------------- #
#  ADR-020 (3) — nonverbal-слот в voice.yaml
# --------------------------------------------------------------------------- #
class TestNonverbalSlot:
    """Слот объявлен, синтез отложен до этапа голоса."""

    def test_persona_voice_has_nonverbal(self) -> None:
        voice = PersonaVoice.from_dict({"pack": "lilith", "nonverbal": {"laugh": {"tag": "[laughs]"}}})
        assert voice.nonverbal == {"laugh": {"tag": "[laughs]"}}
        assert voice.as_dict()["nonverbal"] == {"laugh": {"tag": "[laughs]"}}

    def test_nonverbal_defaults_to_empty(self) -> None:
        assert PersonaVoice.from_dict({}).nonverbal == {}

    def test_nonverbal_rejects_non_dict(self) -> None:
        """Кривой yaml не роняет реестр."""
        assert PersonaVoice.from_dict({"nonverbal": "смех"}).nonverbal == {}

    def test_shipped_voice_yaml_declares_slot(self) -> None:
        data = yaml.safe_load((PROJECT_ROOT / "personas" / "lilith" / "voice.yaml").read_text(encoding="utf-8"))
        assert "nonverbal" in data
        assert data["nonverbal"] == {} or data["nonverbal"] is None
        text = (PROJECT_ROOT / "personas" / "lilith" / "voice.yaml").read_text(encoding="utf-8")
        assert "ADR-020.3" in text

    def test_slot_reaches_persona_frame(self, tmp_path: Path) -> None:
        """Кадр persona обязан нести nonverbal — иначе Unity о нём не узнает."""
        folder = tmp_path / "lilith"
        folder.mkdir(parents=True)
        (folder / "voice.yaml").write_text(
            yaml.safe_dump({"pack": "lilith", "nonverbal": {"sigh": {"tag": "[sighs]", "weight": 0.8}}},
                           allow_unicode=True),
            encoding="utf-8",
        )
        persona = PersonaRegistry(tmp_path).get("lilith")
        assert persona.voice_spec.as_dict()["nonverbal"]["sigh"]["tag"] == "[sighs]"

    def test_no_synthesis_implementation_yet(self) -> None:
        """Слот — не реализация: TTS нонвербалику пока не синтезирует."""
        tts = (PROJECT_ROOT / "src" / "lilith_core" / "voice" / "tts.py").read_text(encoding="utf-8")
        assert "nonverbal" not in tts, "синтез нонвербалики должен появиться не раньше этапа голоса"


# --------------------------------------------------------------------------- #
#  ADR-020 (4) — спека этапа 7: sandbox + пермишены
# --------------------------------------------------------------------------- #
class TestStage7Spec:
    """US2/US3 «Нейроны» перенесены в спеку этапа 7 в формате Given/When/Then."""

    @pytest.fixture(scope="class")
    def spec(self) -> str:
        return (PROJECT_ROOT / "docs" / "STAGE7_HANDS_SPEC.md").read_text(encoding="utf-8")

    def test_spec_exists(self, spec: str) -> None:
        assert "ЭТАП 7" in spec

    def test_given_when_then_format(self, spec: str) -> None:
        assert spec.count("**Given**") >= 8
        assert spec.count("**When**") >= 8
        assert spec.count("**Then**") >= 8

    def test_priorities_present(self, spec: str) -> None:
        for priority in ("P1", "P2", "P3"):
            assert f"({priority})" in spec

    def test_us2_sandbox_and_cleanup(self, spec: str) -> None:
        assert "US2" in spec
        assert "sandbox" in spec.lower()
        assert "confine" in spec.lower()
        assert "cleanup" in spec.lower()
        assert "path_escape" in spec

    def test_us3_permissions(self, spec: str) -> None:
        assert "US3" in spec
        assert "allowed_tools" in spec
        assert "timeout_sec" in spec
        assert "tool_not_allowed" in spec

    def test_empty_whitelist_means_deny_all(self, spec: str) -> None:
        """То же правило, что у персон (D6): пусто = запрещено всё."""
        assert "пустой список = запрещено всё" in spec

    def test_hitl_25_seconds(self, spec: str) -> None:
        assert "25" in spec and "авто-ОТКЛОН" in spec

    def test_quality_gates_and_constitution(self, spec: str) -> None:
        assert "Testing NON-NEGOTIABLE" in spec
        assert "verify_stage_artifact" in spec


class TestAdr020Recorded:
    """Решение заведено в журнал (наш DECISIONS.md = конституция)."""

    def test_adr_020_exists(self) -> None:
        text = (PROJECT_ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
        assert "## ADR-020." in text
        for needle in ("sample-accurate", "японских", "nonverbal", "STAGE7_HANDS_SPEC"):
            assert needle in text

    def test_neurona_notes_filled(self) -> None:
        text = (PROJECT_ROOT / "docs" / "NEURONA_NOTES.md").read_text(encoding="utf-8")
        assert "СТАТУС: ЗАПОЛНЕН" in text
        for section in ("3.1 Транспорт аудио", "3.2 Рот", "3.3 Лицо/риг", "3.4 Голос",
                        "3.5 Мозг/память", "3.6 Агентный слой", "3.7 Инструменты"):
            assert section in text, f"в NEURONA_NOTES.md нет раздела {section}"
        assert "6000.1.9f1" in text
        assert "SALSA" in text
