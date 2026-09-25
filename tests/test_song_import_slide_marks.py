"""Marked source intervals survive ties without implying a slide trajectory."""
from copy import deepcopy
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.builder import _retime_note
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.verify_source import read_source
from feedback_converter.song_import.verify_timeline import expected
from test_song_import_score import beat, import_json, measure, raw_score, write_gp
from test_song_import_verification import example, verify


def test_final_tied_quarter_keeps_full_sustain_and_marked_segment(tmp_path):
    # Same written rhythm as Rats bar 102: rest eighth, half + eighth tied
    # into the final marked quarter. Total sustain is 3.5 quarter beats.
    document = raw_score([measure({"duration": [1, 8], "rest": True, "notes": [{"rest": True}]},
        beat(19, duration=(1, 2)), beat(19, duration=(1, 8), tie=True),
        beat(19, duration=(1, 4), tie=True, slide="downwards"))])
    note = import_json(tmp_path, document)["tracks"][0]["notes"][0]
    assert (note["t"], note["f"], note["sus"]) == (.25, 19, 1.75)
    assert note["slide_out_marks"] == [{"direction": "down", "start": 1.25, "end": 1.75}]
    assert note["slide_out"] == "down"
    independent = expected(read_source(tmp_path / "score.json"), {"offset": 0, "scale": 1})
    assert independent["parts"][0]["notes"][0]["note"]["slide_out_marks"] == note["slide_out_marks"]


@pytest.mark.parametrize("directions", [("upwards", "downwards"), ("downwards", "downwards")])
def test_multiple_tied_marks_keep_order_and_only_unambiguous_scalar(tmp_path, directions):
    document = raw_score([measure(beat(5, duration=(1, 4), slide=directions[0]),
        beat(5, duration=(1, 4), tie=True), beat(5, duration=(1, 2), tie=True, slide=directions[1]))])
    note = import_json(tmp_path, document)["tracks"][0]["notes"][0]
    assert note["slide_out_marks"] == [
        {"direction": "up" if directions[0] == "upwards" else "down", "start": 0., "end": .5},
        {"direction": "down", "start": 1., "end": 2.}]
    assert note.get("slide_out") == ("down" if directions[0] == directions[1] else None)
    independent = expected(read_source(tmp_path / "score.json"), {"offset": 0, "scale": 1})
    assert independent["parts"][0]["notes"][0]["note"]["slide_out_marks"] == note["slide_out_marks"]


def test_repeated_authored_chord_marks_are_relative_to_each_attack(tmp_path):
    first = beat(5, duration=(1, 2), slide="upwards")
    first["notes"].append({"string": 1, "fret": 7})
    document = raw_score([measure(first, beat(5, duration=(1, 2), tie=True, slide="downwards"),
                                  repeatStart=True, repeat=2)])
    track = import_json(tmp_path, document)["tracks"][0]
    assert len(track["chords"]) == 2 and not track["notes"]
    assert [chord["t"] for chord in track["chords"]] == [0., 2.]
    for chord in track["chords"]:
        marked = next(n for n in chord["notes"] if n["f"] == 5)
        assert marked["sus"] == 2.
        assert marked["slide_out_marks"] == [
            {"direction": "up", "start": 0., "end": 1.}, {"direction": "down", "start": 1., "end": 2.}]
        assert "slide_out" not in marked
        assert "slide_out_marks" not in next(n for n in chord["notes"] if n["f"] == 7)
        retimed = _retime_note(marked, {"offset": 1, "scale": 1.25}, 10, chord_time=chord["t"])
        assert retimed["slide_out_marks"][1] == {"direction": "down", "start": 1.25, "end": 2.5}


@pytest.mark.parametrize("flag,direction", [(4, "down"), (8, "up")])
def test_gpif_tied_segment_matches_independent_xml_reader(tmp_path, flag, direction):
    def effect(note, properties):
        if note.find("Tie") is not None:
            ET.SubElement(ET.SubElement(properties, "Property", name="Slide"), "Flags").text = str(flag)
    source = write_gp(tmp_path, measures=[{"beats": [
        {"value": "Half", "fret": 5}, {"value": "Half", "fret": 5, "tie": True}]}], note_extras=effect)
    note = load_performance(source)["tracks"][0]["notes"][0]
    marks = [{"direction": direction, "start": 1, "end": 2}]
    assert note["slide_out_marks"] == marks and note["sus"] == 2
    independent = expected(read_source(source), {"offset": 0, "scale": 1})
    assert independent["parts"][0]["notes"][0]["note"]["slide_out_marks"] == marks


