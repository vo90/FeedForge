from copy import deepcopy
import pytest
from test_song_import_score import raw_score, measure, beat
from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected


@pytest.mark.parametrize('visible', [True, False])
def test_tempo_visibility_changes_labels_not_the_musical_clock(visible):
    source = raw_score([measure(beat())])
    baseline = render(parse(source))
    source['parts'][0]['automations']['tempo'][0]['visible'] = visible
    original = deepcopy(source)
    report = inspect_songsterr(source)
    assert report['status'] == 'limitations'
    assert report['findings'][0]['feature'] == 'tempo.visible'
    result = render(parse(source))
    assert result['tracks'] == baseline['tracks']
    assert result['scoreTimeline'] == baseline['scoreTimeline']
    assert expected(songsterr(source), {'offset': 0, 'scale': 1})['score_duration'] == result['duration']
    assert source == original


def test_inactive_touch_position_does_not_turn_an_ordinary_note_into_a_harmonic():
    source = raw_score([measure(beat(fret=4, harmonicFret=4))])
    original = deepcopy(source)
    assert inspect_songsterr(source)['status'] == 'compatible'
    converted = render(parse(source))['tracks'][0]['notes'][0]
    verified = expected(songsterr(source), {'offset': 0, 'scale': 1})['parts'][0]['notes'][0]
    assert converted['f'] == 4 and not converted.get('hm') and not converted.get('hp')
    assert source == original
    assert verified
    source['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['harmonic'] = 'natural'
    assert inspect_songsterr(source)['status'] == 'blocked'
    with pytest.raises(Exception, match='harmonicFret'):
        parse(source)
    with pytest.raises(Exception, match='Harmonic-fret'):
        songsterr(source)


@pytest.mark.parametrize('value', ['true', 1, None, {}])
def test_malformed_visibility_is_not_treated_as_a_display_preference(value):
    source = raw_score([measure(beat())])
    source['parts'][0]['automations']['tempo'][0]['visible'] = value
    with pytest.raises(Exception, match='visibility'):
        parse(source)
    with pytest.raises(Exception, match='visibility'):
        songsterr(source)


@pytest.mark.parametrize('value', [True, '4', -1, float('inf')])
def test_inactive_harmonic_metadata_still_requires_a_valid_number(value):
    source = raw_score([measure(beat(harmonicFret=value))])
    with pytest.raises(Exception): parse(source)
    with pytest.raises(Exception): songsterr(source)
