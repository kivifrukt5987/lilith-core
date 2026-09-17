"""Тесты моста лица: фейковый VTuber Studio сервер, аутентификация, реконнект."""

from __future__ import annotations

import asyncio
import json

import pytest

try:
    from websockets.asyncio.server import serve as ws_serve
except ImportError:  # старые websockets
    from websockets import serve as ws_serve  # type: ignore[attr-defined]

from lilith_core.face import MockFaceBridge, NullFaceBridge, VmcBridge, VTuberStudioBridge

AUTH_RESPONSE = json.dumps(
    {"APIName": "AuthenticationResponse", "Data": {"Authenticated": True}}
)


class FakeVTS:
    """Мини-VTuber Studio: аутентифицирует и складывает принятые запросы."""

    def __init__(self, authenticated: bool = True) -> None:
        self.authenticated = authenticated
        self.received: list[dict] = []
        self.connections = 0
        self._server = None
        self.port = 0

    async def _handler(self, ws) -> None:
        self.connections += 1
        async for raw in ws:
            message = json.loads(raw)
            self.received.append(message)
            if message.get("APIName") == "AuthenticationRequest":
                payload = AUTH_RESPONSE if self.authenticated else json.dumps(
                    {"APIName": "AuthenticationResponse", "Data": {"Authenticated": False}}
                )
                await ws.send(payload)

    async def start(self) -> None:
        self._server = await ws_serve(self._handler, "127.0.0.1", self.port or 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None


@pytest.fixture
async def fake_vts():
    server = FakeVTS()
    await server.start()
    yield server
    await server.stop()


def make_bridge(fake: FakeVTS, **overrides) -> VTuberStudioBridge:
    base = dict(
        url=f"ws://127.0.0.1:{fake.port}",
        hotkeys={"joy": "hotkey-joy", "smug": "hotkey-smug"},
        reconnect_delay_sec=0.2,
    )
    base.update(overrides)
    return VTuberStudioBridge(**base)


@pytest.mark.asyncio
class TestVTuberStudioBridge:
    """Подключение, аутентификация, хоткеи, реконнект."""

    async def test_connect_auth_and_hotkey(self, fake_vts: FakeVTS) -> None:
        bridge = make_bridge(fake_vts)
        await bridge.start()
        try:
            for _ in range(50):
                if bridge.connected:
                    break
                await asyncio.sleep(0.05)
            assert bridge.connected is True

            assert await bridge.set_emotion("joy") is True
            await asyncio.sleep(0.1)

            auth = fake_vts.received[0]
            assert auth["APIName"] == "AuthenticationRequest"
            assert auth["Data"]["PluginName"] == "LILITH-CORE"
            triggers = [m for m in fake_vts.received if m["APIName"] == "HotkeyTriggerRequest"]
            assert triggers and triggers[0]["Data"]["HotkeyID"] == "hotkey-joy"
        finally:
            await bridge.stop()

    async def test_unmapped_emotion_skipped(self, fake_vts: FakeVTS) -> None:
        bridge = make_bridge(fake_vts)
        await bridge.start()
        try:
            for _ in range(50):
                if bridge.connected:
                    break
                await asyncio.sleep(0.05)
            assert await bridge.set_emotion("teleport") is False  # хоткея нет
            assert bridge.applied[-1] == "teleport"               # но в истории осталась
        finally:
            await bridge.stop()

    async def test_reconnect_after_server_death(self, fake_vts: FakeVTS) -> None:
        bridge = make_bridge(fake_vts)
        await bridge.start()
        try:
            for _ in range(50):
                if bridge.connected:
                    break
                await asyncio.sleep(0.05)
            assert bridge.connected

            await fake_vts.stop()  # студия умерла
            for _ in range(50):
                if not bridge.connected:
                    break
                await asyncio.sleep(0.05)
            assert not bridge.connected
            assert bridge.last_error

            await fake_vts.start()  # студия ожила
            for _ in range(100):
                if bridge.connected:
                    break
                await asyncio.sleep(0.05)
            assert bridge.connected is True  # мост вернулся сам
            assert fake_vts.connections >= 2
        finally:
            await bridge.stop()

    async def test_auth_failure_is_retried_not_fatal(self) -> None:
        bad = FakeVTS(authenticated=False)
        await bad.start()
        bridge = VTuberStudioBridge(
            url=f"ws://127.0.0.1:{bad.port}", reconnect_delay_sec=0.2
        )
        await bridge.start()
        try:
            await asyncio.sleep(0.5)
            assert bridge.connected is False
            assert "не аутентифицировала" in (bridge.last_error or "")
        finally:
            await bridge.stop()
            await bad.stop()

    async def test_set_emotion_without_connection(self) -> None:
        bridge = VTuberStudioBridge(url="ws://127.0.0.1:1", reconnect_delay_sec=0.2)
        assert await bridge.set_emotion("joy") is False
        assert bridge.last_error


@pytest.mark.asyncio
class TestOtherBridges:
    """Пустышка, mock и резервный слот."""

    async def test_null_bridge_records_but_not_delivers(self) -> None:
        bridge = NullFaceBridge()
        assert await bridge.set_emotion("joy") is False
        assert bridge.applied == ["joy"]
        assert bridge.state()["connected"] is False

    async def test_mock_bridge_delivers_when_connected(self) -> None:
        bridge = MockFaceBridge()
        await bridge.start()
        assert await bridge.set_emotion("smug") is True
        await bridge.stop()
        assert await bridge.set_emotion("smug") is False

    def test_vmc_reserve_unavailable(self) -> None:
        ok, reason = VmcBridge().available()
        assert ok is False
        assert "резерв" in reason

    def test_state_shape(self) -> None:
        state = NullFaceBridge().state()
        assert set(state) >= {"bridge", "connected", "last_error", "applied_count", "last_emotions"}
