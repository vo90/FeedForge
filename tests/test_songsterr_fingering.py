"""Authored teaching hints must survive, never become invented playing instructions."""
import json
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, measure, raw_score, import_json
from test_song_import_verification import example, verify
from test_songsterr_hybrid_lead import build, song
from feedback_converter.song_import import ScoreImportError
from feedback_converter.song_import.high_frets import project
from feedback_converter.song_import.voices import flat


@pytest.mark.parametrize("source,expected", [(None, None), ("0", None), ("T", 0), ("1", 1), ("2", 2), ("3", 3), ("4", 4)])
def test_canonical_source_values_are_optional_teaching_hints(tmp_path, source, expected):
    doc = raw_score([measure(beat(0 if source == "0" else 5, leftFingering=source))])
    p = import_json(tmp_path, doc)
    note = flat(p["tracks"][0])[0]
    assert note.get("fg") == expected
    if expected is None:
        assert "fg" not in note
    assert not [f for f in p["compatibilityReport"]["findings"] if f["feature"] == "note.leftFingering"]


@pytest.mark.parametrize("bad", [False, True, 0, 1, 1.0, "5", "thumb", "", {}, []])
def test_invalid_values_are_located_without_guessing(tmp_path, bad):
    with pytest.raises(ScoreImportError) as caught:
        import_json(tmp_path, raw_score([measure(beat(leftFingering=bad))]))
    rows = caught.value.compatibility["findings"]
    assert any(r["feature"] == "note.leftFingering" and r["impact"] == "blocking"
               and r["location"].endswith("/notes/0/leftFingering") for r in rows)


def test_templates_distinguish_fingering_and_reverse_source_string_order(tmp_path):
    bars = []
    for finger in ["1", "2", None, "1"]:
        b = beat(3, string=5, leftFingering=finger)
        b["notes"].append({"string": 4, "fret": 5, "leftFingering": "4"})
        bars.append(measure(b))
    p = import_json(tmp_path, raw_score(bars))
    track = p["tracks"][0]
    assert [c["id"] for c in track["chords"]] == [0, 1, 2, 0]
    assert [t["fingers"] for t in track["templates"]] == [
        [1, 4, -1, -1, -1, -1], [2, 4, -1, -1, -1, -1], [-1, 4, -1, -1, -1, -1]]


def test_repeated_strums_keep_each_strings_hint(tmp_path):
    b = beat(3, string=5, leftFingering="T")
    b["notes"].append({"string": 4, "fret": 5, "leftFingering": "3"})
    b["brushStroke"] = {"direction": "down", "duration": 30, "shift": 100}
    p = import_json(tmp_path, raw_score([measure(b, repeatStart=True, repeat=2)]))
    notes = flat(p["tracks"][0])
    assert [n["fg"] for n in notes] == [0, 3, 0, 3]
    assert notes[1]["t"] > notes[0]["t"] and notes[3]["t"] > notes[2]["t"]


@pytest.mark.parametrize("origin", [None, "1"])
def test_tied_finger_changes_do_not_backdate_or_create_attacks(tmp_path, origin):
    doc = raw_score([measure(beat(duration=(1, 2), leftFingering=origin),
                             beat(duration=(1, 2), tie=True, leftFingering="4"))])
    p = import_json(tmp_path, doc)
    notes = flat(p["tracks"][0])
    assert len(notes) == 1 and notes[0]["sus"] == 2
    assert notes[0].get("fg") == (1 if origin else None)
    findings = [f for f in p["compatibilityReport"]["findings"] if f["feature"] == "note.leftFingering"]
    assert len(findings) == 1 and findings[0]["value"] == "4"
    assert findings[0]["impact"] == "display_or_expression"


def test_trills_only_label_the_authored_attack(tmp_path):
    doc = raw_score([measure(beat(5, leftFingering="1", trill={"auxiliaryFret": 7, "speed": 60}))])
    notes = flat(import_json(tmp_path, doc)["tracks"][0])
    assert len(notes) > 2 and notes[0]["fg"] == 1
    assert all("fg" not in n for n in notes[1:])


def test_combined_voices_and_high_fret_projection_preserve_hints(tmp_path):
    from test_songsterr_voices import document
    doc = document("mixed")
    for voice in doc["parts"][0]["measures"][0]["voices"]:
        for n in voice["beats"][0]["notes"]:
            n["leftFingering"] = str(n["string"] + 1)
    p = import_json(tmp_path, doc)
    assert len(p["tracks"]) == 1
    track = p["tracks"][0]
    assert track["templates"][track["chords"][0]["id"]]["fingers"] == [-1, -1, -1, 3, 2, 1]
    b = beat(5, string=5, leftFingering="1")
    b["notes"].extend([{"string": 4, "fret": 7, "leftFingering": "3"},
                       {"string": 3, "fret": 26, "leftFingering": "4"}])
    projected, receipt = project(import_json(tmp_path, raw_score([measure(b)])))
    assert len(receipt["notes"]) == 1
    assert projected["tracks"][0]["templates"][0]["fingers"] == [1, 3, -1, -1, -1, -1]


