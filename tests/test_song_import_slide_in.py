"""Incoming cues preserve destination timing, not invented approach geometry."""
from copy import deepcopy
import xml.etree.ElementTree as ET

import pytest

from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.builder import _retime_note
from feedback_converter.song_import.model import ScoreImportError
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.verify_source import read_source, UnverifiedFeature
from feedback_converter.song_import.verify_timeline import expected
from test_song_import_score import beat, import_json, measure, raw_score, write_gp
from test_song_import_verification import example, verify


@pytest.mark.parametrize("prefix,direction", [("below", "up"), ("above", "down")])
@pytest.mark.parametrize("outgoing", ["", "shift", "legato", "downwards", "upwards"])
def test_songsterr_confirmed_enum_and_combinations(tmp_path, prefix, direction, outgoing):
    document = raw_score([measure(beat(5, duration=(1, 2), slide=prefix + outgoing), beat(9, duration=(1, 2)))])
    note = import_json(tmp_path, document)["tracks"][0]["notes"][0]
    assert note["slide_in_marks"] == [{"direction": direction, "time": 0}]
    assert (note["f"], note["t"], note["sus"]) == (5, 0, 1)
    if outgoing in {"shift", "legato"}:
        assert note["sl"] == 9
        assert note.get("ln", False) == (outgoing == "legato")
    elif outgoing:
        assert note["slide_out_marks"] == [{"direction": "up" if outgoing == "upwards" else "down", "start": 0, "end": 1}]
    else:
        assert not any(k in note for k in ("sl", "slu", "slide_out", "slide_out_marks"))
    independent = expected(read_source(tmp_path / "score.json"), {"offset": 0, "scale": 1})
    for key, value in independent["parts"][0]["notes"][0]["note"].items():
        assert note[key] == value


@pytest.mark.parametrize("incoming,direction", [(16, "up"), (32, "down")])
@pytest.mark.parametrize("outgoing", [0, 1, 2, 4, 8])
def test_gpif_flags_preserve_independent_in_and_out(tmp_path, incoming, direction, outgoing):
    def effect(node, properties):
        if node.get("id") == "0":
            ET.SubElement(ET.SubElement(properties, "Property", name="Slide"), "Flags").text = str(incoming + outgoing)
    source = write_gp(tmp_path, measures=[{"beats": [{"value": "Half", "fret": 5}, {"value": "Half", "fret": 9}]}], note_extras=effect)
    note = load_performance(source)["tracks"][0]["notes"][0]
    assert note["slide_in_marks"] == [{"direction": direction, "time": 0}]
    independent = expected(read_source(source), {"offset": 0, "scale": 1})
    for key, value in independent["parts"][0]["notes"][0]["note"].items():
        assert note[key] == value
    if outgoing in (1, 2):
        assert note["sl"] == 9 and note.get("ln", False) == (outgoing == 2)
    elif outgoing:
        assert note["slide_out_marks"][0]["direction"] == ("up" if outgoing == 8 else "down")


@pytest.mark.parametrize("flags", [-1, 3, 12, 19, 48, 49, 64, 128, 256])
def test_conflicting_or_unknown_gpif_flags_stay_explicitly_unsupported(tmp_path, flags):
    def effect(node, properties):
        ET.SubElement(ET.SubElement(properties, "Property", name="Slide"), "Flags").text = str(flags)
    source = write_gp(tmp_path, note_extras=effect)
    with pytest.raises(ScoreImportError, match="slide flags"):
        load_performance(source)
    with pytest.raises(UnverifiedFeature, match="slide combination"):
        read_source(source)


@pytest.mark.parametrize("slide", ["unknown", "fromBelow", "belowsideways", "abovebelow", [], True])
def test_unconfirmed_songsterr_values_are_never_guessed(tmp_path, slide):
    with pytest.raises(ScoreImportError, match="Songsterr slide"):
        import_json(tmp_path, raw_score([measure(beat(slide=slide))]))
    with pytest.raises(UnverifiedFeature, match="slide type"):
        read_source(tmp_path / "score.json")


def test_tied_chord_repeats_keep_each_authored_onset_and_outgoing_segment(tmp_path):
    first = beat(5, duration=(1, 4), slide="below")
    first["notes"].append({"string": 1, "fret": 7})
    document = raw_score([measure(first, beat(5, duration=(1, 4), tie=True, slide="above"),
        beat(5, duration=(1, 2), tie=True, slide="belowdownwards"), repeatStart=True, repeat=2)])
    performance = import_json(tmp_path, document)
    track = performance["tracks"][0]
    assert [c["t"] for c in track["chords"]] == [0, 2]
    assert not track["notes"]
    marks = [{"direction": "up", "time": 0}, {"direction": "down", "time": .5}, {"direction": "up", "time": 1}]
    for chord in track["chords"]:
        note = next(n for n in chord["notes"] if n["f"] == 5)
        assert note["slide_in_marks"] == marks and note["sus"] == 2
        assert note["slide_out_marks"] == [{"direction": "down", "start": 1, "end": 2}]
        assert "slide_in_marks" not in next(n for n in chord["notes"] if n["f"] == 7)
        mapped = _retime_note(note, {"offset": 1, "scale": 1.5}, 10, chord_time=chord["t"])
        assert mapped["slide_in_marks"] == [{"direction": m["direction"], "time": m["time"] * 1.5} for m in marks]
    independent = expected(read_source(tmp_path / "score.json"), {"offset": 0, "scale": 1})
    assert [n["note"]["slide_in_marks"] for n in independent["parts"][0]["notes"] if n["note"]["f"] == 5] == [marks, marks]
    # Incoming cues add no fabricated notation effects; exact raw source remains.
    assert performance["sourceScore"]["document"] == document


