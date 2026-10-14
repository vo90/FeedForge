"""Qualified natural-harmonic shorthand and ordinary playback stay distinct."""
from copy import deepcopy

import pytest

from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.model import ScoreImportError
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from test_song_import_score import beat, import_json, measure, raw_score


SHORTHAND = [(2, 2.4, 36), (3, 3.2, 31), (6, 5.8, 34), (8, 8.2, 36),
             (10, 9.6, 34), (15, 14.7, 34), (22, 21.7, 34)]
ORDINARY = [1, 11, 13, 14, 18, 20, 21, 23]
HARMONIC_KEYS = {"hm", "hn", "hps", "harmonic_alias", "harmonic_target",
                 "harmonic_changes", "__harmonic_pitch_offset"}


def source_note(document, index=0):
    return document["parts"][0]["measures"][0]["voices"][0]["beats"][index]["notes"][0]


def written(performance):
    return [item for bar in performance["tracks"][0]["notation"]["measures"]
            for voice in bar["staves"]["staff"]["voices"] for item in voice["beats"]]


def document(fret, touch="absent", *, capo=0, drop=0):
    item = beat(fret, string=1, harmonic="natural")
    if touch != "absent":
        item["notes"][0]["harmonicFret"] = None if touch == "null" else fret
    item["harmonic"] = True
    result = raw_score([measure(item)], tuning=[n + drop for n in [64, 59, 55, 50, 45, 40]])
    result["parts"][0]["capo"] = capo
    return result


def ordinary_control(source):
    result = deepcopy(source)
    for part in result["parts"]:
        for bar in part["measures"]:
            for voice in bar["voices"]:
                for item in voice["beats"]:
                    item.pop("harmonic", None)
                    for note in item["notes"]:
                        note.pop("harmonic", None)
                        note.pop("harmonicFret", None)
    return result


@pytest.mark.parametrize("fret,node,pitch", SHORTHAND)
@pytest.mark.parametrize("touch", ["absent", "null", "integer"])
@pytest.mark.parametrize("capo,drop", [(0, 0), (2, -5)])
def test_integer_shorthand_uses_qualified_precise_node_and_pitch(fret, node, pitch, touch, capo, drop):
    source = document(fret, touch, capo=capo, drop=drop)
    original = deepcopy(source)
    result = render(parse(source))
    note, = result["tracks"][0]["notes"]
    assert (note["f"], note["hn"], note["hps"], note["hm"]) == (fret, node, pitch, True)
    authored, = written(result)[0]["notes"]
    assert (authored["fret"], authored["midi"]) == (fret, 59 + drop + capo + pitch)
    report = inspect_songsterr(source)
    assert report["status"] != "blocked"
    assert not any(row["feature"] == "note.natural_harmonic_fallback" for row in report["findings"])
    assert source == original


@pytest.mark.parametrize("fret", ORDINARY)
@pytest.mark.parametrize("touch", ["absent", "null", "integer"])
@pytest.mark.parametrize("capo,drop", [(0, 0), (2, -5)])
def test_no_transform_natural_mark_keeps_an_ordinary_pitched_attack(fret, touch, capo, drop):
    source = document(fret, touch, capo=capo, drop=drop)
    original = deepcopy(source)
    result = render(parse(source))
    control = render(parse(ordinary_control(source)))
    note, = result["tracks"][0]["notes"]
    assert not (HARMONIC_KEYS & note.keys())
    assert result["tracks"] == control["tracks"]
    assert result["scoreTimeline"] == control["scoreTimeline"]
    assert result["duration"] == control["duration"]
    authored, = written(result)[0]["notes"]
    assert (authored["fret"], authored["midi"]) == (fret, 59 + drop + capo + fret)
    report = inspect_songsterr(source)
    warning, = [row for row in report["findings"] if row["feature"] == "note.natural_harmonic_fallback"]
    assert (warning["category"], warning["impact"], warning["workStatus"]) == (
        "game_limitation", "display_or_expression", "display_limitation")
    assert warning["retained"] == "original_source"
    assert warning["location"].startswith("parts/0/measures/0/voices/0/beats/0/notes/0")
    assert not any(row["impact"] in {"blocking", "gameplay_omission"} for row in report["findings"])
    assert source == original


