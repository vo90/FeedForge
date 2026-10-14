"""Note dynamics retain written scope without changing the playable chart."""
from copy import deepcopy

import pytest

from feedback_converter.feedpak_validator import _schema_validator
from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.model import ScoreImportError
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.voices import flat
from test_song_import_score import beat, import_json, measure, raw_score


DYNAMICS = ("ppp", "pp", "p", "mp", "mf", "f", "ff", "fff")


def written_beats(performance):
    return [item for bar in performance["tracks"][0]["notation"]["measures"]
            for voice in bar["staves"]["staff"]["voices"] for item in voice["beats"]]


def without_note_dynamics(document):
    control = deepcopy(document)
    for part in control["parts"]:
        for bar in part["measures"]:
            for voice in bar["voices"]:
                for item in voice["beats"]:
                    for note in item["notes"]:
                        note.pop("velocity", None)
    return control


def playable(performance):
    return {"tracks": [{key: value for key, value in track.items() if key != "notation"}
                       for track in performance["tracks"]],
            **{key: performance[key] for key in
               ("duration", "beats", "tempos", "time_signatures", "scoreTimeline", "strumEvidence")}}


def dynamics_rows(document, **options):
    return [row for row in inspect_songsterr(document, **options)["findings"]
            if row["feature"] == "note.velocity"]


@pytest.mark.parametrize("value", DYNAMICS)
def test_each_note_dynamic_is_local_notation_and_retained_evidence(tmp_path, value):
    first = beat(8, string=1, duration=(1, 2))
    first["notes"] += [{"string": 2, "fret": 7, "velocity": value}, {"string": 3, "fret": 5}]
    document = raw_score([measure(first, beat(10, string=1, duration=(1, 2)))])
    original = deepcopy(document)
    result = import_json(tmp_path, document)
    assert result["sourceScore"]["document"] == original == document
    assert playable(result) == playable(render(parse(without_note_dynamics(document))))
    written = written_beats(result)
    assert list(_schema_validator("notation.schema.json").iter_errors(result["tracks"][0]["notation"])) == []
    assert "dyn" not in written[0]
    assert [note.get("dyn") for note in written[0]["notes"]] == [None, value, None]
    assert all("dyn" not in note for note in written[1]["notes"])
    assert [note.note_dynamic for note in parse(document).tracks[0].written_bars[0][0].beats[0].notes] == [None, value, None]
    row, = dynamics_rows(document)
    assert row["location"] == "parts/0/measures/0/voices/0/beats/0/notes/1/velocity"
    assert row["value"] == value
    assert (row["category"], row["impact"]) == ("game_limitation", "display_or_expression")
    entry, = [item for item in result["featureInventory"]
              if item["scope"] == "Songsterr note" and item["field"] == "velocity"]
    assert "notation" in entry["representations"] and "playable" not in entry["representations"]


@pytest.mark.parametrize("value", [None, "mf", "ppp", "fff"])
def test_note_dynamic_does_not_overwrite_beat_dynamic_or_inherit_to_companions(value):
    first = beat(8, string=1, velocity=value)
    first["velocity"] = "ff"
    first["notes"].append({"string": 2, "fret": 7})
    document = raw_score([measure(first)])
    result = render(parse(document))
    item, = written_beats(result)
    assert item["dyn"] == "ff"
    assert [note.get("dyn") for note in item["notes"]] == [value, None]
    assert ("dyn" in item["notes"][0]) is (value is not None)
    assert playable(result) == playable(render(parse(without_note_dynamics(document))))


def test_absent_and_null_do_not_add_an_instruction_or_player_limitation(tmp_path):
    absent = raw_score([measure(beat(7))])
    null = deepcopy(absent)
    null["parts"][0]["measures"][0]["voices"][0]["beats"][0]["notes"][0]["velocity"] = None
    absent_result = import_json(tmp_path, absent)
    null_result = import_json(tmp_path, null)
    assert absent_result["tracks"] == null_result["tracks"]
    assert dynamics_rows(absent) == []
    row, = dynamics_rows(null)
    assert row["value"] is None
    assert (row["category"], row["impact"]) == ("source_metadata", "source_retained")
    assert null_result["sourceScore"]["document"] == null


@pytest.mark.parametrize("value", [None, False, 64, "MF", "", ["mf"], {"value": "mf"}])
def test_notation_schema_rejects_invalid_exported_note_dynamics(value):
    result = render(parse(raw_score([measure(beat(7, velocity="mf"))])))
    notation = result["tracks"][0]["notation"]
    written_beats(result)[0]["notes"][0]["dyn"] = value
    errors = list(_schema_validator("notation.schema.json").iter_errors(notation))
    assert errors and all(list(error.absolute_path)[-3:] == ["notes", 0, "dyn"] for error in errors)


MALFORMED = [False, True, 0, 1, 0.0, 64, "", "MF", " mf ", "forte", "0", [], {}, ["mf"], {"value": "mf"}]
MALFORMED += [{"value": float("nan")}, [float("inf")]]


