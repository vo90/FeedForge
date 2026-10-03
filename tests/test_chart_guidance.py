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


def test_touching_cross_string_boundaries_do_not_create_wide_lane():
    data = chart([note(38.1525, 2, 1, .1625), note(38.315, 7, 0, .325)],
                 [chord(38.64, (0, 2, 2), .325)], [template((0, 2, 2))])
    original = music_digest(data)
    assert 38.1525 + .1625 > 38.315  # reproduce the binary arithmetic artefact
    finalize(data)
    assert all(a['width'] == 4 for a in data['anchors'])
    assert at(data, 38.64) == {'time': 38.64, 'fret': 2, 'width': 4}
    assert music_digest(data) == original
    assert validate(data) == []


@pytest.mark.parametrize('overlap', [.000001, .002, .2])
def test_real_cross_string_overlaps_still_require_full_width(overlap):
    data = chart([note(1, 2, 1, 1 + overlap), note(2, 7, 0, 1)])
    finalize(data)
    assert covers(at(data, 2), 2, 7)
    assert at(data, 2)['width'] == 6
    assert validate(data) == []


def test_independent_checker_rejects_hiding_real_microsecond_overlap():
    data = chart([note(1, 2, 1, 1.000001), note(2, 7, 0, 1)])
    finalize(data)
    data['anchors'] = [{'time': 0., 'fret': 2, 'width': 4},
                       {'time': 2., 'fret': 4, 'width': 4}]
    data['ext']['chartGuidance']['wideAnchorCount'] = 0
    rehash(data)
    assert any('excludes an active fret' in e for e in validate(data))


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


@pytest.mark.parametrize("hidden_fret", [0,1,7,24,127])
def test_plain_dead_editor_frets_do_not_widen_fresh_chord_guidance(hidden_fret):
    frets=(3,hidden_fret,0,0,3,3)
    ch=chord(64,frets,.46125)
    ch["notes"][1].update(mt=True,sus=.3075)
    data=chart(chords=[ch],templates=[template(frets)])
    before=music_digest(data)
    finalize(data)
    assert at(data,64)=={"time":0.0,"fret":3,"width":4}
    assert music_digest(data)==before
    assert validate(data)==[]

@pytest.mark.parametrize("cue", [{"pm":True},{"fhm":True},{"mt":True,"sl":9},{"mt":True,"po":True}])
def test_positioned_techniques_keep_their_frets(cue):
    data=chart([note(0,3,0,2),note(1,7,1,1,**cue)])
    finalize(data)
    assert covers(at(data,1),3,7)
    assert validate(data)==[]


def test_back_in_black_pull_off_prepares_seven_to_ten_at_the_chord_attack():
    ch = {"t": 214.76, "id": 0, "notes": [
        {"s": 3, "f": 9, "sus": .150625, "ln": True},
        {"s": 4, "f": 0, "sus": .150625, "mt": True},
        {"s": 5, "f": 0, "sus": .150625}]}
    data = chart([note(209, 9, sustain=1), note(212.19, 10, 4, 2.57),
                  note(214.910625, 7, 3, .30125, po=True), note(215.211875, 9, 3, .753125, vb=True)],
                 [ch], [{"frets": [-1,-1,-1,9,0,0]}])
    original = music_digest(data)
    finalize(data)
    assert at(data, 214.76) == {"time": 214.76, "fret": 7, "width": 4}
    assert at(data, 214.910625) == at(data, 214.76)
    assert music_digest(data) == original
    assert validate(data) == []


@pytest.mark.parametrize("frets,flags", [((9,7), {"po":True}), ((6,8), {"ho":True}),
                                       ((0,3), {"ho":True}), ((9,0), {"po":True})])
def test_explicit_connected_techniques_fit_one_normal_lane_from_first_attack(frets, flags):
    data = chart([note(0, frets[0], sustain=1), note(1, frets[0], sustain=.15),
                  note(1.15, frets[1], sustain=.3, **flags)])
    finalize(data)
    assert covers(at(data, 1), *(f for f in frets if f))
    assert at(data, 1)["width"] == 4
    assert validate(data) == []


@pytest.mark.parametrize("gap", [0, .001, .00101, .01, .2])
def test_only_rounding_sized_gaps_can_connect_a_pull_off(gap):
    data = chart([note(0,9,sustain=1), note(2,9,sustain=.15), note(2.15+gap,7,po=True)])
    finalize(data)
    assert covers(at(data,2),7) is (gap <= .001)
    assert validate(data) == []


