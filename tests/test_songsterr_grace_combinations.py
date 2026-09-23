from copy import deepcopy
import pytest
from test_song_import_score import beat, measure, raw_score
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected


def test_grace_rest_delays_attack_without_inventing_a_note():
    rest = {'notes': [{'rest': True}], 'rest': True, 'type': 64,
            'duration': [1, 64], 'graceNote': 'onBeat'}
    source = raw_score([measure(rest, beat(duration=(1, 8)))])
    original = deepcopy(source)
    notes = render(parse(source))['tracks'][0]['notes']
    checked = expected(songsterr(source), {'offset': 0, 'scale': 1})['parts'][0]['notes']
    assert len(notes) == len(checked) == 1
    assert notes[0]['t'] == checked[0]['note']['t'] == .03125
    assert notes[0]['sus'] == checked[0]['note']['sus'] == .21875
    assert source == original


def test_grace_before_a_rest_keeps_the_rest_and_only_the_authored_attack():
    grace = {**beat(fret=5, duration=(1, 16)), 'graceNote': 'onBeat'}
    rest = {**beat(), 'notes': [{'rest': True}]}
    source = raw_score([measure(grace, rest)])
    actual = render(parse(source))['tracks'][0]
    checked = expected(songsterr(source), {'offset': 0, 'scale': 1})['parts'][0]['notes']
    assert len(actual['notes']) == len(checked) == 1
    assert actual['notes'][0]['t'] == checked[0]['note']['t'] == 0
    assert actual['notes'][0]['sus'] == checked[0]['note']['sus'] == .125
    assert actual['notation']['measures'][0]['staves']['staff']['voices'][0]['beats'][1]['rest'] is True


def test_explicit_grace_rake_uses_source_spread_and_keeps_the_principal_onset():
    grace = {'duration': [1, 32], 'type': 32, 'graceNote': 'onBeat',
             'brushStroke': {'direction': 'down', 'duration': 60, 'shift': 100},
             'notes': [{'string': 3, 'fret': 0, 'dead': True}, {'string': 4, 'fret': 0, 'dead': True}]}
    source = raw_score([measure(grace, beat(fret=6, string=2, duration=(1, 8)))])
    notes = render(parse(source))['tracks'][0]['notes']
    checked = expected(songsterr(source), {'offset': 0, 'scale': 1})['parts'][0]['notes']
    assert [n['t'] for n in notes] == [0, .03125, .0625]
    assert [n['sus'] for n in notes] == [.0625, .03125, .1875]
    assert [r['note']['t'] for r in checked] == [0, .03125, .0625]
    assert all(n['mt'] for n in notes[:2])


@pytest.mark.parametrize('previous_duration,first_sustain', [((1, 1), 1.9375), ((1, 4), .5)])
def test_before_bar_grace_borrows_tail_and_keeps_written_measure(previous_duration, first_sustain):
    grace = {**beat(fret=5, duration=(1, 32)), 'type': 32, 'graceNote': 'beforeBeat'}
    source = raw_score([measure(beat(fret=3, duration=previous_duration)), measure(grace, beat(fret=7))])
    original = deepcopy(source)
    result = render(parse(source))['tracks'][0]
    checked = expected(songsterr(source), {'offset': 0, 'scale': 1})['parts'][0]['notes']
    assert [n['t'] for n in result['notes']] == [0, 1.9375, 2]
    assert [n['sus'] for n in result['notes']] == [first_sustain, .0625, 2]
    assert [n['note']['t'] for n in checked] == [0, 1.9375, 2]
    assert [n['note']['sus'] for n in checked] == [first_sustain, .0625, 2]
    written = result['notation']['measures'][1]['staves']['staff']['voices'][0]['beats'][0]
    assert written['grace'] == 'a' and written['t'] == 1.9375 and written['beat_pos'] == [0, 1]
    assert source == original


def test_before_beat_at_recording_start_uses_source_on_beat_fallback():
    grace = {**beat(fret=5, duration=(1, 32)), 'type': 32, 'graceNote': 'beforeBeat'}
    source = raw_score([measure(grace, beat(fret=7))])
    result = render(parse(source))['tracks'][0]
    checked = expected(songsterr(source), {'offset': 0, 'scale': 1})['parts'][0]['notes']
    assert [n['t'] for n in result['notes']] == [0, .0625]
    assert [n['note']['t'] for n in checked] == [0, .0625]
    assert result['notes'][1]['sus'] == 1.9375


def test_cross_bar_grace_inside_an_intact_repeated_pair_keeps_its_borrowed_time():
    grace = {**beat(duration=(1, 32)), 'graceNote': 'beforeBeat'}
    source = raw_score([measure(beat(), repeatStart=True), measure(grace, beat(), repeat=2)])
    actual = render(parse(source))['tracks'][0]['notes']
    checked = expected(songsterr(source), {'offset': 0, 'scale': 1})['parts'][0]['notes']
    assert [n['t'] for n in actual] == [0, 1.9375, 2, 4, 5.9375, 6]
    assert [n['note']['t'] for n in checked] == [0, 1.9375, 2, 4, 5.9375, 6]


def test_cross_bar_grace_still_rejects_a_jump_between_the_borrowing_pair():
    grace = {**beat(duration=(1, 32)), 'graceNote': 'beforeBeat'}
    source = raw_score([measure(beat(), repeatStart=True), measure(beat(), repeat=2), measure(grace, beat())])
    with pytest.raises(ValueError, match='repeat'): render(parse(source))
    with pytest.raises(ValueError, match='repeat'): expected(songsterr(source), {'offset': 0, 'scale': 1})


def test_a_repeat_elsewhere_does_not_disable_cross_bar_grace():
    grace = {**beat(duration=(1, 32)), 'graceNote': 'beforeBeat'}
    source = raw_score([measure(beat(), repeatStart=True, repeat=2), measure(beat()), measure(grace, beat())])
    actual = render(parse(source))['tracks'][0]['notes']
    checked = expected(songsterr(source), {'offset': 0, 'scale': 1})['parts'][0]['notes']
    assert [n['t'] for n in actual] == [0, 2, 4, 5.9375, 6]
    assert [n['note']['t'] for n in checked] == [0, 2, 4, 5.9375, 6]


def test_two_groups_borrow_from_the_same_authored_principal_budget():
    on = {**beat(fret=3, duration=(1, 16)), 'graceNote': 'onBeat'}
    before = {**beat(fret=7, duration=(1, 16)), 'graceNote': 'beforeBeat'}
    source = raw_score([measure(on, beat(fret=5, duration=(1, 4)), before, beat(fret=9, duration=(1, 4)))])
    actual = render(parse(source))['tracks'][0]['notes']
    checked = expected(songsterr(source), {'offset': 0, 'scale': 1})['parts'][0]['notes']
    assert [n['t'] for n in actual] == [0, .125, .375, .5]
    assert [n['sus'] for n in actual] == [.125, .25, .125, .5]
    assert [n['note']['sus'] for n in checked] == [.125, .25, .125, .5]