@pytest.mark.parametrize("fret", [19, 24])
@pytest.mark.parametrize("touch", ["absent", "null", "integer"])
def test_known_equal_pitch_nodes_remain_real_harmonics(fret, touch):
    result = render(parse(document(fret, touch)))
    note, = result["tracks"][0]["notes"]
    assert (note["hm"], note["hn"], note["hps"]) == (True, fret, fret)
    assert not any(row["feature"] == "note.natural_harmonic_fallback"
                   for row in inspect_songsterr(document(fret, touch))["findings"])


@pytest.mark.parametrize("fret,touch,pitch", [(3, 2.7, 34), (3, 3.2, 31), (15, 14.7, 34)])
def test_explicit_precise_node_wins_over_integer_shorthand(fret, touch, pitch):
    source = document(fret)
    source_note(source)["harmonicFret"] = touch
    note, = render(parse(source))["tracks"][0]["notes"]
    assert (note["hn"], note["hps"]) == (touch, pitch)


@pytest.mark.parametrize("fret", [0, 3, 15, 18])
@pytest.mark.parametrize("touch", [True, "15", -1, 3.1, 12, float("nan"), float("inf")])
def test_unknown_or_contradictory_explicit_touches_are_not_normalized(fret, touch):
    source = document(fret)
    source_note(source)["harmonicFret"] = touch
    with pytest.raises(ScoreImportError):
        parse(source)


@pytest.mark.parametrize("fret", [0, True, "18", 18.1, float("nan"), float("inf")])
def test_special_zero_and_malformed_frets_do_not_enter_ordinary_fallback(fret):
    source = document(fret)
    with pytest.raises(ScoreImportError):
        parse(source)


@pytest.mark.parametrize("fret", [15, 18])
@pytest.mark.parametrize("field,value", [("harmonicData", {}), ("harmonicData", {"type": "natural"}),
    ("dead", True), ("pickScrape", "up"), ("harmonic", "unknown")])
def test_opaque_or_incompatible_techniques_remain_guarded(fret, field, value):
    source = document(fret)
    source_note(source)[field] = deepcopy(value)
    with pytest.raises(ScoreImportError):
        parse(source)


def cliffs_contexts():
    bend = {"tone": 100, "points": [{"position": 0, "tone": 0},
            {"position": 30, "tone": 100}, {"position": 60, "tone": 0}]}
    first = beat(18, string=1, duration=(1, 16), harmonic="natural", bend=deepcopy(bend))
    second = beat(18, string=1, duration=(1, 8), harmonic="natural", bend=deepcopy(bend))
    third = beat(15, string=1, duration=(1, 8), harmonic="natural")
    first["harmonic"] = second["harmonic"] = third["harmonic"] = True
    first["type"], second["type"], third["type"] = 16, 8, 8
    third["text"] = {"text": "Vibrato + Artificial Harmonic"}
    return raw_score([measure(first, beat(18, string=1, duration=(1, 16), tie=True),
                              second, beat(18, string=1, duration=(1, 4), tie=True)),
                      measure(third, beat(15, string=1, duration=(1, 2), tie=True, vibrato=True))])


