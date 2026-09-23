"""Тесты хотфикса 0.6.4: «тело не доезжает до сцены, VrmLoader молчит».

Блокер приёмки **F7** (Windows, Unity 6000.0.84f1, UniVRM 0.131.2): сервер жив,
продюсер ``ready``, оверлей показывает персону и окно 512×640, Console чистая
(0 ошибок, 0 warnings), а в Hierarchy у ``LilithFace`` нет детей — тела нет.

Диагноз, воспроизведённый в песочнице живым сервером (``scratch/repro_064.py``):

1. на подключение сервер шлёт только ``hello`` (в нём ``persona`` — её и показывает
   оверлей), а на ``ready`` — только ``state``;
2. кадр ``persona`` приходит **исключительно** в ответ на ``persona_request``
   клиента или на ``POST /api/face/personas/<id>/activate`` (контракт A5/ADR-015);
3. ``LilithFaceClient.OnPersona`` — единственная точка, где зовётся
   ``VrmLoader.Swap``; ``RequestPersona()`` написан, но никто его не звал;
4. итог: свопа нет вовсе → ни ``NotifyFailed``, ни исключений, ни тела,
   а единственный лог про персону был спрятан за ``config.verbose = false``.

Здесь закреплено:

**A. Python** — формула URL тела (probe ↔ C#), контракт триггера, эндпоинт
модели, access-лог ``/api/face/*`` и «проба не всегда зелёная».

**B. C#-гварды** — ``VrmLoader`` строит URL сам, своп идемпотентен, клиент
просит кадр и держит watchdog, ``swap:false`` уважается, логи безусловные,
новые поля конфига и строка про тело в оверлее.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml
from fastapi.testclient import TestClient
from loguru import logger

from lilith_core.app import create_app
from lilith_core.config import LoggingSettings, Settings
from lilith_core.face import PersonaRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "unity-client" / "Assets" / "LilithFace" / "Scripts"
VRM_LOADER = SCRIPTS_DIR / "VrmLoader.cs"
FACE_CLIENT = SCRIPTS_DIR / "LilithFaceClient.cs"
CLIENT_CONFIG = SCRIPTS_DIR / "LilithClientConfig.cs"
TEST_VRM = PROJECT_ROOT / "tests" / "samples" / "test_cube.vrm"


def read_cs(path: Path) -> str:
    """Прочесть C#-файл клиента."""
    return path.read_text(encoding="utf-8")


def _load_script(name: str):
    """Загрузить модуль из ``scripts/`` (там нет ``__init__.py``)."""
    path = PROJECT_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"lilith_064_{name}", path)
    assert spec is not None and spec.loader is not None, f"не загрузить {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load_script("unity_face_probe")


def code_calls(text: str, needle: str) -> list[str]:
    """Строки кода, которые **начинаются** с ``needle`` (комментарий вызовом не считается).

    Контрольный выстрел 0.6.4 показал: гвард вида ``assert "Foo()" in text`` засыпает,
    если вызов закомментировать — а именно так и выглядит «починили на словах».
    """
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith(needle) and not line.strip().startswith(("//", "*"))
    ]


def strip_preprocessor_blocks(text: str, symbol: str = "LILITH_UNIVRM") -> str:
    """Вырезать блоки ``#if <symbol> … #endif`` (с вложенностью) — как в 0.6.3.

    Нужно, чтобы гвард «лог живёт вне условной компиляции» не обманывался:
    строка внутри ``#if LILITH_UNIVRM`` до Кирюши доедет, а вот строка внутри
    ``#if UNITY_STANDALONE_WIN`` — только на Windows.
    """
    out: list[str] = []
    depth = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#if"):
            if symbol in stripped:
                depth += 1
            elif depth:
                depth += 1
            out.append("")
            continue
        if stripped.startswith("#endif") and depth:
            depth -= 1
            out.append("")
            continue
        out.append("" if depth else line)
    return "\n".join(out)


def make_persona(root: Path, persona_id: str, *, body: bytes | None = None, **overrides: Any) -> Path:
    """Минимальная персона этапа 6; ``body`` — положить ``model.vrm`` рядом."""
    folder = root / persona_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "persona.md").write_text(f"душа {persona_id}", encoding="utf-8")
    (folder / "fallback.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    card = {
        "id": persona_id,
        "display_name": persona_id.title(),
        "version": 1,
        "brain_profile": "chat",
        "greeting": "привет",
        "tags": ["test"],
        "lora": None,
        "tools": [],
        "memory_scope": persona_id,
    }
    voice = {"pack": "lilith", "speaker": "kseniya", "sample_rate": 24000, "reference_wav": None}
    face = {"vrm_path": "", "slot": 0, "position": [0.0, 0.0, 0.0], "window": {"width": 512, "height": 640}}
    card.update(overrides.pop("card", {}))
    voice.update(overrides.pop("voice", {}))
    face.update(overrides.pop("face", {}))
    (folder / "card.yaml").write_text(yaml.safe_dump(card, allow_unicode=True), encoding="utf-8")
    (folder / "voice.yaml").write_text(yaml.safe_dump(voice, allow_unicode=True), encoding="utf-8")
    (folder / "face.yaml").write_text(yaml.safe_dump(face, allow_unicode=True), encoding="utf-8")
    if body is not None:
        (folder / "model.vrm").write_bytes(body)
    return folder


