"""Independent note dynamics preserve authored scope, never gameplay loudness."""
from copy import deepcopy
import hashlib
import io
import json
from zipfile import ZipFile

import pytest
import yaml

from feedback_converter.song_import.verification import Check, _notation
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from test_song_import_score import beat, measure, raw_score
from test_song_import_verification import example
from test_songsterr_voice_rhythm_metadata import build_package, verify_files


LABELS = ("ppp", "pp", "p", "mp", "mf", "f", "ff", "fff")


@pytest.mark.parametrize("value", [None, *LABELS])
def test_independent_reader_keeps_only_authored_note_dynamic(value):
    first = beat(5, duration=(1, 4), velocity=value)
    first["notes"].append({"string": 1, "fret": 7})
    first["velocity"] = "fff"
    continuation = beat(5, duration=(1, 4), tie=True, velocity="p")
    doc = raw_score([measure(first, continuation, beat(9, duration=(1, 2)))])
    original = deepcopy(doc)
    source = songsterr(doc)
    wanted = expected(source, {"offset": 0, "scale": 1})
    written = wanted["parts"][0]["notation_beats"]
    assert written[0]["notation"]["dyn"] == "fff"
    assert written[0]["notes"][0].get("dyn") == value
    assert "dyn" not in written[0]["notes"][1]
    assert written[1]["notes"][0]["dyn"] == "p"
    assert written[1]["notes"][0]["tied"] is True
    assert "dyn" not in written[2]["notes"][0]
    assert "velocity" not in source.ignored
    assert not any("dyn" in n["note"] or "velocity" in n["note"]
                   for n in wanted["parts"][0]["notes"])
    assert doc == original


@pytest.mark.parametrize("value", [False, True, 0, 1, 0., "", "MF", "forte", [], {}, ["mf"]])
@pytest.mark.parametrize("placement", ["note", "rest", "excluded", "selection"])
def test_malformed_values_fail_before_rest_or_selection(value, placement):
    doc = raw_score([measure(beat(5))])
    target = doc["parts"][0]
    selected = None
    if placement in ("excluded", "selection"):
        doc["tracks"].append({**doc["tracks"][0], "id": 99,
                              "instrumentId": 0 if placement == "excluded" else 29})
        target = deepcopy(target)
        doc["parts"].append(target)
        if placement == "selection":
            selected = {0}
    note = target["measures"][0]["voices"][0]["beats"][0]["notes"][0]
    note["velocity"] = value
    if placement == "rest":
        note["rest"] = True
    with pytest.raises(ValueError, match="/notes/0/velocity: invalid note dynamic"):
        songsterr(doc, track_indices=selected)


def notation_checked(source, notation):
    wanted = expected(songsterr(source), {"offset": 1, "scale": 1})["parts"][0]
    stream = io.BytesIO()
    with ZipFile(stream, "w") as z:
        z.writestr("notation.json", json.dumps(notation))
    check = Check()
    with ZipFile(stream) as z:
        _notation(z, {"notation": "notation.json"}, wanted, check)
    return check.errors


@pytest.mark.parametrize("fault", [None, "missing", "wrong", "beat_scope", "companion", "later_note", "duplicate"])
def test_hand_authored_notation_rejects_dynamic_loss_and_scope_changes(fault):
    source, package = example()
    source_beats = source["parts"][0]["measures"][0]["voices"][0]["beats"]
    source_beats[0]["notes"][0]["velocity"] = "mf"
    source_beats[0]["notes"].append({"string": 4, "fret": 7})
    notation = package["notation.json"]
    beats = notation["measures"][0]["staves"]["staff"]["voices"][0]["beats"]
    beats[0]["notes"][0]["dyn"] = "mf"
    beats[0]["notes"].append({"str": 1, "fret": 7, "midi": 52})
    if fault == "missing":
        beats[0]["notes"][0].pop("dyn")
    elif fault == "wrong":
        beats[0]["notes"][0]["dyn"] = "f"
    elif fault == "beat_scope":
        beats[0]["dyn"] = beats[0]["notes"][0].pop("dyn")
    elif fault == "companion":
        beats[0]["notes"][1]["dyn"] = beats[0]["notes"][0].pop("dyn")
    elif fault == "later_note":
        beats[1]["notes"][0]["dyn"] = beats[0]["notes"][0].pop("dyn")
    elif fault == "duplicate":
        beats[0]["notes"][1]["dyn"] = "mf"
    errors = notation_checked(source, notation)
    assert bool(errors) == bool(fault), errors
    if fault:
        assert any("/dyn" in error["location"] for error in errors)


