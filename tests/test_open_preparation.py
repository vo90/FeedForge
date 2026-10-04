"""An open pickup previews a nearby position without moving musical events."""
from copy import deepcopy

import pytest

from feedback_converter.chart_guidance import finalize, music_digest, digest
from feedback_converter.generated_hand_positions import generate_positions
from feedback_converter.difficulty import ensure_difficulty
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


@pytest.mark.parametrize('gap', [.6, 1, 3])
def test_substantial_silence_breaks_the_open_pickup(gap):
    data = passage(gap=gap)
    finalize(data)
    assert at(data, 2)['fret'] == 5


def test_connected_long_open_hold_has_no_total_duration_cutoff():
    data = passage([note(2, 0, sustain=1)], gap=.75)
    finalize(data)
    assert at(data, 2)['fret'] == 2


@pytest.mark.parametrize('gap', [.01, .2, .49])
def test_short_detached_pickups_adopt_destination(gap):
    data = passage(gap=gap)
    finalize(data)
    assert at(data, 2)['fret'] == 2


def test_cirice_three_opens_after_silence_share_destination_lane():
    data = chart(notes=[note(201.165, 2, sustain=.3),
        note(212.3925, 0, sustain=.3275), note(212.72, 0, 1, .33375),
        note(213.05375, 0, 1, .33375), note(213.3875, 15, 1, .33375),
        note(213.72125, 14, 1, .6675, vb=True), note(215.05625, 12, 1, .67, vb=True)])
    before = music_digest(data)
    finalize(data)
    assert at(data, 212.39)['fret'] == 2
    for t in (212.3925, 212.72, 213.05375, 213.3875):
        assert at(data, t) == {'time':212.3925, 'fret':12, 'width':4}
    assert music_digest(data) == before
    assert validate(data) == []


def test_many_detached_opens_use_local_beat_and_do_not_cross_a_rest():
    data = chart(notes=[note(0, 5, sustain=.1),
        *[note(10 + i * .3, 0, i % 2, .02) for i in range(12)], note(13.6, 15, sustain=.3)])
    data['beats'] = [{'time':i*.3} for i in range(50)]
    finalize(data)
    assert at(data, 10) == at(data, 13.6)
    fast = chart(notes=[note(0, 5, sustain=.1), note(2, 0, sustain=.1), note(2.5, 15)])
    fast['beats'] = [{'time':i*.25} for i in range(20)]
    finalize(fast)
    assert at(fast, 2)['fret'] == 5  # 0.4 s silence exceeds a local beat


def test_open_on_another_string_can_bridge_detached_attacks():
    data = chart(notes=[note(0, 5, sustain=.1), note(2, 0, 0, 3),
                        note(3, 0, 1, .1), note(5, 15, 1, .3)])
    finalize(data)
    assert at(data, 2) == at(data, 5)


@pytest.mark.parametrize('supplied_beats', [False, True])
def test_generated_difficulty_uses_the_same_local_beat_gap(supplied_beats):
    data = chart(notes=[note(0, 5, sustain=.1),
        *[note(2+i*.6, 0, i%2, .05) for i in range(3)], note(3.8, 15)])
    beats = [{'time':i*.6} for i in range(10)]
    data['beats'] = beats
    finalize(data)
    assert at(data, 2) == at(data, 3.8)
    if supplied_beats:
        del data['beats']
    ensure_difficulty(data, beats=beats if supplied_beats else (), duration=5)
    full = data['phrases'][0]['levels'][-1]
    assert at(full, 2) == at(full, 3.8)
    assert full['notes'] == data['notes']


def test_later_opens_can_prepare_after_conflicting_fretted_hold_releases():
    data = chart(notes=[note(0, 5, 2, 2.4), note(2, 0, 0, .5),
                        note(2.5, 0, 1, .5), note(3, 15, 1, .25)])
    finalize(data)
    assert covers(at(data, 2), 5)
    assert at(data, 2.5) == at(data, 3)
    assert validate(data) == []


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


def test_wide_destination_is_shared_but_all_open_ending_keeps_context():
    data = passage()
    data['notes'][-1]['sl'] = 12
    rows = generate_positions(data)
    assert next(a for a in reversed(rows) if a['time'] <= 2)['fret'] == 2
    assert rows[-1]['fret'] == 9  # The subsequent slide travels to fret 12.
    assert rows[-1]['width'] == 4
    data['notes'].pop()
    rows = generate_positions(data)
    assert rows == [{'time': 0., 'fret': 5, 'width': 4}]


@pytest.mark.parametrize('policy', [None, 'chord-local-v1', 'open-preparation-v1'])
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
    assert data['ext']['chartGuidance']['positionPolicy'] == 'slide-follow-v1'
    assert music_digest(data) == music_digest(old)
    assert validate(data) == []


def test_edited_and_unknown_receipts_remain_protected():
    data = passage()
    finalize(data)
    data['ext']['chartGuidance']['positionPolicy'] = 'future-policy'
    with pytest.raises(ValueError):
        finalize(data, regenerate=True)
