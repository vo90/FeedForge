"""Known musical answers plus an independent raw reader and corrupt archives."""
from copy import deepcopy
from fractions import Fraction as F

import pytest

from feedback_converter.song_import import ScoreImportError
from feedback_converter.song_import.verify_source import songsterr as read_source
from test_song_import_score import beat, import_json, measure, raw_score
from test_song_import_verification import example, verify


@pytest.mark.parametrize("meter", [(2, 4), (3, 4), (4, 4), (6, 8), (6, 4), (21, 32)])
def test_whole_measure_rest_uses_meter_in_both_readers(tmp_path, meter):
    rest = {"rest": True, "type": 1, "duration": [1, 1], "notes": [{"rest": True}]}
    source = raw_score([measure(rest, signature=list(meter)), measure(beat(), signature=[4, 4])])
    before = deepcopy(source)
    result = import_json(tmp_path, source)
    seconds = meter[0] / meter[1] * 2
    track = result["tracks"][0]
    assert track["notes"][0]["t"] == pytest.approx(seconds)
    written = track["notation"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"][0]
    assert written["rest"] and written["dur"] == 1 and "tu" not in written
    assert written["end_time"] - written["t"] == pytest.approx(seconds)
    independent = read_source(source)
    assert independent.parts[0].beats[0][0]["length"] == F(meter[0] * 4, meter[1])
    assert source == before


def test_whole_bar_rest_does_not_hide_a_real_overflow(tmp_path):
    rest = {"rest": True, "type": 1, "duration": [1, 1], "notes": [{"rest": True}]}
    for beats in ([beat()], [rest, rest], [{**rest, "dots": 1}]):
        with pytest.raises(ScoreImportError, match="exceeds"):
            import_json(tmp_path, raw_score([measure(*beats, signature=[2, 4])]))


def test_rest_meter_changes_repeats_and_independent_voice(tmp_path):
    rest = {"rest": True, "type": 1, "duration": [1, 1], "notes": [{"rest": True}]}
    first = measure(rest, signature=[2, 4], repeatStart=True)
    first["voices"].append({"beats": [beat(duration=(1, 2))]})
    source = raw_score([first, measure(rest, signature=[6, 4], repeat=2), measure(beat(), signature=[4, 4])])
    track = import_json(tmp_path, source)["tracks"][0]
    assert [n["t"] for n in track["notes"]] == [0, 4, 8]
    assert len(track["notation"]["measures"]) == 5


def test_precise_bend_wins_and_equal_coordinate_uses_last_value(tmp_path):
    points = [{"position": 0, "precisePosition": 0, "tone": 0},
              {"position": 30, "precisePosition": 25, "tone": 50},
              {"position": 30, "precisePosition": 25, "tone": 100},
              {"position": 60, "precisePosition": 100, "tone": 0}]
    source = raw_score([measure(beat(bend={"points": points}))])
    note = import_json(tmp_path, source)["tracks"][0]["notes"][0]
    assert note["bnv"] == [{"t": 0, "v": 0}, {"t": .5, "v": 2}, {"t": 2, "v": 0}]
    atoms = read_source(source).parts[0].bars[0]
    assert atoms[0].bends == [(F(0), 0), (F(1, 4), 2), (F(1), 0)]


@pytest.mark.parametrize("points", [
    [{"position": 0, "precisePosition": 0, "tone": 0}, {"position": 60, "tone": 100}],
    [{"position": 0, "precisePosition": 40, "tone": 0}, {"position": 60, "precisePosition": 30, "tone": 100}],
    [{"position": 0, "precisePosition": -1, "tone": 0}],
    [{"position": 61, "precisePosition": 100, "tone": 0}],
])
def test_malformed_precision_is_not_sorted_clamped_or_dropped(tmp_path, points):
    source = raw_score([measure(beat(bend={"points": points}))])
    with pytest.raises(ScoreImportError):
        import_json(tmp_path, source)
    with pytest.raises(ValueError):
        read_source(source)


def test_precise_curve_mutation_rejected_independently(tmp_path):
    source, package = example()
    raw = source["parts"][0]["measures"][0]["voices"][0]["beats"]
    raw[0]["notes"][0]["bend"] = {"points": [
        {"position": 0, "precisePosition": 0, "tone": 0},
        {"position": 30, "precisePosition": 25, "tone": 100},
        {"position": 60, "precisePosition": 100, "tone": 0}]}
    curve = [{"t": 0, "v": 0}, {"t": .125, "v": 2}, {"t": .5, "v": 0}]
    package["chart.json"]["notes"][0].update(bn=2, bnv=curve)
    assert verify(tmp_path, source, package)["status"] == "passed"
    curve[1]["t"] = .25
    assert verify(tmp_path, source, package)["status"] == "failed"


@pytest.mark.parametrize("field,flag,annotation", [("slapping", "slp", "slap"), ("popping", "plk", "pop")])
def test_alias_reaches_chord_members_and_notation(tmp_path, field, flag, annotation):
    chord = beat()
    chord[field] = True
    chord["notes"].append({"string": 1, "fret": 7})
    source = raw_score([measure(chord)])
    source["tracks"][0]["instrumentId"] = 33
    track = import_json(tmp_path, source)["tracks"][0]
    assert all(n[flag] is True for n in track["chords"][0]["notes"])
    assert track["notation"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"][0][annotation] is True
    assert all(n.effects[flag] is True for n in read_source(source).parts[0].bars[0])


def test_missing_slap_flag_is_rejected(tmp_path):
    source, package = example()
    source["parts"][0]["measures"][0]["voices"][0]["beats"][0]["slapping"] = True
    package["chart.json"]["notes"][0]["slp"] = True
    package["notation.json"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"][0]["slap"] = True
    assert verify(tmp_path, source, package)["status"] == "passed"
    del package["chart.json"]["notes"][0]["slp"]
    assert verify(tmp_path, source, package)["status"] == "failed"


def test_clef_is_carried_through_repeat_without_transposing_notes(tmp_path):
    source = raw_score([measure(beat(), clef="F4", repeatStart=True),
                        measure(beat(), clef="G2", repeat=2), measure(beat())])
    track = import_json(tmp_path, source)["tracks"][0]
    assert [m["staves"]["staff"]["clef"] for m in track["notation"]["measures"]] == ["F4", "G2", "F4", "G2", "G2"]
    assert all(n["f"] == 3 for n in track["notes"])
    assert read_source(source).parts[0].clefs == ["F4", "G2", "G2"]


def test_clef_loss_rejected(tmp_path):
    source, package = example()
    source["parts"][0]["measures"][0]["clef"] = "F4"
    package["notation.json"]["measures"][0]["staves"]["staff"]["clef"] = "F4"
    assert verify(tmp_path, source, package)["status"] == "passed"
    package["notation.json"]["measures"][0]["staves"]["staff"]["clef"] = "G2"
    assert "notation_clef" in {e["code"] for e in verify(tmp_path, source, package)["errors"]}


@pytest.mark.parametrize("unit,quarters", [(2, 180), (4, 90), (8, 45)])
def test_dotted_tempo_unit_has_exact_quarter_clock(tmp_path, unit, quarters):
    source = raw_score([measure(beat())])
    source["parts"][0]["automations"]["tempo"][0].update(bpm=60, type=unit, dotted=True)
    result = import_json(tmp_path, source)
    assert result["tracks"][0]["notes"][0]["sus"] == pytest.approx(240 / quarters)
    assert read_source(source).bars[0].tempos[0] == quarters


@pytest.mark.parametrize("field,strings", [("upArpeggio", [2, 1, 0]), ("downArpeggio", [0, 1, 2])])
@pytest.mark.parametrize("value", [1, 3, 8])
def test_legacy_arpeggio_uses_authored_player_duration(tmp_path, field, strings, value):
    chord = {"duration": [1, 1], field: value,
             "notes": [{"string": 5, "fret": 3}, {"string": 3, "fret": 5}, {"string": 4, "fret": 4}]}
    source = raw_score([measure(chord)])
    track = import_json(tmp_path, source)["tracks"][0]
    step = 2 ** (value - 7) / 13  # Quarter clock at 120 BPM.
    assert [n["s"] for n in track["notes"]] == strings
    assert [n["t"] for n in track["notes"]] == pytest.approx([0, step, 2 * step])
    atoms = sorted(read_source(source).parts[0].bars[0], key=lambda n: n.q + n.attack_offset)
    assert [n.string for n in atoms] == strings
    assert [float(n.q + n.attack_offset) / 2 for n in atoms] == pytest.approx([0, step, 2 * step])


def test_explicit_modern_arpeggio_precedes_retained_legacy_field(tmp_path):
    chord = {"duration": [1, 4], "upArpeggio": 1,
             "arpeggio": {"direction": "down", "duration": 120, "shift": 100},
             "notes": [{"string": 5, "fret": 3}, {"string": 4, "fret": 4}]}
    source = raw_score([measure(chord)])
    notes = import_json(tmp_path, source)["tracks"][0]["notes"]
    assert [n["s"] for n in notes] == [0, 1]
    assert [n["t"] for n in notes] == [0, .0625]
    assert [float(n.q + n.attack_offset) for n in read_source(source).parts[0].bars[0]] == [0, .125]
