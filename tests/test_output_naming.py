from __future__ import annotations

from pathlib import Path

import pytest

from feedback_converter.output_naming import (
    output_path,
    render_output_template,
    safe_path_segment,
    unique_output_path,
)


@pytest.mark.parametrize(
    ("artist", "title"),
    [
        ("Beyoncé", "Déjà Vu"),
        ("Björn", "Öppna landskap"),
        ("東京事変", "群青日和"),
        ("Кино", "Группа крови"),
    ],
)
def test_template_keeps_unicode_with_missing_optional_metadata(artist: str, title: str) -> None:
    assert render_output_template(
        "{artist} - {album} - {title} - {parts}",
        {"artist": artist, "title": title, "arrangement_names": {"lead": "Lead", "bass": "Bass"}},
        input_psarc=Path("source.psarc"),
    ) == f"{artist} - {title} - BL"


def test_source_filename_and_fallback_keep_unicode() -> None:
    assert render_output_template("{source}", {}, input_psarc=Path("夜に駆ける.psarc")) == "夜に駆ける"
    assert safe_path_segment("...", fallback="Björk") == "Björk"


@pytest.mark.parametrize("device", ["CON", "aux.txt", "NUL", "COM1", "lpt9.log", "COM¹", "LPT²", "COM³.txt"])
def test_windows_device_names_remain_safe(device: str) -> None:
    assert safe_path_segment(device) == f"_{device}"


def test_invalid_windows_path_characters_cannot_escape_output_folder(tmp_path: Path) -> None:
    requested = output_path(
        tmp_path / "source.psarc",
        tmp_path / "out",
        {"artist": '..\\Björk/..', "title": '夜:<歌>"|?*\x00\x1f. '},
        output_layout="artist",
        source_root=None,
        name_template="{title}",
        fallback_title="song",
        suffix=".feedpak",
    )

    assert requested == tmp_path / "out" / ".._Björk_" / "夜__歌_______.feedpak"
    assert requested.resolve().is_relative_to((tmp_path / "out").resolve())


def test_unicode_artist_folder_and_preserved_source_layout(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source = source_root / "日本語" / "アルバム" / "song.psarc"
    metadata = {"artist": "東京事変", "title": "群青日和"}
    options = dict(
        source_root=source_root,
        name_template="{artist} - {title}",
        fallback_title="song",
        suffix=".feedpak",
    )

    assert output_path(source, tmp_path / "out", metadata, output_layout="artist", **options) == (
        tmp_path / "out" / "東京事変" / "東京事変 - 群青日和.feedpak"
    )
    assert output_path(source, tmp_path / "out", metadata, output_layout="preserve", **options) == (
        tmp_path / "out" / "日本語" / "アルバム" / "東京事変 - 群青日和.feedpak"
    )


def test_canonically_equivalent_names_share_batch_reservations(tmp_path: Path) -> None:
    reserved: set[Path] = set()
    results = []
    for title in ("Déjà Vu", "De\u0301ja\u0300 Vu"):
        requested = output_path(
            tmp_path / "source.psarc", tmp_path / "out", {"title": title},
            output_layout="flat", source_root=None, name_template="{title}",
            fallback_title="song", suffix=".feedpak",
        )
        results.append(unique_output_path(requested, reserved, overwrite=True))

    assert [path.name for path in results] == ["Déjà Vu.feedpak", "Déjà Vu (2).feedpak"]


def test_unicode_disk_and_batch_collisions_preserve_existing_file(tmp_path: Path) -> None:
    requested = tmp_path / "東京事変 - 群青日和.feedpak"
    requested.write_bytes(b"existing package")
    reserved: set[Path] = set()

    assert unique_output_path(requested, reserved, overwrite=False).name == "東京事変 - 群青日和 (2).feedpak"
    assert unique_output_path(requested, reserved, overwrite=False).name == "東京事変 - 群青日和 (3).feedpak"
    assert requested.read_bytes() == b"existing package"
    assert list(tmp_path.iterdir()) == [requested]


def test_distinct_non_ascii_titles_do_not_collapse_to_one_name(tmp_path: Path) -> None:
    reserved: set[Path] = set()
    titles = ["東京", "大阪", "Cafe", "Café"]

    names = [
        unique_output_path(tmp_path / f"{safe_path_segment(title)}.feedpak", reserved, overwrite=False).name
        for title in titles
    ]

    assert names == [f"{title}.feedpak" for title in titles]