@pytest.fixture
def personas_dir(tmp_path: Path) -> Path:
    """``lilith`` — с телом (процедурный куб, ADR-017), ``nova`` — без тела."""
    root = tmp_path / "personas"
    make_persona(root, "lilith", body=TEST_VRM.read_bytes())
    make_persona(root, "nova")
    return root


@pytest.fixture
def face_settings(settings: Settings, tmp_project: Path, personas_dir: Path) -> Settings:
    """Настройки с включённым лицом и каталогом персон хотфикса."""
    settings.features.face_enabled = True
    settings.features.voice_enabled = True
    settings.features.brain_enabled = False
    settings.features.memory_enabled = False
    settings.app.personas_dir = str(personas_dir)
    settings.app.agent_id = "lilith"
    return settings


@pytest.fixture
def face_client(face_settings: Settings) -> Iterator[TestClient]:
    """TestClient с живым lifespan."""
    with TestClient(create_app(face_settings)) as client:
        yield client


def _free_port() -> int:
    """Свободный порт для живого сервера."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def live_server(face_settings: Settings) -> Iterator[tuple[str, str]]:
    """Живой uvicorn в фоне: ``(http_base, ws_producer_url)``.

    Нужен, потому что регрессия триггера проверяется **тем же путём, которым
    пойдёт Unity**: настоящий сокет + настоящий HTTP GET тела.
    """
    import uvicorn

    app = create_app(face_settings)
    app.state.started_at = time.time()
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 10.0
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.05)
    else:  # pragma: no cover - сервер не поднялся
        pytest.fail("uvicorn не поднялся за 10 секунд")

    yield f"http://127.0.0.1:{port}", f"ws://127.0.0.1:{port}{face_settings.face.producer_path}"

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def log_lines() -> Iterator[list[str]]:
    """Перехват сообщений loguru (access-лог пишется через неё)."""
    lines: list[str] = []
    sink_id = logger.add(lambda message: lines.append(message.record["message"]), level="INFO")
    try:
        yield lines
    finally:
        try:
            logger.remove(sink_id)
        except ValueError:
            # ``create_app`` зовёт ``setup_logging(force=True)`` → ``logger.remove()``
            # снимает все синки, включая наш. Это не ошибка теста.
            pass


def access_lines(lines: list[str]) -> list[str]:
    """Только строки нашего access-лога (httpx в тестах тоже печатает «HTTP Request»)."""
    return [line for line in lines if line.startswith("HTTP ") and "Request:" not in line]


# --------------------------------------------------------------------------- #
#  A1. Формула URL тела: probe ↔ VrmLoader.BuildModelUrl
# --------------------------------------------------------------------------- #
class TestBodyUrlFormula:
    """URL тела клиент строит сам — и строит его ровно так же, как сервер."""

    def test_probe_formula(self) -> None:
        built = probe.model_url_for("http://127.0.0.1:8765", "lilith")
        assert built == "http://127.0.0.1:8765/api/face/personas/lilith/model.vrm"

    def test_trailing_slash_does_not_double(self) -> None:
        assert "//api" not in probe.model_url_for("http://127.0.0.1:8765/", "lilith")

    def test_http_base_from_ws(self) -> None:
        assert probe.http_base_from_ws("ws://127.0.0.1:8765/ws/face/producer") == "http://127.0.0.1:8765"
        assert probe.http_base_from_ws("wss://host/ws/face/producer") == "https://host"

    def test_csharp_builds_the_same_url(self) -> None:
        """Гвард: в C# та же формула, что в probe (иначе клиент уйдёт в 404)."""
        text = read_cs(VRM_LOADER)
        assert "public static string BuildModelUrl(string baseUrl, string personaId)" in text
        body = text.split("public static string BuildModelUrl", 1)[1].split("/// <summary>", 1)[0]
        assert '"/api/face/personas/"' in body, "формула пути тела изменилась"
        assert '"/model.vrm"' in body
        assert "TrimEnd('/')" in body, "без TrimEnd base со слэшем даст //api"
        assert "Uri.EscapeDataString" in body, "id персоны обязан экранироваться"

    def test_csharp_model_url_for_uses_static_helper(self) -> None:
        text = read_cs(VRM_LOADER)
        block = text.split("public string ModelUrlFor(string personaId)", 1)[1][:200]
        assert "BuildModelUrl(serverBaseUrl, personaId)" in block

    def test_endpoint_path_matches_formula(self, face_client: TestClient) -> None:
        """URL из формулы probe реально существует на сервере."""
        url = probe.model_url_for("http://testserver", "lilith").replace("http://testserver", "")
        response = face_client.get(url)
        assert response.status_code == 200
        assert response.content[:4] == b"glTF"