@pytest.mark.parametrize("value", MALFORMED)
@pytest.mark.parametrize("placement", ["sounding", "rest", "excluded"])
def test_invalid_note_dynamics_are_not_silently_ignored(value, placement):
    document = raw_score([measure(beat(3)), measure(beat(5, velocity=deepcopy(value)))])
    if placement == "rest":
        document["parts"][0]["measures"][1]["voices"][0]["beats"][0]["notes"][0] = {
            "rest": True, "velocity": deepcopy(value)}
    elif placement == "excluded":
        excluded = deepcopy(document["parts"][0])
        document["parts"][0] = without_note_dynamics(document)["parts"][0]
        document["parts"].append(excluded)
        document["tracks"].append({"id": 1, "name": "Piano", "instrumentId": 0})
    with pytest.raises(ScoreImportError, match="dynamic"):
        parse(document)
    row, = dynamics_rows(document)
    assert row["impact"] == "blocking"
    assert row["location"].endswith("/notes/0/velocity")


@pytest.mark.parametrize("placement", ["rest", "excluded_instrument", "excluded_selection"])
def test_valid_inactive_notes_are_evidence_only(placement):
    document = raw_score([measure(beat(3)), measure(beat(5, velocity="mf"))])
    selection = None
    if placement == "rest":
        document["parts"][0]["measures"][1]["voices"][0]["beats"][0]["notes"][0] = {
            "rest": True, "velocity": "mf"}
    else:
        document["parts"].append(deepcopy(document["parts"][0]))
        document["parts"][0] = without_note_dynamics(document)["parts"][0]
        document["tracks"].append({"id": 1, "name": "Other", "instrumentId": 0 if placement == "excluded_instrument" else 29,
                                   "tuning": [64, 59, 55, 50, 45, 40]})
        selection = {0} if placement == "excluded_selection" else None
    result = render(parse(document, track_indices=selection))
    control = render(parse(without_note_dynamics(document), track_indices=selection))
    assert result["tracks"] == control["tracks"]
    row, = dynamics_rows(document, track_indices=selection)
    assert (row["category"], row["impact"]) == ("source_metadata", "source_retained")


@pytest.mark.parametrize("technique", ["ordinary", "ghost", "accentuated", "letRing", "tie"])
def test_dynamics_survive_written_repeats_without_changing_attack_or_sustain(technique):
    first = beat(5, string=2, duration=(1, 2), velocity="p")
    second = beat(5 if technique == "tie" else 7, string=2, duration=(1, 2), velocity="ff")
    if technique == "letRing":
        first["letRing"] = True
        second["notes"][0]["string"] = 1
    elif technique == "tie":
        second["notes"][0]["tie"] = True
    elif technique in ("ghost", "accentuated"):
        first["notes"][0][technique] = True
    document = raw_score([measure(first, second, repeatStart=True, repeat=2)])
    original = deepcopy(document)
    result = render(parse(document))
    control = render(parse(without_note_dynamics(document)))
    assert playable(result) == playable(control)
    assert [item["notes"][0].get("dyn") for item in written_beats(result)] == ["p", "ff", "p", "ff"]
    assert document == original
    assert len(flat(result["tracks"][0])) == (2 if technique == "tie" else 4)
    if technique == "tie":
        assert [item["notes"][0].get("tied", False) for item in written_beats(result)] == [False, True, False, True]


def test_note_dynamics_do_not_leak_between_voices():
    bar = measure(beat(3, string=1, velocity="p"))
    bar["voices"].append({"beats": [beat(5, string=3, velocity="ff")]})
    document = raw_score([bar])
    result = render(parse(document))
    assert [item["notes"][0]["dyn"] for item in written_beats(result)] == ["p", "ff"]
    assert playable(result) == playable(render(parse(without_note_dynamics(document))))


def test_note_dynamics_do_not_change_qualified_natural_harmonic_playback():
    # Compact source-shaped regression for the captured Cliffs failure. The
    # separately qualified harmonic rules must not change note-dynamic scope.
    bend = {"tone": 100, "points": [{"position": 0, "tone": 0},
                                   {"position": 30, "tone": 100}, {"position": 60, "tone": 0}]}
    first = beat(18, string=1, duration=(1, 16), harmonic="natural", bend=deepcopy(bend))
    first["harmonic"] = True
    second = beat(18, string=1, duration=(1, 8), harmonic="natural", bend=deepcopy(bend))
    second["harmonic"] = True
    third = beat(15, string=1, duration=(1, 8), harmonic="natural")
    third.update(harmonic=True, text={"text": "Vibrato + Artificial Harmonic"})
    chord = beat(8, string=1)
    chord["notes"] += [{"string": 2, "fret": 7, "velocity": "mf"}, {"string": 3, "fret": 5}]
    document = raw_score([measure(first, second, third), measure(chord)])
    report = inspect_songsterr(document)
    blocked = [row for row in report["findings"] if row["impact"] == "blocking"]
    assert blocked == []
    assert len([row for row in report["findings"]
                if row["feature"] == "note.natural_harmonic_fallback"]) == 2
    dynamic, = dynamics_rows(document)
    assert dynamic["value"] == "mf" and dynamic["impact"] != "blocking"
    assert playable(render(parse(document))) == playable(render(parse(without_note_dynamics(document))))
