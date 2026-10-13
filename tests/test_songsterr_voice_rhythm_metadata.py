"""Legacy voice layout hints must never change explicit musical instructions."""
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


FEATURE = "voice.hasSameRhythm"
INVALID = [None, 0, 1, 0.0, 1.0, "", "true", "false", [], {}, [True], {"value": True}]


def voices(document):
    for part in document["parts"]:
        for bar in part["measures"]:
            yield from bar["voices"]


def rest(duration=(1, 1)):
    return {"duration": list(duration), "rest": True, "notes": [{"rest": True}]}


def musical_case(kind):
    upper = [beat(3, string=0, duration=(1, 2)), beat(5, string=0, duration=(1, 2))]
    if kind == "single_voice":
        return raw_score([measure(*upper)])
    if kind == "equal_rhythm_different_notes":
        lower = [beat(0, string=5, duration=(1, 2)), beat(2, string=5, duration=(1, 2))]
    elif kind == "unequal_rhythm_same_beat_count":
        upper = [beat(3, duration=(1, 4)), beat(5, duration=(1, 4)), beat(7, duration=(1, 2))]
        lower = [beat(0, string=5, duration=(1, 2)), rest((1, 4)), beat(2, string=5, duration=(1, 4))]
    elif kind == "different_beat_counts_rests_ties":
        lower = [beat(0, string=5, duration=(1, 4)), beat(0, string=5, duration=(1, 4), tie=True),
                 rest((1, 4)), beat(2, string=5, duration=(1, 4))]
    elif kind == "fully_resting_voice":
        lower = [rest((1, 4)) for _ in range(4)]
    else:
        raise AssertionError(kind)
    bar = measure(*upper)
    bar["voices"].append({"beats": lower})
    if kind == "fully_resting_voice":
        bar["voices"][1]["rest"] = True
    return raw_score([bar])


def music(document):
    result = render(parse(document))
    reference = expected(read_source(document), {"offset": 0, "scale": 1})
    return result["tracks"], result["duration"], reference


@pytest.mark.parametrize("kind", ["single_voice", "equal_rhythm_different_notes", "unequal_rhythm_same_beat_count",
                                   "different_beat_counts_rests_ties", "fully_resting_voice"])
@pytest.mark.parametrize("value", [False, True])
def test_layout_flag_preserves_all_voices_music_and_source(tmp_path, kind, value):
    document = musical_case(kind)
    baseline = music(document)
    for voice in voices(document):
        voice["hasSameRhythm"] = value
    original = deepcopy(document)
    assert music(document) == baseline
    result = import_json(tmp_path, document)
    assert result["sourceScore"]["document"] == original == document
    assert not any("hasSameRhythm" in warning for warning in result["warnings"])
    entry = next(row for row in result["featureInventory"]
                 if row["scope"] == "Songsterr voice" and row["field"] == "hasSameRhythm")
    assert entry["handling"] == "source"
    assert entry["representations"] == ["source"]
    report = inspect_songsterr(document)
    findings = [row for row in report["findings"] if row["feature"] == FEATURE]
    assert len(findings) == len(list(voices(document)))
    for row in findings:
        assert row["category"] == "source_metadata"
        assert row["impact"] == "source_retained"
        assert row["retained"] == "original_source"
        assert row["value"] is value
    assert report["status"] == "compatible"


def test_absence_adds_no_metadata_claim(tmp_path):
    document = musical_case("different_beat_counts_rests_ties")
    result = import_json(tmp_path, document)
    assert not any(row["feature"] == FEATURE for row in inspect_songsterr(document)["findings"])
    assert not any(row["field"] == "hasSameRhythm" for row in result["featureInventory"])


@pytest.mark.parametrize("value", INVALID)
@pytest.mark.parametrize("placement", ["sounding", "whole_rest", "excluded_instrument"])
def test_non_booleans_rejected_before_inactive_and_rest_shortcuts(value, placement):
    document = musical_case("fully_resting_voice")
    target = document["parts"][0]["measures"][0]["voices"][int(placement == "whole_rest")]
    if placement == "excluded_instrument":
        document["tracks"].append({"id": 1, "name": "Strings", "instrumentId": 48})
        document["parts"].append(deepcopy(document["parts"][0]))
        target = document["parts"][1]["measures"][0]["voices"][1]
    target["hasSameRhythm"] = value
    original = deepcopy(document)
    for reader in (parse, read_source):
        with pytest.raises(ValueError, match="hasSameRhythm"):
            reader(document)
    report = inspect_songsterr(document)
    assert report["status"] == "blocked"
    assert any(row["feature"] == FEATURE and row["impact"] == "blocking" for row in report["findings"])
    assert document == original


