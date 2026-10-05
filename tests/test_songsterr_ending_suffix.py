"""A recording may end several bars before the tab, but cannot authorize retiming."""
from copy import deepcopy
import json
from pathlib import Path
import zipfile

import pytest
import yaml

from test_songsterr_recording_end import recording, META
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.ending_cutoff import authorize, allowed, boundary_policy
from feedback_converter.song_import.preparation import finalize
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.synchronization import align_from_songsterr
from feedback_converter.song_import.verification import verify_import


@pytest.fixture(scope='module')
def suffix(recording, tmp_path_factory):
    _, original, _, audio, timing, _ = recording
    root = tmp_path_factory.mktemp('multi-bar-ending')
    raw = json.loads(original.read_text(encoding='utf8'))
    # The available recording is unchanged; add three bars of real attacks,
    # including chords. These must never become silently stretched into it.
    measures = raw['parts'][0]['measures']
    extra = deepcopy(measures[-3:])
    extra[1]['voices'][0]['beats'][0]['notes'].append({'string': 4, 'fret': 7})
    measures.extend(extra)
    # An earlier held note crosses the cutoff. Its attack remains unchanged.
    beats = measures[25]['voices'][0]['beats']
    beats[6]['notes'][0].update(fret=beats[5]['notes'][0]['fret'], tie=True)
    source = root / 'score.json'
    source.write_text(json.dumps(raw), encoding='utf8')
    performance = load_performance(source, META)
    timing = {**timing, 'points': list(range(0, 59, 2))}
    candidate = align_from_songsterr(performance, audio, timing, META, allow_ending_candidate=True)
    alignment = authorize(performance, audio, candidate)
    return root, source, performance, audio, timing, alignment


def build(root, source, performance, audio, alignment):
    root.mkdir()
    result = build_feedpak(performance, audio, alignment, root, output_dir=root/'out', source_path=source,
        compatibility=performance['compatibilityReport'], recipe={'preservationContract': 56,
        'audioSource': audio['source'], 'alignment': {'provenance': alignment['provenance']},
        **({'preparation': alignment['preparation']} if alignment.get('preparation') else {})})
    return Path(result['stagingPath'])


@pytest.fixture(scope='module')
def completed_suffix(suffix):
    root, source, performance, audio, _, alignment = suffix
    return source, performance, audio, alignment, build(root/'job', source, performance, audio, alignment)


def test_multibar_cut_keeps_earlier_notes_and_preserves_source_and_omissions(completed_suffix):
    source, performance, audio, alignment, archive = completed_suffix
    assert alignment['recordingEnd']['version'] == 2
    assert alignment['recordingEnd']['cutoffMeasureIndex'] == 25
    assert allowed(alignment, audio['duration'])
    report = verify_import(source, archive, alignment, META)
    assert report['status'] == 'passed', report
    assert report['adjustments']['omittedEndingNotes'] == 26
    assert report['adjustments']['terminalSustains'] == 1
    assert report['timing']['independentAudioMatchAssessed']
    with zipfile.ZipFile(archive) as z:
        manifest = yaml.safe_load(z.read('manifest.yaml'))
        recipe = manifest['song_import']
        assert z.read(recipe['sourceFile']) == source.read_bytes()
        ledger = json.loads(z.read(recipe['endingOmissionsFile']))
        assert len(ledger['notes']) == 26
        assert min(n['audioStart'] for n in ledger['notes']) == 51.75
        assert max(n['audioStart'] for n in ledger['notes']) == 57.75
        chart = json.loads(z.read(manifest['arrangements'][0]['file']))
        assert len(chart['notes']) == 206
        original = performance['tracks'][0]['notes'][:206]
        assert [(n['t'], n['f'], n['s']) for n in chart['notes']] == [(n['t'], n['f'], n['s']) for n in original]
        assert all(n['t'] < audio['duration'] and n['t']+n['sus'] <= audio['duration'] for n in chart['notes'])


def test_preparation_reauthorizes_the_same_suffix(suffix, tmp_path):
    _, source, performance, audio, _, original = suffix
    prepared, alignment = finalize(performance, audio, deepcopy(original), tmp_path)
    assert alignment['preparation']['seconds'] == 2
    assert alignment['recordingEnd']['cutoffMeasureIndex'] == 25
    assert alignment['recordingEnd']['cutoffMeasureStart'] == 52
    archive = build(tmp_path/'job', source, performance, prepared, alignment)
    report = verify_import(source, archive, alignment, META)
    assert report['status'] == 'passed', report
    assert report['adjustments']['omittedEndingNotes'] == 26