# --------------------------------------------------------------------------- #
#  A2. Контракт триггера: кто и когда присылает кадр persona
# --------------------------------------------------------------------------- #
class TestTriggerContract:
    """Регрессия блокера: hello+ready кадр persona НЕ приносят — и это норма контракта."""

    def test_hello_and_ready_do_not_push_persona(self, face_client: TestClient) -> None:
        with face_client.websocket_connect("/ws/face/producer") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            assert hello["persona"] == "lilith", "оверлей берёт персону именно отсюда"

            ws.send_json({"type": "hello", "client": "unity/6000.0.84f1"})
            ws.send_json({"type": "ready"})

            # Детерминированно: на client-hello сервер отвечает hello+state,
            # на ready — state. Кадр persona среди них появиться не должен.
            received = [ws.receive_json() for _ in range(3)]
            kinds = [frame.get("type") for frame in received]
            assert "persona" not in kinds, (
                "сервер начал пушить persona сам — контракт A5 изменён, нужен ADR"
            )
            assert kinds.count("hello") == 1 and kinds.count("state") == 2

    def test_persona_request_is_the_trigger(self, face_client: TestClient) -> None:
        with face_client.websocket_connect("/ws/face/producer") as ws:
            assert ws.receive_json()["type"] == "hello"
            ws.send_json({"type": "persona_request", "id": "lilith"})
            frame = ws.receive_json()
            assert frame["type"] == "persona"
            assert frame["id"] == "lilith"
            assert frame["swap"] is True
            assert frame["vrm"] == "/api/face/personas/lilith/model.vrm"
            assert frame["face"]["window"] == {"width": 512, "height": 640}

    def test_frame_vrm_equals_client_built_url(self, face_client: TestClient) -> None:
        """Если сервер прислал ``vrm`` — он совпадает с построенным клиентом."""
        with face_client.websocket_connect("/ws/face/producer") as ws:
            ws.receive_json()
            ws.send_json({"type": "persona_request", "id": "lilith"})
            frame = ws.receive_json()
        built = probe.model_url_for("http://127.0.0.1:8765", "lilith")
        assert "http://127.0.0.1:8765" + frame["vrm"] == built

    def test_persona_without_body_sends_null_vrm(self, face_client: TestClient) -> None:
        """``vrm: null`` — причина, по которой клиент обязан строить URL сам."""
        with face_client.websocket_connect("/ws/face/producer") as ws:
            ws.receive_json()
            ws.send_json({"type": "persona_request", "id": "nova"})
            frame = ws.receive_json()
        assert frame["id"] == "nova"
        assert frame.get("vrm") is None

    def test_registry_knows_body_availability(self, face_settings: Settings) -> None:
        registry = PersonaRegistry(Path(face_settings.app.personas_dir))
        registry.reload()
        assert registry.get("lilith").vrm_available is True
        assert registry.get("nova").vrm_available is False


# --------------------------------------------------------------------------- #
#  A3. Эндпоинт тела
# --------------------------------------------------------------------------- #
class TestModelEndpoint:
    """``GET /api/face/personas/<id>/model.vrm`` — то, что качает Unity."""

    def test_serves_glb(self, face_client: TestClient) -> None:
        response = face_client.get("/api/face/personas/lilith/model.vrm")
        assert response.status_code == 200
        assert response.headers["content-type"] == "model/gltf-binary"
        assert response.content[:4] == b"glTF"
        assert len(response.content) == TEST_VRM.stat().st_size

    def test_missing_body_404_with_hint(self, face_client: TestClient) -> None:
        response = face_client.get("/api/face/personas/nova/model.vrm")
        assert response.status_code == 404
        payload = response.json()
        assert payload["status"] == "missing_vrm"
        assert "vrm_path" in payload["detail"]

    def test_unknown_persona_404(self, face_client: TestClient) -> None:
        assert face_client.get("/api/face/personas/ghost/model.vrm").status_code == 404


# --------------------------------------------------------------------------- #
#  A4. Access-лог /api/face/* (uvicorn поднят с access_log=False)
# --------------------------------------------------------------------------- #
class TestFaceAccessLog:
    """«HTTP-GET'ы серверным логгером не печатаются вообще» — больше не так."""

    def test_default_prefixes(self) -> None:
        assert LoggingSettings().access_log_prefixes == ["/api/face/"]

    def test_config_yaml_ships_the_flag(self) -> None:
        text = (PROJECT_ROOT / "config" / "config.yaml").read_text(encoding="utf-8")
        assert 'access_log_prefixes: ["/api/face/"]' in text

    def test_model_download_is_logged(self, face_client: TestClient, log_lines: list[str]) -> None:
        face_client.get("/api/face/personas/lilith/model.vrm")
        lines = access_lines(log_lines)
        assert any("/api/face/personas/lilith/model.vrm" in line and "200" in line for line in lines), lines

    def test_log_has_method_status_size_and_time(
        self, face_client: TestClient, log_lines: list[str]
    ) -> None:
        face_client.get("/api/face/personas")
        line = next(line for line in access_lines(log_lines) if line.endswith("Б)") or "мс)" in line)
        assert re.search(r"^HTTP GET /api/face/personas → 200 \(\d+ Б, \d+\.\d мс\)$", line), line

    def test_404_is_logged_too(self, face_client: TestClient, log_lines: list[str]) -> None:
        """Отказ тоже обязан быть виден: именно его не хватало на приёмке F7."""
        face_client.get("/api/face/personas/nova/model.vrm")
        assert any("→ 404" in line for line in access_lines(log_lines))

    def test_other_paths_are_not_logged(self, face_client: TestClient, log_lines: list[str]) -> None:
        face_client.get("/healthz")
        face_client.get("/api/version")
        assert not any("/healthz" in line or "/api/version" in line for line in access_lines(log_lines))

    def test_empty_prefixes_disable_middleware(
        self, face_settings: Settings, tmp_project: Path, log_lines: list[str]
    ) -> None:
        face_settings.logging.access_log_prefixes = []
        with TestClient(create_app(face_settings)) as client:
            client.get("/api/face/personas")
        assert access_lines(log_lines) == []

    def test_run_py_keeps_uvicorn_access_log_off(self) -> None:
        """Гвард на решение: общий access-log uvicorn не включаем (поллинг панели)."""
        text = (PROJECT_ROOT / "src" / "lilith_core" / "run.py").read_text(encoding="utf-8")
        assert "access_log=False" in text


