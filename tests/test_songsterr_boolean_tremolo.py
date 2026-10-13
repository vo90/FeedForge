"""Point-free boolean bar markers are retained without inventing expression."""
from copy import deepcopy
import json
from zipfile import ZipFile

import pytest
import yaml

from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.songsterr_whammy import source_whammy
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr as read_source
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.verify_whammy import read_bar
from test_song_import_score import beat, import_json, measure, raw_score
from test_songsterr_voice_rhythm_metadata import build_package, verify_files
from test_songsterr_whammy import bar, compare


FEATURE = "beat.tremoloBar"
MALFORMED = [0, 1, -1, 0.0, 1.0, "", "true", "false", [], [True], {}, {"enabled": True},
             {"points": []}, {"points": True}, {"points": [{"position": 0, "tone": False}]},
             {"points": [{"position": 0, "tone": 0}], "unknown": True}]


def source_beats(document):
    for part in document["parts"]:
        for measure_ in part["measures"]:
            for voice in measure_["voices"]:
                yield from voice["beats"]


def music(document):
    performance = render(parse(document))
    independent = expected(read_source(document), {"offset": 0, "scale": 1})
    return performance["tracks"], performance["duration"], independent


def case(kind):
    if kind == "tied_held_bar":
        first = {**beat(duration=(1, 2)), **bar([(0, -50), (30, -100), (60, 0)])}
        document = raw_score([measure(first, beat(duration=(1, 4), tie=True), beat(duration=(1, 4), tie=True))])
        return document, list(source_beats(document))[1]
    if kind == "harmonic_chord_tie":
        first = beat(5, duration=(1, 2), harmonic="natural")
        first["notes"] += [{"string": s, "fret": 5, "harmonic": "natural"} for s in range(1, 5)]
        tied = deepcopy(first)
        for note in tied["notes"]:
            note["tie"] = True
        document = raw_score([measure(first, tied)])
        return document, list(source_beats(document))[1]
    document = raw_score([measure(beat()), measure(beat(5))])
    target = next(source_beats(document))
    if kind == "bar_vibrato":
        target["vibratoWithTremoloBar"] = "wide"
    elif kind in {"note_rest", "beat_rest"}:
        target["notes"] = [{"rest": True}]
        if kind == "beat_rest":
            target["rest"] = True
    elif kind != "plain":
        raise AssertionError(kind)
    return document, target


@pytest.mark.parametrize("kind", ["plain", "bar_vibrato", "note_rest", "beat_rest", "harmonic_chord_tie", "tied_held_bar"])
@pytest.mark.parametrize("value", [False, True])
def test_boolean_retains_exact_source_without_changing_music(tmp_path, kind, value):
    document, target = case(kind)
    baseline = music(document)
    target["tremoloBar"] = value
    original = deepcopy(document)
    assert music(document) == baseline
    result = import_json(tmp_path, document)
    assert result["sourceScore"]["document"] == original == document
    assert not any("tremoloBar" in warning for warning in result["warnings"])
    rows = [r for r in result["compatibilityReport"]["findings"]
            if r["feature"] == FEATURE and type(r.get("value")) is bool]
    assert len(rows) == 1
    assert rows[0]["value"] is value
    assert (rows[0]["category"], rows[0]["impact"], rows[0]["retained"]) == (
        "source_metadata", "source_retained", "original_source")
    if kind == "plain":
        assert result["compatibilityReport"]["status"] == "compatible"
        inventory = next(r for r in result["featureInventory"]
                         if r["scope"] == "Songsterr beat" and r["field"] == "tremoloBar")
        assert inventory["handling"] == "source" and inventory["representations"] == ["source"]
        assert all("whammy" not in n for track in result["tracks"] for n in track["notes"])


@pytest.mark.parametrize("value", [False, True])
@pytest.mark.parametrize("mapped", [False, True])
def test_tied_held_bar_is_not_reset_by_boolean(value, mapped):
    document, target = case("tied_held_bar")
    alignment = ({"mapping": "piecewise-linear", "anchors": [
        {"score": 0, "audio": .3}, {"score": 1, "audio": 1.3}, {"score": 2, "audio": 3.3}]}
        if mapped else None)
    baseline, _ = compare(document, alignment)
    target["tremoloBar"] = value
    actual, _ = compare(document, alignment)
    assert actual == baseline
    segments = actual[0]["whammy"]["segments"]
    assert len(segments) == 3
    assert [point["v"] for segment in segments[1:] for point in segment["curve"]] == [-1, -1, -1, -1]


@pytest.mark.parametrize("value", [None, False, True])
@pytest.mark.parametrize("vibrato", [None, "slight", "wide"])
def test_only_real_vibrato_supplies_expression_with_point_free_marker(value, vibrato):
    obj = {"tremoloBar": value, "vibratoWithTremoloBar": vibrato}
    assert source_whammy(obj) == source_whammy({"vibratoWithTremoloBar": vibrato})
    assert read_bar(obj, "beat") == read_bar({"vibratoWithTremoloBar": vibrato}, "beat")
    if vibrato is None:
        assert source_whammy(obj) is None
    else:
        assert source_whammy(obj) == {"curve": [], "vibrato": vibrato}


