from copy import deepcopy
import pytest
from test_song_import_score import beat, measure, raw_score
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected


@pytest.mark.parametrize('fret,pitch', [(4,28),(5,24),(7,19),(9,28),(12,12),(16,28),(19,19)])
@pytest.mark.parametrize('capo', [0,2])
def test_integer_natural_nodes_keep_playing_position_and_sounding_pitch(fret, pitch, capo):
    source = raw_score([measure(beat(fret=fret, harmonic='natural', harmonicFret=fret))])
    source['parts'][0]['capo'] = capo
    original = deepcopy(source)
    assert inspect_songsterr(source)['status'] != 'blocked'
    actual = render(parse(source))['tracks'][0]
    checked = expected(songsterr(source), {'offset': 0, 'scale': 1})['parts'][0]
    assert actual['notes'][0]['hm'] and actual['notes'][0]['f'] == fret
    written = actual['notation']['measures'][0]['staves']['staff']['voices'][0]['beats'][0]['notes'][0]
    assert written['midi'] == actual['tuning'][actual['notes'][0]['s']] + capo + pitch
    assert checked['notation_beats'][0]['notes'][0]['midi'] == written['midi']
    assert written['fret'] == fret and source == original


@pytest.mark.parametrize('fret,touch,kind', [(3,2.7,'natural'),(3,3.2,'natural'),(6,5.8,'natural'),
    (7,12,'natural'),(5,12,'pinch'),(5,12,'artificial'),(5,12,'tapped'),(5,12,'semi'),(5,12,'feedback')])
def test_nodes_requiring_a_different_instruction_are_not_flattened(fret,touch,kind):
    source = raw_score([measure(beat(fret=fret, harmonic=kind, harmonicFret=touch))])
    assert inspect_songsterr(source)['status'] == 'blocked'
    with pytest.raises(ValueError): parse(source)
    with pytest.raises(ValueError): songsterr(source)


def test_natural_harmonic_tie_keeps_a_single_playable_harmonic():
    note = beat(fret=7, duration=(1,2), harmonic='natural', harmonicFret=7)
    tied = deepcopy(note); tied['notes'][0]['tie'] = True
    source = raw_score([measure(note,tied)])
    actual = render(parse(source))['tracks'][0]['notes']
    checked = expected(songsterr(source), {'offset': 0, 'scale': 1})['parts'][0]['notes']
    assert len(actual) == len(checked) == 1
    assert actual[0]['hm'] and actual[0]['sus'] == checked[0]['note']['sus'] == 2


@pytest.mark.parametrize('corruption', [None, 'ordinary_pitch', 'missing_instruction'])
def test_independent_archive_check_catches_harmonic_pitch_or_cue_loss(tmp_path, corruption):
    from test_song_import_verification import example, verify
    source, package = example()
    raw = source['parts'][0]['measures'][0]['voices'][0]['beats'][2]['notes'][0]
    raw.pop('ghost'); raw.update(harmonic='natural', harmonicFret=7)
    chart_note = package['chart.json']['notes'][2]
    chart_note.pop('ghost'); chart_note['hm'] = True
    written = package['notation.json']['measures'][0]['staves']['staff']['voices'][0]['beats'][2]['notes'][0]
    written.pop('ghost'); written['midi'] = 59  # Open E2 (40) + third partial (19).
    if corruption == 'ordinary_pitch': written['midi'] = 47
    if corruption == 'missing_instruction': chart_note.pop('hm')
    report = verify(tmp_path, source, package)
    assert report['status'] == ('passed' if corruption is None else 'failed'), report
