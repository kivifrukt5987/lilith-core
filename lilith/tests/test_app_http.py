"""Тесты HTTP-слоя: веб-панель, healthz, версия, состояние."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from lilith_core import __stage__, __version__
from lilith_core.app import INDEX_HTML, create_app


class TestIndexPage:
    """Мини веб-панель."""

    def test_returns_html(self, client: TestClient) -> None:
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    def test_html_contains_expected_markup(self, client: TestClient) -> None:
        html = client.get("/").text
        assert "<!DOCTYPE html>" in html
        assert "LILITH" in html
        assert 'id="log"' in html
        assert 'id="input"' in html
        assert "WebSocket(" in html

    def test_html_has_no_external_resources(self, client: TestClient) -> None:
        """Панель должна работать офлайн: никаких CDN, шрифтов и чужих скриптов."""
        html = client.get("/").text
        for forbidden in ("https://cdn", "http://cdn", "googleapis", "unpkg.com", "jsdelivr"):
            assert forbidden not in html

    def test_index_file_exists_in_package(self) -> None:
        assert INDEX_HTML.is_file()
        assert INDEX_HTML.stat().st_size > 1000

    def test_panel_can_be_disabled(self, settings) -> None:
        settings.features.web_panel = False
        app = create_app(settings)
        with TestClient(app) as disabled_client:
            response = disabled_client.get("/")
        assert response.status_code == 503
        assert response.json()["status"] == "disabled"


class TestHealthz:
    """Диагностический эндпоинт."""

    def test_shape(self, client: TestClient) -> None:
        payload = client.get("/healthz").json()

        assert payload["status"] == "ok"
        assert payload["version"] == __version__
        assert payload["stage"] == __stage__
        assert payload["uptime_sec"] >= 0
        assert "echo_mode" in payload
        assert payload["ws"]["path"] == "/ws"

    def test_contains_features_and_sessions(self, client: TestClient) -> None:
        payload = client.get("/healthz").json()
        assert "echo_mode" in payload["features"]
        assert "web_panel" in payload["features"]
        assert payload["ws"]["active"] == 0
        assert payload["ws"]["total_connections"] == 0

    def test_contains_persona_info(self, client: TestClient) -> None:
        payload = client.get("/healthz").json()
        assert "persona" in payload
        assert set(payload["persona"]) >= {"path", "exists", "chars", "empty"}

    def test_brain_key_not_leaked(self, client: TestClient, monkeypatch) -> None:
        monkeypatch.setenv("LILITH_BRAIN__API_KEY", "sk-supersecret-9999")
        payload = client.get("/healthz").json()
        assert "supersecret" not in json.dumps(payload, ensure_ascii=False)

    def test_uptime_grows(self, client: TestClient) -> None:
        first = client.get("/healthz").json()["uptime_sec"]
        second = client.get("/healthz").json()["uptime_sec"]
        assert second >= first


class TestVersionEndpoint:
    """Метаданные сборки."""

    def test_version_payload(self, client: TestClient) -> None:
        payload = client.get("/api/version").json()
        assert payload["version"] == __version__
        assert payload["stage"] == __stage__
        assert payload["stage_name"] == "face-unity"   # этап 6: пивот на Unity-клиент
        assert len(payload["roadmap"]) == 10           # 9 этапов + запланированный 6.5
        assert payload["codename"] == "LILITH.EXE"


class TestStateEndpoint:
    """Состояние подключений."""

    def test_state_empty_at_start(self, client: TestClient) -> None:
        payload = client.get("/api/state").json()
        assert payload["active"] == 0
        assert payload["sessions"] == []

    def test_state_reflects_ws_connection(self, client: TestClient) -> None:
        with client.websocket_connect("/ws"):
            payload = client.get("/api/state").json()
            assert payload["active"] == 1
            assert payload["total_connections"] == 1

        payload_after = client.get("/api/state").json()
        assert payload_after["active"] == 0


class TestDocsAndErrors:
    """Swagger и обработка ошибок."""

    def test_docs_enabled_in_debug(self, client: TestClient) -> None:
        # sample_config выставляет app.debug = true
        assert client.get("/docs").status_code == 200

    def test_docs_disabled_in_prod(self, settings) -> None:
        settings.app.debug = False
        app = create_app(settings)
        with TestClient(app) as prod_client:
            assert prod_client.get("/docs").status_code == 404

    def test_openapi_schema(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()
        assert "/healthz" in schema["paths"]

    def test_unknown_route_is_404(self, client: TestClient) -> None:
        assert client.get("/api/nesuschestvuet").status_code == 404


class TestBrainProfilesEndpoint:
    """Реестр профилей мозга с healthcheck (этап 2)."""

    def test_mock_profiles_healthy(self, settings) -> None:
        settings.features.brain_enabled = True
        settings.brain.defaults.provider = "mock"
        app = create_app(settings)
        with TestClient(app) as client:
            payload = client.get("/api/brain/profiles").json()

        assert payload["default_profile"] == "chat"
        assert payload["brain_enabled"] is True
        names = [p["name"] for p in payload["profiles"]]
        assert names == ["chat", "coder"]          # из sample_config, по алфавиту
        chat = payload["profiles"][0]
        assert chat["health"]["ok"] is True
        assert chat["health"]["models"] == ["mock-chat"]
        assert chat["model"] == "test-model"

    def test_offline_server_reported_not_fatal(self, settings) -> None:
        settings.brain.defaults.provider = "openai_compatible"
        settings.brain.defaults.base_url = "http://127.0.0.1:1/v1"
        app = create_app(settings)
        with TestClient(app) as client:
            response = client.get("/api/brain/profiles")

        assert response.status_code == 200
        payload = response.json()
        for profile in payload["profiles"]:
            assert profile["health"]["ok"] is False
            assert profile["health"]["error"]

    def test_cached_second_call(self, settings) -> None:
        settings.brain.defaults.provider = "mock"
        app = create_app(settings)
        with TestClient(app) as client:
            first = client.get("/api/brain/profiles").json()
            second = client.get("/api/brain/profiles").json()
        assert first == second
        assert app.state.brain_profiles_cache["data"] is not None


class TestStatsAndPersonaReload:
    """Хотфикс 0.2.1: счётчик токенов и горячая перечитка персоны."""

    def test_stats_empty_at_start(self, client: TestClient) -> None:
        payload = client.get("/api/brain/stats").json()
        assert payload["total_requests"] == 0
        assert payload["total_tokens"] == 0
        assert payload["profiles"] == {}
        assert payload["uptime_sec"] >= 0

    def test_stats_accumulate_after_brain_replies(self, settings) -> None:
        settings.features.brain_enabled = True
        settings.brain.defaults.provider = "mock"
        app = create_app(settings)
        with TestClient(app) as client:
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()
                ws.receive_json()
                ws.send_json({"type": "chat", "text": "раз"})
                while True:
                    frame = ws.receive_json()
                    if frame["type"] == "chat" and not frame["data"].get("partial"):
                        break
                ws.send_json({"type": "chat", "text": "два"})
                while True:
                    frame = ws.receive_json()
                    if frame["type"] == "chat" and not frame["data"].get("partial"):
                        break

            payload = client.get("/api/brain/stats").json()

        assert payload["total_requests"] == 2
        assert payload["total_tokens"] > 0
        assert payload["total_completion_tokens"] > 0
        assert "chat" in payload["profiles"]
        assert payload["profiles"]["chat"]["requests"] == 2
        assert payload["estimated_tokens"] > 0  # mock честно помечает оценку

    def test_stats_count_brain_errors(self, settings) -> None:
        settings.features.brain_enabled = True
        settings.brain.defaults.provider = "mock"
        app = create_app(settings)
        app.state.mock_brain.fail_on = "умри"
        with TestClient(app) as client:
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()
                ws.receive_json()
                ws.send_json({"type": "chat", "text": "умри"})
                frame = ws.receive_json()
                assert frame["type"] == "error"
            payload = client.get("/api/brain/stats").json()
        assert payload["total_errors"] == 1

    def test_persona_reload_endpoint(self, settings, persona_file) -> None:
        settings.app.persona_path = str(persona_file)
        app = create_app(settings)
        with TestClient(app) as client:
            persona_file.write_text("Ты — ПЕРЕЧИТАННАЯ_ЛИЛИТ.", encoding="utf-8")
            response = client.post("/api/persona/reload")

            assert response.status_code == 200
            payload = response.json()
            assert payload["status"] == "ok"
            assert payload["chars"] > 0
            assert "ПЕРЕЧИТАННАЯ_ЛИЛИТ" in app.state.chat_cycle.system_prompt
            assert "ПЕРЕЧИТАННАЯ_ЛИЛИТ" in app.state.system_prompt
