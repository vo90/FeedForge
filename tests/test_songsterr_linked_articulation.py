from copy import deepcopy
import pytest
from test_song_import_score import beat, measure, raw_score
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected


@pytest.mark.parametrize('slide', ['above', 'below', 'upwards', 'downwards', 'belowdownwards', 'shift', 'legato'])
def test_staccato_and_slide_keep_the_same_attack_and_authored_destination(slide):
    source = raw_score([measure(beat(fret=7, duration=(1, 4), staccato=True, slide=slide),
                                {**beat(fret=9, duration=(3, 4)), 'type': 2, 'dots': 1})])
    original = deepcopy(source)
    chart = render(parse(source))['tracks'][0]
    independent = expected(songsterr(source), {'offset': 0, 'scale': 1})['parts'][0]
    first, second = chart['notes']
    assert (first['t'], first['sus'], second['t']) == (0, .25, .5)
    assert first['sus'] == independent['notes'][0]['note']['sus']
    if slide in ('shift', 'legato'):
        assert first['sl'] == 9
        assert first.get('ln', False) == (slide == 'legato')
    if slide in ('above', 'below', 'belowdownwards'):
        assert first['slide_in_marks'][0]['time'] == 0
    if slide in ('upwards', 'downwards', 'belowdownwards'):
        assert first['slide_out_marks'][0]['end'] == .25
    written = chart['notation']['measures'][0]['staves']['staff']['voices'][0]['beats'][0]
    assert written['dur'] == 4 and written['end_time'] == .5
    assert source == original


def test_staccato_bend_uses_shortened_sound_interval_before_bend_generation():
    bend = {'points': [{'position': 0, 'tone': 0}, {'position': 30, 'tone': 100}, {'position': 60, 'tone': 50}]}
    source = raw_score([measure(beat(fret=7, duration=(1, 4), staccato=True, hp=True, bend=bend), beat(fret=9, duration=(3, 4)))])
    notes = render(parse(source))['tracks'][0]['notes']
    checked = expected(songsterr(source), {'offset': 0, 'scale': 1})['parts'][0]['notes']
    assert notes[0]['bnv'] == [{'t': 0., 'v': 0.}, {'t': .125, 'v': 2.}, {'t': .25, 'v': 1.}]
    assert notes[0]['bnv'] == checked[0]['note']['bnv']
    assert notes[0]['ln'] and notes[1]['ho']
    assert notes[0]['sus'] == .25


@pytest.mark.parametrize('origin,continuation,sustain', [(True, False, 1), (False, True, 2), (True, True, 1)])
def test_ties_are_folded_before_source_staccato_articulation(origin, continuation, sustain):
    source = raw_score([measure(beat(duration=(1, 4), staccato=origin),
                                {**beat(duration=(3, 4), tie=True, staccato=continuation), 'type': 2, 'dots': 1})])
    original = deepcopy(source)
    actual = render(parse(source))['tracks'][0]
    checked = expected(songsterr(source), {'offset': 0, 'scale': 1})['parts'][0]['notes']
    assert len(actual['notes']) == len(checked) == 1
    assert actual['notes'][0]['sus'] == checked[0]['note']['sus'] == sustain
    assert len(actual['notes'][0]['source_ids']) == 2
    assert actual['notation']['measures'][0]['staves']['staff']['voices'][0]['beats'][1]['notes'][0]['tied']
    assert source == original


def test_staccato_tie_chain_crosses_bar_without_adding_an_attack():
    source = raw_score([measure(beat(staccato=True)), measure(beat(tie=True))])
    actual = render(parse(source))['tracks'][0]['notes']
    checked = expected(songsterr(source), {'offset': 0, 'scale': 1})['parts'][0]['notes']
    assert len(actual) == len(checked) == 1
    assert actual[0]['sus'] == checked[0]['note']['sus'] == 2


def test_staccato_plain_tie_keeps_origin_and_existing_sound_duration():
    source = raw_score([measure(beat(duration=(1, 4), staccato=True), beat(fret=4, duration=(1, 4), tie=True))])
    original = deepcopy(source)
    actual = render(parse(source))
    independent = expected(songsterr(source), {'offset': 0, 'scale': 1})
    note, = actual['tracks'][0]['notes']
    assert (note['f'], note['t'], note['sus']) == (3, 0, .5)
    assert note['sus'] == independent['parts'][0]['notes'][0]['note']['sus']
    assert actual['plainTieIdentityEvidence'] == independent['plain_tie_identities']
    row, = actual['plainTieIdentityEvidence']
    assert (row['attack'], row['start'], row['end']) == (0, .5, 1)
    assert row['authored'] == {'fret': 4} and row['used'] == {'fret': 3}
    assert row['originSourceId'] == note['source_ids'][0]
    assert source == original


@pytest.mark.parametrize('changed', ['gesture', 'gap'])
def test_staccato_does_not_repair_a_broken_tie(changed):
    following = beat(fret=4 if changed == 'gesture' else 3, duration=(1, 4), tie=True,
                     **({'slide': 'upwards'} if changed == 'gesture' else {}))
    beats = [beat(duration=(1, 4), staccato=True)]
    if changed == 'gap': beats.append({'duration': [1, 4], 'notes': [{'rest': True}]})
    source = raw_score([measure(*beats, following)])
    with pytest.raises(ValueError, match='tie'): render(parse(source))
    with pytest.raises(ValueError, match='tie'): expected(songsterr(source), {'offset': 0, 'scale': 1})


def test_combined_staccato_tie_pitch_gesture_remains_explicit_until_verified():
    source = raw_score([measure(beat(duration=(1, 4), staccato=True, slide='upwards'),
                                beat(duration=(3, 4), tie=True))])
    with pytest.raises(ValueError, match='pitch gesture'): render(parse(source))
    with pytest.raises(ValueError, match='pitch gesture'): expected(songsterr(source), {'offset': 0, 'scale': 1})