@pytest.mark.parametrize("value", [False, True])
def test_excluded_instrument_flag_is_retained_without_adding_music(value):
    document = musical_case("single_voice")
    document["tracks"].append({"id": 1, "name": "Strings", "instrumentId": 48})
    document["parts"].append(deepcopy(document["parts"][0]))
    baseline = music(document)
    document["parts"][1]["measures"][0]["voices"][0]["hasSameRhythm"] = value
    assert music(document) == baseline
    report = inspect_songsterr(document)
    rows = [row for row in report["findings"] if row["feature"] == FEATURE]
    assert len(rows) == 1
    assert rows[0]["impact"] == "source_retained"
    assert rows[0]["value"] is value


def test_unrelated_active_voice_instruction_still_blocks():
    document = musical_case("single_voice")
    next(voices(document)).update(hasSameRhythm=True, futureVoiceInstruction=True)
    report = inspect_songsterr(document)
    assert report["status"] == "blocked"
    assert any(row["feature"] == "voice.futureVoiceInstruction" and row["impact"] == "blocking"
               for row in report["findings"])
    for reader in (parse, read_source):
        with pytest.raises(ValueError):
            reader(document)


def build_package(root, document, contract=120, *, retain_source=True, inventory_version=None):
    from feedback_converter.song_import import load_performance
    from feedback_converter.song_import.builder import build_feedpak
    from test_song_import_builder import inputs

    path = root / "source.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    performance = load_performance(path)
    compatibility = deepcopy(performance["compatibilityReport"])
    if inventory_version is not None:
        compatibility["version"] = inventory_version
    _, audio, _, job = inputs(root)
    alignment = {"status": "validated", "offset": 1, "scale": 1}
    built = build_feedpak(performance, audio, alignment, job, output_dir=root / "out",
                         source_path=path if retain_source else None, compatibility=compatibility,
                         recipe={"preservationContract": contract})
    with ZipFile(built["stagingPath"]) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = yaml.safe_load(files["manifest.yaml"])
    assert files[manifest["song_import"]["sourceFile"]] == path.read_bytes()
    return files, manifest, alignment, path


def verify_files(tmp_path, files, source_path, alignment):
    from feedback_converter.song_import.verification import verify_import

    archive = tmp_path / "checked.feedpak"
    with ZipFile(archive, "w") as out:
        for name, content in files.items():
            out.writestr(name, content)
    return verify_import(source_path, archive, alignment)


@pytest.fixture(scope="module")
def packaged_voice_metadata(tmp_path_factory):
    document = musical_case("different_beat_counts_rests_ties")
    for voice, value in zip(voices(document), [True, False]):
        voice["hasSameRhythm"] = value
    document["tracks"].append({"id": 1, "name": "Strings", "instrumentId": 48})
    document["parts"].append({"measures": [measure(beat())]})
    document["parts"][1]["measures"][0]["voices"][0]["hasSameRhythm"] = False
    return build_package(tmp_path_factory.mktemp("voice-layout-package"), document)


@pytest.mark.parametrize("fault", [None, "missing", "duplicate", "wrong_value", "bool_zero", "missing_value",
                                   "wrong_retention", "wrong_impact", "wrong_location", "old_inventory",
                                   "old_contract", "old_contract_and_inventory", "fret", "time", "source_loss",
                                   "excluded_missing"])
