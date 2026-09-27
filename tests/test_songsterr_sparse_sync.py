"""Sparse entry evidence cannot be borrowed from unrelated later material."""
from copy import deepcopy

import numpy as np
import pytest
import soundfile as sf

from feedback_converter.song_import.recording_sync import (
    DT, MIDIS, _sparse_context, assess_features, features,
)


def truth(start=15.25, step=.25, duration=61.75, instrument='guitar'):
    # Independent ideal acoustic observations: fixed-pitch plateaus plus narrow
    # picked transients. These encode known attack/pitch truth, not decisions.
    rng = np.random.default_rng(932)
    events = [{'t': float(t), 'end': float(t + step * .85), 'midi': int(m), 'effects': {}}
              for t, m in zip(np.arange(start, duration - .25, step), rng.integers(40, 65, 500))]
    frames = np.arange(1 + int(duration / DT)) * DT
    pitch = np.zeros((len(frames), len(MIDIS)), dtype='float32')
    flux = np.ones((len(frames), 4), dtype='float32') * .15
    for event in events:
        mask = (frames >= event['t'] + .015) & (frames <= event['end'])
        pitch[mask, event['midi'] - 28] = 1
        flux += (3 * np.exp(-.5 * ((frames - event['t']) / .018) ** 2))[:, None]
    return [{'id': 'guitar', 'instrument': instrument, 'events': events}], pitch, flux, duration


@pytest.mark.parametrize('start,step', [(15.25, .25), (15.10, .30), (23.10, .30), (15.25, .5), (31.1, .3)])
def test_delayed_entries_near_different_boundaries_keep_their_timing(start, step):
    tracks, pitch, flux, duration = truth(start, step)
    before = deepcopy(tracks)
    result = assess_features(tracks, pitch, flux, duration)
    assert result['status'] == 'supported', result
    assert result['sparseWindows'] >= 1
    assert tracks == before
    for window in result['windows']:
        for row in window['tracks']:
            evidence = row.get('sparseEvidence')
            if evidence and evidence['status'] == 'supported':
                assert evidence['context']['attacks'] >= 6
                assert all(check['attacks'] == 2 for check in evidence['localChecks'])
                # The ordinary end-margin note is retained in the context.
                for n in tracks[0]['events']:
                    if window['end'] - .2 <= n['t'] < window['end']:
                        assert round(n['t'], 3) in {g['time'] for g in evidence['context']['groups']}


@pytest.mark.parametrize('count', [1, 2])
@pytest.mark.parametrize('shift', [-.5, -.25, .25, .5, 1.0])
def test_incorrect_opening_attacks_cannot_hide_in_correct_later_notes(count, shift):
    tracks, pitch, flux, duration = truth()
    for n in tracks[0]['events'][:count]:
        n['t'] += shift
        n['end'] += shift
    result = assess_features(tracks, pitch, flux, duration)
    assert result['status'] == 'inconclusive', result


@pytest.mark.parametrize('change', ['wrong_pitch', 'missing_intro', 'drift', 'whole_shift', 'no_pitch', 'no_attacks'])
def test_local_and_context_acoustic_evidence_are_both_required(change):
    tracks, pitch, flux, duration = truth()
    if change == 'wrong_pitch':
        for n in tracks[0]['events'][:2]:
            n['midi'] += 6
    elif change == 'missing_intro':
        pitch[:int(16 / DT)] = 0
        flux[:int(16 / DT)] = 0
    elif change in {'whole_shift', 'drift'}:
        for n in tracks[0]['events']:
            shift = .25 if change == 'whole_shift' else .8 * n['t'] / duration
            n['t'] += shift
            n['end'] += shift
    elif change == 'no_pitch':
        pitch[:] = 0
    else:
        flux[:] = 0
    assert assess_features(tracks, pitch, flux, duration)['status'] == 'inconclusive'


@pytest.mark.parametrize('effect', ['mt', 'bn', 'bnv', 'sl', 'slu', 'whammy', 'ho', 'po', 'ln',
    'hm', 'hp', 'hn', 'hps', 'harmonic_target', 'harmonic_changes', 'harmonic_alias',
    'pick_scrape_marks', 'slide_out', 'slide_out_marks', 'slide_in_marks', 'tr'])
def test_sparse_path_does_not_guess_unassessable_or_legato_attacks(effect):
    tracks, pitch, flux, duration = truth()
    tracks[0]['events'][0]['effects'][effect] = True
    result = assess_features(tracks, pitch, flux, duration)
    assert result['status'] == 'inconclusive', result


def test_a_mixed_chord_cannot_discard_its_unassessable_member():
    tracks, pitch, flux, duration = truth()
    tracks[0]['events'].append({**tracks[0]['events'][0], 'midi': None, 'effects': {'mt': True}})
    assert assess_features(tracks, pitch, flux, duration)['status'] == 'inconclusive'


