from copy import deepcopy
from fractions import Fraction as F
import pytest

from feedback_converter.song_import import ScoreImportError
from feedback_converter.song_import.verify_source import songsterr as read_source
from test_song_import_score import beat, measure, raw_score, import_json
from test_song_import_verification import example, verify


@pytest.mark.parametrize("feel,first", [("8th", F(2, 3)), ("dotted8th", F(3, 4)), ("scottish8th", F(1, 4))])
def test_swing_pair_preserves_written_eighths_and_measure_duration(tmp_path, feel, first):
    source = raw_score([measure(beat(duration=(1, 8)), beat(5, duration=(1, 8)), tripletFeel=feel)])
    before = deepcopy(source)
    track = import_json(tmp_path, source)["tracks"][0]
    assert [n["t"] for n in track["notes"]] == pytest.approx([0, float(first / 2)])
    assert [n["sus"] for n in track["notes"]] == pytest.approx([float(first / 2), float((1 - first) / 2)])
    written = track["notation"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"]
    assert [b["dur"] for b in written] == [8, 8]
    assert written[1]["beat_pos"] == [1, 2]
    assert [a.q for a in read_source(source).parts[0].bars[0]] == [0, first]
    assert source == before


def test_swing_carries_changes_and_repeats(tmp_path):
    pair = [beat(duration=(1, 16)), beat(5, duration=(1, 16))]
    source = raw_score([measure(*pair, tripletFeel="16th", repeatStart=True),
                        measure(*pair, repeat=2), measure(*pair, tripletFeel="off")])
    notes = import_json(tmp_path, source)["tracks"][0]["notes"]
    assert [n["t"] for n in notes] == pytest.approx([0, 1/6, 2, 2+1/6, 4, 4+1/6, 6, 6+1/6, 8, 8.125])


def test_swing_exemptions_are_shared_between_voices(tmp_path):
    bar = measure(beat(duration=(1, 8)), beat(5, duration=(1, 8)), tripletFeel="8th")
    bar["voices"].append({"beats": [beat(7, string=1, duration=(1, 12)), beat(8, string=1, duration=(1, 12)), beat(9, string=1, duration=(1, 12))]})
    source = raw_score([bar])
    track = import_json(tmp_path, source)["tracks"][0]
    assert [n["t"] for n in track["notes"] if n["s"] == 5] == [0, .25]
    assert read_source(source).parts[0].bars[0][1].q == F(1, 2)


def test_incomplete_pair_and_duplet_are_not_swung(tmp_path):
    first = beat(duration=(1, 8)); first["tuplet"] = 2
    for beats in ([beat(duration=(1, 8))], [first, beat(5, duration=(1, 8))]):
        source = raw_score([measure(*beats, tripletFeel="8th")])
        notes = import_json(tmp_path, source)["tracks"][0]["notes"]
        assert notes[0]["sus"] == .25
        assert read_source(source).parts[0].bars[0][0].length == F(1, 2)


@pytest.mark.parametrize("tail", [False, True])
def test_before_beat_grace_steals_previous_time_without_extending_measure(tmp_path, tail):
    grace = {**beat(4, duration=(1, 16)), "graceNote": "beforeBeat"}
    beats = [beat(3, duration=(1, 4)), grace]
    if not tail: beats.append(beat(5, duration=(1, 4)))
    source = raw_score([measure(*beats)])
    track = import_json(tmp_path, source)["tracks"][0]
    assert [n["t"] for n in track["notes"]] == ([0, .375] if tail else [0, .375, .5])
    assert [n["sus"] for n in track["notes"]][:2] == [.375, .125]
    assert track["notation"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"][1]["grace"] == "a"
    atoms = read_source(source).parts[0].bars[0]
    assert atoms[0].length == F(3, 4) and atoms[1].q == F(3, 4)


def test_before_grace_group_shares_budget(tmp_path):
    grace = {**beat(4, duration=(1, 16)), "graceNote": "beforeBeat"}
    source = raw_score([measure(beat(3, duration=(1, 4)), grace,
                                {**deepcopy(grace), "notes": [{"string": 0, "fret": 5}]}, beat(6, duration=(1, 4)))])
    notes = import_json(tmp_path, source)["tracks"][0]["notes"]
    assert [n["t"] for n in notes] == [0, .375, .4375, .5]
    assert [float(n.q)/2 for n in read_source(source).parts[0].bars[0]] == [0, .375, .4375, .5]


def test_recording_start_grace_follows_source_fallback_and_keeps_written_mark(tmp_path):
    source = raw_score([measure({**beat(4, duration=(1, 16)), "graceNote": "beforeBeat"}, beat())])
    original = deepcopy(source)
    track = import_json(tmp_path, source)['tracks'][0]
    assert [n['t'] for n in track['notes']] == [0, .125]
    assert [float(n.q)/2 for n in read_source(source).parts[0].bars[0]] == [0, .125]
    assert track['notation']['measures'][0]['staves']['staff']['voices'][0]['beats'][0]['grace'] == 'a'
    assert source == original


@pytest.mark.parametrize("enabled,expected", [(True, [60, 75, 90, 105, 120]), (False, [60, 120])])
def test_gradual_tempo_uses_destination_flag_and_explicit_enablement(tmp_path, enabled, expected):
    source = raw_score([measure(beat(), repeatStart=True), measure(beat(), repeat=2)])
    source["parts"][0]["automations"] = {"gradualTempo": enabled, "tempo": [
        {"measure": 0, "position": 0, "bpm": 60}, {"measure": 1, "position": 0, "bpm": 120, "linear": True}]}
    result = import_json(tmp_path, source)
    assert [t["bpm"] for t in result["tempos"]][:len(expected)] == expected
    bars = read_source(source).bars
    assert list(bars[0].tempos.values()) == (expected[:-1] if enabled else [60])
    assert bars[1].tempos[0] == 120


def test_fermata_explicit_hold_has_known_duration_and_tempo_restoration(tmp_path):
    source = raw_score([measure(beat(), repeatStart=True, repeat=2)])
    source["parts"][0]["automations"]["fermata"] = [{"measure": 0, "position": 1920, "type": "medium", "length": .6}]
    result = import_json(tmp_path, source)
    assert [t["bpm"] for t in result["tempos"]][:3] == [120, 62, 120]
    expected = 1.5 + 60/62
    assert result["tracks"][0]["notes"][0]["sus"] == pytest.approx(expected)
    assert result["tracks"][0]["notes"][1]["t"] == pytest.approx(expected)
    assert read_source(source).bars[0].tempos == {F(0): 120, F(2): 62, F(3): 120}


def test_fractional_fermata_subdivision_and_unmeasured_hold_rejection(tmp_path):
    source = raw_score([measure(beat())])
    source["parts"][0]["automations"]["fermata"] = [{"measure": 0, "position": 3360, "type": "short", "length": .12}]
    result = import_json(tmp_path, source)
    assert result["tracks"][0]["notes"][0]["sus"] == pytest.approx(1.75 + .5 * 60/89)
    assert read_source(source).bars[0].tempos[F(7, 2)] == 89
    del source["parts"][0]["automations"]["fermata"][0]["length"]
    with pytest.raises(ScoreImportError):
        import_json(tmp_path, source)


def test_independent_verifier_detects_flattened_swing(tmp_path):
    source, package = example()
    bar = source["parts"][0]["measures"][0]; bar["tripletFeel"] = "8th"
    raw = bar["voices"][0]["beats"]
    times, durations = [1, 4/3, 1.5, 11/6], [1/3, 1/6, 1/3, 1/6]
    written = package["notation.json"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"]
    for i, (b, n, w) in enumerate(zip(raw, package["chart.json"]["notes"], written)):
        b.update(duration=[1, 8], type=8)
        n.update(t=times[i], sus=durations[i])
        w.update(t=times[i], duration_seconds=durations[i], dur=8, beat_pos=[F(i, 2).numerator, F(i, 2).denominator])
    package["chart.json"]["notes"][-1]["slide_out_marks"][0]["end"] = 1/6
    report = verify(tmp_path, source, package)
    assert report["status"] == "passed", report
    package["chart.json"]["notes"][1]["t"] = 1.25
    assert verify(tmp_path, source, package)["status"] == "failed"


def test_conflicting_part_automation_is_not_applied_to_every_track(tmp_path):
    source = raw_score([measure(beat())])
    source["tracks"].append({**deepcopy(source["tracks"][0]), "id": 1})
    source["parts"].append(deepcopy(source["parts"][0]))
    source["parts"][0]["automations"]["fermata"] = [{"measure": 0, "position": 1920, "type": "medium", "length": .6}]
    with pytest.raises(ScoreImportError, match="disagree"):
        import_json(tmp_path, source)
    with pytest.raises(ValueError, match="different"):
        read_source(source)
