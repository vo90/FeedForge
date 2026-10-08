"""Low bass can authorize an ending only with independently reproducible audio."""
from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pytest
import soundfile as sf
import yaml

from feedback_converter.song_import import low_bass_sync as low, recording_sync as rs
from feedback_converter.song_import import local_sync as ls, worker, audio
from feedback_converter.song_import.verification import verify_import

META = {'songId': '12', 'revisionId': '34', 'approval': 'approved'}
VIDEO = 'abcdefghijk'


def fixture(root, *, lowest=23, mode='aligned', strings=4, duration=48.25):
    root.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(19)
    frets = rng.integers(0, 5, 100).tolist()
    tuning = [lowest] + [33, 38, 43, 48, 53][:strings-1]
    raw = {'format': 'songsterr', 'songId': 12, 'revisionId': 34,
        'title': 'Low bass ending', 'artist': 'Synthetic',
        'tracks': [{'id': 0, 'name': 'Bass', 'instrumentId': 33, 'tuning': tuning[::-1]}],
        'parts': [{'strings': strings, 'frets': 24,
            'automations': {'tempo': [{'measure': 0, 'position': 0, 'bpm': 120}]},
            'measures': [{'signature': [4, 4], 'voices': [{'beats': [
                {'duration': [1, 4], 'notes': [{'string': strings-1, 'fret': f}]}
                for f in frets[i:i+4]]}]} for i in range(0, 100, 4)]}]}
    source = root/'source.json'
    source.write_text(json.dumps(raw), encoding='utf8')
    rate = 22050
    signal = np.zeros(round(rate*duration))
    for i, fret in enumerate(frets):
        at = i*.5 + (.3 if mode == 'delayed' else 0)
        t = np.arange(round(.32*rate))/rate
        hz = 440*2**((lowest+fret+(1 if mode == 'wrong_pitch' else 0)-69)/12)
        wave = sum(np.sin(2*np.pi*hz*h*t)/h for h in range(1, 6))
        if mode == 'noise':
            wave = rng.normal(0, .5, len(t))
        wave *= .15*np.minimum(1, t/.004)*np.exp(-t*7)
        start = round(at*rate)
        size = min(len(t), len(signal)-start)
        if size > 0:
            signal[start:start+size] += wave[:size]
    recording = root/'truth.wav'
    sf.write(recording, signal, rate, subtype='FLOAT')
    tracks = [{'id': 'bass', 'instrument': 'bass', 'events': [
        {'t': i*.5, 'end': (i+1)*.5, 'midi': lowest+f, 'effects': {}}
        for i, f in enumerate(frets)]}]
    return source, recording, tracks


@pytest.mark.parametrize('lowest', [21, 23, 24, 26])
def test_low_bass_recording_evidence_without_changing_legacy(tmp_path, lowest):
    _, recording, tracks = fixture(tmp_path, lowest=lowest)
    duration = sf.info(recording).duration
    before = rs.assess(tracks, recording, duration, 'map', clock_version=rs.PREVIOUS_VERSION)
    new = rs.assess(tracks, recording, duration, 'map', clock_version=rs.CUTOFF_VERSION)
    after = rs.assess(tracks, recording, duration, 'map', clock_version=rs.PREVIOUS_VERSION)
    assert before == after
    assert new['status'] == 'supported', new
    if before['status'] == 'supported':
        assert new == before  # Enough higher notes may already support D tuning.
    else:
        assert new['version'] == rs.CUTOFF_VERSION
        assert new['lowBassEvidence']['referenceHz'] == new['referenceEvidence']['referenceHz']
        assert not new['lowBassEvidence']['everyNoteVerified']
    assert np.array_equal(rs.MIDIS, np.arange(28, 97))


@pytest.mark.parametrize('mode', ['delayed', 'wrong_pitch', 'noise'])
def test_wrong_low_bass_recording_cannot_authorize_cutoff(tmp_path, mode):
    _, recording, tracks = fixture(tmp_path, mode=mode)
    report = rs.assess(tracks, recording, sf.info(recording).duration, 'map', clock_version=rs.CUTOFF_VERSION)
    assert report['status'] != 'supported'
    assert not any(w['status'] == 'supported' for w in report['lowBassEvidence']['windows'])


