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


def test_batch_preserves_conversion_options_for_every_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "song.psarc"
    source.write_bytes(b"song")
    converted_options: list[dict[str, object]] = []

    monkeypatch.setattr(
        batch,
        "plan_psarc_songs",
        lambda *_args, **_kwargs: SimpleNamespace(to_dict=lambda: {"outputs": []}),
    )

    def fake_convert(*_args: object, **kwargs: object) -> list[SimpleNamespace]:
        converted_options.append(kwargs)
        return [SimpleNamespace(output_path=tmp_path / "song.feedpak")]

    monkeypatch.setattr(batch, "convert_psarc_songs", fake_convert)

    result = batch.convert_many(
        [source],
        tmp_path / "out",
        output_layout="artist",
        name_template="{artist} - {title}",
        include_tones=False,
        b_standard_to_7_string=True,
        separate_stems=True,
        demucs_url="http://127.0.0.1:7865",
        demucs_api_key="secret",
        demucs_model="model",
        demucs_stems=["guitar", "vocals"],
    )

    assert result.ok
    assert len(converted_options) == 1
    options = converted_options[0]
    assert options["include_tones"] is False
    assert options["b_standard_to_7_string"] is True
    assert options["separate_stems"] is True
    assert options["demucs_url"] == "http://127.0.0.1:7865"
    assert options["demucs_api_key"] == "secret"
    assert options["demucs_model"] == "model"
    assert options["demucs_stems"] == ["guitar", "vocals"]
    assert options["output_layout"] == "artist"
    assert options["name_template"] == "{artist} - {title}"


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


def test_local_rs1_audio_wins_while_external_source_can_supply_missing_preview() -> None:
    local_main_id = 111
    external_main_id = 222
    external_preview_id = 333
    manifest = "manifests/songs/song_lead.json"
    chart = "songs/bin/generic/song_lead.sng"
    local_main = f"audio/windows/{local_main_id}.wem"
    external_main = f"audio/windows/{external_main_id}.wem"
    external_preview = f"audio/windows/{external_preview_id}.wem"
    local = {
        manifest: json.dumps({"SongKey": "song", "SongName": "Song"}).encode(),
        chart: b"chart",
        "audio/windows/song_song.bnk": _bank_with_wem_id(local_main_id),
        local_main: b"local full mix",
    }
    external = {
        "audio/windows/song_song.bnk": _bank_with_wem_id(external_main_id),
        "audio/windows/song_song_preview.bnk": _bank_with_wem_id(external_preview_id),
        external_main: b"external full mix that must not replace local audio",
        external_preview: b"external preview",
    }

    selected = converter._content_for_song_group(
        local,
        "song",
        {manifest, chart},
        rs1_songs_content=external,
    )

    assert selected[local_main] == b"local full mix"
    assert external_main not in selected
    marked_preview = f"{converter.INTERNAL_PREVIEW_AUDIO_PREFIX}{external_preview}"
    assert selected[marked_preview] == b"external preview"
