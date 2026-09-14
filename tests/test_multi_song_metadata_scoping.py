from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from feedback_converter.batch import _PlanningMetadataCache
from feedback_converter.converter import (
    PSARC_PLANNING_CACHE_VERSION,
    PsarcPlanningData,
    _content_for_song_group,
    _extract_metadata,
    _extract_output_metadata,
    _metadata_content_for_song_group,
    _plan_loaded_song_outputs,
    _playable_song_groups,
    _song_groups,
    _song_key_from_manifest,
    _song_keys_from_manifest,
    psarc_planning_data_from_cache,
)
from feedback_converter.inspector import _read_preview_content


def _json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def _attributes(
    key: str,
    *,
    title: str | None = None,
    artist: str | None = None,
    album: str | None = None,
    year: int | None = None,
    duration: float | None = None,
    arrangement: str = "Lead",
) -> dict[str, object]:
    attributes: dict[str, object] = {
        "SongKey": key,
        "ArrangementName": arrangement,
    }
    if title is not None:
        attributes["SongName"] = title
    if artist is not None:
        attributes["ArtistName"] = artist
    if album is not None:
        attributes["AlbumName"] = album
    if year is not None:
        attributes["SongYear"] = year
    if duration is not None:
        attributes["SongLength"] = duration
    return attributes


def _aggregate_manifest() -> bytes:
    # This mirrors the ordering that exposed the real RS1 bug: a minimal
    # RisingSun vocals record is followed by a complete IGotMine record.
    return _json(
        {
            "Entries": {
                "00": {"Attributes": _attributes("RisingSun", arrangement="Vocals")},
                "01": {
                    "Attributes": _attributes(
                        "IGotMine",
                        title="I Got Mine",
                        artist="The Black Keys",
                        album="Attack & Release",
                        year=2008,
                        duration=250.211,
                        arrangement="Bass",
                    )
                },
                "02": {
                    "Attributes": _attributes(
                        "RisingSun",
                        title="House of the Rising Sun",
                        artist="The Animals",
                        album="Retrospective",
                        year=1964,
                        duration=281.811,
                        arrangement="Rhythm",
                    )
                },
            },
            "InsertRoot": "Static.Songs.Entries",
        }
    )


def _song_manifest(key: str) -> bytes:
    if key == "igotmine":
        attributes = _attributes(
            "IGotMine",
            title="I Got Mine",
            artist="The Black Keys",
            album="Attack & Release",
            year=2008,
            duration=250.211,
        )
    else:
        attributes = _attributes(
            "RisingSun",
            title="House of the Rising Sun",
            artist="The Animals",
            album="Retrospective",
            year=1964,
            duration=281.811,
            arrangement="Rhythm",
        )
    return _json({"Entries": {key: {"Attributes": attributes}}})


def _content(*, include_song_manifests: bool) -> dict[str, bytes]:
    content = {
        "manifests/songs/songs.hsan": _aggregate_manifest(),
        "songs/bin/generic/igotmine_lead.sng": b"",
        "songs/bin/generic/risingsun_rhythm.sng": b"",
    }
    if include_song_manifests:
        content.update(
            {
                "manifests/songs/igotmine_lead.json": _song_manifest("igotmine"),
                "manifests/songs/risingsun_rhythm.json": _song_manifest("risingsun"),
            }
        )
    return content


def test_multi_song_aggregate_is_not_owned_by_its_first_song_key() -> None:
    aggregate = _aggregate_manifest()

    assert _song_keys_from_manifest(aggregate) == ("risingsun", "igotmine")
    assert _song_key_from_manifest(aggregate) == ""

    groups = _playable_song_groups(_song_groups(_content(include_song_manifests=True)))
    assert set(groups) == {"igotmine", "risingsun"}
    assert all("manifests/songs/songs.hsan" not in paths for paths in groups.values())


