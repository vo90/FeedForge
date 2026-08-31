import json
from types import SimpleNamespace

import pytest
import yaml

from feedback_converter import converter


def _ns(**kwargs):
    return SimpleNamespace(**kwargs)


def _manifest_item(song_xml, persistent_id, master_id, **extra):
    return {
        "SongXml": song_xml,
        "PersistentID": persistent_id,
        "MasterID_RDV": master_id,
        "ArrangementName": "Lead",
        **extra,
    }


def _empty_song():
    level = _ns(difficulty=0, notes=[], anchors=[], fingerprints=[[], []])
    return _ns(
        metadata=_ns(tuning=[0, 0, 0, 0, 0, 0], capo=0, songLength=5.0),
        chordTemplates=[],
        chordNotes=[],
        levels=[level],
        phraseIterations=[],
        phrases=[],
        beats=[],
        sections=[],
        tones=[],
        vocals=[],
    )


def test_duplicate_lead_arrangements_match_exact_song_xml_stems():
    content = {
        "manifests/song/song.hsan": json.dumps(
            {
                "Entries": [
                    {"Attributes": {"PersistentID": "A", "MasterID_RDV": 101, "CentOffset": 11.8}},
                    {"Attributes": {"PersistentID": "B", "MasterID_RDV": 202, "CentOffset": -1200}},
                ]
            }
        ).encode(),
        "manifests/song/song_lead.json": json.dumps(
            {"Attributes": _manifest_item("urn:application:xml:song_lead", "A", 101)}
        ).encode(),
        "manifests/song/song_lead2.json": json.dumps(
            {"Attributes": _manifest_item("urn:application:xml:song_lead2", "B", 202)}
        ).encode(),
    }

    metadata = converter._extract_metadata(content)

    assert converter._cent_offset_for_arrangement(
        "songs/bin/generic/song_lead.sng", metadata
    ) == (11.8, None)
    assert converter._cent_offset_for_arrangement(
        "songs/bin/generic/song_lead2.sng", metadata
    ) == (-1200.0, None)
    assert converter._cent_offset_for_arrangement(
        "songs/bin/generic/song_lead3.sng", metadata
    ) == (0.0, None)


@pytest.mark.parametrize(
    ("attributes", "expected", "warning_fragment"),
    [
        ({"CentOffset": 0}, 0.0, None),
        ({}, 0.0, None),
        ({"CentOffset": "11.8"}, 11.8, None),
        ({"CentOffset": "invalid"}, 0.0, "Invalid CentOffset"),
        ({"CentOffset": True}, 0.0, "Invalid CentOffset"),
        ({"CentOffset": float("nan")}, 0.0, "Invalid CentOffset"),
        ({"CentOffset": float("inf")}, 0.0, "Invalid CentOffset"),
    ],
)
def test_cent_offset_defaults_and_sanitizes_invalid_values(
    attributes, expected, warning_fragment
):
    item = _manifest_item("urn:application:xml:song_lead", "A", 101, **attributes)
    entries = converter._arrangement_cent_offsets([item])

    value, warning = converter._cent_offset_for_arrangement(
        "songs/bin/generic/song_lead.sng",
        {"arrangement_cent_offsets": entries},
    )

    assert value == expected
    if warning_fragment is None:
        assert warning is None
    else:
        assert warning_fragment in warning


def test_conflicting_exact_song_xml_matches_warn_and_default_to_zero():
    entries = converter._arrangement_cent_offsets(
        [
            _manifest_item(
                "urn:application:xml:song_lead", "A", 101, CentOffset=12.0
            ),
            _manifest_item(
                "urn:application:xml:song_lead", "B", 202, CentOffset=-5.0
            ),
        ]
    )

    value, warning = converter._cent_offset_for_arrangement(
        "songs/bin/generic/song_lead.sng",
        {"arrangement_cent_offsets": entries},
    )

    assert value == 0.0
    assert warning == (
        "Ambiguous CentOffset for songs/bin/generic/song_lead.sng; used 0.0."
    )


def test_conflicting_identity_join_values_are_not_selected_by_order():
    entries = converter._arrangement_cent_offsets(
        [
            {"PersistentID": "A", "CentOffset": 12.0},
            {"PersistentID": "A", "CentOffset": -5.0},
            _manifest_item("urn:application:xml:song_lead", "A", 101),
        ]
    )

    value, warning = converter._cent_offset_for_arrangement(
        "songs/bin/generic/song_lead.sng",
        {"arrangement_cent_offsets": entries},
    )

    assert value == 0.0
    assert warning == (
        "Ambiguous CentOffset for songs/bin/generic/song_lead.sng; used 0.0."
    )


def test_conversion_writes_cent_offset_to_chart_and_manifest(tmp_path, monkeypatch):
    class FakeSong:
        @staticmethod
        def parse(_data):
            return _empty_song()

    monkeypatch.setattr(converter, "Song", FakeSong)
    input_path = tmp_path / "song.psarc"
    input_path.write_bytes(b"fixture")
    output = tmp_path / "song.feedpak.work"
    content = {
        "manifests/song/song_lead.json": json.dumps(
            {
                "Attributes": _manifest_item(
                    "urn:application:xml:song_lead",
                    "A",
                    101,
                    SongName="Song",
                    ArtistName="Artist",
                    SongLength=5.0,
                    CentOffset=13.25,
                )
            }
        ).encode(),
        "songs/bin/generic/song_lead.sng": b"sng",
        "audio/windows/song.wem": b"wem-data",
    }

    result = converter.convert_psarc(
        input_path,
        output,
        archive=False,
        _content=content,
    )

    manifest = yaml.safe_load((output / "manifest.yaml").read_text(encoding="utf-8"))
    manifest_arrangement = manifest["arrangements"][0]
    arrangement = json.loads(
        (output / manifest_arrangement["file"]).read_text(encoding="utf-8")
    )
    assert manifest_arrangement["centOffset"] == 13.25
    assert arrangement["centOffset"] == 13.25
    assert result.manifest["arrangements"][0]["centOffset"] == 13.25


def test_cent_offset_changes_no_other_arrangement_data():
    song = _empty_song()
    baseline = converter._song_to_arrangement(
        song,
        "songs/bin/generic/song_lead.sng",
        {},
        include_tones=False,
    )
    shifted = converter._song_to_arrangement(
        song,
        "songs/bin/generic/song_lead.sng",
        {},
        include_tones=False,
        cent_offset=-1200.0,
    )

    assert baseline.pop("centOffset") == 0.0
    assert shifted.pop("centOffset") == -1200.0
    assert shifted == baseline
