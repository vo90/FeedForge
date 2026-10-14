"""Shared lanes require evidence of one recurring, physically playable grip."""
from copy import deepcopy

import pytest

from feedback_converter.chart_guidance import POLICY, digest, finalize, music_digest
from feedback_converter.generated_hand_positions import (
    POSITION_POLICY,
    PREVIOUS_POSITION_POLICIES,
    generate_positions,
)
from feedback_converter.guidance_provenance import record, resolve, stamp
from feedback_converter.verify_chart_guidance import validate


def note(time, string, fret, sustain=.1, **techniques):
    return {"t": time, "s": string, "f": fret, "sus": sustain, **techniques}


def split_grip(*, root=(1, 2), upper=((2, 4), (3, 4)),
               roots=(1., 1.5), responses=(1.25, 1.75), tuning=None,
               root_flags=None, upper_flags=None):
    frets = [-1] * 6
    for string, fret in upper:
        frets[string] = fret
    return {
        "name": "Split power chord", "tuning": [0] * 6 if tuning is None else tuning,
        "capo": 0,
        "notes": [note(t, *root, **(root_flags or {})) for t in roots],
        "chords": [{"t": t, "id": 0, "notes": [
            {"s": s, "f": f, "sus": .1, **(upper_flags or {})} for s, f in upper
        ]} for t in responses],
        "templates": [{"name": "", "frets": frets, "fingers": [-1] * 6}],
        "beats": [{"time": i * .5} for i in range(20)],
        "anchors": [], "handshapes": [],
    }


def position(anchors, time):
    return next(a for a in reversed(anchors) if a["time"] <= time)


def assert_lane(anchors, times, fret):
    for time in times:
        current = position(anchors, time)
        assert (current["fret"], current["width"]) == (fret, 4), (time, anchors)


def picked_pattern(roles):
    """R is the root; C is the upper dyad; F/O pick its fifth/octave singly."""
    data = split_grip(roots=(), responses=())
    for i, role in enumerate(roles):
        time = 1. + i * .125
        if role == "C":
            data["chords"].append({"t": time, "id": 0, "notes": [
                {"s": s, "f": 4, "sus": .05} for s in (2, 3)
            ]})
        else:
            string, fret = {"R": (1, 2), "F": (2, 4), "O": (3, 4)}[role]
            data["notes"].append(note(time, string, fret, .05))
    return data


def test_thunderstruck_split_power_chord_is_straight_from_the_first_pair():
    data = split_grip(root_flags={"pm": True}, upper_flags={"pm": True})
    before = deepcopy(data)
    anchors = generate_positions(data)

    assert anchors == [{"time": 0., "fret": 2, "width": 4}]
    assert data == before
    finalize(data)
    assert data["anchors"] == anchors
    assert music_digest(data) == music_digest(before)
    # Guidance grouping must not turn the two actual dyads into a sounding triad.
    assert all(len(c["notes"]) == 2 for c in data["chords"])
    assert data["handshapes"] == [
        {"chord_id": 0, "start_time": 1.25, "end_time": 1.35, "arp": False},
        {"chord_id": 0, "start_time": 1.75, "end_time": 1.85, "arp": False},
    ]
    assert validate(data) == []


def test_repeated_root_picks_between_responses_still_describe_one_grip():
    data = split_grip(roots=(1., 1.125, 1.5, 1.625))
    assert_lane(generate_positions(data), (1., 1.125, 1.25, 1.5, 1.625, 1.75), 2)


def test_thunderstruck_repeated_and_separately_picked_upper_notes_share_the_grip():
    data = picked_pattern(("R", "C", "R", "C", "C", "R", "C", "R", "F", "O",
                           "R", "C", "R", "C", "C", "R", "C"))
    before = deepcopy(data)
    assert generate_positions(data) == [{"time": 0., "fret": 2, "width": 4}]
    finalize(data)
    assert data["notes"] == before["notes"]
    assert data["chords"] == before["chords"]
    assert data["templates"] == before["templates"]
    assert validate(data) == []


@pytest.mark.parametrize("roles", [
    ("R", "F", "O", "R", "C"),
    ("R", "C", "R", "F", "O"),
    ("R", "F", "O", "R", "O", "F"),
])
def test_complete_upper_pair_can_be_picked_separately_or_together(roles):
    data = picked_pattern(roles)
    before = deepcopy(data)
    assert generate_positions(data) == [{"time": 0., "fret": 2, "width": 4}]
    assert data == before


def test_terminal_split_upper_pair_does_not_absorb_later_independent_dyads():
    data = picked_pattern(("R", "C", "R", "F", "O", "C", "C"))
    anchors = generate_positions(data)
    assert_lane(anchors, (1., 1.125, 1.25, 1.375, 1.5), 2)
    assert_lane(anchors, (1.625, 1.75), 4)


