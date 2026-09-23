#!/usr/bin/env python3
"""``unity_face_probe.py`` — эмулятор Unity-клиента лица (решение F6-б).

Зачем: Unity в песочнице не запустить, а критерий готовности этапа 6 — «рот
шевелится от чанков TTS». Этот скрипт подключается к продюсеру **точно так же,
как Unity-клиент**, и проверяет тракт до последней мелочи:

* рукопожатие ``hello``: частота, формат, размер чанка, активная персона;
* ``speak`` → поток ``audio``: каждый чанк ровно ``chunk_bytes``, base64
 распаковывается, ``seq`` растёт без дыр, ``offset_ms`` монотонен, последний ``final``;
* серверные виземы (если попросить ``--want-server-visemes``);
* эмоции, ``done``, ``stop``;
* **тело доезжает** (хотфикс 0.6.4, проверка по умолчанию): ``persona_request`` →
  кадр ``persona`` с ``vrm`` → HTTP GET ``/api/face/personas/<id>/model.vrm`` по URL,
  который клиент строит сам, → 200 и magic ``glTF``. Отключается ``--skip-body-check``;
* своп персоны (``--persona``);
* групповая сцена (``--group``).

Зависимостей нет вообще: WebSocket-клиент написан на stdlib (``socket`` +
``base64`` + ``hashlib`` + ``struct``), поэтому скрипт запускается тем же
питоном, что и сервер, даже в голом ``.venv``.

Запуск::

    python scripts/unity_face_probe.py                       # проверить hello
    python scripts/unity_face_probe.py --speak "Привет, Кирюша."
    python scripts/unity_face_probe.py --speak "Раз." --persona nova --json out.json
    python scripts/unity_face_probe.py --group main --speak "Раз."

Код возврата: 0 — все проверки прошли, 1 — есть провалы (можно вешать в CI).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import socket
import struct
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

#: Маска WebSocket-клиента (RFC 6455 требует, чтобы клиент маскировал кадры).
MASK_KEY = b"\x5a\x9c\x31\x7e"

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# --------------------------------------------------------------------------- #
#  Минимальный WebSocket-клиент
# --------------------------------------------------------------------------- #
class MiniWebSocket:
    """Клиент RFC 6455 на сокетах: ровно столько, чтобы говорить с продюсером."""

    def __init__(self, url: str, timeout: float = 10.0) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in ("ws", "wss"):
            raise ValueError(f"нужна ws:// или wss:// схема, получено {parsed.scheme}://")
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        self.path = parsed.path or "/"
        if parsed.query:
            self.path += "?" + parsed.query
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self._buffer = bytearray()

        if parsed.scheme == "wss":
            import ssl

            context = ssl.create_default_context()
            raw = socket.create_connection((self.host, self.port), timeout=timeout)
            self.sock = context.wrap_socket(raw, server_hostname=self.host)
        else:
            self.sock = socket.create_connection((self.host, self.port), timeout=timeout)

        self._handshake()

    def _handshake(self) -> None:
        """HTTP Upgrade → WebSocket."""
        assert self.sock is not None
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "User-Agent: lilith-unity-probe/1.0\r\n"
            "\r\n"
        )
        self.sock.sendall(request.encode("ascii"))

        expected = base64.b64encode(hashlib.sha1((key + WS_GUID).encode("ascii")).digest()).decode("ascii")
        headers = b""
        while b"\r\n\r\n" not in headers:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("сервер закрыл соединение во время рукопожатия")
            headers += chunk

        head, _, rest = headers.partition(b"\r\n\r\n")
        self._buffer.extend(rest)
        lines = head.decode("latin1").split("\r\n")
        status_line = lines[0]
        if " 101 " not in status_line and not status_line.upper().endswith("101 SWITCHING PROTOCOLS"):
            raise ConnectionError(f"ожидался 101 Switching Protocols, получено: {status_line}")
        if expected.lower() not in head.decode("latin1").lower():
            # Некоторые прокси не возвращают accept-ключ; предупреждаем, но не падаем.
            sys.stderr.write("warning: Sec-WebSocket-Accept не совпал\n")

    # -- кадры ------------------------------------------------------------------- #
    def _recv_exact(self, count: int) -> bytes:
        """Прочитать ровно ``count`` байт."""
        assert self.sock is not None
        while len(self._buffer) < count:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("соединение закрыто")
            self._buffer.extend(chunk)
        data = bytes(self._buffer[:count])
        del self._buffer[:count]
        return data

    def recv(self) -> tuple[int, bytes]:
        """Прочитать один кадр: (opcode, payload). Фрагментация не поддерживается."""
        header = self._recv_exact(2)
        first, second = header[0], header[1]
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._recv_exact(8))[0]

        mask = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(length) if length else b""
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, payload

    def recv_text(self, timeout: float | None = None) -> str | None:
        """Текстовый кадр; ``None`` по таймауту, исключение при закрытии."""
        assert self.sock is not None
        self.sock.settimeout(timeout if timeout is not None else self.timeout)
        opcode, payload = self.recv()
        if opcode == 0x8:  # close
            raise ConnectionError("сервер закрыл соединение")
        if opcode == 0x9:  # ping → pong
            self.send(b"", opcode=0xA)
            return self.recv_text(timeout)
        if opcode == 0xA:  # pong
            return self.recv_text(timeout)
        return payload.decode("utf-8", "replace")

    def send(self, payload: bytes, opcode: int = 0x1) -> None:
        """Отправить кадр (обязательно маскированный — мы клиент)."""
        assert self.sock is not None
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack(">H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack(">Q", length))
        header.extend(MASK_KEY)
        masked = bytes(b ^ MASK_KEY[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def send_json(self, frame: dict[str, Any]) -> None:
        """Отправить JSON-кадр."""
        self.send(json.dumps(frame, ensure_ascii=False).encode("utf-8"))

    def close(self) -> None:
        """Вежливо закрыть соединение."""
        if self.sock is None:
            return
        try:
            self.send(b"", opcode=0x8)
        except OSError:
            pass
        try:
            self.sock.close()
        finally:
            self.sock = None

    def __enter__(self) -> "MiniWebSocket":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


# --------------------------------------------------------------------------- #
#  Проверки
# --------------------------------------------------------------------------- #
@dataclass
class Report:
    """Накопитель результатов: проверки + метрики потока."""

    checks: list[tuple[bool, str]] = field(default_factory=list)
    frames: Counter = field(default_factory=Counter)
    audio_chunks: int = 0
    audio_bytes: int = 0
    bad_chunks: list[str] = field(default_factory=list)
    seq_gaps: list[int] = field(default_factory=list)
    offsets: list[int] = field(default_factory=list)
    visemes: Counter = field(default_factory=Counter)
    emotions: Counter = field(default_factory=Counter)
    hello: dict[str, Any] = field(default_factory=dict)
    persona_frames: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_sec: float = 0.0
    final_frame: dict[str, Any] = field(default_factory=dict)
    stop_frames: int = 0
    focus_frames: list[str] = field(default_factory=list)
    #: Хотфикс 0.6.4: сводка проверки «тело доезжает» (URL, статус, байты, magic).
    body: dict[str, Any] = field(default_factory=dict)

    def check(self, ok: bool, message: str) -> None:
        """Записать результат проверки."""
        self.checks.append((bool(ok), message))

    @property
    def passed(self) -> int:
        """Число пройденных проверок."""
        return sum(1 for ok, _ in self.checks if ok)

    @property
    def failed(self) -> int:
        """Число проваленных проверок."""
        return sum(1 for ok, _ in self.checks if not ok)

    def to_dict(self) -> dict[str, Any]:
        """JSON-отчёт (для --json и для прикладывания к отчёту по этапу)."""
        return {
            "ok": self.failed == 0,
            "passed": self.passed,
            "failed": self.failed,
            "duration_sec": round(self.duration_sec, 3),
            "hello": self.hello,
            "frames": dict(self.frames),
            "audio": {
                "chunks": self.audio_chunks,
                "bytes": self.audio_bytes,
                "bad_chunks": self.bad_chunks[:10],
                "seq_gaps": self.seq_gaps[:10],
                "offset_ms_monotonic": all(b >= a for a, b in zip(self.offsets, self.offsets[1:])),
            },
            "visemes": dict(self.visemes),
            "emotions": dict(self.emotions),
            "personas": self.persona_frames,
            "focus": self.focus_frames,
            "stop_frames": self.stop_frames,
            "body": self.body,
            "errors": self.errors,
            "checks": [{"ok": ok, "check": message} for ok, message in self.checks],
        }


def http_base_from_ws(ws_url: str) -> str:
    """HTTP-база из WS-адреса — та же формула, что в ``LilithFaceClient.HttpBaseUrlFrom``."""
    url = ws_url.split("?")[0].replace("wss://", "https://").replace("ws://", "http://")
    index = url.find("/ws")
    return url[:index] if index > 0 else url


def model_url_for(base_url: str, persona_id: str) -> str:
    """URL тела персоны.

    **Хотфикс 0.6.4:** формула обязана совпадать с ``VrmLoader.BuildModelUrl``
    (C#) — именно её клиент использует, когда сервер не прислал ``vrm`` в кадре
    ``persona``. Гвард на совпадение живёт в ``tests/test_hotfix_064.py``.
    """
    return f"{base_url.rstrip('/')}/api/face/personas/{persona_id}/model.vrm"


def fetch_body(url: str, *, timeout: float = 15.0) -> dict[str, Any]:
    """Скачать тело по HTTP (stdlib, без зависимостей) и описать результат."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - локальный сервер
            payload = response.read()
            return {
                "url": url,
                "status": int(response.status),
                "content_type": response.headers.get("content-type") or "",
                "bytes": len(payload),
                "magic": payload[:4].decode("ascii", "replace"),
                "error": "",
            }
    except urllib.error.HTTPError as exc:
        body = exc.read()[:400].decode("utf-8", "replace")
        return {"url": url, "status": exc.code, "content_type": "", "bytes": 0, "magic": "", "error": body}
    except Exception as exc:  # noqa: BLE001 - diagnosтика важнее типа ошибки
        return {"url": url, "status": 0, "content_type": "", "bytes": 0, "magic": "", "error": repr(exc)}


def check_body_reaches_scene(
    ws: MiniWebSocket,
    report: Report,
    *,
    ws_url: str,
    persona_id: str,
    timeout: float = 8.0,
    verbose: bool = False,
) -> None:
    """**Хотфикс 0.6.4.** Регрессия блокера F7: «тело не доезжает до сцены».

    Проверяет весь путь, которым пойдёт Unity-клиент:

    1. ``persona_request`` → сервер обязан ответить кадром ``persona`` с ``swap:true``;
    2. в кадре есть ``vrm`` (иначе у персоны нет тела на диске — 404 ждёт и клиента);
    3. URL, который клиент **строит сам**, совпадает с ``vrm`` из кадра;
    4. HTTP GET этого URL отдаёт 200 и настоящий GLB (magic ``glTF``).

    Пункты 3–4 — ровно то, чего не хватало в 0.6.3: probe никогда не проверял
    путь «подключился → тело», поэтому блокер и дожил до приёмки на Windows.
    """
    base = http_base_from_ws(ws_url)
    built_url = model_url_for(base, persona_id)
    report.body = {"persona": persona_id, "built_url": built_url}

    ws.send_json({"type": "persona_request", "id": persona_id})
    frame: dict[str, Any] | None = None
    for candidate in read_frames(ws, report, stop_when=lambda f: f.get("type") in {"persona", "error"}, timeout=timeout):
        report.frames[candidate.get("type", "?")] += 1
        if verbose:
            print(f"  ← {candidate.get('type')}: {json.dumps(candidate, ensure_ascii=False)[:200]}")
        if candidate.get("type") == "persona":
            frame = candidate
        elif candidate.get("type") == "error":
            report.errors.append(str(candidate.get("detail")))

    report.check(frame is not None, f"persona_request('{persona_id}') вернул кадр persona")
    if frame is None:
        report.body["verdict"] = "кадр persona не пришёл — триггера свопа нет"
        return

    report.check(bool(frame.get("swap", False)), f"кадр persona.swap = {frame.get('swap')!r}")
    frame_vrm = frame.get("vrm")
    report.body["frame_vrm"] = frame_vrm
    report.check(
        bool(frame_vrm),
        f"кадр persona.vrm = {frame_vrm!r}"
        + ("" if frame_vrm else " (нет тела на диске: face.yaml:vrm_path или personas/<id>/model.vrm)"),
    )
    if frame_vrm:
        report.check(
            base + str(frame_vrm) == built_url,
            f"URL из кадра совпадает с построенным клиентом: {built_url}",
        )

    fetched = fetch_body(built_url, timeout=max(5.0, timeout))
    report.body.update(fetched)
    report.check(fetched["status"] == 200, f"GET {built_url} → {fetched['status']} {fetched['error'][:120]}")
    report.check(fetched["magic"] == "glTF", f"тело — настоящий GLB: magic={fetched['magic']!r}, {fetched['bytes']} Б")
    if fetched["status"] == 200 and fetched["magic"] == "glTF":
        report.body["verdict"] = "тело доедет: URL живой, GLB валиден"


def read_frames(ws: MiniWebSocket, report: Report, *, stop_when: Any, timeout: float = 8.0) -> Iterator[dict[str, Any]]:
    """Читать кадры до выполнения ``stop_when(frame)`` или таймаута."""
    while True:
        try:
            raw = ws.recv_text(timeout=timeout)
        except socket.timeout:
            report.errors.append(f"таймаут {timeout} с без кадров")
            return
        except ConnectionError as exc:
            report.errors.append(str(exc))
            return
        if raw is None:
            continue
        try:
            frame = json.loads(raw)
        except json.JSONDecodeError:
            report.errors.append(f"не JSON: {raw[:120]}")
            continue
        yield frame
        if stop_when(frame):
            return


def probe(
    url: str,
    *,
    speak: str = "",
    persona: str = "",
    want_server_visemes: bool = False,
    group: str = "",
    timeout: float = 20.0,
    verbose: bool = False,
    skip_body_check: bool = False,
) -> Report:
    """Прогнать сценарий клиента и вернуть отчёт."""
    report = Report()
    if group:
        parsed = urlparse(url)
        base = url.split("?")[0]
        url = f"{base}?group={group}" if parsed.query == "" else url

    started = time.time()
    try:
        ws = MiniWebSocket(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - важно любое падение подключения
        report.check(False, f"подключиться к {url}: {exc!r}")
        report.duration_sec = time.time() - started
        return report

    with ws:
        report.check(True, f"подключился к {url}")

        # 1) рукопожатие
        hello_name = "hello-group" if group else "hello"
        first = next(read_frames(ws, report, stop_when=lambda f: f.get("type") == hello_name, timeout=timeout), None)
        if first is None:
            report.check(False, f"не получил '{hello_name}' от сервера")
            report.duration_sec = time.time() - started
            return report

        report.hello = first
        report.frames[first.get("type", "?")] += 1
        sample_rate = int(first.get("sample_rate") or 0)
        chunk_bytes = int(first.get("chunk_bytes") or 0)
        report.check(sample_rate > 0, f"hello.sample_rate = {sample_rate}")
        report.check(chunk_bytes > 0 and chunk_bytes % 2 == 0, f"hello.chunk_bytes = {chunk_bytes} (кратно 2)")
        report.check(first.get("format") in (None, "pcm_s16le"), f"hello.format = {first.get('format')}")
        if not group:
            report.check(bool(first.get("producer")), f"hello.producer = {first.get('producer')}")
            report.check(bool(first.get("persona")), f"hello.persona = {first.get('persona')}")

        # 2) клиентское рукопожатие (как Unity)
        ws.send_json(
            {
                "type": "hello",
                "client": "unity-probe/1.0",
                "want_server_visemes": want_server_visemes,
                "platform": "PythonProbe",
            }
        )
        ws.send_json({"type": "ready"})

        # 2.5) хотфикс 0.6.4: «тело доезжает» — главная проверка приёмки F7.
        #      Идём тем же путём, что и Unity-клиент: persona_request → кадр
        #      persona → HTTP GET model.vrm по URL, построенному клиентом.
        if not group and not skip_body_check:
            hello_persona = str(first.get("persona") or "")
            if hello_persona:
                check_body_reaches_scene(
                    ws,
                    report,
                    ws_url=url,
                    persona_id=hello_persona,
                    timeout=min(timeout, 10.0),
                    verbose=verbose,
                )
            else:
                report.check(False, "hello.persona пуста — проверять тело не для кого")

        # 3) своп персоны, если попросили
        if persona:
            ws.send_json({"type": "persona_request", "id": persona})
            for frame in read_frames(ws, report, stop_when=lambda f: f.get("type") in {"persona", "error"}, timeout=timeout):
                report.frames[frame.get("type", "?")] += 1
                if frame.get("type") == "persona":
                    report.persona_frames.append(str(frame.get("id")))
                elif frame.get("type") == "error":
                    report.errors.append(str(frame.get("detail")))
            report.check(persona in report.persona_frames, f"своп персоны на '{persona}'")

        # 4) реплика
        if speak:
            ws.send_json({"type": "speak", "text": speak})
            expected_seq = 0
            for frame in read_frames(ws, report, stop_when=lambda f: f.get("type") in {"done", "error"}, timeout=timeout):
                kind = frame.get("type", "?")
                report.frames[kind] += 1
                if verbose:
                    print(f"  ← {kind}: {json.dumps({k: v for k, v in frame.items() if k != 'data'}, ensure_ascii=False)[:160]}")

                if kind == "audio":
                    report.audio_chunks += 1
                    raw = base64.b64decode(frame.get("data") or "")
                    report.audio_bytes += len(raw)
                    if len(raw) != chunk_bytes and not frame.get("final"):
                        report.bad_chunks.append(f"seq={frame.get('seq')} len={len(raw)} (ожидал {chunk_bytes})")
                    seq = frame.get("seq")
                    if seq != expected_seq:
                        report.seq_gaps.append(f"{expected_seq}→{seq}")
                    expected_seq = (seq or 0) + 1
                    report.offsets.append(int(frame.get("offset_ms") or 0))
                elif kind == "viseme":
                    report.visemes[str(frame.get("code"))] += 1
                elif kind == "emotion":
                    report.emotions[str(frame.get("tag"))] += 1
                elif kind == "focus":
                    report.focus_frames.append(str(frame.get("persona")))
                elif kind == "stop":
                    report.stop_frames += 1
                elif kind == "error":
                    report.errors.append(str(frame.get("detail")))
                elif kind == "done":
                    report.final_frame = frame

            report.check(report.audio_chunks > 0, f"пришло аудио-чанков: {report.audio_chunks}")
            report.check(not report.bad_chunks, f"все чанки ровно {chunk_bytes} Б (исключая final): {not report.bad_chunks}")
            report.check(not report.seq_gaps, f"seq без дыр: {not report.seq_gaps}")
            report.check(
                all(b >= a for a, b in zip(report.offsets, report.offsets[1:])),
                "offset_ms монотонно растёт",
            )
            report.check(report.frames.get("done", 0) >= 1, "получен кадр done")
            if want_server_visemes:
                report.check(sum(report.visemes.values()) > 0, "серверные виземы пришли")
            else:
                report.check(sum(report.visemes.values()) == 0, "серверных визем нет (Unity считает сам, A3.1)")

        # 5) stats (как Unity раз в 5 с)
        ws.send_json({"type": "stats", "fps": 60, "dropped": 0, "queued_ms": 0, "playing": False})
        ws.send_json({"type": "ping"})
        for frame in read_frames(ws, report, stop_when=lambda f: f.get("type") in {"pong", "state", "error"}, timeout=3.0):
            report.frames[frame.get("type", "?")] += 1

        report.check(report.audio_bytes % 2 == 0, "всё аудио кратно 2 байтам (int16)")
        if sample_rate and report.audio_bytes:
            seconds = report.audio_bytes / 2 / sample_rate
            report.check(seconds > 0, f"аудио на {seconds:.2f} с при {sample_rate} Гц")

    report.duration_sec = time.time() - started
    return report


def print_report(report: Report, *, json_path: str = "") -> None:
    """Человекочитаемый отчёт + (опционально) JSON-файл."""
    print("\n" + "=" * 74)
    print("ПРОБА UNITY-КЛИЕНТА · LILITH-CORE этап 6")
    print("=" * 74)
    if report.body:
        body = report.body
        print(
            f"тело: {body.get('persona', '?')} · GET {body.get('status', '-')} · "
            f"{body.get('bytes', 0)} Б · magic {body.get('magic') or '-'} · {body.get('verdict', '')}"
        )
    if report.hello:
        hello = report.hello
        print(
            f"hello: {hello.get('producer', hello.get('group', '?'))} · "
            f"{hello.get('sample_rate')} Гц · чанк {hello.get('chunk_bytes')} Б · "
            f"формат {hello.get('format', '-')} · сервер {hello.get('server_version', '-')}"
        )
    print(
        f"кадры: {dict(report.frames)}\n"
        f"аудио: {report.audio_chunks} чанков, {report.audio_bytes} байт"
        f" ({report.audio_bytes / 2 / max(1, int(report.hello.get('sample_rate') or 24000)):.2f} с)"
    )
    if report.visemes:
        print(f"виземы: {dict(report.visemes)}")
    if report.emotions:
        print(f"эмоции: {dict(report.emotions)}")
    if report.persona_frames:
        print(f"персоны: {report.persona_frames}")
    if report.errors:
        print(f"ошибки сервера: {report.errors}")

    print("-" * 74)
    for ok, message in report.checks:
        print(f"  [{'OK ' if ok else 'FAIL'}] {message}")
    print("-" * 74)
    print(f"ИТОГ: {report.passed} пройдено, {report.failed} провалено, {report.duration_sec:.2f} с")

    if json_path:
        Path(json_path).write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"JSON-отчёт: {json_path}")


def main() -> int:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(
        description="Эмулятор Unity-клиента лица: проверяет тракт /ws/face/producer без Unity.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--url", default="ws://127.0.0.1:8765/ws/face/producer", help="адрес продюсера")
    parser.add_argument("--speak", default="", help="попросить сервер сказать текст")
    parser.add_argument("--persona", default="", help="сменить персону перед репликой")
    parser.add_argument("--group", default="", help="подключиться к /ws/group с этим именем")
    parser.add_argument("--want-server-visemes", action="store_true", help="попросить серверную разметку визем")
    parser.add_argument("--timeout", type=float, default=20.0, help="таймаут ожидания кадров, с")
    parser.add_argument("--json", default="", help="куда сохранить JSON-отчёт")
    parser.add_argument("--verbose", action="store_true", help="печатать каждый кадр")
    parser.add_argument(
        "--skip-body-check",
        action="store_true",
        help="не проверять «тело доезжает» (хотфикс 0.6.4: persona_request + GET model.vrm)",
    )
    args = parser.parse_args()

    report = probe(
        args.url,
        speak=args.speak,
        persona=args.persona,
        want_server_visemes=args.want_server_visemes,
        group=args.group,
        timeout=args.timeout,
        verbose=args.verbose,
        skip_body_check=args.skip_body_check,
    )
    print_report(report, json_path=args.json)
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
