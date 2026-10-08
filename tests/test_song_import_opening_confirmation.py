"""Confirm the applied map through encoding, without fitting a second map."""
from copy import deepcopy
import json
from zipfile import ZipFile

import numpy as np
import pytest
import soundfile as sf
import yaml

from feedback_converter.song_import import local_sync as ls, recording_sync as rs
from feedback_converter.song_import import audio, worker
from feedback_converter.song_import.verification import Check, verify_import
from test_song_import_local_sync import fixture


@pytest.mark.parametrize('candidate', [None, [], [True, 1.4], [float('nan'), 1.4],
    [float('inf'), 1.4], [.201, 1.4], [-.1, 1.4], [.8, 1.4]])
def test_confirmation_rejects_invalid_or_out_of_grid_candidates(candidate):
    assert ls.confirm(*fixture(), candidate)['status'] == 'inconclusive'


@pytest.mark.parametrize('damage', ['wrong_pitch', 'missing_fit', 'missing_held', 'no_attacks', 'competing'])
def test_confirmation_retains_all_acoustic_gates(damage):
    args = list(fixture())
    candidate = ls.propose(*args)['replacementBoundaries']
    pitch, flux = args[-2:]
    if damage == 'wrong_pitch':
        args[-2] = np.roll(pitch, 2, axis=1)
    elif damage in ('missing_fit', 'missing_held'):
        t = .2 if damage == 'missing_fit' else 1.9
        flux[max(0, round((t-.15)/rs.DT)):round((t+.15)/rs.DT)] = .1
    elif damage == 'no_attacks':
        flux[:] = .1
    else:
        delay = round(.2/rs.DT)
        pitch[delay:] += pitch[:-delay].copy()
        flux[delay:] += flux[:-delay].copy()
    assert ls.confirm(*args, candidate)['status'] == 'inconclusive'


def test_confirmation_keeps_source_selected_track_and_requires_near_best_support():
    args = list(fixture())
    candidate = ls.propose(*args)['replacementBoundaries']
    result = ls.confirm(*args, candidate)
    assert result['status'] == 'supported'
    assert result['replacementBoundaries'] == candidate
    # An unrelated good arrangement must not rescue the selected bad part.
    good = deepcopy(args[0][0]); good['id'] = 'z'
    args[0][0]['events'][0]['midi'] += 2
    args[0].append(good)
    result = ls.confirm(*args, candidate)
    assert result['trackId'] == 'lead'
    assert result['status'] == 'inconclusive'


def test_confirmation_rejects_a_candidate_that_passes_old_gates_but_is_not_near_best():
    result = ls.confirm(*fixture(), [.19, 1.4])
    assert result['alternativeRatio'] >= 1.15
    assert min(result['fitPitch'] + result['heldOutPitch']) >= .12
    assert min(result['fitAttack'] + result['heldOutAttack']) >= 1.3
    assert result['maxOnsetError'] <= .055
    assert result['candidateToBestRatio'] < .95
    assert result['status'] == 'inconclusive'


@pytest.mark.parametrize('field', ['candidateToBestRatio', 'fitAttack', 'heldOutAttack'])
@pytest.mark.parametrize('delta,accepted', [(1e-6, True), (1e-4, False)])
def test_v2_metric_rounding_is_bounded(field, delta, accepted):
    args = fixture()
    fresh = ls.confirm(*args, ls.propose(*args)['replacementBoundaries'])
    stored = deepcopy(fresh)
    if isinstance(stored[field], list):
        stored[field][0] += delta
    else:
        stored[field] += delta
    check = Check(); ls.compare_opening_evidence(fresh, stored, check)
    assert (not check.errors) == accepted


