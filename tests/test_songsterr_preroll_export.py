"""A silent pre-roll must not alter playable timing or export invalid metadata."""
from copy import deepcopy
import json
from pathlib import Path
import zipfile

import numpy as np
import pytest
import soundfile as sf
import yaml

from feedback_converter.song_import import audio as audio_module, worker
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.synchronization import align_from_songsterr
from feedback_converter.song_import.verification import verify_import

META = {'songId': '12', 'revisionId': '34', 'approval': 'approved'}
VIDEO = 'abcdefghijk'


def package(tmp_path, monkeypatch, offset):
    raw = {'format': 'songsterr', 'songId': 12, 'revisionId': 34, 'title': 'Intro', 'artist': 'Test',
           'tracks': [{'id': 0, 'name': 'Guitar', 'instrumentId': 29, 'tuning': [64, 59, 55, 50, 45, 40]}],
           'parts': [{'automations': {'tempo': [{'measure': 0, 'position': [0, 1], 'bpm': 120, 'type': 4}]},
                      'measures': [
               {'signature': [4, 4], 'voices': [{'beats': [{'type': 1, 'duration': [1, 1], 'notes': [{'rest': True}]}]}]},
               {'signature': [3, 4], 'voices': [{'beats': [{'type': 2, 'dots': 1, 'duration': [3, 4], 'notes': [{'rest': True}]}]}]},
               {'signature': [5, 4], 'voices': [{'beats': [
                   {'type': 4, 'duration': [1, 4], 'notes': [{'string': 5, 'fret': f}]} for f in [0, 3, 5, 7, 9]]}]}
           ]}]}
    source = tmp_path / 'source.json'
    source.write_text(json.dumps(raw), encoding='utf-8')
    duration = 6 + offset + .5
    recording = tmp_path / 'recording.wav'
    sf.write(recording, .1 * np.sin(np.arange(round(duration * 22050)) * .1), 22050)
    monkeypatch.setattr(audio_module, '_public_url', lambda url: url)
    monkeypatch.setattr(audio_module, '_download_youtube', lambda url, directory, tools:
                        (recording, {'kind': 'youtube', 'videoId': VIDEO, 'url': url}))
    monkeypatch.setattr(worker, 'align_audio', lambda *a, **kw: pytest.fail('The exact source map is usable.'))
    sync = {'version': 1, 'source': 'songsterr-video-points', **META, 'videoId': VIDEO,
            'status': 'done', 'feature': None, 'points': [t + offset for t in [0, 2, 3.5, 6]]}
    result = worker.run_import({'scorePath': str(source), 'metadata': META,
        'audio': {'kind': 'url', 'url': f'https://www.youtube.com/watch?v={VIDEO}'}, 'synchronization': sync,
        'artworkLookup': False, 'workDir': str(tmp_path / 'work'), 'outputDir': str(tmp_path / 'output')})
    return source, sync, duration, result


@pytest.mark.parametrize('offset,meters,first_downbeat', [
    (0, [(0, [4, 4]), (2, [3, 4]), (3.5, [5, 4])], 0),
    (-.25, [(0, [4, 4]), (1.75, [3, 4]), (3.25, [5, 4])], 1.75),
    (-2.25, [(0, [3, 4]), (1.25, [5, 4])], 1.25),
    (-3.1, [(0, [3, 4]), (.4, [5, 4])], .4),
    (-3.5, [(0, [5, 4])], 0),
])
def test_complete_package_numbers_visible_downbeats_and_preserves_active_meter(tmp_path, monkeypatch, offset, meters, first_downbeat):
    source, _, _, result = package(tmp_path, monkeypatch, offset)
    assert result['ok'], result
    assert result['verification']['status'] == 'passed'
    with zipfile.ZipFile(result['stagingPath']) as z:
        manifest = yaml.safe_load(z.read('manifest.yaml'))
        timeline = json.loads(z.read(manifest['song_timeline']))
        chart = json.loads(z.read(manifest['arrangements'][0]['file']))
        notation = json.loads(z.read(manifest['arrangements'][0]['notation']))
        downbeats = [b for b in timeline['beats'] if b['measure'] > 0]
        assert downbeats[0]['time'] == pytest.approx(first_downbeat)
        assert [b['measure'] for b in downbeats] == list(range(1, len(downbeats) + 1))
        assert [(m['time'], m['ts']) for m in timeline['time_signatures']] == meters
        assert chart['beats'] == timeline['beats']
        assert chart['time_signatures'] == timeline['time_signatures']
        assert [n['t'] for n in chart['notes']] == pytest.approx([3.5 + offset + i * .5 for i in range(5)])
        assert [n['sus'] for n in chart['notes']] == [.5] * 5
        assert [m['idx'] for m in notation['measures']] == [1, 2, 3]
        assert [m['source_measure'] for m in notation['measures']] == [1, 2, 3]
        assert notation['measures'][0]['t'] == offset
        assert z.read(manifest['song_import']['sourceFile']) == source.read_bytes()


@pytest.mark.parametrize('fault', ['ordinal', 'meter', 'attack'])
def test_independent_verifier_rejects_incorrect_metadata_or_shifted_note(tmp_path, monkeypatch, fault):
    source, sync, duration, result = package(tmp_path, monkeypatch, -.25)
    assert result['ok'], result
    performance = load_performance(source, META)
    alignment = align_from_songsterr(performance, {'duration': duration, 'source': {'kind': 'youtube', 'videoId': VIDEO}}, sync, META)
    with zipfile.ZipFile(result['stagingPath']) as z:
        files = {n: z.read(n) for n in z.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml'])
    target_file = manifest['song_timeline'] if fault != 'attack' else manifest['arrangements'][0]['file']
    changed = json.loads(files[target_file])
    if fault == 'ordinal':
        next(b for b in changed['beats'] if b['measure'] > 0)['measure'] += 1
    elif fault == 'meter':
        changed['time_signatures'][0]['ts'] = [7, 8]
    else:
        changed['notes'][0]['t'] += .1
    files[target_file] = json.dumps(changed).encode()
    modified = tmp_path / 'tampered.feedpak'
    with zipfile.ZipFile(modified, 'w') as z:
        for name, data in files.items():
            z.writestr(name, data)
    assert verify_import(source, modified, alignment, META)['status'] == 'failed'


def test_preroll_fix_cannot_discard_a_real_attack_before_recording(tmp_path, monkeypatch):
    source, sync, duration, _ = package(tmp_path, monkeypatch, -.25)
    performance = load_performance(source, META)
    note = deepcopy(performance['tracks'][0]['notes'][0])
    note['t'] = 0
    performance['tracks'][0]['notes'].insert(0, note)
    with pytest.raises(ImportFailure) as caught:
        align_from_songsterr(performance, {'duration': duration, 'source': {'kind': 'youtube', 'videoId': VIDEO}}, sync, META)
    assert caught.value.diagnostics['sourceSyncReason'] == 'negative_note_time'
