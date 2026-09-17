"""Мост лица (этап 5): WebSocket-клиент к VTuber Studio с реконнектом.

Протокол: VTuber Studio WebSocket API (JSON): рукопожатие ``AuthenticationRequest``
→ ``AuthenticationResponse``, далее эмоции шляются триггерами хоткеев
(``HotkeyTriggerRequest``), идентификаторы хоткеев на каждую эмоцию лежат в конфиге
(``face.hotkeys``). Аватар и его хоткеи живьём в VTuber Studio; мы лишь нажимаем
на эмоциональные кнопки.

Резервный слот (объявлен, не реализован): VMC/OSC-отправитель бленд-шейпов напрямую
в VirtualMotionCapture-совместимые приёмники (``VmcBridge``).

Реконнект: экспоненциальный backoff от ``face.reconnect_delay_sec`` с потолком 30 с;
мост никогда не роняет ядро: нет студии — эмоции пишутся в журнал состояний.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any

import websockets
from loguru import logger

__all__ = ["FaceBridge", "VTuberStudioBridge", "MockFaceBridge", "NullFaceBridge", "VmcBridge"]


@dataclass
class FaceBridge:
    """Контракт моста лица: подключить, эмоций наложить, отключить."""

    name: str = "base"
    connected: bool = False
    last_error: str | None = None
    applied: list[str] = field(default_factory=list)

    def available(self) -> tuple[bool, str]:
        return False, "не реализован"

    async def start(self) -> None:
        """Поднять соединение (и цикл реконнектов)."""

    async def stop(self) -> None:
        """Остановить соединение."""

    async def set_emotion(self, emotion: str, intensity: float = 1.0) -> bool:
        """Наложить эмоцию. True, если аватар её получил."""
        return False

    def state(self) -> dict[str, Any]:
        """Сводка для /api/face/state и панели."""
        return {
            "bridge": self.name,
            "connected": self.connected,
            "last_error": self.last_error,
            "applied_count": len(self.applied),
            "last_emotions": self.applied[-8:],
        }


@dataclass
class NullFaceBridge(FaceBridge):
    """Мост-пустышка: лицо выключено, эмоции копятся в журнале состояний."""
    name: str = "null"


    def available(self) -> tuple[bool, str]:
        return True, ""

    async def set_emotion(self, emotion: str, intensity: float = 1.0) -> bool:
        self.applied.append(emotion)
        return False


@dataclass
class MockFaceBridge(FaceBridge):
    """Mock для тестов/песочницы: записи эмоций, управляемые обрывы связи."""
    name: str = "mock"

    fail_connect: bool = False

    def available(self) -> tuple[bool, str]:
        return True, ""

    async def start(self) -> None:
        if self.fail_connect:
            self.connected = False
            self.last_error = "mock: соединение запрещено"
            return
        self.connected = True

    async def stop(self) -> None:
        self.connected = False

    async def set_emotion(self, emotion: str, intensity: float = 1.0) -> bool:
        self.applied.append(emotion)
        return self.connected


@dataclass
class VmcBridge(FaceBridge):
    """Резервный слот: VMC/OSC бленд-шейпы напрямую (без VTuber Studio)."""
    name: str = "vmc"


    def available(self) -> tuple[bool, str]:
        return False, "резервный мост: VMC/OSC-отправитель запланирован, когда понадобится"


class VTuberStudioBridge(FaceBridge):
    """WebSocket-клиент VTuber Studio с аутентификацией и вечным реконнектом."""
    name: str = "vtuber-studio"


    def __init__(
        self,
        url: str = "ws://localhost:8001",
        plugin: str = "LILITH-CORE",
        developer: str = "Kiryusha",
        token_env: str = "LILITH_VTS_TOKEN",
        hotkeys: dict[str, str] | None = None,
        reconnect_delay_sec: float = 3.0,
        emotion_default: str = "neutral",
    ) -> None:
        super().__init__(name="vtuber-studio")
        self.url = url
        self.plugin = plugin
        self.developer = developer
        self.token_env = token_env
        self.hotkeys = dict(hotkeys or {})
        self.reconnect_delay_sec = reconnect_delay_sec
        self.emotion_default = emotion_default
        self._ws: Any = None
        self._task: asyncio.Task | None = None
        self._stopping = False

    # -- жизненный цикл ------------------------------------------------------ #
    def available(self) -> tuple[bool, str]:
        return True, ""

    def _token(self) -> str:
        return os.getenv(self.token_env, "") or ""

    async def start(self) -> None:
        """Запускает фоновый цикл соединения с реконнектами."""
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name="face-bridge")
        logger.info("Мост лица стартует: {}", self.url)

    async def stop(self) -> None:
        """Останавливает цикл и закрывает сокет."""
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        await self._close_ws()
        self.connected = False
        logger.info("Мост лица остановлен")

    async def _close_ws(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None

    async def _loop(self) -> None:
        """Вечный цикл: подключиться → ауф → держать; упало → backoff → повтор."""
        delay = self.reconnect_delay_sec
        while not self._stopping:
            try:
                async with websockets.connect(self.url, open_timeout=5, close_timeout=2) as ws:
                    self._ws = ws
                    await self._authenticate(ws)
                    self.connected = True
                    self.last_error = None
                    delay = self.reconnect_delay_sec
                    logger.info("Мост лица подключён: {}", self.url)
                    async for _message in ws:  # держим соединение и читаем ответы
                        continue
                    raise ConnectionError("VTuber Studio закрыла соединение")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - любая сетевая смерть = реконнект
                self.connected = False
                self.last_error = f"{exc.__class__.__name__}: {exc}"
                await self._close_ws()
                if self._stopping:
                    return
                logger.warning("Мост лица: {} — реконнект через {:.1f} c", self.last_error, delay)
                await asyncio.sleep(delay)
                delay = min(30.0, delay * 1.7)

    async def _authenticate(self, ws: Any) -> None:
        """Рукопожатие VTuber Studio API."""
        request = {
            "APIName": "AuthenticationRequest",
            "Data": {
                "DeveloperName": self.developer,
                "PluginName": self.plugin,
                "AuthenticationToken": self._token(),
            },
        }
        await ws.send(json.dumps(request))
        raw = await asyncio.wait_for(ws.recv(), timeout=5)
        data = json.loads(raw)
        if data.get("APIName") != "AuthenticationResponse":
            raise ConnectionError(f"ожидал AuthenticationResponse, получил {data.get('APIName')}")
        if not (data.get("Data") or {}).get("Authenticated", False):
            raise ConnectionError("VTuber Studio не аутентифицировала плагин (проверь токен/разрешения)")

    # -- эмоции ---------------------------------------------------------------- #
    async def set_emotion(self, emotion: str, intensity: float = 1.0) -> bool:
        """Триггерит хоткей эмоции в VTuber Studio."""
        self.applied.append(emotion)
        if not self.connected or self._ws is None:
            self.last_error = "мост не подключён"
            return False
        hotkey = self.hotkeys.get(emotion) or self.hotkeys.get(self.emotion_default)
        if not hotkey:
            logger.debug("Лицо: для эмоции '{}' нет хоткея в конфиге — пропускаю", emotion)
            return False
        payload = {"APIName": "HotkeyTriggerRequest", "Data": {"HotkeyID": hotkey}}
        try:
            await self._ws.send(json.dumps(payload))
            return True
        except Exception as exc:  # noqa: BLE001
            self.connected = False
            self.last_error = f"{exc.__class__.__name__}: {exc}"
            return False
