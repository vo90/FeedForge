"""Empty reference tracks are not failed playable arrangements."""
from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.diagnostics import diagnose_arrangements
from feedback_converter.song_import.model import EmptyScoreError, ScoreImportError
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from test_song_import_score import beat, measure, raw_score


def mixed_source():
    doc = raw_score([measure(beat())])
    doc["tracks"].append({**deepcopy(doc["tracks"][0]), "id": "reference", "name": "Reference"})
    doc["parts"].append(deepcopy(doc["parts"][0]))
    rest = doc["parts"][1]["measures"][0]["voices"][0]["beats"][0]
    rest.update(rest=True, notes=[{"rest": True}], chord={"text": "A"})
    return doc


def save(tmp_path, doc):
    path = tmp_path / "source.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


@pytest.mark.parametrize("kind", ["labelled_rest", "no_note_records", "renamed", "flag_false", "two_rest_voices", "overflow_rest"])
def test_validated_empty_tracks_are_neutral_without_mutating_source(tmp_path, kind):
    doc = mixed_source()
    voices = doc["parts"][1]["measures"][0]["voices"]
    if kind == "no_note_records":
        voices[0]["beats"][0]["notes"] = []
    elif kind == "renamed":
        doc["tracks"][1]["name"] = "Any name"
    elif kind == "flag_false":
        doc["tracks"][1]["isEmpty"] = False
    elif kind == "two_rest_voices":
        voices.append(deepcopy(voices[0]))
    elif kind == "overflow_rest":
        voices[0]["beats"].append({"duration": [1, 4], "notes": [{"rest": True}]})
    original = deepcopy(doc)
    score = parse(doc)
    original_score = deepcopy(score)
    reused, reparsed = inspect_songsterr(doc), inspect_songsterr(doc)
    diagnose_arrangements(doc, reused, parsed_score=score)
    diagnose_arrangements(doc, reparsed)
    assert reused == reparsed
    assert score == original_score and doc == original
    result = load_performance(save(tmp_path, doc))
    report = result["compatibilityReport"]
    assert report["status"] != "blocked"
    assert report["arrangementSummary"] == {
        "requested": 2, "scoreReady": 1, "skippedEmpty": 1, "packageVerified": False}
    skipped = report["arrangements"][1]
    assert (skipped["status"], skipped["noteCount"], skipped["blockingFeatures"]) == ("skipped_empty", 0, [])
    assert not any(f["impact"] == "blocking" for f in report["findings"])
    assert [t["id"] for t in result["tracks"]] == ["0"]
    assert any(t["id"] == "reference" and t["reason"] == "empty" for t in result["source"]["excludedTracks"])


@pytest.mark.parametrize("kind", ["lying_empty_flag", "chords_name", "second_voice", "high_fret"])
def test_real_notes_are_never_mistaken_for_an_empty_reference(tmp_path, kind):
    doc = mixed_source()
    voices = doc["parts"][1]["measures"][0]["voices"]
    if kind == "second_voice":
        voices.append({"beats": [beat(5)]})
    else:
        voices[0]["beats"] = [beat(30 if kind == "high_fret" else 5)]
    doc["tracks"][1].update(name="CHORDS" if kind == "chords_name" else "Reference", isEmpty=True)
    result = load_performance(save(tmp_path, doc))
    report = result["compatibilityReport"]
    assert report["arrangementSummary"]["skippedEmpty"] == 0
    assert report["arrangements"][1]["status"] == "score_ready"
    assert len(result["tracks"]) == 2
    if kind == "high_fret":
        assert report["arrangements"][1]["omissions"]
        assert report["omissions"]


@pytest.mark.parametrize("kind", ["invalid_duration", "invalid_label", "open_repeat", "unknown_technique", "duplicate_id", "orphan_tie"])
def test_broken_tracks_still_fail_even_when_they_look_empty(tmp_path, kind):
    doc = mixed_source()
    bar = doc["parts"][1]["measures"][0]
    rest = bar["voices"][0]["beats"][0]
    if kind == "invalid_duration":
        rest["duration"] = [0, 1]
    elif kind == "invalid_label":
        rest["chord"] = {"text": 123}
    elif kind == "open_repeat":
        for part in doc["parts"]:
            part["measures"][0]["repeatStart"] = True
    elif kind == "unknown_technique":
        rest["newTechnique"] = True
    elif kind == "duplicate_id":
        doc["tracks"][1]["id"] = doc["tracks"][0]["id"]
    elif kind == "orphan_tie":
        bar["voices"][0]["beats"] = [beat(tie=True)]
    with pytest.raises(ScoreImportError) as caught:
        load_performance(save(tmp_path, doc))
    assert not isinstance(caught.value, EmptyScoreError)
    assert caught.value.compatibility["status"] == "blocked"


