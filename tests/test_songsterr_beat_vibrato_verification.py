"""Beat-level legacy vibrato warnings are checked independently of conversion."""
from copy import deepcopy
import json
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_compatibility_verification import reported_fixture
from feedback_converter.song_import.verification import Check, _compatibility_report, verify_import
from feedback_converter.song_import.verify_source import songsterr
from test_song_import_score import beat, measure, raw_score, import_json
from test_songsterr_hybrid_lead import build, song, rest


def warning(field, value=True, beat_index=0):
    return {
        'feature': 'beat.' + field,
        'location': f'parts/0/measures/0/voices/0/beats/{beat_index}/{field}',
        'value': value, 'valueTruncated': False, 'retained': 'original_source',
        'impact': 'display_or_expression', 'category': 'game_limitation',
    }


def check_report(tmp_path, source, report):
    path = tmp_path/'source.json'
    path.write_text(json.dumps(source), encoding='utf-8')
    check = Check()
    _compatibility_report(report, path, songsterr(source), check, preservation_contract=63)
    return check.errors


@pytest.mark.parametrize('field', ['vibrato', 'wideVibrato'])
@pytest.mark.parametrize('fault', [None, 'missing', 'value', 'location', 'duplicate',
    'invented', 'retention', 'impact', 'category'])
def test_source_bound_warning_rejects_report_corruption(tmp_path, field, fault):
    # Hand-authored report: no production inventory constructs expectations.
    source, package = reported_fixture()
    source['parts'][0]['measures'][0]['voices'][0]['beats'][0][field] = True
    report = package['import/compatibility.json']
    report['findings'].append(warning(field))
    row = report['findings'][-1]
    if fault == 'missing': report['findings'].pop()
    elif fault == 'value': row['value'] = False
    elif fault == 'location': row['location'] = warning(field, beat_index=1)['location']
    elif fault == 'duplicate': report['findings'].append(deepcopy(row))
    elif fault == 'invented': report['findings'].append(warning(field, beat_index=1))
    elif fault == 'retention': row['retained'] = 'discarded'
    elif fault == 'impact': row['impact'] = 'gameplay_omission'
    elif fault == 'category': row['category'] = 'source_interpretation'
    report['findingCount'] = len(report['findings'])
    errors = check_report(tmp_path, source, report)
    assert bool(errors) == bool(fault), errors
    if fault: assert any(e['code'].startswith('compatibility_') for e in errors)


@pytest.mark.parametrize('field', ['vibrato', 'wideVibrato'])
@pytest.mark.parametrize('value', [None, False, '', [], {}])
def test_inactive_markings_need_no_warning_and_reject_fabricated_one(tmp_path, field, value):
    source, package = reported_fixture()
    source['parts'][0]['measures'][0]['voices'][0]['beats'][0][field] = value
    report = package['import/compatibility.json']
    assert not check_report(tmp_path, source, report)
    report['findings'].append(warning(field, value))
    report['findingCount'] += 1
    assert 'compatibility_coverage' in {e['code'] for e in check_report(tmp_path, source, report)}


