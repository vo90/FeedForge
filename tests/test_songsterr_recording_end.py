"""Ending omissions require real audio evidence and independent source checks."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import zipfile

import numpy as np
import pytest
import soundfile as sf
import yaml

from feedback_converter.song_import.audio import ImportFailure, prepare_audio
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.ending_cutoff import authorize, allowed, mapped_tracks
from feedback_converter.song_import.recording_sync import assess_features, features, digest
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.synchronization import align_from_songsterr
from feedback_converter.song_import.verification import verify_import

META = {'songId': '12', 'revisionId': '34', 'approval': 'approved'}
VIDEO = 'abcdefghijk'


@pytest.fixture(scope='module')
def recording(tmp_path_factory):
    root = tmp_path_factory.mktemp('recording-end')
    rng = np.random.default_rng(761)
    frets = rng.integers(0, 13, 208).tolist()
    measures = [{'signature': [4, 4], 'voices': [{'beats': [
        {'duration': [1, 8], 'notes': [{'string': 5, 'fret': fret}]}
        for fret in frets[i:i + 8]]}]} for i in range(0, len(frets), 8)]
    raw = {'format': 'songsterr', 'songId': 12, 'revisionId': 34, 'title': 'Ending', 'artist': 'Test',
           'tracks': [{'id': 0, 'name': 'Guitar', 'instrumentId': 29, 'tuning': [64, 59, 55, 50, 45, 40]}],
           'parts': [{'measures': measures, 'automations': {'tempo': [
               {'measure': 0, 'position': [0, 1], 'bpm': 120, 'type': 4}]}}]}
    score = root / 'source.json'
    score.write_text(json.dumps(raw), encoding='utf-8')
    performance = load_performance(score, META)
    # Synthesized plucked notes provide known, exact attack/pitch truth. The
    # recording ends midway through the final measure, before two attacks.
    rate, duration = 22050, 51.5
    signal = np.zeros(round(rate * duration))
    for i, fret in enumerate(frets):
        start = round(i * .25 * rate)
        length = min(round(.23 * rate), len(signal) - start)
        if length <= 0:
            continue
        t = np.arange(length) / rate
        hz = 440 * 2 ** ((40 + fret - 69) / 12)
        envelope = np.minimum(t / .002, 1) * np.exp(-t * 8)
        signal[start:start + length] += .15 * envelope * sum(np.sin(2 * np.pi * hz * h * t) / h for h in range(1, 6))
    wav = root / 'truth.wav'
    sf.write(wav, signal, rate)
    media = root / 'media'
    media.mkdir()
    audio = prepare_audio({'kind': 'file', 'path': str(wav)}, media)
    audio['source'].update(kind='youtube', videoId=VIDEO)
    sync = {'version': 1, 'source': 'songsterr-video-points', **META, 'videoId': VIDEO,
            'status': 'done', 'feature': None, 'points': list(range(0, 53, 2))}
    alignment = align_from_songsterr(performance, audio, sync, META, allow_ending_candidate=True)
    return root, score, performance, audio, sync, alignment


def test_source_map_alone_cannot_authorize_omissions(recording, tmp_path):
    _, source, performance, audio, _, alignment = recording
    assert alignment['status'] == 'needs_ending_check'
    assert not allowed(alignment, audio['duration'])
    with pytest.raises(ImportFailure, match='synchronization'):
        build_feedpak(performance, audio, alignment, tmp_path, output_dir=tmp_path / 'out', source_path=source)


def test_preparation_does_not_move_the_cutoff_evidence_windows(recording,tmp_path):
    from feedback_converter.song_import.preparation import finalize
    _,source,performance,audio,_,candidate=recording
    original=authorize(performance,audio,deepcopy(candidate))
    prepared,alignment=finalize(performance,audio,original,tmp_path)
    assert alignment['preparation']['seconds']==2
    assert alignment['recordingSync']['analysisOriginSeconds']==2
    assert alignment['recordingSync']['status']=='supported'
    assert alignment['recordingSync']['windowCount']==original['recordingSync']['windowCount']
    result=build_feedpak(performance,prepared,alignment,tmp_path,output_dir=tmp_path/'out',source_path=source,
        compatibility=performance['compatibilityReport'],recipe={'preservationContract':35,'audioSource':audio['source'],
        'alignment':{'provenance':alignment['provenance']},'preparation':alignment['preparation']})
    verified=verify_import(source,Path(result['stagingPath']),alignment,META)
    assert verified['status']=='passed',verified
    assert verified['adjustments']['omittedEndingNotes']==2


@pytest.mark.parametrize('duration', [49.9, 40])
def test_larger_missing_ending_requires_acoustic_authorization(recording, duration):
    _, _, performance, audio, sync, _ = recording
    candidate = align_from_songsterr(performance, {**audio, 'duration': duration}, sync, META, allow_ending_candidate=True)
    assert candidate['status'] == 'needs_ending_check'
    assert not allowed(candidate, duration)
    with pytest.raises(ImportFailure):
        align_from_songsterr(performance, {**audio, 'duration': duration}, sync, META)


@pytest.mark.parametrize('change', ['shift_left', 'shift_right', 'drift', 'wrong_pitch', 'silence', 'sparse'])
def test_audio_guard_refuses_known_bad_or_inconclusive_matches(recording, change):
    _, _, performance, audio, _, candidate = recording
    pitch, flux, _ = features(audio['path'])
    tracks = mapped_tracks(performance, candidate)
    if change == 'silence':
        pitch[:] = 0
        flux[:] = 0
    else:
        for track in tracks:
            if change == 'sparse':
                track['events'] = track['events'][::40]
            for note in track['events']:
                shift = {'shift_left': -.25, 'shift_right': .25}.get(change, 0)
                if change == 'drift':
                    shift = .8 * note['t'] / audio['duration']
                note['t'] += shift
                note['end'] += shift
                if change == 'wrong_pitch':
                    note['midi'] += 6
    report = assess_features(tracks, pitch, flux, audio['duration'])
    assert report['status'] == 'inconclusive', report
    assert not report['everyNoteVerified'] and not report['calibratedProbability']


@pytest.fixture(scope='module')
def completed(recording):
    root, source, performance, audio, _, candidate = recording
    before = deepcopy(performance)
    alignment = authorize(performance, audio, deepcopy(candidate))
    job = root / 'job'
    job.mkdir()
    result = build_feedpak(performance, audio, alignment, job, output_dir=root / 'out', source_path=source,
                          compatibility=performance['compatibilityReport'], recipe={'preservationContract': 11})
    assert performance == before
    return source, performance, audio, alignment, Path(result['stagingPath'])


def test_correct_audio_cuts_only_the_two_late_attacks_and_retains_source(completed):
    source, performance, audio, alignment, archive = completed
    report = verify_import(source, archive, alignment, META)
    assert report['status'] == 'passed', report
    assert report['adjustments']['omittedEndingNotes'] == 2
    assert report['counts']['expectedPlayableNotes'] == 206
    assert report['timing']['independentAudioMatchAssessed']
    with zipfile.ZipFile(archive) as z:
        manifest = yaml.safe_load(z.read('manifest.yaml'))
        assert z.read(manifest['song_import']['sourceFile']) == source.read_bytes()
        chart = json.loads(z.read(manifest['arrangements'][0]['file']))
        assert len(chart['notes']) == 206
        assert all(n['t'] < audio['duration'] and n['t'] + n['sus'] <= audio['duration'] for n in chart['notes'])
        original = performance['tracks'][0]['notes'][:206]
        assert [(n['t'], n['sus'], n['f'], n['s']) for n in chart['notes']] == [(n['t'], n['sus'], n['f'], n['s']) for n in original]
        ledger = json.loads(z.read(manifest['song_import']['endingOmissionsFile']))
        assert [n['audioStart'] for n in ledger['notes']] == [51.5, 51.75]


@pytest.mark.parametrize('change', ['early_note', 'missing_note', 'false_ledger', 'missing_evidence', 'wrong_map', 'old_contract', 'forged_pass'])
def test_verifier_cannot_be_bypassed_by_modified_notes_or_forged_pass(completed, tmp_path, change):
    source, _, _, original_alignment, archive = completed
    alignment = deepcopy(original_alignment)
    with zipfile.ZipFile(archive) as z:
        files = {n: z.read(n) for n in z.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml'])
    recipe = manifest['song_import']
    chart_path = manifest['arrangements'][0]['file']
    chart = json.loads(files[chart_path])
    ledger_path = recipe['endingOmissionsFile']
    ledger = json.loads(files[ledger_path])
    if change == 'early_note':
        chart['notes'][40]['f'] += 1
    elif change == 'missing_note':
        chart['notes'].pop(40)
    elif change == 'false_ledger':
        ledger['notes'][0]['audioStart'] -= 1
    elif change == 'missing_evidence':
        recipe['recordingSyncFile'] = 'missing.json'
    elif change == 'wrong_map':
        alignment['provenance']['mapHash'] = 'changed'
    elif change == 'old_contract':
        recipe['preservationContract'] = 10
    elif change == 'forged_pass':
        # Make all saved hashes/receipts agree with unrelated silent audio. Only
        # re-evaluating actual audio against raw source notes can detect this.
        silent = tmp_path / 'silence.ogg'
        with sf.SoundFile(silent, 'w', samplerate=22050, channels=1, format='OGG', subtype='VORBIS') as writer:
            remaining = 22050 * 51 + 11025
            while remaining:
                size = min(32768, remaining)
                writer.write(np.zeros(size, dtype='float32'))
                remaining -= size
        files['audio/full.ogg'] = silent.read_bytes()
        stored = alignment['recordingSync']
        stored['audioSha256'] = hashlib.sha256(files['audio/full.ogg']).hexdigest()
        alignment['recordingEnd']['syncEvidenceHash'] = digest(stored)
        recipe['recordingEnd'] = deepcopy(alignment['recordingEnd'])
        ledger.update(alignment['recordingEnd'])
        files[recipe['recordingSyncFile']] = json.dumps(stored).encode()
    files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    files[chart_path] = json.dumps(chart).encode()
    files[ledger_path] = json.dumps(ledger).encode()
    target = tmp_path / 'modified.feedpak'
    with zipfile.ZipFile(target, 'w') as z:
        for name, data in files.items():
            z.writestr(name, data)
    result = verify_import(source, target, alignment, META)
    assert result['status'] == 'failed', result
    if change == 'forged_pass':
        assert any(e['code'] == 'ending_audio_sync' for e in result['errors']), result


def test_a_stored_status_without_valid_policy_cannot_authorize_the_builder(completed):
    _, _, audio, alignment, _ = completed
    for field in ['recordingEnd', 'recordingSync', 'anchors']:
        altered = deepcopy(alignment)
        altered.pop(field)
        assert not allowed(altered, audio['duration'])
    assert not allowed(alignment, audio['duration'] - .1)


def test_ending_chords_and_crossing_held_tails_preserve_the_remaining_chart(recording, tmp_path):
    _, source, _, audio, sync, _ = recording
    raw = json.loads(source.read_text())
    beats = raw['parts'][0]['measures'][-1]['voices'][0]['beats']
    # A held tail beginning before the recording end must survive; later chord
    # members are individually accounted for even though the whole chord goes.
    beats[6]['notes'][0].update(fret=beats[5]['notes'][0]['fret'], tie=True)
    beats[7]['notes'].append({'string': 4, 'fret': 7})
    path = tmp_path / 'source.json'
    path.write_text(json.dumps(raw))
    performance = load_performance(path, META)
    alignment = authorize(performance, audio, align_from_songsterr(
        performance, audio, sync, META, allow_ending_candidate=True))
    job = tmp_path / 'job'
    job.mkdir()
    result = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path / 'out', source_path=path,
                          compatibility=performance['compatibilityReport'], recipe={'preservationContract': 11})
    report = verify_import(path, Path(result['stagingPath']), alignment, META)
    assert report['status'] == 'passed', report
    assert report['adjustments']['omittedEndingNotes'] == 2
    assert report['adjustments']['terminalSustains'] == 1


def test_cutoff_never_silently_drops_an_entire_arrangement(completed, tmp_path):
    source, performance, audio, alignment, _ = completed
    altered = deepcopy(performance)
    track = deepcopy(altered['tracks'][0])
    track.update(id='late-part', notes=track['notes'][-2:])
    altered['tracks'].append(track)
    with pytest.raises(ImportFailure, match='entire arrangement'):
        build_feedpak(altered, audio, alignment, tmp_path, output_dir=tmp_path / 'out', source_path=source)


def test_failed_acoustic_guard_does_not_fall_back_to_a_more_permissive_matcher(recording, monkeypatch):
    from feedback_converter.song_import import worker, ending_cutoff
    _, _, performance, audio, sync, _ = recording
    def refused(*args):
        raise ImportFailure('alignment_failed', 'Insufficient earlier timing evidence.')
    monkeypatch.setattr(ending_cutoff, 'authorize', refused)
    monkeypatch.setattr(worker, 'align_audio', lambda *a, **kw: pytest.fail('must not bypass the acoustic guard'))
    with pytest.raises(ImportFailure, match='Insufficient earlier timing'):
        worker._choose_alignment(performance, audio, {'synchronization': sync, 'metadata': META})


@pytest.fixture(scope='module')
def sparse_completed(recording, tmp_path_factory):
    _, source, performance, original_audio, sync, _ = recording
    root = tmp_path_factory.mktemp('sparse-recording-end')
    data, rate = sf.read(original_audio['path'], always_2d=True)
    offset = 15.4
    signal = np.concatenate([np.zeros((round(offset * rate), data.shape[1])), data])
    wav = root / 'delayed.wav'
    sf.write(wav, signal, rate)
    media = root / 'media'
    media.mkdir()
    audio = prepare_audio({'kind': 'file', 'path': str(wav)}, media)
    audio['source'].update(kind='youtube', videoId=VIDEO)
    timing = {**sync, 'points': [t + offset for t in sync['points']]}
    alignment = authorize(performance, audio, align_from_songsterr(
        performance, audio, timing, META, allow_ending_candidate=True))
    assert alignment['recordingSync']['sparseWindows'] == 1
    job = root / 'job'
    job.mkdir()
    result = build_feedpak(performance, audio, alignment, job, output_dir=root / 'out', source_path=source,
                          compatibility=performance['compatibilityReport'], recipe={
                              'preservationContract': 32, 'audioSource': audio['source'],
                              'sourceMetadata': dict(performance.get('source') or {}),
                              'alignment': {'method': alignment['method'], 'provenance': alignment['provenance']}})
    return source, performance, audio, alignment, Path(result['stagingPath'])


def test_sparse_cutoff_package_is_independently_verified_without_retiming(sparse_completed):
    source, performance, audio, alignment, archive = sparse_completed
    report = verify_import(source, archive, alignment, META)
    assert report['status'] == 'passed', report
    assert report['adjustments']['omittedEndingNotes'] == 2
    assert report['timing']['independentAudioMatchAssessed']
    with zipfile.ZipFile(archive) as z:
        manifest = yaml.safe_load(z.read('manifest.yaml'))
        chart = json.loads(z.read(manifest['arrangements'][0]['file']))
        assert chart['notes'][0]['t'] == pytest.approx(15.4)
        assert [n['f'] for n in chart['notes']] == [n['f'] for n in performance['tracks'][0]['notes'][:206]]
        assert z.read(manifest['song_import']['sourceFile']) == source.read_bytes()


@pytest.mark.parametrize('change', ['bad_prefix', 'old_sync_version'])
def test_forged_sparse_success_cannot_authorize_an_incorrect_package(sparse_completed, tmp_path, change):
    source, _, _, original, archive = sparse_completed
    alignment = deepcopy(original)
    with zipfile.ZipFile(archive) as z:
        files = {n: z.read(n) for n in z.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml'])
    recipe = manifest['song_import']
    stored = alignment['recordingSync']
    if change == 'bad_prefix':
        import io
        data, rate = sf.read(io.BytesIO(files['audio/full.ogg']), always_2d=True)
        data[:round(16 * rate)] = 0
        replacement = tmp_path / 'bad-prefix.ogg'
        with sf.SoundFile(replacement, 'w', samplerate=rate, channels=data.shape[1], format='OGG', subtype='VORBIS') as writer:
            for start in range(0, len(data), 32768):
                writer.write(data[start:start + 32768])
        files['audio/full.ogg'] = replacement.read_bytes()
        stored['audioSha256'] = hashlib.sha256(files['audio/full.ogg']).hexdigest()
    else:
        stored['version'] = 'mapped-pitch-onsets-v1'
    alignment['recordingEnd']['syncEvidenceHash'] = digest(stored)
    recipe['recordingEnd'] = deepcopy(alignment['recordingEnd'])
    ledger = json.loads(files[recipe['endingOmissionsFile']])
    ledger.update(alignment['recordingEnd'])
    files[recipe['endingOmissionsFile']] = json.dumps(ledger).encode()
    files[recipe['recordingSyncFile']] = json.dumps(stored).encode()
    files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    target = tmp_path / 'forged.feedpak'
    with zipfile.ZipFile(target, 'w') as z:
        for name, data in files.items():
            z.writestr(name, data)
    report = verify_import(source, target, alignment, META)
    assert report['status'] == 'failed', report
    assert any(e['code'] == ('ending_audio_sync' if change == 'bad_prefix' else 'ending_policy') for e in report['errors'])