def test_rs1_dlc_aggregate_does_not_override_is_this_love_song_manifest() -> None:
    aggregate = _json(
        {
            "Entries": {
                "00": {
                    "Attributes": _attributes(
                        "IsThisLove",
                        arrangement="Vocals",
                    )
                },
                "01": {
                    "Attributes": _attributes(
                        "AnotherDlcSong",
                        title="Wrong aggregate identity",
                        artist="Wrong Artist",
                    )
                },
            }
        }
    )
    authoritative = _json(
        {
            "Entries": {
                "isthislove": {
                    "Attributes": _attributes(
                        "IsThisLove",
                        title="Is This Love",
                        artist="Whitesnake",
                        album="Whitesnake",
                        year=1987,
                    )
                }
            }
        }
    )
    content = {
        "manifests/songs/rs1_dlc.hsan": aggregate,
        "manifests/songs/isthislove_lead.json": authoritative,
        "songs/bin/generic/isthislove_lead.sng": b"",
    }

    groups = _playable_song_groups(_song_groups(content))
    selected = _metadata_content_for_song_group(
        content,
        "isthislove",
        groups["isthislove"],
    )

    assert selected == {"manifests/songs/isthislove_lead.json": authoritative}
    assert _extract_output_metadata(selected) == {
        "title": "Is This Love",
        "artist": "Whitesnake",
        "album": "Whitesnake",
        "year": 1987,
        "arrangement_names": {},
    }


def test_single_song_manifest_grouping_and_bytes_are_unchanged() -> None:
    manifest = _json(
        {
            "Entries": {
                "only": {
                    "Attributes": _attributes(
                        "OnlySong",
                        title="Only Song",
                        artist="Only Artist",
                    )
                }
            }
        }
    )
    content = {
        # Deliberately generic filename: the sole explicit SongKey must own it.
        "manifests/songs/generic-name.json": manifest,
        "songs/bin/generic/onlysong_lead.sng": b"",
    }

    groups = _playable_song_groups(_song_groups(content))
    assert groups == {
        "onlysong": {
            "manifests/songs/generic-name.json",
            "songs/bin/generic/onlysong_lead.sng",
        }
    }
    assert _metadata_content_for_song_group(
        content,
        "onlysong",
        groups["onlysong"],
    ) == {"manifests/songs/generic-name.json": manifest}


def test_planning_and_conversion_use_the_same_song_scoped_metadata(tmp_path: Path) -> None:
    content = _content(include_song_manifests=True)
    groups = _playable_song_groups(_song_groups(content))
    entries: list[tuple[str, dict[str, bytes]]] = []

    for key, paths in sorted(groups.items()):
        planning_content = _metadata_content_for_song_group(content, key, paths)
        conversion_content = _content_for_song_group(content, key, paths)
        conversion_metadata_bytes = {
            path: data
            for path, data in conversion_content.items()
            if path.lower().endswith((".json", ".hsan"))
        }
        assert conversion_metadata_bytes == planning_content
        planning_metadata = _extract_output_metadata(planning_content)
        conversion_metadata = _extract_output_metadata(conversion_content)
        assert conversion_metadata == planning_metadata
        entries.append((key, planning_content))

    assert _extract_output_metadata(entries[0][1])["title"] == "I Got Mine"
    assert _extract_output_metadata(entries[1][1]) == {
        "title": "House of the Rising Sun",
        "artist": "The Animals",
        "album": "Retrospective",
        "year": 1964,
        "arrangement_names": {},
    }

    planned = _plan_loaded_song_outputs(
        Path("rs1compatibilitydisc_p.psarc"),
        entries,
        output_dir=tmp_path,
        output_layout="flat",
        source_root=None,
        name_template="{artist} - {title}",
        overwrite=False,
        reserved_outputs=set(),
    )
    assert [item.output_path.name for item in planned] == [
        "The Black Keys - I Got Mine.feedpak",
        "The Animals - House of the Rising Sun.feedpak",
    ]