def test_absent_and_null_keep_existing_music_and_add_no_boolean_claim():
    document, target = case("plain")
    baseline = music(document)
    assert not any(r["feature"] == FEATURE for r in inspect_songsterr(document)["findings"])
    target["tremoloBar"] = None
    assert music(document) == baseline
    assert not any(r["feature"] == FEATURE and type(r.get("value")) is bool
                   for r in inspect_songsterr(document)["findings"])


@pytest.mark.parametrize("value", [False, True])
def test_legacy_negative_fret_mute_does_not_treat_boolean_as_pitch_expression(tmp_path, value):
    document = raw_score([measure(beat(-1, dead=True))])
    baseline = music(document)
    next(source_beats(document))["tremoloBar"] = value
    assert music(document) == baseline
    result = import_json(tmp_path, document)
    note, = result["tracks"][0]["notes"]
    assert note["f"] == 127 and note["mt"] is True
    assert "whammy" not in note
    report = result["compatibilityReport"]
    assert report["status"] != "blocked"
    row, = [r for r in report["findings"] if r["feature"] == FEATURE]
    assert row["value"] is value and row["impact"] == "source_retained"


@pytest.mark.parametrize("value", [False, True])
@pytest.mark.parametrize("field,expression", [
    ("vibrato", True), ("wideVibrato", True), ("vibratoWithTremoloBar", "slight"),
    ("tremoloBar", {"points": [{"position": 0, "tone": 0}, {"position": 60, "tone": 100}]})])
def test_real_inherited_pitch_expression_still_blocks_legacy_mute(value, field, expression):
    target = {**beat(-1, dead=True), "tremoloBar": value}
    target[field] = deepcopy(expression)
    document = raw_score([measure(target)])
    for reader in (parse, read_source):
        with pytest.raises(ValueError):
            reader(document)
    assert inspect_songsterr(document)["status"] == "blocked"


@pytest.mark.parametrize("malformed", MALFORMED)
def test_no_general_acceptance_of_invalid_bar_shapes(malformed):
    document, target = case("plain")
    target["tremoloBar"] = deepcopy(malformed)
    for reader in (parse, read_source):
        with pytest.raises(ValueError):
            reader(document)
    with pytest.raises(ValueError):
        source_whammy(target)
    with pytest.raises(ValueError):
        read_bar(target, "beat")


@pytest.mark.parametrize("where", ["note", "voice"])
def test_boolean_exception_does_not_apply_to_other_scopes(where):
    document, target = case("plain")
    target["tremoloBar"] = True
    if where == "note":
        target["notes"][0]["tremoloBar"] = True
    else:
        document["parts"][0]["measures"][0]["voices"][0]["tremoloBar"] = True
    for reader in (parse, read_source):
        with pytest.raises(ValueError):
            reader(document)


def excluded_document(value):
    document, _ = case("plain")
    document["tracks"].append({"id": 1, "name": "Excluded strings", "instrumentId": 48})
    # Unsupported pitches in a non-playable instrument must not gain new guards.
    document["parts"].append({"measures": [measure({**beat(-1, string=99), "tremoloBar": value}),
                                         measure(beat(-1, string=99))]})
    return document


@pytest.mark.parametrize("value", [False, True])
def test_excluded_instrument_retains_flag_without_new_pitch_rules(value):
    document = excluded_document(value)
    baseline = deepcopy(document)
    baseline["parts"][1]["measures"][0]["voices"][0]["beats"][0].pop("tremoloBar")
    assert music(document) == music(baseline)
    rows = [r for r in inspect_songsterr(document)["findings"] if r["feature"] == FEATURE]
    assert len(rows) == 1 and rows[0]["value"] is value
    assert rows[0]["trackIndex"] == 1 and rows[0]["impact"] == "source_retained"


@pytest.fixture(scope="module")
def packaged_booleans(tmp_path_factory):
    document = raw_score([measure({**beat(3, duration=(1, 4)), "tremoloBar": True},
                                 {**beat(5, duration=(1, 4)), "tremoloBar": False},
                                 {**beat(7, duration=(1, 4)), **bar([(0, 0), (0, 0), (60, -100)])},
                                 {**beat(9, duration=(1, 4)), "tremoloBar": None})])
    document["tracks"].append({"id": 1, "name": "Strings", "instrumentId": 48})
    document["parts"].append({"measures": [measure({**beat(), "tremoloBar": True})]})
    return build_package(tmp_path_factory.mktemp("boolean-bar-package"), document, contract=121)


@pytest.mark.parametrize("value", [False, True])
@pytest.mark.parametrize("fault", [None, "missing", "duplicate", "wrong_value", "numeric_value", "missing_value",
                                   "location", "retention", "impact", "old_contract", "old_inventory", "both_old",
                                   "source_loss", "excluded_missing", "invented_curve", "removed_real_curve"])