# --------------------------------------------------------------------------- #
#  A5. Проба: регрессия триггера end-to-end + контрольный выстрел
# --------------------------------------------------------------------------- #
class TestProbeBodyCheck:
    """``unity_face_probe.py`` обязан ловить «тело не доезжает» без Unity."""

    def test_probe_is_green_when_body_exists(self, live_server: tuple[str, str]) -> None:
        _http, ws_url = live_server
        report = probe.probe(ws_url, timeout=15.0)
        assert report.failed == 0, json.dumps(report.to_dict()["checks"], ensure_ascii=False, indent=2)
        assert report.body["status"] == 200
        assert report.body["magic"] == "glTF"
        assert report.body["bytes"] == TEST_VRM.stat().st_size
        assert "доедет" in report.body["verdict"]

    def test_probe_body_check_is_on_by_default(self, live_server: tuple[str, str]) -> None:
        _http, ws_url = live_server
        assert probe.probe(ws_url, timeout=15.0).body, "проверка тела не запускается по умолчанию"

    def test_probe_skip_flag(self, live_server: tuple[str, str]) -> None:
        _http, ws_url = live_server
        report = probe.probe(ws_url, timeout=15.0, skip_body_check=True)
        assert report.body == {}
        assert report.failed == 0

    def test_control_shot_probe_is_red_without_body(self, live_server: tuple[str, str]) -> None:
        """Контрольный выстрел: у ``nova`` тела нет — проверка обязана покраснеть."""
        _http, ws_url = live_server
        report = probe.Report()
        with probe.MiniWebSocket(ws_url, timeout=10.0) as ws:
            assert ws.recv_text(timeout=5.0)  # hello сервера
            probe.check_body_reaches_scene(ws, report, ws_url=ws_url, persona_id="nova", timeout=5.0)
        assert report.failed >= 2, json.dumps(report.checks, ensure_ascii=False)
        assert report.body["status"] == 404
        assert report.body["magic"] == ""

    def test_control_shot_fetch_body_on_dead_port(self) -> None:
        """Мёртвый порт — не «тихий успех», а понятный провал."""
        result = probe.fetch_body(f"http://127.0.0.1:{_free_port()}/api/face/personas/x/model.vrm", timeout=2.0)
        assert result["status"] == 0
        assert result["magic"] == ""
        assert result["error"]

    def test_access_log_sees_real_download(self, live_server: tuple[str, str], log_lines: list[str]) -> None:
        """На живом сокете: Unity качает тело — в логе сервера остаётся строка."""
        _http, ws_url = live_server
        probe.probe(ws_url, timeout=15.0)
        time.sleep(0.2)
        assert any("model.vrm" in line and "→ 200" in line for line in access_lines(log_lines))


# --------------------------------------------------------------------------- #
#  B1. VrmLoader: URL строим сами, своп идемпотентен
# --------------------------------------------------------------------------- #
class TestVrmLoaderSelfBuiltUrl:
    """Просьба архитектора: «VrmLoader сам строит url по приходу кадра персоны/ready»."""

    def test_swap_falls_back_to_self_built_url(self) -> None:
        text = read_cs(VRM_LOADER)
        swap = text.split("public void Swap(string personaId", 1)[1].split("private IEnumerator SwapRoutine", 1)[0]
        assert code_calls(swap, "relative = ModelUrlFor(personaId);"), "Swap больше не строит URL сам"
        assert 'source = "URL построен клиентом"' in swap

    def test_dead_end_message_is_gone(self) -> None:
        """Прежний тупик «нет ни локального пути, ни URL модели» больше не существует."""
        assert "нет ни локального пути, ни URL модели" not in read_cs(VRM_LOADER)

    def test_empty_persona_still_fails_loudly(self) -> None:
        text = read_cs(VRM_LOADER)
        assert "URL тела построить не из чего" in text
        assert "Failed?.Invoke(personaId, VrmLoadResult.NotFound)" in text

    def test_swap_is_idempotent(self) -> None:
        text = read_cs(VRM_LOADER)
        signature = "public void Swap(string personaId, string localPath, string serverRelativePath = \"\", bool force = false)"
        assert signature in text, "сигнатура Swap изменилась — гварды и сцена могут разъехаться"
        guard = text.split(signature, 1)[1][:600]
        assert "!force" in guard and "Model != null" in guard and "CurrentPersona == personaId" in guard

    def test_idempotence_guard_logs(self) -> None:
        assert "уже на сцене — повторный своп не нужен" in read_cs(VRM_LOADER)

    def test_in_flight_guard_exists(self) -> None:
        """Сервер шлёт ``hello`` дважды — без этого гварда тело качалось бы два раза."""
        text = read_cs(VRM_LOADER)
        assert "public string LoadingPersona" in text
        swap = text.split("public void Swap(string personaId", 1)[1].split("/// <summary>", 1)[0]
        assert "_loadingPersona == personaId" in swap, "гвард «уже грузится» пропал"
        assert "уже грузится — повторный своп пропущен" in swap
        assert code_calls(swap, "_loadingPersona = personaId ?? \"\";"), "Swap не запоминает, кого грузит"

    def test_in_flight_guard_is_reset_by_clear(self) -> None:
        clear = read_cs(VRM_LOADER).split("public void Clear()", 1)[1][:400]
        assert code_calls(clear, '_loadingPersona = "";')

    def test_generation_recheck_survived(self) -> None:
        """Правка 0.6.3 (повторная проверка generation после загрузки) не потеряна."""
        assert read_cs(VRM_LOADER).count("generation != _generation") == 2

    def test_body_info_summary(self) -> None:
        text = read_cs(VRM_LOADER)
        assert "public readonly struct BodyInfo" in text
        assert "public BodyInfo LastBody { get; private set; }" in text
        assert "public string Describe()" in text

    def test_no_await_in_loader(self) -> None:
        """Гвард 0.6.3 (CS4032) не сломан новой правкой: ``await`` — только в комментариях."""
        offenders = [
            line.strip()
            for line in read_cs(VRM_LOADER).splitlines()
            if re.search(r"\bawait\s", line) and not line.strip().startswith(("//", "*"))
        ]
        assert offenders == [], f"await в корутине вернёт CS4032: {offenders}"


