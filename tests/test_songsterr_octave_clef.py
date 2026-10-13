"""Octave notation is retained without applying a second pitch transposition."""
from copy import deepcopy
import json
from zipfile import ZipFile

import pytest
import yaml

from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr as read_source
from feedback_converter.song_import.verify_timeline import expected
from test_song_import_score import beat, import_json, measure, raw_score


VALID = [None, "8va", "8vb", "15ma", "15mb"]
INVALID = [False, True, 0, 1, -1, 8.0, "", "8VA", "8va ", "15va", "unknown", [], {}, ["8va"], {"type": "8va"}]


def source_beats(document):
    for part in document["parts"]:
        for bar in part["measures"]:
            for voice in bar["voices"]:
                yield from voice["beats"]


def music(document):
    result = render(parse(document))
    reference = expected(read_source(document), {"offset": 0, "scale": 1})
    return result["tracks"], result["duration"], reference


def gesture(kind):
    if kind == "chord_tie_repeat":
        first = beat(13, string=1, duration=(1, 2))
        first["notes"].append({"string": 2, "fret": 12})
        tied = deepcopy(first)
        for note in tied["notes"]:
            note["tie"] = True
        return raw_score([measure(first, tied, repeatStart=True, repeat=2)])
    if kind == "grace_hopo":
        grace = {**beat(2, duration=(1, 16), hp=True), "graceNote": "onBeat"}
        return raw_score([measure(beat(1, duration=(1, 4)), grace,
                                  beat(4, duration=(1, 4)), beat(4, duration=(1, 2), tie=True))])
    if kind == "slide":
        return raw_score([measure(beat(7, duration=(1, 2), slide="shift"),
                                  beat(9, duration=(1, 2)))])
    if kind == "bend":
        return raw_score([measure(beat(13, bend={"points": [
            {"position": 0, "tone": 0}, {"position": 60, "tone": 100}]}))])
    if kind == "rest":
        return raw_score([measure({"duration": [1, 1], "rest": True, "notes": [{"rest": True}]}),
                          measure(beat(13, string=1))])
    doc = raw_score([measure(beat(13, string=1, duration=(1, 2)), beat(15, string=1, duration=(1, 2)))],
                    tuning=[62, 57, 53, 48, 43, 36])
    if kind == "capo":
        doc["tracks"][0]["capo"] = 2
    return doc


@pytest.mark.parametrize("kind", ["plain", "capo", "chord_tie_repeat", "grace_hopo", "slide", "bend", "rest"])
@pytest.mark.parametrize("value", VALID)
def test_markings_preserve_all_music_and_immutable_source(tmp_path, kind, value):
    document = gesture(kind)
    baseline = music(document)
    for row in source_beats(document):
        row["octaveClef"] = value
    original = deepcopy(document)
    assert music(document) == baseline
    result = import_json(tmp_path, document)
    assert result["sourceScore"]["document"] == original == document
    assert not any("octaveClef" in warning for warning in result["warnings"])
    entry = next(row for row in result["featureInventory"]
                 if row["scope"] == "Songsterr beat" and row["field"] == "octaveClef")
    assert entry["handling"] == "source"
    assert entry["representations"] == ["source"]
    report = inspect_songsterr(document)
    findings = [row for row in report["findings"] if row["feature"] == "beat.octaveClef"]
    assert len(findings) == len(list(source_beats(document)))
    for row in findings:
        assert row["category"] == "source_metadata"
        assert row["impact"] == "source_retained"
        assert row["retained"] == "original_source"
        assert row["value"] == value and "value" in row
    assert report["status"] == "compatible"


