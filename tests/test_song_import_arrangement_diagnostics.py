from copy import deepcopy
import json

import pytest

from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.diagnostics import diagnose_arrangements
from feedback_converter.song_import.worker import run_import
from test_song_import_score import beat, measure, raw_score


def source_with_parts():
    doc = raw_score([measure(beat(pickScrape=True))])
    doc["tracks"][0]["id"] = "authored-lead"
    for name, partbeat in [("Bass", beat()), ("Rhythm", beat(tie=True))]:
        doc["tracks"].append({**deepcopy(doc["tracks"][0]), "id": "authored-" + name, "name": name})
        doc["parts"].append({"measures": [measure(partbeat)], "automations": deepcopy(doc["parts"][0]["automations"])})
    return doc


def test_all_reachable_parts_are_diagnosed_without_rewriting_ids_or_music():
    doc = source_with_parts(); before = deepcopy(doc)
    report = inspect_songsterr(doc)
    diagnose_arrangements(doc, report)
    assert [(r["trackId"], r["status"], r["stage"]) for r in report["arrangements"]] == [
        ("authored-lead", "blocked", "compatibility"), ("authored-Bass", "score_ready", "timeline"),
        ("authored-Rhythm", "blocked", "timeline")]
    assert report["arrangementSummary"] == {"requested": 3, "scoreReady": 1, "packageVerified": False}
    assert report["findings"][0]["workStatus"] == "decision_required"
    assert report["findings"][0]["decisionId"] == "D1"
    assert report["findings"][-1]["measure"] == 1
    assert doc == before


def test_global_tempo_and_meter_still_apply_to_each_diagnostic():
    doc = source_with_parts()
    doc["parts"][2]["measures"][0]["signature"] = [3, 4]
    report = inspect_songsterr(doc); diagnose_arrangements(doc, report)
    assert not any(r["status"] == "score_ready" for r in report["arrangements"])
    assert "time signature" in report["arrangements"][1]["message"]


def test_no_partial_package_is_published_and_full_source_survives(tmp_path):
    doc = source_with_parts()
    source = tmp_path / "source.json"; source.write_text(json.dumps(doc))
    result = run_import({"scorePath": str(source), "workDir": str(tmp_path / "work"),
                         "outputDir": str(tmp_path / "library"), "auditDir": str(tmp_path / "evidence")})
    assert not result["ok"]
    assert not (tmp_path / "library").exists()
    record = json.loads((tmp_path / "evidence/records" / (result["evidence"]["id"] + ".json")).read_text())
    report = json.loads((tmp_path / "evidence/objects" / record["objects"]["compatibility"]).read_text())
    assert report["publicationPolicy"] == "all_requested_arrangements"
    assert report["arrangementSummary"]["scoreReady"] == 1
    assert (tmp_path / "evidence/objects" / record["objects"]["source"]).read_bytes() == source.read_bytes()


def test_parser_failures_keep_exact_note_location():
    doc = raw_score([measure(beat(staccato="invalid"))])
    report = inspect_songsterr(doc); diagnose_arrangements(doc, report)
    failure = report["findings"][-1]
    assert failure["location"] == "parts/0/measures/0/voices/0/beats/0/notes/0"
    assert failure["measure"] == failure["beat"] == failure["note"] == 1


@pytest.mark.parametrize("kind", ["ordinary", "split_voices", "empty", "high_frets",
                                  "duplicate_ids", "voice_collision", "negative_other", "overfilled_other"])
def test_reused_parse_keeps_diagnostics_and_parsed_score_unchanged(kind):
    from feedback_converter.song_import.songsterr import parse
    from feedback_converter.song_import.voices import derived_id

    doc = raw_score([measure(beat())])
    doc["tracks"].append({**deepcopy(doc["tracks"][0]), "id": "second", "name": "Second"})
    doc["parts"].append(deepcopy(doc["parts"][0]))
    if kind in ("split_voices", "voice_collision"):
        doc["parts"][0]["measures"][0]["voices"].append({"beats": [beat(5)]})
    if kind == "empty":
        doc["parts"][0]["measures"][0]["voices"][0]["beats"][0]["notes"] = []
    if kind == "high_frets":
        doc["parts"][0]["measures"][0]["voices"][0]["beats"][0]["notes"][0]["fret"] = 30
    if kind == "duplicate_ids":
        doc["tracks"][1]["id"] = doc["tracks"][0]["id"]
    if kind == "voice_collision":
        doc["tracks"][1]["id"] = derived_id(str(doc["tracks"][0]["id"]), 0)
    if kind == "negative_other":
        doc["parts"][1]["measures"][0]["voices"][0]["beats"][0]["notes"][0].update(fret=-1, dead=True)
    if kind == "overfilled_other":
        doc["parts"][1]["measures"][0]["voices"][0]["beats"].append(beat(5))
    original = deepcopy(doc)
    score = parse(doc)
    original_score = deepcopy(score)
    baseline, reused = inspect_songsterr(doc), inspect_songsterr(doc)
    diagnose_arrangements(doc, baseline)
    diagnose_arrangements(doc, reused, parsed_score=score)
    assert reused == baseline
    assert score == original_score and doc == original


