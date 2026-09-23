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
    doc = raw_score([measure(beat(staccato=True, tie=True))])
    report = inspect_songsterr(doc); diagnose_arrangements(doc, report)
    failure = report["findings"][-1]
    assert failure["location"] == "parts/0/measures/0/voices/0/beats/0/notes/0"
    assert failure["measure"] == failure["beat"] == failure["note"] == 1


@pytest.mark.parametrize("field", ["rasgueado", "hasRasgueado"])
def test_authored_rasgueado_spellings_remain_a_design_decision(field):
    doc = raw_score([measure(beat())])
    doc["parts"][0]["measures"][0]["voices"][0]["beats"][0][field] = True
    before = deepcopy(doc)
    report = inspect_songsterr(doc)
    finding = next(f for f in report["findings"] if f["feature"] == "beat." + field)
    assert finding["impact"] == "blocking"
    assert finding["workStatus"] == "decision_required"
    assert finding["decisionId"] == "D5"
    assert doc == before
