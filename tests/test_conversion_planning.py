from __future__ import annotations

import json
from pathlib import Path

import pytest

from feedback_converter import converter


def _song_content(artist: str, title: str, *, album: str = "", year: int | None = None) -> dict[str, bytes]:
    metadata = {
        "ArtistName": artist,
        "SongName": title,
        "AlbumName": album,
        "SongYear": year,
    }
    return {"songs/metadata/song.json": json.dumps(metadata).encode("utf-8")}


def test_planner_uses_authoritative_metadata_even_when_source_filename_is_different(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "ABBA_AngelEyes_v1_p.psarc"
    input_path.write_bytes(b"psarc placeholder")
    monkeypatch.setattr(
        converter,
        "_read_psarc_song_entries",
        lambda *_args, **_kwargs: [("angeleyes", _song_content("ABBA", "Angel Eyes"))],
    )

    plan = converter.plan_psarc_songs(
        input_path,
        tmp_path / "out",
        name_template="{artist} - {title}",
    )

    assert plan.outputs[0].output_path.name == "ABBA - Angel Eyes.feedpak"
    assert plan.outputs[0].artist == "ABBA"
    assert plan.outputs[0].title == "Angel Eyes"


def test_multi_song_psarc_applies_selected_template_to_every_song(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "collection.psarc"
    input_path.write_bytes(b"psarc placeholder")
    monkeypatch.setattr(
        converter,
        "_read_psarc_song_entries",
        lambda *_args, **_kwargs: [
            ("first", _song_content("Artist One", "First Song", year=2001)),
            ("second", _song_content("Artist Two", "Second Song", year=2002)),
        ],
    )

    plan = converter.plan_psarc_songs(
        input_path,
        tmp_path / "out",
        name_template="{year} - {title} - {artist}",
    )

    assert [output.output_path.name for output in plan.outputs] == [
        "2001 - First Song - Artist One.feedpak",
        "2002 - Second Song - Artist Two.feedpak",
    ]

    source_named = converter.plan_psarc_songs(
        input_path,
        tmp_path / "source-named",
        name_template="{source}",
    )
    assert [output.output_path.name for output in source_named.outputs] == [
        "collection.feedpak",
        "collection (2).feedpak",
    ]


def test_batch_reservations_prevent_cross_psarc_collisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_input = tmp_path / "one.psarc"
    second_input = tmp_path / "two.psarc"
    first_input.write_bytes(b"one")
    second_input.write_bytes(b"two")
    monkeypatch.setattr(
        converter,
        "_read_psarc_song_entries",
        lambda *_args, **_kwargs: [("song", _song_content("Same Artist", "Same Song"))],
    )
    reserved: set[Path] = set()

    first = converter.plan_psarc_songs(
        first_input,
        tmp_path / "out",
        name_template="{artist} - {title}",
        overwrite=True,
        reserved_outputs=reserved,
    )
    second = converter.plan_psarc_songs(
        second_input,
        tmp_path / "out",
        name_template="{artist} - {title}",
        overwrite=True,
        reserved_outputs=reserved,
    )

    assert first.outputs[0].output_path.name == "Same Artist - Same Song.feedpak"
    assert second.outputs[0].output_path.name == "Same Artist - Same Song (2).feedpak"


def test_plan_preserves_each_folder_import_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_root = tmp_path / "first-library"
    second_root = tmp_path / "second-library"
    first_input = first_root / "Rock" / "one.psarc"
    second_input = second_root / "Pop" / "two.psarc"
    first_input.parent.mkdir(parents=True)
    second_input.parent.mkdir(parents=True)
    first_input.write_bytes(b"one")
    second_input.write_bytes(b"two")

    def fake_entries(input_path: Path, **_kwargs: object) -> list[tuple[str, dict[str, bytes]]]:
        title = "One" if Path(input_path) == first_input else "Two"
        return [(title.lower(), _song_content("Artist", title))]

    monkeypatch.setattr(converter, "_read_psarc_song_entries", fake_entries)
    reserved: set[Path] = set()
    first = converter.plan_psarc_songs(
        first_input,
        tmp_path / "out",
        output_layout="preserve",
        source_root=first_root,
        name_template="{title}",
        reserved_outputs=reserved,
    )
    second = converter.plan_psarc_songs(
        second_input,
        tmp_path / "out",
        output_layout="preserve",
        source_root=second_root,
        name_template="{title}",
        reserved_outputs=reserved,
    )

    assert first.outputs[0].output_path == tmp_path / "out" / "Rock" / "One.feedpak"
    assert second.outputs[0].output_path == tmp_path / "out" / "Pop" / "Two.feedpak"


def test_conversion_rejects_a_stale_or_mismatched_plan(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "song.psarc"
    input_path.write_bytes(b"original")
    stat = input_path.stat()
    entries = [("song", _song_content("ABBA", "Song"))]
    plan = {
        "inputPath": str(input_path),
        "sourceSize": stat.st_size,
        "sourceMtimeNs": str(stat.st_mtime_ns),
        "outputs": [
            {
                "key": "song",
                "path": str(tmp_path / "out" / "ABBA - Song.feedpak"),
                "artist": "Wrong Artist",
                "title": "Song",
            }
        ],
    }

    with pytest.raises(ValueError, match="artist: planned 'Wrong Artist', found 'ABBA'"):
        converter._validated_planned_targets(input_path, entries, plan, source_stat=stat)

    plan["outputs"][0]["artist"] = "ABBA"
    plan["sourceSize"] = stat.st_size + 1
    with pytest.raises(ValueError, match="Source PSARC changed"):
        converter._validated_planned_targets(input_path, entries, plan, source_stat=stat)


def test_song_group_metadata_preserves_archive_order_when_paths_are_a_set() -> None:
    primary_manifest = "manifests/songs/focuhocu_lead.json"
    alternate_manifest = "manifests/songs/focuhocu_rhythm.json"
    chart = "songs/bin/generic/focuhocu_lead.sng"
    content = {
        primary_manifest: json.dumps(
            {"ArtistName": "Focus", "SongName": "Hocus Pocus", "AlbumName": "Moving Waves"}
        ).encode("utf-8"),
        alternate_manifest: json.dumps(
            {"ArtistName": "Focus", "SongName": "Hocus Pocus", "AlbumName": "Focus II"}
        ).encode("utf-8"),
        chart: b"chart",
    }

    class ReverseIterationSet(set[str]):
        def __iter__(self):  # type: ignore[override]
            return iter((alternate_manifest, primary_manifest, chart))

    selected = converter._content_for_song_group(
        content,
        "focuhocu",
        ReverseIterationSet(content),
    )

    assert list(selected) == list(content)
    assert converter._extract_metadata(selected)["album"] == "Moving Waves"
    assert converter._extract_output_metadata(content)["album"] == "Moving Waves"
