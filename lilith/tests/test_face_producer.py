"""Тесты этапа 6: продюсер лица для Unity, реестр персон v2, LoRA-слот, группы.

Покрывают серверную половину пивота (Unity в песочнице не запускается, поэтому
критерий готовности — этот файл + ``scripts/unity_face_probe.py`` у Кирюши).

Что проверяем:

* :mod:`lilith_core.voice.pcm` — ресемплинг, ровные чанки по 2048 байт, смещения;
* :mod:`lilith_core.face.ws_frames` — форма кадров контракта (A3);
* :mod:`lilith_core.face.personas` — ``card.yaml``/``voice.yaml``/``face.yaml``
  + legacy-фолбэк на ``profile.yaml`` (D1-б), своп активной персоны (D9);
* :mod:`lilith_core.face.lora` — prompt-only режим и стабы бэкендов (D10-а);
* :mod:`lilith_core.face.group` — потолок участников, слоты, фокус (E1–E5);
* HTTP: ``/api/face/personas`` (D8), отдача VRM вне репозитория (D5.4), активация;
* WS: ``/ws/face/producer`` — ``hello`` → ``speak`` → ``audio``×N → ``done``,
  прерывание ``stop`` (A3.4), серверные виземы по ``want_server_visemes`` (A3.1),
  ``/ws/group`` — состав и фокус.
"""

from __future__ import annotations

import base64
import json
import math
import struct
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from lilith_core.app import create_app
from lilith_core.face import (
    GroupFull,
    GroupManager,
    LoraSpec,
    PersonaLoraManager,
    PersonaRegistry,
    PromptOnlyLora,
    UnknownPersona,
    build_lora_manager,
    ws_frames,
)
from lilith_core.face.endpoints import parse_client_frame
from lilith_core.voice.pcm import (
    CHUNK_BYTES,
    DEFAULT_SAMPLE_RATE,
    PcmChunker,
    resample_pcm16,
    wav_to_pcm,
)
from lilith_core.voice.stt import write_wav


