"""Pedal expression is retained and disclosed without rewriting guitar notes."""
from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from feedback_converter.song_import import ScoreImportError
from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from test_song_import_score import beat, measure, raw_score, import_json
from test_song_import_compatibility_verification import reported_fixture
from test_song_import_verification import verify
from test_songsterr_hybrid_lead import build, song, rest


def test_pinned_reference_distinguishes_pedal_controllers_from_note_timing():
    r = json.loads((Path(__file__).parent/'fixtures/songsterr_sustain_pedal_reference.json').read_text())
    assert r['referenceSha256'] == '4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    assert r['schemaSha256'] == '8b9267cd39f7f3b0511bade44de01cf3fe7c8025d5a7d534f8a6de448c940e17'
    assert len(r['cases']) == 12
    for c in r['cases']:
        assert c['active']['noteHash'] == c['control']['noteHash']
        assert c['active']['controllers'] and not c['control']['controllers']
        assert c['active']['spans'] == ([[0,8]] if c['shape']=='overlap' else [[0,2],[3,6],[7,8]])


@pytest.mark.parametrize('enabled', [None, False, True])
@pytest.mark.parametrize('shape', ['rest', 'tie', 'overlap'])
def test_notes_ties_rests_repeats_and_other_expressions_are_unchanged(tmp_path, enabled, shape):
    first = beat(5,duration=(1,4))
    first['notes'].append({'fret':7,'string':1})
    first['letRing'] = True
    continuation = beat(5,duration=(1,4),tie=True) if shape=='tie' else rest((1,4))
    doc = raw_score([measure(first, continuation, beat(9,duration=(1,4)), rest((1,4)),repeatStart=True,repeat=2)])
    if shape=='overlap':
        doc['parts'][0]['measures'][0]['voices'].append({'beats':[rest((1,2)),rest((1,2))]})
    before = deepcopy(doc)
    plain = import_json(tmp_path, before)
    for voice in doc['parts'][0]['measures'][0]['voices']:
        for b in voice['beats']:
            b['sustainPedal']=enabled
    original = deepcopy(doc)
    converted = import_json(tmp_path, doc)
    assert doc == original
    assert converted['tracks'] == plain['tracks']
    assert expected(songsterr(doc),{'offset':0,'scale':1}) == expected(songsterr(before),{'offset':0,'scale':1})
    fields = [x for x in converted['featureInventory'] if x['field']=='sustainPedal']
    assert fields and all(x['handling']=='source' and x['representations']==['source'] for x in fields)
    findings = [x for x in converted['compatibilityReport']['findings'] if x['feature']=='beat.sustainPedal']
    expected_findings = (6 if shape=='overlap' else 4) if enabled else 0
    assert len(findings) == expected_findings
    assert all(x['value'] is True and x['impact']=='display_or_expression' for x in findings)


@pytest.mark.parametrize('value', [0, 1, 'true', 'false', '', [], {}, ['on']])
@pytest.mark.parametrize('silent', [False, True])
def test_wrong_types_block_even_when_falsy_or_on_a_rest(tmp_path,value,silent):
    b = rest() if silent else beat()
    b['sustainPedal']=value
    doc = raw_score([measure(b)])
    with pytest.raises(ScoreImportError) as exc:
        import_json(tmp_path,doc)
    assert any(f['feature']=='beat.sustainPedal' and f['impact']=='blocking' and f['location'].endswith('/sustainPedal')
               for f in exc.value.compatibility['findings'])
    with pytest.raises(ValueError,match='sustainPedal'):
        songsterr(doc)


@pytest.mark.parametrize('program,active,warning', [(30,True,True),(33,True,True),(30,False,False),(0,True,False)])
def test_only_active_selected_guitar_bass_expression_is_reported(program,active,warning):
    doc = raw_score([measure({**beat(),'sustainPedal':active})])
    doc['tracks'][0]['instrumentId']=program
    f=[x for x in inspect_songsterr(doc)['findings'] if x['feature']=='beat.sustainPedal']
    assert bool(f)==warning


@pytest.mark.parametrize('fault', [None,'remove_report','alter_value','extend_sustain','drop_note','shift_attack','invent_let_ring'])
def test_package_checker_requires_annotation_and_does_not_excuse_changed_music(tmp_path,fault):
    source, package = reported_fixture()
    source['parts'][0]['measures'][0]['voices'][0]['beats'][0]['sustainPedal']=True
    report=package['import/compatibility.json']
    report['findingCount']+=1
    report['findings'].append({'feature':'beat.sustainPedal','location':'parts/0/measures/0/voices/0/beats/0/sustainPedal',
        'value':True,'valueTruncated':False,'retained':'original_source','impact':'display_or_expression','category':'game_limitation'})
    notes=package['chart.json']['notes']
    if fault=='remove_report':report['findings'].pop();report['findingCount']-=1
    if fault=='alter_value':report['findings'][-1]['value']=False
    if fault=='extend_sustain':notes[0]['sus']+=.3
    if fault=='drop_note':notes.pop()
    if fault=='shift_attack':notes[0]['t']+=.05
    if fault=='invent_let_ring':notes[0]['lr']=True
    checked=verify(tmp_path,source,package)
    assert checked['status']==('failed' if fault else 'passed'),checked


def test_hybrid_difficulty_package_keeps_pedal_source_and_independent_report(tmp_path):
    doc=song()
    doc['parts'][1]['measures'][1]['voices'][0]['beats'][0]['sustainPedal']=True
    *_,archive,report=build(tmp_path,doc,mapped=True,difficulty=True)
    assert report['status']=='passed',report
    with ZipFile(archive) as z:
        manifest=yaml.safe_load(z.read('manifest.yaml'))
        assert json.loads(z.read(manifest['song_import']['sourceFile']))==doc
        findings=json.loads(z.read(manifest['song_import']['compatibilityFile']))['findings']
        assert any(f['feature']=='beat.sustainPedal' and f['value'] is True for f in findings)