def recording_fixture(tmp_path, monkeypatch, lowest):
    frets = np.random.default_rng(19).integers(0, 5, 16).tolist()
    raw = {'format': 'songsterr', 'songId': 12, 'revisionId': 34,
        'title': 'Encoded opening', 'artist': 'Synthetic',
        'tracks': [{'id': 0, 'name': 'Bass', 'instrumentId': 33, 'tuning': [43, 38, 33, lowest]}],
        'parts': [{'strings': 4, 'frets': 24,
            'automations': {'tempo': [{'measure': 0, 'position': 0, 'bpm': 120}]},
            'measures': [{'signature': [4, 4], 'voices': [{'beats': [
                {'duration': [1, 4], 'notes': [{'string': 3, 'fret': f}]} for f in frets[i:i+4]]}]}
                for i in range(0, 16, 4)]}]}
    source = tmp_path/'source.json'; source.write_text(json.dumps(raw))
    rate = 22050; samples = np.zeros(rate*10)
    for i, fret in enumerate(frets):
        start = .2+i*.45 if i < 4 else 2+(i-4)*.5
        t = np.arange(round(.32*rate))/rate
        hz = 440*2**((lowest+fret-69)/12)
        wave = sum(np.sin(2*np.pi*hz*h*t)/h for h in range(1, 6))
        wave *= .15*np.minimum(1, t/.004)*np.exp(-t*7)
        at = round(start*rate); samples[at:at+len(wave)] += wave
    # Five milliseconds changes the encoded grid winner, not the performed notes.
    shift = round(.005*rate); first = samples[:2*rate].copy()
    samples[:2*rate] = 0; samples[shift:2*rate] = first[:-shift]
    recording = tmp_path/'recording.wav'; sf.write(recording, samples, rate, subtype='FLOAT')
    monkeypatch.setattr(audio, '_public_url', lambda url: url)
    monkeypatch.setattr(audio, '_download_youtube', lambda *a, **k:
        (recording, {'kind': 'youtube', 'videoId': 'abcdefghijk'}))
    return source, {'scorePath': str(source), 'audio': {'kind': 'url', 'url': 'https://youtu.be/abcdefghijk'},
        'artworkLookup': False, 'metadata': {'songId': '12', 'revisionId': '34', 'approval': 'approved'},
        'synchronization': {'version': 1, 'source': 'songsterr-video-points', 'songId': '12',
            'revisionId': '34', 'videoId': 'abcdefghijk', 'status': 'done', 'feature': None,
            'points': [-.2, 2, 4, 6, 8]},
        'workDir': str(tmp_path/'work'), 'outputDir': str(tmp_path/'out')}


@pytest.mark.parametrize('lowest', [28, 40])
def test_real_encoding_preserves_initial_candidate_and_archive_verifies(tmp_path, monkeypatch, lowest):
    source, request = recording_fixture(tmp_path, monkeypatch, lowest)
    initial, encoded_winners = [], []
    propose, confirm = ls.propose, ls.confirm
    def trace_initial(*args):
        result = propose(*args); initial.append(result); return result
    def trace_confirm(*args):
        encoded_winners.append(propose(*args[:-1]))
        return confirm(*args)
    monkeypatch.setattr(ls, 'propose', trace_initial)
    monkeypatch.setattr(ls, 'confirm', trace_confirm)
    result = worker.run_import(request)
    (tmp_path/'result.json').write_text(json.dumps(result, indent=2))
    assert result['ok'], result
    assert result['verification']['status'] == 'passed'
    receipt = result['alignment']['openingRepair']
    assert receipt['version'] == ls.CONFIRMED_REPAIR
    assert result['recipe']['preservationContract'] >= 106
    assert receipt['replacementBoundaries'] == initial[0]['replacementBoundaries']
    assert encoded_winners[0]['replacementBoundaries'] != receipt['replacementBoundaries']
    assert receipt['candidateToBestRatio'] >= .95
    assert min(receipt['fitAttack'] + receipt['heldOutAttack']) >= 1.3
    evidence = tmp_path/'song-import-evidence'
    record = json.loads((evidence/'records'/f"{result['evidence']['id']}.json").read_text())
    alignment = json.loads((evidence/'objects'/record['objects']['appliedAlignment']).read_text())
    assert alignment['sourceTiming']['points'] == [-.2, 2, 4, 6, 8]
    offset = result['verification']['preparationSeconds']
    assert [p['audio']-offset for p in alignment['anchors'][2:]] == pytest.approx([4, 6, 8])
    assert verify_import(source, result['stagingPath'], alignment)['status'] == 'passed'
    with ZipFile(result['stagingPath']) as z:
        original = {n: z.read(n) for n in z.namelist()}
    for fault in ('candidate', 'score', 'attack', 'hash', 'later_anchor', 'contract'):
        files = dict(original); changed = deepcopy(alignment)
        manifest = yaml.safe_load(files['manifest.yaml'])
        if fault == 'candidate': changed['openingRepair']['replacementBoundaries'][0] += .1
        elif fault == 'score': changed['openingRepair']['candidateToBestRatio'] += .01
        elif fault == 'attack': changed['openingRepair']['heldOutAttack'][0] += .1
        elif fault == 'hash': changed['openingRepair']['audioSha256'] = 'forged'
        elif fault == 'later_anchor': changed['anchors'][2]['audio'] += .1
        else:
            # The receipt cannot be smuggled into the historical verification path.
            manifest['song_import']['preservationContract'] = 105
            files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
        files[manifest['song_import']['openingRepairFile']] = json.dumps(changed['openingRepair']).encode()
        archive = tmp_path/(fault+'.feedpak')
        with ZipFile(archive, 'w') as z:
            for name, data in files.items(): z.writestr(name, data)
        assert verify_import(source, archive, changed)['status'] == 'failed', fault
