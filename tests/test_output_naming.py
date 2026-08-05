from __future__ import annotations

from pathlib import Path

import pytest

from feedback_converter.output_naming import (
    output_path,
    render_output_template,
    safe_path_segment,
    unique_output_path,
    validate_name_template,
    validate_template_metadata,
)


def test_template_preserves_unicode_and_collapses_missing_optional_fields() -> None:
    metadata = {
        "artist": "Beyoncé",
        "title": "Déjà Vu",
        "album": "",
        "arrangement_names": {"lead": "Lead", "bass": "Bass"},
    }

    rendered = render_output_template(
        "{artist} - {album} - {title} - {parts}",
        metadata,
        input_psarc=Path("source.psarc"),
    )

    assert rendered == "Beyoncé - Déjà Vu - BL"


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("{source}", "original_file"),
        ("{artist} - {title}", "ABBA - Angel Eyes"),
        ("{title} - {artist}", "Angel Eyes - ABBA"),
        ("{artist} - {album} - {title}", "ABBA - Voulez-Vous - Angel Eyes"),
        ("{artist} - {title} - {parts}", "ABBA - Angel Eyes - BL"),
        ("{year} - {artist} - {title}", "1979 - ABBA - Angel Eyes"),
    ],
)
def test_every_desktop_naming_convention_is_rendered_by_the_shared_engine(template: str, expected: str) -> None:
    metadata = {
        "artist": "ABBA",
        "title": "Angel Eyes",
        "album": "Voulez-Vous",
        "year": 1979,
        "arrangement_names": {"lead": "Lead", "bass": "Bass"},
    }

    assert render_output_template(template, metadata, input_psarc=Path("original_file.psarc")) == expected


def test_template_rejects_unknown_fields_instead_of_writing_literal_braces() -> None:
    with pytest.raises(ValueError, match=r"Unknown output naming field.*\{artits\}"):
        validate_name_template("{artits} - {title}")


def test_missing_artist_is_only_used_when_source_metadata_is_really_missing() -> None:
    rendered = render_output_template(
        "{artist} - {title}",
        {"title": "A Song"},
        input_psarc=Path("source.psarc"),
    )

    assert rendered == "Unknown Artist - A Song"


def test_planning_rejects_a_template_when_required_metadata_is_missing() -> None:
    with pytest.raises(ValueError, match=r"metadata is missing: \{artist\}"):
        validate_template_metadata("{artist} - {title}", {"title": "A Song"})

    # Source naming remains safe even for metadata-poor packages.
    validate_template_metadata("{source}", {})


def test_windows_reserved_names_and_invalid_characters_are_safe() -> None:
    assert safe_path_segment("CON") == "_CON"
    assert safe_path_segment('Artist: Song?') == "Artist_ Song_"


def test_output_path_honors_artist_and_preserve_layouts(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    input_path = source_root / "Rock" / "Album" / "song.psarc"
    metadata = {"artist": "An Artist", "title": "A Song"}

    artist_path = output_path(
        input_path,
        tmp_path / "out",
        metadata,
        output_layout="artist",
        source_root=source_root,
        name_template="{artist} - {title}",
        fallback_title="song",
        suffix=".feedpak",
    )
    preserve_path = output_path(
        input_path,
        tmp_path / "out",
        metadata,
        output_layout="preserve",
        source_root=source_root,
        name_template="{title} - {artist}",
        fallback_title="song",
        suffix=".feedpak",
    )

    assert artist_path == tmp_path / "out" / "An Artist" / "An Artist - A Song.feedpak"
    assert preserve_path == tmp_path / "out" / "Rock" / "Album" / "A Song - An Artist.feedpak"


def test_unique_output_path_accounts_for_disk_and_batch_collisions(tmp_path: Path) -> None:
    requested = tmp_path / "ABBA - Angel Eyes.feedpak"
    requested.write_bytes(b"existing")
    reserved: set[Path] = set()

    first = unique_output_path(requested, reserved, overwrite=False)
    second = unique_output_path(requested, reserved, overwrite=False)

    assert first.name == "ABBA - Angel Eyes (2).feedpak"
    assert second.name == "ABBA - Angel Eyes (3).feedpak"


def test_overwrite_still_prevents_two_batch_items_from_sharing_one_path(tmp_path: Path) -> None:
    requested = tmp_path / "same.feedpak"
    reserved: set[Path] = set()

    first = unique_output_path(requested, reserved, overwrite=True)
    second = unique_output_path(requested, reserved, overwrite=True)

    assert first == requested
    assert second.name == "same (2).feedpak"
