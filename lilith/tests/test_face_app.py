"""Интеграция лица с шиной: теги вырезаются, эмоции уходят в мост и в данные."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from lilith_core.app import create_app
from lilith_core.face import MockFaceBridge
from lilith_core.protocol import chat_message


@pytest.fixture
def face_settings(settings):
    settings.features.face_enabled = True
    settings.features.brain_enabled = True
    settings.brain.defaults.provider = "mock"
    return settings


@pytest.fixture
def face_app(face_settings):
    app = create_app(face_settings)
    app.state.face.bridge = MockFaceBridge()  # детерминированный мост вместо сети
    with TestClient(app) as client:
        yield client


def handshake(ws) -> None:
    ws.receive_json()
    ws.receive_json()


def final_frame(ws):
    while True:
        frame = ws.receive_json()
        if frame["type"] in ("error", "system") or (
            frame["type"] == "chat" and not frame["data"].get("partial")
        ):
            return frame


class TestTagsInBus:
    """Реплика с тегами: пользователь видит чистый текст, мост получает эмоции."""

    def test_tags_stripped_and_events_recorded(self, face_app) -> None:
        app = face_app.app

        async def tagged(text, context):
            return chat_message("[emotion: joy] Привет! [emotion: smug] Хи-хи, попался.")

        app.state.reply_handler = tagged

        with face_app.websocket_connect("/ws") as ws:
            handshake(ws)
            ws.send_json({"type": "chat", "text": "ау"})
            frame = final_frame(ws)

        assert frame["text"] == "Привет! Хи-хи, попался."
        assert frame["data"]["emotions"] == ["joy", "smug"]
        bridge = app.state.face.bridge
        assert bridge.applied[-2:] == ["joy", "smug"]

    def test_keyword_fallback_when_no_tags(self, face_app) -> None:
        app = face_app.app

        async def plain(text, context):
            return chat_message("кхххх, спички уже приготовлены")

        app.state.reply_handler = plain

        with face_app.websocket_connect("/ws") as ws:
            handshake(ws)
            ws.send_json({"type": "chat", "text": "ау"})
            frame = final_frame(ws)

        assert frame["data"]["emotions"] == ["evil"]
        assert "кхххх" in frame["text"]

    def test_no_emotions_no_problem(self, face_app) -> None:
        with face_app.websocket_connect("/ws") as ws:
            handshake(ws)
            ws.send_json({"type": "chat", "text": "привет"})
            frame = final_frame(ws)
        assert frame["data"]["emotions"] == []


class TestFaceEndpoints:
    """HTTP-поверхность лица."""

    def test_state_shape(self, face_app) -> None:
        payload = face_app.get("/api/face/state").json()
        assert payload["enabled"] is True
        assert payload["bridge"]["bridge"] == "mock"
        assert payload["keyword_fallback"] is True
        assert "joy" in payload["emotions"]["emotions"]

    def test_manual_emotion(self, face_app) -> None:
        response = face_app.post("/api/face/emotion", json={"name": "joy"})
        assert response.status_code == 200
        assert response.json()["delivered"] is True
        state = face_app.get("/api/face/state").json()
        assert state["bridge"]["last_emotions"][-1] == "joy"

    def test_disabled_is_503(self, settings) -> None:
        app = create_app(settings)
        with TestClient(app) as client:
            assert client.get("/api/face/state").status_code == 503
            assert client.post("/api/face/emotion", json={"name": "joy"}).status_code == 503
