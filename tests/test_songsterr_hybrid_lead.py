from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, measure, raw_score
from test_song_import_builder import inputs
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.hybrid_lead import choose_main, normalize_options, occupied, plan
from feedback_converter.song_import.hybrid_context import Clock
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.verification import verify_import


def rest(duration=(1, 1)):
    notation = {"type": duration[1] // 2, "dots": 1} if duration[0] == 3 else {}
    return {"duration": list(duration), "rest": True, "notes": [], **notation}


def song():
    doc = raw_score([measure(beat(3, duration=(1, 4)), rest((3, 4))), measure(rest()), measure(rest()),
                     measure(rest((3, 4)), beat(7, duration=(1, 4)))])
    for ident, name, bars in [(1, "Clean Guitar", [measure(rest()), measure(beat(5, duration=(1, 2)), rest((1, 2))), measure(rest()), measure(rest())]),
                              (2, "Rhythm Guitar", [measure(rest()), measure(rest()), measure(beat(8, duration=(1, 2)), rest((1, 2))), measure(rest())])]:
        doc["tracks"].append({**deepcopy(doc["tracks"][0]), "id": ident, "name": name})
        doc["parts"].append({"measures": bars})
    return doc


def prepared(tmp_path, doc=None, overrides=None):
    source = tmp_path / "score.json"
    source.write_text(json.dumps(doc or song()), encoding="utf-8")
    performance = load_performance(source, composition_context=True)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    options = normalize_options({"enabled": True, **(overrides or {})})
    main = choose_main(performance, options, source_hash)
    options.update(mainTrackId=main, sourceSha256=source_hash)
    return source, performance, options


def build(tmp_path, doc=None, enabled=True, mapped=False, difficulty=False, overrides=None):
    source, p, options = prepared(tmp_path, doc, overrides)
    _, audio, _, directory = inputs(tmp_path)
    alignment = {"status": "validated", "offset": 0, "scale": 1}
    if mapped:
        alignment = {"status": "validated", "mapping": "piecewise-linear", "anchors": [
            {"score": 0, "audio": 0}, {"score": 2, "audio": 1.5}, {"score": 8, "audio": 8}],
            "tempos": [{"time": 0, "bpm": 160}, {"time": 1.5, "bpm": 120 / (6.5 / 6)}]}
    recipe = {"preservationContract": 33, "source": "songsterr", "scoreHash": options["sourceSha256"], "audioHash": audio["hash"]}
    if enabled:
        recipe["hybridLead"] = options
    result = build_feedpak(p, audio, alignment, directory, output_dir=tmp_path / "out", recipe=recipe,
                          source_path=source, compatibility=p["compatibilityReport"], output_settings={"generateDifficulty": difficulty},
                          hybrid_lead={"enabled": enabled, "mainTrackId": options["mainTrackId"], "options": options})
    archive = Path(result["stagingPath"])
    report = verify_import(source, archive, alignment, hybrid_options=options if enabled else {"enabled": False})
    return source, p, options, alignment, archive, report


@pytest.mark.parametrize("mapped", [False, True])
@pytest.mark.parametrize("difficulty", [False, True])
def test_multi_donor_preserves_original_payloads_and_main(tmp_path, mapped, difficulty):
    on, off = tmp_path / "on", tmp_path / "off"
    on.mkdir(); off.mkdir()
    *_, archive, report = build(on, mapped=mapped, difficulty=difficulty)
    assert report["status"] == "passed", report
    *_, baseline, baseline_report = build(off, enabled=False, mapped=mapped, difficulty=difficulty)
    assert baseline_report["status"] == "passed", baseline_report
    with ZipFile(archive) as z, ZipFile(baseline) as original:
        m = yaml.safe_load(z.read("manifest.yaml"))
        assert len(m["arrangements"]) == 4
        for name in original.namelist():
            if name != "manifest.yaml" and not name.startswith("audio/"):
                assert z.read(name) == original.read(name), name
        r = json.loads(z.read("import/hybrid-lead.json"))
        assert {p["trackId"] for p in r["passages"]} == {"1", "2"}
        chart = json.loads(z.read(m["arrangements"][-1]["file"]))
        assert m["arrangements"][-1].get("notation"), "Supported source notation must be composed."
        main = json.loads(z.read(m["arrangements"][0]["file"]))
        assert all(n in chart["notes"] for n in main["notes"])
        assert chart["name"] == "Hybrid Lead"


def test_single_guitar_creates_identical_arrangement(tmp_path):
    doc = song(); doc["tracks"] = doc["tracks"][:1]; doc["parts"] = doc["parts"][:1]
    *_, archive, report = build(tmp_path, doc)
    assert report["status"] == "passed", report
    with ZipFile(archive) as z:
        m = yaml.safe_load(z.read("manifest.yaml"))
        a, b = [json.loads(z.read(row["file"])) for row in m["arrangements"]]
        b["name"] = a["name"]
        assert a == b
        assert m["song_import"]["hybridLeadResult"]["status"] == "no_additions"


def test_ambiguous_choice_is_hash_bound_and_bass_is_not_a_main(tmp_path):
    source, p, options = prepared(tmp_path)
    p["tracks"][1]["role"] = "lead"
    with pytest.raises(ImportFailure) as error:
        choose_main(p, {"enabled": True}, options["sourceSha256"])
    assert error.value.code == "awaiting_main_choice"
    assert len(error.value.diagnostics["tracks"]) == 3
    assert choose_main(p, options, options["sourceSha256"]) == "0"
    with pytest.raises(ImportFailure, match="changed"):
        choose_main(p, options, "different-source")
    for track in p["tracks"]:
        track["instrument"] = "bass"
    assert choose_main(p, {"enabled": True}, options["sourceSha256"]) is None


def test_written_staccato_and_omitted_frets_protect_main(tmp_path):
    doc = song()
    doc["parts"][0]["measures"][1] = measure(beat(30, staccato=True))
    source, p, options = prepared(tmp_path, doc)
    intervals = occupied(p["tracks"][0], p["compositionContext"]["tracks"]["0"], Clock(p["scoreTimeline"]))
    assert any(a <= 4 and b >= 8 for a, b in intervals)
    package = tmp_path / "package"
    package.mkdir()
    *_, archive, report = build(package, doc)
    assert report["status"] == "passed", report
    with ZipFile(archive) as z:
        receipt = json.loads(z.read("import/hybrid-lead.json"))
        assert all(p["trackId"] != "1" for p in receipt["passages"])


def test_incompatible_and_excluded_sources_are_not_used(tmp_path):
    source, p, options = prepared(tmp_path)
    p["tracks"][1]["capo"] = 2
    options["excludedTrackIds"] = ["2"]
    r = plan(p, options, "0", {"status": "validated", "offset": 0, "scale": 1}, 8)
    assert r["status"] == "no_additions"
    assert {x["reason"] for x in r["excluded"]} == {"incompatible_setup", "excluded_by_user"}


@pytest.mark.parametrize("technique", ["strum", "chord", "whammy", "tie_harmonic", "slide", "trill", "grace", "voices"])
def test_borrowed_source_groups_keep_techniques_and_lineage(tmp_path, technique):
    doc = song()
    donor = doc["parts"][1]["measures"][1]
    b = donor["voices"][0]["beats"][0]
    if technique in {"strum", "chord"}:
        b["notes"].extend([{"string": 1, "fret": 7}, {"string": 2, "fret": 0}])
        if technique == "strum": b["brushStroke"] = {"direction": "down", "duration": 30, "shift": 100}
    if technique == "whammy": b["tremoloBar"] = {"points": [{"position": 0, "tone": 0}, {"position": 60, "tone": -100}]}
    if technique == "tie_harmonic":
        donor["voices"][0]["beats"] = [beat(5, duration=(1, 4)), beat(5, duration=(1, 4), tie=True, harmonic="artificial", harmonicFret=7), rest((1, 2))]
    if technique == "slide":
        donor["voices"][0]["beats"] = [beat(5, duration=(1, 4), slide="legato"), beat(7, duration=(1, 4)), rest((1, 2))]
    if technique == "trill": b["notes"][0]["trill"] = {"auxiliaryFret": 7, "speed": 60}
    if technique == "grace":
        donor["voices"][0]["beats"] = [{**beat(3, duration=(1, 16)), "graceNote": "onBeat"}, beat(5, duration=(1, 2)), rest((1, 2))]
    if technique == "voices":
        donor["voices"].append({"beats": [beat(9, duration=(1, 2)), rest((1, 2))]})
    *_, archive, report = build(tmp_path, doc, mapped=True)
    assert report["status"] == "passed", report
    with ZipFile(archive) as z:
        receipt = json.loads(z.read("import/hybrid-lead.json"))
        assert receipt["passages"]
        assert any(s["sourceTrackId"] == "1" for s in receipt["sources"])


def test_bass_only_is_not_applicable_and_retains_original(tmp_path):
    doc = raw_score([measure(beat(3))], tuning=[43, 38, 33, 28])
    doc["tracks"][0].update(instrumentId=34, name="Bass")
    *_, archive, report = build(tmp_path, doc)
    assert report["status"] == "passed", report
    with ZipFile(archive) as z:
        manifest = yaml.safe_load(z.read("manifest.yaml"))
        assert len(manifest["arrangements"]) == 1
        assert "import/hybrid-lead.json" not in z.namelist()
        assert manifest["song_import"]["hybridLeadResult"]["status"] == "not_applicable"


def test_donor_internal_rests_cannot_be_filled_by_another_donor(tmp_path):
    doc = song()
    doc["parts"][1]["measures"][1] = measure(beat(5, duration=(1, 4)), rest((1, 8)), beat(5, duration=(1, 4)), rest((3, 8)))
    doc["parts"][2]["measures"][1] = measure(rest((1, 4)), beat(8, duration=(1, 8)), rest((5, 8)))
    source, p, options = prepared(tmp_path, doc)
    r = plan(p, options, "0", {"status": "validated", "offset": 0, "scale": 1}, 8)
    assert any(x["trackId"] == "1" and x["start"] == 4 and x["end"] == 6.5 for x in r["passages"])
    assert not any(x["trackId"] == "2" and x["start"] == 5 for x in r["passages"])


def test_explicit_source_priority_and_repeatable_planning(tmp_path):
    doc = song()
    doc["parts"][1]["measures"][1] = measure(beat(5, duration=(3, 4)), rest((1, 4)))
    doc["parts"][2]["measures"][1] = measure(beat(8, duration=(1, 4)), rest((3, 4)))
    source, p, options = prepared(tmp_path, doc)
    alignment = {"status": "validated", "offset": 0, "scale": 1}
    normal = plan(p, options, "0", alignment, 8)
    assert normal["passages"][0]["trackId"] == "1"
    options["preferredTrackIds"] = ["2", "1"]
    preferred = plan(p, options, "0", alignment, 8)
    assert preferred["passages"][0]["trackId"] == "2"
    assert preferred == plan(p, options, "0", alignment, 8)


def test_phrase_that_crosses_main_cannot_be_cropped(tmp_path):
    doc = song()
    doc["parts"][0]["measures"][1] = measure(rest((1, 4)), beat(12, duration=(1, 4)), rest((1, 2)))
    source, p, options = prepared(tmp_path, doc)
    result = plan(p, options, "0", {"status": "validated", "offset": 0, "scale": 1}, 8)
    assert not any(p["trackId"] == "1" for p in result["passages"])


def test_worker_asks_for_main_before_audio_preparation(tmp_path, monkeypatch):
    from feedback_converter.song_import.worker import run_import
    import feedback_converter.song_import.worker as worker
    doc = song(); doc["tracks"][1]["name"] = "Other Lead Guitar"
    source = tmp_path / "source.json"; source.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(worker, "prepare_audio", lambda *a, **kw: pytest.fail("Audio was prepared before source selection"))
    result = run_import({"scorePath": str(source), "workDir": str(tmp_path / "work"), "outputDir": str(tmp_path / "out"), "hybridLead": {"enabled": True}})
    assert result["code"] == "awaiting_main_choice"
    assert len(result["hybridChoice"]["tracks"]) == 3
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("fault", ["main", "extra", "hash", "source", "tail", "disabled", "overlap", "boundary", "lineage", "occurrence", "notation_voice"])
def test_mutated_derived_chart_or_receipt_is_rejected(tmp_path, fault):
    source, _, options, alignment, archive, report = build(tmp_path)
    assert report["status"] == "passed", report
    with ZipFile(archive) as z:
        files = {name: z.read(name) for name in z.namelist()}
    m = yaml.safe_load(files["manifest.yaml"])
    r = json.loads(files["import/hybrid-lead.json"])
    name = m["arrangements"][-1]["file"]
    chart = json.loads(files[name])
    if fault == "main": chart["notes"][0]["f"] += 1
    if fault == "extra": m["arrangements"].append(deepcopy(m["arrangements"][-1]))
    if fault == "hash": r["chartSha256"] = "0" * 64
    if fault == "source": r["sources"][0]["sourceTrackId"] = "2"
    if fault == "tail": chart["notes"][1]["sus"] = 99
    if fault == "disabled": m["song_import"].pop("hybridLead")
    if fault == "overlap": r["passages"][0]["start"] = 0
    if fault == "boundary": r["passages"][0]["boundaries"] = ["barline", "barline"]
    if fault == "lineage": r["passages"][0]["events"][0]["sourceIds"] = ["songsterr:0:0:0:0:0"]
    if fault == "occurrence": r["passages"][0]["events"][0]["occurrences"] = [1]
    if fault == "notation_voice":
        notation_name = m["arrangements"][-1]["notation"]
        notation = json.loads(files[notation_name])
        notation["measures"][0]["staves"]["staff"]["voices"][0]["v"] = 99
        files[notation_name] = json.dumps(notation).encode()
        r["notationSha256"] = hashlib.sha256(files[notation_name]).hexdigest()
    files[name] = json.dumps(chart, ensure_ascii=False, separators=(",", ":")).encode()
    if fault != "hash": r["chartSha256"] = hashlib.sha256(files[name]).hexdigest()
    files["import/hybrid-lead.json"] = json.dumps(r).encode()
    files["manifest.yaml"] = yaml.safe_dump(m).encode()
    target = tmp_path / "tampered.feedpak"
    with ZipFile(target, "w") as z:
        for name, data in files.items(): z.writestr(name, data)
    assert verify_import(source, target, alignment, hybrid_options=options)["status"] == "failed"