@pytest.mark.parametrize("corruption", [None, "missing", "changed", "extra", "bool", "float"])
def test_independent_known_answer_rejects_fingering_corruption(tmp_path, corruption):
    source, package = example()
    source["parts"][0]["measures"][0]["voices"][0]["beats"][0]["notes"][0]["leftFingering"] = "3"
    notes = package["chart.json"]["notes"]
    notes[0]["fg"] = 3
    package["notation.json"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"][0]["notes"][0]["fng"] = 3
    if corruption == "missing": del notes[0]["fg"]
    if corruption == "changed": notes[0]["fg"] = 2
    if corruption == "extra": notes[1]["fg"] = 1
    if corruption == "bool": notes[0]["fg"] = True
    if corruption == "float": notes[0]["fg"] = 3.0
    result = verify(tmp_path, source, package)
    assert result["status"] == ("failed" if corruption else "passed"), result


@pytest.mark.parametrize("corruption", [False, True])
def test_independent_chord_diagram_matches_authored_fingers(tmp_path, corruption):
    source, package = example()
    raw = source["parts"][0]["measures"][0]["voices"][0]["beats"]
    raw[0]["notes"][0]["leftFingering"] = "1"
    raw[0]["notes"].append({"string": 4, "fret": 8, "leftFingering": "4"})
    chart = package["chart.json"]
    lead = chart["notes"].pop(0); lead.pop("t"); lead["fg"] = 1
    chart["chords"] = [{"t": 1, "id": 0, "notes": [lead, {"s": 1, "f": 8, "sus": .5, "fg": 4}]}]
    chart["templates"] = [{"frets": [3, 8, -1, -1, -1, -1], "fingers": [1, 4, -1, -1, -1, -1]}]
    written = package["notation.json"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"][0]["notes"]
    written[0]["fng"] = 1
    written.append({"midi": 53, "str": 1, "fret": 8, "fng": 4})
    if corruption: chart["templates"][0]["fingers"][0] = 2
    result = verify(tmp_path, source, package)
    assert result["status"] == ("failed" if corruption else "passed"), result


def test_real_feedpak_and_hybrid_lead_preserve_donor_hints(tmp_path):
    doc = song()
    doc["parts"][0]["measures"][0]["voices"][0]["beats"][0]["notes"][0]["leftFingering"] = "2"
    donor = doc["parts"][1]["measures"][1]["voices"][0]["beats"][0]
    donor["notes"][0]["leftFingering"] = "1"
    donor["notes"].append({"string": 1, "fret": 7, "leftFingering": "3"})
    *_, archive, report = build(tmp_path, doc, mapped=True, difficulty=True)
    assert report["status"] == "passed", report
    with ZipFile(archive) as z:
        manifest = yaml.safe_load(z.read("manifest.yaml"))
        hybrid = json.loads(z.read(manifest["arrangements"][-1]["file"]))
        assert hybrid["name"] == "Hybrid Lead"
        assert any(n.get("fg") == 2 for n in hybrid["notes"])
        assert any(t["fingers"] == [-1, -1, -1, -1, 3, 1] for t in hybrid["templates"])


@pytest.mark.parametrize("kind", ["tie", "fretted_zero", "trill"])
def test_full_package_retains_source_only_instructions_and_verifies(tmp_path, kind):
    doc = song(); donor = doc["parts"][1]["measures"][1]["voices"][0]
    if kind == "tie":
        donor["beats"][:1] = [beat(5, duration=(1, 4), leftFingering="1"),
                              beat(5, duration=(1, 4), tie=True, leftFingering="3")]
    else:
        donor["beats"][0]["notes"][0]["leftFingering"] = "0" if kind == "fretted_zero" else "1"
        if kind == "trill": donor["beats"][0]["notes"][0]["trill"] = {"auxiliaryFret": 7, "speed": 60}
    *_, archive, report = build(tmp_path, doc)
    assert report["status"] == "passed", report
    with ZipFile(archive) as z:
        manifest = yaml.safe_load(z.read("manifest.yaml"))
        assert json.loads(z.read(manifest["song_import"]["sourceFile"])) == doc
        if kind != "trill":
            compatibility = json.loads(z.read(manifest["song_import"]["compatibilityFile"]))
            assert any(f["feature"] == "note.leftFingering" for f in compatibility["findings"])