# --------------------------------------------------------------------------- #
#  helpers
# --------------------------------------------------------------------------- #
def sine_pcm(ms: int, freq: float = 220.0, amp: int = 18000, rate: int = 16000) -> bytes:
    """Синусоида в raw PCM int16 mono."""
    n = max(1, rate * ms // 1000)
    return struct.pack(f"<{n}h", *[int(amp * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)])


def make_persona(root: Path, persona_id: str, **overrides) -> Path:
    """Создать папку персоны в формате этапа 6 (card/voice/face.yaml)."""
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
    face = {"vrm_path": "", "slot": 0, "position": [0.0, 0.0, 0.0], "idle": {"blink_freq": 0.3}}
    card.update(overrides.pop("card", {}))
    voice.update(overrides.pop("voice", {}))
    face.update(overrides.pop("face", {}))

    (folder / "card.yaml").write_text(yaml.safe_dump(card, allow_unicode=True), encoding="utf-8")
    (folder / "voice.yaml").write_text(yaml.safe_dump(voice, allow_unicode=True), encoding="utf-8")
    (folder / "face.yaml").write_text(yaml.safe_dump(face, allow_unicode=True), encoding="utf-8")
    for name, blob in overrides.items():
        (folder / name).write_bytes(blob)
    return folder


@pytest.fixture
def personas_dir(tmp_path: Path) -> Path:
    """Каталог с двумя персонами этапа 6 и одной legacy-персоной."""
    root = tmp_path / "personas"
    make_persona(root, "lilith")
    make_persona(root, "nova", card={"tags": ["guest"], "lora": {"path": "nova.safetensors", "autoload": True}})
    legacy = root / "old"
    legacy.mkdir(parents=True)
    (legacy / "persona.md").write_text("старая душа", encoding="utf-8")
    (legacy / "profile.yaml").write_text(
        yaml.safe_dump({"voice": "legacy-voice", "brain": "coder", "note": "старый формат"}),
        encoding="utf-8",
    )
    return root


@pytest.fixture
def face6_settings(settings, tmp_project, personas_dir):
    """Настройки с включёнными лицом/голосом и каталогом персон этапа 6."""
    settings.features.face_enabled = True
    settings.features.voice_enabled = True
    settings.features.brain_enabled = True
    settings.brain.defaults.provider = "mock"
    settings.app.personas_dir = str(personas_dir)
    settings.app.agent_id = "lilith"
    settings.face.producer_sample_rate = DEFAULT_SAMPLE_RATE
    settings.face.producer_chunk_bytes = CHUNK_BYTES
    return settings


@pytest.fixture
def f6_client(face6_settings) -> TestClient:
    """TestClient с живым lifespan."""
    app = create_app(face6_settings)
    with TestClient(app) as client:
        yield client


# --------------------------------------------------------------------------- #
#  PCM-конвейер
# --------------------------------------------------------------------------- #
class TestResample:
    """Приведение потока к целевой частоте (A2)."""

    def test_same_rate_is_noop(self) -> None:
        pcm = sine_pcm(50, rate=24000)
        assert resample_pcm16(pcm, 24000, 24000) == pcm

    def test_upsample_changes_length(self) -> None:
        pcm = sine_pcm(100, rate=16000)  # 1600 сэмплов
        out = resample_pcm16(pcm, 16000, 24000)
        assert len(out) // 2 == pytest.approx(2400, abs=2)

    def test_downsample_changes_length(self) -> None:
        pcm = sine_pcm(100, rate=48000)  # 4800 сэмплов
        out = resample_pcm16(pcm, 48000, 24000)
        assert len(out) // 2 == pytest.approx(2400, abs=2)

    def test_empty_and_short_input(self) -> None:
        assert resample_pcm16(b"", 16000, 24000) == b""
        assert resample_pcm16(struct.pack("<h", 100), 16000, 24000) == struct.pack("<h", 100)

    def test_bad_rate_raises(self) -> None:
        with pytest.raises(ValueError):
            resample_pcm16(sine_pcm(10), 0, 24000)

    def test_wav_to_pcm_converts(self) -> None:
        wav = write_wav(sine_pcm(100, rate=48000), 48000)
        pcm, rate = wav_to_pcm(wav, 24000)
        assert rate == 24000
        assert len(pcm) // 2 == pytest.approx(2400, abs=2)


class TestPcmChunker:
    """Ровные чанки по 2048 байт (A1-а)."""

    def test_exact_multiple(self) -> None:
        chunker = PcmChunker(sample_rate=24000, chunk_bytes=CHUNK_BYTES, utterance_id="u-1")
        chunks = list(chunker.push(b"\x01\x02" * CHUNK_BYTES))
        assert len(chunks) == 2
        assert all(len(c.data) == CHUNK_BYTES for c in chunks)
        assert [c.seq for c in chunks] == [0, 1]
        assert chunker.flush() is None

    def test_tail_is_shorter_and_final(self) -> None:
        chunker = PcmChunker(sample_rate=24000, chunk_bytes=CHUNK_BYTES)
        total = CHUNK_BYTES * 2 + 512
        chunks = list(chunker.push(b"\x00" * total))
        assert len(chunks) == 2
        tail = chunker.flush()
        assert tail is not None and len(tail.data) == 512 and tail.final is True
        assert tail.seq == 2

    def test_offsets_are_continuous(self) -> None:
        chunker = PcmChunker(sample_rate=24000, chunk_bytes=CHUNK_BYTES)
        chunks = list(chunker.push(b"\x00" * (CHUNK_BYTES * 3)))
        assert [c.byte_offset for c in chunks] == [0, CHUNK_BYTES, CHUNK_BYTES * 2]
        # 1024 сэмпла на 24 kHz ≈ 42.67 мс
        assert chunks[1].offset_ms == 42
        assert chunks[2].offset_ms == 85

    def test_incremental_push(self) -> None:
        """Данные приходят кусочками — чанк всё равно ровно 2048 байт."""
        chunker = PcmChunker(sample_rate=24000, chunk_bytes=CHUNK_BYTES)
        out: list = []
        for _ in range(10):
            out.extend(chunker.push(b"\x00" * 500))
        out.extend(chunker.close())
        assert sum(len(c.data) for c in out) == 5000
        assert all(len(c.data) == CHUNK_BYTES for c in out[:-1])

    def test_bad_chunk_size(self) -> None:
        with pytest.raises(ValueError):
            PcmChunker(chunk_bytes=1023)  # не кратно 2


# --------------------------------------------------------------------------- #
#  Кадры протокола (A3)
# --------------------------------------------------------------------------- #
class TestWsFrames:
    """Форма кадров, зафиксированная контрактом продюсера."""

    def test_hello_shape(self) -> None:
        frame = ws_frames.hello_frame(
            sample_rate=24000, chunk_bytes=2048, persona="lilith", server_version="0.6.0"
        )
        assert frame["type"] == "hello"
        assert frame["producer"] == "lilith-face"
        assert frame["format"] == "pcm_s16le"
        assert frame["sample_rate"] == 24000
        assert frame["chunk_bytes"] == 2048
        assert frame["persona"] == "lilith"

    def test_audio_frame_is_base64_of_raw_pcm(self) -> None:
        chunker = PcmChunker(sample_rate=24000, chunk_bytes=CHUNK_BYTES)
        chunk = next(iter(chunker.push(b"\x11\x22" * CHUNK_BYTES)))
        frame = ws_frames.audio_frame(chunk, utterance_id="u-9", persona="lilith")
        assert frame["type"] == "audio"
        assert frame["bytes"] == CHUNK_BYTES
        assert base64.b64decode(frame["data"]) == chunk.data
        assert frame["utterance_id"] == "u-9"
        assert frame["persona"] == "lilith"
        assert frame["sample_rate"] == 24000

    def test_emotion_frame_has_ttl(self) -> None:
        frame = ws_frames.emotion_frame("joy", intensity=0.5, ttl_ms=3000, utterance_id="u-1")
        assert frame == {
            "type": "emotion",
            "tag": "joy",
            "intensity": 0.5,
            "ttl_ms": 3000,
            "utterance_id": "u-1",
        }

    def test_persona_focus_stop_done_frames(self) -> None:
        assert ws_frames.persona_frame("nova", swap=True)["type"] == "persona"
        assert ws_frames.focus_frame("nova", group="main")["group"] == "main"
        stop = ws_frames.stop_frame("u-1")
        assert stop["type"] == "stop" and stop["utterance_id"] == "u-1" and stop["reason"] == "barge_in"
        assert ws_frames.done_frame("u-1", chunks=7)["chunks"] == 7
        assert ws_frames.error_frame("ой", code="x")["code"] == "x"
        assert ws_frames.pong_frame("p1")["ping_id"] == "p1"

    def test_client_frame_parser(self) -> None:
        frame, error = parse_client_frame('{"type":"speak","text":"привет"}')
        assert error == "" and frame["type"] == "speak"

    def test_client_frame_parser_rejects(self) -> None:
        assert parse_client_frame("не json")[0] is None
        assert parse_client_frame('{"text":"без type"}')[0] is None
        assert parse_client_frame('{"type":"unknown"}')[0] is None
        assert parse_client_frame(b'{"type":"ping"}')[0]["type"] == "ping"

    def test_client_frame_size_limit(self) -> None:
        big = json.dumps({"type": "speak", "text": "ы" * 70000})
        frame, error = parse_client_frame(big)
        assert frame is None and "больше" in error


# --------------------------------------------------------------------------- #
#  Реестр персон v2 (D1–D9)
# --------------------------------------------------------------------------- #
class TestPersonaRegistryV2:
    """card/voice/face.yaml + legacy-фолбэк + активная персона."""

    def test_reads_new_format(self, personas_dir: Path) -> None:
        registry = PersonaRegistry(personas_dir, active="lilith")
        lilith = registry.get("lilith")
        assert lilith is not None
        assert lilith.card.display_name == "Lilith"
        assert lilith.card.memory_scope == "lilith"
        assert lilith.card.tools == []           # D6: пусто = запрещено всё
        assert lilith.voice_spec.pack == "lilith"
        assert lilith.voice_spec.sample_rate == 24000
        assert lilith.voice_spec.reference_wav is None
        assert lilith.face_spec.idle.blink_freq == pytest.approx(0.3)
        assert lilith.source == "card"

    def test_legacy_profile_fallback(self, personas_dir: Path) -> None:
        registry = PersonaRegistry(personas_dir)
        old = registry.get("old")
        assert old is not None
        assert old.source == "profile"
        assert old.voice == "legacy-voice"
        assert old.brain == "coder"
        assert old.extra["note"] == "старый формат"

    def test_lora_slot_is_prompt_only_by_default(self, personas_dir: Path) -> None:
        registry = PersonaRegistry(personas_dir)
        assert registry.get("lilith").card.lora.path == ""            # lora: null
        nova = registry.get("nova").card.lora
        assert nova.path == "nova.safetensors" and nova.autoload is True
        assert registry.get("lilith").card.lora.as_dict()["mode"] == "prompt-only"

    def test_vrm_outside_repo(self, personas_dir: Path, tmp_path: Path) -> None:
        """D5.4: тело лежит ВНЕ репозитория, путь — из face.yaml."""
        body = tmp_path / "bodies" / "lilith.vrm"
        body.parent.mkdir(parents=True)
        body.write_bytes(b"glTF" + b"\x00" * 64)
        spec = personas_dir / "lilith" / "face.yaml"
        data = yaml.safe_load(spec.read_text(encoding="utf-8"))
        data["vrm_path"] = str(body)
        spec.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

        registry = PersonaRegistry(personas_dir)
        lilith = registry.get("lilith")
        assert lilith.vrm_available is True
        assert lilith.has_vrm is False          # в самой папке персоны model.vrm нет
        assert lilith.describe()["has_vrm"] is True

    def test_describe_shape(self, personas_dir: Path) -> None:
        """D8: публичная сводка без приватных полей карточки."""
        row = {r["id"]: r for r in PersonaRegistry(personas_dir).describe()}["lilith"]
        assert set(row) == {
            "id", "display_name", "vrm", "has_vrm", "voice", "fallback",
            "lora", "tools", "memory_scope", "greeting", "tags", "face", "source",
        }
        assert row["voice"] == {"pack": "lilith", "speaker": "kseniya"}
        assert row["lora"]["state"] == "prompt-only"
        assert "persona_md" not in row

    def test_active_persona_and_swap(self, personas_dir: Path) -> None:
        registry = PersonaRegistry(personas_dir, active="lilith")
        assert registry.active == "lilith"
        assert registry.set_active("nova").id == "nova"
        assert registry.active == "nova"
        assert registry.set_active("нет-такой") is None
        assert registry.active == "nova"

    def test_active_defaults_to_first_when_unknown(self, personas_dir: Path) -> None:
        assert PersonaRegistry(personas_dir, active="призрак").active == "lilith"

    def test_hot_reload_keeps_active(self, personas_dir: Path) -> None:
        registry = PersonaRegistry(personas_dir, active="lilith")
        make_persona(personas_dir, "third")
        registry.reload()
        assert registry.active == "lilith"
        assert "third" in registry.ids()


# --------------------------------------------------------------------------- #
#  LoRA (D10-а)
# --------------------------------------------------------------------------- #
class TestLoraSlot:
    """Prompt-only слот + стабы бэкендов."""

    @pytest.mark.asyncio
    async def test_prompt_only_apply(self, personas_dir: Path) -> None:
        manager = PersonaLoraManager()
        persona = PersonaRegistry(personas_dir).get("nova")
        state = await manager.apply(persona)
        assert state.mode == "prompt-only"
        assert state.loaded is False
        assert state.persona == "nova"
        assert "prompt-only" in state.reason

    @pytest.mark.asyncio
    async def test_prompt_only_unload(self) -> None:
        manager = PersonaLoraManager()
        await manager.unload()
        assert manager.state().persona == ""

    def test_resolve_path_prefers_lora_dir(self, personas_dir: Path, tmp_path: Path) -> None:
        lora_dir = tmp_path / "loras"
        lora_dir.mkdir()
        (lora_dir / "nova.safetensors").write_bytes(b"weights")
        manager = PersonaLoraManager(lora_dir=lora_dir)
        persona = PersonaRegistry(personas_dir).get("nova")
        resolved = Path(manager.resolve_path(persona.card.lora, persona))
        assert resolved == lora_dir / "nova.safetensors"

    def test_stub_backends_report_unavailable(self) -> None:
        from lilith_core.face.lora import LlamaCppLora, LMStudioLora, LocalAiLora

        for backend in (LocalAiLora(), LMStudioLora(), LlamaCppLora()):
            ok, reason = backend.available()
            assert ok is False and reason
        assert PromptOnlyLora().available()[0] is True

    def test_build_from_settings(self, face6_settings) -> None:
        manager = build_lora_manager(face6_settings)
        assert isinstance(manager.backend, PromptOnlyLora)
        assert manager.scale == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_unavailable_backend_falls_back(self, personas_dir: Path) -> None:
        """Если в конфиге стаб — менеджер честно уходит в prompt-only."""
        from lilith_core.face.lora import LocalAiLora

        manager = PersonaLoraManager(backend=LocalAiLora())
        persona = PersonaRegistry(personas_dir).get("nova")
        state = await manager.apply(persona)
        assert isinstance(manager.backend, PromptOnlyLora)
        assert state.loaded is False


# --------------------------------------------------------------------------- #
#  Группы (E1–E5)
# --------------------------------------------------------------------------- #
class TestGroups:
    """Потолок участников, слоты, фокус."""

    def test_join_assigns_slots(self, personas_dir: Path) -> None:
        registry = PersonaRegistry(personas_dir)
        session = GroupManager(registry, max_participants=4).ensure("main", participants=["lilith", "nova"])
        assert session.persona_ids() == ["lilith", "nova"]
        assert [m.slot for m in sorted(session.members, key=lambda m: m.slot)] == [0, 1]
        assert session.focus == "lilith"

    def test_capacity_limit(self, personas_dir: Path) -> None:
        registry = PersonaRegistry(personas_dir)
        manager = GroupManager(registry, max_participants=2)
        session = manager.ensure("main", participants=["lilith", "nova"])
        with pytest.raises(GroupFull):
            session.join(registry.get("old"))

    def test_unknown_persona(self, personas_dir: Path) -> None:
        with pytest.raises(UnknownPersona):
            GroupManager(PersonaRegistry(personas_dir)).ensure("main", participants=["призрак"])

    def test_leave_moves_focus(self, personas_dir: Path) -> None:
        session = GroupManager(PersonaRegistry(personas_dir)).ensure("main", participants=["lilith", "nova"])
        assert session.focus == "lilith"
        assert session.leave("lilith") is True
        assert session.focus == "nova"
        assert session.leave("lilith") is False

    @pytest.mark.asyncio
    async def test_set_focus_and_publish(self, personas_dir: Path) -> None:
        manager = GroupManager(PersonaRegistry(personas_dir))
        session = manager.ensure("main", participants=["lilith", "nova"])
        queue = session.subscribe()
        assert await manager.set_focus("main", "nova") is True
        assert session.focus == "nova"
        assert await manager.set_focus("main", "lilith") is False or session.has("lilith")
        await session.publish(ws_frames.focus_frame("nova", group="main"))
        frame = queue.get_nowait()
        assert frame["type"] == "focus" and frame["persona"] == "nova"

    @pytest.mark.asyncio
    async def test_full_queue_drops_frame(self, personas_dir: Path) -> None:
        session = GroupManager(PersonaRegistry(personas_dir)).ensure("main", participants=["lilith"])
        queue = session.subscribe(maxsize=1)
        await session.publish(ws_frames.focus_frame("lilith"))
        assert await session.publish(ws_frames.focus_frame("lilith")) == 0  # переполнение не блокирует

    def test_group_file_layout(self, personas_dir: Path, tmp_path: Path) -> None:
        """E4: group.yaml задаёт состав сцены."""
        group_file = tmp_path / "group.yaml"
        group_file.write_text(yaml.safe_dump({"main": {"participants": ["nova"]}}), encoding="utf-8")
        manager = GroupManager(PersonaRegistry(personas_dir), group_file=group_file)
        assert manager.default_layout("main") == ["nova"]
        assert manager.ensure("main").persona_ids() == ["nova"]

    def test_describe(self, personas_dir: Path) -> None:
        manager = GroupManager(PersonaRegistry(personas_dir))
        manager.ensure("main", participants=["lilith"])
        info = manager.describe()["main"]
        assert info["max_participants"] == 4
        assert info["participants"][0]["persona"] == "lilith"


# --------------------------------------------------------------------------- #
#  HTTP: /api/face/personas, VRM, активация (D8, D5.4, D9)
# --------------------------------------------------------------------------- #
class TestFacePersonasHttp:
    """Эндпоинты реестра персон этапа 6."""

    def test_list(self, f6_client: TestClient) -> None:
        payload = f6_client.get("/api/face/personas").json()
        assert payload["active"] == "lilith"
        ids = [row["id"] for row in payload["personas"]]
        assert ids == ["lilith", "nova", "old"]
        active_flags = {row["id"]: row["active"] for row in payload["personas"]}
        assert active_flags["lilith"] is True and active_flags["nova"] is False
        assert payload["lora"]["state"] == "prompt-only"

    def test_legacy_alias_still_works(self, f6_client: TestClient) -> None:
        payload = f6_client.get("/api/personas").json()
        assert payload["default"] == "lilith"
        assert payload["personas"][0]["id"] == "lilith"

    def test_vrm_missing_is_404(self, f6_client: TestClient) -> None:
        response = f6_client.get("/api/face/personas/lilith/model.vrm")
        assert response.status_code == 404
        assert response.json()["status"] == "missing_vrm"

    def test_vrm_unknown_persona_is_404(self, f6_client: TestClient) -> None:
        assert f6_client.get("/api/face/personas/призрак/model.vrm").status_code == 404

    def test_vrm_served_from_outside_repo(self, f6_client: TestClient, tmp_path: Path, personas_dir: Path) -> None:
        body = tmp_path / "lilith-body.vrm"
        body.write_bytes(b"glTF\x02\x00\x00\x00" + b"\x00" * 128)
        spec = personas_dir / "lilith" / "face.yaml"
        data = yaml.safe_load(spec.read_text(encoding="utf-8"))
        data["vrm_path"] = str(body)
        spec.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

        response = f6_client.get("/api/face/personas/lilith/model.vrm")
        assert response.status_code == 200
        assert response.content.startswith(b"glTF")

    def test_activate_switches_persona(self, f6_client: TestClient) -> None:
        response = f6_client.post("/api/face/personas/nova/activate")
        assert response.status_code == 200
        body = response.json()
        assert body["active"] == "nova"
        assert body["frame"]["type"] == "persona" and body["frame"]["id"] == "nova"
        assert f6_client.get("/api/face/personas").json()["active"] == "nova"

    def test_activate_unknown_is_404(self, f6_client: TestClient) -> None:
        response = f6_client.post("/api/face/personas/призрак/activate")
        assert response.status_code == 404

    def test_groups_endpoint(self, f6_client: TestClient) -> None:
        payload = f6_client.get("/api/face/groups").json()
        assert payload["max_participants"] == 4
        assert isinstance(payload["groups"], dict)

    def test_face_state_includes_producer(self, f6_client: TestClient) -> None:
        payload = f6_client.get("/api/face/state").json()
        assert payload["enabled"] is True


# --------------------------------------------------------------------------- #
#  WS: /ws/face/producer
# --------------------------------------------------------------------------- #
def drain_until(socket, predicate, limit: int = 400) -> list[dict]:
    """Читать кадры, пока ``predicate(frame)`` не станет True; вернуть всё прочитанное."""
    frames: list[dict] = []
    for _ in range(limit):
        frame = socket.receive_json()
        frames.append(frame)
        if predicate(frame):
            return frames
    raise AssertionError(f"не дождались условия за {limit} кадров: {[f.get('type') for f in frames][-10:]}")


class TestProducerWs:
    """Полный тракт: hello → speak → audio×N → done."""

    def test_hello_on_connect(self, f6_client: TestClient) -> None:
        with f6_client.websocket_connect("/ws/face/producer") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            assert hello["producer"] == "lilith-face"
            assert hello["sample_rate"] == DEFAULT_SAMPLE_RATE
            assert hello["chunk_bytes"] == CHUNK_BYTES
            assert hello["format"] == "pcm_s16le"
            assert hello["persona"] == "lilith"
            assert set(hello["personas"]) == {"lilith", "nova", "old"}

    def test_client_hello_sets_preferences(self, f6_client: TestClient) -> None:
        with f6_client.websocket_connect("/ws/face/producer") as ws:
            ws.receive_json()
            ws.send_text(json.dumps({"type": "hello", "want_server_visemes": True, "client": "test/1.0"}))
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            state = ws.receive_json()
            assert state["type"] == "state"
            assert state["want_server_visemes"]  # серверная разметка включена для нас

    def test_speak_streams_pcm_chunks(self, f6_client: TestClient) -> None:
        with f6_client.websocket_connect("/ws/face/producer") as ws:
            ws.receive_json()
            ws.send_text(json.dumps({"type": "speak", "text": "Привет, Кирюша. Мур-мур."}))
            frames = drain_until(ws, lambda f: f.get("type") == "done")

            audio = [f for f in frames if f["type"] == "audio"]
            assert audio, "аудио-кадров не было"
            for frame in audio[:-1]:
                assert frame["bytes"] == CHUNK_BYTES
                assert len(base64.b64decode(frame["data"])) == CHUNK_BYTES
            assert [f["seq"] for f in audio] == list(range(len(audio)))
            assert len({f["utterance_id"] for f in audio}) == 1
            assert audio[-1]["final"] is True
            assert frames[-1]["type"] == "done"
            assert frames[-1]["chunks"] == len(audio)
            # A3.1: без want_server_visemes серверных визем нет — Unity считает сам
            assert not [f for f in frames if f["type"] == "viseme"]

    def test_want_server_visemes_enables_marks(self, f6_client: TestClient) -> None:
        with f6_client.websocket_connect("/ws/face/producer") as ws:
            ws.receive_json()
            ws.send_text(json.dumps({"type": "hello", "want_server_visemes": True}))
            ws.receive_json()
            ws.receive_json()
            ws.send_text(json.dumps({"type": "speak", "text": "Раз два три четыре пять."}))
            frames = drain_until(ws, lambda f: f.get("type") == "done")
            assert [f for f in frames if f["type"] == "viseme"], "серверные виземы не пришли"

    def test_ping_pong_and_stats(self, f6_client: TestClient) -> None:
        with f6_client.websocket_connect("/ws/face/producer") as ws:
            ws.receive_json()
            ws.send_text(json.dumps({"type": "ping", "id": "p-1"}))
            assert ws.receive_json() == {"type": "pong", "ping_id": "p-1"}
            ws.send_text(json.dumps({"type": "stats", "fps": 60, "dropped": 0}))
            state = f6_client.get("/api/face/personas").json()["producer"]
            assert any(s.get("fps") == 60 for s in state["clients_stats"].values())

    def test_persona_request_swaps(self, f6_client: TestClient) -> None:
        with f6_client.websocket_connect("/ws/face/producer") as ws:
            ws.receive_json()
            ws.send_text(json.dumps({"type": "persona_request", "id": "nova"}))
            frame = drain_until(ws, lambda f: f.get("type") == "persona")[-1]
            assert frame["id"] == "nova" and frame["swap"] is True
            assert frame["voice"]["pack"] == "lilith"
            assert f6_client.get("/api/face/personas").json()["active"] == "nova"

    def test_persona_request_unknown(self, f6_client: TestClient) -> None:
        with f6_client.websocket_connect("/ws/face/producer") as ws:
            ws.receive_json()
            ws.send_text(json.dumps({"type": "persona_request", "id": "призрак"}))
            frame = ws.receive_json()
            assert frame["type"] == "error" and frame["code"] == "unknown_persona"

    def test_speak_empty_text_is_error(self, f6_client: TestClient) -> None:
        with f6_client.websocket_connect("/ws/face/producer") as ws:
            ws.receive_json()
            ws.send_text(json.dumps({"type": "speak", "text": "  "}))
            frame = ws.receive_json()
            assert frame["type"] == "error" and frame["code"] == "empty_text"

    def test_bad_frame_keeps_connection(self, f6_client: TestClient) -> None:
        with f6_client.websocket_connect("/ws/face/producer") as ws:
            ws.receive_json()
            ws.send_text("{не json")
            assert ws.receive_json()["code"] == "bad_frame"
            ws.send_text(json.dumps({"type": "ping"}))
            assert ws.receive_json()["type"] == "pong"

    def test_voice_disabled_reports_error(self, face6_settings) -> None:
        face6_settings.features.voice_enabled = False
        app = create_app(face6_settings)
        with TestClient(app) as client:
            with client.websocket_connect("/ws/face/producer") as ws:
                ws.receive_json()
                ws.send_text(json.dumps({"type": "speak", "text": "ау"}))
                frame = ws.receive_json()
                assert frame["type"] == "error" and frame["code"] == "voice_disabled"

    def test_panel_voice_also_feeds_producer(self, f6_client: TestClient) -> None:
        """A6.2: основной /ws стримит в панель, продюсер получает тот же поток."""
        with f6_client.websocket_connect("/ws/face/producer") as producer:
            producer.receive_json()
            with f6_client.websocket_connect("/ws") as panel:
                panel.receive_json()
                panel.receive_json()
                panel.send_json({"type": "voice", "text": "Раз.", "data": {}})

                legacy = drain_until(panel, lambda f: f["data"].get("kind") == "done")
                assert [f["data"]["kind"] for f in legacy].count("audio") >= 1

                flat = drain_until(producer, lambda f: f.get("type") == "done")
                assert [f["type"] for f in flat].count("audio") >= 1
                assert flat[-1]["reason"] == "eof"

    def test_stop_interrupts_utterance(self, f6_client: TestClient) -> None:
        """A3.4: ``stop`` отменяет задачу реплики и рассылает кадр ``stop``.

        MockTTS отдаёт всё мгновенно, поэтому здесь — медленный бэкенд, который
        держит реплику в полёте, пока тест её прерывает.
        """
        import asyncio

        from starlette.testclient import WebSocketTestSession

        from lilith_core.voice.tts import TTSBackend, VoiceProfile  # noqa: F401 - контракт бэкенда

        class SlowTTS(TTSBackend):
            """Тянущийся бэкенд: по одному длинному wav-предложению с паузами."""

            name = "slow"

            def available(self) -> tuple[bool, str]:
                return True, ""

            async def synthesize(self, text: str, profile: VoiceProfile) -> bytes:
                return write_wav(sine_pcm(400, rate=24000), 24000)

            async def stream(self, text: str, profile: VoiceProfile):
                for _ in range(30):
                    yield await self.synthesize(text, profile)
                    await asyncio.sleep(0.05)

        # VoiceCore.sync() пересобирает профили из конфига на каждый вызов, поэтому
        # подменяем сам бэкенд 'mock' (именно его резолвит профиль 'lilith').
        voice_core = f6_client.app.state.voice
        voice_core.tts._backends["mock"] = SlowTTS()

        hub = f6_client.app.state.face_producer
        with f6_client.websocket_connect("/ws/face/producer") as ws:
            ws.receive_json()
            ws.send_text(json.dumps({"type": "speak", "text": "Очень длинная фраза."}))
            first = drain_until(ws, lambda f: f.get("type") == "audio")[-1]
            utterance_id = first["utterance_id"]

            assert isinstance(ws, WebSocketTestSession)
            stopped = ws.portal.call(hub.stop, utterance_id)
            assert stopped is True

            frame = drain_until(ws, lambda f: f.get("type") == "stop")[-1]
            assert frame["utterance_id"] == utterance_id
            assert frame["reason"] == "barge_in"

    def test_stop_without_utterance_is_false(self, f6_client: TestClient) -> None:
        """Прерывать нечего — честно возвращаем False, ошибки клиенту не шлём."""
        from starlette.testclient import WebSocketTestSession

        hub = f6_client.app.state.face_producer
        with f6_client.websocket_connect("/ws/face/producer") as ws:
            ws.receive_json()
            assert isinstance(ws, WebSocketTestSession)
            assert ws.portal.call(hub.stop, "u-нет-такой") is False

    def test_legacy_unity_alias(self, f6_client: TestClient) -> None:
        """A6.1-б: /ws/unity жив и отдаёт оба протокола."""
        with f6_client.websocket_connect("/ws/unity") as ws:
            legacy = ws.receive_json()
            assert legacy["adapter"] == "unity-vrm-salsa"
            assert legacy["alias_of"] == "/ws/face/producer"
            producer_hello = ws.receive_json()
            assert producer_hello["producer"] == "lilith-face"


@pytest.fixture
def group_client(face6_settings, personas_dir, tmp_path) -> TestClient:
    """Клиент с ``group.yaml``, где в сцене только lilith: слоты для новых свободны."""
    group_file = tmp_path / "group.yaml"
    group_file.write_text(yaml.safe_dump({"main": {"participants": ["lilith"]}}), encoding="utf-8")
    face6_settings.face.group_file = str(group_file)
    app = create_app(face6_settings)
    with TestClient(app) as client:
        yield client


class TestGroupWs:
    """``/ws/group``: состав, рассадка, фокус (E3/E4)."""

    def test_hello_group(self, f6_client: TestClient) -> None:
        with f6_client.websocket_connect("/ws/group") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello-group"
            assert hello["group"] == "main"
            assert hello["max_participants"] == 4
            assert hello["sample_rate"] == DEFAULT_SAMPLE_RATE
            personas = [row["persona"] for row in hello["participants"]]
            assert "lilith" in personas

    def test_named_group_via_query(self, f6_client: TestClient) -> None:
        with f6_client.websocket_connect("/ws/group?group=stream") as ws:
            assert ws.receive_json()["group"] == "stream"

    def test_persona_request_joins(self, group_client: TestClient) -> None:
        """E4: слот из ``face.yaml``/запроса; ``group.yaml`` задал состав сцены."""
        with group_client.websocket_connect("/ws/group") as ws:
            hello = ws.receive_json()
            assert [row["persona"] for row in hello["participants"]] == ["lilith"]

            ws.send_text(json.dumps({"type": "persona_request", "id": "nova", "slot": 3}))
            frames = drain_until(ws, lambda f: f.get("type") == "layout")
            layout = frames[-1]
            assert layout["joined"]["persona"] == "nova"
            assert layout["joined"]["slot"] == 3
            assert {row["persona"] for row in layout["participants"]} == {"lilith", "nova"}

    def test_join_is_idempotent(self, group_client: TestClient) -> None:
        """Повторный запрос той же персоны не плодит участников."""
        with group_client.websocket_connect("/ws/group") as ws:
            ws.receive_json()
            ws.send_text(json.dumps({"type": "persona_request", "id": "nova"}))
            drain_until(ws, lambda f: f.get("type") == "layout")
            ws.send_text(json.dumps({"type": "persona_request", "id": "nova"}))
            layout = drain_until(ws, lambda f: f.get("type") == "layout")[-1]
            assert [row["persona"] for row in layout["participants"]].count("nova") == 1

    def test_group_full_reports_error(self, f6_client: TestClient) -> None:
        f6_client.app.state.face_groups.max_participants = 1
        with f6_client.websocket_connect("/ws/group") as ws:
            ws.receive_json()
            ws.send_text(json.dumps({"type": "persona_request", "id": "nova"}))
            frame = drain_until(ws, lambda f: f.get("type") == "error")[-1]
            assert frame["code"] == "group_full"

    def test_focus_frame_on_speak(self, f6_client: TestClient) -> None:
        with f6_client.websocket_connect("/ws/group") as ws:
            ws.receive_json()
            ws.send_text(json.dumps({"type": "speak", "text": "Раз.", "persona": "lilith"}))
            frame = drain_until(ws, lambda f: f.get("type") == "focus")[-1]
            assert frame["persona"] == "lilith" and frame["group"] == "main"

    def test_speak_by_non_member_is_error(self, f6_client: TestClient) -> None:
        with f6_client.websocket_connect("/ws/group") as ws:
            ws.receive_json()
            ws.send_text(json.dumps({"type": "speak", "text": "Раз.", "persona": "призрак"}))
            frame = ws.receive_json()
            assert frame["type"] == "error" and frame["code"] == "not_a_member"
