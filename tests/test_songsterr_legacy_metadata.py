"""Old source metadata is retained, never substituted for playable instructions."""
from copy import deepcopy
import json
from zipfile import ZipFile

import pytest
import yaml

from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from test_song_import_score import beat, measure, raw_score
from test_song_import_verification import example


def current_package(tmp_path, source):
    from feedback_converter.song_import import load_performance
    from feedback_converter.song_import.builder import build_feedpak
    from test_song_import_builder import inputs
    path = tmp_path/'source.json'
    path.write_text(json.dumps(source), encoding='utf-8')
    performance = load_performance(path)
    _, audio, _, job = inputs(tmp_path)
    alignment = {'status': 'validated', 'offset': 1, 'scale': 1}
    built = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path/'out',
                         source_path=path, compatibility=performance['compatibilityReport'],
                         recipe={'preservationContract': 88})
    with ZipFile(built['stagingPath']) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml'])
    assert files[manifest['song_import']['sourceFile']] == path.read_bytes()
    return files, manifest, alignment, path


def verify_files(tmp_path, files, source_path, alignment):
    from feedback_converter.song_import.verification import verify_import
    archive = tmp_path/'current.feedpak'
    with ZipFile(archive, 'w') as out:
        for name, data in files.items():
            out.writestr(name, data)
    return verify_import(source_path, archive, alignment)


def legacy_document(index=19, tempo=None, grace=True):
    doc = raw_score([measure(beat(duration=(1, 4)), beat(fret=5, duration=(3, 4)))])
    bar = doc['parts'][0]['measures'][0]
    bar['index'] = index
    first = bar['voices'][0]['beats'][0]
    first['tempo'] = {'type': 4, 'bpm': 65} if tempo is None else tempo
    first['notes'][0]['grace'] = grace
    return doc


def played(doc):
    producer = render(parse(doc))
    independent = expected(songsterr(doc), {'offset': 0, 'scale': 1})
    return producer['tracks'], producer['duration'], independent


@pytest.mark.parametrize('index', [None, 0, 1, 1.0, 99])
@pytest.mark.parametrize('grace', [None, False, True])
@pytest.mark.parametrize('tempo', [None, *({'type': unit, 'bpm': 65} for unit in (1, 2, 4, 8, 16, 32, 64))])
def test_validated_legacy_metadata_does_not_change_any_music(index, grace, tempo):
    doc = legacy_document(index, tempo, grace)
    if tempo is None:
        doc['parts'][0]['measures'][0]['voices'][0]['beats'][0]['tempo'] = None
    original = deepcopy(doc)
    control = raw_score([measure(beat(duration=(1, 4)), beat(fret=5, duration=(3, 4)))])
    assert played(doc) == played(control)
    assert doc == original
    inventory = parse(doc).feature_inventory
    for scope, field in [('measure', 'index'), ('beat', 'tempo'), ('note', 'grace')]:
        row = next(row for row in inventory if row['scope'] == 'Songsterr ' + scope and row['field'] == field)
        assert row['representations'] == ['source']
    report = inspect_songsterr(doc)
    assert report['status'] == 'compatible'
    findings = {row['feature']: row for row in report['findings']}
    assert set(findings) == {'measure.index', 'beat.tempo', 'note.grace'}
    for row in findings.values():
        assert row['category'] == 'source_metadata'
        assert row['impact'] == 'source_retained'
        assert row['retained'] == 'original_source'
    assert findings['measure.index']['value'] == index
    assert findings['note.grace']['value'] is grace


@pytest.mark.parametrize('scope,value', [
    ('index', False), ('index', True), ('index', -1), ('index', 1.5), ('index', ''),
    ('index', '1'), ('index', []), ('index', {}), ('index', float('inf')),
    ('grace', 0), ('grace', 1), ('grace', ''), ('grace', 'beforeBeat'), ('grace', []), ('grace', {}),
    ('tempo', False), ('tempo', 0), ('tempo', ''), ('tempo', []), ('tempo', {}),
    ('tempo', {'type': 4}), ('tempo', {'bpm': 65}), ('tempo', {'type': 4, 'bpm': 0}),
    ('tempo', {'type': 4, 'bpm': True}), ('tempo', {'type': True, 'bpm': 65}),
    ('tempo', {'type': 4, 'bpm': '65'}), ('tempo', {'type': 4, 'bpm': float('nan')}),
    ('tempo', {'type': 4, 'bpm': 65, 'dotted': False}), ('tempo', {'type': 4, 'bpm': 65, 'unknown': 0}),
])
def test_malformed_legacy_values_are_not_hidden_by_inactive_unknown_handling(scope, value):
    doc = legacy_document()
    bar = doc['parts'][0]['measures'][0]
    first = bar['voices'][0]['beats'][0]
    target = bar if scope == 'index' else first if scope == 'tempo' else first['notes'][0]
    target[scope] = value
    for reader in (parse, songsterr):
        with pytest.raises(ValueError):
            reader(doc)
    report = inspect_songsterr(doc)
    assert report['status'] == 'blocked'
    assert any(row['feature'] == {'index': 'measure.index', 'tempo': 'beat.tempo', 'grace': 'note.grace'}[scope]
               and row['impact'] == 'blocking' for row in report['findings'])


