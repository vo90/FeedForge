from copy import deepcopy
import json
import zipfile

from feedback_converter.difficulty import ensure_difficulty
from feedback_converter.songsterr import write_feedpak


def chart():
    return {
        "name": "Bass", "tuning": [0, 0, 0, 0], "capo": 0,
        "notes": [{"t": i * 0.25 + 1, "s": 0, "f": i % 4, "sus": 0.2}
                  for i in range(32)],
        "chords": [], "templates": [], "handshapes": [],
        "anchors": [{"time": 0, "fret": 1, "width": 4}],
        "stats": {"events": 32, "notes": 32},
    }


def test_ladders_are_nested_cover_full_chart_and_preserve_authored_data():
    arr = chart()
    original = deepcopy(arr)
    beats = [{"time": i * 0.5, "measure": i // 4 + 1 if i % 4 == 0 else -1}
             for i in range(20)]
    assert ensure_difficulty(arr, beats=beats, duration=10)
    full = []
    for phrase in arr["phrases"]:
        previous = set()
        for level in phrase["levels"]:
            times = {n["t"] for n in level["notes"]}
            assert previous <= times
            assert all(phrase["start_time"] <= t < phrase["end_time"] for t in times)
            previous = times
        full.extend(phrase["levels"][-1]["notes"])
    assert full == original["notes"]
    assert {k: arr[k] for k in original} == original
    authored = deepcopy(arr)
    assert not ensure_difficulty(arr)
    assert arr == authored


def test_single_level_phrases_replaced_chords_atomic_and_drums_untouched():
    arr = chart()
    arr["phrases"] = [{"levels": [{"difficulty": 0}]}]
    chord = {"t": 2, "id": 0, "notes": [{"s": 1, "f": 3, "sus": 1}]}
    arr["chords"] = [chord]
    assert ensure_difficulty(arr)
    for phrase in arr["phrases"]:
        for level in phrase["levels"]:
            if any(n["t"] == 2 for n in level["notes"]):
                assert chord in level["chords"]
    assert not ensure_difficulty({"type": "drums", "hits": [{"t": 1}]})
    assert not ensure_difficulty({"notes": [], "chords": []})


def test_songsterr_export_generates_aligned_levels_without_mutating_preview(tmp_path):
    arr = chart()
    original = deepcopy(arr)
    audio = tmp_path / "audio.ogg"
    audio.write_bytes(b"OggS-test")
    timeline = {"version": 1, "duration": 12, "tempos": [], "time_signatures": [],
                "beats": [{"time": i, "measure": i // 4 + 1 if i % 4 == 0 else -1}
                          for i in range(12)], "sections": []}
    output = tmp_path / "dd.feedpak"
    write_feedpak([arr], timeline, audio, output, title="DD", artist="Test", offset=2,
                  generate_difficulty=True)
    with zipfile.ZipFile(output) as archive:
        wire = json.loads(archive.read("arrangements/bass.json"))
    assert arr == original
    full = [n for p in wire["phrases"] for n in p["levels"][-1]["notes"]]
    assert full == wire["notes"]
    assert full[0]["t"] == 3
    assert any(len(p["levels"]) == 4 for p in wire["phrases"])
    assert wire["ext"]["generatedDifficulty"]["sourceAuthored"] is False


def test_editor_export_keeps_single_level_phrases_unless_requested(tmp_path):
    arr = chart()
    arr["phrases"] = [{"start_time": 0, "end_time": 12, "max_difficulty": 0,
                       "levels": [{"difficulty": 0, "notes": deepcopy(arr["notes"])}]}]
    original = deepcopy(arr)
    audio = tmp_path / "audio.ogg"
    audio.write_bytes(b"OggS-test")
    timeline = {"version": 1, "duration": 12, "tempos": [], "time_signatures": [],
                "beats": [], "sections": []}
    output = tmp_path / "source.feedpak"
    write_feedpak([arr], timeline, audio, output, title="Source", artist="Test")
    with zipfile.ZipFile(output) as archive:
        wire = json.loads(archive.read("arrangements/bass.json"))
    assert wire["phrases"] == original["phrases"]
    assert wire["notes"] == original["notes"]
    assert "generatedDifficulty" not in wire.get("ext", {})
    assert arr == original


def test_generated_levels_keep_source_identity_and_replaced_phrase_evidence():
    arr = chart()
    arr["ext"] = {"source": {"persistentId": "source-id"}}
    arr["phrases"] = [{"start_time": 1, "end_time": 10, "levels": [{"difficulty": 0}]}]
    original = deepcopy(arr)
    assert ensure_difficulty(arr)
    evidence = arr["ext"]["generatedDifficulty"]
    assert evidence["originalPhrases"] == original["phrases"]
    assert arr["ext"]["source"] == original["ext"]["source"]
    for key in ("notes", "chords", "anchors", "handshapes", "templates"):
        assert arr[key] == original[key]