def test_wholly_empty_score_still_fails(tmp_path):
    doc = mixed_source()
    doc["tracks"] = doc["tracks"][1:]
    doc["parts"] = doc["parts"][1:]
    with pytest.raises(EmptyScoreError, match="No playable notes") as caught:
        load_performance(save(tmp_path, doc))
    assert caught.value.compatibility["status"] == "blocked"


def test_nonempty_notes_excluded_by_playback_are_not_structurally_empty():
    doc = raw_score([measure(beat())])
    score = parse(doc)
    # Source-neutral renderer control: a nonempty bar excluded from traversal.
    from unittest.mock import patch
    with patch("feedback_converter.song_import.timeline.playback_order", return_value=[]):
        with pytest.raises(ScoreImportError) as caught:
            render(score)
    assert not isinstance(caught.value, EmptyScoreError)


def test_nonempty_notes_omitted_at_bar_boundary_remain_distinct(tmp_path):
    doc = mixed_source()
    doc["parts"][1]["measures"][0]["voices"][0]["beats"].append(beat(8, duration=(1, 4)))
    score = parse(doc)
    assert score.tracks[1].bars == [[]]
    assert score.tracks[1].authored_note_count == 1
    report = inspect_songsterr(doc)
    diagnose_arrangements(doc, report, parsed_score=score)
    assert report["arrangements"][1]["status"] == "blocked"
    assert report["arrangementSummary"]["skippedEmpty"] == 0
    with pytest.raises(ScoreImportError) as caught:
        load_performance(save(tmp_path, doc))
    assert not isinstance(caught.value, EmptyScoreError)


def test_real_diagnostic_blocker_stops_before_audio_and_keeps_evidence(tmp_path, monkeypatch):
    from feedback_converter.song_import import diagnostics, worker
    from feedback_converter.song_import.compatibility import add_finding
    original = diagnostics.diagnose_arrangements

    def blocked(document, report, **kwargs):
        original(document, report, **kwargs)
        report["arrangements"][0].update(status="blocked", message="Synthetic timeline failure")
        add_finding(report, feature="arrangement.timeline", category="source_interpretation",
                    impact="blocking", message="Synthetic timeline failure", location="parts/0")

    monkeypatch.setattr(diagnostics, "diagnose_arrangements", blocked)
    monkeypatch.setattr(worker, "prepare_audio", lambda *a, **k: pytest.fail("Audio must not start"))
    source = save(tmp_path, raw_score([measure(beat())]))
    result = worker.run_import({"scorePath": str(source), "workDir": str(tmp_path / "work"),
                               "outputDir": str(tmp_path / "library"), "auditDir": str(tmp_path / "evidence")})
    assert not result["ok"]
    assert not (tmp_path / "library").exists()
    record = json.loads((tmp_path / "evidence/records" / (result["evidence"]["id"] + ".json")).read_text())
    report = json.loads((tmp_path / "evidence/objects" / record["objects"]["compatibility"]).read_text())
    assert report["status"] == "blocked"
    assert report["arrangements"][0]["message"] == "Synthetic timeline failure"


@pytest.mark.parametrize("tamper", [False, True])
def test_packaged_live_track_passes_independent_check_but_corrupted_note_fails(tmp_path, tamper):
    from test_song_import_builder import inputs
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.verification import verify_import
    _, audio, _, job = inputs(tmp_path)
    source = save(tmp_path, mixed_source())
    performance = load_performance(source)
    alignment = {"status": "validated", "offset": 0, "scale": 1}
    built = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path / "out",
                         source_path=source, compatibility=performance["compatibilityReport"])
    archive = Path(built["stagingPath"])
    with ZipFile(archive) as z:
        files = {n: z.read(n) for n in z.namelist()}
    assert files["import/source.json"] == source.read_bytes()
    if tamper:
        chart = json.loads(files["arrangements/0.json"])
        chart["notes"][0]["f"] += 1
        files["arrangements/0.json"] = json.dumps(chart).encode()
        archive = tmp_path / "tampered.feedpak"
        with ZipFile(archive, "w") as z:
            for name, contents in files.items():
                z.writestr(name, contents)
    report = verify_import(source, archive, alignment)
    assert report["status"] == ("failed" if tamper else "passed"), report["errors"]
    if tamper:
        assert "note_f" in {error["code"] for error in report["errors"]}
