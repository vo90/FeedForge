"""Archive mutations cannot conceal legacy flags or invent musical effects."""
from copy import deepcopy
import json
from zipfile import ZipFile

import pytest
import yaml

from feedback_converter.song_import import load_performance
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.verification import verify_import
from test_song_import_builder import inputs
from test_song_import_score import beat, measure, raw_score


def document(value=True, field='harmonic'):
    first = beat(12, duration=(1, 2), harmonic='natural')
    first['notes'].append({'string': 1, 'fret': 12, 'harmonic': 'natural'})
    first[field] = value
    second = beat(5, duration=(1, 2), bend={
        'points': [{'position': 0, 'tone': 0}, {'position': 60, 'tone': 100}]})
    second['fadeIn'] = True
    return raw_score([measure(first, second)])


def package(tmp_path, raw):
    path = tmp_path/'source.json'
    path.write_text(json.dumps(raw), encoding='utf-8')
    original = path.read_bytes()
    performance = load_performance(path)
    _, audio, _, job = inputs(tmp_path)
    alignment = {'status': 'validated', 'offset': 1, 'scale': 1}
    built = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path/'out',
        source_path=path, compatibility=performance['compatibilityReport'],
        recipe={'preservationContract': 91})
    with ZipFile(built['stagingPath']) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml'])
    assert files[manifest['song_import']['sourceFile']] == original
    assert path.read_bytes() == original
    return path, files, manifest, alignment


def verify(tmp_path, path, files, alignment):
    target = tmp_path/'checked.feedpak'
    with ZipFile(target, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return verify_import(path, target, alignment)


@pytest.mark.parametrize('field', ['harmonic', 'fadeIn'])
@pytest.mark.parametrize('value', [None, False, True])
def test_flags_are_typed_source_evidence_and_never_chart_effects(tmp_path, field, value):
    path, files, manifest, alignment = package(tmp_path, document(value, field))
    result = verify(tmp_path, path, files, alignment)
    assert result['status'] == 'passed', result
    report = json.loads(files[manifest['song_import']['compatibilityFile']])
    rows = [r for r in report['findings'] if r['feature'] in ('beat.harmonic', 'beat.fadeIn')]
    assert len(rows) == 2
    first = next(r for r in rows if '/beats/0/' in r['location'])
    assert first['value'] is value
    expected = 'display_or_expression' if field == 'fadeIn' and value is True else 'source_retained'
    assert first['impact'] == expected
    chart = json.loads(files[manifest['arrangements'][0]['file']])
    assert all('fadeIn' not in n for n in chart['notes'])
    assert chart['notes'][0]['f'] == 5 and chart['notes'][0]['bn']
    assert [(n['f'], n['hn'], n['hps']) for n in chart['chords'][0]['notes']] == [(12, 12, 12)]*2


@pytest.mark.parametrize('field', ['harmonic', 'fadeIn'])
@pytest.mark.parametrize('value', [None, False, True])
@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'value_type', 'missing_value',
    'category', 'impact', 'work_status', 'retention', 'report_version', 'contract'])
def test_archive_requires_exact_legacy_flag_ledger(tmp_path, field, value, fault):
    path, files, manifest, alignment = package(tmp_path, document(value, field))
    report_path = manifest['song_import']['compatibilityFile']
    report = json.loads(files[report_path])
    row = next(r for r in report['findings'] if r['feature'] == 'beat.'+field and '/beats/0/' in r['location'])
    if fault == 'missing': report['findings'].remove(row)
    elif fault == 'duplicate': report['findings'].append(deepcopy(row))
    elif fault == 'value_type': row['value'] = 1 if value is True else 0
    elif fault == 'missing_value': row.pop('value')
    elif fault == 'category': row['category'] = 'wrong'
    elif fault == 'impact': row['impact'] = 'wrong'
    elif fault == 'work_status': row['workStatus'] = 'wrong'
    elif fault == 'retention': row['retained'] = 'discarded'
    elif fault == 'report_version': report['version'] = 90
    elif fault == 'contract':
        manifest['song_import']['preservationContract'] = 90
        files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    report['findingCount'] = len(report['findings'])
    files[report_path] = json.dumps(report).encode()
    result = verify(tmp_path, path, files, alignment)
    assert result['status'] == 'failed', (field, value, fault, result)
    assert any(r['code'].startswith('compatibility') for r in result['errors']), result


@pytest.mark.parametrize('fault', ['harmonic_pitch', 'harmonic_node', 'bend', 'attack', 'duration', 'notation'])
def test_qualifying_metadata_does_not_excuse_musical_corruption(tmp_path, fault):
    path, files, manifest, alignment = package(tmp_path, document())
    arrangement = manifest['arrangements'][0]
    chart = json.loads(files[arrangement['file']])
    note = chart['chords'][0]['notes'][0]
    if fault == 'harmonic_pitch': note['hps'] = 19
    elif fault == 'harmonic_node': note['hn'] = 7
    elif fault == 'bend': chart['notes'][0]['bn'] += 1
    elif fault == 'attack': chart['notes'][0]['t'] += .1
    elif fault == 'duration': chart['notes'][0]['sus'] += .1
    elif fault == 'notation':
        notation = json.loads(files[arrangement['notation']])
        notation['measures'][0]['staves']['staff']['voices'][0]['beats'][0]['notes'][0]['fret'] = 7
        files[arrangement['notation']] = json.dumps(notation).encode()
    files[arrangement['file']] = json.dumps(chart).encode()
    result = verify(tmp_path, path, files, alignment)
    assert result['status'] == 'failed', result


@pytest.mark.parametrize('id', ['drums', 0])
def test_excluded_track_flags_are_retained_without_qualifying_its_note_vocabulary(tmp_path, id):
    raw = document()
    raw['tracks'].append({'id': id, 'name': 'Drums', 'instrumentId': 128})
    raw['parts'].append({'measures': [measure({'duration': [1, 1], 'harmonic': True,
        'fadeIn': True, 'notes': [{'fret': -9, 'string': 99, 'harmonic': 'drum-only'}]})]})
    path, files, manifest, alignment = package(tmp_path, raw)
    result = verify(tmp_path, path, files, alignment)
    assert result['status'] == 'passed', result
    report = json.loads(files[manifest['song_import']['compatibilityFile']])
    rows = [r for r in report['findings'] if r['feature'] in ('beat.harmonic', 'beat.fadeIn') and r['location'].startswith('parts/1/')]
    assert len(rows) == 2
    assert all(r['category'] == 'source_metadata' and r['impact'] == r['workStatus'] == 'source_retained' for r in rows)
