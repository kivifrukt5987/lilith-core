"""Тесты этапа 5 v2: виземы, реестр персон, голос+лицо по WS, unity-адаптер."""

from __future__ import annotations

import math
import struct

import pytest
from fastapi.testclient import TestClient

from lilith_core.app import create_app
from lilith_core.face import PersonaRegistry, VISEME_WINDOWS, extract_visemes, analyze_window


def pcm_tone(ms: int, freq: float = 220.0, amp: int = 20000, rate: int = 16000) -> bytes:
    """Низкий громкий тон -> открытые корзины."""
    n = rate * ms // 1000
    return struct.pack("<%dh" % n, *[int(amp * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)])


def pcm_bright(ms: int, amp: int = 8000, rate: int = 16000) -> bytes:
    """Яркий шумоподобный сигнал (высокий ZCR) -> узкие корзины."""
    import random

    rnd = random.Random(7)
    n = rate * ms // 1000
    return struct.pack("<%dh" % n, *[int(amp * rnd.choice([-1, 1]) * (0.4 + 0.6 * rnd.random())) for i in range(n)])


def pcm_silence(ms: int, rate: int = 16000) -> bytes:
    return b"\x00\x00" * (rate * ms // 1000)


class TestVisemes:
    """Фонемные корзины из PCM."""

    def test_silence_is_rest(self) -> None:
        frames = extract_visemes(pcm_silence(200))
        assert frames
        assert all(f.viseme == "rest" and f.intensity == 0.0 for f in frames)

    def test_loud_low_tone_is_open(self) -> None:
        frames = extract_visemes(pcm_tone(200, freq=180))
        assert frames
        assert frames[0].viseme in ("A", "O", "U")
        assert frames[0].intensity > 0.3

    def test_bright_is_narrow(self) -> None:
        frames = extract_visemes(pcm_bright(200))
        assert frames
        assert frames[0].viseme in ("I", "E")

    def test_window_count(self) -> None:
        frames = extract_visemes(pcm_tone(300))  # 300 мс / 60 мс = 5 окон
        assert len(frames) == 5

    def test_intensity_clamped(self) -> None:
        frames = extract_visemes(pcm_tone(200, amp=32700))
        assert frames
        assert all(0.0 <= f.intensity <= 1.0 for f in frames)

    def test_vocabulary(self) -> None:
        assert set(VISEME_WINDOWS) == {"A", "I", "U", "E", "O", "rest"}


class TestPersonaRegistry:
    """personas/<id>: душа, тело, фолбэк."""

    def test_scan(self, tmp_path) -> None:
        (tmp_path / "lilith").mkdir(parents=True)
        (tmp_path / "lilith" / "persona.md").write_text("душа", encoding="utf-8")
        (tmp_path / "lilith" / "fallback.jpg").write_bytes(b"\xff\xd8\xff")
        (tmp_path / "lilith" / "profile.yaml").write_text("voice: lilith\nbrain: chat\n", encoding="utf-8")
        (tmp_path / "asya").mkdir()

        registry = PersonaRegistry(tmp_path)
        listing = {row["id"]: row for row in registry.list()}

        assert set(listing) == {"lilith", "asya"}
        assert listing["lilith"]["has_persona_md"] is True
        assert listing["lilith"]["fallback"].endswith("/personas/lilith/fallback.jpg")
        assert listing["lilith"]["vrm"] is None
        assert listing["lilith"]["voice"] == "lilith"
        assert listing["asya"]["has_persona_md"] is False

    def test_vrm_detected(self, tmp_path) -> None:
        (tmp_path / "lilith").mkdir(parents=True)
        (tmp_path / "lilith" / "model.vrm").write_bytes(b"VRM ")
        registry = PersonaRegistry(tmp_path)
        assert registry.get("lilith").has_vrm is True
        assert registry.get("lilith").as_dict()["vrm"].endswith("model.vrm")

    def test_hot_reload(self, tmp_path) -> None:
        registry = PersonaRegistry(tmp_path)
        assert registry.ids() == []
        (tmp_path / "nova").mkdir()
        registry.reload()
        assert registry.ids() == ["nova"]

    def test_missing_dir_is_empty(self, tmp_path) -> None:
        assert PersonaRegistry(tmp_path / "nope").list() == []


@pytest.fixture
def face_voice_settings(settings, tmp_project):
    settings.features.face_enabled = True
    settings.features.voice_enabled = True
    settings.features.brain_enabled = True
    settings.brain.defaults.provider = "mock"
    settings.app.personas_dir = str(tmp_project / "personas")
    (tmp_project / "personas" / "lilith").mkdir(parents=True)
    (tmp_project / "personas" / "lilith" / "fallback.jpg").write_bytes(b"\xff\xd8\xff")
    return settings


@pytest.fixture
def fv_client(face_voice_settings):
    app = create_app(face_voice_settings)
    with TestClient(app) as client:
        yield client


class TestVoiceFaceWs:
    """Голос+лицо по тому же сокету: аудио-чанки, виземы, эмоции."""

    def test_personas_endpoint(self, fv_client: TestClient) -> None:
        payload = fv_client.get("/api/personas").json()
        assert payload["default"] == "lilith"
        assert payload["personas"][0]["fallback"].endswith("fallback.jpg")

    def test_voice_stream_emits_audio_and_visemes(self, fv_client: TestClient) -> None:
        with fv_client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.receive_json()
            ws.send_json({"type": "voice", "text": "Привет, Кирюша. Мур-мур.", "data": {}})

            kinds = []
            seqs = set()
            while True:
                frame = ws.receive_json()
                assert frame["type"] == "face"
                kinds.append(frame["data"]["kind"])
                if frame["data"]["kind"] == "audio":
                    seqs.add(frame["data"]["seq"])
                if frame["data"]["kind"] == "done":
                    break

        assert kinds.count("audio") >= 1
        assert "viseme" in kinds
        assert kinds[-1] == "done"
        assert seqs == {0, 1}  # два предложения -> два аудио-чанка

    def test_voice_disabled_is_error(self, settings) -> None:
        settings.features.voice_enabled = False
        app = create_app(settings)
        with TestClient(app) as client:
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()
                ws.receive_json()
                ws.send_json({"type": "voice", "text": "ау", "data": {}})
                frame = ws.receive_json()
                assert frame["type"] == "error"
                assert frame["data"]["code"] == "voice_disabled"

    def test_emotion_frames_after_tagged_reply(self, fv_client: TestClient) -> None:
        app = fv_client.app

        async def tagged(text, context):
            from lilith_core.protocol import chat_message

            return chat_message("[emotion: joy] Привет!")

        app.state.reply_handler = tagged

        with fv_client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.receive_json()
            ws.send_json({"type": "chat", "text": "ау"})
            faces = []
            final = None
            while True:
                frame = ws.receive_json()
                if frame["type"] == "face":
                    faces.append(frame["data"])
                elif frame["type"] == "chat" and not frame["data"].get("partial"):
                    final = frame
                    break

        assert final["text"] == "Привет!"
        assert {"kind": "emotion", "name": "joy"} in faces


class TestUnityAdapter:
    """``/ws/unity`` — алиас продюсера (A6.1-б): кадр этапа 5 + hello этапа 6 + зеркало."""

    def test_hello_and_mirror(self, fv_client: TestClient) -> None:
        with fv_client.websocket_connect("/ws/unity") as unity:
            hello = unity.receive_json()
            assert hello["adapter"] == "unity-vrm-salsa"
            assert hello["status"] == "reserved"
            assert hello["alias_of"] == "/ws/face/producer"

            # вторым кадром приходит рукопожатие продюсера (этап 6)
            producer_hello = unity.receive_json()
            assert producer_hello["type"] == "hello"
            assert producer_hello["producer"] == "lilith-face"
            assert producer_hello["format"] == "pcm_s16le"

            with fv_client.websocket_connect("/ws") as panel:
                panel.receive_json()
                panel.receive_json()
                panel.send_json({"type": "voice", "text": "Раз.", "data": {}})
                # unity получает и кадры старой шины (type=face), и плоские кадры продюсера
                legacy_kinds: set[str] = set()
                producer_types: set[str] = set()
                while "done" not in legacy_kinds or "done" not in producer_types:
                    frame = unity.receive_json()
                    if frame["type"] == "face":
                        legacy_kinds.add(frame["kind"])
                    else:
                        producer_types.add(frame["type"])
            assert "audio" in legacy_kinds and "viseme" in legacy_kinds
            assert "audio" in producer_types and "done" in producer_types