def test_no_low_material_keeps_exact_legacy_report(tmp_path):
    _, recording, tracks = fixture(tmp_path, lowest=40)
    duration = sf.info(recording).duration
    for old, new in [(rs.PREVIOUS_VERSION, rs.CUTOFF_VERSION), (rs.VERSION, rs.PADDING_VERSION)]:
        assert rs.assess(tracks, recording, duration, 'map', clock_version=old) == rs.assess(
            tracks, recording, duration, 'map', clock_version=new)


@pytest.mark.parametrize('reason', ['supported', 'mismatch', 'sparse', 'guitar', 'below_range', 'mute', 'bend'])
def test_no_extra_fft_or_authority_for_ineligible_cases(monkeypatch, reason):
    events = [{'t': i, 'end': i+.4, 'midi': 23, 'effects': {}} for i in range(12)]
    track = {'id': 'bass', 'instrument': 'bass', 'events': events}
    window = {'start': 0, 'end': 16, 'status': 'inconclusive'}
    base = {'status': 'inconclusive', 'windows': [window]}
    if reason == 'supported': base['status'] = 'supported'
    if reason == 'mismatch': window['status'] = 'suspected_mismatch'
    if reason == 'sparse': track['events'] = events[:5]
    if reason == 'guitar': track['instrument'] = 'guitar'
    if reason == 'below_range':
        for n in events: n['midi'] = 20
    if reason in ('mute', 'bend'):
        for n in events: n['effects'] = {'mt' if reason == 'mute' else 'bn': True}
    monkeypatch.setattr(rs, 'features', lambda *a, **kw: pytest.fail('Unexpected low-bass FFT'))
    assert low.supplement([track], None, 16, base) is base


def test_high_attacks_cannot_stand_in_for_low_bass(monkeypatch):
    events = [{'t': i*.5, 'end': i*.5+.3, 'midi': 23 if i < 6 else 40, 'effects': {}}
              for i in range(30)]
    tracks = [{'id': 'bass', 'instrument': 'bass', 'events': events}]
    base = {'status': 'inconclusive', 'windows': [{'start': 0, 'end': 16, 'status': 'inconclusive'}],
            'referenceEvidence': {'referenceHz': 443.0}}
    def features(*a, **kw):
        assert kw == {'reference_hz': 443.0, 'midi_min': 21}
        return None, None, None
    def assess(selected, *a, **kw):
        assert len(selected[0]['events']) == 6
        assert all(n['midi'] == 23 for n in selected[0]['events'])
        assert kw['unique_clock'] is True
        return {'windows': [{'start': 0, 'end': 16, 'status': 'inconclusive', 'parts': []}]}
    monkeypatch.setattr(rs, 'features', features)
    monkeypatch.setattr(ls, 'assess_features', assess)
    assert low.supplement(tracks, None, 16, base)['status'] == 'inconclusive'
    assert 'lowBassEvidence' not in base


def test_repeated_low_pattern_has_no_unique_clock():
    times = np.arange(.5, 15, .5)
    track = {'id': 'bass', 'instrument': 'bass', 'events': [
        {'t': t, 'end': t+.3, 'midi': 23, 'effects': {}} for t in times]}
    grid = np.arange(round(16/rs.DT)+1)*rs.DT
    pitch = np.zeros((len(grid), 76))
    flux = np.full((len(grid), 4), .1)
    for t in np.arange(-3, 19, .5):
        pitch[:, 2] += np.exp(-.5*((grid-t-.085)/.03)**2)
        flux[:, 0] += 5*np.exp(-.5*((grid-t)/.02)**2)
    result = ls.assess_features([track], pitch, flux, 16, midi_min=21, unique_clock=True)
    assert result['supportedWindows'] == 0


def run_worker(root, monkeypatch, *, strings=4, duration=48.25):
    source, recording, _ = fixture(root, strings=strings, duration=duration)
    monkeypatch.setattr(audio, '_public_url', lambda u: u)
    monkeypatch.setattr(audio, '_download_youtube', lambda *a, **kw: (
        recording, {'kind': 'youtube', 'videoId': VIDEO}))
    result = worker.run_import({'scorePath': str(source), 'metadata': META,
        'audio': {'kind': 'url', 'url': f'https://youtu.be/{VIDEO}'}, 'artworkLookup': False,
        'workDir': str(root/'work'), 'outputDir': str(root/'out'),
        'synchronization': {'version': 1, 'source': 'songsterr-video-points', **META,
            'videoId': VIDEO, 'status': 'done', 'feature': None, 'points': list(range(0, 52, 2))}})
    assert result['ok'], result
    assert result['verification']['status'] == 'passed'
    return source, result