def test_context_is_same_part_contiguous_and_selected_without_acoustic_hunting():
    tracks, pitch, flux, duration = truth()
    track = tracks[0]
    before = _sparse_context(track, pitch, flux, 0, 16, duration)
    assert before['status'] == 'supported'
    # Corrupt only the first context outside the sparse pair. More distant
    # material is still correct, but cannot replace the deterministic context.
    changed = deepcopy(track)
    for n in changed['events'][3:6]:
        n['midi'] += 7
    after = _sparse_context(changed, pitch, flux, 0, 16, duration)
    assert after['status'] == 'inconclusive'
    assert [g['time'] for g in before['context']['groups']] == [g['time'] for g in after['context']['groups']]
    assert all(c['status'] == 'supported' for c in after['localChecks'])
    # Another part's later events cannot supply the required six groups.
    short = {**track, 'events': track['events'][:3]}
    later = {**track, 'id': 'other', 'events': track['events'][3:]}
    assert assess_features([short, later], pitch, flux, duration)['status'] == 'inconclusive'


@pytest.mark.parametrize('change', ['long_gap', 'blocked_neighbor', 'one_attack', 'margin_only', 'unpitched'])
def test_insufficient_or_disconnected_material_remains_inconclusive(change):
    tracks, pitch, flux, duration = truth()
    t = tracks[0]
    if change == 'long_gap':
        t['events'] = t['events'][:3] + t['events'][15:]
    elif change == 'blocked_neighbor':
        t['events'][3]['effects']['whammy'] = True
    elif change == 'one_attack':
        t['events'] = t['events'][2:]
    elif change == 'margin_only':
        t['events'] = t['events'][3:]
        t['events'].insert(0, {**t['events'][0], 't': 15.9, 'end': 16.1})
    else:
        t['events'][0]['midi'] = None
    assert _sparse_context(t, pitch, flux, 0, 16, duration)['status'] == 'inconclusive'


def test_no_override_of_an_already_measured_mismatch_in_another_part():
    tracks, pitch, flux, duration = truth()
    unrelated = deepcopy(tracks[0])
    unrelated.update(id='unrelated', events=[{**n, 't': n['t'] - 8, 'end': n['end'] - 8}
                                            for n in unrelated['events'][:40]])
    result = assess_features(tracks + [unrelated], pitch, flux, duration)
    assert result['status'] == 'inconclusive'
    assert not any('sparseEvidence' in row for row in result['windows'][0]['tracks'])


def test_pair_checks_do_not_average_a_bad_pair_with_a_good_pair():
    tracks, pitch, flux, duration = truth(start=14.75)
    tracks[0]['events'][0]['midi'] += 8
    result = _sparse_context(tracks[0], pitch, flux, 0, 16, duration)
    assert result['status'] == 'inconclusive'
    assert len(result['localChecks']) >= 2
    assert result['localChecks'][0]['status'] == 'inconclusive'


def test_internal_rest_boundary_uses_the_same_rule():
    tracks, pitch, flux, duration = truth(start=0)
    tracks[0]['events'] = [n for n in tracks[0]['events'] if n['t'] < 8 or n['t'] >= 23.25]
    # 8-24 contains just the resumed part's entry; earlier/later ordinary windows
    # provide song-wide context. A rest is not a permission to move attacks.
    result = assess_features(tracks, pitch, flux, duration)
    assert result['status'] == 'supported', result
    assert result['sparseWindows'] >= 1


def test_repetitive_ambiguous_evidence_is_not_a_sparse_success():
    tracks, pitch, flux, duration = truth()
    pitch[:] = 0
    pitch[:, 12] = 1
    flux[:] = 1
    for n in tracks[0]['events']:
        n['midi'] = 40
    assert assess_features(tracks, pitch, flux, duration)['status'] == 'inconclusive'


def test_real_synthesized_plucks_with_delayed_entry(tmp_path):
    tracks, _, _, duration = truth(start=15.4)
    rate = 22050
    signal = np.zeros(round(rate * duration))
    for n in tracks[0]['events']:
        start = round(n['t'] * rate)
        length = min(round(.23 * rate), len(signal) - start)
        t = np.arange(length) / rate
        hz = 440 * 2 ** ((n['midi'] - 69) / 12)
        envelope = np.minimum(t / .002, 1) * np.exp(-t * 8)
        signal[start:start + length] += .1 * envelope * sum(np.sin(2 * np.pi * hz * h * t) / h for h in range(1, 6))
    wav = tmp_path / 'delayed-plucks.wav'
    sf.write(wav, signal, rate)
    pitch, flux, _ = features(wav)
    result = assess_features(tracks, pitch, flux, duration)
    assert result['status'] == 'supported', result
    assert result['sparseWindows'] == 1
