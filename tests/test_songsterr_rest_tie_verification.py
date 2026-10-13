"""Source-bound rest-bridge receipts, notation, timing and composition regressions."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from feedback_converter.song_import import load_performance
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.verification import verify_import
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from test_song_import_builder import inputs
from test_song_import_score import beat, measure, raw_score
from test_songsterr_hybrid_lead import rest


CONTRACT = 122
FEATURE = "note.rest_tie"
POLICY = "songsterr-rest-tie-continuation-v1"
SIDECAR = "import/rest-ties.json"
IDENTITY = {"status": "validated", "offset": 0, "scale": 1}


def source(origin=7, stored=0, *, cross_bar=False):
    """A real rest interrupts one held target, then a contiguous tie extends it."""
    if cross_bar:
        return raw_score([measure(beat(origin, duration=(1, 2)), rest((1, 2))),
                          measure(beat(stored, duration=(1, 4), tie=True),
                                  beat(origin, duration=(1, 4), tie=True), rest((1, 2)))])
    return raw_score([measure(beat(origin, duration=(1, 4)), rest((1, 4)),
                              beat(stored, duration=(1, 4), tie=True),
                              beat(origin, duration=(1, 4), tie=True))])


def read_package(path):
    with ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    return files, yaml.safe_load(files["manifest.yaml"])


def verify_files(directory, files, path, alignment, **kwargs):
    target = directory / "changed.feedpak"
    with ZipFile(target, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return verify_import(path, target, alignment, **kwargs)


def package(directory, document, *, alignment=None, contract=CONTRACT,
            inventory_version=None, retain_source=True, hybrid=False):
    path = directory / "source.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    performance = load_performance(path, composition_context=hybrid)
    inventory = deepcopy(performance["compatibilityReport"])
    if inventory_version is not None:
        inventory["version"] = inventory_version
    _, audio, _, job = inputs(directory)
    alignment = deepcopy(alignment or IDENTITY)
    recipe = {"preservationContract": contract}
    hybrid_request = None
    options = None
    if hybrid:
        from feedback_converter.song_import.hybrid_lead import normalize_options
        options = normalize_options({"enabled": True, "mainTrackId": "0",
                                     "sourceSha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        recipe.update(source="songsterr", scoreHash=options["sourceSha256"],
                      audioHash=audio["hash"], hybridLead=options)
        hybrid_request = {"enabled": True, "mainTrackId": "0", "options": options}
    built = build_feedpak(performance, audio, alignment, job, output_dir=directory / "out",
                         source_path=path if retain_source else None, compatibility=inventory,
                         recipe=recipe, hybrid_lead=hybrid_request)
    archive = Path(built["stagingPath"])
    result = verify_import(path, archive, alignment, **({"hybrid_options": options} if hybrid else {}))
    assert result["status"] == "passed", result
    files, manifest = read_package(archive)
    return files, manifest, alignment, path, performance, options


@pytest.mark.parametrize("origin,stored", [(7, 0), (0, 11), (3, 3)])
@pytest.mark.parametrize("cross_bar", [False, True])
def test_independent_reconstruction_requires_source_rest_evidence(tmp_path, origin, stored, cross_bar):
    document = source(origin, stored, cross_bar=cross_bar)
    before = deepcopy(document)
    path = tmp_path / "source.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    performance = load_performance(path)
    independent = songsterr(document)
    wanted = expected(independent, IDENTITY)
    assert document == before
    assert performance["restTieEvidence"] == wanted["rest_ties"]
    rows = wanted["rest_ties"]
    assert len(rows) == 2
    assert rows[0]["bridgesRest"] is True and rows[1]["bridgesRest"] is False
    assert rows[1]["rests"] == []
    assert all(row["used"] == {"fret": origin} for row in rows)
    assert [row["authored"] for row in rows] == [{"fret": stored}, {"fret": origin}]
    assert all(row["originSourceId"] == "songsterr:0:0:0:0:0" for row in rows)
    assert all(row["originOccurrence"] == 1 for row in rows)
    assert all(row["rule"] == "plain-tie-continues-through-rests" for row in rows)
    assert rows[0]["rests"] == [{"sourceId": "songsterr:0:0:0:1",
                                  "location": "parts/0/measures/0/voices/0/beats/1",
                                  "occurrence": 1,
                                  "start": 1.0 if cross_bar else .5,
                                  "end": 2.0 if cross_bar else 1.0,
                                  "quarters": {"start": "2" if cross_bar else "1",
                                               "end": "4" if cross_bar else "2"}}]
    notes = performance["tracks"][0]["notes"]
    assert [(note["t"], note["f"], note["sus"]) for note in notes] == [
        (0.0, origin, 3.0 if cross_bar else 2.0)]
    # Archived written rhythm remains a rest, while tied pitches follow the attack.
    written = wanted["parts"][0]["notation_beats"]
    assert sum(bool(item.get("rest")) for item in written) >= 1
    tied_notes = [n for item in written for n in item["notes"] if n.get("tied")]
    assert [n["fret"] for n in tied_notes] == [origin, origin]
    with pytest.raises(ValueError):
        expected(independent, IDENTITY, rest_bridge_ties=False)


@pytest.fixture(scope="module")
def packaged_rest_tie(tmp_path_factory):
    return package(tmp_path_factory.mktemp("rest-tie-package"), source())


def test_receipt_inventory_and_original_source_are_exact(packaged_rest_tie):
    files, manifest, _, path, performance, _ = packaged_rest_tie
    receipt = json.loads(files[SIDECAR])
    assert receipt == {"version": 1, "policy": POLICY, "timeDomain": "score_seconds",
                       "sourceSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "continuations": performance["restTieEvidence"]}
    assert manifest["song_import"]["restTieFile"] == SIDECAR
    assert manifest["song_import"]["restTiePolicy"] == POLICY
    assert files[manifest["song_import"]["sourceFile"]] == path.read_bytes()
    report = json.loads(files[manifest["song_import"]["compatibilityFile"]])
    diagnostics = [row for row in report["findings"] if row["feature"] == FEATURE]
    assert len(diagnostics) == len(receipt["continuations"])
    for diagnostic, row in zip(diagnostics, receipt["continuations"]):
        assert diagnostic["location"] == row["location"] + "@visit" + str(row["occurrence"])
        assert (diagnostic["category"], diagnostic["impact"], diagnostic["retained"]) == (
            "source_interpretation", "source_retained", "original_source")
        assert diagnostic["value"] == {key: row[key] for key in (
            "authored", "used", "rule", "originSourceId", "bridgesRest")}
    assert report["status"] == "compatible"
    assert not performance["warnings"]
    assert not any(row["impact"] in {"blocking", "display_or_expression"} for row in diagnostics)


@pytest.mark.parametrize("fault", [
    "missing", "reference", "policy", "receipt_policy", "hash", "version", "time_domain",
    "extra", "count", "duplicate", "sourceId", "originSourceId", "location", "trackId",
    "occurrence", "originOccurrence", "voice", "string", "attack", "previousEnd", "start", "end",
    "authored", "used", "authored_boolean", "rule", "bridgesRest", "bridgesRest_numeric",
    "rests_missing", "rest_source", "rest_location", "rest_occurrence", "rest_start", "rest_end",
    "rest_quarters", "rest_extra", "row_extra", "old_contract", "old_inventory", "both_old",
    "source_missing", "source_loss", "inventory_missing", "inventory_duplicate", "inventory_value",
    "inventory_retention", "inventory_impact", "inventory_location", "inventory_track",
    "new_attack", "wrong_fret", "short_sustain", "notation_fret", "notation_midi", "notation_rest",
])
def test_source_bound_rest_tie_archive_rejects_corruption(tmp_path, packaged_rest_tie, fault):
    original_files, original_manifest, alignment, path, _, _ = packaged_rest_tie
    files, manifest = deepcopy(original_files), deepcopy(original_manifest)
    receipt = json.loads(files[SIDECAR])
    row = receipt["continuations"][0]
    report_path = manifest["song_import"]["compatibilityFile"]
    report = json.loads(files[report_path])
    diagnostic = next(item for item in report["findings"] if item["feature"] == FEATURE)
    if fault in {"sourceId", "originSourceId", "location", "trackId", "rule"}:
        row[fault] += "-invented"
    elif fault in {"occurrence", "originOccurrence", "voice", "string"}:
        row[fault] += 1
    elif fault in {"attack", "previousEnd", "start", "end"}:
        row[fault] += .01
    elif fault in {"authored", "used"}:
        row[fault]["fret"] += 1
    elif fault == "authored_boolean":
        row["authored"]["fret"] = False
    elif fault == "bridgesRest":
        row["bridgesRest"] = False
    elif fault == "bridgesRest_numeric":
        row["bridgesRest"] = 1
    elif fault == "rests_missing":
        row["rests"] = []
    elif fault.startswith("rest_"):
        field = fault.removeprefix("rest_")
        selected = row["rests"][0]
        if field in {"source", "location"}:
            selected["sourceId" if field == "source" else field] += "-invented"
        elif field in {"occurrence", "start", "end"}:
            selected[field] += 1 if field == "occurrence" else .01
        elif field == "quarters":
            selected["quarters"]["end"] = "3"
        elif field == "extra":
            selected["unexpected"] = True
    elif fault == "row_extra":
        row["unexpected"] = True
    elif fault == "hash":
        receipt["sourceSha256"] = "0" * 64
    elif fault == "version":
        receipt["version"] = True
    elif fault == "time_domain":
        receipt["timeDomain"] = "audio_seconds"
    elif fault == "receipt_policy":
        receipt["policy"] = "invented"
    elif fault == "extra":
        receipt["unexpected"] = True
    elif fault == "count":
        receipt["continuations"] = []
    elif fault == "duplicate":
        receipt["continuations"].append(deepcopy(row))
    elif fault == "inventory_missing":
        report["findings"].remove(diagnostic)
        report["findingCount"] -= 1
    elif fault == "inventory_duplicate":
        report["findings"].append(deepcopy(diagnostic))
        report["findingCount"] += 1
    elif fault == "inventory_value":
        diagnostic["value"]["bridgesRest"] = 1
    elif fault == "inventory_retention":
        diagnostic["retained"] = "discarded"
    elif fault == "inventory_impact":
        diagnostic["impact"] = "display_or_expression"
    elif fault == "inventory_location":
        diagnostic["location"] += "-invented"
    elif fault == "inventory_track":
        diagnostic["trackId"] = "invented"
    elif fault == "source_missing":
        files.pop(manifest["song_import"]["sourceFile"])
    elif fault == "source_loss":
        source_path = manifest["song_import"]["sourceFile"]
        document = json.loads(files[source_path])
        document["parts"][0]["measures"][0]["voices"][0]["beats"][1]["rest"] = False
        files[source_path] = json.dumps(document).encode()
    if fault in {"old_contract", "both_old"}:
        manifest["song_import"]["preservationContract"] = CONTRACT - 1
    if fault in {"old_inventory", "both_old"}:
        report["version"] = CONTRACT - 1
    if fault == "reference":
        manifest["song_import"].pop("restTieFile")
    if fault == "policy":
        manifest["song_import"]["restTiePolicy"] = "wrong"
    if fault in {"new_attack", "wrong_fret", "short_sustain"}:
        from feedback_converter.chart_guidance import finalize
        chart_path = manifest["arrangements"][0]["file"]
        chart = json.loads(files[chart_path])
        if fault == "new_attack":
            chart["notes"].append({**deepcopy(chart["notes"][0]), "t": 1.0, "sus": .5})
        elif fault == "wrong_fret":
            chart["notes"][0]["f"] = 0
        else:
            chart["notes"][0]["sus"] = .5
        finalize(chart, regenerate=True)
        files[chart_path] = json.dumps(chart).encode()
    if fault.startswith("notation_"):
        notation_path = manifest["arrangements"][0]["notation"]
        notation = json.loads(files[notation_path])
        beats = notation["measures"][0]["staves"]["staff"]["voices"][0]["beats"]
        if fault == "notation_rest":
            beats[1]["rest"] = False
        else:
            beats[2]["notes"][0]["fret" if fault == "notation_fret" else "midi"] = 0
        files[notation_path] = json.dumps(notation).encode()
    files[SIDECAR] = json.dumps(receipt).encode()
    if fault == "missing":
        files.pop(SIDECAR)
    files[report_path] = json.dumps(report).encode()
    files["manifest.yaml"] = yaml.safe_dump(manifest).encode()
    result = verify_files(tmp_path, files, path, alignment)
    # A historical reader rejects this previously unsupported source before
    # archive comparison; the downgraded package must never be accepted.
    assert result["status"] in ({"failed", "unsupported"} if fault in {"old_contract", "both_old"}
                                else {"failed"}), (fault, result)


@pytest.mark.parametrize("fault", ["old_contract", "old_inventory", "missing_source"])
def test_builder_requires_contract_122_inventory_and_source(tmp_path, fault):
    # The shared original-source guard can reject before the rest-specific gate.
    with pytest.raises(ImportFailure, match="original tab" if fault == "missing_source" else "122"):
        package(tmp_path, source(), contract=121 if fault == "old_contract" else 122,
                inventory_version=121 if fault == "old_inventory" else None,
                retain_source=fault != "missing_source")


def test_archive_verifier_does_not_reuse_producer_qualification(tmp_path, packaged_rest_tie, monkeypatch):
    from feedback_converter.song_import import rest_ties
    files, _, alignment, path, _, _ = packaged_rest_tie
    def forbidden(*args, **kwargs):
        raise AssertionError("Independent verifier reused producer rest-tie qualification.")
    monkeypatch.setattr(rest_ties, "admitted", forbidden)
    monkeypatch.setattr(rest_ties, "record", forbidden)
    result = verify_files(tmp_path, files, path, alignment)
    assert result["status"] == "passed", result


@pytest.mark.parametrize("mapped", [False, True])
def test_mapped_package_retimes_music_but_receipt_stays_in_score_seconds(tmp_path, mapped):
    alignment = ({"status": "validated", "mapping": "piecewise-linear", "anchors": [
        {"score": 0, "audio": .25}, {"score": 1, "audio": 1.25}, {"score": 4, "audio": 4.85}],
        "tempos": [{"time": .25, "bpm": 120}, {"time": 1.25, "bpm": 100}]}
        if mapped else {"status": "validated", "offset": .4, "scale": 1.2})
    files, manifest, _, _, performance, _ = package(tmp_path, source(cross_bar=True), alignment=alignment)
    receipt = json.loads(files[SIDECAR])
    assert receipt["continuations"] == performance["restTieEvidence"]
    assert receipt["continuations"][0]["start"] == 2.0
    chart = json.loads(files[manifest["arrangements"][0]["file"]])
    assert len(chart["notes"]) == 1
    note = chart["notes"][0]
    assert note["t"] == (.25 if mapped else .4)
    assert note["sus"] == pytest.approx(3.4 if mapped else 3.6)


def test_voice_projection_keeps_each_bridge_bound_to_its_own_origin(tmp_path):
    document = source()
    other = deepcopy(document["parts"][0]["measures"][0]["voices"][0])
    other["beats"][0]["notes"][0]["fret"] = 9
    other["beats"][3]["notes"][0]["fret"] = 9
    document["parts"][0]["measures"][0]["voices"].append(other)
    files, manifest, _, _, performance, _ = package(tmp_path, document)
    assert len(manifest["arrangements"]) == 2
    rows = json.loads(files[SIDECAR])["continuations"]
    tracks = {track["id"]: track for track in performance["tracks"]}
    assert {row["trackId"] for row in rows} == set(tracks)
    assert {row["voice"] for row in rows} == {0, 1}
    for row in rows:
        assert row["used"]["fret"] == tracks[row["trackId"]]["notes"][0]["f"]
        assert row["originSourceId"] == f"songsterr:0:0:{row['voice']}:0:0"
    reference = expected(songsterr(document), IDENTITY)
    assert rows == reference["rest_ties"] == performance["restTieEvidence"]


def test_repeat_visits_keep_distinct_rest_and_origin_occurrences(tmp_path):
    document = source()
    document["parts"][0]["measures"][0].update(repeatStart=True, repeat=2)
    files, manifest, _, _, performance, _ = package(tmp_path, document)
    rows = json.loads(files[SIDECAR])["continuations"]
    assert [(row["occurrence"], row["originOccurrence"], row["start"]) for row in rows] == [
        (1, 1, 1.), (1, 1, 1.5), (2, 2, 3.), (2, 2, 3.5)]
    assert [row["rests"][0]["occurrence"] for row in rows if row["bridgesRest"]] == [1, 2]
    assert len({row["sourceId"] for row in rows}) == 2
    assert rows == performance["restTieEvidence"]
    chart = json.loads(files[manifest["arrangements"][0]["file"]])
    assert [(n["t"], n["sus"]) for n in chart["notes"]] == [(0., 2.), (2., 2.)]


def test_hybrid_does_not_fill_written_rest_under_extended_sustain(tmp_path):
    document = raw_score([measure(beat(7)), measure(rest()),
                          measure(beat(0, tie=True)), measure(rest())])
    document["tracks"].append({**deepcopy(document["tracks"][0]), "id": 1, "name": "Rhythm Guitar"})
    document["parts"].append({"measures": [measure(rest()),
        measure(beat(5, duration=(1, 2)), rest((1, 2))), measure(rest()),
        measure(rest((1, 4)), beat(9, duration=(1, 2)), rest((1, 4)))]})
    files, manifest, _, _, performance, _ = package(tmp_path, document, hybrid=True)
    main = performance["tracks"][0]["notes"]
    assert [(n["t"], n["f"], n["sus"]) for n in main] == [(0., 7, 6.)]
    hybrid = json.loads(files[manifest["arrangements"][-1]["file"]])
    assert [(n["t"], n["f"]) for n in hybrid["notes"]] == [(0., 7), (6.5, 9)]
    assert hybrid["notes"][0]["sus"] == 6.
    receipt = json.loads(files["import/hybrid-lead.json"])
    assert any(row["start"] == 4. and row["reason"] == "crosses_primary_material"
               for row in receipt["candidateDecisions"])
    assert all(passage["start"] >= 12 for passage in receipt["passages"])


@pytest.mark.parametrize("plain_identity", [False, True])
def test_historical_121_without_rest_bridge_keeps_old_reconstruction(tmp_path, plain_identity):
    document = raw_score([measure(beat(7, duration=(1, 2)),
                                  beat(0 if plain_identity else 7, duration=(1, 2), tie=True))])
    files, manifest, alignment, path, _, _ = package(tmp_path, document, contract=121, inventory_version=121)
    assert SIDECAR not in files
    assert "restTieFile" not in manifest["song_import"]
    assert verify_files(tmp_path, files, path, alignment)["status"] == "passed"


@pytest.mark.parametrize("referenced", [False, True])
def test_extra_rest_receipt_is_not_ignored_for_ordinary_song(tmp_path, referenced):
    document = raw_score([measure(beat())])
    files, manifest, alignment, path, _, _ = package(tmp_path, document)
    assert SIDECAR not in files
    files[SIDECAR] = json.dumps({"version": 1, "policy": POLICY, "timeDomain": "score_seconds",
                                 "sourceSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                 "continuations": []}).encode()
    if referenced:
        manifest["song_import"].update(restTieFile=SIDECAR, restTiePolicy=POLICY)
        files["manifest.yaml"] = yaml.safe_dump(manifest).encode()
    result = verify_files(tmp_path, files, path, alignment)
    assert result["status"] == "failed", result
