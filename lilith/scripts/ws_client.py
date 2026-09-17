"""Ручная проверка WebSocket-шины из консоли.

Запуск (сервер должен уже работать)::

    python scripts/ws_client.py
    python scripts/ws_client.py --url ws://127.0.0.1:9000/ws --text "привет"
    python scripts/ws_client.py --interactive

Требуется ``pip install websockets``.
"""

from __future__ import annotations

import argparse
import asyncio
import json

try:
    import websockets
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Нужен пакет websockets:  pip install websockets") from exc


def show(frame: dict) -> None:
    """Красиво печатает входящий кадр."""
    kind = frame.get("type", "?")
    text = frame.get("text")
    data = frame.get("data") or {}
    line = f"  ← [{kind}]"
    if text:
        line += f" {text}"
    if data:
        line += f"  {json.dumps(data, ensure_ascii=False)}"
    print(line)


async def run(url: str, texts: list[str], interactive: bool) -> None:
    """Подключается, шлёт сообщения и печатает ответы."""
    print(f"→ подключаюсь к {url}")
    async with websockets.connect(url, max_size=2**22) as ws:
        # handshake: сервер сразу шлёт hello + system
        for _ in range(2):
            show(json.loads(await ws.recv()))

        await ws.send(json.dumps({"type": "ping", "id": "manual"}))
        show(json.loads(await ws.recv()))

        for text in texts:
            print(f"→ [chat] {text}")
            await ws.send(json.dumps({"type": "chat", "text": text, "data": {"source": "ws_client"}}))
            show(json.loads(await ws.recv()))

        if not interactive:
            return

        print("\nИнтерактивный режим. Пустая строка или Ctrl+C — выход.\n")
        while True:
            try:
                text = input("вы> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not text:
                return
            await ws.send(json.dumps({"type": "chat", "text": text, "data": {"source": "ws_client"}}))
            show(json.loads(await ws.recv()))


def main() -> None:
    """Точка входа скрипта."""
    parser = argparse.ArgumentParser(description="Проверка WebSocket-шины LILITH-CORE")
    parser.add_argument("--url", default="ws://127.0.0.1:8765/ws")
    parser.add_argument("--text", action="append", default=None, help="сообщение (можно несколько раз)")
    parser.add_argument("--interactive", "-i", action="store_true", help="режим диалога")
    args = parser.parse_args()

    texts = args.text or ["Лиль, проверка связи 🦇"]
    asyncio.run(run(args.url, texts, args.interactive))


if __name__ == "__main__":
    main()
