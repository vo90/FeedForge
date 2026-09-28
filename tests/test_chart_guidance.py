"""Presentation completion never changes musical instructions."""
from copy import deepcopy
import random

import pytest

from feedback_converter.chart_guidance import POLICY, digest, finalize, music_digest
from feedback_converter.verify_chart_guidance import validate, validate_arrangement
from feedback_converter.difficulty import ensure_difficulty


def note(t, fret, string=0, sustain=0, **techniques):
    return {"t": t, "s": string, "f": fret, "sus": sustain, **techniques}


def chart(notes=(), chords=(), templates=(), strings=6):
    return {"name": "Example", "tuning": [0] * strings, "capo": 0,
            "notes": list(notes), "chords": list(chords), "templates": list(templates),
            "anchors": [], "handshapes": []}


def chord(t, frets=(3, 5), sustain=1, tid=0, **techniques):
    return {"t": t, "id": tid, "notes": [{"s": s, "f": f, "sus": sustain, **techniques} for s, f in enumerate(frets)]}


def template(frets=(3, 5), strings=6):
    return {"name": "", "frets": list(frets) + [-1] * (strings - len(frets)), "fingers": [-1] * strings}


def at(data, time):
    return next(a for a in reversed(data["anchors"]) if a["time"] <= time)


def covers(anchor, *frets):
    return all(anchor["fret"] <= f < anchor["fret"] + anchor["width"] for f in frets)


def rehash(data):
    proof = data["ext"]["chartGuidance"]
    proof["guidanceSha256"] = digest({k: data[k] for k in proof["fields"]})


@pytest.mark.parametrize("frets", [(1, 5), (1, 12), (20, 24), (1, 24)])
def test_inclusive_bounds_cover_wide_chords_at_neck_edges(frets):
    data = chart(chords=[chord(1, frets)], templates=[template(frets)])
    original = deepcopy(data)
    finalize(data)
    assert covers(at(data, 1), *frets)
    assert at(data, 1)["width"] >= max(frets) - min(frets) + 1
    assert music_digest(data) == music_digest(original)
    assert validate(data) == []


@pytest.mark.parametrize("strings", [4, 5, 6, 7, 8])
def test_open_and_unpitched_material_does_not_invent_a_fret(strings):
    data = chart([note(0, 0, strings - 1, 1), note(2, 127, 0, .1, mt=True)], strings=strings)
    finalize(data)
    assert data["anchors"] == [{"time": 0, "fret": 1, "width": 4}]
    assert validate(data) == []


def test_stability_open_notes_rests_and_bounded_lookahead():
    data = chart([note(0, 5), note(.25, 8), note(.5, 6), note(1, 0, sustain=1), note(10, 7)])
    finalize(data)
    assert len(data["anchors"]) == 1
    assert covers(data["anchors"][0], 5, 6, 7, 8)
    assert validate(data) == []


def test_sustained_other_string_remains_covered_until_its_release():
    data = chart([note(0, 2, 0, 4), note(1, 15, 1, 1), note(2, 17, 1, 1)])
    finalize(data)
    assert covers(at(data, 1), 2, 15)
    assert covers(at(data, 2), 2, 17)
    assert validate(data) == []


def test_next_attack_releases_only_guidance_not_source_sustain():
    data = chart([note(0, 2, 0, 10), note(1, 15, 0, 1)])
    finalize(data)
    assert at(data, 1)["width"] == 4
    assert data["notes"][0]["sus"] == 10
    assert validate(data) == []


def test_pick_scrape_reference_fret_does_not_move_the_hand_position():
    data = chart([note(0, 3, sustain=.5), note(1, 34, sustain=1, mt=True,
                 pick_scrape_marks=[{"direction": "down", "start": 0, "end": 1}])])
    before = deepcopy(data["notes"])
    finalize(data)
    assert len(data["anchors"]) == 1
    assert covers(data["anchors"][0], 3)
    assert data["notes"] == before
    assert validate(data) == []


def test_zero_duration_attacks_do_not_leave_a_permanent_wide_lane():
    data = chart([note(0, 2, 0), note(1, 20, 1)])
    finalize(data)
    assert at(data, 1)["width"] == 4
    assert validate(data) == []


@pytest.mark.parametrize("key", ["sl", "slu"])
def test_known_slide_corridor_and_natural_harmonic_contact(key):
    data = chart([note(0, 5, 0, 2, **{key: 12}), note(.5, 3, 1, .5, hm=True, hn=3.2)])
    finalize(data)
    assert covers(at(data, 0), 5, 12)
    assert covers(at(data, .5), 3, 4, 5, 12)
    assert validate(data) == []


def test_wide_lane_shrinks_after_a_coherent_narrow_passage():
    data = chart([note(2, 3), note(2.5, 4), note(3, 2)], [chord(0, (1, 24))], [template((1, 24))])
    finalize(data)
    assert at(data, 0)["width"] == 24
    assert at(data, 3)["width"] == 4
    assert validate(data) == []