def test_absence_adds_no_metadata_claim_and_does_not_transpose_explicit_pitch(tmp_path):
    document = gesture("plain")
    baseline = import_json(tmp_path, document)
    assert not any(row["feature"] == "beat.octaveClef" for row in inspect_songsterr(document)["findings"])
    written = baseline["tracks"][0]["notation"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"]
    assert [row["notes"][0]["midi"] for row in written] == [70, 72]
    assert [row["f"] for row in baseline["tracks"][0]["notes"]] == [13, 15]
    for row in source_beats(document):
        row["octaveClef"] = "8va"
    actual = import_json(tmp_path, document)
    assert actual["tracks"] == baseline["tracks"]


@pytest.mark.parametrize("value", INVALID)
@pytest.mark.parametrize("placement", ["note", "whole_rest", "note_rest"])
def test_invalid_values_cannot_hide_in_inactive_or_rest_shortcuts(value, placement):
    document = raw_score([measure(beat()), measure(beat())])
    first = next(source_beats(document))
    if placement != "note":
        first["notes"] = [{"rest": True}]
        if placement == "whole_rest":
            first["rest"] = True
    first["octaveClef"] = value
    original = deepcopy(document)
    for reader in (parse, read_source):
        with pytest.raises(ValueError, match="octaveClef"):
            reader(document)
    report = inspect_songsterr(document)
    assert report["status"] == "blocked"
    assert any(row["feature"] == "beat.octaveClef" and row["impact"] == "blocking"
               for row in report["findings"])
    assert document == original


def test_measure_clef_still_has_separate_written_notation_semantics(tmp_path):
    document = raw_score([measure({**beat(), "octaveClef": "8vb"}, clef="F4", repeatStart=True),
                          measure({**beat(), "octaveClef": "15ma"}, clef="G2", repeat=2)])
    result = import_json(tmp_path, document)
    assert [bar["staves"]["staff"]["clef"] for bar in result["tracks"][0]["notation"]["measures"]] == ["F4", "G2", "F4", "G2"]
    assert all(note["f"] == 3 for note in result["tracks"][0]["notes"])
    assert read_source(document).parts[0].clefs == ["F4", "G2"]


def test_unknown_adjacent_fields_remain_blocked():
    document = gesture("plain")
    next(source_beats(document)).update(octaveClef="8va", futurePitchInstruction=True)
    assert inspect_songsterr(document)["status"] == "blocked"
    for reader in (parse, read_source):
        with pytest.raises(ValueError):
            reader(document)


@pytest.mark.parametrize("value", VALID)
def test_excluded_instrument_marking_is_accounted_for_without_adding_music(value):
    document = gesture("plain")
    document["tracks"].append({"id": 1, "name": "Strings", "instrumentId": 48})
    document["parts"].append(deepcopy(document["parts"][0]))
    baseline = music(document)
    document["parts"][1]["measures"][0]["voices"][0]["beats"][0]["octaveClef"] = value
    assert music(document) == baseline
    report = inspect_songsterr(document)
    findings = [row for row in report["findings"] if row["feature"] == "beat.octaveClef"]
    assert len(findings) == 1
    assert findings[0]["impact"] == "source_retained"
    assert findings[0]["value"] == value


@pytest.mark.parametrize("value", [False, 0, "", [], {}, "newOctaveMode"])
def test_independent_reader_does_not_ignore_bad_markings_on_excluded_parts(value):
    document = gesture("plain")
    document["tracks"].append({"id": 1, "name": "Strings", "instrumentId": 48})
    document["parts"].append(deepcopy(document["parts"][0]))
    document["parts"][1]["measures"][0]["voices"][0]["beats"][0]["octaveClef"] = value
    with pytest.raises(ValueError, match="octaveClef"):
        read_source(document)
    assert inspect_songsterr(document)["status"] == "blocked"


@pytest.fixture(scope="module")
def packaged_octaves(tmp_path_factory):
    from feedback_converter.song_import import load_performance
    from feedback_converter.song_import.builder import build_feedpak
    from test_song_import_builder import inputs

    root = tmp_path_factory.mktemp("octave-package")
    rows = [beat(13 + i, string=1, duration=(1, 8) if i >= 3 else (1, 4)) for i in range(5)]
    for row, value in zip(rows, VALID):
        row["octaveClef"] = value
    document = raw_score([measure(*rows)])
    document["tracks"].append({"id": 1, "name": "Strings", "instrumentId": 48})
    document["parts"].append({"measures": [measure({**beat(12), "octaveClef": "8va"})]})
    path = root / "source.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    performance = load_performance(path)
    _, audio, _, job = inputs(root)
    alignment = {"status": "validated", "offset": 1, "scale": 1}
    built = build_feedpak(performance, audio, alignment, job, output_dir=root / "out",
                         source_path=path, compatibility=performance["compatibilityReport"],
                         recipe={"preservationContract": 118})
    with ZipFile(built["stagingPath"]) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = yaml.safe_load(files["manifest.yaml"])
    assert files[manifest["song_import"]["sourceFile"]] == path.read_bytes()
    assert manifest["song_import"]["preservationContract"] == 118
    return files, manifest, alignment, path


@pytest.mark.parametrize("fault", [None, "missing", "duplicate", "wrong_value", "missing_null_value",
                                   "wrong_retention", "wrong_impact", "wrong_location", "old_contract",
                                   "fret", "time", "source_loss", "excluded_missing"])
def test_contract_118_checks_exact_retention_and_rejects_corruption(tmp_path, packaged_octaves, fault):
    from feedback_converter.song_import.verification import verify_import

    original_files, manifest, alignment, source_path = packaged_octaves
    files = deepcopy(original_files)
    report = json.loads(files["import/compatibility.json"])
    target = next(row for row in report["findings"] if row["feature"] == "beat.octaveClef")
    if fault == "missing":
        report["findings"].remove(target)
        report["findingCount"] -= 1
    elif fault == "excluded_missing":
        excluded = next(row for row in report["findings"]
                        if row["feature"] == "beat.octaveClef" and row["trackIndex"] == 1)
        report["findings"].remove(excluded)
        report["findingCount"] -= 1
    elif fault == "duplicate":
        report["findings"].append(deepcopy(target))
        report["findingCount"] += 1
    elif fault == "wrong_value":
        target["value"] = "8va"
    elif fault == "missing_null_value":
        assert target["value"] is None
        target.pop("value")
    elif fault == "wrong_retention":
        target["retained"] = "discarded"
    elif fault == "wrong_impact":
        target["impact"] = "display_or_expression"
    elif fault == "wrong_location":
        target["location"] += "/invented"
    elif fault == "old_contract":
        report["version"] = 117
    elif fault in {"fret", "time"}:
        chart_path = manifest["arrangements"][0]["file"]
        chart = json.loads(files[chart_path])
        chart["notes"][0]["f" if fault == "fret" else "t"] += 1 if fault == "fret" else .1
        files[chart_path] = json.dumps(chart).encode()
    elif fault == "source_loss":
        source_file = manifest["song_import"]["sourceFile"]
        retained = json.loads(files[source_file])
        next(source_beats(retained)).pop("octaveClef")
        files[source_file] = json.dumps(retained).encode()
    files["import/compatibility.json"] = json.dumps(report).encode()
    archive = tmp_path / "checked.feedpak"
    with ZipFile(archive, "w") as out:
        for name, content in files.items():
            out.writestr(name, content)
    result = verify_import(source_path, archive, alignment)
    assert result["status"] == ("failed" if fault else "passed"), result
    if fault in {"fret", "time"}:
        assert ("note_f" if fault == "fret" else "note_time") in {row["code"] for row in result["errors"]}
    elif fault == "source_loss":
        assert "retained_source" in {row["code"] for row in result["errors"]}
    elif fault:
        assert any(row["code"].startswith("compatibility") for row in result["errors"]), result


@pytest.mark.parametrize("mapped", [False, True])
def test_hybrid_package_keeps_source_notation_without_transposing_donor_music(tmp_path, mapped):
    from test_songsterr_hybrid_lead import build, song

    document = song()
    for i, row in enumerate(source_beats(document)):
        row["octaveClef"] = VALID[i % len(VALID)]
    source, _, _, _, archive, report = build(tmp_path, document, mapped=mapped, difficulty=True, contract=118)
    assert report["status"] == "passed", report
    with ZipFile(archive) as package:
        manifest = yaml.safe_load(package.read("manifest.yaml"))
        assert len(manifest["arrangements"]) == 4
        assert package.read(manifest["song_import"]["sourceFile"]) == source.read_bytes()
        hybrid = json.loads(package.read(manifest["arrangements"][-1]["file"]))
        assert [note["f"] for note in hybrid["notes"]] == [3, 5, 8, 7]


def test_contract_117_without_new_metadata_remains_verifiable(tmp_path):
    from test_songsterr_hybrid_lead import build

    *_, archive, report = build(tmp_path, gesture("plain"), enabled=False, contract=117)
    assert report["status"] == "passed", report
    with ZipFile(archive) as package:
        manifest = yaml.safe_load(package.read("manifest.yaml"))
        assert manifest["song_import"]["preservationContract"] == 117