# --------------------------------------------------------------------------- #
#  B2. Логи свопа: старт / финиш / отказ — безусловно
# --------------------------------------------------------------------------- #
class TestSwapLogs:
    """«Ни NotifyFailed в Console, ни исключений» — потому что логов не было вовсе."""

    @pytest.mark.parametrize(
        "needle",
        [
            "[Lilith] тело: старт свопа",
            "[Lilith] тело: фаза 6 — ГОТОВО",
            "[Lilith] тело: ОТКАЗ",
            "[Lilith] тело: фаза 1 — скачано",
        ],
    )
    def test_log_points_exist(self, needle: str) -> None:
        assert needle in read_cs(VRM_LOADER)

    def test_every_failure_branch_logs(self) -> None:
        """В ``SwapRoutine`` рядом с каждым ``Failed?.Invoke`` есть строка в Console.

        Публичные ``NotifyLoaded``/``NotifyFailed`` (0.6.3) в проверку не входят:
        они сами являются точкой уведомления для внешнего кода.
        """
        text = read_cs(VRM_LOADER)
        routine = text.split("private IEnumerator SwapRoutine(", 1)[1]
        routine = routine.split("/// <summary>Превратить относительный путь", 1)[0]
        invokes = [match.start() for match in re.finditer(r"Failed\?\.Invoke", routine)]
        assert len(invokes) >= 5, "веток отказа в SwapRoutine стало меньше — проверь правку"
        for position in invokes:
            window = routine[max(0, position - 700) : position]
            assert re.search(r"Debug\.(LogWarning|LogError)\(", window), (
                f"Failed?.Invoke на символе {position} без лога рядом"
            )

    def test_loader_logs_are_not_gated_by_verbose(self) -> None:
        assert "if (config.verbose)" not in read_cs(VRM_LOADER), "логи загрузчика снова спрятали за verbose"

    def test_loader_logs_live_outside_univrm_branch(self) -> None:
        """Старт/отказ видны и до импорта UniVRM (ветка ``#else`` тоже логом закрыта)."""
        outside = strip_preprocessor_blocks(read_cs(VRM_LOADER))
        assert "[Lilith] тело: старт свопа" in outside
        assert "[Lilith] тело: ОТКАЗ" in outside

    def test_finish_log_reports_size_time_and_parent(self) -> None:
        text = read_cs(VRM_LOADER)
        finish = text.split("ГОТОВО", 1)[1][:400]
        assert "мс" in finish and "МБ" in finish and "родитель" in finish

    def test_stopwatch_is_realtime(self) -> None:
        """``Time.realtimeSinceStartup`` — иначе пауза загрузки исказит отчёт."""
        assert "Time.realtimeSinceStartup" in read_cs(VRM_LOADER)


