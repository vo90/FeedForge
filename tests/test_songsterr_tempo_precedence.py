"""Exact-coordinate tempo precedence, not approximate BPM reconciliation."""
from copy import deepcopy
from fractions import Fraction as F
import json
from pathlib import Path

import pytest

from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.compatibility import inspect_songsterr
from test_song_import_score import beat, measure, raw_score
from test_songsterr_combined_tempo import clocks, rates
from test_song_import_compatibility_verification import reported_fixture
from test_song_import_verification import verify

REFERENCE=json.loads((Path(__file__).parent/'fixtures/songsterr_tempo_precedence_reference.json').read_text())
CASES=REFERENCE['cases']


@pytest.mark.parametrize('case', range(len(CASES)))
def test_full_instruction_replacement_matches_pinned_preparation(case):
    assert REFERENCE['referenceSha256']=='4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    fixture=CASES[case]
    doc=raw_score([]);doc['parts'][0]=deepcopy(fixture['input'])
    before=deepcopy(doc)
    wanted=rates(fixture['expected']['tempos'])
    assert clocks(doc)==(wanted,wanted)
    actual=render(parse(doc)); independent=expected(songsterr(doc),{'offset':0,'scale':1})
    assert actual['duration']==pytest.approx(independent['score_duration'])
    assert len(actual['tracks'][0]['notes'])==(7 if fixture['repeat'] else 4)
    assert doc==before


@pytest.mark.parametrize('position', [None, 0, [0,1], '0'])
def test_exact_zero_coordinates_and_last_entry_order(position):
    doc=raw_score([measure(beat())]);auto=doc['parts'][0]['automations']
    mark={'measure':0,'bpm':60}
    if position is not None:mark['position']=position
    auto['tempo'].append(mark)
    assert clocks(doc)==({(0,F(0)):60},{(0,F(0)):60})
    auto['tempo'].reverse()
    assert clocks(doc)==({(0,F(0)):120},{(0,F(0)):120})


def test_nearby_positions_are_not_merged_or_rounded():
    doc=raw_score([measure(beat())])
    doc['parts'][0]['automations']['tempo'] += [{'measure':0,'position':1,'bpm':60},{'measure':0,'position':2,'bpm':90}]
    wanted={(0,F(0)):120,(0,F(1,960)):60,(0,F(1,480)):90}
    assert clocks(doc)==(wanted,wanted)
    assert not inspect_songsterr(doc)['findings']


def test_every_superseded_entry_points_to_final_winner():
    doc=raw_score([measure(beat())]);tempos=doc['parts'][0]['automations']['tempo']
    tempos += [{'measure':0,'position':0,'bpm':100},{'measure':0,'bpm':80}]
    report=inspect_songsterr(doc)
    assert len(report['findings'])==2
    for i,row in enumerate(report['findings']):
        assert row['feature']=='tempo.superseded'
        assert row['location']==f'parts/0/automations/tempo/{i}'
        assert row['value']=={'authored':tempos[i],'used':tempos[2],'selectedIndex':2}


@pytest.mark.parametrize('field,value', [('bpm',0),('bpm',-10),('bpm','NaN'),('type',0),('type',-4),
    ('linear',1),('dotted',1),('visible',0),('text',7),('measure',-1),('measure',1),('position',-1),('position',3840),('unknownTempo',True)])
def test_replacement_does_not_hide_invalid_earlier_entries(field,value):
    doc=raw_score([measure(beat())]);tempos=doc['parts'][0]['automations']['tempo']
    tempos.append(deepcopy(tempos[0]));tempos[0][field]=value
    for reader in (parse,songsterr):
        with pytest.raises(ValueError):reader(doc)


def test_tracks_must_agree_after_resolving_their_own_entries():
    doc=raw_score([measure(beat())])
    doc['tracks'].append({**deepcopy(doc['tracks'][0]),'id':1})
    doc['parts'].append(deepcopy(doc['parts'][0]))
    doc['parts'][0]['automations']['tempo'].append({'measure':0,'bpm':60})
    for reader in (parse,songsterr):
        with pytest.raises(ValueError,match='disagree'):reader(doc)
    doc['parts'][1]['automations']['tempo'].append({'measure':0,'bpm':60})
    assert clocks(doc)==({(0,F(0)):60},{(0,F(0)):60})


@pytest.mark.parametrize('fault',[None,'remove_report','wrong_winner','wrong_source_value','wrong_tempo','wrong_attack'])
def test_independent_package_checker_validates_resolution_and_music(tmp_path,fault):
    source,package=reported_fixture()
    tempos=source['parts'][0]['automations']['tempo']
    tempos.insert(0,{'measure':0,'position':0,'bpm':90,'type':4})
    report=package['import/compatibility.json']
    report['findings'][0]['location']='parts/0/automations/tempo/1/text'
    report['findings'].append({'feature':'tempo.superseded','location':'parts/0/automations/tempo/0',
        'value':{'authored':deepcopy(tempos[0]),'used':deepcopy(tempos[1]),'selectedIndex':1},
        'valueTruncated':False,'retained':'original_source','impact':'display_or_expression','category':'source_interpretation'})
    report['findingCount']+=1
    if fault=='remove_report':report['findings'].pop();report['findingCount']-=1
    if fault=='wrong_winner':report['findings'][-1]['value']['selectedIndex']=0
    if fault=='wrong_source_value':report['findings'][-1]['value']['authored']['bpm']=100
    if fault=='wrong_tempo':package['timeline.json']['tempos'][0]['bpm']=90
    if fault=='wrong_attack':package['chart.json']['notes'][1]['t']+=.1
    result=verify(tmp_path,source,package)
    assert result['status']==('failed' if fault else 'passed'),result


@pytest.mark.parametrize('version', [45, 46])
def test_new_accounting_does_not_require_rewriting_historical_reports(tmp_path, version):
    source, package = reported_fixture()
    tempos = source['parts'][0]['automations']['tempo']
    tempos.append({'measure': 0, 'position': 0, 'bpm': 120})
    package['import/compatibility.json']['version'] = version
    result = verify(tmp_path, source, package)
    assert result['status'] == ('passed' if version == 45 else 'failed'), result
    if version == 46:
        assert 'compatibility_coverage' in {e['code'] for e in result['errors']}


def test_current_contract_cannot_downgrade_accounting_version(tmp_path):
    source, package = reported_fixture()
    package['import/compatibility.json']['version'] = 45
    package['manifest.yaml']['song_import']['preservationContract'] = 46
    result = verify(tmp_path, source, package)
    assert result['status'] == 'failed', result
    assert 'compatibility_version' in {e['code'] for e in result['errors']}