@pytest.fixture(scope='module')
def package(tmp_path_factory):
    with pytest.MonkeyPatch.context() as patch:
        return run_worker(tmp_path_factory.mktemp('low-bass-worker'), patch)


def test_encoded_package_preserves_notes_and_diagnostic_limits(package):
    _, result = package
    assert result['coverage']['notes'] == 97
    assert result['recipe']['preservationContract'] >= 105
    assert result['recipe']['alignment']['endingPadding']['omittedNoteCount'] == 3
    assert any('Timing not fully verified' in w for w in result['warnings'])
    with ZipFile(result['stagingPath']) as archive:
        assert archive.testzip() is None
        manifest = yaml.safe_load(archive.read('manifest.yaml'))
        sync = json.loads(archive.read(manifest['song_import']['endingPaddingSyncFile']))
        assert sync['version'] == rs.PADDING_VERSION
        assert sync['outroSupported'] is True
        assert sync['lowBassEvidence']['windows']
        chart = json.loads(archive.read(manifest['arrangements'][0]['file']))
        assert all(n['t'] == pytest.approx(2+i*.5) for i, n in enumerate(chart['notes']))


@pytest.mark.parametrize('strings', [5, 6])
def test_extended_bass_imports_using_same_general_rule(tmp_path, monkeypatch, strings):
    run_worker(tmp_path, monkeypatch, strings=strings)


@pytest.mark.parametrize('duration', [48.0, 49.75])
def test_cutoff_only_and_padding_only_are_independently_verified(tmp_path, monkeypatch, duration):
    _, result = run_worker(tmp_path, monkeypatch, duration=duration)
    with ZipFile(result['stagingPath']) as archive:
        recipe = yaml.safe_load(archive.read('manifest.yaml'))['song_import']
        if duration == 48:
            assert 'recordingSyncFile' in recipe and 'endingPaddingSyncFile' not in recipe
            assert json.loads(archive.read(recipe['recordingSyncFile']))['version'] == rs.CUTOFF_VERSION
        else:
            assert 'endingPaddingSyncFile' in recipe and 'endingOmissionsFile' not in recipe
            assert json.loads(archive.read(recipe['endingPaddingSyncFile']))['version'] == rs.PADDING_VERSION


@pytest.mark.parametrize('fault', ['low_count', 'reference', 'status', 'contract'])
def test_independent_verifier_rejects_forged_low_bass_evidence(package, tmp_path, fault):
    source, result = package
    with ZipFile(result['stagingPath']) as archive:
        files = {n: archive.read(n) for n in archive.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml'])
    recipe = manifest['song_import']
    # Use the worker's retained applied alignment, including preparation and map.
    # The evidence location is next to output/work roots, not inside the job.
    evidence_root = source.parent/'song-import-evidence'
    record = json.loads((evidence_root/'records'/f"{result['evidence']['id']}.json").read_text())
    alignment = json.loads((evidence_root/'objects'/record['objects']['appliedAlignment']).read_text())
    stored = alignment['endingPaddingSync']
    if fault == 'low_count': stored['lowBassEvidence']['windows'][0]['parts'][0]['lowPitchGroups'] += 1
    if fault == 'reference': stored['lowBassEvidence']['referenceHz'] += 1
    if fault == 'status': stored['lowBassEvidence']['windows'][0]['status'] = 'inconclusive'
    if fault == 'contract': recipe['preservationContract'] = 104
    alignment['endingPadding']['syncEvidenceHash'] = rs.digest(stored)
    files[recipe['endingPaddingSyncFile']] = json.dumps(stored).encode()
    files[recipe['endingPaddingFile']] = json.dumps(alignment['endingPadding']).encode()
    recipe['alignment']['endingPadding'] = deepcopy(alignment['endingPadding'])
    omissions = json.loads(files[recipe['endingOmissionsFile']])
    omissions['syncEvidenceHash'] = rs.digest(stored)
    files[recipe['endingOmissionsFile']] = json.dumps(omissions).encode()
    files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    damaged = tmp_path/'damaged.feedpak'
    with ZipFile(damaged, 'w') as archive:
        for name, data in files.items(): archive.writestr(name, data)
    report = verify_import(source, damaged, alignment, META)
    assert report['status'] == 'failed', report
    if fault != 'contract':
        assert any('timing_assessment' in e['code'] for e in report['errors']), report
