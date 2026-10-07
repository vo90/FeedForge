"""Original recording bounds attacks; short added silence only preserves tails."""
from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from feedback_converter.song_import import cutoff_padding as cp, ending_padding as ep, worker
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.synchronization import align_from_songsterr
from feedback_converter.song_import.verification import verify_import
from test_song_import_ending_padding import source_audio
from test_songsterr_recording_end import META


@pytest.mark.parametrize('start', [10, 10.001, 10.2, 15])
def test_padding_never_admits_later_attacks(start):
    tracks = [{'events': [{'t': 9, 'end': 10.4}, {'t': start, 'end': start + .1}]}]
    spec = cp.plan(tracks, 10)
    assert spec['lastNoteEnd'] == 10.4 and spec['omittedNoteCount'] == 1
    assert len(cp.retained(tracks, 10)[0][0]['events']) == 1


@pytest.mark.parametrize('events', [
    [{'t': 10, 'end': 10.4}],
    [{'t': 9, 'end': 12.01, 'effects': {'sl': 3}}, {'t': 11, 'end': 12}],
    [{'t': 9, 'end': 9.5}, {'t': 11, 'end': 12}],
    [{'t': 9, 'end': 10.4}, {'t': float('nan'), 'end': 12}],
    [{'t': 9, 'end': 10.4}, {'t': 11, 'end': 10}],
])
def test_invalid_or_unnecessary_padding_is_rejected(events):
    assert cp.plan([{'events': events}], 10) is None


def test_entire_late_arrangement_cannot_be_erased():
    assert cp.plan([{'events': [{'t': 9, 'end': 10.4}]},
                    {'events': [{'t': 10, 'end': 11}]}], 10) is None


@pytest.fixture(scope='module')
def combined_source(source_audio):
    root, original, _, audio, sync = source_audio
    raw = json.loads(original.read_text())
    # Keep the first track's slide and bend, and add real late source attacks.
    raw['parts'][0]['measures'][26]['voices'][0]['beats'] = [
        {'duration': [1, 1], 'notes': [{'string': 5, 'fret': 9}]}]
    donor = deepcopy(raw['tracks'][0]); donor.update(id=1, name='Rhythm Guitar')
    raw['tracks'].append(donor)
    part = deepcopy(raw['parts'][0])
    for bar in part['measures']:
        bar['voices'][0]['beats'] = [{'duration': [1, 1], 'rest': True, 'notes': []}]
    # A retained early note and a later attack INSIDE the proposed .1s padding.
    part['measures'][0]['voices'][0]['beats'] = [{'duration': [1, 1], 'notes': [{'string': 5, 'fret': 3}]}]
    part['measures'][25]['voices'][0]['beats'] = [
        {'duration': [31, 32], 'rest': True, 'notes': []},
        {'duration': [1, 32], 'notes': [{'string': 5, 'fret': 5}]}]
    raw['parts'].append(part)
    source = root/'combined.json'; source.write_text(json.dumps(raw))
    return source, load_performance(source, META, composition_context=True), audio, sync


@pytest.fixture(scope='module')
def combined_package(combined_source, tmp_path_factory):
    source, _, audio, sync = combined_source
    root = tmp_path_factory.mktemp('combined-package')
    # Exercise the real worker, acoustic checks, encoding and Hybrid Lead.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(worker, 'prepare_audio', lambda *a, **kw: deepcopy(audio))
        result = worker.run_import({'scorePath': str(source), 'metadata': META, 'synchronization': sync,
            'workDir': str(root/'work'), 'outputDir': str(root/'out'), 'artworkLookup': False,
            'hybridLead': {'enabled': True}})
    assert result['ok'], result
    record = json.loads(next((root/'song-import-evidence/records').glob('*.json')).read_text())
    alignment = json.loads((root/'song-import-evidence/objects'/record['objects']['appliedAlignment']).read_text())
    path = Path(result['stagingPath'])
    return source, path, alignment, result


def test_combined_ending_preserves_tails_and_omits_attacks_including_hybrid(combined_package):
    _, path, alignment, result = combined_package
    assert result['verification']['status'] == 'passed'
    assert result['verification']['adjustments']['omittedEndingNotes'] == 2
    assert result['verification']['endingSilenceSeconds'] == pytest.approx(.1)
    assert cp.selected(alignment)
    with ZipFile(path) as z:
        manifest = yaml.safe_load(z.read('manifest.yaml'))
        assert manifest['duration'] == 54
        assert any(a.get('derived') for a in manifest['arrangements'])
        for arrangement in manifest['arrangements']:
            chart = json.loads(z.read(arrangement['file']))
            notes = chart['notes'] + [{**n, 't': n.get('t', c['t'])} for c in chart['chords'] for n in c['notes']]
            assert all(n['t'] < 53.9 for n in notes)
        chart = json.loads(z.read(manifest['arrangements'][0]['file']))
        final = [n for c in chart['chords'] if c['t'] == 53.75 for n in c['notes']]
        assert len(final) == 3 and all(n['sus'] == .25 for n in final)
        assert next(n for n in final if n.get('bnv'))['bnv'][-1]['t'] == .25
        assert next(n for n in final if n.get('slide_out_marks'))['slide_out_marks'][0]['end'] == .25
        ledger = json.loads(z.read('import/ending-omissions.json'))
        assert sorted(n['audioStart'] for n in ledger['notes']) == [53.9375, 54]


