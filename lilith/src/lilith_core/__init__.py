"""LILITH-CORE — домашний Python-стек ИИ-компаньона.

Модули (по этапам дорожной карты):

* ``config``   — загрузка настроек (YAML + .env + переменные окружения).
* ``logging_setup`` — loguru + перехват стандартного logging.
* ``persona``  — системный промт из ``persona.md``.
* ``protocol`` — схема сообщений WebSocket.
* ``session``  — менеджер подключений.
* ``app``      — FastAPI-оркестратор и веб-панель.

Этап 6: лицо = Unity-клиент (продюсер ``/ws/face/producer``, реестр персон v2,\nLoRA-слот prompt-only, групповые сцены).
"""

from __future__ import annotations

__version__ = "0.6.3"
__stage__ = 6
__app_name__ = "LILITH-CORE"
__codename__ = "LILITH.EXE"

__all__ = [
    "__version__",
    "__stage__",
    "__app_name__",
    "__codename__",
]