@pytest.mark.parametrize('change', ['early_attack', 'missing_note', 'false_ledger', 'wrong_boundary', 'old_contract'])
def test_suffix_cannot_hide_changes_to_the_retained_chart_or_evidence(completed_suffix, tmp_path, change):
    source, _, _, original, archive = completed_suffix
    alignment = deepcopy(original)
    with zipfile.ZipFile(archive) as z:
        files = {n: z.read(n) for n in z.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml'])
    recipe = manifest['song_import']
    chart_path = manifest['arrangements'][0]['file']
    chart = json.loads(files[chart_path])
    ledger_path = recipe['endingOmissionsFile']
    ledger = json.loads(files[ledger_path])
    if change == 'early_attack': chart['notes'][40]['t'] += .01
    elif change == 'missing_note': chart['notes'].pop(40)
    elif change == 'false_ledger': ledger['notes'].pop()
    elif change == 'old_contract': recipe['preservationContract'] = 55
    elif change == 'wrong_boundary':
        alignment['recordingEnd']['cutoffMeasureIndex'] -= 1
        recipe['recordingEnd'] = deepcopy(alignment['recordingEnd'])
        ledger.update(alignment['recordingEnd'])
    files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    files[chart_path] = json.dumps(chart).encode()
    files[ledger_path] = json.dumps(ledger).encode()
    target = tmp_path/'bad.feedpak'
    with zipfile.ZipFile(target, 'w') as z:
        for name, data in files.items(): z.writestr(name, data)
    report = verify_import(source, target, alignment, META)
    assert report['status'] == 'failed', report


def test_bad_timing_cannot_be_excused_as_a_missing_ending(suffix):
    _, _, performance, audio, timing, _ = suffix
    changed = {**timing, 'points': [t+.25 for t in timing['points']]}
    candidate = align_from_songsterr(performance, audio, changed, META, allow_ending_candidate=True)
    assert candidate['status'] == 'needs_ending_check'
    with pytest.raises(ImportFailure, match='matched reliably'):
        authorize(performance, audio, candidate)
    assert not allowed(candidate, audio['duration'])


@pytest.mark.parametrize('missing', ['source', 'contract'])
def test_builder_requires_source_and_current_contract(suffix, tmp_path, missing):
    _, source, performance, audio, _, alignment = suffix
    with pytest.raises(ImportFailure, match='contract 56'):
        build_feedpak(performance, audio, alignment, tmp_path, output_dir=tmp_path/'out',
            source_path=None if missing == 'source' else source,
            recipe={'preservationContract': 55 if missing == 'contract' else 56})


def test_staggered_chord_is_not_partly_discarded(suffix, tmp_path):
    _, source, performance, audio, _, alignment = suffix
    changed = deepcopy(performance)
    track = changed['tracks'][0]
    track['chords'].append({'t': 51.4, 'notes': [
        {'t': 51.4, 's': 1, 'f': 3, 'sus': .05}, {'t': 51.6, 's': 2, 'f': 4, 'sus': .1}]})
    with pytest.raises(ImportFailure, match='staggered chord'):
        build(tmp_path/'job', source, changed, audio, alignment)


def test_exact_bar_boundary_is_supported_and_invalid_maps_are_not():
    anchors = [{'score': t, 'audio': t} for t in (0, 2, 4, 6)]
    assert boundary_policy({'anchors': anchors}, 4)['cutoffMeasureIndex'] == 2
    for duration in (0, 6, -1, float('nan'), float('inf'), True):
        assert boundary_policy({'anchors': anchors}, duration) is None
    for invalid in (None, [], anchors[::-1], [{'audio': 0}, {'audio': 1}],
                    [anchors[0], {'score': 2, 'audio': float('nan')} ]):
        assert boundary_policy({'anchors': invalid}, 1) is None


def test_worker_builds_hybrid_with_cutoff_and_independent_evidence(suffix, tmp_path, monkeypatch):
    from feedback_converter.song_import import audio as audio_module, worker
    _, source, _, audio, timing, _ = suffix
    monkeypatch.setattr(audio_module, '_public_url', lambda url: url)
    monkeypatch.setattr(audio_module, '_download_youtube', lambda *a, **kw: (Path(audio['path']), audio['source']))
    result = worker.run_import({'scorePath': str(source), 'metadata': META,
        'audio': {'kind': 'url', 'url': 'https://www.youtube.com/watch?v=abcdefghijk'},
        'synchronization': timing, 'workDir': str(tmp_path/'work'), 'outputDir': str(tmp_path/'out'),
        'hybridLead': {'enabled': True}, 'outputSettings': {'nameTemplate': '{artist} - {title}'}})
    assert result['ok'], result
    assert result['verification']['status'] == 'passed'
    assert result['verification']['adjustments']['omittedEndingNotes'] == 26
    assert result['verification']['hybridLead']['status'] == 'no_additions'
    assert result['recipe']['alignment']['recordingEnd']['version'] == 2
    assert result['evidence']['version'] == 83
    with zipfile.ZipFile(result['stagingPath']) as z:
        manifest = yaml.safe_load(z.read('manifest.yaml'))
        assert len(manifest['arrangements']) == 2
        for arrangement in manifest['arrangements']:
            chart = json.loads(z.read(arrangement['file']))
            assert len(chart['notes']) == 206
            assert all(n['t'] < manifest['duration'] for n in chart['notes'])
