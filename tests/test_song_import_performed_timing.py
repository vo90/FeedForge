from copy import deepcopy

import pytest

from feedback_converter.song_import import ScoreImportError
from test_song_import_score import beat, import_json, measure, raw_score
from test_song_import_verification import example, verify


@pytest.mark.parametrize("direction,strings", [("down", [0, 1, 2]), ("up", [2, 1, 0])])
@pytest.mark.parametrize("field", ["brushStroke", "arpeggio"])
def test_strum_preserves_order_offsets_and_notated_chord(tmp_path, direction, strings, field):
    stroke = {"duration": [1, 4], "type": 4, field: {"direction": direction, "duration": 120, "shift": 100},
              "notes": [{"string": 5, "fret": 3}, {"string": 3, "fret": 5}, {"string": 4, "fret": 4}]}
    source = raw_score([measure(stroke)])
    track = import_json(tmp_path, source)["tracks"][0]
    assert not track["chords"]
    assert [n["s"] for n in track["notes"]] == strings
    assert [n["t"] for n in track["notes"]] == pytest.approx([0, 1 / 24, 1 / 12])
    assert [n["t"] + n["sus"] for n in track["notes"]] == pytest.approx([.5] * 3)
    written = track["notation"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"][0]
    assert written["dur"] == 4 and len(written["notes"]) == 3
    assert source["parts"][0]["measures"][0]["voices"][0]["beats"][0] == stroke


def test_shift_moves_attacks_before_beat_without_moving_notation(tmp_path):
    chord = {"duration": [1, 4], "brushStroke": {"direction": "down", "duration": 120, "shift": 0},
             "notes": [{"string": 5, "fret": 3}, {"string": 4, "fret": 5}]}
    track = import_json(tmp_path, raw_score([measure(beat(7, string=0, duration=(1, 4)), chord)]))["tracks"][0]
    assert [n["t"] for n in track["notes"]] == [0, .4375, .5]
    assert track["notation"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"][1]["t"] == .5


def test_long_grace_group_is_fitted_by_source_rule_not_discarded(tmp_path):
    grace = {**beat(duration=(1, 16)), "graceNote": "onBeat"}
    source = raw_score([measure(grace, {**deepcopy(grace), "notes": [{"string": 0, "fret": 4}]},
                                beat(5, duration=(1, 8)), beat(7, duration=(7, 8)))])
    track = import_json(tmp_path, source)["tracks"][0]
    assert [n["t"] for n in track["notes"]] == [0, .0625, .125, .25]
    assert [n["sus"] for n in track["notes"]][:3] == [.0625, .0625, .125]


def test_staccato_changes_sound_duration_not_written_duration(tmp_path):
    result = import_json(tmp_path, raw_score([measure(beat(staccato=True))]))
    assert result["tracks"][0]["notes"][0]["sus"] == 1
    assert result["tracks"][0]["notation"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"][0]["end_time"] == 2
    assert result["compatibilityReport"]["status"] == "limitations"


def test_strum_verification_is_independent_and_catches_flattened_attacks(tmp_path):
    source, package = example()
    first = source["parts"][0]["measures"][0]["voices"][0]["beats"][0]
    del first["notes"][0]["hp"]
    first["notes"].append({"string": 4, "fret": 8})
    first["brushStroke"] = {"direction": "down", "duration": 120, "shift": 100}
    notes = package["chart.json"]["notes"]
    del notes[0]["ln"]
    del notes[1]["ho"]
    notes[0]["pkd"] = 0
    notes.insert(1, {"t": 1.0625, "s": 1, "f": 8, "sus": .4375, "pkd": 0})
    written = package["notation.json"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"]
    written[0]["notes"].append({"midi": 53, "str": 1, "fret": 8})
    del written[1]["notes"][0]["ho"]
    report = verify(tmp_path, source, package)
    assert report["status"] == "passed", report
    notes[1]["t"] = 1
    assert "note_time" in {e["code"] for e in verify(tmp_path, source, package)["errors"]}


def test_conflicting_or_unknown_strum_parameters_do_not_get_guessed(tmp_path):
    chord = {"duration": [1, 4], "brushStroke": {"direction": "down", "duration": 120, "shift": 100, "future": 1},
             "notes": [{"string": 0, "fret": 3}, {"string": 1, "fret": 5}]}
    with pytest.raises(ScoreImportError, match="strum"):
        import_json(tmp_path, raw_score([measure(chord)]))
