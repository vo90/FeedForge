import json
from types import SimpleNamespace

import pytest
import yaml

from feedback_converter import converter


def _ns(**kwargs):
    return SimpleNamespace(**kwargs)


def _properties(*, route="lead", represent=1, bonus=0):
    return {
        "pathLead": int(route == "lead"),
        "pathRhythm": int(route == "rhythm"),
        "pathBass": int(route == "bass"),
        "bonusArr": bonus,
        "represent": represent,
    }


def _manifest_item(stem, *, name="Lead", properties=None):
    item = {
        "SongXml": f"urn:application:xml:{stem}",
        "ArrangementName": name,
    }
    if properties is not None:
        item["ArrangementProperties"] = properties
    return item


def _empty_song(tuning=None):
    level = _ns(difficulty=0, notes=[], anchors=[], fingerprints=[[], []])
    return _ns(
        metadata=_ns(
            tuning=list(tuning or [0, 0, 0, 0, 0, 0]),
            capo=0,
            songLength=5.0,
        ),
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


@pytest.mark.parametrize(
    ("route", "expected"),
    [("lead", "Lead"), ("rhythm", "Rhythm"), ("bass", "Bass")],
)
def test_representative_route_flags_define_primary_identity(route, expected):
    item = _manifest_item(
        "song_part",
        name="Misleading name",
        properties=_properties(route=route, represent=1),
    )

    assert converter._arrangement_label_from_manifest(item) == expected


@pytest.mark.parametrize("represent", [0, "0", False, 2])
def test_explicit_nonrepresentative_arrangements_are_alternate(represent):
    item = _manifest_item(
        "song_lead",
        properties=_properties(route="lead", represent=represent),
    )

    assert converter._arrangement_label_from_manifest(item) == "Alt. Lead"


def test_bonus_identity_takes_precedence_over_representative_status():
    primary_bonus = _manifest_item(
        "song_lead", properties=_properties(route="lead", represent=1, bonus=1)
    )
    alternate_bonus = _manifest_item(
        "song_lead2", properties=_properties(route="lead", represent=0, bonus="1")
    )

    assert converter._arrangement_label_from_manifest(primary_bonus) == "Bonus Lead"
    assert converter._arrangement_label_from_manifest(alternate_bonus) == "Bonus Lead"


@pytest.mark.parametrize("represent", [None, "unknown", 0.5])
def test_missing_or_unparseable_represent_preserves_plain_role(represent):
    props = _properties(route="lead")
    if represent is None:
        props.pop("represent")
    else:
        props["represent"] = represent
    item = _manifest_item("song_lead", properties=props)

    assert converter._arrangement_label_from_manifest(item) == "Lead"


def test_full_manifest_representative_fallback_is_used_when_property_is_missing():
    props = _properties(route="rhythm")
    props.pop("represent")
    item = _manifest_item("song_rhythm", properties=props)
    item["Representative"] = "0"

    assert converter._arrangement_label_from_manifest(item) == "Alt. Rhythm"
    source = converter._arrangement_sources([item])["song_rhythm"][0]
    assert source["arrangement_properties"]["represent"] == 0


def test_decorated_fallback_names_are_not_double_prefixed():
    alternate = _manifest_item(
        "song_lead",
        name="Alt. Lead",
        properties={"bonusArr": 0, "represent": 0},
    )
    bonus = _manifest_item(
        "song_lead2",
        name="Bonus Lead",
        properties={"bonusArr": 1, "represent": 0},
    )

    assert converter._arrangement_label_from_manifest(alternate) == "Alt. Lead"
    assert converter._arrangement_label_from_manifest(bonus) == "Bonus Lead"


def test_arrangement_id_uses_exact_song_xml_stem_not_substring_order():
    names = converter._arrangement_names(
        [
            _manifest_item("song_lead", properties=_properties(represent=1)),
            _manifest_item("song_lead2", properties=_properties(represent=0)),
        ]
    )
    metadata = {"arrangement_names": names}

    assert converter._arrangement_id("songs/bin/generic/song_lead.sng", metadata) == "lead"
    assert (
        converter._arrangement_id("songs/bin/generic/song_lead2.sng", metadata)
        == "alt-lead"
    )


def test_exact_manifest_match_preserves_normalized_source_identity():
    primary = _manifest_item(
        "song_lead", properties=_properties(route="lead", represent="1", bonus=False)
    )
    alternate = _manifest_item(
        "song_lead2", properties=_properties(route="lead", represent="0")
    )
    sources = converter._arrangement_sources([primary, alternate])
    metadata = {"arrangement_sources": sources}

    assert converter._source_identity_for_arrangement(
        "songs/bin/generic/song_lead.sng", metadata
    ) == {
        "format": "psarc-manifest2014",
        "arrangement_properties": {
            "pathLead": 1,
            "pathRhythm": 0,
            "pathBass": 0,
            "bonusArr": 0,
            "represent": 1,
        },
    }
    assert converter._source_identity_for_arrangement(
        "songs/bin/generic/song_lead2.sng", metadata
    )["arrangement_properties"]["represent"] == 0
    assert (
        converter._source_identity_for_arrangement(
            "songs/bin/generic/song_lead3.sng", metadata
        )
        is None
    )


def test_conflicting_or_incomplete_source_identity_is_omitted():
    first = _manifest_item("song_lead", properties=_properties(represent=1))
    conflicting = _manifest_item("song_lead", properties=_properties(represent=0))
    incomplete = _manifest_item(
        "song_rhythm",
        properties={"pathRhythm": 1, "bonusArr": 0, "represent": 1},
    )
    metadata = {
        "arrangement_sources": converter._arrangement_sources(
            [first, conflicting, incomplete]
        )
    }

    assert (
        converter._source_identity_for_arrangement(
            "songs/bin/generic/song_lead.sng", metadata
        )
        is None
    )
    assert (
        converter._source_identity_for_arrangement(
            "songs/bin/generic/song_rhythm.sng", metadata
        )
        is None
    )


def test_conversion_keeps_unique_manifest_and_chart_names_in_sync(tmp_path, monkeypatch):
    class FakeSong:
        @staticmethod
        def parse(_data):
            return _empty_song()

    monkeypatch.setattr(converter, "Song", FakeSong)
    input_path = tmp_path / "song.psarc"
    input_path.write_bytes(b"fixture")
    output = tmp_path / "song.feedpak.work"
    items = [
        _manifest_item("song_lead", properties=_properties(represent=1)),
        _manifest_item("song_lead2", properties=_properties(represent=0)),
        _manifest_item("song_lead3", properties=_properties(represent=0)),
        _manifest_item(
            "song_lead_bonus", properties=_properties(represent=0, bonus=1)
        ),
    ]
    content = {
        f"manifests/song/{index}.json": json.dumps(
            {
                "Attributes": {
                    **item,
                    "SongName": "Song",
                    "ArtistName": "Artist",
                    "SongLength": 5.0,
                }
            }
        ).encode()
        for index, item in enumerate(items)
    }
    content.update(
        {
            f"songs/bin/generic/{stem}.sng": b"sng"
            for stem in ("song_lead", "song_lead2", "song_lead3", "song_lead_bonus")
        }
    )
    content["audio/windows/song.wem"] = b"wem-data"

    converter.convert_psarc(
        input_path,
        output,
        archive=False,
        _content=content,
    )

    manifest = yaml.safe_load((output / "manifest.yaml").read_text(encoding="utf-8"))
    expected = {
        "lead": "Lead",
        "alt-lead": "Alt. Lead",
        "alt-lead-2": "Alt. Lead 2",
        "bonus-lead": "Bonus Lead",
    }
    assert {entry["id"]: entry["name"] for entry in manifest["arrangements"]} == expected
    for entry in manifest["arrangements"]:
        arrangement = json.loads(
            (output / entry["file"]).read_text(encoding="utf-8")
        )
        assert arrangement["name"] == entry["name"]
        assert arrangement["ext"]["source"]["format"] == "psarc-manifest2014"


def test_seven_string_name_transform_remains_manifest_consistent(tmp_path, monkeypatch):
    class FakeSong:
        @staticmethod
        def parse(_data):
            return _empty_song(converter.B_STANDARD_6_TUNING)

    monkeypatch.setattr(converter, "Song", FakeSong)
    input_path = tmp_path / "song.psarc"
    input_path.write_bytes(b"fixture")
    output = tmp_path / "song.feedpak.work"
    item = _manifest_item("song_lead", properties=_properties(represent=0))
    content = {
        "manifests/song/lead.json": json.dumps(
            {
                "Attributes": {
                    **item,
                    "SongName": "Song",
                    "ArtistName": "Artist",
                    "SongLength": 5.0,
                }
            }
        ).encode(),
        "songs/bin/generic/song_lead.sng": b"sng",
        "audio/windows/song.wem": b"wem-data",
    }

    converter.convert_psarc(
        input_path,
        output,
        archive=False,
        b_standard_to_7_string=True,
        _content=content,
    )

    manifest = yaml.safe_load((output / "manifest.yaml").read_text(encoding="utf-8"))
    entry = manifest["arrangements"][0]
    arrangement = json.loads((output / entry["file"]).read_text(encoding="utf-8"))
    assert entry["id"] == "alt-lead"
    assert entry["name"] == arrangement["name"] == "Alt. Lead 7-string"
