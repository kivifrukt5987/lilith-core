"""Тесты паков моделей: манифест, установка, докачка, sha, удаление."""

from __future__ import annotations

import hashlib

import httpx
import pytest

from lilith_core.voice import PackError, PackManager

MANIFEST = """
packs:
  local-pack:
    type: stt
    source: local
    ref: src/model.bin
    target_dir: models/stt
    size: 0
    sha256: null
    note: "локальный тестовый пак"
  net-pack:
    type: tts
    source: url
    ref: http://packed.example/voice.bin
    target_dir: models/tts
    size: 1000
    sha256: null
  broken-pack:
    type: vision
    source: local
    ref: src/model.bin
    target_dir: models/vision
    sha256: "{wrong}"
"""


@pytest.fixture
def pack_env(tmp_path):
    """Манифест + локальный источник + менеджер."""
    (tmp_path / "src").mkdir()
    payload = b"MODELWEIGHTS" * 100
    (tmp_path / "src" / "model.bin").write_bytes(payload)
    manifest = tmp_path / "models" / "packs.yaml"
    manifest.parent.mkdir()
    manifest.write_text(MANIFEST.format(wrong="0" * 64), encoding="utf-8")
    manager = PackManager(manifest, root=tmp_path)
    return tmp_path, manager, payload


class TestManifest:
    """Чтение манифеста и статусы."""

    def test_list_statuses(self, pack_env) -> None:
        _tmp, manager, _payload = pack_env
        statuses = {row["name"]: row for row in manager.list()}
        assert statuses["local-pack"]["state"] == "missing"
        assert statuses["net-pack"]["state"] == "missing"
        assert set(statuses) == {"local-pack", "net-pack", "broken-pack"}

    def test_unknown_pack_raises(self, pack_env) -> None:
        _tmp, manager, _payload = pack_env
        with pytest.raises(PackError, match="не найден"):
            manager.get("teleport-pack")

    def test_missing_manifest_is_empty(self, tmp_path) -> None:
        manager = PackManager(tmp_path / "nope.yaml", root=tmp_path)
        assert manager.list() == []

    def test_user_pack_hot_pickup(self, pack_env) -> None:
        """Своя запись в манифесте подхватывается reload'ом без правки кода."""
        tmp, manager, _payload = pack_env
        with manifest_path(tmp).open("a", encoding="utf-8") as fh:
            fh.write("  my-pack:\n    type: voice-clone\n    source: local\n    ref: src/model.bin\n    target_dir: voices/my\n")
        manager.reload()
        assert "my-pack" in manager.names()


def manifest_path(tmp) -> "Path":  # type: ignore[name-defined]
    from pathlib import Path as _P

    return _P(tmp) / "models" / "packs.yaml"


@pytest.mark.asyncio
class TestInstall:
    """Установка: local, sha, докачка, битые случаи."""

    async def test_install_local(self, pack_env) -> None:
        _tmp, manager, payload = pack_env
        status = await manager.install("local-pack")
        assert status["state"] == "installed"
        assert manager.target_path(manager.get("local-pack")).read_bytes() == payload

    async def test_install_twice_is_noop(self, pack_env) -> None:
        _tmp, manager, payload = pack_env
        await manager.install("local-pack")
        status = await manager.install("local-pack")
        assert status["state"] == "installed"

    async def test_sha_mismatch_removes_part(self, pack_env) -> None:
        tmp, manager, _payload = pack_env
        with pytest.raises(PackError, match="sha256"):
            await manager.install("broken-pack")
        target = manager.target_path(manager.get("broken-pack"))
        assert not target.is_file()
        assert not target.with_suffix(target.suffix + ".part").is_file()

    async def test_download_with_resume(self, pack_env) -> None:
        tmp, manager, _payload = pack_env
        payload = bytes(range(256)) * 4  # 1024 байта
        digest = hashlib.sha256(payload).hexdigest()

        # манифест с sha и размером
        manifest = manifest_path(tmp)
        manifest.write_text(
            f"""
packs:
  net-pack:
    type: tts
    source: url
    ref: http://packed.example/voice.bin
    target_dir: models/tts
    size: {len(payload)}
    sha256: {digest}
""",
            encoding="utf-8",
        )

        def handler(request: httpx.Request) -> httpx.Response:
            rng = request.headers.get("range")
            if rng:
                start = int(rng.split("=")[1].split("-")[0])
                return httpx.Response(206, content=payload[start:], headers={"content-length": str(len(payload) - start)})
            return httpx.Response(200, content=payload, headers={"content-length": str(len(payload))})

        manager._packs.clear()
        manager.reload()
        manager._transport = httpx.MockTransport(handler)

        # имитируем обрыв: первые 400 байт уже лежат в .part
        part = manager.target_path(manager.get("net-pack")).with_suffix(".bin.part")
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(payload[:400])

        progress: list[tuple[int, int]] = []

        async def cb(_name: str, done: int, total: int) -> None:
            progress.append((done, total))

        status = await manager.install("net-pack", progress=cb)
        assert status["state"] == "installed"
        assert status["sha_ok"] is True
        assert manager.target_path(manager.get("net-pack")).read_bytes() == payload
        assert progress and progress[-1][0] == len(payload)

    async def test_download_http_error(self, pack_env) -> None:
        tmp, manager, _payload = pack_env
        manager._transport = httpx.MockTransport(lambda _r: httpx.Response(404))
        with pytest.raises(PackError, match="404"):
            await manager.install("net-pack")

    async def test_local_source_missing(self, pack_env) -> None:
        tmp, manager, _payload = pack_env
        manifest = manifest_path(tmp)
        manifest.write_text(
            "packs:\n  ghost:\n    type: stt\n    source: local\n    ref: src/nope.bin\n    target_dir: models/x\n",
            encoding="utf-8",
        )
        manager.reload()
        with pytest.raises(PackError, match="не найден"):
            await manager.install("ghost")


class TestRemove:
    """Удаление файлов пака."""

    @pytest.mark.asyncio
    async def test_remove_installed(self, pack_env) -> None:
        _tmp, manager, _payload = pack_env
        await manager.install("local-pack")
        manager.remove("local-pack")
        assert manager.status("local-pack")["state"] == "missing"

    def test_remove_missing_is_silent(self, pack_env) -> None:
        _tmp, manager, _payload = pack_env
        manager.remove("local-pack")  # не бросает
