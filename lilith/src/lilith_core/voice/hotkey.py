"""Push-to-talk хоткей (этап 4): запись речи по удержанию комбинации.

pynput импортируется лениво: в песочнице/без GUI слой вежливо недоступен,
а :class:`MockPushToTalk` даёт те же кнопки тестам иfuture-панели.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from loguru import logger

__all__ = ["PushToTalk", "MockPushToTalk", "parse_combo"]


def parse_combo(combo: str) -> list[str]:
    """'ctrl+space' -> ['ctrl', 'space'] (для pynput-маппинга и панели)."""
    return [part.strip().lower() for part in combo.split("+") if part.strip()]


@dataclass
class PushToTalk:
    """Глобальный хоткей записи: удержал — пишешь, отпустил — реплика ушла в STT."""

    combo: str = "ctrl+space"
    on_start: Callable[[], Any] | None = None
    on_stop: Callable[[], Any] | None = None
    _listener: Any = field(default=None, repr=False)

    def available(self) -> tuple[bool, str]:
        """Доступен ли глобальный захват клавиш (нужен GUI/pynput)."""
        import importlib.util

        if importlib.util.find_spec("pynput") is None:
            return False, "pynput не установлен (pip install -e .[voice])"
        return True, ""

    def start(self) -> None:
        """Вешает глобальный слушатель комбинации."""
        ok, reason = self.available()
        if not ok:
            raise RuntimeError(reason)
        from pynput import keyboard

        pressed: set[str] = set()
        wanted = set(parse_combo(self.combo))

        def _name(key: Any) -> str:
            try:
                return key.char.lower()
            except AttributeError:
                return key.name.lower()

        def on_press(key: Any) -> None:
            pressed.add(_name(key))
            if wanted <= pressed and self._listener is not None:
                if self.on_start:
                    self.on_start()

        def on_release(key: Any) -> None:
            pressed.discard(_name(key))
            if not (wanted <= pressed) and self.on_stop:
                self.on_stop()

        self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self._listener.start()
        logger.info("Push-to-talk включён: {}", self.combo)

    def stop(self) -> None:
        """Снимает слушатель."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
            logger.info("Push-to-talk выключен")


@dataclass
class MockPushToTalk:
    """Те же кнопки, но из кода: для тестов и песочницы без GUI."""

    combo: str = "ctrl+space"
    on_start: Callable[[], Any] | None = None
    on_stop: Callable[[], Any] | None = None
    recording: bool = False

    def available(self) -> tuple[bool, str]:
        return True, ""

    def start(self) -> None:
        """«Вешает слушатель» (ничего не делает)."""

    def stop(self) -> None:
        """Снимает слушатель и сбрасывает запись."""
        self.recording = False

    def press(self) -> None:
        """Имитировать нажатие комбинации."""
        self.recording = True
        if self.on_start:
            self.on_start()

    def release(self) -> None:
        """Имитировать отпускание."""
        self.recording = False
        if self.on_stop:
            self.on_stop()
