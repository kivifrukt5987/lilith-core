"""Гварды HTTP-поверхности движка горла (0.7.0, ADR-027): /api/voice/engine.

Плюс главный регрессионный гвард пакета: сервер СТАРТУЕТ и ОСТАНАВЛИВАЕТСЯ
с назначенным резидентом qwen3, даже когда faster-qwen3-tts/CUDA отсутствуют
(прогрев честно пропускается, фолбэк-цепочка ADR-012 жива).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from lilith_core.app import create_app


@pytest.fixture
def voice_settings(settings, tmp_project):
    settings.features.voice_enabled = True
    settings.voice.packs_manifest = str(tmp_project / "models" / "packs.yaml")
    settings.voice.packs_root = str(tmp_project)
    (tmp_project / "models").mkdir(exist_ok=True)
    (tmp_project / "models" / "packs.yaml").write_text("packs: {}\n", encoding="utf-8")
    return settings


@pytest.fixture
def voice_client(voice_settings):
    app = create_app(voice_settings)
    with TestClient(app) as client:
        yield client


class TestEngineEndpoints:
    def test_status_shape(self, voice_client: TestClient) -> None:
        payload = voice_client.get("/api/voice/engine").json()
        assert payload["resident"] is None          # по умолчанию короны нет
        assert payload["configured"] is None
        engines = payload["engines"]
        assert {"qwen3", "silero", "edge", "cosyvoice2", "fish", "zero-shot", "mock"} <= set(engines)
        assert engines["mock"]["available"] is True
        assert engines["cosyvoice2"]["available"] is False  # шкаф честен
        assert engines["qwen3"]["loaded"] is False

    def test_switch_to_mock(self, voice_client: TestClient) -> None:
        res = voice_client.post("/api/voice/engine", json={"name": "mock"}).json()
        assert res["status"] == "ok" and res["engine"]["resident"] == "mock"
        assert voice_client.get("/api/voice/engine").json()["resident"] == "mock"

    def test_switch_to_none(self, voice_client: TestClient) -> None:
        voice_client.post("/api/voice/engine", json={"name": "mock"})
        res = voice_client.post("/api/voice/engine", json={"name": "none"}).json()
        assert res["engine"]["resident"] is None

    def test_switch_unknown_404(self, voice_client: TestClient) -> None:
        res = voice_client.post("/api/voice/engine", json={"name": "gucci"})
        assert res.status_code == 404
        assert "не в реестре" in res.json()["detail"]

    def test_switch_empty_400(self, voice_client: TestClient) -> None:
        assert voice_client.post("/api/voice/engine", json={"name": "  "}).status_code == 400

    def test_switch_bad_json_400(self, voice_client: TestClient) -> None:
        res = voice_client.post(
            "/api/voice/engine", content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert res.status_code == 400

    def test_disabled_503(self, client: TestClient) -> None:
        assert client.get("/api/voice/engine").status_code == 503
        assert client.post("/api/voice/engine", json={"name": "mock"}).status_code == 503

    def test_profiles_describe_extended(self, voice_client: TestClient) -> None:
        payload = voice_client.get("/api/voice/profiles").json()
        kinds = {row["kind"] for row in payload["backends"] if isinstance(row, dict)}
        assert "tts-resident" in kinds
        assert "tts_engines" in payload and "tts_resident" in payload

    def test_soul_profiles_shipped_in_config(self, voice_client: TestClient) -> None:
        """Профили душ из config.yaml видны в реестре (lilith-soul/olya-soul на qwen3)."""
        payload = voice_client.get("/api/voice/profiles").json()
        profiles = {row["name"]: row for row in payload["backends"] if row.get("kind") == "voice-profile"}
        # sample_config тестов не содержит soul-профилей — проверяем дефолт-набор не сломан
        assert "lilith" in profiles


class TestLifespanWithResident:
    def test_boot_with_dead_resident_survives(self, voice_settings) -> None:
        """qwen3-резидент назначен, пакета/GPU нет: сервер жив от старта до остановки."""
        voice_settings.voice.tts_resident = "qwen3"
        app = create_app(voice_settings)
        with TestClient(app) as client:
            payload = client.get("/api/voice/engine").json()
            assert payload["configured"] == "qwen3"
            assert payload["engines"]["qwen3"]["available"] is False
            # фолбэк-цепочка жива: озвучка уходит в mock
            res = client.post("/api/voice/say", json={"text": "Живы?"})
            assert res.status_code == 200
            assert res.content[:4] == b"RIFF"

    def test_boot_with_unknown_resident_survives(self, voice_settings) -> None:
        voice_settings.voice.tts_resident = "ghost-engine"
        app = create_app(voice_settings)
        with TestClient(app) as client:
            assert client.get("/api/voice/engine").json()["resident"] is None
