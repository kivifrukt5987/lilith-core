"""FastAPI-оркестратор LILITH-CORE.

Этап 1: HTTP-каркас + WebSocket-эхо + мини веб-панель.

Эндпоинты:

* ``GET  /``            — веб-панель (``webui/index.html``).
* ``GET  /healthz``     — живой ли сервер, аптайм, статистика сессий, конфиг-сводка.
* ``GET  /api/version`` — версия и номер этапа.
* ``WS   /ws``          — шина сообщений (см. :mod:`lilith_core.protocol`).

Фабрика :func:`create_app` позволяет поднимать несколько независимых приложений
в тестах с разными конфигами.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from loguru import logger

from . import __app_name__, __codename__, __stage__, __version__
from .brain import BrainError, ChatCycle, LLMClient, MockBrain
from .brain.stats import TokenStats
from .memory import MemoryCore, apply_runtime_update, load_runtime_settings
from .face import (
    FaceBus,
    FaceCore,
    PersonaRegistry,
    extract_visemes,
    strip_emotion_tags,
)
from .voice import PackError, VoiceCore, read_wav
from pydantic import ValidationError
from .config import Settings, config_source_info, get_settings, resolve_path
from .echo import ReplyHandler, build_reply_handler
from .logging_setup import setup_logging
from .persona import build_system_prompt, load_persona
from .protocol import (
    Message,
    MsgType,
    build_message,
    chat_message,
    error_message,
    hello_message,
    parse_raw,
    pong_message,
    system_message,
)
from .session import Session, SessionManager

__all__ = ["create_app", "WEBUI_DIR", "INDEX_HTML"]

#: Каталог с веб-панелью (лежит внутри пакета, чтобы переживать установку).
WEBUI_DIR: Path = Path(__file__).resolve().parent / "webui"
INDEX_HTML: Path = WEBUI_DIR / "index.html"

#: Человекочитаемые имена этапов дорожной карты.
STAGE_NAMES: dict[int, str] = {
    1: "skeleton",
    2: "brain",
    3: "memory",
    4: "voice",
    5: "face",
    6: "hands",
    7: "bridges",
    8: "stream",
}


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Инициализация и завершение работы сервера."""
    settings: Settings = app.state.settings
    app.state.started_at = time.time()

    persona = load_persona(settings=settings)
    app.state.persona = persona
    app.state.system_prompt = build_system_prompt(settings=settings)

    # Реальный адрес может отличаться от конфига (флаги --host/--port в run.main),
    # поэтому берём его из app.state, а не из settings.
    host = getattr(app.state, "bound_host", settings.server.host)
    port = getattr(app.state, "bound_port", settings.server.port)

    logger.info(
        "{} v{} (этап {}) запущен: http://{}:{}",
        settings.app.name,
        __version__,
        __stage__,
        host,
        port,
    )
    logger.info("Конфиг: {}", config_source_info())
    logger.info("Включено: {}", ", ".join(settings.features.enabled()) or "ничего")
    logger.info("Персона: {}", persona.info())
    if persona.is_empty:
        logger.warning("persona.md пуст или не найден — на этапе 2 мозгу нечего будет читать")

    memory: MemoryCore | None = app.state.memory
    if memory is not None:
        await memory.start()
    face: FaceCore | None = app.state.face
    if face is not None:
        await face.start()

    yield

    if face is not None:
        await face.stop()
    if memory is not None:
        await memory.stop()
    logger.info("Останавливаюсь. Всего подключений: {}", app.state.sessions.total_connections)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Создаёт и настраивает приложение.

    :param settings: готовый конфиг; если ``None`` — берётся глобальный.
    """
    settings = settings or get_settings()
    setup_logging(settings, force=True)

    app = FastAPI(
        title=f"{settings.app.name} — {__codename__}",
        version=__version__,
        description=(
            "Домашний Python-стек ИИ-компаньона: оркестратор, память, уши, горло, "
            "лицо, руки, мосты. Этап 1 — скелет."
        ),
        debug=settings.app.debug,
        lifespan=_lifespan,
        docs_url="/docs" if settings.app.debug else None,
        redoc_url=None,
    )

    app.state.settings = settings
    app.state.sessions = SessionManager()
    load_runtime_settings(settings)  # рантайм-правки памяти поверх YAML
    app.state.llm_client = LLMClient()
    app.state.mock_brain = MockBrain()
    app.state.memory = (
        MemoryCore(settings, llm=app.state.llm_client, mock=app.state.mock_brain)
        if settings.features.memory_enabled
        else None
    )
    app.state.voice = VoiceCore(settings) if settings.features.voice_enabled else None
    app.state.face = FaceCore(settings) if settings.features.face_enabled else None
    app.state.face_bus = FaceBus()
    app.state.personas = PersonaRegistry(resolve_path(settings.app.personas_dir))
    app.state.chat_cycle = ChatCycle(
        settings, llm=app.state.llm_client, mock=app.state.mock_brain
    )
    app.state.reply_handler = build_reply_handler(settings, cycle=app.state.chat_cycle)
    app.state.brain_profiles_cache = {"at": 0.0, "data": None}
    app.state.token_stats = TokenStats()
    app.state.started_at = time.time()
    app.state.bound_host = settings.server.host
    app.state.bound_port = settings.server.port
    app.state.persona = None
    app.state.system_prompt = ""

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.server.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    personas_dir = resolve_path(settings.app.personas_dir)
    personas_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/personas", StaticFiles(directory=str(personas_dir)), name="personas")
    app.mount("/ui", StaticFiles(directory=str(WEBUI_DIR)), name="ui")

    _register_routes(app)
    _register_error_handlers(app)
    return app


# --------------------------------------------------------------------------- #
#  HTTP
# --------------------------------------------------------------------------- #
def _register_routes(app: FastAPI) -> None:
    """Регистрирует HTTP- и WS-маршруты."""

    @app.get("/", include_in_schema=False)
    async def index() -> Any:
        """Веб-панель."""
        if not app.state.settings.features.web_panel:
            return JSONResponse(
                {"status": "disabled", "detail": "web_panel отключён в конфиге"},
                status_code=503,
            )
        if not INDEX_HTML.is_file():
            return JSONResponse(
                {"status": "error", "detail": f"webui/index.html не найден: {INDEX_HTML}"},
                status_code=500,
            )
        return FileResponse(INDEX_HTML, media_type="text/html; charset=utf-8")

    @app.get("/healthz", tags=["service"])
    async def healthz() -> dict[str, Any]:
        """Проверка живости + сводка состояния."""
        settings: Settings = app.state.settings
        started = getattr(app.state, "started_at", time.time())
        persona = getattr(app.state, "persona", None)
        return {
            "status": "ok",
            "app": settings.app.name,
            "codename": __codename__,
            "version": __version__,
            "stage": __stage__,
            "uptime_sec": round(time.time() - started, 2),
            "env": settings.app.env,
            "debug": settings.app.debug,
            "features": settings.features.enabled(),
            "echo_mode": settings.features.echo_mode,
            "brain": settings.brain.masked(),
            "persona": persona.info() if persona else {"loaded": False},
            "memory": (
                {"enabled": False}
                if app.state.memory is None
                else {
                    "enabled": True,
                    "summarize_every_n": settings.memory.summarize_every_n,
                    "auto_summarize": settings.memory.auto_summarize,
                }
            ),
            "ws": {"path": settings.server.ws_path, **app.state.sessions.stats()},
            "config": config_source_info(),
        }

    @app.get("/api/version", tags=["service"])
    async def version() -> dict[str, Any]:
        """Версия сборки."""
        return {
            "app": __app_name__,
            "codename": __codename__,
            "version": __version__,
            "stage": __stage__,
            "stage_name": STAGE_NAMES.get(__stage__, "unknown"),
            "roadmap": [
                "1. skeleton (config, loguru, FastAPI, WS echo, web panel)",
                "2. brain (OpenAI-compatible LLM client)",
                "3. memory (aiosqlite journal + chromadb RAG + summarizer)",
                "4. voice (faster-whisper STT + silero VAD + silero/edge TTS)",
                "5. face (emotion tags + VTuber Studio / VMC bridge)",
                "6. hands (tools registry + human-in-the-loop confirm banner)",
                "7. bridges (discord.py + vkbottle)",
                "8. stream & service (twitchio, OBS scenes, docker-compose, autostart)",
            ],
        }

    @app.get("/api/brain/profiles", tags=["brain"])
    async def brain_profiles() -> dict[str, Any]:
        """Реестр профилей мозга + здоровье каждого сервера (кэш 10 с)."""
        settings: Settings = app.state.settings
        cache: dict[str, Any] = app.state.brain_profiles_cache
        now = time.time()
        if cache["data"] is not None and now - cache["at"] < 10:
            return cache["data"]

        profiles: list[dict[str, Any]] = []
        for name in settings.brain.profile_names():
            resolved = settings.brain.resolve(name)
            client = (
                app.state.mock_brain
                if resolved.provider == "mock"
                else app.state.llm_client
            )
            health = await client.health(resolved, timeout=2.0)
            profiles.append(
                {
                    "name": name,
                    "note": resolved.note,
                    "provider": resolved.provider,
                    "model": resolved.model,
                    "base_url": resolved.base_url,
                    "health": health,
                }
            )
        data = {
            "default_profile": settings.brain.default_profile,
            "brain_enabled": settings.features.brain_enabled,
            "profiles": profiles,
        }
        cache.update(at=now, data=data)
        return data

    @app.get("/api/brain/stats", tags=["brain"])
    async def brain_stats() -> dict[str, Any]:
        """Накопительная статистика токенов/запросов по профилям (счётчик панели)."""
        return app.state.token_stats.snapshot()

    @app.post("/api/persona/reload", tags=["brain"])
    async def persona_reload() -> dict[str, Any]:
        """Принудительная горячая перечитка persona.md без перезапуска сервера."""
        cycle: ChatCycle = app.state.chat_cycle
        prompt = cycle.reload_system_prompt()
        app.state.system_prompt = prompt
        return {
            "status": "ok",
            "chars": len(prompt),
            "path": str(app.state.settings.persona_file),
        }

    @app.get("/api/memory/settings", tags=["memory"])
    async def memory_settings_get() -> dict[str, Any]:
        """Текущие настраиваемые из программы поля памяти."""
        from .memory import RUNTIME_FIELDS

        settings: Settings = app.state.settings
        return {
            "fields": {key: getattr(settings.memory, key) for key in RUNTIME_FIELDS},
            "editable": list(RUNTIME_FIELDS),
            "enabled": settings.features.memory_enabled,
        }

    @app.post("/api/memory/settings", tags=["memory"])
    async def memory_settings_post(request: Request) -> Any:
        """Меняет настройки памяти на лету и сохраняет их до перезапуска."""
        try:
            patch = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(status_code=400, content={"status": "error", "detail": "нужен JSON-объект"})
        if not isinstance(patch, dict):
            return JSONResponse(status_code=400, content={"status": "error", "detail": "нужен JSON-объект"})
        try:
            fields = apply_runtime_update(app.state.settings, patch)
        except ValidationError as exc:  # раньше ValueError: она его подкласс
            return JSONResponse(status_code=422, content={"status": "error", "detail": str(exc.errors()[:2])})
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"status": "error", "detail": str(exc)})
        return {"status": "ok", "fields": fields}

    @app.get("/api/memory/state", tags=["memory"])
    async def memory_state() -> dict[str, Any]:
        """Сводка памяти: журнал, RAG, саммаризатор, рантайм-поля."""
        memory: MemoryCore | None = app.state.memory
        if memory is None:
            return {"enabled": False, "hint": "включи features.memory_enabled или флаг --with-memory"}
        return await memory.describe()

    @app.get("/api/voice/profiles", tags=["voice"])
    async def voice_profiles() -> Any:
        """Реестры ушей/горла/голосов/паков: доступность и текущие дефолты."""
        voice: VoiceCore | None = app.state.voice
        if voice is None:
            return JSONResponse(
                status_code=503,
                content={"status": "disabled", "detail": "включи features.voice_enabled или флаг --with-voice"},
            )
        return voice.describe()

    @app.post("/api/voice/transcribe", tags=["voice"])
    async def voice_transcribe(request: Request) -> Any:
        """Принимает моно-WAV байтами и возвращает распознанный текст."""
        voice: VoiceCore | None = app.state.voice
        if voice is None:
            return JSONResponse(status_code=503, content={"status": "disabled"})
        raw = await request.body()
        try:
            pcm, rate = read_wav(raw)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(status_code=400, content={"status": "error", "detail": f"нужен моно-WAV 16 бит: {exc}"})
        try:
            transcript = await voice.transcribe(pcm, rate)
        except RuntimeError as exc:
            return JSONResponse(status_code=503, content={"status": "error", "detail": str(exc)})
        return {
            "text": transcript.text,
            "language": transcript.language,
            "duration_s": round(transcript.duration_s, 2),
            "backend": transcript.backend,
        }

    @app.post("/api/voice/say", tags=["voice"])
    async def voice_say(request: Request) -> Any:
        """Озвучить текст профилем голоса: отдаёт WAV (или mp3 у edge)."""
        voice: VoiceCore | None = app.state.voice
        if voice is None:
            return JSONResponse(status_code=503, content={"status": "disabled"})
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse(status_code=400, content={"status": "error", "detail": "нужен JSON {text, profile?}"})
        text = str(payload.get("text") or "").strip()
        if not text:
            return JSONResponse(status_code=400, content={"status": "error", "detail": "пустой текст"})
        profile = payload.get("profile")
        try:
            audio = await voice.speak(text, profile)
        except RuntimeError as exc:
            return JSONResponse(status_code=503, content={"status": "error", "detail": str(exc)})
        return Response(content=audio, media_type="audio/wav")

    @app.get("/api/packs", tags=["voice"])
    async def packs_list() -> Any:
        """Статусы паков моделей (installed/partial/missing/broken)."""
        voice: VoiceCore | None = app.state.voice
        if voice is None:
            return JSONResponse(status_code=503, content={"status": "disabled"})
        return {"packs": voice.packs.list()}

    @app.post("/api/packs/install", tags=["voice"])
    async def packs_install(request: Request) -> Any:
        """Установить пак: докачка + sha256-проверка."""
        voice: VoiceCore | None = app.state.voice
        if voice is None:
            return JSONResponse(status_code=503, content={"status": "disabled"})
        try:
            payload = await request.json()
            name = str(payload.get("name") or "")
            status = await voice.packs.install(name)
        except PackError as exc:
            return JSONResponse(status_code=400, content={"status": "error", "detail": str(exc)})
        return {"status": "ok", "pack": status}

    @app.post("/api/packs/remove", tags=["voice"])
    async def packs_remove(request: Request) -> Any:
        """Удалить файлы пака."""
        voice: VoiceCore | None = app.state.voice
        if voice is None:
            return JSONResponse(status_code=503, content={"status": "disabled"})
        try:
            payload = await request.json()
            voice.packs.remove(str(payload.get("name") or ""))
        except PackError as exc:
            return JSONResponse(status_code=400, content={"status": "error", "detail": str(exc)})
        return {"status": "ok"}

    @app.get("/api/face/state", tags=["face"])
    async def face_state() -> Any:
        """Состояние моста лица, история эмоций и словари согласования."""
        face: FaceCore | None = app.state.face
        if face is None:
            return JSONResponse(
                status_code=503,
                content={"status": "disabled", "detail": "включи features.face_enabled"},
            )
        return face.describe()

    @app.post("/api/face/emotion", tags=["face"])
    async def face_emotion(request: Request) -> Any:
        """Ручная эмоция на аватар (для проверок и будущей панели)."""
        face: FaceCore | None = app.state.face
        if face is None:
            return JSONResponse(status_code=503, content={"status": "disabled"})
        try:
            payload = await request.json()
            name = str(payload.get("name") or "")
        except Exception:  # noqa: BLE001
            return JSONResponse(status_code=400, content={"status": "error", "detail": "нужен JSON {name}"})
        delivered = await face.bridge.set_emotion(name)
        return {"status": "ok", "delivered": delivered, "bridge": face.bridge.state()}

    @app.get("/api/personas", tags=["face"])
    async def personas_list() -> dict[str, Any]:
        """Реестр персон: души, тела и фолбэки (фундамент вкладок этапа 8)."""
        registry: PersonaRegistry = app.state.personas
        return {
            "default": app.state.settings.app.agent_id,
            "personas": registry.list(),
        }

    @app.get("/api/state", tags=["service"])
    async def state() -> dict[str, Any]:
        """Состояние подключений (пригодится панели на этапе 6)."""
        return app.state.sessions.stats()

    @app.websocket("/ws/unity")
    async def unity_adapter(websocket: WebSocket) -> None:
        """Зарезервированный адаптер Unity+VRM+SALSA (интерфейс этапа 5, реализация к 8).

        Зеркалит face-кадры из шины и принимает команды эмоций/визем от внешней сцены.
        """
        app = websocket.scope["app"] if "app" in websocket.scope else None
        await websocket.accept()
        bus: FaceBus = app.state.face_bus
        queue = bus.subscribe()
        await websocket.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "adapter": "unity-vrm-salsa",
                    "status": "reserved",
                    "note": "интерфейс зарезервирован этапом 5, реализация на этапе 8",
                },
                ensure_ascii=False,
            )
        )

        async def pump() -> None:
            while True:
                frame = await queue.get()
                await websocket.send_text(json.dumps({"type": "face", **frame}, ensure_ascii=False))

        pump_task = asyncio.create_task(pump())
        try:
            while True:
                raw = await websocket.receive_text()
                parsed = parse_raw(raw)
                if not parsed.ok or parsed.message is None:
                    continue
                if parsed.message.type is MsgType.FACE:
                    kind = (parsed.message.data or {}).get("kind")
                    if kind == "emotion":
                        face_core: FaceCore | None = app.state.face
                        if face_core is not None:
                            await face_core.bridge.set_emotion(str(parsed.message.data.get("name") or ""))
        except WebSocketDisconnect:
            pass
        finally:
            pump_task.cancel()
            bus.unsubscribe(queue)

    @app.websocket(app.state.settings.server.ws_path)
    async def websocket_endpoint(websocket: WebSocket) -> None:
        """Основная шина сообщений."""
        await handle_websocket(websocket, app)


def _register_error_handlers(app: FastAPI) -> None:
    """Единый формат ошибок — JSON, чтобы панель не ломалась на HTML-страницах."""

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:  # noqa: ARG001
        logger.exception("Необработанная ошибка на {}: {!r}", request.url.path, exc)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": "внутренняя ошибка сервера", "type": type(exc).__name__},
        )


async def stream_voice_to_session(
    app: FastAPI, session: Session, text: str, voice_profile: str | None
) -> None:
    """Озвучить текст: аудио-чанки + виземы кадрами FACE по сокету сессии и в шину."""
    import base64

    voice_core = app.state.voice
    bus: FaceBus = app.state.face_bus
    seq = 0
    async for chunk_wav in voice_core.stream(text, voice_profile):
        pcm, rate = read_wav(chunk_wav)
        audio_frame = {
            "kind": "audio",
            "audio_b64": base64.b64encode(chunk_wav).decode("ascii"),
            "seq": seq,
            "sample_rate": rate,
        }
        await app.state.sessions.send(session, build_message(MsgType.FACE, data=dict(audio_frame)))
        await bus.publish(audio_frame)
        for index, frame in enumerate(extract_visemes(pcm, rate)):
            viseme_frame = {
                "kind": "viseme",
                "viseme": frame.viseme,
                "intensity": frame.intensity,
                "seq": seq,
                "offset_ms": index * 60,
            }
            await app.state.sessions.send(session, build_message(MsgType.FACE, data=dict(viseme_frame)))
            await bus.publish(viseme_frame)
        seq += 1
    done_frame = {"kind": "done", "seq": seq}
    await app.state.sessions.send(session, build_message(MsgType.FACE, data=dict(done_frame)))
    await bus.publish(done_frame)


def build_chat_partial(chunk: str, stream_id: str, profile: str | None) -> Message:
    """Частичный кадр стриминга: панель дописывает пузырь по stream_id."""
    return Message(
        type=MsgType.CHAT,
        text=chunk,
        data={
            "role": "assistant",
            "partial": True,
            "stream_id": stream_id,
            "profile": profile,
        },
    )


# --------------------------------------------------------------------------- #
#  WebSocket
# --------------------------------------------------------------------------- #
async def handle_websocket(websocket: WebSocket, app: FastAPI) -> None:
    """Обслуживает одно WebSocket-подключение от приёма до закрытия.

    Вынесено из маршрута, чтобы его можно было тестировать напрямую.
    """
    settings: Settings = app.state.settings
    sessions: SessionManager = app.state.sessions
    reply_handler: ReplyHandler = app.state.reply_handler

    await websocket.accept()

    headers = dict(websocket.headers or {})
    client = websocket.client
    remote = f"{client.host}:{client.port}" if client else "unknown"
    session = await sessions.register(
        websocket,
        remote=remote,
        user_agent=headers.get("user-agent", ""),
    )

    try:
        await sessions.send(
            session,
            hello_message(
                name=settings.app.name,
                version=__version__,
                session_id=session.id,
                extra={
                    "stage": __stage__,
                    "mode": "brain" if settings.features.brain_enabled else "echo",
                    "ws_path": settings.server.ws_path,
                    "user_name": settings.app.user_name,
                    "persona_name": settings.app.persona_name,
                    "agent_id": settings.app.agent_id,
                    "default_profile": settings.brain.default_profile,
                },
            ),
        )
        await sessions.send(
            session,
            system_message(
                f"{settings.app.persona_name} на связи. Этап {__stage__}: мозг ещё не подключён, "
                "отвечаю эхом. 🦇",
                stage=__stage__,
            ),
        )

        while True:
            raw = await websocket.receive_text()
            if len(raw) > settings.server.ws_max_message_size:
                await sessions.send(
                    session,
                    error_message(
                        f"сообщение больше {settings.server.ws_max_message_size} байт",
                        code="message_too_large",
                    ),
                )
                continue

            sessions.note_incoming(session)
            parsed = parse_raw(raw)
            if not parsed.ok or parsed.message is None:
                logger.warning("WS[{}]: кривой пакет: {}", session.id, parsed.error)
                await sessions.send(session, error_message(parsed.error or "не удалось разобрать сообщение"))
                continue

            response = await dispatch(parsed.message, session=session, app=app, reply_handler=reply_handler)
            if response is not None:
                await sessions.send(session, response)
                summary = (response.data or {}).get("memory_summary")
                if summary:
                    await sessions.send(
                        session,
                        system_message(
                            f"📜 память: создано саммари последних "
                            f"{app.state.settings.memory.summarize_every_n} сообщений",
                            summary=str(summary)[:400],
                        ),
                    )

    except WebSocketDisconnect as exc:
        logger.info("WS[{}]: клиент отключился (code={})", session.id, exc.code)
    except asyncio.CancelledError:  # pragma: no cover - штатная отмена задачи
        logger.info("WS[{}]: задача отменена", session.id)
        raise
    except Exception as exc:  # noqa: BLE001 - сокет мог умереть по любой причине
        logger.exception("WS[{}]: ошибка соединения: {!r}", session.id, exc)
        try:
            await sessions.send(session, error_message(f"внутренняя ошибка: {exc!r}", code="internal"))
        except Exception:  # noqa: BLE001, S110 - уже некому отвечать
            pass
    finally:
        await sessions.unregister(session.id)


async def dispatch(
    message: Any,
    *,
    session: Session,
    app: FastAPI,
    reply_handler: ReplyHandler,
) -> Any | None:
    """Маршрутизирует входящее сообщение и возвращает ответ (или ``None``).

    Отдельная функция — на следующих этапах сюда добавятся ветки ``state``,
    ``tool_call``, ``confirm`` и прочие, не трогая сокет-цикл.
    """
    msg_type: MsgType = message.type

    if msg_type is MsgType.PING:
        return pong_message(ping_id=message.id)

    if msg_type is MsgType.HELLO:
        return system_message(
            "рукопожатие принято",
            client_id=message.data.get("client_id"),
            session_id=session.id,
        )

    if msg_type is MsgType.CHAT:
        text = (message.text or "").strip()
        if not text:
            return error_message("пустая реплика: поле 'text' не должно быть пустым", code="empty_text")

        wanted_profile = message.data.get("profile")
        settings: Settings = app.state.settings
        if wanted_profile and wanted_profile not in settings.brain.profiles:
            return error_message(
                f"неизвестный профиль мозга '{wanted_profile}' "
                f"(доступны: {', '.join(settings.brain.profile_names())})",
                code="unknown_profile",
            )

        stream_id = uuid.uuid4().hex[:10]
        sessions_ref: SessionManager = app.state.sessions

        async def push_partial(chunk: str) -> None:
            """Стриминг: частичный кадр chat с data.partial=true."""
            await sessions_ref.send(
                session,
                build_chat_partial(chunk, stream_id, wanted_profile),
            )

        agent_id = message.data.get("agent_id") or settings.app.agent_id
        memory: MemoryCore | None = app.state.memory
        memory_hits: list[str] = []
        if memory is not None:
            memory_hits = await memory.retrieve(agent_id, text)

        context = {
            "session_id": session.id,
            "source": message.data.get("source", "webui"),
            "message_id": message.id,
            "profile": wanted_profile,
            "agent_id": agent_id,
            "push": push_partial,
            "stream_id": stream_id,
            "memory_hits": memory_hits,
        }
        try:
            reply = await reply_handler(text, context)
            reply.data.setdefault("stream_id", stream_id)
            # теги эмоций — внутренняя разметка: вырезаем из текста, события копим
            face_core: FaceCore | None = app.state.face
            if face_core is not None:
                clean, events, _delivered = await face_core.process_reply(reply.text or "")
            else:
                clean, events = strip_emotion_tags(reply.text or "")
            reply.text = clean
            reply.data["emotions"] = [event.name for event in events]
            bus: FaceBus = app.state.face_bus
            for event in events:
                frame = {"kind": "emotion", "name": event.name}
                await sessions_ref.send(
                    session, build_message(MsgType.FACE, data=dict(frame))
                )
                await bus.publish(frame)
            usage = reply.data.get("usage")
            if usage:
                app.state.token_stats.record(
                    reply.data.get("profile") or settings.brain.default_profile,
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    estimated=bool(usage.get("estimated")),
                    tok_per_sec=reply.data.get("tok_per_sec"),
                )
            if memory is not None:
                summary = await memory.remember_turn(
                    agent_id,
                    text,
                    reply.text or "",
                    session_id=session.id,
                    source=str(context["source"]),
                    profile=reply.data.get("profile"),
                )
                if summary:
                    reply.data["memory_summary"] = summary  # панель/шина узнают после финала
            return reply
        except BrainError as exc:
            logger.warning("Мозг отказал: {}", exc)
            app.state.token_stats.record_error(
                wanted_profile or settings.brain.default_profile
            )
            return error_message(str(exc), code="brain_error")
        except Exception as exc:  # noqa: BLE001 - ответ должен уйти клиенту в любом случае
            logger.exception("Ошибка обработчика реплик: {!r}", exc)
            return error_message(f"обработчик реплик упал: {exc!r}", code="reply_failed")

    if msg_type is MsgType.VOICE:
        voice_core = app.state.voice
        if voice_core is None:
            return error_message("голос выключен: features.voice_enabled или --with-voice", code="voice_disabled")
        text = (message.data.get("text") or message.text or "").strip()
        if not text:
            return error_message("пустой текст озвучки", code="empty_text")
        await stream_voice_to_session(app, session, text, message.data.get("profile"))
        return None

    if msg_type is MsgType.SYSTEM:
        logger.info("WS[{}]: system: {}", session.id, message.text)
        return None

    if msg_type in (MsgType.PONG, MsgType.LOG, MsgType.STATE):
        logger.debug("WS[{}]: служебный пакет {} (игнорирую)", session.id, msg_type.value)
        return None

    return error_message(f"тип '{msg_type.value}' пока не обрабатывается", code="unsupported_type")  # pragma: no cover


# --------------------------------------------------------------------------- #
#  Готовое приложение (для `uvicorn lilith_core.app:app`)
# --------------------------------------------------------------------------- #
app = create_app()


def get_app() -> FastAPI:
    """Возвращает глобальное приложение (удобно для импорта в тестах)."""
    return app


def chat(text: str) -> str:
    """Хелпер для быстрой проверки протокола из Python-консоли."""
    return chat_message(text).to_json()
