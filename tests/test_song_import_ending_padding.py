"""Real encoded audio, source-fidelity checks and adversarial tail eligibility."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pytest
import soundfile as sf
import yaml

from feedback_converter.song_import import ending_padding as ep, worker
from feedback_converter.song_import.audio import ImportFailure, prepare_audio
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.preparation import finalize, recording_view
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.synchronization import align_from_songsterr
from feedback_converter.song_import.verification import verify_import
from test_songsterr_recording_end import META, VIDEO


@pytest.mark.parametrize('events,duration,eligible', [
    ([{'t': 9, 'end': 10.3}], 10, True),
    ([{'t': 9, 'end': 12}], 10, True),
    ([{'t': 9, 'end': 12.00001}], 10, False),
    ([{'t': 10, 'end': 10.3}], 10, False),
    ([{'t': 9, 'end': 10}], 10, False),
    ([{'t': -1, 'end': 10.3}], 10, False),
    ([{'t': 9, 'end': 10.3}, {'t': 10.2, 'end': 10.4}], 10, False),
])
def test_only_existing_short_tails(events, duration, eligible):
    assert bool(ep.bounds([{'events': events}], duration)) == eligible


def test_all_arrangements_must_qualify():
    assert ep.bounds([{'events': [{'t': 9, 'end': 10.3}]}, {'events': [{'t': 10.1, 'end': 10.4}]}], 10) is None


@pytest.fixture(scope='module')
def source_audio(tmp_path_factory):
    root = tmp_path_factory.mktemp('ending-padding')
    rng = np.random.default_rng(761)
    frets = rng.integers(0, 13, 208).tolist()
    bars = [{'signature': [4, 4], 'voices': [{'beats': [
        {'duration': [1, 8], 'notes': [{'string': 5, 'fret': f}]} for f in frets[i:i+8]]}]}
        for i in range(0, len(frets), 8)]
    # A held note, a moving bend and an untargeted slide share the final attack.
    bars[-1]['voices'][0]['beats'][-1]['notes'] += [
        {'string': 4, 'fret': 5, 'slide': 'downwards'},
        {'string': 3, 'fret': 7, 'bend': {'points': [{'position': 0, 'tone': 0}, {'position': 60, 'tone': 100}]}}]
    # Empty notation may end far later; it must not decide the padding length.
    bars += [{'signature': [4, 4], 'voices': [{'beats': [{'duration': [1, 1], 'rest': True, 'notes': []}]}]} for _ in range(4)]
    raw = {'format': 'songsterr', 'songId': 12, 'revisionId': 34, 'title': 'Tail fixture', 'artist': 'Synthetic',
        'tracks': [{'id': 0, 'name': 'Guitar', 'instrumentId': 29, 'tuning': [64, 59, 55, 50, 45, 40]}],
        'parts': [{'measures': bars, 'automations': {'tempo': [{'measure': 0, 'position': [0, 1], 'bpm': 120, 'type': 4}]}}]}
    source = root/'source.json'; source.write_text(json.dumps(raw), encoding='utf8')
    perf = load_performance(source, META)
    rate, duration = 22050, 51.9
    samples = np.zeros(round(rate*duration))
    for i, fret in enumerate(frets):
        begin = round(i*.25*rate); size = min(round(.23*rate), len(samples)-begin)
        t = np.arange(size)/rate
        hz = 440*2**((40+fret-69)/12)
        samples[begin:begin+size] += .15*np.minimum(t/.002, 1)*np.exp(-t*8)*sum(
            np.sin(2*np.pi*hz*h*t)/h for h in range(1, 6))
    wav = root/'truth.wav'; sf.write(wav, samples, rate, subtype='FLOAT')
    job = root/'audio'; job.mkdir()
    audio = prepare_audio({'kind': 'file', 'path': str(wav)}, job, defer_encoding=True)
    audio['source'].update(kind='youtube', videoId=VIDEO)
    sync = {'version': 1, 'source': 'songsterr-video-points', **META, 'videoId': VIDEO,
        'status': 'done', 'feature': None, 'points': list(range(0, 61, 2))}
    return root, source, perf, audio, sync


@pytest.fixture(scope='module')
def package(source_audio):
    root, source, perf, audio, sync = source_audio
    alignment = align_from_songsterr(perf, audio, sync, META, allow_padding_candidate=True)
    assert alignment['status'] == 'needs_padding_check'
    assert alignment['paddingCandidate']['lastNoteEnd'] == 52
    assert alignment['anchors'][-1]['audio'] == 60
    job = root/'package-job'; job.mkdir()
    with pytest.raises(ImportFailure, match='synchronization'):
        build_feedpak(perf, audio, alignment, job, output_dir=root/'out')
    alignment = ep.authorize(perf, audio, alignment)
    prepared, alignment = finalize(perf, audio, alignment, job)
    recipe = {'preservationContract': 36, 'audioSource': audio['source'], 'preparation': alignment['preparation'],
              'alignment': {'provenance': alignment['provenance'], 'endingPadding': alignment['endingPadding']}}
    result = build_feedpak(perf, prepared, alignment, job, output_dir=root/'out', source_path=source,
                          recipe=recipe, compatibility=perf['compatibilityReport'])
    return source, Path(result['stagingPath']), alignment, audio


def test_real_padding_preserves_source_gestures_and_original_recording(package):
    source, path, alignment, original = package
    result = verify_import(source, path, alignment, META)
    assert result['status'] == 'passed', result
    assert 'adjustments' not in result
    assert alignment['endingPadding']['seconds'] == pytest.approx(.1)
    assert alignment['endingPaddingSync']['outroSupported'] is True
    assert alignment['endingPaddingSync']['audioDuration'] == 51.9
    with ZipFile(path) as z:
        m = yaml.safe_load(z.read('manifest.yaml'))
        assert m['duration'] == 54
        assert alignment['preparation']['seconds'] == 2
        chart = json.loads(z.read(m['arrangements'][0]['file']))
        notes = chart['notes'] + [{**n, 't': n.get('t', c['t'])} for c in chart['chords'] for n in c['notes']]
        final = [n for n in notes if n['t'] == 53.75]
        assert len(final) == 3 and all(n['sus'] == .25 for n in final)
        assert next(n for n in final if n.get('slide_out_marks'))['slide_out_marks'][0]['end'] == .25
        assert next(n for n in final if n.get('bnv'))['bnv'][-1]['t'] == .25
        decoded, rate = sf.read(recording_view(io.BytesIO(z.read('audio/full.ogg')), alignment['preparation']))
        before, _ = sf.read(original['path'])
        assert decoded.shape == before.shape and np.corrcoef(decoded, before)[0, 1] > .99


def test_support_only_earlier_in_song_cannot_authorize_padding(source_audio, monkeypatch):
    _, _, perf, audio, sync = source_audio
    alignment = align_from_songsterr(perf, audio, sync, META, allow_padding_candidate=True)
    monkeypatch.setattr(ep.rs, 'assess', lambda *a, **kw: {'status': 'supported',
        'windows': [{'end': 40, 'status': 'supported'}]})
    with pytest.raises(ImportFailure, match='including its ending'):
        ep.authorize(perf, audio, alignment)


def test_changed_decoder_source_cannot_reuse_timing_authority(package, source_audio, tmp_path):
    _, _, alignment, original = package
    changed = deepcopy(original)
    path = tmp_path/'changed.wav'
    sf.write(path, np.zeros(22050*3), 22050, subtype='FLOAT')
    changed['encodingSourcePath'] = str(path)
    with pytest.raises(ImportFailure, match='recording changed') as caught:
        finalize(source_audio[2], changed, alignment, tmp_path)
    assert caught.value.code == 'needs_audio'


def test_worker_uses_only_original_recording_for_all_acoustic_checks(source_audio, tmp_path, monkeypatch):
    _, source, _, audio, sync = source_audio
    monkeypatch.setattr(worker, 'prepare_audio', lambda *a, **kw: deepcopy(audio))
    result = worker.run_import({'scorePath': str(source), 'metadata': META, 'synchronization': sync,
        'workDir': str(tmp_path/'work'), 'outputDir': str(tmp_path/'out'), 'artworkLookup': False})
    assert result['ok'], result
    assert result['verification']['endingSilenceSeconds'] == pytest.approx(.1)
    evidence = Path(result['evidence']['root']) if 'root' in result['evidence'] else tmp_path/'song-import-evidence'
    record = json.loads((evidence/'records'/f"{result['evidence']['id']}.json").read_text())
    alignment = json.loads((evidence/'objects'/record['objects']['appliedAlignment']).read_text())
    assert alignment['timingAssessment']['audioDuration'] == alignment['endingPaddingSync']['audioDuration'] == 51.9


@pytest.mark.parametrize('fault', ['duration', 'frames', 'original_end', 'note', 'curve', 'audible_tail', 'forged_sync'])
def test_independent_verifier_rejects_corruption(package, tmp_path, fault):
    source, path, original, _ = package
    alignment = deepcopy(original)
    with ZipFile(path) as z: files = {n: z.read(n) for n in z.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml']); recipe = manifest['song_import']
    if fault == 'duration': manifest['duration'] += .1
    if fault == 'frames': alignment['endingPadding']['frames'] += 100
    if fault == 'original_end': alignment['endingPadding']['originalDuration'] -= .1
    if fault in ('note', 'curve'):
        filename = manifest['arrangements'][0]['file']; chart = json.loads(files[filename])
        notes = chart['notes'] + [n for c in chart['chords'] for n in c['notes']]
        if fault == 'note': notes[-1]['sus'] -= .02
        else: next(n for n in notes if n.get('bnv'))['bnv'][-1]['v'] = 0
        files[filename] = json.dumps(chart).encode()
    if fault in ('audible_tail', 'forged_sync'):
        data, rate = sf.read(io.BytesIO(files['audio/full.ogg']))
        if fault == 'audible_tail': data[-round(.04*rate):] = .2
        else: data[round(2*rate):round(26*rate)] = 0
        output = io.BytesIO()
        with sf.SoundFile(output, 'w', samplerate=rate, channels=1, format='OGG', subtype='VORBIS') as writer:
            for i in range(0, len(data), 32768): writer.write(data[i:i+32768])
        files['audio/full.ogg'] = output.getvalue()
        alignment['preparation']['audioSha256'] = hashlib.sha256(output.getvalue()).hexdigest()
        recipe['preparation'] = deepcopy(alignment['preparation'])
        if fault == 'forged_sync':
            from feedback_converter.song_import.local_sync import _hash
            recording = recording_view(io.BytesIO(output.getvalue()), alignment['preparation'])
            alignment['endingPadding']['recordingSamplesSha256'] = _hash(recording)
            alignment['endingPaddingSync']['audioSha256'] = _hash(recording)
            alignment['endingPadding']['syncEvidenceHash'] = ep.rs.digest(alignment['endingPaddingSync'])
    files[recipe['endingPaddingFile']] = json.dumps(alignment['endingPadding']).encode()
    files[recipe['endingPaddingSyncFile']] = json.dumps(alignment['endingPaddingSync']).encode()
    recipe['alignment']['endingPadding'] = deepcopy(alignment['endingPadding'])
    files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    target = tmp_path/'damaged.feedpak'
    with ZipFile(target, 'w') as z:
        for name, data in files.items(): z.writestr(name, data)
    result = verify_import(source, target, alignment, META)
    assert result['status'] == 'failed', result


def test_uncertain_padding_retains_existing_trim_fallback(monkeypatch):
    calls = []
    def choose(*args, **kwargs):
        calls.append(kwargs['allow_padding_candidate'])
        return {'paddingCandidate': {}} if kwargs['allow_padding_candidate'] else {'status': 'validated', 'terminalSustains': {}}
    # A nonempty candidate triggers the actual fallback branch.
    def source_map(*args, **kwargs):
        result = choose(*args, **kwargs)
        if kwargs['allow_padding_candidate']: result['paddingCandidate'] = {'originalDuration': 10}
        return result
    monkeypatch.setattr(worker, 'align_from_songsterr', source_map)
    def decline(*args): raise ImportFailure('source_sync_unavailable', 'Not supported.', {'sourceSyncReason': 'ending_padding_sync_inconclusive'})
    monkeypatch.setattr(ep, 'authorize', decline)
    result = worker._choose_alignment({}, {}, {})
    assert calls == [True, False]
    assert 'terminalSustains' in result and 'endingPadding' not in result
    assert result['endingPaddingDeclined']['sourceSyncReason'] == 'ending_padding_sync_inconclusive'