def test_tempo_and_nonlinear_alignment_map_each_incoming_destination(tmp_path):
    document = raw_score([measure(beat(5, duration=(1, 4)), beat(5, duration=(1, 4), tie=True, slide="below"),
        beat(5, duration=(1, 4), tie=True, slide="above"), beat(5, duration=(1, 4), tie=True, slide="below"))])
    document["parts"][0]["automations"]["tempo"].append({"measure": 0, "position": 1920, "bpm": 60})
    note = import_json(tmp_path, document)["tracks"][0]["notes"][0]
    assert [m["time"] for m in note["slide_in_marks"]] == [.5, 1, 2]
    alignment = {"mapping": "piecewise-linear", "anchors": [
        {"score": 0, "audio": 1}, {"score": 1, "audio": 2}, {"score": 2, "audio": 4}, {"score": 3, "audio": 7}]}
    mapped = _retime_note(note, alignment, 9)
    assert mapped["sus"] == 6 and [m["time"] for m in mapped["slide_in_marks"]] == [.5, 1, 3]
    independent = expected(read_source(tmp_path / "score.json"), alignment)
    assert independent["parts"][0]["notes"][0]["note"]["slide_in_marks"] == mapped["slide_in_marks"]


@pytest.mark.parametrize("marks", [None, {}, [{"direction": "sideways", "time": 0}],
    [{"direction": "up", "time": -1}], [{"direction": "up", "time": 3}],
    [{"direction": "up", "time": True}], [{"direction": "up", "time": float("nan")}],
    [{"direction": "up", "time": float("inf")}], [{"direction": "up", "time": 10**400}],
    [{"direction": "up", "time": 0, "fret": 3}],
    [{"direction": "up", "time": 1}, {"direction": "down", "time": 0}],
    [{"direction": "up", "time": 1}, {"direction": "down", "time": 1}]])
def test_invalid_marker_arrays_cannot_be_serialized(marks):
    with pytest.raises(ImportFailure, match="lide-in"):
        _retime_note({"t": 0, "sus": 2, "slide_in_marks": marks}, {"offset": 0, "scale": 1}, 5)


def test_empty_and_zero_sustain_marks_are_supported_without_invented_duration():
    alignment = {"offset": 1, "scale": 1.2}
    for marks in ([], [{"direction": "up", "time": 0}]):
        mapped = _retime_note({"t": .5, "sus": 0, "slide_in_marks": marks}, alignment, 5)
        assert mapped["slide_in_marks"] == marks and mapped["sus"] == 0
        assert "slide_out" not in mapped


def test_rounding_cannot_silently_combine_distinct_incoming_cues():
    with pytest.raises(ImportFailure, match="precision"):
        _retime_note({"t": 0, "sus": 2, "slide_in_marks": [
            {"direction": "up", "time": 1}, {"direction": "down", "time": 1.0000001}]}, {"offset": 0, "scale": 1}, 5)


def incoming_example():
    # Hand-written expected archive, not exporter output. Both cues share one note.
    source, package = example()
    source["parts"][0]["measures"][0]["voices"][0]["beats"][-1]["notes"][0]["slide"] = "belowupwards"
    package["chart.json"]["notes"][-1]["slide_in_marks"] = [{"direction": "up", "time": 0}]
    return source, package


def test_hand_calculated_incoming_and_outgoing_archive_passes(tmp_path):
    source, package = incoming_example()
    report = verify(tmp_path, source, package)
    assert report["version"] == 91 and report["status"] == "passed", report
    assert "slide_in_destination_timing" in report["scope"]


@pytest.mark.parametrize("fault,code", [("omit", "slide_in_count"), ("empty", "slide_in_count"),
    ("time", "slide_in_time"), ("direction", "slide_in_direction"), ("invent", "slide_in_count"),
    ("negative", "slide_in_bounds"), ("outside", "slide_in_bounds"), ("duplicate", "slide_in_bounds"),
    ("shape", "slide_in_shape"), ("bool", "slide_in_shape"), ("huge", "slide_in_bounds"), ("start_fret", "slide_in_shape")])
def test_independent_checker_rejects_omitted_altered_or_invented_incoming_cues(tmp_path, fault, code):
    source, package = incoming_example()
    marked = package["chart.json"]["notes"][-1]
    if fault == "omit": del marked["slide_in_marks"]
    elif fault == "empty": marked["slide_in_marks"] = []
    elif fault == "time": marked["slide_in_marks"][0]["time"] = .1
    elif fault == "direction": marked["slide_in_marks"][0]["direction"] = "down"
    elif fault == "invent": package["chart.json"]["notes"][0]["slide_in_marks"] = deepcopy(marked["slide_in_marks"])
    elif fault == "negative": marked["slide_in_marks"][0]["time"] = -.1
    elif fault == "outside": marked["slide_in_marks"][0]["time"] = .6
    elif fault == "duplicate": marked["slide_in_marks"].append(deepcopy(marked["slide_in_marks"][0]))
    elif fault == "shape": marked["slide_in_marks"] = None
    elif fault == "bool": marked["slide_in_marks"][0]["time"] = False
    elif fault == "huge": marked["slide_in_marks"][0]["time"] = 10**400
    elif fault == "start_fret": marked["slide_in_marks"][0]["fret"] = 28
    report = verify(tmp_path, source, package)
    assert report["status"] == "failed", report
    assert code in {e["code"] for e in report["errors"]}, report
