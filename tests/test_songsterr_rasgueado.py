"""Keep the authored music; defer only the boolean strumming instruction."""
from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from feedback_converter.song_import import ScoreImportError, load_performance
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.verification import verify_import
from feedback_converter.song_import.verify_source import songsterr
from test_song_import_builder import inputs
from test_song_import_compatibility_verification import reported_fixture
from test_song_import_score import beat, measure, raw_score
from test_song_import_verification import verify


def passage(direction="down"):
    chord = beat(fret=0, duration=(1, 2))
    chord["notes"] = [{"string": s, "fret": f} for s, f in enumerate([0, 0, 1, 2, 2, 0])]
    chord["arpeggio"] = {"direction": direction, "duration": 238, "shift": 12}
    return raw_score([measure(beat(duration=(1, 4)), beat(duration=(1, 4), tie=True),
                              chord, repeatStart=True, repeat=2)])


@pytest.mark.parametrize("value", [True, False, None])
@pytest.mark.parametrize("program", [29, 33, 0])
def test_boolean_instruction_is_located_nonblocking_and_source_is_unchanged(value, program):
    doc = passage()
    doc["tracks"][0]["instrumentId"] = program
    doc["parts"][0]["measures"][0]["voices"][0]["beats"][0]["hasRasgueado"] = value
    before = deepcopy(doc)
    report = inspect_songsterr(doc)
    found = [r for r in report["findings"] if r["feature"] == "beat.hasRasgueado"]
    assert bool(found) is (value is True and program != 0)
    if found:
        row = found[0]
        assert row["category"] == "game_limitation"
        assert row["impact"] == "display_or_expression"
        assert row["workStatus"] == "display_limitation" and "decisionId" not in row
        assert row["location"] == "parts/0/measures/0/voices/0/beats/0/hasRasgueado"
        assert (row["measure"], row["voice"], row["beat"]) == (1, 1, 1)
        assert row["retained"] == "original_source" and row["value"] is True
        assert "scored normally" in row["message"]
    assert report["status"] != "blocked" and doc == before


@pytest.mark.parametrize("value", [0, 1, "", "true", "eamii", [], {}, [True]])
def test_malformed_instruction_is_not_coerced_or_ignored(value):
    doc = passage()
    doc["parts"][0]["measures"][0]["voices"][0]["beats"][0]["hasRasgueado"] = value
    report = inspect_songsterr(doc)
    row = next(r for r in report["findings"] if r["feature"] == "beat.hasRasgueado")
    assert report["status"] == "blocked" and row["category"] == "source_structure"
    with pytest.raises(ScoreImportError, match="rasgueado"):
        parse(doc)
    with pytest.raises(ValueError, match="rasgueado"):
        songsterr(doc)


@pytest.mark.parametrize("direction", ["up", "down"])
def test_marked_notes_ties_repeats_and_explicit_strums_are_identical(tmp_path, direction):
    doc = passage(direction)
    path = tmp_path / "source.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    ordinary = load_performance(path)
    for b in doc["parts"][0]["measures"][0]["voices"][0]["beats"]:
        b["hasRasgueado"] = True
    path.write_text(json.dumps(doc), encoding="utf-8")
    marked = load_performance(path)
    assert marked["tracks"] == ordinary["tracks"]
    assert marked["strumEvidence"] == ordinary["strumEvidence"]
    assert marked["duration"] == ordinary["duration"]
    assert len(marked["strumEvidence"]) == 2  # Authored repeat, not invented strokes.
    assert all(g["direction"] == direction for g in marked["strumEvidence"])
    found = [r for r in marked["compatibilityReport"]["findings"] if r["feature"] == "beat.hasRasgueado"]
    assert len(found) == 3  # One entry per source location, including the tie.


def test_built_package_preserves_marking_and_verifies_authored_music(tmp_path):
    _, audio, _, job = inputs(tmp_path)
    doc = passage()
    for b in doc["parts"][0]["measures"][0]["voices"][0]["beats"]:
        b["hasRasgueado"] = True
    path = tmp_path / "source.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    performance = load_performance(path)
    alignment = {"status": "validated", "offset": 1, "scale": 1}
    built = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path / "out",
                          source_path=path, compatibility=performance["compatibilityReport"],
                          recipe={"preservationContract": 29})
    archive = Path(built["stagingPath"])
    report = verify_import(path, archive, alignment)
    assert report["status"] == "passed", report
    with ZipFile(archive) as z:
        manifest = yaml.safe_load(z.read("manifest.yaml"))
        assert z.read(manifest["song_import"]["sourceFile"]) == path.read_bytes()
        findings = json.loads(z.read(manifest["song_import"]["compatibilityFile"]))["findings"]
        assert sum(r["feature"] == "beat.hasRasgueado" for r in findings) == 3


@pytest.mark.parametrize("fault", [None, "report_missing", "value", "numeric_value", "location", "source", "contract",
                                  "pitch", "time", "sustain", "extra_attack", "missing_note"])
def test_independent_package_check_requires_evidence_and_unchanged_notes(tmp_path, fault):
    source, package = reported_fixture()
    source["parts"][0]["measures"][0]["voices"][0]["beats"][0]["hasRasgueado"] = True
    report = package["import/compatibility.json"]
    row = {"feature": "beat.hasRasgueado", "location": "parts/0/measures/0/voices/0/beats/0/hasRasgueado",
           "value": True, "valueTruncated": False, "retained": "original_source",
           "impact": "display_or_expression", "category": "game_limitation"}
    report["findings"].append(row)
    report["findingCount"] += 1
    notes = package["chart.json"]["notes"]
    if fault == "report_missing":
        report["findings"].pop(); report["findingCount"] -= 1
    elif fault == "value": row["value"] = False
    elif fault == "numeric_value": row["value"] = 1
    elif fault == "contract": package["manifest.yaml"].pop("song_import")
    elif fault == "location": row["location"] = row["location"].replace("beats/0", "beats/1")
    elif fault == "source":
        package["import/source.json"] = deepcopy(source)
        del package["import/source.json"]["parts"][0]["measures"][0]["voices"][0]["beats"][0]["hasRasgueado"]
    elif fault == "pitch": notes[0]["f"] += 1
    elif fault == "time": notes[0]["t"] += .1
    elif fault == "sustain": notes[0]["sus"] += .1
    elif fault == "extra_attack": notes.append(deepcopy(notes[0]))
    elif fault == "missing_note": notes.pop(0)
    result = verify(tmp_path, source, package)
    assert result["status"] == ("passed" if fault is None else "failed"), result
