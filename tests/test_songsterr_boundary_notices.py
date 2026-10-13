"""Boundary notices describe recorded beat adjustments without changing music."""
from copy import deepcopy

import pytest

from feedback_converter.song_import.compatibility import new_report
from feedback_converter.song_import.measure_boundaries import report_findings, warning_summary
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from test_song_import_score import beat, measure, raw_score


PREFIX = 'Some overfilled tab bars use Songsterr playback boundaries: '
SUFFIX = '. Original tab retained.'
SHORTENED = 'overflowing beat durations are shortened'
OMITTED = 'beats outside their bar are omitted'


@pytest.mark.parametrize('actions,body', [
    ([], None), (['clip'], SHORTENED), (['duration_cap'], SHORTENED),
    (['clip', 'duration_cap'], SHORTENED), (['omit'], OMITTED),
    (['clip', 'omit'], SHORTENED + ' and ' + OMITTED),
    (['duration_cap', 'omit'], SHORTENED + ' and ' + OMITTED),
    (['omit', 'clip', 'duration_cap', 'clip'], SHORTENED + ' and ' + OMITTED),
])
def test_summary_describes_only_present_operations(actions, body):
    rows = [{'action': action} for action in actions]
    original = deepcopy(rows)
    assert warning_summary(rows) == (PREFIX + body + SUFFIX if body else None)
    assert rows == original


def test_unrecognized_future_action_keeps_a_conservative_notice():
    assert warning_summary([{'action': 'future-boundary-operation'}]) == (
        'Some tab beats use Songsterr playback boundaries. See import details '
        'for the recorded timing adjustments. Original tab retained.')


@pytest.mark.parametrize('kind,actions,body', [
    ('clip', ['clip'], SHORTENED), ('duration_cap', ['duration_cap'], SHORTENED),
    ('omit-attack', ['omit'], OMITTED), ('omit-rest', ['omit'], OMITTED),
    ('omit-tie', ['omit'], OMITTED), ('mixed', ['clip', 'omit'], SHORTENED + ' and ' + OMITTED),
    ('cap-and-omit', ['duration_cap', 'omit'], SHORTENED + ' and ' + OMITTED),
])
def test_source_warning_and_findings_follow_actual_rows(kind, actions, body):
    if kind == 'clip':
        beats = [beat(3, duration=(3, 4)), beat(5, duration=(1, 2))]
    elif kind == 'duration_cap':
        beats = [beat(3, duration=(2, 1))]
    elif kind == 'mixed':
        beats = [beat(3, duration=(3, 4)), beat(5, duration=(1, 2)), beat(7, duration=(1, 4))]
    elif kind == 'cap-and-omit':
        beats = [beat(3, duration=(2, 1)), beat(7, duration=(1, 4))]
    else:
        tail = ({'duration': [1, 4], 'notes': [{'rest': True}]} if kind == 'omit-rest'
                else beat(3, duration=(1, 4), **({'tie': True} if kind == 'omit-tie' else {})))
        beats = [beat(3), tail]
    raw = raw_score([measure(*beats), measure(beat(9))])
    original = deepcopy(raw)
    score = parse(raw)
    rows = score.source['measureBoundaryAdjustments']
    assert [row['action'] for row in rows] == actions
    assert [warning for warning in score.warnings if 'playback boundaries' in warning] == [PREFIX + body + SUFFIX]
    assert rows == songsterr(raw).boundary_adjustments
    before_rows = deepcopy(rows)
    p = render(score)
    v = expected(songsterr(raw), {'offset': 0, 'scale': 1})
    actual = p['tracks'][0]['notes']
    wanted = [row['note'] for row in v['parts'][0]['notes']]
    assert [(n['t'], n['f'], n['sus']) for n in actual] == [(n['t'], n['f'], n['sus']) for n in wanted]
    assert actual[-1]['t'] == 2 and actual[-1]['f'] == 9
    report = new_report()
    report_findings(p, report)
    findings = report['findings']
    assert len(findings) == len(rows)
    for row, finding in zip(rows, findings):
        assert finding['value'] == row
        assert finding['category'] == 'source_interpretation'
        assert finding['impact'] == ('gameplay_omission' if row['action'] == 'omit' else 'display_or_expression')
        assert 'attack' not in finding['message']
        assert ('This beat starts outside its written bar' in finding['message']) == (row['action'] == 'omit')
    assert raw == original and p['sourceScore']['document'] == original
    assert rows == before_rows


def test_ordinary_score_gets_no_boundary_notice():
    score = parse(raw_score([measure(beat(3))]))
    assert not score.source.get('measureBoundaryAdjustments')
    assert not any('playback boundaries' in warning for warning in score.warnings)
