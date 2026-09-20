#!/usr/bin/env python3
"""Собирает текстовый «бандл» этапа 6 — резервный канал передачи, если zip не проходит.

Зачем: Кирюша не может прикрепить архив в чат (клиент не принимает .zip), а
файлообменники из песочницы отдают HTML вместо файла. Этот скрипт упаковывает
**текстовые** артефакты этапа 6 (unity-client/, ключевые Python-модули, доки)
в один Markdown-файл с base64-блоками. Его можно открыть, скопировать кусками
в чат — и собрать обратно на машине Кирюши.

Запуск (в песочнице)::

    python3 scripts/make_bundle.py                 # → ../HANDOFF_BUNDLE_stage6.md
    python3 scripts/make_bundle.py --only unity    # только unity-client/

Сборка обратно (у Кирюши)::

    python unpack_bundle.py HANDOFF_BUNDLE_stage6.md --out E:\\lilith-core\\lilith\\Локальная Лилит

Что входит: всё, что нельзя восстановить из архива этапов 1–5, то есть новые файлы
этапа 6. Бинарщина (``test_cube.vrm``, картинки) не пакуется — VRM генерируется
командой ``python scripts/make_test_vrm.py`` прямо на месте.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: Наборы файлов для бандла (относительно корня проекта).
SETS: dict[str, tuple[str, ...]] = {
    "unity": (
        "unity-client/**/*",
    ),
    "server": (
        "src/lilith_core/voice/pcm.py",
        "src/lilith_core/face/ws_frames.py",
        "src/lilith_core/face/producer.py",
        "src/lilith_core/face/endpoints.py",
        "src/lilith_core/face/lora.py",
        "src/lilith_core/face/group.py",
        "src/lilith_core/face/personas.py",
        "src/lilith_core/face/__init__.py",
        "src/lilith_core/app.py",
        "src/lilith_core/config.py",
        "src/lilith_core/protocol.py",
        "src/lilith_core/memory/journal.py",
        "src/lilith_core/memory/__init__.py",
        "src/lilith_core/__init__.py",
        "src/lilith_core/webui/index.html",
        "config/config.yaml",
    ),
    "scripts": (
        "scripts/unity_face_probe.py",
        "scripts/make_test_vrm.py",
        "scripts/build_stage_archive.py",
    ),
    "tests": (
        "tests/test_face_producer.py",
        "tests/test_vrm_sample_and_probe.py",
        "tests/test_structure.py",
    ),
    "docs": (
        "README.md",
        "CHANGELOG.md",
        "STAGE6_REPORT.md",
        ".editorconfig",
        ".gitignore",
        "personas/README.md",
        "personas/lilith/card.yaml",
        "personas/lilith/voice.yaml",
        "personas/lilith/face.yaml",
        "personas/_template/card.yaml",
        "personas/_template/voice.yaml",
        "personas/_template/face.yaml",
        "personas/_template/persona.md",
        "personas/_template/README.md",
        "docs/ARCHITECTURE.md",
        "docs/DECISIONS.md",
        "docs/NEURONA_NOTES.md",
        "artifacts/README.md",
    ),
}


def collect(only: str | None) -> list[Path]:
    """Собрать список файлов по выбранным наборам."""
    keys = [only] if only else list(SETS)
    files: list[Path] = []
    seen: set[Path] = set()
    for key in keys:
        for pattern in SETS[key]:
            for path in sorted(PROJECT_ROOT.glob(pattern)):
                if path.is_file() and path not in seen:
                    seen.add(path)
                    files.append(path)
    return files


def build(out_path: Path, only: str | None) -> None:
    """Собрать бандл и записать его в Markdown."""
    files = collect(only)
    if not files:
        raise SystemExit("не нашлось ни одного файла — проверь наборы SETS")

    lines = [
        "# 📦 HANDOFF BUNDLE · LILITH-CORE этап 6 (v0.6.0)",
        "",
        "Текстовый бандл: все новые файлы этапа 6 в base64-блоках.",
        "Собирается обратно скриптом `unpack_bundle.py` (он в конце этого файла).",
        "",
        f"Файлов: **{len(files)}** · корень распаковки: `Локальная Лилит/`",
        "",
        "| # | Файл | Байт | sha256 (8) |",
        "|---|---|---|---|",
    ]
    payloads: list[tuple[str, bytes]] = []
    for path in files:
        blob = path.read_bytes()
        payloads.append((path.relative_to(PROJECT_ROOT).as_posix(), blob))
        lines.append(
            f"| {len(payloads)} | `{path.relative_to(PROJECT_ROOT).as_posix()}` "
            f"| {len(blob):,} | `{hashlib.sha256(blob).hexdigest()[:8]}` |"
        )

    lines += ["", "---", ""]
    for name, blob in payloads:
        encoded = base64.b64encode(blob).decode("ascii")
        lines += [
            f"## FILE: {name}",
            f"<!-- bytes={len(blob)} sha256={hashlib.sha256(blob).hexdigest()} -->",
            "```b64",
            encoded,
            "```",
            "",
        ]

    lines += ["---", "", "## Скрипт сборки `unpack_bundle.py`", "",
              "Сохрани этот файл рядом с бандлом и запусти:", "",
              "```bat", "python unpack_bundle.py HANDOFF_BUNDLE_stage6.md --out \"E:\\lilith-core\\lilith\\Локальная Лилит\"", "```", "",
              "```python", UNPACK_SCRIPT, "```", ""]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    size_kb = out_path.stat().st_size / 1024
    print(f"[OK] {out_path}")
    print(f"     файлов: {len(files)}, размер бандла: {size_kb:.1f} КБ")


UNPACK_SCRIPT = '''#!/usr/bin/env python3
"""Собирает файлы из HANDOFF_BUNDLE_stage6.md (base64-блоки между ```b64)."""

from __future__ import annotations

import argparse
import base64
import hashlib
import re
from pathlib import Path

BLOCK = re.compile(r"^## FILE: (.+?)\\n<!-- bytes=(\\d+) sha256=([0-9a-f]+) -->\\n```b64\\n(.*?)\\n```", re.S | re.M)


def main() -> None:
    parser = argparse.ArgumentParser(description="Распаковка текстового бандла LILITH-CORE")
    parser.add_argument("bundle", help="путь к HANDOFF_BUNDLE_stage6.md")
    parser.add_argument("--out", default=".", help="корень распаковки (папка проекта)")
    parser.add_argument("--dry-run", action="store_true", help="только показать, что будет создано")
    args = parser.parse_args()

    text = Path(args.bundle).read_text(encoding="utf-8")
    out_root = Path(args.out).expanduser()
    found = 0
    bad = []

    for match in BLOCK.finditer(text):
        name, size, digest, encoded = match.groups()
        blob = base64.b64decode(encoded.strip())
        found += 1
        if len(blob) != int(size) or hashlib.sha256(blob).hexdigest() != digest:
            bad.append(name)
            continue
        target = out_root / name
        if args.dry_run:
            print(f"  {target}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        print(f"  [OK] {name} ({len(blob):,} Б)")

    print(f"\\nФайлов: {found}, ошибок: {len(bad)}")
    if bad:
        raise SystemExit("повреждены (блок обрезан при копировании?): " + ", ".join(bad))


if __name__ == "__main__":
    main()
'''


def main() -> None:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(description="Сборка текстового бандла этапа 6")
    parser.add_argument("--only", choices=sorted(SETS), default=None, help="только один набор файлов")
    parser.add_argument("--out", default=None, help="куда сохранить (по умолчанию ../HANDOFF_BUNDLE_stage6.md)")
    args = parser.parse_args()

    out_path = Path(args.out) if args.out else PROJECT_ROOT.parent.parent / "HANDOFF_BUNDLE_stage6.md"
    build(out_path, args.only)


if __name__ == "__main__":
    main()