def test_preview_uses_the_same_scoped_metadata_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = _content(include_song_manifests=True)

    class FakePsarc:
        def __init__(self, *, crypto: bool) -> None:
            assert crypto is True

        def parse_metadata_stream(self, _stream: object) -> dict[str, bytes]:
            return dict(content)

        def parse_selected_stream(
            self,
            _stream: object,
            _selector: object,
        ) -> dict[str, bytes]:
            return {}

    monkeypatch.setattr("feedback_converter.inspector.PSARC", FakePsarc)
    source = tmp_path / "rs1compatibilitydisc_p.psarc"
    source.write_bytes(b"fake psarc; parser is replaced by the fixture")

    preview_content, song_count = _read_preview_content(source)
    groups = _playable_song_groups(_song_groups(content))
    first_key, first_paths = next(iter(groups.items()))
    expected = _metadata_content_for_song_group(content, first_key, first_paths)
    preview_metadata_bytes = {
        path: data
        for path, data in preview_content.items()
        if path.lower().endswith((".json", ".hsan"))
    }

    assert song_count == 2
    assert preview_metadata_bytes == expected


def test_aggregate_only_metadata_is_filtered_for_each_song_key() -> None:
    content = _content(include_song_manifests=False)
    groups = _playable_song_groups(_song_groups(content))

    rising_content = _metadata_content_for_song_group(
        content,
        "risingsun",
        groups["risingsun"],
    )
    got_mine_content = _metadata_content_for_song_group(
        content,
        "igotmine",
        groups["igotmine"],
    )

    assert _song_keys_from_manifest(next(iter(rising_content.values()))) == ("risingsun",)
    assert _song_keys_from_manifest(next(iter(got_mine_content.values()))) == ("igotmine",)
    assert _extract_output_metadata(rising_content)["title"] == "House of the Rising Sun"
    assert _extract_output_metadata(got_mine_content)["title"] == "I Got Mine"
    assert _extract_metadata(rising_content)["duration"] == 281.811


def test_aggregate_scope_ignores_unkeyed_identity_record() -> None:
    content = {
        "manifests/songs/shared.hsan": _json(
            {
                "Entries": {
                    "00": {"Attributes": {"SongName": "Wrong unkeyed title"}},
                    "01": {
                        "Attributes": _attributes(
                            "Target",
                            title="Right title",
                            artist="Right Artist",
                        )
                    },
                    "02": {
                        "Attributes": _attributes(
                            "Other",
                            title="Other title",
                        )
                    },
                }
            }
        ),
        "songs/bin/generic/target_lead.sng": b"",
    }
    groups = _playable_song_groups(_song_groups(content))

    scoped = _metadata_content_for_song_group(
        content,
        "target",
        groups["target"],
    )

    assert _extract_output_metadata(scoped)["title"] == "Right title"
    assert b"Wrong unkeyed title" not in next(iter(scoped.values()))


def test_single_explicit_key_still_ignores_unkeyed_identity_record() -> None:
    content = {
        "manifests/songs/target.json": _json(
            {
                "Entries": {
                    "00": {"Attributes": {"SongName": "Wrong unkeyed title"}},
                    "01": {
                        "Attributes": _attributes(
                            "Target",
                            title="Right sole-key title",
                        )
                    },
                }
            }
        ),
        "songs/bin/generic/target_lead.sng": b"",
    }
    groups = _playable_song_groups(_song_groups(content))

    scoped = _metadata_content_for_song_group(
        content,
        "target",
        groups["target"],
    )

    assert _extract_output_metadata(scoped)["title"] == "Right sole-key title"
    assert b"Wrong unkeyed title" not in next(iter(scoped.values()))


