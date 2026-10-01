"""Known-answer mixed coordinate curves, independent archives and regressions."""
from copy import deepcopy
from fractions import Fraction as F

import pytest

from feedback_converter.song_import import ScoreImportError
from feedback_converter.song_import.verify_source import songsterr as read_source
from feedback_converter.song_import.verify_timeline import expected
from test_song_import_score import beat, import_json, measure, raw_score
from test_song_import_verification import example, verify


@pytest.mark.parametrize('points,answer', [
    ([{'position': 0, 'tone': 0}, {'position': 46, 'precisePosition': 77, 'tone': 100},
      {'position': 60, 'tone': 100}], [(F(0), 0), (F(77, 100), 2), (F(1), 2)]),
    ([{'position': 0, 'tone': 0}, {'position': 12, 'precisePosition': 20, 'tone': 150},
      {'position': 45, 'precisePosition': 75, 'tone': 150}, {'position': 60, 'tone': 0}],
     [(F(0), 0), (F(1, 5), 3), (F(3, 4), 3), (F(1), 0)]),
    ([{'position': 0, 'tone': 0}, {'position': 1, 'tone': 50},
      {'position': 46, 'precisePosition': 77, 'tone': 100}, {'position': 60, 'tone': 0}],
     [(F(0), 0), (F(1, 50), 1), (F(77, 100), 2), (F(1), 0)]),
    ([{'position': 0, 'precisePosition': 0, 'tone': 0}, {'position': 1.5, 'tone': 50},
      {'position': 60, 'tone': 0}], [(F(0), 0), (F(3, 100), 1), (F(1), 0)]),
    ([{'position': 0, 'tone': 0}, {'position': 1, 'tone': 50},
      {'position': 1, 'precisePosition': 2, 'tone': 100}, {'position': 60, 'tone': 0}],
     [(F(0), 0), (F(1, 50), 2), (F(1), 0)]),
    ([{'position': 0, 'tone': 0}, {'position': 1, 'tone': 50}, {'position': 60, 'tone': 0}],
     [(F(0), 0), (F(1, 60), 1), (F(1), 0)]),
])
def test_mixed_bend_coordinates_match_source_player(tmp_path, points, answer):
    source = raw_score([measure(beat(bend={'points': points}))])
    before = deepcopy(source)
    note = import_json(tmp_path, source)['tracks'][0]['notes'][0]
    assert [p['t'] for p in note['bnv']] == pytest.approx([float(p * 2) for p, _ in answer], abs=1e-9)
    assert [p['v'] for p in note['bnv']] == [v for _, v in answer]
    assert read_source(source).parts[0].bars[0][0].bends == answer
    assert source == before


@pytest.mark.parametrize('points', [
    [{'position': 0, 'precisePosition': 90, 'tone': 0}, {'position': 30, 'tone': 50}],
    [{'position': 30, 'precisePosition': 10, 'tone': 0}, {'position': 20, 'tone': 50}],
    [{'position': 0, 'tone': 0}, {'position': 60, 'precisePosition': 101, 'tone': 50}],
    [{'position': -1, 'tone': 0}, {'position': 60, 'precisePosition': 100, 'tone': 50}],
    [{'position': 0, 'tone': 0}, {'position': 60, 'precisePosition': None, 'tone': 50}],
    [{'position': 0, 'tone': 0}, {'position': 60, 'precisePosition': True, 'tone': 50}],
    [{'position': 0, 'tone': 0}, {'position': 60, 'precisePosition': float('nan'), 'tone': 50}],
    [{'position': 0, 'precisePosition': 0, 'tone': 0}, {'position': 60, 'tone': -50}],
    [{'position': 0, 'precisePosition': 0, 'tone': 0}, {'position': 60, 'tone': 450}],
])
def test_invalid_mixed_curves_are_not_sorted_clamped_or_repaired(tmp_path, points):
    source = raw_score([measure(beat(bend={'points': points}))])
    with pytest.raises(ScoreImportError):
        import_json(tmp_path, source)
    with pytest.raises(ValueError):
        read_source(source)


def test_mixed_curve_survives_chords_ties_repeats_and_tempo_changes(tmp_path):
    mixed = [{'position': 0, 'tone': 0}, {'position': 1, 'tone': 50},
             {'position': 46, 'precisePosition': 77, 'tone': 100}, {'position': 60, 'tone': 100}]
    first = beat(duration=(1, 2), bend={'points': mixed})
    first['notes'].append({'string': 1, 'fret': 5})
    source = raw_score([measure(first, beat(duration=(1, 2), tie=True), repeatStart=True),
                        measure(beat(bend={'points': mixed}), repeat=2)])
    source['parts'][0]['automations']['tempo'].append({'measure': 1, 'position': [0, 1], 'bpm': 90, 'type': 4})
    precise = deepcopy(source)
    for m in precise['parts'][0]['measures']:
        for b in m['voices'][0]['beats']:
            for n in b['notes']:
                for p in n.get('bend', {}).get('points', []):
                    p.setdefault('precisePosition', {0: 0, 1: 2, 46: 77, 60: 100}[p['position']])
    before = deepcopy(source)
    got, wanted = import_json(tmp_path, source), import_json(tmp_path, precise)
    for key in ('notes', 'chords', 'notation'):
        assert got['tracks'][0][key] == wanted['tracks'][0][key]
    mapping = {'offset': 2, 'scale': 1.25}
    assert expected(read_source(source), mapping) == expected(read_source(precise), mapping)
    assert source == before


@pytest.mark.parametrize('corruption', [None, 'unrounded_legacy', 'pitch', 'missing_point'])
def test_independent_archive_check_rejects_wrong_mixed_bend(tmp_path, corruption):
    source, package = example()
    source['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['bend'] = {'points': [
        {'position': 0, 'tone': 0}, {'position': 1, 'tone': 50},
        {'position': 46, 'precisePosition': 77, 'tone': 100}, {'position': 60, 'tone': 0}]}
    # Half-second source note: 2% is .01s and 77% is .385s from attack.
    curve = [{'t': 0, 'v': 0}, {'t': .01, 'v': 1}, {'t': .385, 'v': 2}, {'t': .5, 'v': 0}]
    package['chart.json']['notes'][0].update(bn=2, bnv=curve)
    if corruption == 'unrounded_legacy': curve[1]['t'] = .5 / 60
    elif corruption == 'pitch': curve[1]['v'] = .5
    elif corruption == 'missing_point': del curve[1]
    report = verify(tmp_path, source, package)
    assert report['status'] == ('passed' if corruption is None else 'failed'), report
