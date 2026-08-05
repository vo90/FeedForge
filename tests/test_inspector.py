from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from feedback_converter import inspector
from feedback_converter.psarc_format.psarc import PSARC


def test_multi_song_inspection_parses_only_one_song_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = PSARC(crypto=False).build(
        {
            "manifests/songs/alpha_lead.json": json.dumps(
                {"SongKey": "alpha", "SongName": "Alpha", "ArtistName": "Artist A"}
            ).encode(),
            "songs/bin/generic/alpha_lead.sng": b"alpha chart",
            "gfxassets/album_art/album_alpha_256.png": b"alpha cover",
            "manifests/songs/beta_lead.json": json.dumps(
                {"SongKey": "beta", "SongName": "Beta", "ArtistName": "Artist B"}
            ).encode(),
            "songs/bin/generic/beta_lead.sng": b"beta chart",
            "gfxassets/album_art/album_beta_256.png": b"beta cover",
        }
    )
    source = tmp_path / "collection.psarc"
    source.write_bytes(archive)
    parsed_charts: list[bytes] = []

    def fake_song_parse(data: bytes) -> SimpleNamespace:
        parsed_charts.append(data)
        return SimpleNamespace(
            vocals=[],
            levels=[object()],
            chordTemplates=[],
            metadata=SimpleNamespace(tuning=[0, 0, 0, 0, 0, 0], capo=0),
        )

    monkeypatch.setattr(inspector.Song, "parse", fake_song_parse)
    monkeypatch.setattr(inspector, "_song_chart_data", lambda *_args: {"notes": [], "chords": []})
    monkeypatch.setattr(inspector, "_tone_preview", lambda *_args: None)

    preview = inspector.inspect_psarc(source, cover_dir=tmp_path / "cover")

    assert preview.song_count == 2
    assert preview.is_multi_song
    assert preview.preview_scope == "first_song"
    assert len(preview.arrangements) == 1
    assert len(parsed_charts) == 1
    assert parsed_charts[0] in {b"alpha chart", b"beta chart"}
    assert preview.cover_path is not None
    expected_cover = b"alpha cover" if preview.title == "Alpha" else b"beta cover"
    assert preview.cover_path.read_bytes() == expected_cover


def test_single_song_inspection_keeps_package_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = PSARC(crypto=False).build(
        {
            "manifests/songs/only_lead.json": json.dumps(
                {"SongKey": "only", "SongName": "Only Song", "ArtistName": "Artist"}
            ).encode(),
            "songs/bin/generic/only_lead.sng": b"only chart",
        }
    )
    source = tmp_path / "single.psarc"
    source.write_bytes(archive)
    monkeypatch.setattr(
        inspector.Song,
        "parse",
        lambda _data: SimpleNamespace(
            vocals=[],
            levels=[object()],
            chordTemplates=[],
            metadata=SimpleNamespace(tuning=[0, 0, 0, 0, 0, 0], capo=0),
        ),
    )
    monkeypatch.setattr(inspector, "_song_chart_data", lambda *_args: {"notes": [], "chords": []})
    monkeypatch.setattr(inspector, "_tone_preview", lambda *_args: None)

    preview = inspector.inspect_psarc(source)

    assert preview.song_count == 1
    assert not preview.is_multi_song
    assert preview.preview_scope == "package"
    assert preview.title == "Only Song"
    assert len(preview.arrangements) == 1


def test_inspection_reports_empty_sng_as_source_data_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = PSARC(crypto=False).build(
        {
            "manifests/songs/only_lead.json": json.dumps(
                {"SongKey": "only", "SongName": "Only Song", "ArtistName": "Artist"}
            ).encode(),
            "songs/bin/generic/only_combo.sng": b"",
            "songs/bin/generic/only_lead.sng": b"valid chart",
        }
    )
    source = tmp_path / "single.psarc"
    source.write_bytes(archive)
    monkeypatch.setattr(
        inspector.Song,
        "parse",
        lambda _data: SimpleNamespace(
            vocals=[],
            levels=[object()],
            chordTemplates=[],
            metadata=SimpleNamespace(tuning=[0, 0, 0, 0, 0, 0], capo=0),
        ),
    )
    monkeypatch.setattr(inspector, "_song_chart_data", lambda *_args: {"notes": [], "chords": []})
    monkeypatch.setattr(inspector, "_tone_preview", lambda *_args: None)

    preview = inspector.inspect_psarc(source)

    assert len(preview.arrangements) == 1
    assert preview.warnings == [
        "Skipped empty SNG entry songs/bin/generic/only_combo.sng; "
        "the source archive contains no chart data for this arrangement."
    ]
