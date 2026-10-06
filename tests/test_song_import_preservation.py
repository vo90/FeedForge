"""Source mistakes remain source mistakes; conversion preserves representation."""
import base64
from copy import deepcopy
import json
import xml.etree.ElementTree as ET
from zipfile import ZipFile

import pytest

from feedback_converter.song_import import ScoreImportError, load_performance
from test_song_import_score import beat, import_json, measure, raw_score, write_gp


def test_authored_chord_keeps_every_note_tie_and_unknown_fingering(tmp_path):
    first = {"duration": [1, 2], "notes": [{"string": 0, "fret": 17}, {"string": 1, "fret": 1, "ghost": True}]}
    second = {"duration": [1, 2], "notes": [{"string": 0, "fret": 17, "tie": True}]}
    document = raw_score([measure(first, second)])
    original = deepcopy(document)
    result = import_json(tmp_path, document)
    track = result["tracks"][0]
    assert track["notes"] == []
    assert len(track["chords"]) == 1
    notes = track["chords"][0]["notes"]
    assert [(n["s"], n["f"], n["sus"]) for n in notes] == [(4, 1, 1), (5, 17, 2)]
    assert notes[0]["ghost"] and not notes[0].get("mt") and not notes[0].get("ig")
    assert len(notes[1]["source_ids"]) == 2
    assert track["templates"] == [{"name": "", "frets": [-1, -1, -1, -1, 1, 17], "fingers": [-1] * 6}]
    assert result["sourceScore"]["document"] == original == document
    json.dumps(result, allow_nan=False)


def test_coincident_independent_voices_are_not_invented_chords(tmp_path):
    document = raw_score([measure(beat(3))])
    document["parts"][0]["measures"][0]["voices"].append({"beats": [beat(7, string=1)]})
    track = import_json(tmp_path, document)["tracks"][0]
    assert len(track["notes"]) == 2 and not track["chords"]
    assert len(track["notation"]["measures"][0]["staves"]["staff"]["voices"]) == 2


def test_explicit_instrument_program_takes_priority_over_part_name(tmp_path):
    document = raw_score([measure(beat())])
    document["tracks"][0]["name"] = "Bass drum cues on guitar"
    document["tracks"].append({"id": 1, "name": "Bass Clarinet", "instrument": "Bass Clarinet", "instrumentId": 71})
    document["parts"].append(deepcopy(document["parts"][0]))
    result = import_json(tmp_path, document)
    assert len(result["tracks"]) == 1 and result["tracks"][0]["instrument"] == "guitar"
    assert result["source"]["excludedTracks"][0]["name"] == "Bass Clarinet"


def test_equivalent_same_string_voices_are_combined_with_explicit_source_receipt(tmp_path):
    document = raw_score([measure(beat(3))])
    document["parts"][0]["measures"][0]["voices"].append({"beats": [beat(3)]})
    result=import_json(tmp_path, document)
    assert len(result['tracks'][0]['notes'])==1
    attack=result['voiceProjection']['tracks'][0]['combinedAttacks'][0]
    assert attack['rule']=='equivalent' and len(attack['sourceIds'])==2


