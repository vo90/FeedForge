"""Tremolo picking uses the established instruction, never invented attacks."""
from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from feedback_converter.song_import import ScoreImportError
from feedback_converter.song_import.verify_source import songsterr, UnverifiedFeature
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.voices import flat
from test_song_import_score import beat, measure, raw_score, import_json
from test_song_import_compatibility_verification import reported_fixture
from test_song_import_verification import verify
from test_songsterr_hybrid_lead import build, song


def test_pinned_reference_scope_maps_to_existing_game_instruction(tmp_path):
    fixture = json.loads((Path(__file__).parent / 'fixtures/songsterr_tremolo_reference.json').read_text())
    assert fixture['referenceSha256'] == '4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    for case in fixture['cases']:
        b = beat(5, string=0, duration=(1, 4))
        b['notes'].append({'fret':7, 'string':1})
        if case['beat'] is not None: b['tremolo'] = case['beat']
        if case['note'] is not None: b['notes'][0]['tremolo'] = case['note']
        notes = sorted(flat(import_json(tmp_path, raw_score([measure(b)]))['tracks'][0]), key=lambda n: -n['s'])
        wanted = [bool(count) for count in case['extraNoteOns']]
        if case['id'] == 'noteFalse':
            # Explicit, existing inactive-flag policy; not Songsterr synthesis.
            assert wanted == [True, False]
            wanted = [False, False]
        assert [n.get('tr', False) for n in notes] == wanted, case['id']
        assert len(notes) == 2 and all(n['sus'] == .5 for n in notes)


@pytest.mark.parametrize('scope', ['note', 'beat', 'both'])
@pytest.mark.parametrize('value', [[1, 8], [1, 16], [1, 32], True, False, None])
def test_instruction_scope_and_unchanged_timing(tmp_path, scope, value):
    b = beat(5, string=5)
    b['notes'].append({'string': 4, 'fret': 7})
    if scope in ('note', 'both'): b['notes'][0]['tremolo'] = value
    if scope in ('beat', 'both'): b['tremolo'] = value
    doc = raw_score([measure(b, repeatStart=True, repeat=2)])
    original = deepcopy(doc)
    p = import_json(tmp_path, doc)
    notes = flat(p['tracks'][0])
    active = value is not None and value is not False
    assert [n.get('tr', False) for n in notes] == [active, active and scope != 'note'] * 2
    assert [(n['t'], n['sus']) for n in notes] == [(0, 2), (0, 2), (2, 2), (2, 2)]
    independent = expected(songsterr(doc), {'offset': 0, 'scale': 1})
    assert [n['note'].get('tr', False) for n in independent['parts'][0]['notes']] == [n.get('tr', False) for n in notes]
    assert doc == original
    findings = [f for f in p['compatibilityReport']['findings'] if f['feature'].endswith('.tremolo')]
    assert len(findings) == (2 if scope == 'both' else 1) * int(active)
    assert all(f['impact'] == 'display_or_expression' and f['value'] == value for f in findings)


@pytest.mark.parametrize('scope', ['note', 'beat'])
@pytest.mark.parametrize('bad', [0, 1, '16', [], {}, [0, 16], [1, 0], [-1, 16], [True, 16], [1, 16.0], [1], [1, 16, 32]])
def test_malformed_subdivisions_fail_with_location(tmp_path, scope, bad):
    b = beat()
    (b if scope == 'beat' else b['notes'][0])['tremolo'] = bad
    doc = raw_score([measure(b)])
    with pytest.raises(ScoreImportError) as caught:
        import_json(tmp_path, doc)
    assert any(f['feature'] == scope + '.tremolo' and f['impact'] == 'blocking'
               and f['location'].endswith('/tremolo') for f in caught.value.compatibility['findings'])
    with pytest.raises((UnverifiedFeature, ValueError)):
        songsterr(doc)


@pytest.mark.parametrize('first,last', [(True, True), (True, False), (False, True)])
def test_ties_keep_existing_whole_sustain_instruction_with_disclosed_limitation(tmp_path, first, last):
    doc = raw_score([measure(beat(duration=(1, 2), tremolo=[1,16] if first else None),
                             beat(duration=(1, 2), tie=True, tremolo=[1,16] if last else None))])
    p = import_json(tmp_path, doc)
    notes = flat(p['tracks'][0])
    assert len(notes) == 1 and notes[0]['tr'] is True and notes[0]['sus'] == 2
    independent = expected(songsterr(doc), {'offset':0, 'scale':1})['parts'][0]['notes']
    assert len(independent) == 1 and independent[0]['note']['tr'] is True
    assert any('one marker for the whole sustain' in f['message'] for f in p['compatibilityReport']['findings'])


@pytest.mark.parametrize('fault', [None, 'remove_mark', 'extra_mark', 'move_attack', 'change_pitch', 'remove_warning', 'change_rate'])
def test_independent_archive_verification_rejects_corruption(tmp_path, fault):
    source, package = reported_fixture()
    note = source['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]
    note['tremolo'] = [1,16]
    chart = package['chart.json']['notes']
    chart[0]['tr'] = True
    report = package['import/compatibility.json']
    report['findingCount'] += 1
    report['findings'].append({'feature':'note.tremolo', 'location':'parts/0/measures/0/voices/0/beats/0/notes/0/tremolo',
        'value':[1,16], 'valueTruncated':False, 'retained':'original_source',
        'impact':'display_or_expression', 'category':'game_limitation'})
    if fault == 'remove_mark': del chart[0]['tr']
    if fault == 'extra_mark': chart[1]['tr'] = True
    if fault == 'move_attack': chart[0]['t'] += .02
    if fault == 'change_pitch': chart[0]['f'] += 1
    if fault == 'remove_warning': report['findings'].pop(); report['findingCount'] -= 1
    if fault == 'change_rate': report['findings'][-1]['value'] = [1,8]
    result = verify(tmp_path, source, package)
    assert result['status'] == ('passed' if fault is None else 'failed'), result


def test_real_package_hybrid_and_source_preserve_tremolo(tmp_path):
    doc = song()
    donor = doc['parts'][1]['measures'][1]['voices'][0]['beats'][0]['notes'][0]
    donor['tremolo'] = [1,16]
    *_, archive, report = build(tmp_path, doc, mapped=True, difficulty=True)
    assert report['status'] == 'passed', report
    with ZipFile(archive) as z:
        manifest = yaml.safe_load(z.read('manifest.yaml'))
        hybrid = json.loads(z.read(manifest['arrangements'][-1]['file']))
        assert any(n.get('tr') for n in flat(hybrid))
        assert json.loads(z.read(manifest['song_import']['sourceFile'])) == doc