def dynamics_document():
    first = beat(5, duration=(1, 4), velocity="mf")
    first["notes"].append({"string": 1, "fret": 7, "velocity": None})
    first["velocity"] = "fff"
    doc = raw_score([measure(first, beat(5, duration=(1, 4), tie=True, velocity="p"),
                             beat(9, duration=(1, 4)),
                             {"duration": [1, 4], "notes": [{"rest": True, "velocity": "ff"}]} )])
    doc["tracks"].append({"id": 99, "name": "Excluded", "instrumentId": 0})
    doc["parts"].append({"measures": [measure({"duration": [1, 1],
                        "notes": [{"rest": True, "velocity": "ppp"}]})]})
    return doc


@pytest.fixture(scope="module")
def dynamics_package(tmp_path_factory):
    return build_package(tmp_path_factory.mktemp("note-dynamics-verification"), dynamics_document(), contract=124)


@pytest.mark.parametrize("fault", [None, "remove_finding", "duplicate_finding", "wrong_value", "wrong_track",
                                   "wrong_scope", "wrong_category", "wrong_status", "wrong_impact", "wrong_retention",
                                   "missing_null", "omit_null_value", "null_to_false", "rest_missing", "excluded_missing",
                                   "old_inventory", "old_contract", "both_old", "notation_missing", "notation_wrong",
                                   "notation_scope", "source_loss", "pitch", "timing"])
def test_contract_124_requires_exact_note_dynamic_data_and_evidence(tmp_path, dynamics_package, fault):
    initial_files, initial_manifest, alignment, source = dynamics_package
    files, manifest = deepcopy(initial_files), deepcopy(initial_manifest)
    report = json.loads(files["import/compatibility.json"])
    row = next(r for r in report["findings"] if r["feature"] == "note.velocity" and r["value"] == "mf")
    null = next(r for r in report["findings"] if r["feature"] == "note.velocity" and r["value"] is None)
    if fault in ("remove_finding", "missing_null", "rest_missing", "excluded_missing"):
        target = row if fault == "remove_finding" else null if fault == "missing_null" else next(
            r for r in report["findings"] if r["feature"] == "note.velocity" and
            r["value"] == ("ff" if fault == "rest_missing" else "ppp"))
        report["findings"].remove(target)
        report["findingCount"] -= 1
    elif fault == "duplicate_finding":
        report["findings"].append(deepcopy(row)); report["findingCount"] += 1
    elif fault == "wrong_value": row["value"] = "f"
    elif fault == "wrong_track": row["trackId"] = "99"
    elif fault == "wrong_scope": row["location"] = row["location"].replace("/notes/0", "")
    elif fault == "wrong_category": row["category"] = "source_metadata"
    elif fault == "wrong_status": row["workStatus"] = "source_retained"
    elif fault == "wrong_impact": row["impact"] = "source_retained"
    elif fault == "wrong_retention": row["retained"] = "discarded"
    elif fault == "omit_null_value": null.pop("value")
    elif fault == "null_to_false": null["value"] = False
    elif fault == "source_loss":
        retained = json.loads(files[manifest["song_import"]["sourceFile"]])
        retained["parts"][0]["measures"][0]["voices"][0]["beats"][0]["notes"][0].pop("velocity")
        files[manifest["song_import"]["sourceFile"]] = json.dumps(retained).encode()
    elif fault in ("notation_missing", "notation_wrong", "notation_scope"):
        name = manifest["arrangements"][0]["notation"]
        notation = json.loads(files[name])
        b = next(iter(notation["measures"][0]["staves"].values()))["voices"][0]["beats"][0]
        note = next(n for n in b["notes"] if n.get("dyn") == "mf")
        if fault == "notation_missing": note.pop("dyn")
        elif fault == "notation_wrong": note["dyn"] = "f"
        else: b["dyn"] = note.pop("dyn")
        files[name] = json.dumps(notation).encode()
    elif fault in ("pitch", "timing"):
        name = manifest["arrangements"][0]["file"]
        chart = json.loads(files[name])
        child = chart["chords"][0]["notes"][0]
        if fault == "pitch": child["f"] += 1
        else: chart["chords"][0]["t"] += .1
        files[name] = json.dumps(chart).encode()
    if fault in ("old_inventory", "both_old"): report["version"] = 123
    if fault in ("old_contract", "both_old"): manifest["song_import"]["preservationContract"] = 123
    files["manifest.yaml"] = yaml.safe_dump(manifest).encode()
    files["import/compatibility.json"] = json.dumps(report).encode()
    checked = verify_files(tmp_path, files, source, alignment)
    assert checked["status"] == ("failed" if fault else "passed"), checked


