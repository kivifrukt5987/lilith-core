"""LILITH-CORE — домашний Python-стек ИИ-компаньона.

Модули (по этапам дорожной карты):

* ``config``   — загрузка настроек (YAML + .env + переменные окружения).
* ``logging_setup`` — loguru + перехват стандартного logging.
* ``persona``  — системный промт из ``persona.md``.
* ``protocol`` — схема сообщений WebSocket.
* ``session``  — менеджер подключений.
* ``app``      — FastAPI-оркестратор и веб-панель.

Этап 1: скелет (конфиг, логи, HTTP + WebSocket-эхо, мини веб-панель).
"""

from __future__ import annotations

__version__ = "0.5.1"
__stage__ = 5
__app_name__ = "LILITH-CORE"
__codename__ = "LILITH.EXE"

__all__ = [
    "__version__",
    "__stage__",
    "__app_name__",
    "__codename__",
]
