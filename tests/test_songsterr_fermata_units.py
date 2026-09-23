from copy import deepcopy
from fractions import Fraction as F

import pytest

from test_song_import_score import beat, import_json, measure, raw_score
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected


@pytest.mark.parametrize('unit,dotted,initial,held,restored', [
    (2, False, 122, 98, 122), (8, False, 30.5, 24.5, 30.5),
    (4, True, 91.5, 49, 61), (8, True, 45.75, 24.5, 30.5),
])
def test_fermata_rounds_authored_bpm_before_normalizing_its_unit(tmp_path, unit, dotted, initial, held, restored):
    source = raw_score([measure(beat())])
    auto = source['parts'][0]['automations']
    auto['tempo'][0].update(bpm=61, type=unit, dotted=dotted)
    auto['fermata'] = [{'measure': 0, 'position': 1920, 'type': 'short', 'length': 0}]
    original = deepcopy(source)
    performance = import_json(tmp_path, source)
    rates = [e['bpm'] for e in performance['tempos']]
    assert rates == [initial, held, restored]
    duration = 120 / initial + 60 / held + 60 / restored
    assert performance['duration'] == pytest.approx(duration)
    independent = songsterr(source)
    assert list(independent.bars[0].tempos.values()) == [F(str(initial)), F(str(held)), F(str(restored))]
    assert expected(independent, {'offset': 0, 'scale': 1})['score_duration'] == pytest.approx(duration)
    assert source == original


def test_tempo_unit_is_inherited_across_measures_and_restored_at_next_bar(tmp_path):
    source = raw_score([measure(beat()), measure(beat()), measure(beat())])
    auto = source['parts'][0]['automations']
    auto['tempo'][0].update(bpm=61, type=8)
    auto['fermata'] = [{'measure': 1, 'position': 2880, 'type': 'short', 'length': 0}]
    assert songsterr(source).bars[1].tempos[F(3)] == F(49, 2)
    assert songsterr(source).bars[2].tempos[F(0)] == F(61, 2)
    assert import_json(tmp_path, source)['tempos'][-1]['bpm'] == 30.5