@pytest.mark.parametrize("value", [False, True])
def test_contract_120_requires_exact_retention_and_unchanged_music(tmp_path, packaged_voice_metadata, fault, value):
    original_files, original_manifest, alignment, source_path = packaged_voice_metadata
    files, manifest = deepcopy(original_files), deepcopy(original_manifest)
    assert manifest["song_import"]["preservationContract"] == 120
    report = json.loads(files["import/compatibility.json"])
    target = next(row for row in report["findings"] if row["feature"] == FEATURE and row["value"] is value)
    if fault == "missing":
        report["findings"].remove(target)
        report["findingCount"] -= 1
    elif fault == "duplicate":
        report["findings"].append(deepcopy(target))
        report["findingCount"] += 1
    elif fault == "wrong_value":
        target["value"] = not target["value"]
    elif fault == "bool_zero":
        target["value"] = int(target["value"])
    elif fault == "missing_value":
        target.pop("value")
    elif fault == "wrong_retention":
        target["retained"] = "discarded"
    elif fault == "wrong_impact":
        target["impact"] = "display_or_expression"
    elif fault == "wrong_location":
        target["location"] += "/invented"
    elif fault == "excluded_missing":
        target = next(row for row in report["findings"] if row["feature"] == FEATURE and row["trackIndex"] == 1)
        report["findings"].remove(target)
        report["findingCount"] -= 1
    elif fault in {"fret", "time"}:
        chart_path = manifest["arrangements"][0]["file"]
        chart = json.loads(files[chart_path])
        note = (chart["notes"] + [n for chord in chart.get("chords", []) for n in chord["notes"]])[0]
        note["f" if fault == "fret" else "t"] += 1 if fault == "fret" else .1
        files[chart_path] = json.dumps(chart).encode()
    elif fault == "source_loss":
        retained_path = manifest["song_import"]["sourceFile"]
        retained = json.loads(files[retained_path])
        next(voices(retained)).pop("hasSameRhythm")
        files[retained_path] = json.dumps(retained).encode()
    if fault in {"old_inventory", "old_contract_and_inventory"}:
        report["version"] = 119
    if fault in {"old_contract", "old_contract_and_inventory"}:
        manifest["song_import"]["preservationContract"] = 119
    files["manifest.yaml"] = yaml.safe_dump(manifest).encode()
    files["import/compatibility.json"] = json.dumps(report).encode()
    result = verify_files(tmp_path, files, source_path, alignment)
    assert result["status"] == ("failed" if fault else "passed"), result
    if fault in {"fret", "time"}:
        assert ("note_f" if fault == "fret" else "note_time") in {row["code"] for row in result["errors"]}
    elif fault == "source_loss":
        assert "retained_source" in {row["code"] for row in result["errors"]}
    elif fault:
        assert any(row["code"].startswith("compatibility") for row in result["errors"]), result


@pytest.mark.parametrize("value", [False, True])
@pytest.mark.parametrize("fault", ["old_contract", "old_inventory", "missing_source"])
def test_builder_requires_current_metadata_contract_and_source(tmp_path, value, fault):
    from feedback_converter.song_import.audio import ImportFailure

    document = musical_case("single_voice")
    next(voices(document))["hasSameRhythm"] = value
    with pytest.raises(ImportFailure, match="120"):
        build_package(tmp_path, document, contract=119 if fault == "old_contract" else 120,
                      inventory_version=119 if fault == "old_inventory" else None,
                      retain_source=fault != "missing_source")


@pytest.mark.parametrize("legacy_false", [False, True])
def test_historical_119_without_active_flag_remains_verifiable(tmp_path, legacy_false):
    document = musical_case("single_voice")
    if legacy_false:
        next(voices(document))["hasSameRhythm"] = False
    files, manifest, alignment, source_path = build_package(tmp_path, document)
    manifest["song_import"]["preservationContract"] = 119
    report = json.loads(files["import/compatibility.json"])
    # The old reader skipped false as inactive unknown metadata. Reproduce its
    # inventory, without weakening contract 120's explicit false-value checks.
    report["findings"] = [row for row in report["findings"] if row["feature"] != FEATURE]
    report["findingCount"] = len(report["findings"])
    report["version"] = 119
    files["manifest.yaml"] = yaml.safe_dump(manifest).encode()
    files["import/compatibility.json"] = json.dumps(report).encode()
    result = verify_files(tmp_path, files, source_path, alignment)
    assert result["status"] == "passed", result


@pytest.mark.parametrize("mapped", [False, True])
def test_hybrid_package_keeps_explicit_donor_music_and_metadata(tmp_path, mapped):
    from test_songsterr_hybrid_lead import build, song

    document = song()
    for index, voice in enumerate(voices(document)):
        voice["hasSameRhythm"] = index % 2 == 0
    source, _, _, _, archive, report = build(tmp_path, document, mapped=mapped, difficulty=True, contract=120)
    assert report["status"] == "passed", report
    with ZipFile(archive) as package:
        manifest = yaml.safe_load(package.read("manifest.yaml"))
        assert len(manifest["arrangements"]) == 4
        assert package.read(manifest["song_import"]["sourceFile"]) == source.read_bytes()
        hybrid = json.loads(package.read(manifest["arrangements"][-1]["file"]))
        assert [note["f"] for note in hybrid["notes"]] == [3, 5, 8, 7]
