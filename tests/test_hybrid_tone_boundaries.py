"""Selection reservations must not extend the sound of a finished passage."""
from copy import deepcopy

import pytest

from test_song_import_score import beat, measure, raw_score
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.tone_timeline import hybrid, State
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_tones import expected, hybrid as verify_hybrid
from feedback_converter.song_import.verification import Check


def fixture(alignment=None, sound_changes=False):
    doc = raw_score([measure(beat()), measure(beat())])
    doc['tracks'].append({**deepcopy(doc['tracks'][0]), 'id': 1, 'instrumentId': 30})
    doc['parts'].append(deepcopy(doc['parts'][0]))
    if sound_changes:
        for part in doc['parts']:
            part.update(sounds=[{'instrumentId': 27, 'label': 'Clean'},
                                {'instrumentId': 30, 'label': 'Distortion'}],
                        trackAutomations={'trackSoundAutomations': [
                            {'measure': 0, 'position': 2880, 'soundId': 0}]})
    source = songsterr(doc)
    alignment = alignment or {'offset': 0, 'scale': 1}
    originals, parts = {}, {}
    for part in source.parts:
        tones, proof = expected(part.tone_source, source, alignment, 10)
        originals[part.id] = {'chart': {'notes': [], 'chords': [], 'tones': tones}, 'toneProof': proof}
        parts[part.id] = {'source': part}
    originals['0']['chart']['notes'] = [{'t': 0, 'sus': 1}, {'t': 2, 'sus': 1}]
    originals['1']['chart']['notes'] = [{'t': 1, 'sus': 1}]
    plan = {'mainTrackId': '0', 'mainEvents': [{'kind': 'notes', 'index': i} for i in (0, 1)],
            'passages': [{'trackId': '1', 'start': 2, 'end': 4, 'ownedStart': 0, 'ownedEnd': 8,
                          'recordingStart': 1, 'recordingEnd': 2,
                          'recordingOwnedStart': 0, 'recordingOwnedEnd': 4,
                          'events': [{'kind': 'notes', 'index': 0}]}]}
    return plan, originals, source, parts, alignment


def verify(plan, originals, source, parts, alignment, **kwargs):
    check = Check()
    tones = verify_hybrid(plan, parts, source, alignment, 10,
                          {tid: row['chart'] for tid, row in originals.items()}, check, **kwargs)
    return tones, check.errors


def test_reservation_before_and_after_passage_does_not_steal_main_sound():
    plan, originals, *args = fixture()
    before = deepcopy((plan, originals))
    tones = hybrid(plan, originals, 10)
    wanted, errors = verify(plan, originals, *args)
    assert not errors
    assert tones == wanted
    state = State(tones)
    assert state.at(0) == originals['0']['chart']['tones']['base']
    assert state.at(1) == originals['1']['chart']['tones']['base']
    assert state.at(2) == originals['0']['chart']['tones']['base']
    assert (plan, originals) == before


@pytest.mark.parametrize('side', ['main', 'donor'])
def test_real_sustain_overlap_still_fails_in_both_checkers(side):
    plan, originals, *args = fixture()
    originals['0' if side == 'main' else '1']['chart']['notes'][0]['sus'] += .05
    with pytest.raises(ImportFailure, match='incompatible sounds'):
        hybrid(plan, originals, 10)
    _, errors = verify(plan, originals, *args)
    assert any(e['code'] == 'tone_conflict' for e in errors)


def test_older_contract_retains_reservation_interpretation():
    plan, originals, *args = fixture()
    with pytest.raises(ImportFailure, match='incompatible sounds'):
        hybrid(plan, originals, 10, passage_boundaries=False)
    _, errors = verify(plan, originals, *args, passage_boundaries=False)
    assert errors


def test_complete_gesture_outside_reservation_keeps_its_sound():
    plan, originals, *args = fixture()
    p = plan['passages'][0]
    p.update(ownedStart=2.5, ownedEnd=3, recordingOwnedStart=1.25, recordingOwnedEnd=1.5)
    tones = hybrid(plan, originals, 10)
    wanted, errors = verify(plan, originals, *args)
    assert not errors and tones == wanted
    assert State(tones).at(1.75) == originals['1']['chart']['tones']['base']


def test_piecewise_clock_and_preparation_use_passage_not_reservation():
    alignment = {'mapping': 'piecewise-linear', 'anchors': [
        {'score': 0, 'audio': 2}, {'score': 1, 'audio': 3}, {'score': 2, 'audio': 5}, {'score': 4, 'audio': 7}]}
    plan, originals, *args = fixture(alignment)
    originals['0']['chart']['notes'] = [{'t': 2, 'sus': 1}, {'t': 5, 'sus': 1}]
    originals['1']['chart']['notes'] = [{'t': 3, 'sus': 2}]
    plan['passages'][0].update(recordingStart=3, recordingEnd=5, recordingOwnedStart=2, recordingOwnedEnd=7)
    tones = hybrid(plan, originals, 10)
    wanted, errors = verify(plan, originals, *args)
    assert not errors and tones == wanted
    assert [c['t'] for c in tones['changes']] == [3, 5]


def test_adjacent_passages_may_have_overlapping_reservations():
    plan, originals, *args = fixture()
    plan['mainEvents'] = plan['mainEvents'][:1]
    originals['1']['chart']['notes'].append({'t': 2, 'sus': 1})
    plan['passages'].append({**deepcopy(plan['passages'][0]), 'start': 4, 'end': 6,
                             'recordingStart': 2, 'recordingEnd': 3,
                             'events': [{'kind': 'notes', 'index': 1}]})
    tones = hybrid(plan, originals, 10)
    wanted, errors = verify(plan, originals, *args)
    assert not errors and tones == wanted
    assert [c['t'] for c in tones['changes']] == [1, 3]


def test_source_changes_during_donor_are_preserved_and_main_returns_in_current_state():
    plan, originals, *args = fixture(sound_changes=True)
    tones = hybrid(plan, originals, 10)
    wanted, errors = verify(plan, originals, *args)
    assert not errors and tones == wanted
    state = State(tones)
    assert state.at(1.5) == State(originals['1']['chart']['tones']).at(1.5)
    assert state.at(2) == State(originals['0']['chart']['tones']).at(2)
    assert state.at(2) != originals['0']['chart']['tones']['base']


def test_independent_checker_does_not_trust_exported_recording_bounds():
    plan, originals, *args = fixture()
    plan['passages'][0]['recordingEnd'] = 2.1
    with pytest.raises(ImportFailure, match='incompatible sounds'):
        hybrid(plan, originals, 10)
    wanted, errors = verify(plan, originals, *args)
    assert not errors
    assert [c['t'] for c in wanted['changes']] == [1, 2]