# --------------------------------------------------------------------------- #
#  B3. LilithFaceClient: триггер на hello + watchdog
# --------------------------------------------------------------------------- #
class TestClientTrigger:
    """Клиент больше не ждёт, что сервер сам пришлёт кадр persona."""

    def test_hello_triggers_body(self) -> None:
        text = read_cs(FACE_CLIENT)
        on_hello = text.split("private void OnHello(Dictionary<string, object> frame)", 1)[1]
        on_hello = on_hello.split("private void OnAudio", 1)[0]
        assert code_calls(on_hello, 'EnsureBodyOnConnect("hello")'), (
            "OnHello не зовёт EnsureBodyOnConnect — тело снова не доедет до сцены"
        )

    def test_ensure_body_asks_server(self) -> None:
        text = read_cs(FACE_CLIENT)
        # окно с запасом: в 0.6.4 метод вырос на дедупликацию persona_request
        block = text.split("private void EnsureBodyOnConnect(string reason)", 1)[1][:2200]
        assert "config.requestPersonaOnConnect" in block
        assert code_calls(block, "RequestPersona(ActivePersona)"), "клиент снова не просит кадр persona"
        assert code_calls(block, "_personaWatchdog = StartCoroutine(PersonaWatchdog(reason))")

    def test_watchdog_waits_and_loads(self) -> None:
        text = read_cs(FACE_CLIENT)
        block = text.split("private IEnumerator PersonaWatchdog(string reason)", 1)[1][:900]
        assert "WaitForSecondsRealtime" in block
        assert "config.personaFrameTimeoutSec" in block
        assert "_personaFrameSeen" in block
        assert "LoadBodyBySelfBuiltUrl" in block

    def test_self_built_load_calls_swap_without_url(self) -> None:
        """Пустой ``serverRelativePath`` — значит URL построит ``VrmLoader`` сам."""
        text = read_cs(FACE_CLIENT)
        block = text.split("public void LoadBodyBySelfBuiltUrl(string reason", 1)[1][:900]
        assert code_calls(block, 'vrmLoader.Swap(ActivePersona, "")'), (
            "watchdog перестал грузить тело по собственному URL"
        )

    def test_no_await_outside_async(self) -> None:
        """Гвард 0.6.3: ``async`` в клиенте только у LilithWSClient."""
        text = read_cs(FACE_CLIENT)
        assert not re.search(r"\bawait\b", text)
        assert "async" not in text

    def test_persona_frame_cancels_watchdog(self) -> None:
        text = read_cs(FACE_CLIENT)
        on_persona = text.split("private void OnPersona(Dictionary<string, object> frame)", 1)[1]
        on_persona = on_persona.split("private void ApplyPersonaWindow", 1)[0]
        assert "_personaFrameSeen = true" in on_persona
        assert "StopCoroutine(_personaWatchdog)" in on_persona

    def test_persona_request_is_deduplicated(self) -> None:
        """Два ``hello`` от сервера не должны превращаться в два ``persona_request``."""
        text = read_cs(FACE_CLIENT)
        block = text.split("private void EnsureBodyOnConnect(string reason)", 1)[1][:1800]
        assert "_personaRequestedFor == ActivePersona" in block, "дедупликация запроса кадра пропала"
        assert "уже запрошен — повтор не шлю" in block
        assert code_calls(block, "_personaRequestedFor = ActivePersona;")

    def test_dedup_is_reset_on_disconnect(self) -> None:
        """После разрыва новое соединение обязано снова попросить кадр."""
        text = read_cs(FACE_CLIENT)
        disconnect = text.split("public void Disconnect()", 1)[1][:900]
        assert code_calls(disconnect, '_personaRequestedFor = "";')
        assert code_calls(disconnect, "_personaFrameSeen = false;")

    def test_watchdog_stops_only_while_alive(self) -> None:
        """``StopCoroutine`` на уничтожаемом объекте дал бы жёлтый warning (а нам нужны 0)."""
        text = read_cs(FACE_CLIENT)
        disconnect = text.split("public void Disconnect()", 1)[1][:700]
        assert "activeInHierarchy" in disconnect


class TestSwapFlagIsRespected:
    """``swap:false`` наконец значит «не грузить»."""

    def test_old_broken_condition_is_gone(self) -> None:
        assert 'GetString(frame, "swap") != ""' not in read_cs(FACE_CLIENT)

    def test_bool_is_parsed(self) -> None:
        text = read_cs(FACE_CLIENT)
        assert 'GetBool(frame, "swap", true)' in text
        assert code_calls(text, "if (!swap)"), "swap:false снова игнорируется"
        assert "private static bool GetBool(Dictionary<string, object> frame, string key, bool fallback)" in text

    def test_persona_log_is_unconditional(self) -> None:
        """Лог про персону больше не спрятан за ``config.verbose`` (причина «тихого no-op»)."""
        text = read_cs(FACE_CLIENT)
        on_persona = text.split("private void OnPersona(Dictionary<string, object> frame)", 1)[1]
        on_persona = on_persona.split("private void ApplyPersonaWindow", 1)[0]
        assert 'Debug.Log($"[Lilith] персона: {personaId}' in on_persona
        assert "if (config.verbose)" not in on_persona

    def test_missing_loader_is_an_error(self) -> None:
        assert "VrmLoader не назначен" in read_cs(FACE_CLIENT)


class TestOverlayShowsBody:
    """Приёмка F7 смотрит в оверлей — тело должно быть видно там."""

    def test_overlay_has_body_line(self) -> None:
        """Строка про тело живёт в оверлее.

        0.6.6: текст оверлея переехал из ``OnGUI()`` в ``BuildOverlayText()`` —
        ``OnGUI`` вызывается несколько раз за кадр, и строка дрожала в билде.
        Гвард стал строже: проверяем и сборку текста, и что снимок доехал до поля.
        """
        text = read_cs(FACE_CLIENT)
        builder = text.split("private void BuildOverlayText()", 1)[1][:2200]
        assert 'тело {body}' in builder
        assert "vrmLoader.LastBody.Describe()" in builder
        assert "грузится…" in builder
        assert "_overlayText = text;" in builder, "снимок не сохраняется в поле"

    def test_ongui_draws_the_snapshot_not_a_recompute(self) -> None:
        ongui = read_cs(FACE_CLIENT).split("private void OnGUI()", 1)[1][:900]
        assert "_overlayText" in ongui, "OnGUI рисует не снимок"
        assert "Describe()" not in ongui, "OnGUI снова считает текст сам — дрожание вернётся"

    def test_overlay_rect_grew(self) -> None:
        assert "Screen.width - 16, 140" in read_cs(FACE_CLIENT)

    def test_loaded_event_logs_rig_bind(self) -> None:
        text = read_cs(FACE_CLIENT)
        block = text.split("private void OnVrmLoaded(string personaId, GameObject model)", 1)[1][:400]
        assert "Debug.Log" in block and "привязано к ригу" in block