@pytest.mark.parametrize('value', [None, False, True, 0, [], {}])
@pytest.mark.parametrize('whole_rest', [False, True])
def test_legacy_grace_is_validated_even_when_note_or_beat_is_a_rest(value, whole_rest):
    rest = {'rest': True, 'duration': [1, 4], 'notes': [{'rest': True, 'grace': value}]}
    if not whole_rest:
        rest.pop('rest')
    doc = raw_score([measure(rest, beat(duration=(3, 4)))])
    for reader in (parse, songsterr):
        if value is None or isinstance(value, bool):
            reader(doc)
        else:
            with pytest.raises(ValueError):
                reader(doc)


@pytest.mark.parametrize('value', [False, 0, '', [], {}, {'type': 4, 'bpm': 0}])
def test_malformed_legacy_beat_tempo_cannot_hide_on_a_whole_rest(value):
    rest = {'rest': True, 'duration': [1, 1], 'tempo': value, 'notes': [{'rest': True}]}
    doc = raw_score([measure(rest), measure(beat())])
    for reader in (parse, songsterr):
        with pytest.raises(ValueError):
            reader(doc)


def test_modern_grace_remains_active_when_legacy_note_flag_is_present():
    grace = {**beat(fret=5, duration=(1, 4)), 'type': 4, 'graceNote': 'onBeat'}
    rest = {'duration': [1, 2], 'rest': True, 'notes': [{'rest': True}]}
    doc = raw_score([measure(grace, beat(fret=7, duration=(1, 4)), rest)])
    original = deepcopy(doc)
    doc['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['grace'] = True
    assert played(doc) == played(original)
    notes = render(parse(doc))['tracks'][0]['notes']
    assert [n['t'] for n in notes] == [0, .25]
    assert notes[0]['sus'] == .25
    without = deepcopy(original)
    del without['parts'][0]['measures'][0]['voices'][0]['beats'][0]['graceNote']
    assert render(parse(without))['tracks'][0]['notes'][1]['t'] != notes[1]['t']


@pytest.mark.parametrize('field', ['graceNote', 'tremoloBar', 'whammy'])
def test_accepting_legacy_grace_does_not_relax_other_note_technique_guards(field):
    doc = legacy_document()
    doc['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0][field] = True
    for reader in (parse, songsterr):
        with pytest.raises(ValueError):
            reader(doc)


@pytest.mark.parametrize('fault', [None, 'missing', 'wrong_value', 'bool_zero', 'missing_null_value',
                                 'wrong_retention', 'old_contract', 'wrong_note'])
def test_package_checker_requires_exact_metadata_accounting_at_contract_88(tmp_path, fault):
    source, _ = example()
    bar = source['parts'][0]['measures'][0]
    first = bar['voices'][0]['beats'][0]
    bar['index'], first['tempo'], first['notes'][0]['grace'] = 7, {'type': 4, 'bpm': 65}, False
    if fault == 'missing_null_value': first['notes'][0]['grace'] = None
    files, manifest, alignment, path = current_package(tmp_path, source)
    report = json.loads(files['import/compatibility.json'])
    target = next(r for r in report['findings'] if r['feature'] == 'note.grace')
    if fault == 'missing':
        report['findings'].remove(target); report['findingCount'] -= 1
    elif fault == 'wrong_value': target['value'] = True
    elif fault == 'bool_zero': target['value'] = 0
    elif fault == 'missing_null_value': target.pop('value')
    elif fault == 'wrong_retention': target['retained'] = 'discarded'
    elif fault == 'old_contract': report['version'] = 87
    elif fault == 'wrong_note':
        chart_path = manifest['arrangements'][0]['file']
        chart = json.loads(files[chart_path]); chart['notes'][0]['f'] += 1
        files[chart_path] = json.dumps(chart).encode()
    files['import/compatibility.json'] = json.dumps(report).encode()
    result = verify_files(tmp_path, files, path, alignment)
    assert result['status'] == ('failed' if fault else 'passed'), result
    if fault == 'wrong_note':
        assert 'note_f' in {row['code'] for row in result['errors']}
    elif fault:
        assert any(row['code'].startswith('compatibility') for row in result['errors']), result