def test_archive_checks_exact_boolean_retention_and_real_expression(tmp_path, packaged_booleans, value, fault):
    original_files, original_manifest, alignment, source = packaged_booleans
    files, manifest = deepcopy(original_files), deepcopy(original_manifest)
    report = json.loads(files["import/compatibility.json"])
    row = next(r for r in report["findings"] if r["feature"] == FEATURE and r.get("value") is value)
    if fault in {"missing", "excluded_missing"}:
        if fault == "excluded_missing":
            row = next(r for r in report["findings"] if r["feature"] == FEATURE and r["trackIndex"] == 1)
        report["findings"].remove(row)
        report["findingCount"] -= 1
    elif fault == "duplicate":
        report["findings"].append(deepcopy(row))
        report["findingCount"] += 1
    elif fault == "wrong_value":
        row["value"] = not value
    elif fault == "numeric_value":
        row["value"] = int(value)
    elif fault == "missing_value":
        row.pop("value")
    elif fault == "location":
        row["location"] += "/invented"
    elif fault == "retention":
        row["retained"] = "discarded"
    elif fault == "impact":
        row["impact"] = "display_or_expression"
    elif fault == "source_loss":
        source_file = manifest["song_import"]["sourceFile"]
        retained = json.loads(files[source_file])
        next(b for b in source_beats(retained) if b.get("tremoloBar") is value).pop("tremoloBar")
        files[source_file] = json.dumps(retained).encode()
    elif fault in {"invented_curve", "removed_real_curve"}:
        chart_file = manifest["arrangements"][0]["file"]
        chart = json.loads(files[chart_file])
        notes = chart["notes"]
        authored = next(n for n in notes if "whammy" in n)
        if fault == "invented_curve":
            notes[0]["whammy"] = deepcopy(authored["whammy"])
        else:
            authored.pop("whammy")
        files[chart_file] = json.dumps(chart).encode()
    if fault in {"old_contract", "both_old"}:
        manifest["song_import"]["preservationContract"] = 120
    if fault in {"old_inventory", "both_old"}:
        report["version"] = 120
    files["manifest.yaml"] = yaml.safe_dump(manifest).encode()
    files["import/compatibility.json"] = json.dumps(report).encode()
    result = verify_files(tmp_path, files, source, alignment)
    assert result["status"] == ("failed" if fault else "passed"), result
    if fault == "source_loss":
        assert "retained_source" in {r["code"] for r in result["errors"]}
    elif fault in {"invented_curve", "removed_real_curve"}:
        assert any(r["code"].startswith("whammy_") for r in result["errors"])
    elif fault:
        assert any(r["code"].startswith("compatibility") for r in result["errors"]), result


@pytest.mark.parametrize("value", [False, True])
@pytest.mark.parametrize("fault", ["old_contract", "old_inventory", "missing_source"])
def test_builder_requires_121_and_source_for_boolean_retention(tmp_path, value, fault):
    from feedback_converter.song_import.audio import ImportFailure

    document, target = case("plain")
    target["tremoloBar"] = value
    with pytest.raises(ImportFailure, match="121"):
        build_package(tmp_path, document, contract=120 if fault == "old_contract" else 121,
                      inventory_version=120 if fault == "old_inventory" else None,
                      retain_source=fault != "missing_source")


@pytest.mark.parametrize("value", [None, False, True])
def test_historical_120_with_no_selected_boolean_remains_verifiable(tmp_path, value):
    document = excluded_document(value)
    files, manifest, alignment, source = build_package(tmp_path, document, contract=121)
    report = json.loads(files["import/compatibility.json"])
    # Old readers never interpreted bar fields on excluded instruments.
    report["findings"] = [r for r in report["findings"] if r["feature"] != FEATURE]
    report["findingCount"] = len(report["findings"])
    report["version"] = 120
    manifest["song_import"]["preservationContract"] = 120
    files["manifest.yaml"] = yaml.safe_dump(manifest).encode()
    files["import/compatibility.json"] = json.dumps(report).encode()
    result = verify_files(tmp_path, files, source, alignment)
    assert result["status"] == "passed", result


@pytest.mark.parametrize("mapped", [False, True])
def test_hybrid_retiming_preserves_boolean_metadata_and_donor_notes(tmp_path, mapped):
    from test_songsterr_hybrid_lead import build, song

    document = song()
    for index, item in enumerate(source_beats(document)):
        item["tremoloBar"] = index % 2 == 0
    source, _, _, _, archive, report = build(tmp_path, document, mapped=mapped, difficulty=True, contract=121)
    assert report["status"] == "passed", report
    with ZipFile(archive) as package:
        manifest = yaml.safe_load(package.read("manifest.yaml"))
        assert package.read(manifest["song_import"]["sourceFile"]) == source.read_bytes()
        hybrid = json.loads(package.read(manifest["arrangements"][-1]["file"]))
        assert [n["f"] for n in hybrid["notes"]] == [3, 5, 8, 7]
        assert all("whammy" not in n for n in hybrid["notes"])
