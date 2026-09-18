"""Known-answer verification tests: expected archives are not made by the importer.

Mutation cases prove the checker catches schema-valid musical corruption. The
small GPIF example is literal source XML, independently authored from JSON data.
"""
from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from feedback_converter.song_import.verification import verify_import


def example():
    source = {"format": "songsterr", "songId": "12", "revisionId": "34", "title": "Fixture", "artist": "Artist",
              "tracks": [{"id": "g", "name": "Lead", "instrumentId": 30, "tuning": [64, 59, 55, 50, 45, 40]}],
              "parts": [{"automations": {"tempo": [{"measure": 0, "position": 0, "bpm": 120}]}, "measures": [
                  {"signature": [4, 4], "marker": "Intro", "voices": [{"beats": [
                      {"type": 4, "duration": [1, 4], "notes": [{"string": 5, "fret": 3, "hp": True}]},
                      {"type": 4, "duration": [1, 4], "notes": [{"string": 5, "fret": 5}]},
                      {"type": 4, "duration": [1, 4], "notes": [{"string": 5, "fret": 7, "ghost": True}]},
                      {"type": 4, "duration": [1, 4], "notes": [{"string": 5, "fret": 30, "slide": "upwards"}]},
                  ]}]}]}]}
    beats = [{"time": 1, "measure": 1}, {"time": 1.5, "measure": -1}, {"time": 2, "measure": -1}, {"time": 2.5, "measure": -1}]
    sections = [{"time": 1, "name": "Intro"}]
    tempos = [{"time": 1, "bpm": 120}]
    chart = {"name": "Lead", "tuning": [0] * 6, "capo": 0, "notes": [
        {"t": 1, "s": 0, "f": 3, "sus": .5, "ln": True},
        {"t": 1.5, "s": 0, "f": 5, "sus": .5, "ho": True},
        {"t": 2, "s": 0, "f": 7, "sus": .5, "ghost": True},
        {"t": 2.5, "s": 0, "f": 30, "sus": .5, "slide_out": "up",
         "slide_out_marks": [{"direction": "up", "start": 0, "end": .5}]}],
        "chords": [], "templates": [], "beats": deepcopy(beats), "sections": deepcopy(sections), "tempos": deepcopy(tempos)}
    timeline = {"version": 1, "beats": beats, "sections": sections, "tempos": tempos, "time_signatures": [{"time": 1, "ts": [4, 4]}]}
    notation = {"version": 1, "instrument": "guitar", "measures": [{"idx": 1, "source_measure": 1, "t": 1,
        "ts": [4, 4], "tempo": 120, "written_tempo": 120, "duration_seconds": 2, "staves": {"staff": {"voices": [
        {"v": 0, "beats": [{"t": t, "duration_seconds": .5, "beat_pos": [i, 1], "dur": 4, "notes": [
            {"midi": 40 + f, "str": 0, "fret": f, **({"ho": True} if f == 5 else {"ghost": True} if f == 7 else {})}]}
                            for i, (t, f) in enumerate([(1, 3), (1.5, 5), (2, 7), (2.5, 30)])]}]}}}]}
    manifest = {"feedpak_version": "1.16.0", "title": "Fixture", "artist": "Artist", "duration": 4,
                "cover": "cover.png", "preview": "audio/preview.ogg", "stems": [{"file": "audio/full.ogg"}],
                "song_timeline": "timeline.json", "arrangements": [{"id": "g", "name": "Lead", "type": "lead",
                    "file": "chart.json", "notation": "notation.json", "tuning": [0] * 6, "capo": 0}]}
    return source, {"chart.json": chart, "timeline.json": timeline, "notation.json": notation, "manifest.yaml": manifest}


def write(tmp_path, source, package):
    path = tmp_path / "score.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    archive = tmp_path / "result.feedpak"
    with ZipFile(archive, "w") as z:
        for name, value in package.items():
            z.writestr(name, yaml.safe_dump(value) if name.endswith(".yaml") else json.dumps(value))
        for name in ("cover.png", "audio/preview.ogg", "audio/full.ogg"):
            z.writestr(name, b"asset existence fixture; decoding is separately validated by builder")
    return path, archive


