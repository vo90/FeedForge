from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from feedback_converter import converter


def _minimal_xml(
    *,
    arrangement: str = "Lead",
    title: str = "Recovered Song",
    artist: str = "Recovery Artist",
    duration: float = 5,
) -> bytes:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<song version="7">
  <title>{title}</title>
  <artistName>{artist}</artistName>
  <arrangement>{arrangement}</arrangement>
  <part>1</part>
  <offset>0</offset>
  <centOffset>12</centOffset>
  <songLength>{duration}</songLength>
  <tuning string0="0" string1="0" string2="0" string3="0" string4="0" string5="0" />
  <capo>0</capo>
  <phrases><phrase disparity="0" ignore="0" maxDifficulty="0" name="VERSE" solo="0" /></phrases>
  <phraseIterations><phraseIteration time="0" phraseId="0" /></phraseIterations>
  <chordTemplates />
  <ebeats><ebeat time="0" measure="1" /></ebeats>
  <sections><section name="verse" number="1" startTime="0" /></sections>
  <levels>
    <level difficulty="0">
      <notes><note time="1" string="0" fret="3" sustain="0.5" /></notes>
      <chords />
      <anchors><anchor time="0.5" fret="1" width="4" /></anchors>
      <handShapes />
    </level>
  </levels>
