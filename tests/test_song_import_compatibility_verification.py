from copy import deepcopy

import pytest

from test_song_import_verification import example, verify


def reported_fixture():
    source, package = example()
    source["parts"][0]["automations"]["tempo"][0]["text"] = "Moderate"
    package["manifest.yaml"]["song_import"] = {"preservationContract": 9, "sourceFile": "import/source.json",
                                              "compatibilityFile": "import/compatibility.json"}
    package["import/source.json"] = source
    package["import/compatibility.json"] = {"version": 9, "source": {"songId": "12", "revisionId": "34"},
        "target": {"feedpak": "1.16.0", "notation": 1}, "status": "limitations", "findingCount": 1, "truncated": False,
        "findings": [{"feature": "tempo.text", "location": "parts/0/automations/tempo/0/text", "value": "Moderate",
                      "valueTruncated": False, "retained": "original_source", "impact": "display_or_expression", "category": "game_limitation"}]}
    return source, package


@pytest.mark.parametrize("fault", [None, "remove", "value", "status", "identity", "retention"])
def test_report_must_account_for_original_limitation_without_changing_other_music(tmp_path, fault):
    source, package = reported_fixture()
    report = package["import/compatibility.json"]
    if fault == "remove": report["findings"] = []; report.update(findingCount=0, status="compatible")
    elif fault == "value": report["findings"][0]["value"] = "Allegro"
    elif fault == "status": report["status"] = "compatible"
    elif fault == "identity": report["source"]["revisionId"] = "35"
    elif fault == "retention": report["findings"][0]["retained"] = "discarded"
    result = verify(tmp_path, source, package)
    assert result["status"] == ("failed" if fault else "passed"), result
    if fault:
        assert any(e["code"].startswith("compatibility") for e in result["errors"])


def test_a_reported_limitation_cannot_excuse_an_unrelated_wrong_note(tmp_path):
    source, package = reported_fixture()
    package["chart.json"]["notes"][0]["f"] = 10
    result = verify(tmp_path, source, package)
    assert result["status"] == "failed"
    assert "note_f" in {e["code"] for e in result["errors"]}


def test_unrepresentable_notation_can_preserve_verified_playable_notes(tmp_path):
    source, package = reported_fixture()
    first = source["parts"][0]["measures"][0]["voices"][0]["beats"][0]
    first.update(type=64, duration=[1, 64])
    # Keep every following beat at its new explicit position; the bar stays 4/4.
    notes = package["chart.json"]["notes"]
    notes[0]["sus"] = .03125
    for n in notes[1:]: n["t"] -= .46875
    del package["manifest.yaml"]["arrangements"][0]["notation"]
    del package["notation.json"]
    report = package["import/compatibility.json"]
    report["findingCount"] = 2
    report["findings"].append({"feature": "notation.written_rhythm", "location": "tracks/g", "value": None,
        "valueTruncated": False, "retained": "original_source", "impact": "display_or_expression", "category": "game_limitation"})
    result = verify(tmp_path, source, package)
    assert result["status"] == "passed", result
    notes[0]["sus"] = .5
    assert "note_sustain" in {e["code"] for e in verify(tmp_path, source, package)["errors"]}
