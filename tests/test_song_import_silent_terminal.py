from test_song_import_verification import example, verify


def test_silent_final_measure_retains_notation_without_extending_audio_or_notes(tmp_path):
    source, package = example()
    source["parts"][0]["measures"].append({"signature": [4, 4], "voices": [{"beats": [
        {"rest": True, "type": 1, "duration": [1, 1], "notes": [{"rest": True}]}]}]})
    package["manifest.yaml"]["duration"] = 3.25
    package["timeline.json"]["beats"].append({"time": 3, "measure": 2})
    package["chart.json"]["beats"].append({"time": 3, "measure": 2})
    package["notation.json"]["measures"].append({"idx": 2, "source_measure": 2, "t": 3,
        "ts": [4, 4], "tempo": 120, "written_tempo": 120, "duration_seconds": 2,
        "staves": {"staff": {"voices": [{"v": 0, "beats": [
            {"t": 3, "duration_seconds": 2, "beat_pos": [0, 1], "dur": 1, "rest": True}]}]}}})
    alignment = {"mapping": "piecewise-linear", "anchors": [
        {"score": 0, "audio": 1}, {"score": 2, "audio": 3}, {"score": 4, "audio": 5}],
        "provenance": {"songId": "12", "revisionId": "34", "terminalBoundary": "songsterr-last-interval",
                       "terminalBeyondAudio": "silent_notation_only"}}
    result = verify(tmp_path, source, package, alignment)
    assert result["status"] == "passed", result
    assert result["counts"]["expectedNotes"] == 4
    package["chart.json"]["notes"][-1]["sus"] = 1
    assert "note_audio_bounds" in {e["code"] for e in verify(tmp_path, source, package, alignment)["errors"]}
