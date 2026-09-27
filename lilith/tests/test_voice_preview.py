"""Гварды scripts/voice_preview.py (ADR-027 §4): выбор и усыновление «души».

Тяжёлые импорты (torch/faster_qwen3_tts) в скрипте ленивые — здесь проверяем
CLI-контракт и поток усыновления на синтетическом preview-каталоге, без GPU.
build_parser() вынесен из скрипта ровно для этого (принцип 0.6.6).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

from lilith_core.voice.stt import write_wav

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "voice_preview.py"


@pytest.fixture(scope="module")
def vp():
    spec = importlib.util.spec_from_file_location("lilith_voice_preview", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestParser:
    def test_defaults(self, vp) -> None:
        args = vp.build_parser().parse_args([])
        assert args.persona == "lilith"
        assert args.count == 12  # ТЗ: 10-15 вариаций
        assert args.language == "Russian"
        assert "1.7B-VoiceDesign" in args.model  # превью — офлайн на 1.7B
        assert "0.6B-Base" in args.clone_model   # контрольный клон — на резиденте
        assert args.adopt is None and args.random_seeds is False

    def test_full_cli(self, vp) -> None:
        args = vp.build_parser().parse_args(
            ["--persona", "olya", "--count", "15", "--random-seeds",
             "--instruct", "нежный женский, анимешный, с придыханием",
             "--adopt", "7", "--adopt-dir", "voices/olya", "--verify-clone"]
        )
        assert args.persona == "olya" and args.count == 15 and args.random_seeds
        assert args.instruct.startswith("нежный") and args.adopt == 7
        assert args.adopt_dir == "voices/olya" and args.verify_clone

    def test_presets_for_both_personas(self, vp) -> None:
        assert set(vp.PRESET_INSTRUCTS) >= {"lilith", "olya"}
        assert all(isinstance(v, str) and v for v in vp.PRESET_INSTRUCTS.values())

    def test_default_text_long_enough_for_reference(self, vp) -> None:
        # референс должен получаться 5-10+ секунд: дефолтный текст — не короче ~100 знаков
        assert len(vp.DEFAULT_TEXT) >= 100


def make_preview_dir(
    root: Path, persona: str, stamp: str, variants: int = 3, text: str = "Тестовая фраза души."
) -> Path:
    preview = root / f"{persona}_{stamp}"
    preview.mkdir(parents=True)
    entries = []
    for i in range(1, variants + 1):
        fname = f"preview_{i:02d}_seed{1000 * i}.wav"
        (preview / fname).write_bytes(write_wav(b"\x00\x00" * 4800, 24000))
        entries.append({"n": i, "file": fname, "seed": 1000 * i,
                        "audio_sec": 0.2, "gen_sec": 0.1, "rtf": 2.0})
    manifest = {
        "persona": persona, "created": "2026-09-27T00:00:00",
        "model": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign", "backend": "torch",
        "instruct": "тестовая душа", "text": text, "language": "Russian",
        "seed_base": 1000, "random_seeds": False, "variants": entries,
    }
    (preview / "manifest.yaml").write_text(yaml.safe_dump(manifest, allow_unicode=True), encoding="utf-8")
    return preview


class TestAdopt:
    def test_adopt_into_personas_voice_dir(self, vp, tmp_project: Path) -> None:
        """ADR-027 §4: душа ложится в personas/<id>/voice/ (reference.wav+txt+soul.yaml)."""
        preview = make_preview_dir(tmp_project / "voices" / "preview", "lilith", "20260927_000001")
        vp.main(["--persona", "lilith", "--adopt", "2"])
        soul_dir = tmp_project / "personas" / "lilith" / "voice"
        assert (soul_dir / "reference.wav").is_file()
        assert (soul_dir / "reference.txt").read_text(encoding="utf-8").strip() == "Тестовая фраза души."
        soul = yaml.safe_load((soul_dir / "soul.yaml").read_text(encoding="utf-8"))
        assert soul["seed"] == 2000 and soul["variant_n"] == 2
        assert Path(soul["source_preview"]).name == preview.name  # путь хранится как в CLI (относительный)
        assert soul["instruct"] == "тестовая душа"
        # reference.wav = побайтовая копия выбранной вариации
        assert (soul_dir / "reference.wav").read_bytes() == (preview / "preview_02_seed2000.wav").read_bytes()

    def test_adopt_dir_override(self, vp, tmp_project: Path) -> None:
        make_preview_dir(tmp_project / "voices" / "preview", "olya", "20260927_000002")
        vp.main(["--persona", "olya", "--adopt", "1", "--adopt-dir", "voices/olya"])
        assert (tmp_project / "voices" / "olya" / "reference.wav").is_file()
        assert not (tmp_project / "personas" / "olya" / "voice").exists()

    def test_adopt_picks_latest_preview(self, vp, tmp_project: Path) -> None:
        root = tmp_project / "voices" / "preview"
        make_preview_dir(root, "lilith", "20260926_000000", text="старая душа")
        make_preview_dir(root, "lilith", "20260927_120000", text="свежая душа")
        vp.main(["--persona", "lilith", "--adopt", "1"])
        soul = yaml.safe_load(
            (tmp_project / "personas" / "lilith" / "voice" / "soul.yaml").read_text(encoding="utf-8")
        )
        assert soul["ref_text"] == "свежая душа"

    def test_adopt_unknown_variant_exits(self, vp, tmp_project: Path) -> None:
        make_preview_dir(tmp_project / "voices" / "preview", "lilith", "20260927_000003", variants=2)
        with pytest.raises(SystemExit, match="не найдена"):
            vp.main(["--persona", "lilith", "--adopt", "9"])

    def test_adopt_without_preview_exits(self, vp, tmp_project: Path) -> None:
        with pytest.raises(SystemExit, match="не найдена"):
            vp.main(["--persona", "ghost", "--adopt", "1"])

    def test_adopt_without_manifest_exits(self, vp, tmp_project: Path) -> None:
        (tmp_project / "voices" / "preview" / "lilith_empty").mkdir(parents=True)
        with pytest.raises(SystemExit, match="нечего усыновлять"):
            vp.main(["--persona", "lilith", "--adopt", "1"])
