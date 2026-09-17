"""Интеграция голоса с HTTP (этап 4): профили, транскрибация, озвучка, паки."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from lilith_core.app import create_app
from lilith_core.voice import write_wav


@pytest.fixture
def voice_settings(settings, tmp_project):
    """Настройки с включённым голосом и локальным манифестом паков."""
    settings.features.voice_enabled = True
    settings.voice.packs_manifest = str(tmp_project / "models" / "packs.yaml")
    settings.voice.packs_root = str(tmp_project)
    (tmp_project / "models").mkdir(exist_ok=True)
    (tmp_project / "models" / "packs.yaml").write_text(
        """
packs:
  demo-pack:
    type: stt
    source: local
    ref: blobs/demo.bin
    target_dir: models/stt
    note: "демо-пак"
""",
        encoding="utf-8",
    )
    (tmp_project / "blobs").mkdir(exist_ok=True)
    (tmp_project / "blobs" / "demo.bin").write_bytes(b"DEMO" * 64)
    return settings


@pytest.fixture
def voice_client(voice_settings):
    app = create_app(voice_settings)
    with TestClient(app) as client:
        yield client


def make_wav(ms: int = 150) -> bytes:
    """Крошечный моно-WAV с тоном."""
    import math
    import struct

    n = 16000 * ms // 1000
    pcm = struct.pack("<%dh" % n, *[int(9000 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(n)])
    return write_wav(pcm, 16000)


class TestVoiceEndpoints:
    """Поверхность /api/voice/*."""

    def test_profiles_describe(self, voice_client: TestClient) -> None:
        payload = voice_client.get("/api/voice/profiles").json()
        assert payload["stt_default"] in {"faster-whisper", "mock"}
        assert payload["tts_default_profile"]
        assert payload["vad"] in {"silero", "energy"}
        kinds = {row["kind"] for row in payload["backends"]}
        assert {"stt", "tts", "voice-profile"} <= kinds
        assert payload["packs"]

    def test_disabled_is_503(self, settings) -> None:
        app = create_app(settings)
        with TestClient(app) as client:
            assert client.get("/api/voice/profiles").status_code == 503
            assert client.post("/api/voice/say", json={"text": "привет"}).status_code == 503

    def test_transcribe_mock(self, voice_client: TestClient) -> None:
        response = voice_client.post("/api/voice/transcribe", content=make_wav())
        assert response.status_code == 200
        payload = response.json()
        assert payload["text"]  # mock-уши отвечают детерминированно
        assert payload["backend"] == "mock"
        assert payload["duration_s"] > 0

    def test_transcribe_bad_audio_400(self, voice_client: TestClient) -> None:
        response = voice_client.post("/api/voice/transcribe", content="не wav".encode("utf-8"))
        assert response.status_code == 400

    def test_say_returns_wav(self, voice_client: TestClient) -> None:
        response = voice_client.post("/api/voice/say", json={"text": "Привет, Кирюша. Мур-мур."})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("audio/")
        assert response.content[:4] == b"RIFF"  # mock отдаёт wav

    def test_say_unknown_profile_falls_back(self, voice_client: TestClient) -> None:
        """Неизвестный профиль голоса не ошибка: фолбэк на дефолт (hot-swap цепочка)."""
        response = voice_client.post("/api/voice/say", json={"text": "раз", "profile": "mocky"})
        assert response.status_code == 200
        assert response.content[:4] == b"RIFF"

    def test_say_empty_text_400(self, voice_client: TestClient) -> None:
        assert voice_client.post("/api/voice/say", json={"text": "  "}).status_code == 400


class TestPacksEndpoints:
    """Поверхность /api/packs/*."""

    def test_list(self, voice_client: TestClient) -> None:
        payload = voice_client.get("/api/packs").json()
        names = {row["name"] for row in payload["packs"]}
        assert names == {"demo-pack"}
        assert payload["packs"][0]["state"] == "missing"

    def test_install_local(self, voice_client: TestClient) -> None:
        response = voice_client.post("/api/packs/install", json={"name": "demo-pack"})
        assert response.status_code == 200
        assert response.json()["pack"]["state"] == "installed"

        again = voice_client.get("/api/packs").json()
        assert again["packs"][0]["state"] == "installed"

    def test_install_unknown_400(self, voice_client: TestClient) -> None:
        response = voice_client.post("/api/packs/install", json={"name": "ghost"})
        assert response.status_code == 400

    def test_remove(self, voice_client: TestClient) -> None:
        voice_client.post("/api/packs/install", json={"name": "demo-pack"})
        response = voice_client.post("/api/packs/remove", json={"name": "demo-pack"})
        assert response.status_code == 200
        payload = voice_client.get("/api/packs").json()
        assert payload["packs"][0]["state"] == "missing"


class TestPacksCli:
    """CLI: lilith-core packs list/install/remove."""

    def test_packs_cli_guard_clauses(self) -> None:
        from lilith_core.run import packs_main

        assert packs_main([]) == 2
        assert packs_main(["install"]) == 2
        assert packs_main(["remove"]) == 2
        assert packs_main(["teleport"]) == 2

    def test_packs_list_cli_reads_manifest(self, capsys) -> None:
        from lilith_core.run import packs_main

        assert packs_main(["list"]) == 0
        out = capsys.readouterr().out
        assert "whisper-small" in out
        assert "missing" in out