def test_response_missing_the_octave_cannot_connect_otherwise_complete_cycles():
    data = picked_pattern(("R", "C", "R", "F", "R", "C"))
    assert_lane(generate_positions(data), (1.125, 1.625), 4)


@pytest.mark.parametrize("root,upper", [
    ((0, 5), ((1, 7), (2, 7))),
    ((1, 14), ((2, 16), (3, 16))),
])
def test_pitch_equivalent_transpositions_on_unambiguous_lower_strings(root, upper):
    data = split_grip(root=root, upper=upper)
    assert_lane(generate_positions(data), (1., 1.25, 1.5, 1.75), root[1])


@pytest.mark.parametrize("root,upper", [
    ((2, 2), ((3, 4), (4, 5))),
    ((3, 2), ((4, 5), (5, 5))),
    ((2, 2), ((3, 4), (4, 4))),
    ((3, 2), ((4, 4), (5, 4))),
], ids=["guitar-d-g-b", "guitar-g-b-e", "bass-middle-crossing", "bass-upper-crossing"])
def test_six_string_upper_crossings_do_not_guess_guitar_or_bass_identity(root, upper):
    # Six tuning offsets alone do not identify the guitar/bass string baseline.
    # Both possible fingerings keep the independently played dyad's own origin.
    data = split_grip(root=root, upper=upper)
    assert_lane(generate_positions(data), (1.25, 1.75), min(f for _, f in upper))


def test_uniform_down_tuning_and_capo_do_not_change_the_grip():
    data = split_grip(tuning=[-2] * 6)
    data["capo"] = 2
    assert_lane(generate_positions(data), (1., 1.25, 1.5, 1.75), 2)


@pytest.mark.parametrize("data", [
    split_grip(roots=(1.,), responses=(1.25,)),
    split_grip(roots=(), responses=(1.25, 1.75)),
    split_grip(tuning=[0, 0, 1, 0, 0, 0]),
    split_grip(root=(1, 2), upper=((2, 4), (3, 5))),
    split_grip(root=(1, 2), upper=((1, 4), (2, 4))),
], ids=["one-pair", "no-root", "incompatible-tuning",
        "not-an-octave", "same-string-conflict"])
def test_unconfirmed_or_incompatible_shapes_keep_chord_local_positions(data):
    anchors = generate_positions(data)
    assert_lane(anchors, (c["t"] for c in data["chords"]), 4)


def test_an_earlier_low_note_cannot_offset_an_independent_full_power_chord():
    data = split_grip(root=(0, 4), upper=((1, 5), (2, 7), (3, 7)))
    assert_lane(generate_positions(data), (1.25, 1.75), 5)


def test_upper_dyad_continuing_without_the_root_resumes_its_own_lowest_fret():
    data = split_grip(responses=(1.25, 1.75, 2., 2.25))
    anchors = generate_positions(data)
    assert_lane(anchors, (1., 1.25, 1.5, 1.75), 2)
    assert_lane(anchors, (2., 2.25), 4)


def test_rest_separates_two_unconfirmed_pairs():
    data = split_grip(roots=(1., 5.), responses=(1.25, 5.25))
    assert_lane(generate_positions(data), (1.25, 5.25), 4)


@pytest.mark.parametrize("beat,expected", [(.75, 2), (.1, 4)])
def test_the_same_attack_spacing_requires_a_local_beat_to_connect(beat, expected):
    data = split_grip(roots=(1., 2.), responses=(1.5, 2.5))
    data["beats"] = [{"time": i * beat} for i in range(40)]
    assert_lane(generate_positions(data), (1.5, 2.5), expected)


@pytest.mark.parametrize("count,expected", [(16, 2), (17, 4)])
def test_root_run_preview_is_bounded_even_for_rapid_picking(count, expected):
    roots = tuple(start + i * .01 for start in (1., 1.5) for i in range(count))
    data = split_grip(roots=roots)
    for n in data["notes"]:
        n["sus"] = .005  # No held root at the dyad may force the low lane anyway.
    assert_lane(generate_positions(data), (1.25, 1.75), expected)


def test_an_extended_root_passage_is_not_reclassified_by_a_late_dyad():
    # Every neighbouring gap is less than one beat, but each complete
    # root-run/response occupies more than two beats.
    roots = tuple(start + i * .3 for start in (1., 2.7) for i in range(5))
    data = split_grip(roots=roots, responses=(2.45, 4.15))
    assert_lane(generate_positions(data), (2.45, 4.15), 4)


