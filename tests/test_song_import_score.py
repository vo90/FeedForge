"""Musical fidelity checks; fixtures are synthetic, not downloaded songs."""

from copy import deepcopy
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import ZipFile

import pytest

from feedback_converter.song_import import ScoreImportError, load_performance
from feedback_converter.song_import.model import Measure
from feedback_converter.song_import.timeline import playback_order


def beat(fret=3, string=0, duration=(1, 1), **note_fields):
    return {"duration": list(duration), "notes": [{"string": string, "fret": fret, **note_fields}]}


def measure(*beats, **fields):
    return {"signature": [4, 4], "voices": [{"beats": list(beats)}], **fields}


def raw_score(measures, tuning=None):
    return {"format": "songsterr", "songId": 123, "revisionId": 456,
            "title": "Synthetic ♯ song", "artist": "Synthetic artist",
            "tracks": [{"id": 0, "name": "Lead Guitar", "instrumentId": 29,
                        "tuning": tuning or [64, 59, 55, 50, 45, 40]}],
            "parts": [{"measures": measures,
                       "automations": {"tempo": [{"measure": 0, "position": [0, 1], "bpm": 120, "type": 4}]}}]}


def import_json(tmp_path, document, metadata=None):
    path = tmp_path / "score.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return load_performance(path, metadata)


def write_gp(tmp_path, *, measures=None, tuning=None, tempos=None, note_extras=None):
    """One guitar track; each measure dictionary optionally overrides GPIF markup."""
    measures = measures or [{}]
    root = ET.Element("GPIF")
    ET.SubElement(root, "GPVersion").text = "8.1.3"
    score = ET.SubElement(root, "Score")
    ET.SubElement(score, "Title").text = "A copy"
    ET.SubElement(score, "Artist").text = "Misc Covers"
    master = ET.SubElement(root, "MasterTrack")
    ET.SubElement(master, "Tracks").text = "0"
    autos = ET.SubElement(master, "Automations")
    for bar, pos, bpm in (tempos or [(0, 0, 120)]):
        auto = ET.SubElement(autos, "Automation")
        for key, val in {"Type": "Tempo", "Bar": str(bar), "Position": str(pos),
                         "Value": f"{bpm} 2", "Linear": "false"}.items():
            ET.SubElement(auto, key).text = val
    track = ET.SubElement(ET.SubElement(root, "Tracks"), "Track", id="0")
    ET.SubElement(track, "Name").text = "Lead Guitar"
    ET.SubElement(ET.SubElement(track, "InstrumentSet"), "Type").text = "electricGuitar"
    staff = ET.SubElement(ET.SubElement(track, "Staves"), "Staff")
    tp = ET.SubElement(ET.SubElement(staff, "Properties"), "Property", name="Tuning")
    ET.SubElement(tp, "Pitches").text = " ".join(map(str, tuning or [40, 45, 50, 55, 59, 64]))
    tables = {key: ET.SubElement(root, key) for key in ("MasterBars", "Bars", "Voices", "Beats", "Notes", "Rhythms")}
    counter = 0
    for bi, spec in enumerate(measures):
        mb = ET.SubElement(tables["MasterBars"], "MasterBar")
        ET.SubElement(mb, "Time").text = "4/4"
        ET.SubElement(mb, "Bars").text = str(bi)
        if "repeat" in spec:
            ET.SubElement(mb, "Repeat", **spec["repeat"])
        if "ending" in spec:
            ET.SubElement(mb, "AlternateEndings").text = spec["ending"]
        if "navigation" in spec:
            ET.SubElement(mb, "Directions")
        bar = ET.SubElement(tables["Bars"], "Bar", id=str(bi))
        ET.SubElement(bar, "Voices").text = str(bi)
        voice = ET.SubElement(tables["Voices"], "Voice", id=str(bi))
        bids = []
        specs = spec.get("beats", [{"value": "Whole", "string": 0, "fret": bi + 1}])
        for ni, bs in enumerate(specs):
            ident = str(counter)
            counter += 1
            bids.append(ident)
            b = ET.SubElement(tables["Beats"], "Beat", id=ident)
            ET.SubElement(b, "Rhythm", ref=ident)
            ET.SubElement(b, "Notes").text = ident
            rhythm = ET.SubElement(tables["Rhythms"], "Rhythm", id=ident)
            ET.SubElement(rhythm, "NoteValue").text = bs.get("value", "Quarter")
            n = ET.SubElement(tables["Notes"], "Note", id=ident)
            p = ET.SubElement(n, "Properties")
            for name, tag in (("String", "String"), ("Fret", "Fret")):
                ET.SubElement(ET.SubElement(p, "Property", name=name), tag).text = str(bs.get(name.lower(), 0))
            if bs.get("tie"):
                ET.SubElement(n, "Tie", destination="true", origin="false")
            if note_extras:
                note_extras(n, p)
        ET.SubElement(voice, "Beats").text = " ".join(bids)
    path = tmp_path / "synthetic.gp"
    with ZipFile(path, "w") as z:
        z.writestr("Content/score.gpif", ET.tostring(root))
    return path


