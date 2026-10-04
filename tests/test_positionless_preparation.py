"""Positionless playing previews the next phrase without changing the music."""
from copy import deepcopy

import pytest

from feedback_converter.chart_guidance import finalize, music_digest
from feedback_converter.generated_hand_positions import generate_positions
from feedback_converter.verify_chart_guidance import validate
from test_chart_guidance import at, chart, chord, covers, note, template


def pickup(extra=None, fret=0):
    return chart([note(0, 2, sustain=.5), note(2, fret, sustain=.25, **(extra or {})),
                  note(2.25, 15, sustain=.25)])


@pytest.mark.parametrize('extra', [{}, {'pm': True}, {'ac': True}, {'ghost': True},
    {'slp': True}, {'plk': True}, {'tp': True}, {'pkd': 1}, {'rh': 2}, {'fhm': True},
    {'pm': True, 'ac': True, 'ghost': True}, {'mt': True}, {'mt': True, 'fhm': True},
    {'tr': True}, {'mt': True, 'tr': True}, {'pm': True, 'tr': True},
    {'whammy': {'version': 1, 'policy': 'optional', 'segments': [
        {'start': 0, 'end': .25, 'source_id': 'test', 'group': 'bar',
         'curve': [{'t': 0, 'v': 0}, {'t': .25, 'v': -1}]}]}}])
def test_open_articulations_share_the_destination_and_preserve_music(extra):
    data = pickup(extra)
    before = music_digest(data)
    finalize(data)
    assert at(data, 1.999)['fret'] == 2
    assert at(data, 2) == at(data, 2.25)
    assert covers(at(data, 2), 15)
    assert music_digest(data) == before
    assert validate(data) == []


@pytest.mark.parametrize('hidden_fret', [0, 3, 19, 24, 127])
@pytest.mark.parametrize('tremolo', [False, True])
def test_dead_placeholder_frets_are_not_played_positions(hidden_fret, tremolo):
    data = pickup({'mt': True, 'tr': tremolo}, hidden_fret)
    before = deepcopy(data)
    finalize(data)
    assert at(data, 2) == at(data, 2.25)
    assert covers(at(data, 2), 15)
    assert data['notes'] == before['notes']
    assert validate(data) == []


def test_cirice_two_muted_chords_prepare_the_7_9_chord():
    data = chart(chords=[
        chord(186.04, (0, 2), .6725, tid=0),
        chord(186.7125, (0, 0), .168125, tid=1, mt=True),
        chord(186.880625, (0, 0), .168125, tid=1, mt=True),
        chord(187.04875, (7, 9), .33625, tid=2)],
        templates=[template((0, 2)), template((0, 0)), template((7, 9))])
    before = music_digest(data)
    finalize(data)
    assert at(data, 186.7)['fret'] == 2
    for time in (186.7125, 186.880625, 187.04875):
        assert at(data, time) == {'time': 186.7125, 'fret': 7, 'width': 4}
    assert music_digest(data) == before
    assert validate(data) == []


def test_open_and_dead_members_share_one_lane_across_chords_and_singles():
    data = pickup()
    data['notes'][1] = note(2, 127, sustain=.05, mt=True)
    data['notes'][-1]['t'] = 2.75
    c = chord(2.25, (0, 18, 0), .25)
    c['notes'][0]['pm'] = True
    c['notes'][1]['mt'] = True
    c['notes'][2]['fhm'] = True
    data.update(chords=[c], templates=[template((0, 18, 0))])
    finalize(data)
    assert at(data, 2) == at(data, 2.25) == at(data, 2.75)
    assert covers(at(data, 2), 15)
    assert validate(data) == []


@pytest.mark.parametrize('extra', [{'fhm': True}, {'pm': True}, {'ghost': True}])
def test_positive_fret_is_retained_without_an_explicit_dead_strike(extra):
    data = pickup(extra, 3)
    finalize(data)
    assert covers(at(data, 2), 3)
    assert not covers(at(data, 2), 15)
    assert validate(data) == []


@pytest.mark.parametrize('extra', [{'ho': True}, {'po': True}, {'ln': True}, {'bn': 1},
    {'bnv': [{'t': 0, 'v': 1}]}, {'bt': 1}, {'vb': True}, {'vibrato': True},
    {'vibrato_marks': [{'start': 0, 'end': .25, 'intensity': 'wide'}]},
    {'hm': True, 'hn': 5}, {'hp': True}, {'harmonic_target': {'kind': 'natural'}},
    {'harmonic_changes': {'version': 1}}, {'sl': 3}, {'slu': 3}, {'su': 3},
    {'slide_out': 'down'}, {'slideOut': 'up'}, {'slide_in_marks': [{'t': 0}]},
    {'slide_out_marks': [{'t': 0}]}, {'pick_scrape_marks': [{'start': 0, 'end': .25}]}])
def test_contact_and_gesture_context_is_not_borrowed(extra):
    data = pickup(extra)
    data['anchors'] = generate_positions(data)
    assert not covers(at(data, 2), 15)


@pytest.mark.parametrize('source_fret', [0, 2])
def test_incoming_link_protects_an_unmarked_open_target(source_fret):
    data = chart([note(0, 2, sustain=.5), note(1.5, source_fret, sustain=.5, ln=True),
                  note(2, 0, sustain=.25), note(2.25, 15)])
    data['anchors'] = generate_positions(data)
    assert not covers(at(data, 2), 15)


def test_expired_link_does_not_pin_an_independent_later_pick():
    data = pickup()
    data['notes'][0]['ln'] = True
    data['anchors'] = generate_positions(data)
    assert at(data, 2) == at(data, 2.25)


def test_other_string_hold_and_mixed_fretted_chord_still_protect_position():
    data = pickup({'mt': True})
    data['notes'][0].update(s=5, sus=2.125)
    finalize(data)
    assert covers(at(data, 2), 2)
    assert validate(data) == []
    mixed = chart(chords=[chord(1, (0, 3, 0), .25), chord(1.25, (12, 15), .25)],
                  templates=[template((0, 3, 0))])
    mixed['chords'][0]['notes'][0]['mt'] = True
    mixed['anchors'] = generate_positions(mixed)
    assert covers(at(mixed, 1), 3)
    assert not covers(at(mixed, 1), 15)


def test_dead_runs_obey_gap_limit_and_keep_context_without_a_destination():
    data = pickup({'mt': True})
    data['notes'][-1]['t'] = 4
    data['anchors'] = generate_positions(data)
    assert at(data, 2)['fret'] == 2
    data['notes'].pop()
    data['anchors'] = generate_positions(data)
    assert at(data, 2)['fret'] == 2
