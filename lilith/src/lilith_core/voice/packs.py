"""Паки моделей (этап 4): манифест ``models/packs.yaml`` + установщик.

Пак = именованный комплект весов (tts / stt / voice-clone / vision / mmproj),
описанный в манифесте: источник (hf / hf-mirror / url / local), размер, sha256,
куда класть. Установка умеет докачку (Range) и проверку хеша; пользовательский
пак = своя запись в манифесте (local-путь или url) без правки кода (ADR-012).
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

import httpx
import yaml
from loguru import logger

__all__ = ["Pack", "PackManager", "PackError", "HF_MIRRORS"]

#: Зеркала HuggingFace (hf-mirror выбирается в манифесте источником).
HF_MIRRORS: dict[str, str] = {
    "hf": "https://huggingface.co",
    "hf-mirror": "https://hf-mirror.com",
}

ProgressCallback = Callable[[str, int, int], Awaitable[None]]  # name, done, total


class PackError(Exception):
    """Ошибка установки/удаления пака."""


@dataclass(slots=True)
class Pack:
    """Запись манифеста пака."""

    name: str
    type: Literal["tts", "stt", "voice-clone", "vision", "mmproj", "llm"]
    source: Literal["hf", "hf-mirror", "url", "local"]
    ref: str  # путь внутри источника: "repo/resolve/main/file" для hf, url или локальный путь
    target_dir: str
    size: int = 0
    sha256: str | None = None
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def url(self) -> str:
        """Полный URL для сетевых источников."""
        if self.source == "local":
            return self.ref
        if self.source in HF_MIRRORS:
            return f"{HF_MIRRORS[self.source]}/{self.ref}"
        return self.ref


class PackManager:
    """Список/установка/удаление паков по манифесту."""

    def __init__(
        self,
        manifest_path: str | Path,
        root: str | Path = ".",
        *,
        transport: Any = None,
    ) -> None:
        self.manifest_path = Path(manifest_path)
        self.root = Path(root)
        self._transport = transport
        self._packs: dict[str, Pack] = {}
        self.reload()

    # -- манифест ------------------------------------------------------------- #
    def reload(self) -> None:
        """Перечитывает манифест (пользовательские записи подхватываются горячо)."""
        self._packs = {}
        if not self.manifest_path.is_file():
            logger.warning("Манифест паков не найден: {}", self.manifest_path)
            return
        raw = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8")) or {}
        for name, spec in (raw.get("packs") or {}).items():
            if not isinstance(spec, dict):
                continue
            self._packs[str(name)] = Pack(
                name=str(name),
                type=spec.get("type", "llm"),
                source=spec.get("source", "hf"),
                ref=str(spec.get("ref", "")),
                target_dir=str(spec.get("target_dir", "models")),
                size=int(spec.get("size", 0) or 0),
                sha256=spec.get("sha256") or None,
                note=spec.get("note", ""),
                extra={k: v for k, v in spec.items() if k not in
                       {"type", "source", "ref", "target_dir", "size", "sha256", "note"}},
            )
        logger.debug("Манифест паков: {} записей", len(self._packs))

    def names(self) -> list[str]:
        return sorted(self._packs)

    def get(self, name: str) -> Pack:
        if name not in self._packs:
            raise PackError(f"пак '{name}' не найден в манифесте (доступны: {', '.join(self.names())})")
        return self._packs[name]

    # -- статус ---------------------------------------------------------------- #
    def target_path(self, pack: Pack) -> Path:
        """Куда ложится файл пака."""
        return self.root / pack.target_dir / Path(pack.ref).name

    def status(self, name: str) -> dict[str, Any]:
        """installed / partial / missing + размеры и хеш."""
        pack = self.get(name)
        path = self.target_path(pack)
        part = path.with_suffix(path.suffix + ".part")
        if path.is_file():
            ok_hash = True
            if pack.sha256:
                ok_hash = self._sha256(path) == pack.sha256.lower()
            return {
                "name": name,
                "type": pack.type,
                "state": "installed" if ok_hash else "broken",
                "path": str(path),
                "size": path.stat().st_size,
                "expected": pack.size or None,
                "sha_ok": ok_hash,
                "note": pack.note,
            }
        if part.is_file():
            return {
                "name": name,
                "type": pack.type,
                "state": "partial",
                "path": str(part),
                "size": part.stat().st_size,
                "expected": pack.size or None,
                "sha_ok": None,
                "note": pack.note,
            }
        return {
            "name": name,
            "type": pack.type,
            "state": "missing",
            "path": str(path),
            "size": 0,
            "expected": pack.size or None,
            "sha_ok": None,
            "note": pack.note,
        }

    def list(self) -> list[dict[str, Any]]:
        """Статусы всех паков."""
        return [self.status(name) for name in self.names()]

    # -- установка --------------------------------------------------------------- #
    async def install(self, name: str, progress: ProgressCallback | None = None) -> dict[str, Any]:
        """Ставит пак: local — копирование, сетевые — скачивание с докачкой и sha."""
        pack = self.get(name)
        target = self.target_path(pack)
        if target.is_file() and (not pack.sha256 or self._sha256(target) == pack.sha256.lower()):
            logger.info("Пак {} уже установлен", name)
            return self.status(name)

        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_suffix(target.suffix + ".part")

        if pack.source == "local":
            src = Path(pack.ref)
            if not src.is_absolute():
                src = self.root / src
            if not src.is_file():
                raise PackError(f"локальный источник пака '{name}' не найден: {src}")
            shutil.copy2(src, part)
        else:
            await self._download(pack, part, progress)

        if pack.sha256:
            digest = self._sha256(part)
            if digest != pack.sha256.lower():
                part.unlink(missing_ok=True)
                raise PackError(f"пак '{name}': sha256 не совпал ({digest[:12]}… вместо {pack.sha256[:12]}…)")
        part.replace(target)
        logger.info("Пак {} установлен: {}", name, target)
        return self.status(name)

    async def _download(self, pack: Pack, part: Path, progress: ProgressCallback | None) -> None:
        """Скачивание с докачкой: HEAD/Range, прогресс-колбэк."""
        headers = {}
        done = part.stat().st_size if part.is_file() else 0
        if done:
            headers["Range"] = f"bytes={done}-"
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, read=300.0),
            follow_redirects=True,
            transport=self._transport,
        ) as client:
            async with client.stream("GET", pack.url(), headers=headers) as resp:
                if resp.status_code == 416:  # докачка не нужна: файл уже полный
                    return
                if resp.status_code not in (200, 206):
                    raise PackError(f"пак '{pack.name}': HTTP {resp.status_code} на {pack.url()}")
                total = int(resp.headers.get("content-length", 0) or 0) + (done if resp.status_code == 206 else 0)
                mode = "ab" if resp.status_code == 206 else "wb"
                if resp.status_code == 200:
                    done = 0
                with part.open(mode) as fh:
                    async for chunk in resp.aiter_bytes(1 << 16):
                        fh.write(chunk)
                        done += len(chunk)
                        if progress:
                            await progress(pack.name, done, total or pack.size)
        if pack.size and part.stat().st_size != pack.size:
            raise PackError(
                f"пак '{pack.name}': размер {part.stat().st_size} != ожидаемый {pack.size} (обрыв?)"
            )

    def remove(self, name: str) -> None:
        """Удаляет файл пака (и .part)."""
        pack = self.get(name)
        for path in (self.target_path(pack), self.target_path(pack).with_suffix(
                self.target_path(pack).suffix + ".part")):
            if path.is_file():
                path.unlink()
                logger.info("Пак {} удалён: {}", name, path)

    # -- хеши ------------------------------------------------------------------- #
    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