def test_mixed_songkey_and_dlckey_records_are_scoped_per_record() -> None:
    content = {
        "manifests/songs/shared.hsan": _json(
            {
                "Entries": {
                    "00": {
                        "Attributes": {
                            "DLCKey": "Foreign",
                            "SongName": "Wrong DLC-only record",
                        }
                    },
                    "01": {
                        "Attributes": {
                            **_attributes(
                                "Target",
                                title="Right mixed-key title",
                                artist="Right Artist",
                            ),
                            # Rocksmith uses a different co-located DLCKey as
                            # package scope. It must not become a song owner.
                            "DLCKey": "SharedPackage",
                        }
                    },
                    "02": {
                        "Attributes": {
                            "DLCKey": "Other",
                            "SongName": "Other DLC-only record",
                        }
                    },
                }
            }
        ),
        "songs/bin/generic/target_lead.sng": b"",
    }
    aggregate = content["manifests/songs/shared.hsan"]

    assert _song_keys_from_manifest(aggregate) == ("foreign", "target", "other")
    groups = _playable_song_groups(_song_groups(content))
    scoped = _metadata_content_for_song_group(
        content,
        "target",
        groups["target"],
    )

    assert _song_keys_from_manifest(next(iter(scoped.values()))) == ("target",)
    assert _extract_output_metadata(scoped)["title"] == "Right mixed-key title"


def test_conflicting_exact_key_aggregate_identity_fails_clearly() -> None:
    content = {
        "manifests/songs/first.hsan": _json(
            {
                "Entries": {
                    "00": {"Attributes": _attributes("Target", title="First title")},
                    "01": {"Attributes": _attributes("Other")},
                }
            }
        ),
        "manifests/songs/second.hsan": _json(
            {
                "Entries": {
                    "00": {"Attributes": _attributes("Target", title="Second title")},
                    "01": {"Attributes": _attributes("Third")},
                }
            }
        ),
        "songs/bin/generic/target_lead.sng": b"",
    }
    groups = _playable_song_groups(_song_groups(content))

    with pytest.raises(
        ValueError,
        match=r"first\.hsan and .*second\.hsan conflict on title for song target",
    ):
        _metadata_content_for_song_group(content, "target", groups["target"])


def test_aggregate_only_ambiguous_nesting_fails_clearly() -> None:
    content = {
        "manifests/songs/shared.hsan": _json(
            {
                "SongKey": "Alpha",
                "Nested": {
                    "SongKey": "Beta",
                    "SongName": "Cannot safely detach this child",
                },
            }
        ),
        "songs/bin/generic/beta_lead.sng": b"",
    }
    groups = _playable_song_groups(_song_groups(content))

    with pytest.raises(
        ValueError,
        match=r"Shared metadata file .* could not be scoped to song beta",
    ):
        _metadata_content_for_song_group(content, "beta", groups["beta"])


def test_aggregate_only_without_an_exact_song_key_fails_clearly() -> None:
    content = {
        "manifests/songs/shared.hsan": _json(
            {
                "Entries": {
                    "00": {"Attributes": _attributes("Alpha")},
                    "01": {"Attributes": _attributes("Gamma")},
                }
            }
        ),
        "songs/bin/generic/beta_lead.sng": b"",
    }
    groups = _playable_song_groups(_song_groups(content))

    with pytest.raises(
        ValueError,
        match=r"no exact SongKey match for song beta.*ambiguous metadata fallback",
    ):
        _metadata_content_for_song_group(content, "beta", groups["beta"])


def test_stale_planning_cache_payload_is_rejected_and_version_four_round_trips() -> None:
    path = Path("disc.psarc")
    songs = [("risingsun", {"artist": "The Animals", "title": "House of the Rising Sun"})]
    planning = PsarcPlanningData(path, 123, 456, songs)

    assert PSARC_PLANNING_CACHE_VERSION == 4
    with pytest.raises(ValueError, match="older planner version"):
        psarc_planning_data_from_cache(
            path,
            123,
            456,
            {"version": 3, "songs": [{"key": "risingsun", "metadata": songs[0][1]}]},
        )

    restored = psarc_planning_data_from_cache(
        path,
        123,
        456,
        planning.to_cache_payload(),
    )
    assert restored == planning


