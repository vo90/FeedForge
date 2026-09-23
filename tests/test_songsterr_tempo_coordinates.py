from fractions import Fraction as F

import pytest

from test_song_import_score import beat, import_json, measure, raw_score
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected


@pytest.mark.parametrize('coordinate,quarter', [(1920, F(2)), (.5, F(1, 1920)), ([1, 2], F(1, 1920))])
def test_static_tempo_ticks_are_not_whole_note_durations(tmp_path, coordinate, quarter):
    source = raw_score([measure(beat())])
    source['parts'][0]['automations']['tempo'].append({'measure': 0, 'position': coordinate, 'bpm': 60})
    actual = import_json(tmp_path, source)
    assert actual['tempos'][1]['time'] == pytest.approx(float(quarter) / 2)
    duration = float(quarter) / 2 + 4 - float(quarter)
    assert actual['duration'] == pytest.approx(duration)
    independent = songsterr(source)
    assert independent.bars[0].tempos[quarter] == 60
    assert expected(independent, {'offset': 0, 'scale': 1})['score_duration'] == pytest.approx(duration)


def test_enabled_ramp_can_begin_and_end_within_a_measure(tmp_path):
    source = raw_score([measure(beat())])
    source['parts'][0]['automations'] = {'gradualTempo': True, 'tempo': [
        {'measure': 0, 'position': 0, 'bpm': 60},
        {'measure': 0, 'position': 960, 'bpm': 60},
        {'measure': 0, 'position': 2880, 'bpm': 120, 'linear': True},
    ]}
    actual = import_json(tmp_path, source)
    assert actual['duration'] == pytest.approx(2 + 2/3 + .5)
    assert songsterr(source).bars[0].tempos == {F(0): 60, F(1): 60, F(2): 90, F(3): 120}


def test_invalid_position_is_rejected_by_both_readers(tmp_path):
    source = raw_score([measure(beat())])
    source['parts'][0]['automations']['tempo'].append({'measure': 0, 'position': 4000, 'bpm': 60})
    with pytest.raises(Exception, match='tempo'):
        import_json(tmp_path, source)
    with pytest.raises(Exception, match='tempo'):
        songsterr(source)
