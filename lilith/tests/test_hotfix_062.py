"""Тесты хотфикса 0.6.2: приёмка на Windows/py3.13 + ответы Q1–Q5 (ADR-021).

Что здесь проверяется:

* **красный №1** — ``httpx.ConnectTimeout`` теперь трактуется как «не достучался»
  (:class:`BrainConnectionError`), а не «сервер долго думает»; тест детерминирован
  на любой ОС (MockTransport вместо реального ``127.0.0.1:1``);
* **красные №2, №3** — тесты ``TestDocsHygiene`` скипаются, а не падают, когда
  файлов уровня workspace нет или они старее 0.6.1 (гибридная папка Кирюши);
* **Q3** — асимметричное сглаживание визем On 0.08 / Off 0.06 + Cubic Out объявлено
  в конфиге клиента и является дефолтом;
* **Q4** — VMC/VTuber Studio помечены legacy и выключены в поставке;
* **Q5** — окно 512×640: ``WindowSpec`` в ``face.yaml``, доезжает в кадре ``persona``.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from lilith_core.brain import BrainConnectionError, BrainTimeoutError, LLMClient
from lilith_core.face import PersonaFace, PersonaRegistry, WindowSpec

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "unity-client" / "Assets" / "LilithFace" / "Scripts"


def read_cs(name: str) -> str:
    """Прочесть C#-файл клиента."""
    return (SCRIPTS_DIR / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
#  Красный №1: ConnectTimeout = «не достучался» (Windows/py3.13)
# --------------------------------------------------------------------------- #
class TestConnectTimeoutMapping:
    """Регрессия приёмки: на Windows закрытый порт таймаутится, а не отказывает."""

    @pytest.mark.asyncio
    async def test_connect_timeout_is_connection_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out")

        client = LLMClient(transport=httpx.MockTransport(handler))
        with pytest.raises(BrainConnectionError):
            await client.complete(make_profile_stub(), _messages())

    @pytest.mark.asyncio
    async def test_read_timeout_is_still_timeout_error(self) -> None:
        """Таймаут ОТВЕТА остался BrainTimeoutError — маппинг не сгребает всё подряд."""
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("слишком долго")

        client = LLMClient(transport=httpx.MockTransport(handler))
        with pytest.raises(BrainTimeoutError):
            await client.complete(make_profile_stub(), _messages())

    @pytest.mark.asyncio
    async def test_connect_error_message_mentions_profile_and_url(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out")

        profile = make_profile_stub(base_url="http://127.0.0.1:1/v1")
        client = LLMClient(transport=httpx.MockTransport(handler))
        with pytest.raises(BrainConnectionError) as exc:
            await client.complete(profile, _messages())
        assert "профиль 'chat'" in str(exc.value)
        assert "127.0.0.1:1" in str(exc.value)

    def test_llm_catches_connect_timeout_before_generic_timeout(self) -> None:
        """Порядок except'ов важен: ConnectTimeout обязан идти до TimeoutException."""
        text = (PROJECT_ROOT / "src" / "lilith_core" / "brain" / "llm.py").read_text(encoding="utf-8")
        connect_at = text.index("except httpx.ConnectTimeout")
        generic_at = text.index("except httpx.TimeoutException", connect_at)
        assert connect_at < generic_at

    def test_no_test_hits_real_loopback_port(self) -> None:
        """Тесты не должны ходить в настоящий 127.0.0.1:1 — на Windows это таймаут."""
        text = (PROJECT_ROOT / "tests" / "test_brain_llm.py").read_text(encoding="utf-8")
        refused = text.split("async def test_connection_refused", 1)[1]
        refused = refused.split("async def ", 1)[0]
        assert "MockTransport" in refused, "test_connection_refused обязан быть детерминированным"
        assert "LLMClient(timeout=1.0)" not in refused


def make_profile_stub(base_url: str = "http://127.0.0.1:1/v1"):
    """Профиль мозга для тестов (копия фабрички из test_brain_llm, чтобы не
    импортировать из каталога tests — он не является пакетом)."""
    from lilith_core.config import ResolvedProfile  # noqa: PLC0415

    return ResolvedProfile(
        name="chat",
        provider="openai_compatible",
        base_url=base_url,
        model="test-model",
        temperature=0.5,
        max_tokens=100,
        top_p=0.9,
        request_timeout_sec=5.0,
        max_retries=0,
        stream=True,
        history_max_messages=10,
        api_key_env="LILITH_NOPE_KEY",
        note="тест 0.6.2",
    )


def _messages() -> list[dict[str, str]]:
    """Минимальная история сообщений."""
    return [{"role": "system", "content": "ты — Лилит"}, {"role": "user", "content": "привет"}]


# --------------------------------------------------------------------------- #
#  Красные №2, №3: гварды для гибридной папки
# --------------------------------------------------------------------------- #
class TestHybridFolderGuards:
    """Тесты структуры не падают, когда workspace-файлов нет или они старые."""

    def test_docs_hygiene_guards_exist(self) -> None:
        text = (PROJECT_ROOT / "tests" / "test_structure.py").read_text(encoding="utf-8")
        block = text.split("class TestDocsHygiene", 1)[1]
        assert block.count("pytest.skip(") >= 2, "нужны гварды и для скрипта, и для PLAN.md"
        assert "старше 0.6.1" in block, "гвард обязан отличать старую копию скрипта"

    def test_no_skipif_class_attribute_left(self) -> None:
        """Классовый skipif не срабатывал в гибридной папке — его быть не должно."""
        text = (PROJECT_ROOT / "tests" / "test_structure.py").read_text(encoding="utf-8")
        block = text.split("class TestDocsHygiene", 1)[1].split("class ", 1)[0]
        assert "pytest.mark.skipif" not in block

    def test_raw_string_for_windows_path(self) -> None:
        """SyntaxWarning '\\S' закрыт raw-строкой (косметика приёмки)."""
        text = (PROJECT_ROOT / "tests" / "test_structure.py").read_text(encoding="utf-8")
        needle = chr(114) + chr(39) + 'set "PYTHON=.venv' + chr(92) + 'Scripts' + chr(92) + 'python.exe"' + chr(39)
        assert needle in text

    def test_sync_bridges_are_separate_class(self) -> None:
        """Синхронные тесты мостов вынесены из auto-asyncio класса."""
        text = (PROJECT_ROOT / "tests" / "test_face_bridge.py").read_text(encoding="utf-8")
        assert "class TestSyncBridges" in text
        other = text.split("class TestOtherBridges", 1)[1].split("class TestSyncBridges", 1)[0]
        assert "def test_state_shape" not in other

    def test_starlette_warning_filtered(self) -> None:
        text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert "starlette.testclient" in text


# --------------------------------------------------------------------------- #
#  Q3 — асимметричное сглаживание визем
# --------------------------------------------------------------------------- #
class TestVisemeSmoothingDefaults:
    """On 0.08 / Off 0.06 / Cubic Out — дефолт (решение Q3)."""

    def test_config_defaults(self) -> None:
        text = read_cs("LilithClientConfig.cs")
        assert re.search(r"public float visemeOnSeconds\s*=\s*0\.08f;", text)
        assert re.search(r"public float visemeOffSeconds\s*=\s*0\.06f;", text)
        assert re.search(r"public bool visemeCubicOut\s*=\s*true;", text)

    def test_driver_uses_asymmetric_approach(self) -> None:
        text = read_cs("VisemeDriver.cs")
        for member in ("OpenSeconds", "CloseSeconds", "CubicOut", "Approach(", "EvaluateCubicOut"):
            assert member in text, f"в VisemeDriver нет {member}"

    def test_legacy_smoothing_still_available(self) -> None:
        """Прежний low-pass остался фолбэком (On/Off = 0)."""
        text = read_cs("VisemeDriver.cs")
        assert "Smoothing" in text
        assert "if (OpenSeconds > 0f || CloseSeconds > 0f)" in text

    def test_client_wires_new_params(self) -> None:
        text = read_cs("LilithFaceClient.cs")
        assert text.count("OpenSeconds = config.visemeOnSeconds") == 2  # Awake + реконфиг из hello
        assert text.count("CubicOut = config.visemeCubicOut") == 2


# --------------------------------------------------------------------------- #
#  Q4 — VMC/VTuber Studio помечены legacy
# --------------------------------------------------------------------------- #
class TestVmcMarkedLegacy:
    """Опциональный внешний адаптер, в этапе 7 не развивается."""

    def test_module_docstring_says_legacy(self) -> None:
        text = (PROJECT_ROOT / "src" / "lilith_core" / "face" / "vtuber_bridge.py").read_text(encoding="utf-8")
        assert "LEGACY" in text.split('"""')[1]

    def test_architecture_documents_legacy_adapters(self) -> None:
        text = (PROJECT_ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
        assert "Legacy-адаптеры лица" in text

    def test_disabled_in_shipped_config(self) -> None:
        import yaml

        data = yaml.safe_load((PROJECT_ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
        assert data["face"]["vtuber_studio_enabled"] is False
        assert data["face"]["vmc_enabled"] is False


# --------------------------------------------------------------------------- #
#  Q5 — окно 512×640 из face.yaml
# --------------------------------------------------------------------------- #
class TestPersonaWindowSpec:
    """Портрет 4:5 по умолчанию, переопределяется персоной."""

    def test_defaults_are_portrait(self) -> None:
        spec = WindowSpec()
        assert (spec.width, spec.height) == (512, 640)

    def test_from_dict_and_garbage(self) -> None:
        assert WindowSpec.from_dict({"width": 720, "height": 1280}).as_dict() == {"width": 720, "height": 1280}
        assert WindowSpec.from_dict(None).as_dict() == {"width": 512, "height": 640}
        assert WindowSpec.from_dict({"width": "ширина"}).as_dict() == {"width": 512, "height": 640}
        # слишком маленькое окно не даём: OBS не сможет захватить
        assert WindowSpec.from_dict({"width": 4, "height": 8}).as_dict() == {"width": 64, "height": 64}

    def test_face_spec_carries_window(self) -> None:
        face = PersonaFace.from_dict({"window": {"width": 600, "height": 800}})
        assert face.as_dict()["window"] == {"width": 600, "height": 800}

    def test_shipped_persona_declares_window(self) -> None:
        import yaml

        data = yaml.safe_load((PROJECT_ROOT / "personas" / "lilith" / "face.yaml").read_text(encoding="utf-8"))
        assert data["window"] == {"width": 512, "height": 640}

    def test_registry_reads_window(self, tmp_path: Path) -> None:
        import yaml as _yaml

        folder = tmp_path / "lilith"
        folder.mkdir(parents=True)
        (folder / "face.yaml").write_text(_yaml.safe_dump({"window": {"width": 480, "height": 800}}), encoding="utf-8")
        persona = PersonaRegistry(tmp_path).get("lilith")
        assert persona.face_spec.window.width == 480
        assert persona.describe()["face"]["window"] == {"width": 480, "height": 800}

    def test_client_applies_persona_window(self) -> None:
        text = read_cs("LilithFaceClient.cs")
        assert "ApplyPersonaWindow" in text
        assert 'face.TryGetValue("window"' in text
        assert "ApplyWindowSize" in text

    def test_transparent_window_resizes(self) -> None:
        text = read_cs("TransparentWindow.cs")
        assert "public void ApplyWindowSize()" in text
        assert "SetWindowPos(" in text

    def test_config_default_is_portrait(self) -> None:
        text = read_cs("LilithClientConfig.cs")
        assert "new Vector2Int(512, 640)" in text
        assert "usePersonaWindowSize = true" in text

    def test_persona_frame_carries_window(self, settings, tmp_path: Path) -> None:
        """Кадр persona обязан нести window — иначе Unity не узнает размер."""
        from lilith_core.face import FaceProducerHub, PersonaRegistry as Registry

        folder = tmp_path / "lilith"
        folder.mkdir(parents=True)
        (folder / "face.yaml").write_text("window:\n  width: 512\n  height: 640\n", encoding="utf-8")
        registry = Registry(tmp_path, active="lilith")
        hub = FaceProducerHub(registry=registry, server_version="test")
        frame = registry.get("lilith").describe()["face"]
        assert frame["window"] == {"width": 512, "height": 640}
        assert hub.registry.active == "lilith"