@pytest.mark.parametrize('context', ['attack', 'tie', 'rest', 'chord', 'voices', 'repeat'])
@pytest.mark.parametrize('bass', [False, True])
def test_production_package_keeps_beat_warnings_without_changing_notes(tmp_path, context, bass):
    from test_song_import_builder import inputs
    from feedback_converter.song_import.builder import build_feedpak

    follow = beat(3, duration=(1, 2), **({'tie': True} if context == 'tie' else {}))
    if context == 'rest': follow = rest((1, 2))
    doc = raw_score([measure(beat(3, duration=(1, 2)), follow)])
    bar = doc['parts'][0]['measures'][0]
    if context == 'chord': follow['notes'].append({'fret': 5, 'string': 1})
    if context == 'voices': bar['voices'].append({'beats': [rest()]})
    if context == 'repeat': bar.update(repeatStart=True, repeat=2)
    if bass: doc['tracks'][0].update(instrumentId=33, tuning=[43, 38, 33, 28])
    plain = import_json(tmp_path, doc)
    # Both forms at one location are separate source facts, including on a rest.
    follow.update(vibrato=True, wideVibrato=True)
    if context == 'voices': bar['voices'][1]['beats'][0]['vibrato'] = True
    performance = import_json(tmp_path, doc)
    def without_vibrato_and_notation(value):
        if isinstance(value, dict):
            return {k: without_vibrato_and_notation(v) for k, v in value.items()
                    if k not in ('notation', 'vb', 'vibrato_marks')}
        if isinstance(value, list): return [without_vibrato_and_notation(v) for v in value]
        return value
    # Existing legacy vibrato rendering/notation is retained; pitch, attacks,
    # durations, ties and every unrelated technique must stay identical.
    assert without_vibrato_and_notation(performance['tracks']) == without_vibrato_and_notation(plain['tracks'])
    findings = performance['compatibilityReport']['findings']
    assert len(findings) == (3 if context == 'voices' else 2)
    assert all(f['impact'] == 'display_or_expression' for f in findings)
    _, audio, _, job = inputs(tmp_path)
    alignment = {'status': 'validated', 'offset': .25, 'scale': 1}
    source = tmp_path/'score.json'
    built = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path/'out',
        source_path=source, compatibility=performance['compatibilityReport'], recipe={'preservationContract': 63})
    result = verify_import(source, built['stagingPath'], alignment)
    assert result['status'] == 'passed', result
    if context == 'attack':
        with ZipFile(built['stagingPath']) as z: original = {n: z.read(n) for n in z.namelist()}
        manifest = yaml.safe_load(original['manifest.yaml'])
        chart_path = manifest['arrangements'][0]['file']
        for fault in ('fret', 'attack', 'sustain', 'missing_vibrato', 'shifted_vibrato', 'missing_warning'):
            files = dict(original)
            chart = json.loads(files[chart_path]); note = chart['notes'][1]
            if fault == 'fret': note['f'] += 1
            elif fault == 'attack': note['t'] += .1
            elif fault == 'sustain': note['sus'] += .1
            elif fault == 'missing_vibrato': del note['vibrato_marks']
            elif fault == 'shifted_vibrato': note['vibrato_marks'][0]['start'] += .1
            elif fault == 'missing_warning':
                report_path = manifest['song_import']['compatibilityFile']
                report = json.loads(files[report_path])
                report['findings'].pop(); report['findingCount'] -= 1
                files[report_path] = json.dumps(report).encode()
            files[chart_path] = json.dumps(chart).encode()
            mutated = tmp_path/f'{fault}.feedpak'
            with ZipFile(mutated, 'w') as z:
                for name, content in files.items(): z.writestr(name, content)
            checked = verify_import(source, mutated, alignment)
            assert checked['status'] == 'failed', fault


@pytest.mark.parametrize('mapped', [False, True])
def test_hybrid_with_note_vibrato_and_beat_warning_retains_both(tmp_path, mapped):
    doc = song()
    first = doc['parts'][0]['measures'][0]['voices'][0]['beats'][0]
    first.update(vibrato=True, wideVibrato=True)
    first['notes'][0]['leftHandVibrato'] = 'slight'
    doc['parts'][1]['measures'][1]['voices'][0]['beats'][0]['wideVibrato'] = True
    source, _, _, _, archive, result = build(tmp_path, doc, mapped=mapped, difficulty=True)
    assert result['status'] == 'passed', result
    with ZipFile(archive) as z:
        manifest = yaml.safe_load(z.read('manifest.yaml'))
        assert z.read(manifest['song_import']['sourceFile']) == source.read_bytes()
        report = json.loads(z.read(manifest['song_import']['compatibilityFile']))
        rows = [f for f in report['findings'] if f['feature'] in ('beat.vibrato', 'beat.wideVibrato')]
        assert len(rows) == 3 and all(f['workStatus'] == 'display_limitation' for f in rows)
        hybrid = next(a for a in manifest['arrangements'] if a['name'] == 'Hybrid Lead')
        chart = json.loads(z.read(hybrid['file']))
        assert any(n.get('vibrato_marks') for n in chart['notes'])
