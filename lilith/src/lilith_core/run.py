"""Точка запуска сервера: ``python -m lilith_core.run``.

На Windows то же самое делает ``start.bat`` в корне проекта.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn
from loguru import logger

from . import __stage__, __version__
from .config import Settings, config_source_info, load_settings, resolve_config_path
from .logging_setup import banner, setup_logging


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Разбирает аргументы командной строки."""
    parser = argparse.ArgumentParser(
        prog="lilith-core",
        description="LILITH-CORE — домашний ИИ-компаньон (этап 1: скелет).",
    )
    parser.add_argument("--host", default=None, help="адрес интерфейса (по умолчанию из конфига)")
    parser.add_argument("--port", type=int, default=None, help="порт (по умолчанию из конфига)")
    parser.add_argument("--config", default=None, help="путь к config.yaml")
    parser.add_argument("--log-level", default=None, help="TRACE/DEBUG/INFO/WARNING/ERROR")
    parser.add_argument("--reload", action="store_true", help="автоперезагрузка при правке кода (для разработки)")
    parser.add_argument("--show-config", action="store_true", help="напечатать эффективный конфиг и выйти")
    parser.add_argument(
        "--with-face",
        action="store_true",
        help="включить лицо этапа 5 (VRM-вьювер в панели, виземы, эмоции)",
    )
    parser.add_argument(
        "--with-voice",
        action="store_true",
        help="включить голос этапа 4 (уши/горло/паки; в песочнице — mock-бэкенды)",
    )
    parser.add_argument(
        "--with-memory",
        action="store_true",
        help="включить память этапа 3 (журнал aiosqlite + RAG + авто-саммари)",
    )
    parser.add_argument(
        "--mock-brain",
        action="store_true",
        help="включить мозг в mock-режиме (демо этапа 2 без модели): ответы-заглушки, стриминг, метрики",
    )
    return parser.parse_args(argv)


def packs_main(argv: list[str]) -> int:
    """CLI паков: lilith-core packs list|install|remove."""
    import asyncio

    from .config import resolve_path
    try:
        from .voice import PackManager
    except ModuleNotFoundError as exc:
        print(f"[LILITH] не хватает зависимости: {exc.name} -> pip install -e \".[dev,memory]\"")  # noqa: CLI-диагноз
        return 1

    if not argv:
        print("использование: lilith-core packs list | install <имя> | remove <имя>")  # noqa: CLI-вывод packs
        return 2
    command, rest = argv[0], argv[1:]
    settings = load_settings()
    manager = PackManager(
        resolve_path(settings.voice.packs_manifest),
        root=resolve_path(settings.voice.packs_root or "."),
    )
    if command == "list":
        for row in manager.list():
            size = f"{row['size'] / 1048576:.1f} МБ" if row["size"] else "—"
            print(f"  {row['name']:<22} {row['state']:<10} {row['type']:<12} {size:>10}  {row['note']}")  # noqa: CLI-вывод packs
        return 0
    if command == "install":
        if not rest:
            print("install требует имя пака")  # noqa: CLI-вывод packs
            return 2

        async def _progress(name: str, done: int, total: int) -> None:
            percent = done * 100 // total if total else 0
            print(f"\r  {name}: {done / 1048576:.1f}/{total / 1048576:.1f} МБ ({percent}%)", end="", flush=True)  # noqa: CLI-вывод packs

        try:
            status = asyncio.run(manager.install(rest[0], progress=_progress))
        except Exception as exc:  # noqa: BLE001
            print(f"\n[ERROR] {exc}")  # noqa: CLI-вывод packs
            return 1
        print(f"\n  готово: {status['path']}")  # noqa: CLI-вывод packs
        return 0
    if command == "remove":
        if not rest:
            print("remove требует имя пака")  # noqa: CLI-вывод packs
            return 2
        manager.remove(rest[0])
        return 0
    print(f"неизвестная команда packs: {command}")  # noqa: CLI-вывод packs
    return 2


def main(argv: list[str] | None = None) -> int:
    """Запускает uvicorn (или packs CLI). Возвращает код возврата процесса."""
    raw = list(argv if argv is not None else sys.argv[1:])
    if raw and raw[0] == "packs":
        return packs_main(raw[1:])
    args = parse_args(raw)

    settings: Settings = load_settings(args.config)
    if args.log_level:
        settings.logging.level = args.log_level.upper()
    if args.with_face:
        settings.features.face_enabled = True
    if args.with_voice:
        settings.features.voice_enabled = True
    if args.with_memory:
        settings.features.memory_enabled = True
    if args.mock_brain:
        settings.features.brain_enabled = True
        settings.brain.defaults.provider = "mock"
    setup_logging(settings, force=True)

    if args.show_config:
        _print_effective_config(settings)
        return 0

    host = args.host or settings.server.host
    port = args.port or settings.server.port

    banner(
        f"LILITH.EXE v{__version__} | stage {__stage__}\n"
        f"panel  -> http://{host}:{port}/\n"
        f"health -> http://{host}:{port}/healthz\n"
        f"ws     -> ws://{host}:{port}{settings.server.ws_path}\n"
        f"docs   -> http://{host}:{port}/docs  (только при app.debug=true)"
    )

    # Фиксируем фактический адрес, чтобы логи и /healthz не врали про порт.
    try:
        from .app import app as fastapi_app
    except ModuleNotFoundError as exc:
        print(f"[LILITH] не хватает зависимости: {exc.name}")  # noqa: CLI-диагноз
        print("[LILITH] скорее всего ты запустил меня не тем питоном (системным вместо .venv).")  # noqa: CLI-диагноз
        print("[LILITH] лечение: двойной клик по start.bat")  # noqa: CLI-диагноз
        print("[LILITH]   или: .venv\\Scripts\\python.exe -m lilith_core.run")  # noqa: CLI-диагноз
        print("[LILITH]   или: pip install -e \".[dev,memory,voice]\"")  # noqa: CLI-диагноз
        return 1

    fastapi_app.state.bound_host = host
    fastapi_app.state.bound_port = port

    try:
        uvicorn.run(
            "lilith_core.app:app",
            host=host,
            port=port,
            reload=args.reload,
            log_config=None,          # логируем через loguru, а не через конфиг uvicorn
            access_log=False,
            lifespan="on",
            ws_max_size=settings.server.ws_max_message_size,
        )
    except KeyboardInterrupt:  # pragma: no cover
        logger.info("Остановлено пользователем (Ctrl+C)")
        return 130
    except OSError as exc:
        logger.error("Не удалось поднять сервер: {}", exc)
        logger.error("Проверьте, не занят ли порт {} другим процессом", port)
        return 1
    return 0


def _print_effective_config(settings: Settings) -> None:
    """Печатает итоговый конфиг (без секретов) — полезно для диагностики."""
    import json

    logger.info("Источник конфига: {}", config_source_info())
    logger.info("Эффективный конфиг:\n{}", json.dumps(settings.public_dict(), ensure_ascii=False, indent=2))
    logger.info("persona.md: {}", settings.persona_file)
    logger.info("persona существует: {}", settings.persona_file.is_file())
    logger.info("config.yaml: {}", resolve_config_path())
    logger.info("cwd: {}", Path.cwd())


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