def test_handshape_ends_with_first_member_and_repeated_attacks_remain():
    first = chord(1, sustain=1)
    first["notes"][1]["sus"] = .5
    data = chart(chords=[first, chord(1.5, sustain=.5), chord(3, sustain=.5)], templates=[template()])
    before = deepcopy(data)
    finalize(data)
    assert data["handshapes"] == [
        {"chord_id": 0, "start_time": 1, "end_time": 2, "arp": False},
        {"chord_id": 0, "start_time": 3, "end_time": 3.5, "arp": False}]
    assert data["chords"] == before["chords"]
    assert validate(data) == []


def test_new_pick_on_member_string_stops_shape_without_shortening_music():
    data = chart([note(.5, 8, 1, .5)], [chord(0, sustain=2)], [template()])
    finalize(data)
    assert data["handshapes"][0]["end_time"] == .5
    assert data["chords"][0]["notes"][0]["sus"] == 2
    assert validate(data) == []


@pytest.mark.parametrize("techniques", [{"sl": 7}, {"bn": 1}, {"hm": True}, {"mt": True}, {"ln": True}, {"ho": True}, {"slide_out": "up"}])
def test_complex_chords_do_not_get_a_guessed_handshape(techniques):
    data = chart(chords=[chord(0, **techniques)], templates=[template()])
    finalize(data)
    assert not data["handshapes"]
    assert validate(data) == []


@pytest.mark.parametrize("marker", [{"arp": True}, {"name": "Am (arp)"}, {"displayName": "Am-arp"}])
def test_authored_arpeggio_templates_are_not_given_ordinary_holds(marker):
    data = chart(chords=[chord(0)], templates=[{**template(), **marker}])
    finalize(data)
    assert data["handshapes"] == []
    assert data["ext"]["chartGuidance"]["handshapeSkippedChords"] == 1
    assert validate(data) == []


def test_idempotent_and_explicit_regeneration_after_composition():
    data = chart([note(0, 3)])
    finalize(data)
    once = deepcopy(data)
    finalize(data)
    assert data == once
    data["notes"].append(note(3, 19))
    with pytest.raises(ValueError, match="regenerate"):
        finalize(data)
    finalize(data, regenerate=True)
    assert covers(at(data, 3), 19)
    assert validate(data) == []


def test_authored_and_manually_edited_guidance_is_preserved():
    data = chart([note(0, 3)])
    data["anchors"] = [{"time": 0, "fret": 2, "width": 5, "custom": "authored"}]
    authored = deepcopy(data["anchors"])
    finalize(data)
    assert data["anchors"] == authored
    assert data["ext"]["chartGuidance"]["fields"] == ["handshapes"]
    data["handshapes"].append({"custom": "user edit"})
    changed = deepcopy(data)
    with pytest.raises(ValueError, match="edited"):
        finalize(data, regenerate=True)
    assert data == changed


@pytest.mark.parametrize("fault", ["coverage", "width", "missing_anchor", "hold_start", "hold_end", "missing_hold", "arp", "music"])
def test_independent_checks_reject_even_rehashed_bad_guidance(fault):
    data = chart(chords=[chord(1)], templates=[template()])
    finalize(data)
    if fault == "coverage": data["anchors"][0].update(fret=10)
    if fault == "width": data["anchors"][0].update(width=0)
    if fault == "missing_anchor": data["anchors"] = []
    if fault == "hold_start": data["handshapes"][0]["start_time"] += .1
    if fault == "hold_end": data["handshapes"][0]["end_time"] += .1
    if fault == "missing_hold": data["handshapes"] = []
    if fault == "arp": data["handshapes"][0]["arp"] = True
    if fault == "music": data["chords"][0]["notes"][0]["f"] = 7
    rehash(data)
    assert validate(data)


def test_difficulty_guidance_uses_retained_events_and_clips_at_phrase_boundary():
    data = chart([note(1, 2, sustain=1), note(3, 20), note(5, 3)], [chord(7.5, sustain=2)], [template()])
    original = music_digest(data)
    finalize(data)
    ensure_difficulty(data, duration=12)
    assert validate_arrangement(data) == []
    assert music_digest(data) == original
    for phrase in data["phrases"]:
        for level in phrase["levels"]:
            assert level["anchors"][0]["time"] == phrase["start_time"]
            assert all(h["end_time"] <= phrase["end_time"] for h in level["handshapes"])


def test_random_sounding_intervals_preserve_music_and_pass_independent_bounds():
    rng = random.Random(1709)
    for _ in range(35):
        data = chart([note(i / 8, rng.randrange(25), rng.randrange(6), rng.randrange(12) / 8) for i in range(100)])
        old = deepcopy(data)
        finalize(data)
        assert validate(data) == []
        assert music_digest(data) == music_digest(old)
        again = deepcopy(data)
        finalize(data)
        assert again == data