@pytest.mark.parametrize("case", ["picked", "wrong-direction", "two-flags", "dead", "bend", "slide", "ambiguous", "long"])
def test_no_speculative_connection_for_unrelated_or_ambiguous_notes(case):
    a, b = note(2,9,sustain=.15), note(2.15,7,po=True)
    notes = [note(0,9,sustain=1), a, b]
    if case == "picked": b.pop("po")
    if case == "wrong-direction": b.pop("po"); b["ho"] = True
    if case == "two-flags": b["ho"] = True
    if case == "dead": a["mt"] = True
    if case == "bend": a["bn"] = 1
    if case == "slide": a["sl"] = 10
    if case == "ambiguous": notes.append(deepcopy(a))
    if case == "long": a["sus"] = .6; b["t"] = 2.6
    data=chart(notes)
    finalize(data)
    assert not covers(at(data,2),7)
    assert validate(data)==[]


def test_compact_chain_is_prepared_but_future_wide_jump_does_not_expand_it():
    data = chart([note(0,9,sustain=1), note(2,9,sustain=.1), note(2.1,7,sustain=.1,po=True),
                  note(2.2,6,sustain=.1,po=True), note(2.3,1,po=True)])
    finalize(data)
    assert covers(at(data,2),6,7,9)
    assert at(data,2)["width"]==4
    assert validate(data)==[]


def test_hopo_anticipation_never_hides_another_sustained_string_or_widens_the_lane():
    data=chart([note(0,9,sustain=1), note(1,12,1,3), note(2,9,sustain=.15), note(2.15,7,po=True)])
    finalize(data)
    assert covers(at(data,2),9,12)
    assert at(data,2)["width"]==4
    assert covers(at(data,2.15),7,12)
    assert validate(data)==[]


def test_source_authored_positions_remain_authored():
    data=chart([note(0,9,sustain=.15),note(.15,7,po=True)])
    authored=[{"time":0,"fret":9,"width":4},{"time":.15,"fret":7,"width":4}]
    data["anchors"]=deepcopy(authored)
    finalize(data)
    assert data["anchors"]==authored
    assert "anchors" not in data["ext"]["chartGuidance"]["fields"]


def test_chords_start_at_their_lowest_fret_even_inside_a_covering_anchor():
    data = chart([note(0, 4, sustain=1)], [chord(1, (5,7,7)), chord(2, (7,8), tid=1)],
                 [template((5,7,7)), template((7,8))])
    finalize(data)
    assert at(data, 1) == {"time":1,"fret":5,"width":4}
    assert at(data, 2) == {"time":2,"fret":7,"width":4}
    assert validate(data) == []


def test_slide_corridor_releases_at_the_next_chord_in_rats():
    data = chart([note(105.5375,3,sustain=.24875,sl=8)],
        [chord(105.78625,(8,7),1.24375), chord(107.03,(3,5,5),.72375,tid=1),
         chord(107.75375,(5,7,7),1.20625,tid=2)],
        [template((8,7)),template((3,5,5)),template((5,7,7))])
    before = music_digest(data)
    finalize(data)
    assert data["anchors"] == [{"time":0.,"fret":3,"width":6},
        {"time":105.78625,"fret":7,"width":4}, {"time":107.03,"fret":3,"width":4},
        {"time":107.75375,"fret":5,"width":4}]
    assert music_digest(data) == before
    assert validate(data) == []


def test_overlapping_low_sustain_stays_covered_then_releases_without_three_new_attacks():
    data = chart([note(0,2,5,1.5)], [chord(1,(7,9),2)], [template((7,9))])
    finalize(data)
    assert covers(at(data,1),2,7,9)
    assert at(data,1.5) == {"time":1.5,"fret":7,"width":4}
    assert validate(data) == []


def test_wide_chord_then_power_chord_has_no_width_memory():
    data = chart(chords=[chord(0,(3,10),1),chord(1,(5,7),1,tid=1)],
                 templates=[template((3,10)),template((5,7))])
    finalize(data)
    assert at(data,0)["width"] == 8
    assert at(data,1) == {"time":1,"fret":5,"width":4}
    assert validate(data) == []


def test_open_only_chord_after_a_stretch_returns_to_four_context_cells():
    data = chart(chords=[chord(0,(3,10),1),chord(1,(0,0),1,tid=1)],
                 templates=[template((3,10)),template((0,0))])
    finalize(data)
    assert at(data,0)["width"] == 8
    assert at(data,1) == {"time":1,"fret":3,"width":4}
    assert validate(data) == []


def test_explicit_regeneration_upgrades_old_owned_positions_without_changing_music():
    data = chart(chords=[chord(0,(5,7))],templates=[template((5,7))])
    finalize(data)
    data["ext"]["chartGuidance"].pop("positionPolicy")
    data["anchors"] = [{"time":0.,"fret":4,"width":4}]
    rehash(data)
    old = deepcopy(data)
    finalize(data)
    assert data == old
    finalize(data,regenerate=True)
    assert at(data,0) == {"time":0.,"fret":5,"width":4}
    assert data["ext"]["chartGuidance"]["positionPolicy"] == "open-preparation-v2"
    assert music_digest(data) == music_digest(old)
    assert validate(data) == []
