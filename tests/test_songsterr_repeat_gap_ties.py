from copy import deepcopy
import json
from pathlib import Path

import pytest

from test_song_import_score import beat, measure, raw_score
from test_songsterr_repeat_ties import both

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/songsterr_repeat_gap_reference.json').read_text())


def flatten(actual, independent):
    def simple(n):
        return {k: n[k] for k in ('t', 's', 'f', 'sus')}
    produced = [simple(n) for t in actual['tracks'] for n in t['notes']]
    produced += [simple({**n, 't': c['t']}) for t in actual['tracks'] for c in t['chords'] for n in c['notes']]
    verified = [simple(n['note']) for p in independent['parts'] for n in p['notes']]
    key = lambda n: (n['t'], n['s'])
    return sorted(produced, key=key), sorted(verified, key=key)


@pytest.mark.parametrize('case', FIXTURE['cases'], ids=lambda c: c['id'] + '-' + c['profile'])
def test_unfilled_repeat_ties_match_native_and_written_out_repetition(case):
    assert FIXTURE['referenceSha256'] == '4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    doc = raw_score([])
    doc['parts'][0] = deepcopy(case['input'])
    doc['tracks'][0].update(instrumentId=case['input']['instrumentId'], tuning=case['input']['tuning'])
    before = deepcopy(doc)
    actual, independent = both(doc)
    produced, verified = flatten(actual, independent)
    native = sorted([{'s': len(case['input']['tuning']) - 1 - n['string'], 'f': n['fret'],
                      't': n['attackTick'] / case['tpqn'] / 2,
                      'sus': (n['endTick'] + 1 - n['attackTick']) / case['tpqn'] / 2}
                     for n in case['expected']], key=lambda n: (n['t'], n['s']))
    assert produced == verified == native
    expanded = deepcopy(doc)
    measures = expanded['parts'][0]['measures']
    entrance = measures.pop()
    del entrance['repeatStart'], entrance['repeat']
    measures.extend(deepcopy(entrance) for _ in range(case['repeats']))
    assert flatten(*both(expanded)) == (produced, verified)
    assert doc == before
    # The written source still ends early. Only the performed sustain carries
    # through unfilled time; no notation duration is rewritten to fill the bar.
    written = actual['tracks'][0]['notation']['measures'][1]
    beats = written['staves']['staff']['voices'][0]['beats']
    assert beats[-1]['end_time'] < 4


def test_unfilled_repeat_plain_tie_keeps_unique_boundary_target():
    entrance = measure(beat(3, duration=(1, 4), tie=True), beat(4, duration=(1, 4)), repeatStart=True, repeat=2)
    doc = raw_score([measure(beat(3)), entrance])
    original = deepcopy(doc)
    actual, independent = both(doc)
    notes, verified = flatten(actual, independent)
    assert notes == verified == [{'t': 0, 's': 5, 'f': 3, 'sus': 2.5},
                                 {'t': 2.5, 's': 5, 'f': 4, 'sus': 2},
                                 {'t': 4.5, 's': 5, 'f': 4, 'sus': .5}]
    assert actual['plainTieIdentityEvidence'] == independent['plain_tie_identities']
    row, = actual['plainTieIdentityEvidence']
    assert (row['occurrence'], row['attack'], row['start'], row['end']) == (3, 2.5, 4, 4.5)
    assert row['authored'] == {'fret': 3} and row['used'] == {'fret': 4}
    assert row['originSourceId'] == actual['tracks'][0]['notes'][1]['source_ids'][0]
    assert doc == original


@pytest.mark.parametrize('fault', ['rest', 'other_voice', 'missing_origin', 'late_tie', 'slide', 'hopo'])
def test_unfilled_repeat_keeps_ambiguous_or_interrupted_links_blocked(fault):
    entrance = measure(beat(3, duration=(1, 4), tie=True), beat(3, duration=(1, 4)), repeatStart=True, repeat=2)
    doc = raw_score([measure(beat(3)), entrance])
    tail = entrance['voices'][0]['beats'][1]
    if fault == 'rest': entrance['voices'][0]['beats'].append({'duration': [1, 8], 'notes': [{'rest': True}]})
    elif fault == 'other_voice':
        tail['notes'] = [{'rest': True}]
        entrance['voices'].append({'beats': [beat(3, duration=(1, 2))]})
    elif fault == 'missing_origin': doc['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'] = [{'rest': True}]
    elif fault == 'late_tie': entrance['voices'][0]['beats'].insert(0, {'duration': [1, 8], 'notes': [{'rest': True}]})
    elif fault == 'slide': tail['notes'][0]['slide'] = 'shift'
    elif fault == 'hopo': tail['notes'][0]['hp'] = True
    from feedback_converter.song_import.songsterr import parse
    from feedback_converter.song_import.timeline import render
    from feedback_converter.song_import.verify_source import songsterr
    from feedback_converter.song_import.verify_timeline import expected
    for convert in (lambda: render(parse(doc)), lambda: expected(songsterr(doc), {'offset': 0, 'scale': 1})):
        with pytest.raises(ValueError): convert()


def test_rest_in_another_voice_does_not_interrupt_tied_string():
    doc = raw_score([measure(beat(5)), measure(beat(5, duration=(1, 2), tie=True), repeatStart=True, repeat=3)])
    for m in doc['parts'][0]['measures']:
        m['voices'].append({'beats': [{'duration': [1, 1], 'notes': [{'rest': True}]}]})
    notes, verified = flatten(*both(doc))
    assert notes == verified == [{'t': 0, 's': 5, 'f': 5, 'sus': 7}]


def test_reattack_before_gap_remains_a_separate_attack():
    doc = raw_score([measure(beat(3)), measure(beat(3, duration=(1, 2), tie=True), beat(3, duration=(1, 4)), repeatStart=True, repeat=2)])
    notes, verified = flatten(*both(doc))
    assert notes == verified == [{'t': 0, 's': 5, 'f': 3, 'sus': 3},
                                 {'t': 3, 's': 5, 'f': 3, 'sus': 2},
                                 {'t': 5, 's': 5, 'f': 3, 'sus': .5}]