def verify(tmp_path, source=None, package=None, alignment=None):
    s, p = example()
    path, archive = write(tmp_path, source or s, package or p)
    return verify_import(path, archive, alignment or {"offset": 1, "scale": 1})


def test_hand_calculated_source_and_archive_pass_without_converter(tmp_path):
    report = verify(tmp_path)
    assert report["status"] == "passed", report
    assert report["counts"]["expectedNotes"] == report["counts"]["archivedNotes"] == 4
    assert report["musicalQualityAssessed"] is False


@pytest.mark.parametrize("fault,code", [
    ("remove", "note_count"), ("duplicate", "note_count"), ("fret", "note_f"), ("string", "note_s"),
    ("time", "note_time"), ("sustain", "note_sustain"), ("ghost", "note_technique"), ("slide", "note_technique"),
    ("downbeats", "timeline_value"), ("meter", "timeline_value"), ("section", "timeline_value"),
    ("tempo", "tempo_value"), ("tuning", "tuning"), ("notation", "notation_duration"),
    ("missing_asset", "missing_asset"), ("nan", "nonfinite_number"),
])
def test_faults_are_detected_with_localized_diagnostics(tmp_path, fault, code):
    source, package = example()
    chart, timeline = package["chart.json"], package["timeline.json"]
    if fault == "remove": chart["notes"].pop()
    elif fault == "duplicate": chart["notes"].append(deepcopy(chart["notes"][-1]))
    elif fault == "fret": chart["notes"][0]["f"] += 1
    elif fault == "string": chart["notes"][0]["s"] = 5
    elif fault == "time": chart["notes"][0]["t"] += .01
    elif fault == "sustain": chart["notes"][0]["sus"] -= .01
    elif fault == "ghost": del chart["notes"][2]["ghost"]
    elif fault == "slide": chart["notes"][-1]["slu"] = 35; del chart["notes"][-1]["slide_out"]
    elif fault == "downbeats": timeline["beats"][1]["measure"] = 1
    elif fault == "meter": timeline["time_signatures"][0]["ts"] = [3, 4]
    elif fault == "section": timeline["sections"][0]["name"] = "Verse"
    elif fault == "tempo": timeline["tempos"][0]["bpm"] = 121
    elif fault == "tuning": chart["tuning"][0] = -2
    elif fault == "notation": package["notation.json"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"][0]["duration_seconds"] = .4
    elif fault == "missing_asset": package["manifest.yaml"]["preview"] = "missing.ogg"
    elif fault == "nan": chart["notes"][0]["t"] = float("nan")
    report = verify(tmp_path, source, package)
    assert report["status"] == "failed", report
    assert code in {error["code"] for error in report["errors"]}, report
    assert all(error["location"] for error in report["errors"])


def test_unknown_active_musical_field_is_not_blanket_passed(tmp_path):
    source, package = example()
    source["parts"][0]["measures"][0]["voices"][0]["beats"][0]["notes"][0]["trill"] = {"fret": 7}
    report = verify(tmp_path, source, package)
    assert report["status"] == "unsupported"
    assert "notes/0/trill" in report["unsupported"][0]["location"]


def test_retained_metadata_and_unusual_high_fret_are_not_music_quality_failures(tmp_path):
    source, package = example()
    source["parts"][0]["measures"][0]["voices"][0]["beats"][3]["text"] = {"text": "Unusual authored fingering"}
    package["notation.json"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"][3]["txt"] = "Unusual authored fingering"
    assert verify(tmp_path, source, package)["status"] == "passed"


def test_piecewise_interpolation_is_independent_and_source_identity_checked(tmp_path):
    source, package = example()
    map_ = {"mapping": "piecewise-linear", "anchors": [{"score": 0, "audio": 1}, {"score": 2, "audio": 3}],
            "provenance": {"songId": "12", "revisionId": "34"}}
    assert verify(tmp_path, source, package, map_)["status"] == "passed"
    map_["provenance"]["revisionId"] = "35"
    assert verify(tmp_path, source, package, map_)["status"] == "failed"


def test_chord_child_notes_are_not_lost_or_counted_as_one_note(tmp_path):
    source, package = example()
    raw = source["parts"][0]["measures"][0]["voices"][0]["beats"]
    raw[0]["notes"].append({"string": 4, "fret": 8})
    chart = package["chart.json"]
    lead = chart["notes"].pop(0)
    lead.pop("t")
    chart["chords"] = [{"t": 1, "id": 0, "notes": [lead, {"s": 1, "f": 8, "sus": .5}]}]
    chart["templates"] = [{"frets": [3, 8, -1, -1, -1, -1], "fingers": [-1] * 6}]
    package["notation.json"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"][0]["notes"].append({"midi": 53, "str": 1, "fret": 8})
    report = verify(tmp_path, source, package)
    assert report["status"] == "passed", report
    assert report["counts"]["archivedNotes"] == 5
    chart["templates"][0].update(name="Invented", fingers=[1] * 6)
    report = verify(tmp_path, source, package)
    assert {"invented_chord_name", "invented_chord_fingers"} <= {e["code"] for e in report["errors"]}


def test_tie_segments_merge_without_restrike(tmp_path):
    source, package = example()
    raw = source["parts"][0]["measures"][0]["voices"][0]["beats"]
    raw[0]["notes"][0] = {"string": 5, "fret": 3}
    raw[1]["notes"][0] = {"string": 5, "fret": 3, "tie": True}
    notes = package["chart.json"]["notes"]
    notes[0] = {"t": 1, "s": 0, "f": 3, "sus": 1}
    notes.pop(1)
    package["notation.json"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"][1]["notes"] = [{"midi": 43, "str": 0, "fret": 3, "tied": True}]
    report = verify(tmp_path, source, package)
    assert report["status"] == "passed", report
    assert report["counts"]["tieSegments"] == 1


def test_gpif_is_read_independently_from_literal_xml(tmp_path):
    source, package = example()
    chart = package["chart.json"]
    for note in chart["notes"]:
        for key in ("ln", "ho", "ghost", "slide_out", "slide_out_marks"):
            note.pop(key, None)
    for beat in package["notation.json"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"]:
        for note in beat["notes"]:
            note.pop("ho", None)
            note.pop("ghost", None)
    path, archive = write(tmp_path, source, package)
    xml = '''<GPIF><GPVersion>8.1.3</GPVersion><Score><Title>Fixture</Title><Artist>Artist</Artist></Score>
    <MasterTrack><Tracks>g</Tracks><Automations><Automation><Type>Tempo</Type><Bar>0</Bar><Position>0</Position><Value>120 2</Value></Automation></Automations></MasterTrack>
    <Tracks><Track id="g"><Name>Lead</Name><InstrumentSet><Type>electricGuitar</Type></InstrumentSet><Staves><Staff><Properties><Property name="Tuning"><Pitches>40 45 50 55 59 64</Pitches></Property></Properties></Staff></Staves></Track></Tracks>
    <MasterBars><MasterBar><Time>4/4</Time><Section><Text>Intro</Text></Section><Bars>b</Bars></MasterBar></MasterBars>
    <Bars><Bar id="b"><Voices>v</Voices></Bar></Bars><Voices><Voice id="v"><Beats>a b c d</Beats></Voice></Voices>
    <Rhythms><Rhythm id="q"><NoteValue>Quarter</NoteValue></Rhythm></Rhythms>
    <Beats><Beat id="a"><Rhythm ref="q"/><Notes>a</Notes></Beat><Beat id="b"><Rhythm ref="q"/><Notes>b</Notes></Beat><Beat id="c"><Rhythm ref="q"/><Notes>c</Notes></Beat><Beat id="d"><Rhythm ref="q"/><Notes>d</Notes></Beat></Beats>
    <Notes><Note id="a"><Properties><Property name="String"><String>0</String></Property><Property name="Fret"><Fret>3</Fret></Property></Properties></Note>
    <Note id="b"><Properties><Property name="String"><String>0</String></Property><Property name="Fret"><Fret>5</Fret></Property></Properties></Note>
    <Note id="c"><Properties><Property name="String"><String>0</String></Property><Property name="Fret"><Fret>7</Fret></Property></Properties></Note>
    <Note id="d"><Properties><Property name="String"><String>0</String></Property><Property name="Fret"><Fret>30</Fret></Property></Properties></Note></Notes></GPIF>'''
    gp = tmp_path / "example.gp"
    with ZipFile(gp, "w") as z:
        z.writestr("Content/score.gpif", xml)
    report = verify_import(gp, archive, {"offset": 1, "scale": 1})
    assert report["status"] == "passed", report
    assert report["format"] == "gpif"
    with ZipFile(gp, "w") as z:
        z.writestr("Content/score.gpif", xml.replace('<Note id="a">', '<Note id="a"><UnverifiedTechnique/>'))
    report = verify_import(gp, archive, {"offset": 1, "scale": 1})
    assert report["status"] == "unsupported", report
    assert "UnverifiedTechnique" in report["unsupported"][0]["message"]
    # Wide vibrato is authored notation, independently distinguished from the
    # generic playable vibrato flag.
    with ZipFile(gp, "w") as z:
        z.writestr("Content/score.gpif", xml.replace('<Note id="a">', '<Note id="a"><Vibrato>Wide</Vibrato>'))
    chart["notes"][0]["vb"] = True
    first = package["notation.json"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"][0]["notes"][0]
    first.update(vib=True, vibw=True)
    _, archive = write(tmp_path, source, package)
    assert verify_import(gp, archive, {"offset": 1, "scale": 1})["status"] == "passed"
    del first["vibw"]
    _, archive = write(tmp_path, source, package)
    assert verify_import(gp, archive, {"offset": 1, "scale": 1})["status"] == "failed"


def test_verification_has_no_production_parser_or_clock_dependency():
    import ast
    root = Path(__file__).resolve().parents[1] / "src/feedback_converter/song_import"
    forbidden = {"songsterr", "gpif", "score", "timeline", "builder", "alignment", "synchronization", "model"}
    for file in ("verification.py", "verify_source.py", "verify_timeline.py"):
        tree = ast.parse((root / file).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[-1] not in forbidden


def test_repeat_order_is_verified_from_raw_navigation(tmp_path):
    source, package = example()
    bar = source["parts"][0]["measures"][0]
    bar.update(repeatStart=True, repeat=2)
    chart, timeline = package["chart.json"], package["timeline.json"]
    chart["notes"] += [{**n, "t": n["t"] + 2} for n in deepcopy(chart["notes"])]
    for obj in (chart, timeline):
        obj["beats"] += [{**b, "time": b["time"] + 2, "measure": 2 if b["measure"] > 0 else -1} for b in deepcopy(obj["beats"])]
        obj["sections"].append({"time": 3, "name": "Intro"})
    m = deepcopy(package["notation.json"]["measures"][0])
    m.update(idx=2, t=3)
    for b in m["staves"]["staff"]["voices"][0]["beats"]:
        b["t"] += 2
    package["notation.json"]["measures"].append(m)
    package["manifest.yaml"]["duration"] = 6
    report = verify(tmp_path, source, package)
    assert report["status"] == "passed", report
    assert report["counts"]["performedMeasures"] == 2
    bar["repeat"] = 3
    assert verify(tmp_path, source, package)["status"] == "failed"


def test_source_bend_envelope_and_fault_are_checked(tmp_path):
    source, package = example()
    source["parts"][0]["measures"][0]["voices"][0]["beats"][0]["notes"][0]["bend"] = {
        "points": [{"position": 0, "tone": 0}, {"position": 60, "tone": 100}]}
    package["chart.json"]["notes"][0].update(bn=2, bnv=[{"t": 0, "v": 0}, {"t": .5, "v": 2}])
    assert verify(tmp_path, source, package)["status"] == "passed"
    # Captured map coordinates are floats. A sub-nanosecond spelling of the
    # exact rational onset must not invent a duplicate zero-time bend point.
    near_boundary = {"mapping": "piecewise-linear", "anchors": [{"score": 1e-13, "audio": 1}, {"score": 2, "audio": 3}]}
    assert verify(tmp_path, source, package, near_boundary)["status"] == "passed"
    package["chart.json"]["notes"][0]["bnv"][1]["v"] = 1
    report = verify(tmp_path, source, package)
    assert "bend_value" in {e["code"] for e in report["errors"]}


def test_multi_voice_overlap_preserved_without_musical_judgement(tmp_path):
    source, package = example()
    source["parts"][0]["measures"][0]["voices"].append({"beats": [
        {"duration": [1, 1], "type": 1, "notes": [{"string": 5, "fret": 8}]}]})
    package["chart.json"]["notes"].append({"t": 1, "s": 0, "f": 8, "sus": 2})
    package["notation.json"]["measures"][0]["staves"]["staff"]["voices"].append({"v": 1, "beats": [
        {"t": 1, "duration_seconds": 2, "beat_pos": [0, 1], "dur": 1, "notes": [{"midi": 48, "str": 0, "fret": 8}]}]})
    report = verify(tmp_path, source, package)
    assert report["status"] == "passed", report


def test_notation_cannot_be_silently_omitted_or_transposed(tmp_path):
    source, package = example()
    package["notation.json"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"][0]["notes"][0]["midi"] += 1
    report = verify(tmp_path, source, package)
    assert "notation_note" in {e["code"] for e in report["errors"]}
    del package["manifest.yaml"]["arrangements"][0]["notation"]
    report = verify(tmp_path, source, package)
    assert report["status"] == "unsupported", report


def test_unknown_exported_gameplay_field_is_not_silently_accepted(tmp_path):
    source, package = example()
    package["chart.json"]["notes"][0]["ignore"] = True
    report = verify(tmp_path, source, package)
    assert "unexpected_note_field" in {e["code"] for e in report["errors"]}


def test_source_hash_mismatch_is_a_failed_conversion_not_a_music_warning(tmp_path):
    source, package = example()
    package["manifest.yaml"]["song_import"] = {"scoreHash": "0" * 64}
    report = verify(tmp_path, source, package)
    assert report["status"] == "failed"
    assert "source_hash" in {e["code"] for e in report["errors"]}


def test_ending_without_repeat_is_unverified_instead_of_silently_skipping_music(tmp_path):
    source, package = example()
    source["parts"][0]["measures"][0]["alternateEnding"] = [2]
    report = verify(tmp_path, source, package)
    assert report["status"] == "unsupported", report


def test_unknown_timing_automation_is_not_silently_accepted(tmp_path):
    source, package = example()
    source["parts"][0]["automations"]["tempo"][0]["swingAmount"] = .5
    assert verify(tmp_path, source, package)["status"] == "unsupported"


@pytest.mark.parametrize("fault,code", [
    ("ghost", "notation_note"), ("hopo", "notation_note"), ("invent_dynamic", "invented_notation_annotation"),
    ("measure_idx", "notation_measure"), ("measure_meter", "notation_measure"), ("measure_tempo", "notation_measure"),
    ("voice", "notation_voice"), ("zero_slide", "note_technique"),
])
def test_peer_review_mutations_are_detected(tmp_path, fault, code):
    source, package = example()
    measure = package["notation.json"]["measures"][0]
    voice = measure["staves"]["staff"]["voices"][0]
    if fault == "ghost": del voice["beats"][2]["notes"][0]["ghost"]
    elif fault == "hopo": del voice["beats"][1]["notes"][0]["ho"]
    elif fault == "invent_dynamic": voice["beats"][0]["dyn"] = "fff"
    elif fault == "measure_idx": measure["idx"] = 999
    elif fault == "measure_meter": measure["ts"] = [3, 8]
    elif fault == "measure_tempo": measure["tempo"] = 1
    elif fault == "voice": voice["v"] = 1
    elif fault == "zero_slide": package["chart.json"]["notes"][0]["sl"] = 0
    report = verify(tmp_path, source, package)
    assert report["status"] == "failed", report
    assert code in {e["code"] for e in report["errors"]}, report


def test_unknown_zero_valued_musical_field_is_not_treated_as_false(tmp_path):
    source, package = example()
    source["parts"][0]["measures"][0]["voices"][0]["beats"][0]["notes"][0]["futureSlideTo"] = 0
    assert verify(tmp_path, source, package)["status"] == "unsupported"


@pytest.mark.parametrize("bad", ["<GPIF>", '<!DOCTYPE GPIF [<!ENTITY x "value">]><GPIF/>'])
def test_malformed_or_entity_xml_returns_a_bounded_failure(tmp_path, bad):
    source, package = example()
    _, archive = write(tmp_path, source, package)
    path = tmp_path / "invalid.gpif"
    path.write_text(bad)
    report = verify_import(path, archive, {"offset": 1, "scale": 1})
    assert report["status"] == "failed"
    assert report["errorCount"] == 1