class TestNewConfigFields:
    """Три новых поля инспектора — с дефолтами, при которых тело доезжает само."""

    @pytest.mark.parametrize(
        ("field", "default"),
        [
            ("public bool autoLoadBody", "true"),
            ("public bool requestPersonaOnConnect", "true"),
            ("public float personaFrameTimeoutSec", "2f"),
        ],
    )
    def test_field_and_default(self, field: str, default: str) -> None:
        text = read_cs(CLIENT_CONFIG)
        assert f"{field} = {default};" in text, f"{field} пропал или сменил дефолт"

    def test_fields_have_tooltips(self) -> None:
        text = read_cs(CLIENT_CONFIG)
        for field in ("autoLoadBody", "requestPersonaOnConnect", "personaFrameTimeoutSec"):
            index = text.index(field)
            assert "[Tooltip(" in text[max(0, index - 900) : index], f"{field} без тултипа"

    def test_no_broken_attribute_parens(self) -> None:
        """Контрольный выстрел по себе: ``"))]`` — опечатка, которую чекер уже ловил."""
        for path in (VRM_LOADER, FACE_CLIENT, CLIENT_CONFIG):
            assert '"))]' not in read_cs(path), f"{path.name}: лишняя скобка в атрибуте"


# --------------------------------------------------------------------------- #
#  A6. sync_test_count.py: путь от репо + хирургия (пункт 4 просилки архитектора)
# --------------------------------------------------------------------------- #
sync_script = _load_script("sync_test_count")

PLAN_TEXT = """# PLAN

> **Версия:** `0.6.4` · **ТЕСТЫ СЕЙЧАС: 691 passed, 2 skipped** (с tree-sitter — 693)

| 16.09.2026 | Этап 1: 477 passed — это факт истории, не трогать |
"""

README_TEXT = """# README

| | |
|---|---|
| Версия | `0.6.4` |
| Тесты | **691 passed, 2 skipped** (pytest), 0 warnings · с tree-sitter — 693 |
"""