def test_cliffs_shaped_bends_ties_and_ambiguous_text_keep_structured_playback(tmp_path):
    source = cliffs_contexts()
    # Deliberately synthetic identity: the rule cannot depend on the reported song.
    source.update(songId=987654321, revisionId=987, title="Harmonic contexts", artist="Fixture")
    original = deepcopy(source)
    result = import_json(tmp_path, source)
    notes = result["tracks"][0]["notes"]
    assert len(notes) == 3
    assert [(n["t"], n["sus"]) for n in notes] == [(0, .25), (.25, .75), (2, 1.25)]
    control = render(parse(ordinary_control(source)))["tracks"][0]["notes"]
    assert notes[:2] == control[:2]
    assert all(not (HARMONIC_KEYS & n.keys()) for n in notes[:2])
    assert all(n["bnv"] and max(p["v"] for p in n["bnv"]) == 2 for n in notes[:2])
    assert (notes[2]["f"], notes[2]["hn"], notes[2]["hps"]) == (15, 14.7, 34)
    # The written tie has no harmonic mark of its own. Existing notation keeps
    # that authored pitch; the one playable harmonic sustain retains pitch93.
    assert [item["notes"][0]["midi"] for item in written(result)] == [77, 77, 77, 77, 93, 74]
    assert [item["notes"][0].get("tied", False) for item in written(result)] == [False, True, False, True, False, True]
    precise_control = ordinary_control(source)
    precise_control["parts"][0]["measures"][1]["voices"][0]["beats"][0]["notes"][0].update(
        harmonic="natural", harmonicFret=14.7)
    assert result["tracks"] == render(parse(precise_control))["tracks"]
    warnings = [row for row in result["compatibilityReport"]["findings"]
                if row["feature"] == "note.natural_harmonic_fallback"]
    assert len(warnings) == 2
    assert len({(row["feature"], row["category"], row["impact"]) for row in warnings}) == 1
    assert result["sourceScore"]["document"] == original == source


@pytest.mark.parametrize("technique", ["ghost", "accentuated", "vibrato", "staccato"])
def test_fallback_retains_other_local_note_instructions(technique):
    source = document(18)
    source_note(source)[technique] = True
    result = render(parse(source))
    assert result["tracks"] == render(parse(ordinary_control(source)))["tracks"]


def test_fallback_on_chords_and_repeats_does_not_create_or_remove_attacks():
    source = document(18)
    bar = source["parts"][0]["measures"][0]
    bar.update(repeatStart=True, repeat=2)
    bar["voices"][0]["beats"][0]["notes"].append({"string": 2, "fret": 11, "harmonic": "natural"})
    result = render(parse(source))
    assert result["tracks"] == render(parse(ordinary_control(source)))["tracks"]
    assert len(result["tracks"][0]["chords"]) == 2
    assert sum(len(chord["notes"]) for chord in result["tracks"][0]["chords"]) == 4
    assert len([row for row in inspect_songsterr(source)["findings"]
                if row["feature"] == "note.natural_harmonic_fallback"]) == 2


@pytest.mark.parametrize("context", ["note_rest", "beat_rest", "excluded_instrument", "excluded_selection"])
def test_inactive_harmonic_marks_do_not_add_a_player_warning_or_policy(context):
    source = document(18)
    selection = None
    marked = source["parts"][0]["measures"][0]["voices"][0]["beats"][0]
    marked.pop("harmonic")
    if context in {"note_rest", "beat_rest"}:
        marked["notes"][0]["rest"] = True
        if context == "beat_rest":
            marked["rest"] = True
        source["parts"][0]["measures"][0]["voices"][0]["beats"].append(beat(7))
    else:
        source["parts"].append(deepcopy(source["parts"][0]))
        source["tracks"].append({**source["tracks"][0], "id": 1,
                                  "instrumentId": 0 if context == "excluded_instrument" else 29})
        source["parts"][0]["measures"][0] = measure(beat(7))
        selection = {0} if context == "excluded_selection" else None
    parsed = parse(source, track_indices=selection)
    assert "naturalHarmonicPlaybackPolicy" not in parsed.source
    report = inspect_songsterr(source, track_indices=selection)
    assert not any(row["feature"] == "note.natural_harmonic_fallback" for row in report["findings"])
