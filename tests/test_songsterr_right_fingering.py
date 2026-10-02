"""Picking-hand annotations cannot become fret-hand numbers or block valid music."""
from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from feedback_converter.song_import import ScoreImportError
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.voices import flat
from test_song_import_score import beat, measure, raw_score, import_json
from test_song_import_compatibility_verification import reported_fixture
from test_song_import_verification import verify
from test_songsterr_hybrid_lead import build, song


def test_reference_proves_enum_and_sampled_scheduling_invariance():
    fixture = json.loads((Path(__file__).parent / 'fixtures/songsterr_right_fingering_reference.json').read_text())
    assert fixture['schemaSha256'] == '8b9267cd39f7f3b0511bade44de01cf3fe7c8025d5a7d534f8a6de448c940e17'
    assert fixture['referenceSha256'] == '4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    assert {c['value'] for c in fixture['cases']} == {None, 'P', 'I', 'M', 'A', 'C'}
    assert len({c['performanceSha256'] for c in fixture['cases']}) == 1


@pytest.mark.parametrize('right', [None, 'P', 'I', 'M', 'A', 'C'])
@pytest.mark.parametrize('left', [None, 'T', '3'])
def test_hands_are_independent_and_all_musical_data_unchanged(tmp_path, right, left):
    first = beat(5, string=5, duration=(1, 2), leftFingering=left)
    first['notes'].append({'string':4, 'fret':7, 'leftFingering':'4'})
    tied = beat(5, string=5, duration=(1, 2), tie=True)
    doc = raw_score([measure(first, tied, repeatStart=True, repeat=2)])
    plain = import_json(tmp_path, doc)
    first['notes'][0]['rightFingering'] = right
    tied['notes'][0]['rightFingering'] = right
    # raw_score may retain or copy input structures; assign to the document.
    for b in doc['parts'][0]['measures'][0]['voices'][0]['beats']:
        b['notes'][0]['rightFingering'] = right
    source = deepcopy(doc)
    result = import_json(tmp_path, doc)
    assert result['tracks'] == plain['tracks']
    assert doc == source
    independent = expected(songsterr(doc), {'offset':0, 'scale':1})
    assert [n['note'].get('fg') for n in independent['parts'][0]['notes']] == [n.get('fg') for n in flat(result['tracks'][0])]
    findings = [f for f in result['compatibilityReport']['findings'] if f['feature'] == 'note.rightFingering']
    assert len(findings) == (2 if right else 0)
    assert all(f['value'] == right and f['impact'] == 'display_or_expression' for f in findings)
    inventory = [r for r in result['featureInventory'] if r['field'] == 'rightFingering']
    assert inventory and all(r['handling'] == 'source' and r['representations'] == ['source'] for r in inventory)


@pytest.mark.parametrize('bad', [False, True, 0, 1, '1', 'T', 'p', '', 'thumb', [], {}, ['P']])
@pytest.mark.parametrize('rest', [False, True])
def test_invalid_values_are_located_even_on_rest_notes(tmp_path, bad, rest):
    doc = raw_score([measure(beat(5, rightFingering=bad, rest=rest))])
    with pytest.raises(ScoreImportError) as exc:
        import_json(tmp_path, doc)
    assert any(f['feature'] == 'note.rightFingering' and f['impact'] == 'blocking'
               and f['location'].endswith('/notes/0/rightFingering') for f in exc.value.compatibility['findings'])
    with pytest.raises(ValueError, match='rightFingering'):
        songsterr(doc)


@pytest.mark.parametrize('fault', [None, 'remove_report', 'alter_value', 'invent_left_finger', 'change_pitch', 'shift_attack'])
def test_independent_package_verification_requires_the_hint_and_unchanged_music(tmp_path, fault):
    source, package = reported_fixture()
    source['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['rightFingering'] = 'P'
    report = package['import/compatibility.json']
    report['findingCount'] += 1
    report['findings'].append({'feature':'note.rightFingering','location':'parts/0/measures/0/voices/0/beats/0/notes/0/rightFingering',
        'value':'P','valueTruncated':False,'retained':'original_source','impact':'display_or_expression','category':'game_limitation'})
    chart = package['chart.json']['notes']
    if fault == 'remove_report': report['findings'].pop(); report['findingCount'] -= 1
    if fault == 'alter_value': report['findings'][-1]['value'] = 'I'
    if fault == 'invent_left_finger': chart[0]['fg'] = 0
    if fault == 'change_pitch': chart[0]['f'] += 1
    if fault == 'shift_attack': chart[0]['t'] += .05
    checked = verify(tmp_path, source, package)
    assert checked['status'] == ('failed' if fault else 'passed'), checked


def test_real_hybrid_package_retains_source_hint_without_a_new_fret_hand_hint(tmp_path):
    doc = song()
    doc['parts'][1]['measures'][1]['voices'][0]['beats'][0]['notes'][0]['rightFingering'] = 'I'
    *_, archive, report = build(tmp_path, doc, mapped=True, difficulty=True)
    assert report['status'] == 'passed', report
    with ZipFile(archive) as z:
        manifest = yaml.safe_load(z.read('manifest.yaml'))
        assert json.loads(z.read(manifest['song_import']['sourceFile'])) == doc
        hybrid = json.loads(z.read(manifest['arrangements'][-1]['file']))
        assert not any('fg' in n for n in flat(hybrid))
        findings = json.loads(z.read(manifest['song_import']['compatibilityFile']))['findings']
        assert any(f['feature'] == 'note.rightFingering' and f['value'] == 'I' for f in findings)