@pytest.fixture
def sync_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Фальшивое дерево «workspace + проект» для функциональной проверки скрипта."""
    project = tmp_path / "lilith"
    (project / "scripts").mkdir(parents=True)
    memory = tmp_path / "память и личность"
    memory.mkdir()
    (memory / "PLAN.md").write_text(PLAN_TEXT, encoding="utf-8")
    (project / "README.md").write_text(README_TEXT, encoding="utf-8")
    (project / "CHANGELOG.md").write_text("## Этап 1\n\n477 passed — история\n", encoding="utf-8")

    monkeypatch.setattr(sync_script, "WORKSPACE", tmp_path)
    monkeypatch.setattr(sync_script, "PROJECT", project)
    monkeypatch.setattr(sync_script, "README", project / "README.md")
    monkeypatch.setattr(
        sync_script,
        "PLAN_CANDIDATES",
        (tmp_path / "lilith" / "PLAN.md", memory / "PLAN.md", project / "PLAN.md"),
    )
    return tmp_path


class TestSyncTestCountScript:
    """Скрипт живёт в проекте (едет в архиве), пути берёт от репо, правит только маркеры."""

    def test_script_lives_in_project(self) -> None:
        """Иначе он не попадает в архив этапа и теряется при следующем переезде."""
        assert (PROJECT_ROOT / "scripts" / "sync_test_count.py").is_file()

    def test_no_hardcoded_old_workspace_name(self) -> None:
        text = (PROJECT_ROOT / "scripts" / "sync_test_count.py").read_text(encoding="utf-8")
        assert "Локальная Лилит" not in text, "скрипт снова смотрит в папку старого workspace"

    def test_paths_are_relative_to_script(self) -> None:
        text = (PROJECT_ROOT / "scripts" / "sync_test_count.py").read_text(encoding="utf-8")
        assert "Path(__file__).resolve().parents[1]" in text

    def test_updates_marker_and_readme_row(self, sync_tree: Path) -> None:
        assert sync_script.sync(764) == 2
        plan = (sync_tree / "память и личность" / "PLAN.md").read_text(encoding="utf-8")
        readme = (sync_tree / "lilith" / "README.md").read_text(encoding="utf-8")
        assert "ТЕСТЫ СЕЙЧАС: 764 passed" in plan
        assert "с tree-sitter — 766" in plan, "маркер PLAN.md обязан обновлять и число «с tree-sitter»"
        assert "**764 passed, 2 skipped**" in readme
        assert "с tree-sitter — 766" in readme, "число «с tree-sitter» = count + TREE_SITTER_TESTS"

    def test_history_inside_plan_is_untouched(self, sync_tree: Path) -> None:
        """Маркер обновляем, а «477 passed» в логе этапа — факт, не данные."""
        sync_script.sync(764)
        plan = (sync_tree / "память и личность" / "PLAN.md").read_text(encoding="utf-8")
        assert "477 passed" in plan

    def test_changelog_is_never_touched(self, sync_tree: Path) -> None:
        before = (sync_tree / "lilith" / "CHANGELOG.md").read_text(encoding="utf-8")
        sync_script.sync(764)
        assert (sync_tree / "lilith" / "CHANGELOG.md").read_text(encoding="utf-8") == before

    def test_dry_run_writes_nothing(self, sync_tree: Path) -> None:
        before_plan = (sync_tree / "память и личность" / "PLAN.md").read_text(encoding="utf-8")
        before_readme = (sync_tree / "lilith" / "README.md").read_text(encoding="utf-8")
        assert sync_script.sync(500, dry_run=True) == 2
        assert (sync_tree / "память и личность" / "PLAN.md").read_text(encoding="utf-8") == before_plan
        assert (sync_tree / "lilith" / "README.md").read_text(encoding="utf-8") == before_readme

    def test_dry_run_is_honest_when_nothing_changes(self, sync_tree: Path) -> None:
        """Контрольный выстрел по себе: ``re.subn`` возвращает ЧИСЛО ЗАМЕН, а не факт изменения.

        Первая версия скрипта на этом попалась: dry-run рапортовал «БУДЕТ обновлён»
        для уже правильного числа. Поэтому сравниваем текст до/после.
        """
        sync_script.sync(764)
        assert sync_script.sync(764, dry_run=True) == 0

    def test_forbidden_docs_are_refused(self, sync_tree: Path) -> None:
        for name in ("CHANGELOG.md", "STAGE1_REPORT.md", "RELEASE_0.6.4.md"):
            with pytest.raises(SystemExit):
                sync_script.check_not_forbidden([Path(name)])

    def test_missing_plan_is_not_fatal(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Гибридная папка без PLAN.md: обновляем README и не падаем."""
        project = tmp_path / "lilith"
        project.mkdir()
        (project / "README.md").write_text(README_TEXT, encoding="utf-8")
        monkeypatch.setattr(sync_script, "WORKSPACE", tmp_path)
        monkeypatch.setattr(sync_script, "PROJECT", project)
        monkeypatch.setattr(sync_script, "README", project / "README.md")
        monkeypatch.setattr(sync_script, "PLAN_CANDIDATES", (tmp_path / "nope" / "PLAN.md",))
        assert sync_script.sync(764) == 1
        assert "**764 passed" in (project / "README.md").read_text(encoding="utf-8")

    def test_repo_docs_are_in_sync_with_collection(self, request: pytest.FixtureRequest) -> None:
        """Число в доках = фактическая коллекция тестов минус два известных skip.

        Гвард без хардкода числа: добавил тест и забыл обновить доки → красный здесь,
        а не «у Кирюши в отчёте не сходится». Два skip — это либо тесты чекера C#
        (без tree-sitter), либо самопроверка архива 0.6.0 (в git `artifacts/` не лежит):
        в любом окружении их ровно два.
        """
        collected = len(request.session.items)
        if collected < 700:
            # Прогон частичный (отдельный файл/класс): сверять число с коллекцией
            # бессмысленно. Полный прогон — run_tests.bat — гвард держит.
            pytest.skip(f"неполная коллекция ({collected} тестов) — гвард работает на всём прогоне")

        plan_file = sync_script.find_plan()
        assert plan_file is not None, "PLAN.md в репо не найден"
        plan = plan_file.read_text(encoding="utf-8")
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

        plan_count = int(re.search(r"ТЕСТЫ СЕЙЧАС:\s*(\d+) passed", plan).group(1))
        readme_count = int(re.search(r"\| Тесты \| \*\*(\d+) passed", readme).group(1))
        expected = collected - 2

        assert plan_count == readme_count, f"PLAN.md ({plan_count}) и README ({readme_count}) разошлись"
        assert plan_count == expected, (
            f"в доках {plan_count} passed, а коллекция даёт {expected} "
            f"({collected} тестов − 2 skip). Обнови: python scripts/sync_test_count.py"
        )
        assert sync_script.sync(plan_count, dry_run=True) == 0, "скип-счётчик в README разъехался"


class TestNoForeignScripts:
    """В C#-комментариях не должно быть случайных не-русских вставок."""

    @pytest.mark.parametrize("name", ["VrmLoader.cs", "LilithFaceClient.cs", "LilithClientConfig.cs"])
    def test_no_cjk_in_touched_files(self, name: str) -> None:
        text = read_cs(SCRIPTS_DIR / name)
        found = re.findall(r"[\u3040-\u30ff\u4e00-\u9fff]+", text)
        assert not found, f"{name}: посторонние символы {found[:3]}"

    def test_face_rig_keeps_japanese_blendshapes(self) -> None:
        """А вот в FaceRig японские имена морфов — легальны (их ждёт VRoid)."""
        assert "笑い" in read_cs(SCRIPTS_DIR / "FaceRig.cs")