def test_combined_requires_explicit_cutoff_permission_and_matching_recording(combined_source):
    _, perf, audio, sync = combined_source
    with pytest.raises(ImportFailure):
        align_from_songsterr(perf, audio, sync, META, allow_padding_candidate=True)
    alignment = align_from_songsterr(perf, audio, sync, META, allow_padding_candidate=True, allow_ending_candidate=True)
    changed = deepcopy(audio); changed['source']['videoId'] = 'different'
    with pytest.raises(ImportFailure, match='original recording'):
        ep.authorize(perf, changed, alignment)


def test_uncertain_acoustic_evidence_does_not_allow_combined_padding(combined_source, monkeypatch):
    _, perf, audio, sync = combined_source
    alignment = align_from_songsterr(perf, audio, sync, META, allow_padding_candidate=True, allow_ending_candidate=True)
    monkeypatch.setattr(ep, 'assess', lambda *a, **kw: {'status': 'inconclusive', 'outroSupported': False})
    with pytest.raises(ImportFailure, match='including its ending'):
        ep.authorize(perf, audio, alignment)


def test_combined_padding_can_also_trim_only_a_long_plain_tail(combined_source, tmp_path, monkeypatch):
    original, _, audio, sync = combined_source
    raw = json.loads(original.read_text())
    part = raw['parts'][1]
    part['measures'][25]['voices'][0]['beats'] = [
        {'duration': [7, 8], 'rest': True, 'notes': []},
        {'duration': [1, 8], 'notes': [{'string': 5, 'fret': 5}]}]
    part['measures'][26]['voices'][0]['beats'] = [
        {'duration': [1, 1], 'notes': [{'string': 5, 'fret': 5, 'tie': True}]}]
    source = tmp_path/'mixed.json'; source.write_text(json.dumps(raw))
    monkeypatch.setattr(worker, 'prepare_audio', lambda *a, **kw: deepcopy(audio))
    result = worker.run_import({'scorePath': str(source), 'metadata': META, 'synchronization': sync,
        'workDir': str(tmp_path/'work'), 'outputDir': str(tmp_path/'out'), 'artworkLookup': False})
    assert result['ok'], result
    assert result['verification']['adjustments']['omittedEndingNotes'] == 1
    assert result['verification']['adjustments']['terminalSustains'] == 1
    assert result['verification']['endingSilenceSeconds'] == pytest.approx(.1)


@pytest.mark.parametrize('fault', ['contract', 'count', 'omission', 'cutoff', 'late_attack', 'short_slide', 'wrong_video', 'warning'])
def test_independent_verifier_rejects_combined_corruption(combined_package, tmp_path, fault):
    source, path, original, _ = combined_package
    alignment = deepcopy(original)
    with ZipFile(path) as z:
        files = {name: z.read(name) for name in z.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml']); recipe = manifest['song_import']
    if fault == 'contract': recipe['preservationContract'] = 101
    if fault == 'count': alignment['endingPadding']['omittedNoteCount'] += 1
    if fault == 'cutoff': alignment['endingPadding']['originalDuration'] += .05
    if fault == 'warning': alignment['endingPadding']['timingWarning'] = {'version': 1}
    if fault == 'wrong_video': recipe['audioSource']['videoId'] = 'different'
    if fault == 'omission':
        ledger = json.loads(files[recipe['endingOmissionsFile']]); ledger['notes'].pop()
        files[recipe['endingOmissionsFile']] = json.dumps(ledger).encode()
    if fault in ('late_attack', 'short_slide'):
        name = manifest['arrangements'][0]['file']; chart = json.loads(files[name])
        if fault == 'late_attack': chart['notes'].append({'t': 53.95, 's': 0, 'f': 5, 'sus': .02})
        else:
            note = next(n for c in chart['chords'] for n in c['notes'] if n.get('slide_out_marks'))
            note['sus'] -= .02; note['slide_out_marks'][0]['end'] -= .02
        files[name] = json.dumps(chart).encode()
    files[recipe['endingPaddingFile']] = json.dumps(alignment['endingPadding']).encode()
    recipe['alignment']['endingPadding'] = deepcopy(alignment['endingPadding'])
    files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    target = tmp_path/'damaged.feedpak'
    with ZipFile(target, 'w') as z:
        for name, data in files.items(): z.writestr(name, data)
    assert verify_import(source, target, alignment, META)['status'] == 'failed'
