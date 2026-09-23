"""Тесты артефактов этапа 6: процедурная VRM 1.0 (C5.3) и эмулятор Unity-клиента (F6-б).

Первая часть проверяет, что ``scripts/make_test_vrm.py`` собирает валидный контейнер
GLB с расширением ``VRMC_vrm``: полный humanoid-скелет, морф-таргеты и все
экспрессии, которые нужны Unity-клиенту (виземы ``aa..oh``, эмоции, ``blink``).

Вторая часть поднимает **живой** сервер на свободном порту и прогоняет против него
``scripts/unity_face_probe.py`` — тот самый сценарий, который запустит Кирюша
перед сборкой Unity. Это и есть доказательство критерия готовности этапа 6 в
песочнице, где Unity не поднять.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"


def _load_script(name: str):
    """Загрузить модуль из ``scripts/`` (там нет ``__init__.py``)."""
    path = SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"lilith_script_{name}", path)
    assert spec is not None and spec.loader is not None, f"не загрузить {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


make_test_vrm = _load_script("make_test_vrm")
unity_face_probe = _load_script("unity_face_probe")


# --------------------------------------------------------------------------- #
#  Процедурная VRM (C5.3)
# --------------------------------------------------------------------------- #
class TestMakeTestVrm:
    """Скрипт собирает VRM 1.0, который UniVRM 0.131 сможет импортировать."""

    @pytest.fixture(scope="class")
    def vrm(self) -> tuple[bytes, dict]:
        data = make_test_vrm.build_vrm(name="TestCube")
        return data, make_test_vrm.validate(data)

    def test_glb_container(self, vrm: tuple[bytes, dict]) -> None:
        data, _info = vrm
        assert data[:4] == b"glTF"
        assert len(data) > 4096  # не пустышка
        assert len(data) < 200_000  # и не тяжелее настоящих тел (D5.4: git не таскает 60 МБ)

    def test_full_humanoid_skeleton(self, vrm: tuple[bytes, dict]) -> None:
        _data, info = vrm
        assert info["human_bones"] == len(make_test_vrm.HUMAN_BONES)
        assert info["nodes"] >= len(make_test_vrm.HUMAN_BONES) + 3  # + root + два меша

    def test_expressions_cover_visemes_and_emotions(self, vrm: tuple[bytes, dict]) -> None:
        _data, info = vrm
        presets = set(info["expressions"])
        assert {"aa", "ih", "ou", "ee", "oh"} <= presets, "нет ротовых визем VRM 1.0"
        assert {"happy", "angry", "sad", "relaxed", "surprised"} <= presets, "нет эмоций VRM 1.0"
        assert "blink" in presets, "нет моргания"
        assert info["morph_targets"] == len(make_test_vrm.EXPRESSIONS)

    def test_sample_file_is_committed(self) -> None:
        sample = PROJECT_ROOT / "tests" / "samples" / "test_cube.vrm"
        assert sample.is_file(), "сгенерируй: python scripts/make_test_vrm.py"
        info = make_test_vrm.validate(sample.read_bytes())
        assert info["human_bones"] > 0

    def test_validate_rejects_garbage(self) -> None:
        with pytest.raises(ValueError):
            make_test_vrm.validate("не vrm".encode("utf-8"))

    def test_validate_rejects_gltf_without_vrm(self) -> None:
        import struct as _struct

        payload = json.dumps({"asset": {"version": "2.0"}, "buffers": [{"byteLength": 4}]}).encode()
        payload += b" " * ((4 - len(payload) % 4) % 4)
        binary = b"\x00\x00\x00\x00"
        total = 12 + 8 + len(payload) + 8 + len(binary)
        blob = _struct.pack("<III", make_test_vrm.GLTF_MAGIC, 2, total)
        blob += _struct.pack("<II", len(payload), make_test_vrm.CHUNK_JSON) + payload
        blob += _struct.pack("<II", len(binary), make_test_vrm.CHUNK_BIN) + binary
        with pytest.raises(ValueError, match="VRMC_vrm"):
            make_test_vrm.validate(blob)

    def test_validate_rejects_truncated_glb(self) -> None:
        data = make_test_vrm.build_vrm()
        with pytest.raises(ValueError):
            make_test_vrm.validate(data[: 12 + 8])


# --------------------------------------------------------------------------- #
#  Эмулятор Unity-клиента против живого сервера (F6-б)
# --------------------------------------------------------------------------- #
def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def live_server(settings, tmp_project, tmp_path):
    """Поднять uvicorn в фоне и вернуть (базовый HTTP-URL, ws-URL продюсера)."""
    import uvicorn

    from lilith_core.app import create_app

    personas_dir = tmp_project / "personas"
    (personas_dir / "lilith").mkdir(parents=True)
    (personas_dir / "lilith" / "persona.md").write_text("душа", encoding="utf-8")
    (personas_dir / "lilith" / "fallback.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (personas_dir / "nova").mkdir()
    (personas_dir / "nova" / "persona.md").write_text("душа новы", encoding="utf-8")
    # Хотфикс 0.6.4: у активной персоны есть тело — иначе новая дефолтная проверка
    # пробы «тело доезжает» (persona_request → GET model.vrm → magic glTF) не имеет
    # смысла. Берём процедурный тест-куб (ADR-017), а не настоящую модель (D5.4).
    shutil.copyfile(
        PROJECT_ROOT / "tests" / "samples" / "test_cube.vrm",
        personas_dir / "lilith" / "model.vrm",
    )

    settings.features.face_enabled = True
    settings.features.voice_enabled = True
    settings.features.memory_enabled = False
    settings.features.brain_enabled = False
    settings.app.personas_dir = str(personas_dir)
    settings.app.agent_id = "lilith"

    app = create_app(settings)
    # lifespan запускает uvicorn (в TestClient его дёргает сам клиент), поэтому
    # готовим состояние приложения руками — так же, как это делает _lifespan.
    from lilith_core.persona import build_system_prompt, load_persona

    app.state.started_at = time.time()
    app.state.persona = load_persona(settings=settings)
    app.state.system_prompt = build_system_prompt(settings=settings)

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

    yield f"http://127.0.0.1:{port}", f"ws://127.0.0.1:{port}{settings.face.producer_path}", app

    server.should_exit = True
    thread.join(timeout=5)


class TestUnityFaceProbe:
    """Проба клиента: тот же путь, которым пойдёт настоящий Unity."""

    def test_mini_websocket_handshake(self, live_server) -> None:
        _http, ws_url, _app = live_server
        ws = unity_face_probe.MiniWebSocket(ws_url, timeout=5.0)
        with ws:
            hello = json.loads(ws.recv_text(timeout=5.0) or "{}")
            assert hello["type"] == "hello"
            assert hello["producer"] == "lilith-face"
            ws.send_json({"type": "ping"})
            # сервер может прислать state/pong в любом порядке — читаем до pong
            for _ in range(5):
                frame = json.loads(ws.recv_text(timeout=5.0) or "{}")
                if frame.get("type") == "pong":
                    break
            else:  # pragma: no cover
                pytest.fail("не дождались pong")

    def test_probe_reports_stream(self, live_server) -> None:
        _http, ws_url, _app = live_server
        report = unity_face_probe.probe(
            ws_url, speak="Привет, Кирюша. Мур-мур.", timeout=15.0
        )
        payload = report.to_dict()
        assert report.failed == 0, json.dumps(payload["checks"], ensure_ascii=False, indent=2)
        assert payload["audio"]["chunks"] >= 1
        assert payload["audio"]["bytes"] % 2 == 0
        assert payload["frames"]["done"] >= 1
        assert payload["audio"]["offset_ms_monotonic"] is True

    def test_probe_wants_server_visemes(self, live_server) -> None:
        _http, ws_url, _app = live_server
        report = unity_face_probe.probe(
            ws_url, speak="Раз два три.", want_server_visemes=True, timeout=15.0
        )
        assert report.failed == 0
        assert sum(report.visemes.values()) > 0, "серверные виземы не пришли"

    def test_probe_persona_swap(self, live_server) -> None:
        _http, ws_url, _app = live_server
        report = unity_face_probe.probe(ws_url, persona="nova", timeout=15.0)
        assert report.failed == 0
        assert "nova" in report.persona_frames

    def test_probe_group_scene(self, live_server) -> None:
        http, _ws_url, _app = live_server
        group_url = http.replace("http://", "ws://") + "/ws/group"
        report = unity_face_probe.probe(group_url, group="main", speak="Раз.", timeout=15.0)
        assert report.frames.get("hello-group", 0) >= 1
        assert report.hello.get("max_participants") == 4

    def test_probe_fails_loudly_on_dead_server(self) -> None:
        """Мёртвый порт — не «тихий успех», а понятный провал (скрипт вернёт 1)."""
        report = unity_face_probe.probe(f"ws://127.0.0.1:{_free_port()}/ws/face/producer", timeout=2.0)
        assert report.failed >= 1
        assert report.passed == 0

    def test_probe_cli_exit_code(self, live_server, tmp_path) -> None:
        _http, ws_url, _app = live_server
        out = tmp_path / "probe.json"
        argv = [
            "unity_face_probe.py",
            "--url", ws_url,
            "--speak", "Проверка CLI.",
            "--json", str(out),
        ]
        old_argv = sys.argv
        try:
            sys.argv = argv
            code = unity_face_probe.main()
        finally:
            sys.argv = old_argv
        assert code == 0
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["ok"] is True
        assert data["audio"]["chunks"] >= 1