def test_raw_repeat_three_means_three_performed_passes(tmp_path):
    result = import_json(tmp_path, raw_score([measure(beat(), repeatStart=True, repeat=3)]))
    assert [n["t"] for n in result["tracks"][0]["notes"]] == [0, 2, 4]
    assert result["duration"] == 6
    assert result["title"] == "Synthetic ♯ song"


def test_gp_repeat_three_and_metadata_override(tmp_path):
    path = write_gp(tmp_path, measures=[{"repeat": {"start": "true", "end": "true", "count": "3"}}])
    result = load_performance(path, {"artist": "Real artist", "title": "Real title", "revisionId": 42})
    assert result["artist"] == "Real artist"
    assert result["source"]["revisionId"] == 42
    assert [n["t"] for n in result["tracks"][0]["notes"]] == [0, 2, 4]


def test_alternate_endings_skip_first_ending_on_second_pass(tmp_path):
    result = import_json(tmp_path, raw_score([
        measure(beat(1), repeatStart=True),
        measure(beat(2), repeat=2, alternateEnding=1),
        measure(beat(3), alternateEnding=2),
    ]))
    assert [n["f"] for n in result["tracks"][0]["notes"]] == [1, 2, 1, 3]
    assert result["duration"] == 8


def test_gp_alternate_endings(tmp_path):
    path = write_gp(tmp_path, measures=[
        {"repeat": {"start": "true", "end": "false", "count": "0"}},
        {"repeat": {"start": "false", "end": "true", "count": "2"}, "ending": "1"},
        {"ending": "2"},
    ])
    assert [n["f"] for n in load_performance(path)["tracks"][0]["notes"]] == [1, 2, 1, 3]


def test_nested_repeat_order():
    bars = [Measure(repeat_start=True), Measure(repeat_start=True),
            Measure(repeat_count=2), Measure(repeat_count=3)]
    assert playback_order(bars) == [0, 1, 2, 1, 2, 3] * 3


def test_midbar_tempo_applies_to_notes_and_sustains(tmp_path):
    path = write_gp(tmp_path, measures=[{"beats": [{"fret": n} for n in range(4)]}],
                    tempos=[(0, 0, 120), (0, 0.5, 60)])
    result = load_performance(path)
    notes = result["tracks"][0]["notes"]
    assert [n["t"] for n in notes] == [0, 0.5, 1, 2]
    assert [n["sus"] for n in notes] == [0.5, 0.5, 1, 1]
    assert result["duration"] == 3


def test_raw_midbar_tempo_and_tie_share_one_timeline(tmp_path):
    data = raw_score([measure(beat(3, duration=(1, 2)), beat(3, duration=(1, 2), tie=True))])
    data["parts"][0]["automations"]["tempo"].append({"measure": 0, "position": 1920, "bpm": 60})
    result = import_json(tmp_path, data)
    assert len(result["tracks"][0]["notes"]) == 1
    assert result["tracks"][0]["notes"][0]["sus"] == 3


def test_gp_tied_notes_are_not_restruck(tmp_path):
    path = write_gp(tmp_path, measures=[{"beats": [{"fret": 4, "value": "Half"},
                                                    {"fret": 4, "value": "Half", "tie": True}]}])
    result = load_performance(path)
    assert len(result["tracks"][0]["notes"]) == 1
    assert result["tracks"][0]["notes"][0]["sus"] == 2