def test_beat_techniques_and_direction_only_slide_are_preserved(tmp_path):
    b = {**beat(16, slide="downwards"), "tapping": True, "vibrato": True}
    result = import_json(tmp_path, raw_score([measure(b)]))
    note = result["tracks"][0]["notes"][0]
    assert note["tp"] and not note.get("vb") and note["slide_out"] == "down"
    assert 'vibrato_marks' not in note
    assert "slu" not in note and not any("five-fret" in warning for warning in result["warnings"])
    notation = result["tracks"][0]["notation"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"][0]
    assert notation["tap"] and notation["vib"]
    assert not notation['notes'][0].get('vib')
    assert any(row['feature'] == 'beat.vibrato' for row in result['compatibilityReport']['findings'])


def test_notation_retains_rests_text_dynamics_tuplets_and_ties(tmp_path):
    a = {**beat(2, duration=(1, 12)), "type": 8, "tuplet": 3, "tupletStart": True, "velocity": "mp", "text": {"text": "as written"}, "gradualVelocity": "decrescendo"}
    b = {**beat(2, duration=(1, 12), tie=True), "type": 8}
    rest = {"duration": [1, 12], "type": 8, "tupletStop": True, "notes": [{"rest": True}], "rest": True}
    result = import_json(tmp_path, raw_score([measure(a, b, rest)]))
    beats = result["tracks"][0]["notation"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"]
    assert beats[0]["tu"] == [3, 2] and beats[0]["dyn"] == "mp" and beats[0]["dec"]
    assert beats[0]["txt"] == "as written" and beats[0]["beat_pos"] == [0, 1]
    assert beats[1]["notes"][0]["tied"] and beats[1]["beat_pos"] == [1, 3]
    assert beats[2]["rest"] and "notes" not in beats[2]
    assert beats[1]["end_time"] == pytest.approx(1 / 3)


def test_downbeats_and_time_signatures_have_feedpak_semantics(tmp_path):
    result = import_json(tmp_path, raw_score([measure(beat()),
        measure(beat(duration=(3, 4)), signature=[3, 4])]))
    assert [b["measure"] for b in result["beats"]] == [1, -1, -1, -1, 2, -1, -1]
    assert result["time_signatures"] == [{"time": 0, "ts": [4, 4]}, {"time": 2, "ts": [3, 4]}]


def test_unknown_active_music_cannot_disappear(tmp_path):
    with pytest.raises(ScoreImportError, match="futureTechnique"):
        import_json(tmp_path, raw_score([measure(beat(futureTechnique=True))]))
    with pytest.raises(ScoreImportError, match="futureTechnique"):
        import_json(tmp_path, raw_score([measure(beat(futureTechnique=0))]))


def test_unknown_timing_automation_cannot_disappear(tmp_path):
    document = raw_score([measure(beat())])
    document["parts"][0]["automations"]["futureTempoRamp"] = {"amount": 1}
    with pytest.raises(ScoreImportError, match="futureTempoRamp"):
        import_json(tmp_path, document)


def test_unmapped_harmonic_position_is_not_silently_discarded(tmp_path):
    with pytest.raises(ScoreImportError, match="harmonicFret"):
        import_json(tmp_path, raw_score([measure(beat(harmonic="natural", harmonicFret=12))]))


def test_unknown_inactive_and_metadata_are_preserved_and_accounted(tmp_path):
    document = raw_score([measure(beat(futureTechnique=False))])
    document["futureMetadata"] = {"value": 4}
    result = import_json(tmp_path, document)
    assert result["sourceScore"]["document"] == document
    assert any(e["field"] == "futureTechnique" and e["handling"] == "inactive_unknown" for e in result["featureInventory"])
    assert any("futureMetadata" in warning for warning in result["warnings"])


def test_inventory_distinguishes_playable_techniques_from_notation(tmp_path):
    document = raw_score([measure(beat(12, ghost=True, slide="downwards"))])
    result = import_json(tmp_path, document)
    entries = {e["field"]: e for e in result["featureInventory"] if e["scope"] == "Songsterr note"}
    assert entries["ghost"]["representations"] == ["notation", "playable", "source"]
    assert entries["slide"]["representations"] == ["playable", "source"]


def test_notation_unsupported_duration_is_retained_without_quantization(tmp_path):
    result = import_json(tmp_path, raw_score([measure({**beat(duration=(1, 64)), "type": 64})]))
    assert result["tracks"][0]["notes"][0]["sus"] == 0.03125
    assert "notation" not in result["tracks"][0]
    assert any("outside FeedPak notation" in warning for warning in result["warnings"])


def test_gpif_preserves_exact_source_xml_chord_and_dynamic(tmp_path):
    path = write_gp(tmp_path)
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("Content/score.gpif"))
    b = root.find("Beats/Beat")
    b.find("Notes").text = "0 1"
    ET.SubElement(b, "Dynamic").text = "MF"
    ET.SubElement(b, "FreeText").text = "Original annotation"
    n = deepcopy(root.find("Notes/Note"))
    n.set("id", "1")
    n.find("Properties/Property[@name='String']/String").text = "1"
    ET.SubElement(n, "AntiAccent")
    root.find("Notes").append(n)
    xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    source = tmp_path / "source.gpif"
    source.write_bytes(xml)
    result = load_performance(source)
    assert base64.b64decode(result["sourceScore"]["data"]) == xml
    track = result["tracks"][0]
    assert len(track["chords"][0]["notes"]) == 2 and track["chords"][0]["notes"][1]["ghost"]
    written = track["notation"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"][0]
    assert written["dyn"] == "mf" and written["txt"] == "Original annotation"


def test_gpif_unknown_beat_property_is_explicit(tmp_path):
    path = write_gp(tmp_path)
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("Content/score.gpif"))
    ET.SubElement(root.find("Beats/Beat"), "FutureTechnique")
    source = tmp_path / "unsupported.gpif"
    source.write_bytes(ET.tostring(root))
    with pytest.raises(ScoreImportError, match="FutureTechnique"):
        load_performance(source)


def test_gpif_unknown_rhythm_field_is_explicit(tmp_path):
    path = write_gp(tmp_path)
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("Content/score.gpif"))
    ET.SubElement(root.find("Rhythms/Rhythm"), "FutureDuration")
    source = tmp_path / "unsupported.gpif"
    source.write_bytes(ET.tostring(root))
    with pytest.raises(ScoreImportError, match="FutureDuration"):
        load_performance(source)


@pytest.mark.parametrize("kind,wide", [("Slight", False), ("Wide", True)])
def test_gpif_vibrato_width_is_retained_in_notation(tmp_path, kind, wide):
    path = write_gp(tmp_path)
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("Content/score.gpif"))
    ET.SubElement(root.find("Notes/Note"), "Vibrato").text = kind
    source = tmp_path / "vibrato.gpif"
    source.write_bytes(ET.tostring(root))
    track = load_performance(source)["tracks"][0]
    assert track["notes"][0]["vb"]
    note = track["notation"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"][0]["notes"][0]
    assert note["vib"] and bool(note.get("vibw")) is wide


def test_gpif_unknown_vibrato_kind_is_explicit(tmp_path):
    path = write_gp(tmp_path)
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("Content/score.gpif"))
    ET.SubElement(root.find("Notes/Note"), "Vibrato").text = "FutureVibrato"
    source = tmp_path / "unsupported.gpif"
    source.write_bytes(ET.tostring(root))
    with pytest.raises(ScoreImportError, match="FutureVibrato"):
        load_performance(source)


def test_gpif_empty_voice_slot_does_not_renumber_authored_voice(tmp_path):
    path = write_gp(tmp_path)
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("Content/score.gpif"))
    root.find("Bars/Bar/Voices").text = "-1 0"
    source = tmp_path / "second-voice.gpif"
    source.write_bytes(ET.tostring(root))
    track = load_performance(source)["tracks"][0]
    assert track["notation"]["measures"][0]["staves"]["staff"]["voices"][0]["v"] == 1
