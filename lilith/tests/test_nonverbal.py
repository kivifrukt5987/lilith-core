"""Гварды нонвербалики (ADR-027 Q7-б): теги → wav-пакеты, разговор не рвётся."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lilith_core.voice import VoiceCore
from lilith_core.voice.nonverbal import (
    nonverbal_pack_path,
    nonverbal_tags,
    split_nonverbal,
)
from lilith_core.voice.stt import read_wav, write_wav

NV_SPEC = {
    "laugh": {"tag": "[laughs]", "weight": 1.0},
    "sigh": {"tag": "[sighs]", "weight": 0.8},
}


class TestNonverbalTags:
    def test_from_voice_yaml_slot(self) -> None:
        assert nonverbal_tags(NV_SPEC) == {"laugh": "[laughs]", "sigh": "[sighs]"}

    def test_string_shorthand(self) -> None:
        assert nonverbal_tags({"hum": "[hums]"}) == {"hum": "[hums]"}

    def test_empty_and_none(self) -> None:
        assert nonverbal_tags(None) == {} and nonverbal_tags({}) == {}

    def test_entries_without_tag_dropped(self) -> None:
        assert nonverbal_tags({"laugh": {"weight": 1.0}}) == {}


class TestSplitNonverbal:
    def test_no_tags_single_segment(self) -> None:
        assert split_nonverbal("просто текст", {}) == [("text", "просто текст")]
        assert split_nonverbal("", {}) == []

    def test_tags_become_events(self) -> None:
        tags = nonverbal_tags(NV_SPEC)
        segments = split_nonverbal("Привет [laughs] как дела [sighs] пока", tags)
        assert segments == [
            ("text", "Привет"),
            ("event", "laugh"),
            ("text", "как дела"),
            ("event", "sigh"),
            ("text", "пока"),
        ]

    def test_tag_at_edges(self) -> None:
        tags = nonverbal_tags(NV_SPEC)
        assert split_nonverbal("[laughs] утро", tags) == [("event", "laugh"), ("text", "утро")]
        assert split_nonverbal("споки [sighs]", tags) == [("text", "споки"), ("event", "sigh")]

    def test_unknown_tag_stays_text(self) -> None:
        tags = nonverbal_tags(NV_SPEC)
        segments = split_nonverbal("хм [unknown] так", tags)
        assert segments == [("text", "хм [unknown] так")]

    def test_adjacent_tags(self) -> None:
        tags = nonverbal_tags(NV_SPEC)
        assert split_nonverbal("[laughs][sighs]", tags) == [("event", "laugh"), ("event", "sigh")]


class TestPackPath:
    def test_existing_pack(self, tmp_path: Path) -> None:
        pack_dir = tmp_path / "nonverbal"
        pack_dir.mkdir()
        (pack_dir / "laugh.wav").write_bytes(write_wav(b"\x01\x00" * 100, 24000))
        assert nonverbal_pack_path(tmp_path, "laugh") == pack_dir / "laugh.wav"

    def test_missing_pack_returns_none(self, tmp_path: Path) -> None:
        assert nonverbal_pack_path(tmp_path, "cry") is None

    def test_no_voice_dir_returns_none(self) -> None:
        assert nonverbal_pack_path(None, "laugh") is None


@pytest.fixture
def voice_settings(settings, tmp_project):
    settings.features.voice_enabled = True
    settings.voice.packs_manifest = str(tmp_project / "models" / "packs.yaml")
    settings.voice.packs_root = str(tmp_project)
    (tmp_project / "models").mkdir(exist_ok=True)
    (tmp_project / "models" / "packs.yaml").write_text("packs: {}\n", encoding="utf-8")
    return settings


@pytest.mark.asyncio
class TestStreamMixed:
    async def test_text_and_pack_interleave(self, voice_settings, tmp_project: Path) -> None:
        core = VoiceCore(voice_settings)
        voice_dir = tmp_project / "personas" / "lilith" / "voice"
        (voice_dir / "nonverbal").mkdir(parents=True)
        laugh_wav = write_wav(b"\x02\x00" * 200, 24000)
        (voice_dir / "nonverbal" / "laugh.wav").write_bytes(laugh_wav)

        chunks = [
            c async for c in core.stream_mixed(
                "Ха-ха [laughs] вот так.", profile=None,
                nonverbal=NV_SPEC, voice_dir=voice_dir,
            )
        ]
        assert laugh_wav in chunks           # пакет доехал как есть
        assert len(chunks) >= 3              # два текстовых сегмента (mock режет предложения) + пакет
        text_chunks = [c for c in chunks if c != laugh_wav]
        assert all(read_wav(c)[1] == 16000 for c in text_chunks)  # mock-горло, 16к

    async def test_missing_pack_skipped_not_fatal(self, voice_settings, tmp_project: Path) -> None:
        core = VoiceCore(voice_settings)
        chunks = [
            c async for c in core.stream_mixed(
                "Смех [laughs] без ассета.", nonverbal=NV_SPEC,
                voice_dir=tmp_project / "personas" / "lilith" / "voice",
            )
        ]
        assert chunks  # реплика продолжается, тег вырезан

    async def test_without_nonverbal_plain_stream(self, voice_settings) -> None:
        core = VoiceCore(voice_settings)
        chunks = [c async for c in core.stream_mixed("Раз. Два.", nonverbal=None)]
        assert len(chunks) == 2


class TestPersonaVoiceSchema:
    def test_lilith_voice_yaml_has_nonverbal_slot(self) -> None:
        """Схема ADR-020.3: слот nonverbal объявлен в voice.yaml персоны."""
        project_root = Path(__file__).resolve().parents[1]
        data = yaml.safe_load(
            (project_root / "personas" / "lilith" / "voice.yaml").read_text(encoding="utf-8")
        )
        assert "nonverbal" in data
