"""An open pickup previews a nearby position without moving musical events."""
from copy import deepcopy

import pytest

from feedback_converter.chart_guidance import finalize, music_digest, digest
from feedback_converter.generated_hand_positions import generate_positions
from feedback_converter.verify_chart_guidance import validate
from test_chart_guidance import at, chart, chord, covers, note, template


def passage(opens=None, target=2, gap=0):
    return chart(notes=(opens if opens is not None else [note(2, 0, sustain=.25)])
                 + [note(2.25 + gap, target, sustain=.25)],
                 chords=[chord(0, (5, 7, 7), 2)], templates=[template((5, 7, 7))])


def test_open_pickup_moves_to_the_destination_without_changing_music():
    data = passage()
    before = music_digest(data)
    finalize(data)
    assert at(data, 1.999)['fret'] == 5
    assert at(data, 2) == {'time': 2, 'fret': 2, 'width': 4}
    assert at(data, 2.25) == at(data, 2)
    assert music_digest(data) == before
    assert validate(data) == []


def test_contiguous_short_open_run_adopts_one_position():
    data = passage([note(1.75, 0, sustain=.25), note(2, 0, sustain=.25)])
    data['chords'][0]['notes'] = [dict(n, sus=1.75) for n in data['chords'][0]['notes']]
    finalize(data)
    assert at(data, 1.75)['fret'] == 2
    assert at(data, 1.75) == at(data, 2.25)


@pytest.mark.parametrize('gap', [.01, .2, 3])
def test_silence_breaks_the_open_pickup(gap):
    data = passage(gap=gap)
    finalize(data)
    assert at(data, 2)['fret'] == 5


def test_distant_destination_does_not_reposition_a_long_open_hold():
    data = passage([note(2, 0, sustain=1)], gap=.75)
    finalize(data)
    assert at(data, 2)['fret'] == 5


@pytest.mark.parametrize('technique', [{'mt': True}, {'ho': True}, {'bn': 1},
                                      {'sl': 2}, {'whammy': [{'time': 0, 'value': 1}]}])
def test_techniques_do_not_become_plain_open_pickups(technique):
    data = passage([note(2, 0, sustain=.25, **technique)], target=16)
    positions = generate_positions(data)
    assert next(a for a in reversed(positions) if a['time'] <= 2)['fret'] != 16


def test_other_string_hold_and_mixed_chord_keep_their_fretted_position():
    data = passage()
    data['chords'][0]['notes'][1]['sus'] = 2.25
    finalize(data)
    assert covers(at(data, 2), 7)
    assert validate(data) == []
    mixed = chart(chords=[chord(1, (0, 5, 7), .25), chord(1.25, (2, 4), .5)],
                  templates=[template((0, 5, 7))])
    assert next(a for a in reversed(generate_positions(mixed)) if a['time'] <= 1)['fret'] == 5


def test_all_open_chord_prepares_next_fretted_chord():
    data = chart(chords=[chord(0, (5, 7), 1), chord(1, (0, 0), .25), chord(1.25, (2, 4), 1)])
    rows = generate_positions(data)
    assert next(a for a in reversed(rows) if a['time'] <= 1)['fret'] == 2


def test_wide_destination_and_all_open_ending_keep_context():
    data = passage()
    data['notes'][-1]['sl'] = 12
    rows = generate_positions(data)
    assert next(a for a in reversed(rows) if a['time'] <= 2)['fret'] == 5
    data['notes'].pop()
    rows = generate_positions(data)
    assert rows == [{'time': 0., 'fret': 5, 'width': 4}]


@pytest.mark.parametrize('policy', [None, 'chord-local-v1'])
def test_recognized_old_positions_upgrade_only_on_explicit_regeneration(policy):
    data = passage()
    finalize(data)
    data['anchors'] = [{'time': 0., 'fret': 5, 'width': 4}, {'time': 2.25, 'fret': 2, 'width': 4}]
    proof = data['ext']['chartGuidance']
    proof['positionPolicy'] = policy
    proof['guidanceSha256'] = digest({k: data[k] for k in proof['fields']})
    old = deepcopy(data)
    finalize(data)
    assert data == old
    finalize(data, regenerate=True)
    assert at(data, 2)['fret'] == 2
    assert data['ext']['chartGuidance']['positionPolicy'] == 'open-preparation-v1'
    assert music_digest(data) == music_digest(old)
    assert validate(data) == []


def test_edited_and_unknown_receipts_remain_protected():
    data = passage()
    finalize(data)
    data['ext']['chartGuidance']['positionPolicy'] = 'future-policy'
    with pytest.raises(ValueError):
        finalize(data, regenerate=True)
