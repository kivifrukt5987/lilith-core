"""Общие фикстуры pytest для LILITH-CORE."""

from __future__ import annotations

import tempfile
import textwrap
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lilith_core.app import create_app
from lilith_core.config import Settings, load_settings, reset_settings_cache



@pytest.fixture(autouse=True)
def _clean_settings_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Каждый тест получает свежий кэш настроек и чистое окружение.

    ``load_settings`` может записать ``LILITH_CONFIG`` в ``os.environ`` — без этой
    чистки один тест начал бы влиять на другой.
    """
    monkeypatch.delenv("LILITH_CONFIG", raising=False)
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture(autouse=True)
def _isolate_loguru() -> Iterator[None]:
    """Логирование в тестах всегда изолировано: пишется во временный каталог pytest.

    Без этого ``setup_logging`` из ``create_app`` писал бы в ``logs/`` репозитория
    и утаскивал бы за собой конфиг предыдущего теста.
    """
    from loguru import logger

    from lilith_core.config import LoggingSettings
    from lilith_core.logging_setup import setup_logging

    tmp_dir = Path(tempfile.mkdtemp(prefix="lilith-logs-"))
    setup_logging(LoggingSettings(dir=str(tmp_dir), console=False, level="WARNING"), force=True)
    yield
    logger.remove()
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture
def tmp_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Временная «рабочая директория проекта» с конфигом и .env."""
    (tmp_path / "config").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def write_yaml(path: Path, data: dict) -> Path:
    """Записывает YAML-конфиг и возвращает путь к нему."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


@pytest.fixture
def sample_config(tmp_project: Path) -> Path:
    """Типовой конфиг для тестов."""
    return write_yaml(
        tmp_project / "config" / "config.yaml",
        {
            "app": {"user_name": "Тестовый Кирюша", "persona_name": "ТестЛилит", "debug": True},
            "server": {"port": 9999},
            "logging": {"level": "DEBUG", "console": False, "dir": str(tmp_project / "logs")},
            "brain": {
                "default_profile": "chat",
                "profiles": {
                    "chat": {"model": "test-model", "base_url": "http://127.0.0.1:1/v1"},
                    "coder": {"base_url": "http://127.0.0.1:2/v1", "temperature": 0.2},
                },
            },
            "features": {"echo_mode": True, "brain_enabled": False},
        },
    )


@pytest.fixture
def settings(sample_config: Path) -> Settings:
    """Настройки, загруженные из тестового конфига."""
    return load_settings(sample_config, env_file=None)


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    """Приложение на тестовых настройках."""
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """TestClient с запущенным lifespan (инициализирует состояние приложения)."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def persona_file(tmp_project: Path) -> Path:
    """Временный persona.md с плейсхолдерами."""
    path = tmp_project / "persona.md"
    path.write_text(
        textwrap.dedent(
            """\
            Ты — $persona_name. Пользователя зовут $user_name.
            Сейчас $weekday, $time. Сборка v$version, этап $stage.
            Неизвестный $placeholder_ostavlen_kak_est.
            """
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def memory_settings(sample_config, tmp_project):
    """Настройки с включёнными мозгом (mock) и памятью в_temp-каталогах."""
    settings = load_settings(sample_config, env_file=None)
    settings.features.brain_enabled = True
    settings.brain.defaults.provider = "mock"
    settings.features.memory_enabled = True
    settings.memory.db_path = str(tmp_project / "data" / "test.db")
    settings.memory.chroma_path = str(tmp_project / "data" / "chroma")
    settings.memory.runtime_settings_path = str(tmp_project / "data" / "runtime.json")
    settings.memory.summarize_every_n = 40
    settings.memory.auto_summarize = True
    return settings