@pytest.mark.parametrize("retained_null", [False, True])
def test_historical_123_without_active_note_mark_remains_verifiable(tmp_path, retained_null):
    doc = raw_score([measure(beat(5))])
    if retained_null:
        doc["parts"][0]["measures"][0]["voices"][0]["beats"][0]["notes"][0]["velocity"] = None
    files, manifest, alignment, source = build_package(tmp_path, doc, contract=124)
    manifest["song_import"]["preservationContract"] = 123
    report = json.loads(files["import/compatibility.json"])
    report["version"] = 123
    report["findings"] = [r for r in report["findings"] if r["feature"] != "note.velocity"]
    report["findingCount"] = len(report["findings"])
    files["manifest.yaml"] = yaml.safe_dump(manifest).encode()
    files["import/compatibility.json"] = json.dumps(report).encode()
    result = verify_files(tmp_path, files, source, alignment)
    assert result["status"] == "passed", result


def notation_notes(notation):
    return [note for bar in notation["measures"] for staff in bar["staves"].values()
            for voice in staff["voices"] for b in voice["beats"] for note in b.get("notes", [])]


@pytest.fixture(scope="module", params=[False, True], ids=["constant-clock", "mapped-clock"])
def hybrid_dynamics_package(tmp_path_factory, request):
    from test_songsterr_hybrid_lead import build, song

    doc = song()
    for pi, mi, marking in ((0, 0, "p"), (1, 1, "mf"), (2, 2, "fff")):
        doc["parts"][pi]["measures"][mi]["voices"][0]["beats"][0]["notes"][0]["velocity"] = marking
    root = tmp_path_factory.mktemp("hybrid-note-dynamics")
    source, _, options, alignment, archive, report = build(root, doc, mapped=request.param,
                                                         difficulty=True, contract=124)
    assert report["status"] == "passed", report
    with ZipFile(archive) as z:
        files = {name: z.read(name) for name in z.namelist()}
    manifest = yaml.safe_load(files["manifest.yaml"])
    name = manifest["arrangements"][-1]["notation"]
    written = notation_notes(json.loads(files[name]))
    # Two selected donors and the main retain their own instructions. The last
    # unmarked main note does not inherit any donor or earlier note marking.
    assert [(note["fret"], note.get("dyn")) for note in written] == [(3, "p"), (5, "mf"), (8, "fff"), (7, None)]
    return files, manifest, options, alignment, source


@pytest.mark.parametrize("fault", [None, "drop_donor", "change_donor", "move_main", "spread_to_unmarked"])
def test_hybrid_preserves_donor_and_main_note_dynamics_and_detects_mutations(tmp_path, hybrid_dynamics_package, fault):
    from feedback_converter.song_import.verification import verify_import

    initial, manifest, options, alignment, source = hybrid_dynamics_package
    files = deepcopy(initial)
    name = manifest["arrangements"][-1]["notation"]
    notation = json.loads(files[name])
    written = {note["fret"]: note for note in notation_notes(notation)}
    if fault == "drop_donor": written[5].pop("dyn")
    elif fault == "change_donor": written[5]["dyn"] = "f"
    elif fault == "move_main": written[7]["dyn"] = written[3].pop("dyn")
    elif fault == "spread_to_unmarked": written[7]["dyn"] = "fff"
    if fault:
        files[name] = json.dumps(notation).encode()
        receipt = json.loads(files["import/hybrid-lead.json"])
        # Rehash deliberately: rejection must come from the source-derived
        # composition comparison, not just from a stale file hash.
        receipt["notationSha256"] = hashlib.sha256(files[name]).hexdigest()
        files["import/hybrid-lead.json"] = json.dumps(receipt).encode()
    target = tmp_path / "hybrid-checked.feedpak"
    with ZipFile(target, "w") as z:
        for path, content in files.items():
            z.writestr(path, content)
    result = verify_import(source, target, alignment, hybrid_options=options)
    assert result["status"] == ("failed" if fault else "passed"), result
    if fault:
        assert "hybrid_notation" in {error["code"] for error in result["errors"]}
