"""Authored names/expressions stay distinct; unknown meanings still stop conversion."""
from copy import deepcopy

import pytest

from feedback_converter.song_import import ScoreImportError
from test_song_import_score import beat, import_json, measure, raw_score
from test_song_import_verification import example, verify


def test_same_shape_keeps_distinct_authored_labels_and_retained_annotations(tmp_path):
    chord = {"duration": [1, 4], "notes": [{"string": 0, "fret": 3}, {"string": 1, "fret": 5}],
             "chord": {"text": "C/G", "width": 40}}
    second = deepcopy(chord)
    second["chord"]["text"] = "Authored name"
    source = raw_score([measure(chord, second, doubleBarline=True)])
    source["parts"][0]["automations"]["tempo"][0]["text"] = "Moderate"
    result = import_json(tmp_path, source)
    track = result["tracks"][0]
    assert [t["name"] for t in track["templates"]] == ["C/G", "Authored name"]
    assert [c["id"] for c in track["chords"]] == [0, 1]
    assert result["sourceScore"]["document"] == source
    report = result["compatibilityReport"]
    assert report["status"] == "limitations"
    assert {f["feature"] for f in report["findings"]} == {"beat.chord", "measure.doubleBarline", "tempo.text"}


@pytest.mark.parametrize("direction,value", [("down", 0), ("up", 1)])
@pytest.mark.parametrize("vibrato,wide", [("slight", False), ("wide", True)])
def test_picking_and_left_hand_vibrato_have_known_values(tmp_path, direction, value, vibrato, wide):
    source = raw_score([measure({**beat(leftHandVibrato=vibrato), "pickStroke": direction})])
    result = import_json(tmp_path, source)
    note = result["tracks"][0]["notes"][0]
    assert note["vb"] and note["pkd"] == value
    written = result["tracks"][0]["notation"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"][0]
    assert written["notes"][0]["vib"] and bool(written["notes"][0].get("vibw")) == wide


@pytest.mark.parametrize("field,value", [("pickStroke", "sideways"), ("chord", {"text": "C", "future": True}), ("wahwah", "future")])
def test_new_uninterpreted_values_are_not_silently_accepted(tmp_path, field, value):
    with pytest.raises(ScoreImportError):
        import_json(tmp_path, raw_score([measure({**beat(), field: value})]))


def test_legacy_brush_is_not_mistaken_for_pick_direction(tmp_path):
    with pytest.raises(ScoreImportError, match="upStroke"):
        import_json(tmp_path, raw_score([measure({**beat(), "upStroke": 1})]))


def test_independent_verifier_detects_lost_pick_direction_and_vibrato(tmp_path):
    source, package = example()
    source["parts"][0]["measures"][0]["voices"][0]["beats"][0]["pickStroke"] = "down"
    note = source["parts"][0]["measures"][0]["voices"][0]["beats"][0]["notes"][0]
    note["leftHandVibrato"] = "wide"
    package["chart.json"]["notes"][0].update(pkd=0, vb=True)
    package["notation.json"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"][0]["notes"][0].update(vib=True, vibw=True)
    report = verify(tmp_path, source, package)
    assert report["status"] == "passed", report
    del package["chart.json"]["notes"][0]["pkd"]
    assert "note_technique" in {e["code"] for e in verify(tmp_path, source, package)["errors"]}