def test_tempo_change_and_piecewise_map_retime_each_boundary(tmp_path):
    document = raw_score([measure(beat(5, duration=(1, 2)), beat(5, duration=(1, 2), tie=True, slide="downwards"))])
    document["parts"][0]["automations"]["tempo"].append({"measure": 0, "position": 1920, "bpm": 60})
    note = import_json(tmp_path, document)["tracks"][0]["notes"][0]
    assert note["sus"] == 3
    assert note["slide_out_marks"] == [{"direction": "down", "start": 1, "end": 3}]
    alignment = {"mapping": "piecewise-linear", "anchors": [
        {"score": 0, "audio": 1}, {"score": 1, "audio": 2}, {"score": 2, "audio": 4}, {"score": 3, "audio": 7}]}
    mapped = _retime_note(note, alignment, 9)
    assert (mapped["t"], mapped["sus"]) == (1, 6)
    assert mapped["slide_out_marks"] == [{"direction": "down", "start": 1, "end": 6}]
    independent = expected(read_source(tmp_path / "score.json"), alignment)
    assert independent["parts"][0]["notes"][0]["note"]["slide_out_marks"] == mapped["slide_out_marks"]


@pytest.mark.parametrize("marks", [None, {}, [{"direction": "sideways", "start": 0, "end": 1}],
    [{"direction": "up", "start": -1, "end": 1}], [{"direction": "up", "start": 1, "end": 1}],
    [{"direction": "up", "start": 0, "end": 3}], [{"direction": "up", "start": True, "end": 1}],
    [{"direction": "up", "start": 0, "end": float("nan")}],
    [{"direction": "up", "start": 1, "end": 2}, {"direction": "down", "start": 0, "end": 1}]])
def test_invalid_or_impossible_intervals_cannot_be_serialized(marks):
    with pytest.raises(ImportFailure, match="lide-out"):
        _retime_note({"t": 0, "sus": 2, "slide_out_marks": marks}, {"offset": 0, "scale": 1}, 5)


def test_submicrosecond_interval_cannot_collapse_silently():
    with pytest.raises(ImportFailure, match="precision"):
        _retime_note({"t": 0, "sus": 2, "slide_out_marks": [{"direction": "up", "start": 1, "end": 1.0000001}]},
                     {"offset": 0, "scale": 1}, 5)


def test_independent_endpoint_rounding_does_not_change_legacy_affine_sustain():
    note = {"t": .1234567, "sus": .1234567,
            "slide_out_marks": [{"direction": "up", "start": 0, "end": .1234567}]}
    mapped = _retime_note(note, {"offset": .1, "scale": 1.1}, 5)
    assert mapped["sus"] == .135802
    assert mapped["slide_out_marks"] == [{"direction": "up", "start": 0, "end": .135803}]


@pytest.mark.parametrize("fault,code", [("omit", "slide_mark_count"), ("empty", "slide_mark_count"),
    ("shift", "slide_mark_time"), ("reverse", "slide_mark_direction"), ("fabricate", "slide_mark_count"),
    ("negative", "slide_mark_bounds"), ("zero", "slide_mark_bounds"), ("outside", "slide_mark_bounds"),
    ("shape", "slide_mark_shape")])
def test_independent_checker_rejects_missing_changed_or_invented_segments(tmp_path, fault, code):
    source, package = example()
    marked = package["chart.json"]["notes"][-1]
    if fault == "omit": del marked["slide_out_marks"]
    elif fault == "empty": marked["slide_out_marks"] = []
    elif fault == "shift": marked["slide_out_marks"][0]["start"] = .1
    elif fault == "reverse": marked["slide_out_marks"][0]["direction"] = "down"
    elif fault == "fabricate": package["chart.json"]["notes"][0]["slide_out_marks"] = deepcopy(marked["slide_out_marks"])
    elif fault == "negative": marked["slide_out_marks"][0]["start"] = -.1
    elif fault == "zero": marked["slide_out_marks"][0]["start"] = .5
    elif fault == "outside": marked["slide_out_marks"][0]["end"] = 1
    elif fault == "shape": marked["slide_out_marks"] = None
    report = verify(tmp_path, source, package)
    assert report["version"] == 19 and report["status"] == "failed", report
    assert code in {e["code"] for e in report["errors"]}, report


@pytest.mark.skipif(not os.environ.get("FEEDFORGE_RATS_SOURCE_FIXTURE"), reason="Optional archived approved Rats source")
def test_archived_rats_bar102_slide_is_only_final_quarter():
    source = Path(os.environ["FEEDFORGE_RATS_SOURCE_FIXTURE"])
    document = json.loads(source.read_text(encoding="utf-8"))
    assert str(document["revisionId"]) == "7788783"
    performance = load_performance(source)
    track = next(t for t in performance["tracks"] if str(t["id"]) == "3")
    notes = track["notes"] + [{"t": c["t"], **n} for c in track["chords"] for n in c["notes"]]
    bar = performance["scoreTimeline"]["measures"][101]
    note = next(n for n in notes if n["f"] == 19 and bar["start"] <= n["t"] < bar["end"] and n.get("slide_out_marks"))
    quarter = 60 / 124
    assert note["sus"] == pytest.approx(3.5 * quarter)
    assert note["slide_out_marks"][0]["start"] == pytest.approx(2.5 * quarter)
    assert note["slide_out_marks"][0]["end"] == pytest.approx(3.5 * quarter)
    assert note["slide_out_marks"][0]["direction"] == "down"