</song>
""".encode("utf-8")


def _manifest(
    *,
    chord_templates: list[dict[str, object]] | None = None,
    song_key: str = "Song",
    title: str = "Recovered Song",
    artist: str = "Recovery Artist",
    duration: float = 5,
    song_xml: str = "urn:application:xml:song_lead",
    arrangement: str = "Lead",
    dlc_key: str | None = None,
    arrangement_properties: dict[str, object] | None = None,
) -> bytes:
    attributes: dict[str, object] = {
        "SongKey": song_key,
        "SongName": title,
        "ArtistName": artist,
        "SongLength": duration,
        "SongXml": song_xml,
        "ArrangementName": arrangement,
        "ArrangementProperties": arrangement_properties or {
            "pathLead": 1,
            "pathRhythm": 0,
            "pathBass": 0,
            "bonusArr": 0,
            "represent": 1,
        },
    }
    if dlc_key is not None:
        attributes["DLCKey"] = dlc_key
    if chord_templates is not None:
        attributes["ChordTemplates"] = chord_templates
    return json.dumps({"Entries": {"fixture": {"Attributes": attributes}}}).encode()


def _content(*, sng: bytes = b"", xml: bytes | None = None) -> dict[str, bytes]:
    return {
        "manifests/songs/song_lead.json": _manifest(),
        "songs/bin/generic/song_lead.sng": sng,
        "songs/arr/song_lead.xml": xml if xml is not None else _minimal_xml(),
        "audio/song.ogg": b"OggS-fixture",
    }


def test_empty_sng_is_converted_from_its_exact_xml_sidecar(tmp_path: Path) -> None:
    input_path = tmp_path / "song.psarc"
    input_path.write_bytes(b"fixture")
    output = tmp_path / "recovered.feedpak.dir"

    result = converter.convert_psarc(
        input_path,
        output,
        archive=False,
        _content=_content(),
    )

    manifest = yaml.safe_load((output / "manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["artist"] == "Recovery Artist"
    assert manifest["title"] == "Recovered Song"
    assert len(manifest["arrangements"]) == 1
    entry = manifest["arrangements"][0]
    assert entry["id"] == "lead"
    assert entry["centOffset"] == 12.0
    arrangement = json.loads((output / entry["file"]).read_text(encoding="utf-8"))
    assert arrangement["notes"] == [{"t": 1.0, "s": 0, "f": 3, "sus": 0.5}]
    assert any(
        "Recovered Lead from embedded Rocksmith XML" in warning.message
        and "compiled SNG songs/bin/generic/song_lead.sng was empty" in warning.message
        for warning in result.warnings
    )


def test_valid_playable_sng_remains_primary_even_if_xml_is_bad(monkeypatch) -> None:
    primary = SimpleNamespace(levels=[object()])

    class FakeSong:
        @staticmethod
        def parse(data: bytes):
            assert data == b"valid-sng"
            return primary

    monkeypatch.setattr(converter, "Song", FakeSong)
    content = _content(sng=b"valid-sng", xml=b"not XML")

    song, recovery, warning = converter._parse_sng_with_xml_fallback(
        content,
        "songs/bin/generic/song_lead.sng",
        b"valid-sng",
        converter._extract_metadata(content),
    )

    assert song is primary
    assert recovery is None
    assert warning is None


def test_unreadable_sng_never_borrows_a_foreign_xml_chart(monkeypatch) -> None:
    class BrokenSong:
        @staticmethod
        def parse(_data: bytes):
            raise ValueError("bad compiled chart")

    monkeypatch.setattr(converter, "Song", BrokenSong)
    content = {
        "songs/bin/generic/song_lead.sng": b"broken",
        "songs/arr/other_lead.xml": _minimal_xml(title="Foreign Song"),
    }

    song, recovery, warning = converter._parse_sng_with_xml_fallback(
        content,
        "songs/bin/generic/song_lead.sng",
        b"broken",
        {},
    )

    assert song is None
    assert recovery is None
    assert warning is not None
    assert "No exact XML sidecar 'songs/arr/song_lead.xml' exists" in warning


def test_exact_xml_fallback_receives_authoritative_manifest_chord_hints() -> None:
    chord_templates = [
        {
            "ChordId": 3,
            "ChordName": "E",
            "Frets": [0, 2, 2, 1, 0, 0],
            "Fingers": [-1, 2, 3, 1, -1, -1],
        }
    ]
    content = {
        "manifests/songs/song_lead.json": _manifest(
            chord_templates=chord_templates
        )
    }
    metadata = converter._extract_metadata(content)

    hints = converter._compiled_chord_template_hints_for_arrangement(
        "songs/bin/generic/song_lead.sng",
        metadata,
    )

    assert hints is not None
    assert list(hints) == [3]
    assert hints[3].name == "E"
    assert hints[3].frets == (0, 2, 2, 1, 0, 0)
    assert hints[3].mask is None


def test_exact_same_stem_xml_with_foreign_identity_is_rejected() -> None:
    content = _content(
        xml=_minimal_xml(title="Foreign Song", artist="Foreign Artist")
    )

    song, recovery, warning = converter._parse_sng_with_xml_fallback(
        content,
        "songs/bin/generic/song_lead.sng",
        b"",
        converter._extract_metadata(content),
    )

    assert song is None
    assert recovery is None
    assert warning is not None
    assert "Title mismatch" in warning


def test_conflicting_exact_songxml_manifest_identities_are_rejected() -> None:
    first = json.loads(_manifest())
    second = json.loads(_manifest(title="Different Song"))
    content = _content()
    content["manifests/songs/song_lead.json"] = json.dumps(
        {
            "Entries": {
                "first": first["Entries"]["fixture"],
                "second": second["Entries"]["fixture"],
            }
        }
    ).encode()

    song, recovery, warning = converter._parse_sng_with_xml_fallback(
        content,
        "songs/bin/generic/song_lead.sng",
        b"",
        converter._extract_metadata(content),
    )

    assert song is None
    assert recovery is None
    assert warning is not None
    assert "ambiguous exact SongXml identities" in warning


def test_manifest_xml_duration_tolerance_accepts_its_boundary() -> None:
    content = _content(xml=_minimal_xml(duration=5.01))

    song, recovery, warning = converter._parse_sng_with_xml_fallback(
        content,
        "songs/bin/generic/song_lead.sng",
        b"",
        converter._extract_metadata(content),
    )

    assert song is not None
    assert recovery is not None
    assert warning is None


def test_manifest_xml_duration_tolerance_rejects_above_its_boundary() -> None:
    content = _content(xml=_minimal_xml(duration=5.011))

    song, recovery, warning = converter._parse_sng_with_xml_fallback(
        content,
        "songs/bin/generic/song_lead.sng",
        b"",
        converter._extract_metadata(content),
    )

    assert song is None
    assert recovery is None
    assert warning is not None
    assert "Duration mismatch" in warning
    assert "allowed difference 0.010s" in warning


def test_real_rs1_combo_manifest_shape_binds_to_exact_xml() -> None:
    content = {
        "manifests/songs/risingsun_combo.json": _manifest(
            song_key="RisingSun",
            dlc_key="RS1CompatibilityDisc",
            title="House of the Rising Sun",
            artist="The Animals",
            duration=281.811,
            song_xml="urn:application:xml:risingsun_combo",
            arrangement="Combo",
            # RS1 Combo records use their playable path flag here; the
            # explicit arrangement identity remains Combo.
            arrangement_properties={
                "pathLead": 0,
                "pathRhythm": 1,
                "pathBass": 0,
                "bonusArr": 0,
                "represent": 1,
            },
        ),
        "songs/bin/generic/risingsun_combo.sng": b"",
        "songs/arr/risingsun_combo.xml": _minimal_xml(
            arrangement="Combo",
            title="House of the Rising Sun",
            artist="The Animals",
            duration=281.811,
        ),
    }

    song, recovery, warning = converter._parse_sng_with_xml_fallback(
        content,
        "songs/bin/generic/risingsun_combo.sng",
        b"",
        converter._extract_metadata(content),
    )

    assert song is not None
    assert recovery is not None
    assert warning is None
    assert song.song_key == "RisingSun"
    assert song.arrangement == "Combo"


def test_real_rs1_legacy_xml_arrangement_labels_are_manifest_scoped() -> None:
    cases = (
        # Official Combo slots sometimes retain the playable route in XML.
        ("combo1", "Combo", "Lead", 1, 0),
        # One official Lead slot is routed as Rhythm and labels its XML so.
        ("lead", "Lead", "Rhythm", 0, 1),
        # Two official alternative Lead slots retain a legacy Combo XML label.
        ("lead2", "Lead", "Combo", 1, 0),
    )
    for suffix, manifest_arrangement, xml_arrangement, path_lead, path_rhythm in cases:
        stem = f"song_{suffix}"
        content = {
            f"manifests/songs/{stem}.json": _manifest(
                song_xml=f"urn:application:xml:{stem}",
                arrangement=manifest_arrangement,
                arrangement_properties={
                    "pathLead": path_lead,
                    "pathRhythm": path_rhythm,
                    "pathBass": 0,
                    "bonusArr": 0,
                    "represent": 1,
                },
            ),
            f"songs/bin/generic/{stem}.sng": b"",
            f"songs/arr/{stem}.xml": _minimal_xml(
                arrangement=xml_arrangement,
            ),
        }

        song, recovery, warning = converter._parse_sng_with_xml_fallback(
            content,
            f"songs/bin/generic/{stem}.sng",
            b"",
            converter._extract_metadata(content),
        )

        assert song is not None, warning
        assert recovery is not None
        assert warning is None
        assert song.arrangement == xml_arrangement


def test_unrelated_xml_arrangement_label_is_not_authorized_by_manifest_route() -> None:
    content = {
        "manifests/songs/song_combo.json": _manifest(
            song_xml="urn:application:xml:song_combo",
            arrangement="Combo",
            arrangement_properties={
                "pathLead": 1,
                "pathRhythm": 0,
                "pathBass": 0,
                "bonusArr": 0,
                "represent": 1,
            },
        ),
        "songs/bin/generic/song_combo.sng": b"",
        "songs/arr/song_combo.xml": _minimal_xml(arrangement="Bass"),
    }

    song, recovery, warning = converter._parse_sng_with_xml_fallback(
        content,
        "songs/bin/generic/song_combo.sng",
        b"",
        converter._extract_metadata(content),
    )

    assert song is None
    assert recovery is None
    assert warning is not None
    assert "manifest-authorized kinds" in warning
