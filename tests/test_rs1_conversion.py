from __future__ import annotations

import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from feedback_converter import batch, converter


def _bank_with_wem_id(wem_id: int) -> bytes:
    return b"BKHD" + struct.pack("<I", wem_id) + b"HIRC"


def _self_contained_song_content(key: str = "song") -> dict[str, bytes]:
    wem_id = 123456789
    return {
        f"manifests/songs/{key}_lead.json": json.dumps(
            {"SongKey": key, "ArtistName": "Artist", "SongName": "Song"}
        ).encode(),
        f"songs/bin/generic/{key}_lead.sng": b"chart",
        f"audio/windows/song_{key}.bnk": _bank_with_wem_id(wem_id),
        f"audio/windows/{wem_id}.wem": b"full mix",
    }


def test_batch_converts_songs_psarc_while_using_it_as_rs1_audio_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compatibility = tmp_path / "rs1compatibilitydlc_p.psarc"
    songs = tmp_path / "songs.psarc"
    compatibility.write_bytes(b"compatibility")
    songs.write_bytes(b"songs")
    plan_calls: list[tuple[Path, Path | None]] = []
    conversion_calls: list[tuple[Path, Path | None]] = []

    def fake_plan(input_path: Path, *_args: object, **kwargs: object) -> SimpleNamespace:
        plan_calls.append((Path(input_path), kwargs.get("rs1_songs_psarc")))
        return SimpleNamespace(to_dict=lambda: {"outputs": []})

    def fake_convert(input_path: Path, *_args: object, **kwargs: object) -> list[SimpleNamespace]:
        conversion_calls.append((Path(input_path), kwargs.get("rs1_songs_psarc")))
        return [SimpleNamespace(output_path=tmp_path / f"{Path(input_path).stem}.feedpak")]

    monkeypatch.setattr(batch, "plan_psarc_songs", fake_plan)
    monkeypatch.setattr(batch, "convert_psarc_songs", fake_convert)

    result = batch.convert_many([compatibility, songs], tmp_path / "out")

    assert result.ok
    assert [item.input_path for item in result.items] == [compatibility, songs]
    assert plan_calls == [(compatibility, songs), (songs, None)]
    assert conversion_calls == [(compatibility, songs), (songs, None)]


def test_self_contained_compatibility_archive_does_not_reload_songs_psarc(tmp_path: Path) -> None:
    compatibility = tmp_path / "rs1compatibilitydisc_p.psarc"
    support = tmp_path / "songs.psarc"
    support.write_bytes(b"not a real archive")

    loaded = converter._load_rs1_songs_content(
        compatibility,
        _self_contained_song_content(),
        support,
    )

    assert loaded is None


def test_rs1_audio_source_auto_discovery_checks_sibling_then_parent(tmp_path: Path) -> None:
    dlc_dir = tmp_path / "dlc"
    dlc_dir.mkdir()
    compatibility = dlc_dir / "rs1compatibilitydlc_p.psarc"
    sibling = dlc_dir / "songs.psarc"
    parent = tmp_path / "songs.psarc"
    sibling.write_bytes(b"sibling")
    parent.write_bytes(b"parent")

    assert converter._default_rs1_songs_psarc(compatibility) == sibling

    sibling.unlink()
    assert converter._default_rs1_songs_psarc(compatibility) == parent


def test_rs1_missing_audio_fails_before_any_output_is_planned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compatibility = tmp_path / "rs1compatibilitydlc_p.psarc"
    compatibility.write_bytes(b"placeholder")
    entries = [
        ("firstsong", {"songs/bin/generic/firstsong_lead.sng": b"chart"}),
        ("secondsong", {"songs/bin/generic/secondsong_bass.sng": b"chart"}),
    ]
    monkeypatch.setattr(converter, "_read_psarc_song_entries", lambda *_args, **_kwargs: entries)

    def unexpected_targets(*_args: object, **_kwargs: object) -> list[Path]:
        raise AssertionError("output planning must not run when linked audio is missing")

    monkeypatch.setattr(converter, "_targets_for_conversion", unexpected_targets)

    with pytest.raises(ValueError, match=r"missing full-mix audio for 2 song\(s\).+same conversion queue.+No partial"):
        converter.convert_psarc_songs(compatibility)


def test_mismatched_rs1_audio_source_has_actionable_error(tmp_path: Path) -> None:
    compatibility = tmp_path / "rs1compatibilitydlc_p.psarc"
    support = tmp_path / "songs.psarc"

    with pytest.raises(ValueError, match="does not contain matching audio"):
        converter._validate_song_audio_entries(
            [("missing", {"songs/bin/generic/missing_lead.sng": b"chart"})],
            compatibility,
            rs1_songs_psarc=support,
        )