def test_load_performance_reuses_successful_parse_but_still_reports_each_part(tmp_path, monkeypatch):
    from feedback_converter.song_import import diagnostics, songsterr
    from feedback_converter.song_import.score import load_performance

    doc = raw_score([measure(beat())])
    doc["tracks"].append({**deepcopy(doc["tracks"][0]), "id": "second", "name": "Second"})
    doc["parts"].append(deepcopy(doc["parts"][0]))
    source = tmp_path / "source.json"
    source.write_text(json.dumps(doc))
    parse, calls = songsterr.parse, []

    def counted(document, **kwargs):
        calls.append(kwargs.get("track_indices"))
        return parse(document, **kwargs)

    monkeypatch.setattr(songsterr, "parse", counted)
    monkeypatch.setattr(diagnostics, "parse", counted)
    result = load_performance(source)
    assert calls == [None]
    assert [r["trackId"] for r in result["compatibilityReport"]["arrangements"]] == ["0", "second"]
    assert result["compatibilityReport"]["arrangementSummary"]["scoreReady"] == 2


def test_failed_complete_parse_keeps_valid_part_and_exact_failure_details(tmp_path):
    from feedback_converter.song_import.model import ScoreImportError
    from feedback_converter.song_import.score import load_performance

    doc = raw_score([measure(beat())])
    doc["tracks"].append({**deepcopy(doc["tracks"][0]), "id": "broken", "name": "Broken"})
    doc["parts"].append({"measures": [measure(beat(staccato="invalid"))],
                         "automations": deepcopy(doc["parts"][0]["automations"])})
    expected = inspect_songsterr(doc)
    diagnose_arrangements(doc, expected)
    source = tmp_path / "source.json"
    source.write_text(json.dumps(doc))
    with pytest.raises(ScoreImportError) as caught:
        load_performance(source)
    assert caught.value.compatibility == expected
    assert expected["arrangements"][0]["status"] == "score_ready"
    assert expected["arrangements"][1]["status"] == "blocked"


@pytest.mark.parametrize("kind", ["no_parts", "empty_notes", "duplicate_ids", "voice_collision"])
def test_complete_source_failures_remain_blocking_with_reused_diagnostics(tmp_path, kind):
    from feedback_converter.song_import.model import ScoreImportError
    from feedback_converter.song_import.score import load_performance
    from feedback_converter.song_import.voices import derived_id

    doc = raw_score([measure(beat())])
    if kind == "no_parts":
        doc.update(tracks=[], parts=[])
    elif kind == "empty_notes":
        doc["parts"][0]["measures"][0]["voices"][0]["beats"][0]["notes"] = []
    else:
        doc["tracks"].append({**deepcopy(doc["tracks"][0]), "name": "Second"})
        doc["parts"].append(deepcopy(doc["parts"][0]))
        if kind == "voice_collision":
            doc["parts"][0]["measures"][0]["voices"].append({"beats": [beat(5)]})
            doc["tracks"][1]["id"] = derived_id(str(doc["tracks"][0]["id"]), 0)
    source = tmp_path / "source.json"
    source.write_text(json.dumps(doc))
    with pytest.raises((ScoreImportError, ValueError)):
        load_performance(source)


@pytest.mark.parametrize("value", [True, "miiTriplet", "eamii"])
def test_explicit_rasgueado_patterns_remain_a_design_decision(value):
    field = "rasgueado"
    doc = raw_score([measure(beat())])
    doc["parts"][0]["measures"][0]["voices"][0]["beats"][0][field] = value
    before = deepcopy(doc)
    report = inspect_songsterr(doc)
    finding = next(f for f in report["findings"] if f["feature"] == "beat." + field)
    assert finding["impact"] == "blocking"
    assert finding["workStatus"] == "decision_required"
    assert finding["decisionId"] == "D5"
    assert doc == before