def test_intervening_conflicting_attack_prevents_joining_two_pairs():
    data = split_grip()
    data["notes"].append(note(1.375, 1, 3))
    assert_lane(generate_positions(data), (1.25, 1.75), 4)


def test_confirmed_later_cycles_cannot_reach_back_across_a_conflicting_attack():
    data = split_grip(roots=(1., 1.5, 2.), responses=(1.25, 1.75, 2.25))
    data["notes"].append(note(1.375, 1, 3))
    anchors = generate_positions(data)
    assert_lane(anchors, (1.25,), 4)
    assert_lane(anchors, (1.5, 1.75, 2., 2.25), 2)


def test_completed_grips_on_both_sides_leave_an_independent_middle_chord_local():
    data = split_grip(roots=(1., 1.5, 2.5, 3.), responses=(1.25, 1.75, 2.75, 3.25))
    # Repeated identical dyads inside a continuing riff are legitimate replies.
    # An actual different full chord provides evidence of an independent shape.
    data["chords"].insert(2, {"t": 2., "id": 1, "notes": [
        {"s": s, "f": f, "sus": .1} for s, f in enumerate((5, 7, 7))
    ]})
    data["templates"].append({"name": "", "frets": [5, 7, 7, -1, -1, -1], "fingers": [-1] * 6})
    anchors = generate_positions(data)
    assert_lane(anchors, (1., 1.25, 1.5, 1.75, 2.5, 2.75, 3., 3.25), 2)
    assert_lane(anchors, (2.,), 5)


@pytest.mark.parametrize("techniques", [
    {"sl": 4}, {"slu": 4}, {"slide_out": "up"}, {"ln": True},
    {"ho": True}, {"po": True}, {"bn": 1}, {"hm": True}, {"mt": True},
])
def test_complex_root_cannot_confirm_an_ordinary_split_grip(techniques):
    data = split_grip(root_flags=techniques)
    assert_lane(generate_positions(data), (1.25, 1.75), 4)


@pytest.mark.parametrize("techniques", [{"bn": 1}, {"ln": True}, {"ho": True}, {"hm": True}])
def test_complex_upper_notes_cannot_confirm_an_ordinary_split_grip(techniques):
    data = split_grip(upper_flags=techniques)
    assert_lane(generate_positions(data), (1.25, 1.75), 4)


def test_ambiguous_duplicate_root_attack_is_not_evidence_of_a_grip():
    data = split_grip()
    data["notes"].append(deepcopy(data["notes"][0]))
    assert_lane(generate_positions(data), (1.25, 1.75), 4)


def test_staggered_only_chord_records_do_not_establish_a_simultaneous_dyad():
    data = split_grip()
    for chord in data["chords"]:
        chord["notes"][1]["t"] = chord["t"] + .05
    before = deepcopy(data)
    assert_lane(generate_positions(data), (1.25, 1.3, 1.75, 1.8), 4)
    assert data == before


@pytest.mark.parametrize("template_fields", [
    {"arp": True},
    {"arpeggio": True},
    {"name": "B5 (arp)"},
    {"displayName": "B5-arp"},
    {"frets": [-1, 2, 4, 4, -1, -1]},
    {"frets": [-1, -1, 4, 4, 5, -1]},
], ids=["arp", "arpeggio", "arpeggio-name", "arpeggio-display-name",
        "template-full-root-shape", "template-extra-contact"])
def test_authored_arpeggio_or_extra_template_contacts_are_not_guessed_as_plain_dyads(template_fields):
    data = split_grip()
    data["templates"][0].update(template_fields)
    before = deepcopy(data)
    assert_lane(generate_positions(data), (1.25, 1.75), 4)
    assert data == before


@pytest.mark.parametrize("flag", ["arp", "arpeggio"])
def test_chord_level_arpeggio_markers_exclude_shared_grip_inference(flag):
    data = split_grip()
    for chord in data["chords"]:
        chord[flag] = True
    before = deepcopy(data)
    assert_lane(generate_positions(data), (1.25, 1.75), 4)
    assert data == before


@pytest.mark.parametrize("spans", [
    [(1.2, 1.4)],
    [(1.4, 2.), (1., 1.5)],
    [(0., .5)],
], ids=["first-dyad-overlaps", "unsorted-overlapping-spans", "unrelated-earlier-span"])
def test_existing_handshape_output_neither_changes_anchor_inference_nor_gets_rewritten(spans):
    data = split_grip()
    expected = generate_positions(data)
    data["handshapes"] = [
        {"chord_id": 0, "start_time": start, "end_time": end, "arp": True}
        for start, end in spans
    ]
    before = deepcopy(data)
    assert generate_positions(data) == expected
    assert data == before