def test_eighth_string_and_physical_tuning_order_survive_raw_import(tmp_path):
    # Deliberately re-entrant: sorting by pitch would corrupt string identity.
    high_to_low = [64, 59, 55, 50, 45, 40, 47, 30]
    result = import_json(tmp_path, raw_score([measure(beat(7, 7))], tuning=high_to_low))
    track = result["tracks"][0]
    assert track["tuning"] == high_to_low[::-1]
    assert track["notes"][0]["s"] == 0
    assert track["notes"][0]["f"] == 7


def test_gp_eight_strings_are_not_reduced_to_gp5_width(tmp_path):
    tuning = [30, 35, 40, 45, 50, 55, 59, 64]
    path = write_gp(tmp_path, tuning=tuning, measures=[{"beats": [{"string": 7, "fret": 5, "value": "Whole"}]}])
    track = load_performance(path)["tracks"][0]
    assert track["tuning"] == tuning
    assert track["notes"][0]["s"] == 7


def test_all_guitars_and_basses_are_retained(tmp_path):
    data = raw_score([measure(beat())])
    for index, program in enumerate([30, 34], start=1):
        data["tracks"].append({"id": index, "name": f"Track {index}", "instrumentId": program,
                                "tuning": [43, 38, 33, 28] if program == 34 else [64, 59, 55, 50, 45, 40]})
        data["parts"].append(deepcopy(data["parts"][0]))
    assert [t["instrument"] for t in import_json(tmp_path, data)["tracks"]] == ["guitar", "guitar", "bass"]


@pytest.mark.parametrize("corruption", ["part", "measure", "duration", "notes", "tuning"])
def test_incomplete_data_is_not_a_success(tmp_path, corruption):
    data = raw_score([measure(beat())])
    if corruption == "part":
        data["parts"][0] = None
    elif corruption == "measure":
        data["parts"][0]["measures"] = []
    elif corruption in ("duration", "notes"):
        del data["parts"][0]["measures"][0]["voices"][0]["beats"][0][corruption]
    else:
        del data["tracks"][0]["tuning"]
    with pytest.raises(ScoreImportError):
        import_json(tmp_path, data)


def test_critical_navigation_is_rejected(tmp_path):
    path = write_gp(tmp_path, measures=[{"navigation": True}])
    with pytest.raises(ScoreImportError, match="Navigation"):
        load_performance(path)


def test_dangling_tie_is_rejected(tmp_path):
    with pytest.raises(ScoreImportError, match="tie"):
        import_json(tmp_path, raw_score([measure(beat(tie=True))]))


def test_bends_and_palm_mutes_survive_conversion(tmp_path):
    data = raw_score([measure(beat(bend={"points": [{"position": 0, "tone": 0}, {"position": 60, "tone": 100}]}))])
    data["parts"][0]["measures"][0]["voices"][0]["beats"][0]["palmMute"] = True
    note = import_json(tmp_path, data)["tracks"][0]["notes"][0]
    assert note["bn"] == 2
    assert note["bnv"] == [{"t": 0, "v": 0}, {"t": 2, "v": 2}]
    assert note["pm"] is True


def test_bend_times_are_relative_to_the_note_not_the_song(tmp_path):
    data = raw_score([measure(beat(duration=(1, 2)), beat(duration=(1, 2),
        bend={"points": [{"position": 0, "tone": 0}, {"position": 60, "tone": 100}]}))])
    note = import_json(tmp_path, data)["tracks"][0]["notes"][1]
    assert note["t"] == 1
    assert note["bnv"] == [{"t": 0, "v": 0}, {"t": 1, "v": 2}]


def test_raw_hammer_origin_marks_the_destination(tmp_path):
    data = raw_score([measure(beat(3, duration=(1, 2), hp=True), beat(5, duration=(1, 2)))])
    notes = import_json(tmp_path, data)["tracks"][0]["notes"]
    assert notes[0]["ln"] is True
    assert notes[1]["ho"] is True
    assert all(not any(k.startswith("__") for k in n) for n in notes)


