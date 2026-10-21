"""Гварды хотфикса 0.7.1 — дефект-лист 21.10 (среда Курьера + загрузчик), ADR-029.

п.1 offline-first загрузчик: явный tts_resident_model_path, локальная папка без
    сети, честная FileNotFoundError вместо петли закачки;
п.2 edge-healthcheck с таймаутом и кэшем (молчаливый пропуск за стеной),
    короткие 503 без трейсов;
п.3 офлайн-рубильник LILITH_VOICE_OFFLINE (autouse-фикстура conftest) — тесты
    не качают silero/edge/whisper из сети;
п.4 timeout-маркеры WS-модулей + pytest-timeout в dev-extras + проверка в run_tests.bat;
п.5 ChannelStripper: канальные токены thought не лезут в контент (стрим и разовый).

Все гварды герметичны: GPU, сеть и движки не нужны.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest
import yaml

from lilith_core.brain.llm import HIDDEN_CHANNELS, ChannelStripper, LLMClient
from lilith_core.voice import VoiceCore
from lilith_core.voice.tts import EdgeTTS, SileroTTS, TTSBackend, VoiceProfile
from lilith_core.voice.tts_qwen3 import Qwen3TTS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FAKE_HF_ID = "test-hf/fake-model-does-not-exist-071"


def _voice_core(settings, tmp_project) -> VoiceCore:
    settings.features.voice_enabled = True
    settings.voice.packs_manifest = str(tmp_project / "models" / "packs.yaml")
    settings.voice.packs_root = str(tmp_project)
    (tmp_project / "models").mkdir(exist_ok=True)
    (tmp_project / "models" / "packs.yaml").write_text("packs: {}\n", encoding="utf-8")
    return VoiceCore(settings)


# --------------------------------------------------------------------------- #
# п.1 — offline-first загрузчик                                               #
# --------------------------------------------------------------------------- #
class TestResidentModelPath:
    def test_config_keys_exist(self) -> None:
        from lilith_core.config import VoiceSettings

        defaults = VoiceSettings()
        assert defaults.tts_resident_model_path == ""
        assert defaults.tts_allow_download is False
        assert defaults.cloud_healthcheck is True
        assert 0 < defaults.cloud_healthcheck_timeout_sec <= 10

    def test_local_yaml_pins_resident_path(self) -> None:
        """local.yaml Курьера: явный путь резидента (приоритет над сканом профилей)."""
        data = yaml.safe_load(
            (PROJECT_ROOT / "config" / "local.yaml").read_text(encoding="utf-8")
        )
        voice = data["voice"]
        assert voice["tts_resident_model_path"] == "E:/models/qwen3-tts-06b"
        assert voice["tts_allow_download"] is False

    def test_priority_explicit_key_over_profile(self, settings, tmp_project) -> None:
        core = _voice_core(settings, tmp_project)
        settings.voice.profiles = {
            "soul": {"backend": "qwen3", "extra": {"model": "profile/model-id"}}
        }
        core.sync()
        # без явного ключа — из профиля
        assert core._resident_model_id("qwen3") == "profile/model-id"
        # явный ключ побеждает профиль (дефект-лист 21.10 п.1)
        settings.voice.tts_resident_model_path = "E:/models/qwen3-tts-06b"
        assert core._resident_model_id("qwen3") == "E:/models/qwen3-tts-06b"


class TestOfflineFirstResolve:
    def test_local_dir_loads_offline(self, tmp_path: Path) -> None:
        model_dir = tmp_path / "qwen3-local"
        model_dir.mkdir()
        resolved, offline = Qwen3TTS().resolve_model(str(model_dir))
        assert resolved == str(model_dir) and offline is True

    def test_unknown_hf_id_without_download_raises_honest_error(self) -> None:
        backend = Qwen3TTS(allow_download=False)
        with pytest.raises(FileNotFoundError) as exc:
            backend.resolve_model(FAKE_HF_ID)
        message = str(exc.value)
        assert "tts_resident_model_path" in message  # рецепт: явный путь
        assert "tts_allow_download" in message       # рецепт: разовая закачка
        assert FAKE_HF_ID in message                 # что именно не найдено

    def test_allow_download_lets_network_through(self) -> None:
        backend = Qwen3TTS(allow_download=True)
        _resolved, offline = backend.resolve_model(FAKE_HF_ID)
        assert offline is False  # закачка разрешена ЯВНО — петли за стеной не будет


# --------------------------------------------------------------------------- #
# п.2 — healthcheck облака: таймаут, кэш, молчаливый пропуск                  #
# --------------------------------------------------------------------------- #
class TestEdgeHealthcheck:
    @pytest.fixture(autouse=True)
    def _reset_cache(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(EdgeTTS, "_health_cache", None)
        # рубильник п.3 отключаем: здесь тестируется именно healthcheck
        monkeypatch.delenv("LILITH_VOICE_OFFLINE", raising=False)
        import importlib.util
        if importlib.util.find_spec("edge_tts") is None:
            pytest.skip("edge-tts не установлен — healthcheck не добраться")

    def test_wall_gives_fast_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import socket

        calls = {"n": 0}

        def refused(*_args, **_kwargs):
            calls["n"] += 1
            raise OSError("стена")

        monkeypatch.setattr(socket, "create_connection", refused)
        edge = EdgeTTS(healthcheck_timeout=2.5)
        ok, reason = edge.available()
        assert ok is False and "healthcheck" in reason and "OSError" in reason
        ok2, _ = edge.available()
        assert ok2 is False
        assert calls["n"] == 1  # вердикт закэширован — стену не долбим

    def test_reachable_cloud_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import socket

        class FakeSock:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(socket, "create_connection", lambda *_a, **_k: FakeSock())
        ok, reason = EdgeTTS().available()
        assert ok is True and reason == ""

    def test_healthcheck_disabled_skips_socket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import socket

        def boom(*_args, **_kwargs):  # pragma: no cover - не должен вызываться
            raise AssertionError("healthcheck вызван при выключенном флаге")

        monkeypatch.setattr(socket, "create_connection", boom)
        ok, _ = EdgeTTS(healthcheck=False).available()
        assert ok is True


class TestSayShort503:
    @pytest.mark.asyncio
    async def test_all_backends_dead_gives_clean_503(self, settings, tmp_project) -> None:
        from fastapi.testclient import TestClient

        from lilith_core.app import create_app

        class Dead(TTSBackend):
            name = "mock"  # подменяем даже мок — все движки мертвы

            def available(self) -> tuple[bool, str]:
                return True, ""

            async def synthesize(self, text: str, profile: VoiceProfile) -> bytes:
                raise ConnectionError("облако за стеной")

        settings.voice.profiles = {"x": {"backend": "mock"}}
        app = create_app(_settings_for_app(settings, tmp_project))
        with TestClient(app) as client:
            app.state.voice.tts._backends["mock"] = Dead()  # noqa: SLF001 — инъекция в гварде
            response = client.post("/api/voice/say", json={"text": "живы?"})
            assert response.status_code == 503
            body = response.json()
            assert body["status"] == "error"
            assert "ConnectionError" in body["detail"]
            assert len(body["detail"]) < 300  # короткая строка, не простыня трейса


def _settings_for_app(settings, tmp_project):
    settings.features.voice_enabled = True
    settings.voice.packs_manifest = str(tmp_project / "models" / "packs.yaml")
    settings.voice.packs_root = str(tmp_project)
    (tmp_project / "models").mkdir(exist_ok=True)
    (tmp_project / "models" / "packs.yaml").write_text("packs: {}\n", encoding="utf-8")
    return settings


# --------------------------------------------------------------------------- #
# п.3 — офлайн-рубильник для тестов (и для жизни за стеной)                   #
# --------------------------------------------------------------------------- #
class TestOfflineSwitch:
    def test_env_switch_is_on_in_tests(self) -> None:
        assert os.environ.get("LILITH_VOICE_OFFLINE") == "1"  # autouse-фикстура conftest

    def test_network_backends_honestly_off(self) -> None:
        from lilith_core.voice.stt import FasterWhisperSTT, SileroVAD

        for backend in (SileroTTS(), EdgeTTS(), FasterWhisperSTT(), SileroVAD()):
            ok, reason = backend.available()
            assert ok is False, getattr(backend, "name", backend)
            assert "LILITH_VOICE_OFFLINE" in reason

    def test_qwen3_not_gated_by_switch(self) -> None:
        """qwen3 — локальный движок (offline-first), рубильник сети его не касается."""
        _ok, reason = Qwen3TTS().available()
        assert "LILITH_VOICE_OFFLINE" not in reason


# --------------------------------------------------------------------------- #
# п.4 — WS-таймауты                                                           #
# --------------------------------------------------------------------------- #
class TestWsTimeoutGuards:
    WS_MODULES = [
        "test_face_producer.py", "test_face_app.py", "test_app_http.py",
        "test_memory_app.py", "test_face_v2.py", "test_app_ws.py", "test_hotfix_064.py",
    ]

    def test_all_ws_modules_marked(self) -> None:
        for name in self.WS_MODULES:
            text = (PROJECT_ROOT / "tests" / name).read_text(encoding="utf-8")
            assert "pytestmark = pytest.mark.timeout(" in text, name

    def test_dev_extras_has_pytest_timeout(self) -> None:
        pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert "pytest-timeout" in pyproject

    def test_marker_registered_in_ini(self) -> None:
        """--strict-markers не краснеет даже без плагина: маркер зарегистрирован в ini."""
        pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert "timeout(sec)" in pyproject

    def test_run_tests_bat_installs_plugin(self) -> None:
        bat = (PROJECT_ROOT / "run_tests.bat").read_text(encoding="utf-8")
        assert "pytest_timeout" in bat and "pytest-timeout" in bat


# --------------------------------------------------------------------------- #
# п.5 — ChannelStripper: юнит                                                 #
# --------------------------------------------------------------------------- #
CH_OPEN = "<|" + "channel|>"
CH_END = "<|" + "end|>"
TOK_STOP = "<|" + "im_" + "stop|>"


class TestChannelStripper:
    def test_plain_text_passthrough(self) -> None:
        s = ChannelStripper()
        assert s.feed("привет, Кирюша") == "привет, Кирюша"
        assert s.flush() == ""

    def test_honest_less_than_not_eaten(self) -> None:
        s = ChannelStripper()
        assert s.feed("a<b и 2<3") == "a<b и 2<3"

    def test_hidden_channel_dropped(self) -> None:
        s = ChannelStripper()
        out = s.feed(CH_OPEN + "thoughtтак, думаю о просодии" + CH_OPEN + "finalПривет!")
        assert "думаю" not in out and "Привет!" in out

    def test_all_hidden_names_covered(self) -> None:
        for name in HIDDEN_CHANNELS:
            s = ChannelStripper()
            out = s.feed(CH_OPEN + name + "скрытый поток" + CH_OPEN + "finalвидно")
            assert "скрытый поток" not in out and "видно" in out, name

    def test_hidden_until_end_dropped(self) -> None:
        s = ChannelStripper()
        assert s.feed(CH_OPEN + "thoughtбесконечный анализ") == ""
        assert s.flush() == ""

    def test_closer_token_restores_visible(self) -> None:
        s = ChannelStripper()
        out = s.feed(CH_OPEN + "thoughtскрыто" + CH_END + "видно")
        assert "скрыто" not in out and "видно" in out

    def test_bare_special_tokens_stripped(self) -> None:
        s = ChannelStripper()
        assert s.feed("привет" + TOK_STOP + "мир") == "приветмир"

    def test_marker_split_across_chunks(self) -> None:
        s = ChannelStripper()
        assert s.feed("ок" + CH_OPEN[:3]) == "ок"   # «<|c» удержано как возможный маркер
        assert s.feed(CH_OPEN[3:] + "thought") == ""  # маркер собран, канал скрытый
        assert s.feed("думу думает") == ""            # всё ещё скрыто
        assert s.feed(CH_OPEN + "final") == ""
        assert s.feed("ответ") == "ответ"

    def test_channel_name_split_across_chunks(self) -> None:
        s = ChannelStripper()
        assert s.feed(CH_OPEN + "tho") == ""   # имя канала не доехало — ждём
        assert s.feed("ughtсекрет" + CH_END) == ""
        assert s.feed("чисто") == "чисто"

    def test_flush_releases_harmless_tail(self) -> None:
        s = ChannelStripper()
        out = s.feed("цензурный хвост с <")
        assert out == "цензурный хвост с "
        assert s.flush() == "<"  # честный «<» на конце потока отдаём, не едим


# --------------------------------------------------------------------------- #
# п.5 — интеграция с LLMClient (MockTransport, без сети)                      #
# --------------------------------------------------------------------------- #
def _profile(stream: bool):
    from lilith_core.config import ResolvedProfile

    return ResolvedProfile(
        name="chat", provider="openai_compatible", base_url="http://127.0.0.1:1/v1",
        model="test-model", temperature=0.7, max_tokens=64, top_p=0.95,
        request_timeout_sec=5.0, max_retries=0, stream=stream,
        history_max_messages=40, api_key_env="", note="гвард 0.7.1",
    )


MESSAGES = [{"role": "user", "content": "привет"}]


def _sse(lines: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = "".join(f"data: {line}\n\n" for line in lines) + "data: [DONE]\n\n"
        return httpx.Response(200, text=payload, headers={"Content-Type": "text/event-stream"})

    return handler


def _json(body: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    return handler


@pytest.mark.asyncio
class TestStripperInClient:
    async def test_stream_thought_channel_hidden(self) -> None:
        lines = [
            json.dumps({"choices": [{"delta": {"content": CH_OPEN + "thought"}}]}),
            json.dumps({"choices": [{"delta": {"content": "так, думаю о вечном"}}]}),
            json.dumps({"choices": [{"delta": {"content": CH_OPEN + "final"}}]}),
            json.dumps({"choices": [{"delta": {"content": "Привет, Кирюша!"}}]}),
            json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        ]
        client = LLMClient(transport=httpx.MockTransport(_sse(lines)))
        tokens: list[str] = []

        async def on_token(piece: str) -> None:
            tokens.append(piece)

        result = await client.complete(_profile(stream=True), MESSAGES, on_token=on_token)
        assert "думаю о вечном" not in result.text
        assert "Привет, Кирюша!" in result.text
        assert CH_OPEN not in result.text and "<|" not in result.text
        assert "".join(tokens) == result.text  # колбэк получает то же, что и итог

    async def test_nonstream_content_cleaned(self) -> None:
        body = {"choices": [{"message": {"content": CH_OPEN + "thoughtанализ" + CH_END + "Ответ."},
                             "finish_reason": "stop"}]}
        client = LLMClient(transport=httpx.MockTransport(_json(body)))
        result = await client.complete(_profile(stream=False), MESSAGES)
        assert result.text == "Ответ."

    async def test_flag_off_keeps_raw(self, tmp_project, monkeypatch) -> None:
        """brain.strip_channel_tokens: false — сырой контент без стрижа (escape hatch)."""
        from lilith_core.config import load_settings, reset_settings_cache

        config = tmp_project / "config" / "config.yaml"
        config.write_text(
            "brain:\n  strip_channel_tokens: false\n  profiles:\n"
            "    chat:\n      model: m\n      base_url: http://127.0.0.1:1/v1\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("LILITH_CONFIG", str(config))
        reset_settings_cache()
        load_settings(config, env_file=None)  # прогреваем кэш get_settings
        body = {"choices": [{"message": {"content": CH_OPEN + "thoughtсырьё"}, "finish_reason": "stop"}]}
        client = LLMClient(transport=httpx.MockTransport(_json(body)))
        result = await client.complete(_profile(stream=False), MESSAGES)
        assert "thought" in result.text and "сырьё" in result.text