def test_an_unrelated_dyad_cannot_reanchor_a_later_pure_single_note_arpeggio():
    data = picked_pattern(("R", "F", "O", "R", "F", "O"))
    # Establish a compact 1-4 lane before a separate passage. Its single notes
    # already fit; only an erroneous inferred grip would move the lane to 2.
    data["chords"] = [{"t": 0., "id": 1, "notes": [
        {"s": s, "f": 1, "sus": .05} for s in (4, 5)
    ]}]
    data["templates"].append({"name": "", "frets": [-1, -1, -1, -1, 1, 1], "fingers": [-1] * 6})
    before = deepcopy(data)
    assert generate_positions(data) == [{"time": 0., "fret": 1, "width": 4}]
    assert data == before


def test_slide_between_pairs_prevents_borrowing_the_earlier_root():
    data = split_grip()
    data["notes"].append(note(1.375, 1, 2, .1, sl=4))
    assert_lane(generate_positions(data), (1.25, 1.75), 4)


def test_sounding_other_string_is_never_hidden_by_a_shared_grip():
    data = split_grip()
    data["notes"].append(note(.75, 5, 12, 2.))
    anchors = generate_positions(data)
    for time, frets in ((1., (2, 12)), (1.25, (4, 12)), (1.5, (2, 12)), (1.75, (4, 12))):
        current = position(anchors, time)
        assert all(current["fret"] <= f < current["fret"] + current["width"] for f in frets)
    finalize(data)
    assert validate(data) == []


def test_a_held_incompatible_contact_rejects_grouping_even_inside_a_compact_lane():
    data = split_grip()
    data["notes"].append(note(.75, 5, 5, 2.))
    before = deepcopy(data)
    anchors = generate_positions(data)
    # Fret 5 fits the proposed 2-5 window, but is not part of this power chord.
    # Mere coverage must never be treated as evidence of the shared shape.
    assert_lane(anchors, (1.25, 1.75), 4)
    finalize(data)
    assert data["notes"] == before["notes"]
    assert data["chords"] == before["chords"]
    assert data["templates"] == before["templates"]
    assert validate(data) == []


def test_precomputation_is_deterministic_and_preserves_existing_handshapes():
    data = split_grip()
    data["handshapes"] = [{"chord_id": 0, "start_time": 1., "end_time": 2., "arp": True}]
    original = deepcopy(data)
    assert generate_positions(data) == generate_positions(deepcopy(data))
    assert data == original


@pytest.mark.parametrize("legacy_receipt_only", [False, True])
def test_explicit_regeneration_upgrades_owned_old_policy(legacy_receipt_only):
    data = split_grip()
    finalize(data)
    old_policy = "positionless-preparation-v1"
    data["anchors"] = [
        {"time": 0., "fret": 2, "width": 4},
        {"time": 1.25, "fret": 4, "width": 4},
        {"time": 1.5, "fret": 2, "width": 4},
        {"time": 1.75, "fret": 4, "width": 4},
    ]
    receipt = data["ext"]["chartGuidance"]
    receipt["positionPolicy"] = old_policy
    receipt["guidanceSha256"] = digest({key: data[key] for key in receipt["fields"]})
    for field in receipt["fields"]:
        stamp(data, field, "generated", producer=POLICY, policy=old_policy)
    if legacy_receipt_only:
        data["ext"].pop("guidanceProvenance")
    previous = deepcopy(data)

    finalize(data)
    assert data == previous
    finalize(data, regenerate=True)
    assert data["anchors"] == [{"time": 0., "fret": 2, "width": 4}]
    assert POSITION_POLICY != old_policy
    assert old_policy in PREVIOUS_POSITION_POLICIES
    assert data["ext"]["chartGuidance"]["positionPolicy"] == POSITION_POLICY
    assert record(data, "anchors")["policy"] == POSITION_POLICY
    assert resolve(data, "anchors") == {
        "origin": "generated", "integrity": "valid", "applicability": "current",
    }
    assert music_digest(data) == music_digest(previous)
    assert data["handshapes"] == previous["handshapes"]
    assert validate(data) == []


def test_authored_position_for_a_recurring_shape_is_preserved():
    data = split_grip()
    data["anchors"] = [{"time": 0., "fret": 1, "width": 5}]
    authored = deepcopy(data["anchors"])
    finalize(data, regenerate=True)
    assert data["anchors"] == authored
    assert "anchors" not in data["ext"]["chartGuidance"]["fields"]


def test_manually_edited_generated_position_is_not_silently_regenerated():
    data = split_grip()
    finalize(data)
    data["anchors"][0]["fret"] = 1
    before = deepcopy(data)
    with pytest.raises(ValueError, match="edited"):
        finalize(data, regenerate=True)
    assert data == before
