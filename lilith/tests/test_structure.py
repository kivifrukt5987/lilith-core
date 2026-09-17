"""Тесты структуры проекта: всё на месте, ничего лишнего в пакет не попало."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = PROJECT_ROOT / "src" / "lilith_core"


class TestProjectLayout:
    """Каркас репозитория."""

    @pytest.mark.parametrize(
        "relative",
        [
            "README.md",
            "pyproject.toml",
            "requirements.txt",
            ".env.example",
            ".gitignore",
            "start.bat",
            "run_tests.bat",
            "config/config.yaml",
            "docs/ARCHITECTURE.md",
            "CHANGELOG.md",
            "STAGE1_REPORT.md",
            "ПРОЧТИ_МЕНЯ.txt",
            "scripts/ws_client.py",
            "scripts/build_stage_archive.py",
            "tests/conftest.py",
        ],
    )
    def test_file_exists(self, relative: str) -> None:
        assert (PROJECT_ROOT / relative).is_file(), f"не найден {relative}"

    @pytest.mark.parametrize(
        "relative",
        [
            "src/lilith_core/__init__.py",
            "src/lilith_core/__main__.py",
            "src/lilith_core/app.py",
            "src/lilith_core/config.py",
            "src/lilith_core/echo.py",
            "src/lilith_core/logging_setup.py",
            "src/lilith_core/persona/__init__.py",
            "src/lilith_core/persona/persona.md",
            "src/lilith_core/protocol.py",
            "src/lilith_core/run.py",
            "src/lilith_core/session.py",
            "src/lilith_core/webui/index.html",
        ],
    )
    def test_module_exists(self, relative: str) -> None:
        assert (PROJECT_ROOT / relative).is_file(), f"не найден {relative}"


class TestImports:
    """Все модули импортируются без побочных эффектов."""

    @pytest.mark.parametrize(
        "module",
        [
            "lilith_core",
            "lilith_core.config",
            "lilith_core.logging_setup",
            "lilith_core.protocol",
            "lilith_core.session",
            "lilith_core.echo",
            "lilith_core.persona",
            "lilith_core.app",
            "lilith_core.run",
        ],
    )
    def test_importable(self, module: str) -> None:
        import importlib

        assert importlib.import_module(module) is not None

    def test_version_and_stage(self) -> None:
        import lilith_core

        assert re.match(r"^\d+\.\d+\.\d+$", lilith_core.__version__)
        assert lilith_core.__stage__ == 5
        assert lilith_core.__codename__ == "LILITH.EXE"


class TestPackageHygiene:
    """В исходниках не должно быть мусора и секретов."""

    def _py_files(self) -> list[Path]:
        return sorted(PACKAGE_DIR.rglob("*.py"))

    def test_no_compiled_files_committed(self) -> None:
        """`.pyc` в дереве пакета быть не должно (кэш `__pycache__` создаётся при прогоне)."""
        compiled = [str(x) for x in PACKAGE_DIR.rglob("*.pyc") if "__pycache__" not in x.parts]
        assert compiled == []

    def test_every_module_has_docstring(self) -> None:
        import ast

        without = []
        for path in self._py_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            if not ast.get_docstring(tree):
                without.append(path.name)
        assert without == [], f"модули без докстринга: {without}"

    def test_future_annotations_everywhere(self) -> None:
        missing = [
            path.name
            for path in self._py_files()
            if "from __future__ import annotations" not in path.read_text(encoding="utf-8")
        ]
        assert missing == [], f"нет `from __future__ import annotations`: {missing}"

    def test_no_print_in_package(self) -> None:
        offenders = []
        for path in self._py_files():
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("print(") and "# noqa" not in stripped:
                    offenders.append(f"{path.name}:{lineno}")
        assert offenders == [], f"print() вместо loguru: {offenders}"

    def test_no_hardcoded_secrets(self) -> None:
        pattern = re.compile(r"(sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,})")
        hits = []
        for path in list(PACKAGE_DIR.rglob("*")) + [PROJECT_ROOT / "config" / "config.yaml"]:
            if path.is_file() and path.suffix in {".py", ".md", ".yaml", ".yml", ".html", ".txt"}:
                if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                    hits.append(path.name)
        assert hits == [], f"похоже на секрет в файлах: {hits}"

    def test_webui_has_no_external_urls(self) -> None:
        html = (PACKAGE_DIR / "webui" / "index.html").read_text(encoding="utf-8")
        for bad in ("http://cdn", "https://cdn", "googleapis", "jsdelivr", "unpkg"):
            assert bad not in html, f"веб-панель тянет внешний ресурс: {bad}"

    def test_webui_is_single_file(self) -> None:
        assert (PACKAGE_DIR / "webui" / "index.html").stat().st_size > 5000

    def test_webui_has_token_counter(self) -> None:
        html = (PACKAGE_DIR / "webui" / "index.html").read_text(encoding="utf-8")
        assert 'id="tokens"' in html
        assert "/api/brain/stats" in html

    def test_webui_has_profile_selector(self) -> None:
        html = (PACKAGE_DIR / "webui" / "index.html").read_text(encoding="utf-8")
        assert 'id="profile"' in html
        assert "/api/brain/profiles" in html

    def test_env_example_has_no_real_values(self) -> None:
        content = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                assert value.strip() == "", f"в .env.example заполнено значение: {key}"

    def test_gitignore_covers_env_and_logs(self) -> None:
        content = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        for entry in (".env", "logs/", "data/", ".venv/", "__pycache__/"):
            assert entry.rstrip("/") in content, f"в .gitignore нет {entry}"


class TestConfigCompleteness:
    """В конфиге уже заложены секции всех восьми этапов."""

    def test_all_sections_present_in_yaml(self) -> None:
        import yaml

        data = yaml.safe_load((PROJECT_ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
        for section in ("app", "server", "logging", "brain", "memory", "voice",
                        "face", "hands", "bridges", "stream", "features"):
            assert section in data, f"в config.yaml нет секции {section}"

    def test_yaml_matches_settings_model(self) -> None:
        """Каждый ключ YAML должен существовать в pydantic-модели (extra='forbid')."""
        from lilith_core.config import Settings, load_settings

        settings = load_settings(PROJECT_ROOT / "config" / "config.yaml", env_file=None)
        assert isinstance(settings, Settings)
        assert settings.app.user_name  # конфиг прочитался

    def test_confirm_timeout_is_25_seconds(self) -> None:
        """Требование плана: баннер подтверждения с 25-секундным отсчётом."""
        from lilith_core.config import load_settings

        settings = load_settings(PROJECT_ROOT / "config" / "config.yaml", env_file=None)
        assert settings.hands.confirm_timeout_sec == pytest.approx(25.0)

    def test_persona_path_resolves(self) -> None:
        from lilith_core.config import load_settings

        settings = load_settings(PROJECT_ROOT / "config" / "config.yaml", env_file=None)
        assert settings.persona_file.is_file()


class TestReadme:
    """README описывает запуск на Windows: venv, конфиг, старт."""

    def test_readme_mentions_windows_steps(self) -> None:
        text = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        for needle in ("py -3.11 -m venv .venv", ".venv\\Scripts\\activate",
                       "pip install -e .", "copy .env.example .env",
                       "python -m lilith_core.run", "start.bat", "pytest"):
            assert needle in text, f"в README нет инструкции: {needle}"

    def test_readme_documents_all_stages(self) -> None:
        text = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        for stage in range(1, 9):
            assert f"| {stage} |" in text or f"| **{stage}** |" in text, f"в README нет этапа {stage}"

    def test_readme_documents_ws_protocol(self) -> None:
        text = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        for needle in ('"type": "chat"', "hello", "ping", "pong", "error", "/healthz"):
            assert needle in text, f"в README не описано: {needle}"


class TestStageArchiveScript:
    """Скрипт сборки архива должен работать и не тащить секреты/мусор."""

    def test_build_archive_creates_zip(self, tmp_path: Path) -> None:
        import sys
        import zipfile

        sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
        try:
            from build_stage_archive import build_archive
        finally:
            sys.path.pop(0)

        out = build_archive(stage=1, out_dir=tmp_path, day="2026-01-01")
        assert out.is_file()
        assert out.name == "LILITH-CORE_stage1_2026-01-01.zip"

        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()

        assert any(n.endswith("src/lilith_core/app.py") for n in names)
        assert any(n.endswith("webui/index.html") for n in names)
        assert any(n.endswith("persona/persona.md") for n in names)
        assert any(n.endswith("README.md") for n in names)

    def test_archive_excludes_secrets_and_junk(self, tmp_path: Path) -> None:
        import sys
        import zipfile

        sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
        try:
            from build_stage_archive import build_archive
        finally:
            sys.path.pop(0)

        out = build_archive(stage=1, out_dir=tmp_path, day="2026-01-02")
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()

        assert not any(n.endswith("/.env") for n in names), "в архив попал .env"
        assert not any(n.endswith(".log") for n in names), "в архив попал лог"
        assert not any("__pycache__" in n for n in names), "в архив попал __pycache__"
        assert not any(n.endswith(".zip") for n in names), "архив внутри архива"
        assert not any("/.venv/" in n for n in names), "в архив попало окружение"


class TestBatchFiles:
    """Windows .bat обязаны быть CRLF и чисто ASCII: cmd.exe не прощает иное.

    Регрессия этапа 1 (v0.1.0): bat с LF-переводами и кириллицей в UTF-8
    разваливался на «'b' is not recognized...» и уводил pip в чужую папку.
    """

    @pytest.mark.parametrize("name", ["start.bat", "run_tests.bat"])
    def test_crlf_line_endings(self, name: str) -> None:
        data = (PROJECT_ROOT / name).read_bytes()
        assert b"\r\n" in data, f"{name}: нет CRLF-переводов"
        assert b"\n" not in data.replace(b"\r\n", b""), f"{name}: найден одиночный LF"

    @pytest.mark.parametrize("name", ["start.bat", "run_tests.py".replace(".py", ".bat")])
    def test_ascii_only(self, name: str) -> None:
        data = (PROJECT_ROOT / name).read_bytes()
        data.decode("ascii")  # бросит UnicodeDecodeError на любом не-ASCII байте

    @pytest.mark.parametrize("name", ["start.bat", "run_tests.bat"])
    def test_changes_to_own_directory(self, name: str) -> None:
        text = (PROJECT_ROOT / name).read_text(encoding="ascii")
        assert 'cd /d "%~dp0"' in text, f"{name}: не встаёт в свою папку"

    def test_start_bat_guards_project_root(self) -> None:
        text = (PROJECT_ROOT / "start.bat").read_text(encoding="ascii")
        assert 'if not exist "pyproject.toml"' in text
        assert "pause" in text  # консоль не должна захлопываться до чтения ошибки

    def test_run_tests_bat_returns_code(self) -> None:
        text = (PROJECT_ROOT / "run_tests.bat").read_text(encoding="ascii")
        assert "exit /b %RC%" in text

    def test_start_bat_opens_browser(self) -> None:
        """One-click режим: панель открывается сама."""
        text = (PROJECT_ROOT / "start.bat").read_text(encoding="ascii")
        assert 'start "" "http://127.0.0.1:8765/"' in text

    def test_start_bat_selfchecks_import(self) -> None:
        """После установки скрипт обязан убедиться, что пакет импортируется."""
        text = (PROJECT_ROOT / "start.bat").read_text(encoding="ascii")
        assert text.count('import lilith_core') >= 2  # до установки и после

    def test_start_bat_python_switch_outside_blocks(self) -> None:
        """Переключение PYTHON на .venv - строкой верхнего уровня, ДО любого использования.

        Регрессия 0.1.1: %VAR%, заданный внутри if ( ... ), раскрывается в том же
        блоке ещё старым значением - pip ставил пакеты не в тот интерпретатор.
        """
        lines = (PROJECT_ROOT / "start.bat").read_text(encoding="ascii").splitlines()
        switch = next(
            i for i, ln in enumerate(lines)
            if ln.strip() == 'set "PYTHON=.venv\Scripts\python.exe"'
        )
        assert not lines[switch].startswith(" "), "set PYTHON обязан быть верхним уровнем"
        venv = next(i for i, ln in enumerate(lines) if "-m venv .venv" in ln)
        assert venv < switch, "сначала создаём .venv, потом переключаемся на него"
        for ln in lines[:switch]:
            assert "%PYTHON%" not in ln, "PYTHON используется раньше, чем назначен"

    def test_start_bat_quotes_extras(self) -> None:
        """.[dev] в кавычках: иначе PowerShell/некоторые cmd съедают скобки."""
        text = (PROJECT_ROOT / "start.bat").read_text(encoding="ascii")
        assert '-m pip install -e ".[dev,memory]"' in text


class TestMemoryLayerStructure:
    """Этап 3: память присутствует в пакете, конфиге и панели."""

    @pytest.mark.parametrize(
        "relative",
        [
            "src/lilith_core/memory/__init__.py",
            "src/lilith_core/memory/journal.py",
            "src/lilith_core/memory/rag.py",
            "src/lilith_core/memory/summarizer.py",
            "src/lilith_core/memory/runtime.py",
        ],
    )
    def test_memory_modules_exist(self, relative: str) -> None:
        assert (PROJECT_ROOT / relative).is_file()

    def test_config_has_runtime_memory_fields(self) -> None:
        import yaml

        data = yaml.safe_load((PROJECT_ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
        assert data["memory"]["auto_summarize"] is True
        assert data["memory"]["summarize_every_n"] == 40
        assert data["memory"]["runtime_settings_path"]
        assert data["memory"]["embedding_model"] == "hash"

    def test_webui_has_vrm_viewer_and_vendor(self) -> None:
        """Этап 5 v2: вьювер и локальный vendor (офлайн-требование панели)."""
        html = (PACKAGE_DIR / "webui" / "index.html").read_text(encoding="utf-8")
        assert "/ui/vrm_viewer.js" in html
        assert "importmap" in html
        assert (PACKAGE_DIR / "webui" / "vrm_viewer.js").is_file()
        vendor = PACKAGE_DIR / "webui" / "vendor"
        assert (vendor / "three.module.js").stat().st_size > 100_000
        assert (vendor / "three-vrm.module.js").stat().st_size > 100_000
        assert (vendor / "GLTFLoader.js").stat().st_size > 10_000

    def test_personas_registry_present(self) -> None:
        assert (PROJECT_ROOT / "personas" / "lilith" / "fallback.jpg").is_file()
        assert (PROJECT_ROOT / "personas" / "lilith" / "persona.md").is_file()

    def test_webui_has_memory_drawer(self) -> None:
        html = (PACKAGE_DIR / "webui" / "index.html").read_text(encoding="utf-8")
        assert 'id="memdraw"' in html
        assert 'id="memN"' in html
        assert "/api/memory/settings" in html

    def test_run_has_with_memory_flag(self) -> None:
        text = (PROJECT_ROOT / "src" / "lilith_core" / "run.py").read_text(encoding="utf-8")
        assert "--with-memory" in text

    def test_aiosqlite_is_core_dependency(self) -> None:
        """Журнал памяти импортится на старте ядра — aiosqlite обязан быть в базе.

        Регрессия 0.3.0: aiosqlite жил только в экстре [memory], из-за чего
        сервер падал на импорте памяти на чистой машине.
        """
        import re

        text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r"^dependencies = \[(.*?)^\]", text, re.S | re.M)
        assert match, "не найдена секция dependencies"
        assert "aiosqlite" in match.group(1)

    def test_start_bat_installs_memory_extra(self) -> None:
        text = (PROJECT_ROOT / "start.bat").read_text(encoding="ascii")
        assert ".[dev,memory]" in text

    @pytest.mark.parametrize("name", ["start.bat", "run_tests.bat"])
    def test_bat_echo_has_no_raw_parens(self, name: str) -> None:
        """Регрессия 0.4.1: echo с "(" внутри if-блока рвал парсинг всего блока.

        Правило: в echo-строках bat круглые скобки только экранированные ^(^).
        """
        import re

        text = (PROJECT_ROOT / name).read_text(encoding="ascii")
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped.lower().startswith("echo"):
                continue
            raw = re.sub(r"\^\(|\^\)", "", stripped)  # убираем экранированные
            assert "(" not in raw and ")" not in raw, (
                f"{name}:{lineno}: неэкранированные скобки в echo: {stripped}"
            )

    @pytest.mark.parametrize("name", ["start.bat", "run_tests.bat"])
    def test_bat_window_never_self_closes(self, name: str) -> None:
        """Регрессия 0.4.0: окно cmd гасло раньше, чем человек читал ошибку.

        Страховка: bat перезапускает себя под cmd /k - сессия остаётся открытой
        при любом исходе, весь вывод виден на экране.
        """
        text = (PROJECT_ROOT / name).read_text(encoding="ascii")
        assert "cmd /k" in text
        assert "LILITH_KEEP" in text


class TestShippingDefaults:
    """Поставляемый конфиг: демо-слои включены из коробки (хотфикс 0.5.1)."""

    def test_config_enables_demo_layers(self) -> None:
        import yaml

        data = yaml.safe_load((PROJECT_ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
        assert data["features"]["memory_enabled"] is True
        assert data["features"]["voice_enabled"] is True
        assert data["features"]["face_enabled"] is True
        assert data["features"]["brain_enabled"] is False  # мозг ждёт свою модель

    def test_run_has_friendly_missing_dependency_hint(self) -> None:
        text = (PROJECT_ROOT / "src" / "lilith_core" / "run.py").read_text(encoding="utf-8")
        assert "не тем питоном" in text
        assert ".[dev,memory,voice]" in text