def test_on_beat_grace_borrows_from_principal_note_without_moving_bar_or_other_voice(tmp_path):
    grace = {**beat(2, duration=(1, 16), hp=True), "graceNote": "onBeat"}
    data = raw_score([measure(beat(1, duration=(1, 4)), grace,
                              beat(4, duration=(1, 4)), beat(4, duration=(1, 2), tie=True)),
                      measure(beat(6))])
    data["parts"][0]["measures"][0]["voices"].append({"beats": [beat(7, string=1)]})
    before = deepcopy(data)
    result = import_json(tmp_path, data)
    notes = result["tracks"][0]["notes"]
    melody = [note for note in notes if note["s"] == 5]
    assert [note["t"] for note in melody] == [0, 0.5, 0.625, 2]
    assert [note["sus"] for note in melody] == [0.5, 0.125, 1.375, 2]
    assert melody[1]["ln"] is True and melody[2]["ho"] is True
    other_voice = next(note for note in notes if note["s"] == 4)
    assert other_voice["t"] == 0 and other_voice["sus"] == 2
    assert result["duration"] == 4
    assert [beat["time"] for beat in result["beats"]] == [0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5]
    assert data == before


def test_grouped_on_beat_grace_keeps_original_beat_span_with_repeat_and_tempo_change(tmp_path):
    grace1 = {**beat(2, duration=(1, 32)), "graceNote": "onBeat"}
    grace2 = {**beat(3, duration=(1, 32)), "graceNote": "onBeat"}
    data = raw_score([measure(grace1, grace2, beat(4, duration=(1, 2)),
                              beat(6, duration=(1, 2)), repeatStart=True, repeat=2)])
    data["parts"][0]["automations"]["tempo"].append({"measure": 0, "position": 1920, "bpm": 60})
    result = import_json(tmp_path, data)
    notes = result["tracks"][0]["notes"]
    # Songsterr limits the complete group to its average encoded duration.
    assert [note["t"] for note in notes] == [0, 0.03125, 0.0625, 1, 3, 3.03125, 3.0625, 4]
    assert [note["sus"] for note in notes] == [0.03125, 0.03125, 0.9375, 2] * 2
    assert result["duration"] == 6


@pytest.mark.parametrize("case", ["unknown", "no_target", "equal_target", "short_target"])
def test_unsupported_or_unbounded_grace_timing_is_rejected(tmp_path, case):
    grace = {**beat(2, duration=(1, 16)), "graceNote": "onBeat"}
    target = beat(4, duration=(1, 1))
    if case == "unknown":
        grace["graceNote"] = case
    elif case == "equal_target":
        target["duration"] = [1, 16]
    elif case == "short_target":
        target["duration"] = [1, 32]
    beats = [grace] if case == "no_target" else [grace, target]
    with pytest.raises(ScoreImportError, match="grace"):
        import_json(tmp_path, raw_score([measure(*beats)]))


def test_repeat_restores_tempo_at_the_written_start(tmp_path):
    data = raw_score([measure(beat(1), repeatStart=True), measure(beat(2), repeat=2)])
    data["parts"][0]["automations"]["tempo"] = [
        {"measure": 0, "position": 0, "bpm": 60},
        {"measure": 1, "position": 0, "bpm": 120},
    ]
    notes = import_json(tmp_path, data)["tracks"][0]["notes"]
    assert [n["t"] for n in notes] == [0, 4, 6, 10]


@pytest.mark.skipif(not os.environ.get("FEEDFORGE_SONGSTERR_GP_FIXTURE"), reason="Optional local approved export")
def test_verified_gp8_export_offline():
    result = load_performance(Path(os.environ["FEEDFORGE_SONGSTERR_GP_FIXTURE"]),
                              {"title": "Woodland Rites", "artist": "Green Lung", "revisionId": 2585330})
    assert result["artist"] == "Green Lung"
    assert [len(t["tuning"]) for t in result["tracks"]] == [6, 6, 4, 6]
    assert sum(b["measure"] >= 0 for b in result["beats"]) == 168
    assert result["duration"] == pytest.approx(272.0)
    assert all(t["notes"] or t["chords"] for t in result["tracks"])
    assert result["source"]["trackCount"] == 7
    assert result["source"]["playableTrackCount"] == 4
    assert len(result["source"]["excludedTracks"]) == 3