def test_batch_cache_permanently_removes_stale_metadata_scope_payload(
    tmp_path: Path,
) -> None:
    source = tmp_path / "disc.psarc"
    source.write_bytes(b"source fingerprint")
    stat = source.stat()
    cache_path = tmp_path / "planning.sqlite3"
    stale_payload = {
        "version": 3,
        "songs": [
            {
                "key": "risingsun",
                "metadata": {
                    "artist": "The Black Keys",
                    "title": "I Got Mine",
                },
            }
        ],
    }

    cache = _PlanningMetadataCache(cache_path)
    assert cache.connection is not None
    cache_key = cache._key(source)
    cache.connection.execute(
        """
        INSERT INTO psarc_metadata_v1(path, size, mtime_ns, payload, last_used)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            cache_key,
            stat.st_size,
            str(stat.st_mtime_ns),
            json.dumps(stale_payload),
            2_000_000_000,
        ),
    )
    cache.connection.commit()

    assert cache.get(source, stat.st_size, stat.st_mtime_ns) is None
    cache.close()

    reopened = _PlanningMetadataCache(cache_path)
    assert reopened.connection is not None
    try:
        row = reopened.connection.execute(
            "SELECT payload FROM psarc_metadata_v1 WHERE path = ?",
            (cache_key,),
        ).fetchone()
        assert row is None
    finally:
        reopened.close()


def test_batch_cache_compare_and_delete_preserves_concurrent_replacement(
    tmp_path: Path,
) -> None:
    source = tmp_path / "disc.psarc"
    source.write_bytes(b"source fingerprint")
    stat = source.stat()
    cache_path = tmp_path / "planning.sqlite3"
    cache = _PlanningMetadataCache(cache_path)
    assert cache.connection is not None
    cache_key = cache._key(source)
    stale_raw = json.dumps(
        {
            "version": 3,
            "songs": [{"key": "target", "metadata": {"title": "Stale"}}],
        },
        separators=(",", ":"),
    )
    fresh = PsarcPlanningData(
        source,
        stat.st_size,
        stat.st_mtime_ns,
        [("target", {"title": "Fresh"})],
    )
    fresh_raw = json.dumps(
        fresh.to_cache_payload(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    cache.connection.execute(
        """
        INSERT INTO psarc_metadata_v1(path, size, mtime_ns, payload, last_used)
        VALUES (?, ?, ?, ?, ?)
        """,
        (cache_key, stat.st_size, str(stat.st_mtime_ns), stale_raw, 2_000_000_000),
    )
    cache.connection.commit()

    base_connection = cache.connection
    writer = sqlite3.connect(cache_path, timeout=10)
    writer.execute("PRAGMA journal_mode=WAL")

    class InterleavingConnection:
        replaced = False

        def execute(
            self,
            statement: str,
            parameters: tuple[object, ...] = (),
        ) -> sqlite3.Cursor:
            if statement.lstrip().startswith("DELETE FROM psarc_metadata_v1"):
                writer.execute(
                    "UPDATE psarc_metadata_v1 SET payload = ?, last_used = ? WHERE path = ?",
                    (fresh_raw, 2_000_000_000, cache_key),
                )
                writer.commit()
                self.replaced = True
            return base_connection.execute(statement, parameters)

        def commit(self) -> None:
            base_connection.commit()

        def close(self) -> None:
            base_connection.close()

    proxy = InterleavingConnection()
    cache.connection = proxy  # type: ignore[assignment]
    try:
        assert cache.get(source, stat.st_size, stat.st_mtime_ns) is None
        assert proxy.replaced is True
    finally:
        cache.close()
        writer.close()

    reopened = _PlanningMetadataCache(cache_path)
    try:
        assert reopened.get(source, stat.st_size, stat.st_mtime_ns) == fresh
    finally:
        reopened.close()
