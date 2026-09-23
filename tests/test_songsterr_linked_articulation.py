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


def test_tied_staccato_is_still_rejected_until_whole_tie_articulation_is_supported():
    source = raw_score([measure(beat(duration=(1, 4)), beat(duration=(3, 4), tie=True, staccato=True))])
    with pytest.raises(ValueError, match='staccato'): parse(source)
    with pytest.raises(ValueError, match='staccato'): songsterr(source)
