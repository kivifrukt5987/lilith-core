"""Гварды scripts/voice_bakeoff.py (ревизия ADR-027, Q-B): CLI-контракт, фразы-ловушки,
манифест, слепая раскладка, честные skip/fail кандидатов.

Движки и GPU не нужны: адаптеры инжектятся фейками (ENGINE_ADAPTERS — точка подмены,
как _engine в ZeroShotTTS). Тяжёлые импорты в скрипте ленивые — контракт модуля.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from lilith_core.voice.stt import write_wav

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "voice_bakeoff.py"


@pytest.fixture(scope="module")
def vb():
    spec = importlib.util.spec_from_file_location("lilith_voice_bakeoff", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # dataclasses резолвит строковые аннотации через sys.modules — без регистрации
    # модуля @dataclass при загрузке из файла падает (AttributeError в _is_type).
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


def make_ref(tmp_path: Path, with_text: bool = True) -> Path:
    ref = tmp_path / "LilithGolos.wav"
    ref.write_bytes(write_wav(b"\x00\x00" * 4800, 24000))
    if with_text:
        ref.with_suffix(".txt").write_text("こんにちは、キリushа。", encoding="utf-8")
    return ref


class TestParser:
    def test_defaults(self, vb) -> None:
        args = vb.build_parser().parse_args([])
        assert args.engine == "all"
        assert args.ref == "voices/bakeoff/LilithGolos.wav"  # JP-референс из директивы Q-B
        assert args.blind is False and args.blind_only is False
        assert args.blind_seed == vb.DEFAULT_BLIND_SEED
        assert "Fun-CosyVoice3" in args.cosyvoice_model
        assert args.fish_checkpoints == "checkpoints/s2-pro"
        assert "xtts_v2" in args.xtts_model
        assert "0.6B-Base" in args.qwen3_model
        # полевой рецепт 08.10: прямая инициализация XTTS локальными весами
        assert args.xtts_model_path is None
        assert args.xtts_config_path is None and args.xtts_vocab_path is None

    def test_engine_choices_are_the_qb_candidates(self, vb) -> None:
        """Кандидаты — ровно из директивы Q-B: CosyVoice 3, Fish S2, XTTS v2, qwen3-контроль."""
        assert vb.ENGINE_ORDER == ["cosyvoice3", "fish", "xtts", "qwen3"]
        assert set(vb.ENGINE_ADAPTERS) == set(vb.ENGINE_ORDER)
        with pytest.raises(SystemExit):
            vb.build_parser().parse_args(["--engine", "rvc"])  # RVC — слой 2-й очереди (Q-C)


class TestPhrases:
    def test_four_categories_with_traps(self, vb) -> None:
        ids = [pid for pid, _ in vb.DEFAULT_PHRASES]
        assert ids == ["neutral", "emotional", "tongue_twister", "intimate"]
        text = " ".join(t for _, t in vb.DEFAULT_PHRASES)
        assert "Кирюша" in text          # ловушка ударения из полевого вердикта («КирюхА»)
        assert "Саша" in text and "сушк" in text  # скороговорка
        assert all(len(t) > 30 for _, t in vb.DEFAULT_PHRASES)
        assert len(set(ids)) == len(ids)

    def test_parse_text_file(self, vb, tmp_path: Path) -> None:
        tf = tmp_path / "phrases.txt"
        tf.write_text("# комментарий\n\nfirst|Первая фраза.\nВторая просто так.\n",
                      encoding="utf-8")
        parsed = vb.parse_text_file(tf)
        # id «lineN» = реальный номер строки в файле (комментарии/пустые считаются)
        assert parsed == [("first", "Первая фраза."), ("line4", "Вторая просто так.")]


class TestRefText:
    def test_sibling_txt(self, vb, tmp_path: Path) -> None:
        ref = make_ref(tmp_path)
        args = vb.build_parser().parse_args(["--ref", str(ref)])
        text, source = vb.resolve_ref_text(args, ref)
        assert text and text.startswith("こんにちは") and source.startswith("file:")

    def test_inline_override(self, vb, tmp_path: Path) -> None:
        ref = make_ref(tmp_path)
        args = vb.build_parser().parse_args(["--ref", str(ref), "--ref-text", "インライン"])
        text, source = vb.resolve_ref_text(args, ref)
        assert text == "インライン" and source == "inline"

    def test_missing(self, vb, tmp_path: Path) -> None:
        ref = make_ref(tmp_path, with_text=False)
        args = vb.build_parser().parse_args(["--ref", str(ref)])
        text, source = vb.resolve_ref_text(args, ref)
        assert text is None and source == "missing"


class TestWavDuration:
    def test_real_wav(self, vb, tmp_path: Path) -> None:
        path = tmp_path / "a.wav"
        path.write_bytes(write_wav(b"\x00\x00" * 24000, 24000))  # 1 секунда
        dur, sr = vb.wav_duration(path)
        assert sr == 24000 and dur == pytest.approx(1.0, abs=0.01)

    def test_garbage_returns_none(self, vb, tmp_path: Path) -> None:
        path = tmp_path / "bad.wav"
        path.write_bytes("не wav совсем".encode())
        dur, sr = vb.wav_duration(path)
        assert dur is None and sr is None


# --------------------------------------------------------------------------- #
# Выпечка на фейковых адаптерах                                               #
# --------------------------------------------------------------------------- #
def fake_ok_factory(engine_files: dict[str, list[str]]):
    """Фабрика фейк-адаптеров: пишет wav'ы и append'ит ctx.files как настоящий."""

    def make(pid_suffix: str):
        def adapter(ctx) -> None:
            engine = pid_suffix
            for pid, _text in ctx.phrases:
                out = ctx.run_dir / f"{engine}_{pid}.wav"
                out.write_bytes(write_wav(b"\x01\x00" * 2400, 24000))
                ctx.files.append({"engine": engine, "phrase_id": pid, "file": out.name,
                                  "sample_rate": 24000, "audio_sec": 0.1,
                                  "gen_sec": 0.05, "rtf": 2.0})
            ctx.engines[engine] = {"status": "ok", "note": "fake"}
        return adapter

    return {name: make(name) for name in engine_files}


class TestBake:
    def test_ok_skip_fail_and_manifest(self, vb, tmp_path: Path, monkeypatch) -> None:
        ref = make_ref(tmp_path)
        fakes = fake_ok_factory({"xtts": []})

        def broken_import(ctx) -> None:
            raise ImportError("нет движка в этом окружении")

        def broken_runtime(ctx) -> None:
            raise RuntimeError("OOM на 12GB (S2-Pro хочет 24GB)")

        monkeypatch.setitem(vb.ENGINE_ADAPTERS, "xtts", fakes["xtts"])
        monkeypatch.setitem(vb.ENGINE_ADAPTERS, "cosyvoice3", broken_import)
        monkeypatch.setitem(vb.ENGINE_ADAPTERS, "fish", broken_runtime)
        monkeypatch.setitem(vb.ENGINE_ADAPTERS, "qwen3", broken_import)

        code = vb.main(["--engine", "all", "--ref", str(ref),
                        "--run-dir", str(tmp_path / "run1")])
        assert code == 0  # хотя бы один испёкся — bake-off состоялся
        run = tmp_path / "run1"
        manifest = yaml.safe_load((run / "manifest.yaml").read_text(encoding="utf-8"))
        assert manifest["engines"]["xtts"]["status"] == "ok"
        assert manifest["engines"]["cosyvoice3"]["status"] == "skipped"
        assert "не установлен" in manifest["engines"]["cosyvoice3"]["reason"]
        assert manifest["engines"]["fish"]["status"] == "failed"
        assert "OOM" in manifest["engines"]["fish"]["reason"]
        files = manifest["files"]
        assert len(files) == 4  # xtts × 4 фразы
        assert {f["phrase_id"] for f in files} == {"neutral", "emotional",
                                                   "tongue_twister", "intimate"}
        assert all((run / f["file"]).is_file() for f in files)
        assert manifest["ref_text"] and manifest["phrases"][0]["id"] == "neutral"

    def test_all_failed_exit_code(self, vb, tmp_path: Path, monkeypatch) -> None:
        ref = make_ref(tmp_path)

        def nope(ctx) -> None:
            raise ImportError("nothing here")

        for engine in vb.ENGINE_ORDER:
            monkeypatch.setitem(vb.ENGINE_ADAPTERS, engine, nope)
        code = vb.main(["--engine", "all", "--ref", str(ref),
                        "--run-dir", str(tmp_path / "run2")])
        assert code == 1  # ни один кандидат не испёкся — красный выход

    def test_manifest_merge_on_rerun(self, vb, tmp_path: Path, monkeypatch) -> None:
        """Второй прогон в тот же run-dir (другое окружение) — манифест дополняется."""
        ref = make_ref(tmp_path)
        run = tmp_path / "run3"
        fakes = fake_ok_factory({"xtts": [], "qwen3": []})
        monkeypatch.setitem(vb.ENGINE_ADAPTERS, "xtts", fakes["xtts"])
        vb.main(["--engine", "xtts", "--ref", str(ref), "--run-dir", str(run)])
        monkeypatch.setitem(vb.ENGINE_ADAPTERS, "qwen3", fakes["qwen3"])
        vb.main(["--engine", "qwen3", "--ref", str(ref), "--run-dir", str(run)])
        manifest = yaml.safe_load((run / "manifest.yaml").read_text(encoding="utf-8"))
        assert set(manifest["engines"]) == {"xtts", "qwen3"}
        assert len(manifest["files"]) == 8  # 2 движка × 4 фразы, без дублей

    def test_missing_ref_exits(self, vb, tmp_path: Path) -> None:
        with pytest.raises(SystemExit, match="референс не найден"):
            vb.main(["--ref", str(tmp_path / "ghost.wav"),
                     "--run-dir", str(tmp_path / "run4")])


class TestBlind:
    def test_blind_layout_and_key(self, vb, tmp_path: Path, monkeypatch) -> None:
        ref = make_ref(tmp_path)
        run = tmp_path / "run5"
        fakes = fake_ok_factory({"xtts": [], "qwen3": []})
        monkeypatch.setitem(vb.ENGINE_ADAPTERS, "xtts", fakes["xtts"])
        monkeypatch.setitem(vb.ENGINE_ADAPTERS, "qwen3", fakes["qwen3"])
        vb.main(["--engine", "xtts", "--ref", str(ref), "--run-dir", str(run)])
        vb.main(["--engine", "qwen3", "--ref", str(ref), "--run-dir", str(run)])
        vb.main(["--blind-only", "--run-dir", str(run), "--blind-seed", "42"])

        blind = run / "blind"
        codes = sorted(p.stem for p in blind.glob("B*.wav"))
        assert codes == [f"B{i:02d}" for i in range(1, 9)]
        key = yaml.safe_load((run / "blind_key.yaml").read_text(encoding="utf-8"))
        assert key["seed"] == 42 and key["count"] == 8
        assert {(e["engine"], e["phrase_id"]) for e in key["key"]} == {
            (eng, pid) for eng in ("xtts", "qwen3")
            for pid in ("neutral", "emotional", "tongue_twister", "intimate")
        }
        # детерминизм: тот же seed — та же раскладка
        before = [(e["code"], e["file"]) for e in key["key"]]
        vb.main(["--blind-only", "--run-dir", str(run), "--blind-seed", "42"])
        key2 = yaml.safe_load((run / "blind_key.yaml").read_text(encoding="utf-8"))
        assert [(e["code"], e["file"]) for e in key2["key"]] == before

    def test_blind_only_without_files_exits(self, vb, tmp_path: Path) -> None:
        run = tmp_path / "empty_run"
        run.mkdir()
        (run / "manifest.yaml").write_text("files: []\n", encoding="utf-8")
        with pytest.raises(SystemExit, match="нечего ослеплять"):
            vb.main(["--blind-only", "--run-dir", str(run)])
