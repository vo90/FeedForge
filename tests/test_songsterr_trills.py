"""Trill fidelity: raw-source oracle, exact boundaries and corrupt archives."""
from copy import deepcopy
from fractions import Fraction as F
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, measure, raw_score
from feedback_converter.song_import.model import ScoreImportError
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr, UnverifiedFeature
from feedback_converter.song_import.verify_timeline import expected


def trill(fret=7, auxiliary=9, speed=60, duration=(1,4), **kwargs):
    return beat(fret=fret, duration=duration, trill={'auxiliaryFret':auxiliary,'speed':speed}, **kwargs)


def compare(document):
    before = deepcopy(document)
    actual = render(parse(document))
    reference = expected(songsterr(document), {'offset':0,'scale':1})
    assert actual.get('trillEvidence', []) == reference['trills']
    assert before == document
    from feedback_converter.song_import.verification import Check, _notes, _chords
    track, part = actual['tracks'][0], reference['parts'][0]
    rows = [(n, 'note') for n in track['notes']] + [({**n,'t':c['t']},'chord') for c in track['chords'] for n in c['notes']]
    check = Check()
    _notes(part['notes'], rows, check, part['source'], actual['duration'])
    _chords(part['notes'], track, check, part['source'])
    assert not check.errors
    return actual


@pytest.mark.parametrize('main,auxiliary', [(7,9), (9,7), (0,2), (2,0)])
def test_pitch_articulation_and_contiguous_endpoints(main, auxiliary):
    perf = compare(raw_score([measure(trill(main, auxiliary))]))
    notes = perf['tracks'][0]['notes']
    assert len(notes) == 8
    assert [n['f'] for n in notes] == [main, auxiliary]*4
    assert not notes[0].get('ho') and not notes[0].get('po')
    for previous, note in zip(notes,notes[1:]):
        assert note['t'] == pytest.approx(previous['t']+previous['sus'])
        assert note['ho' if note['f'] > previous['f'] else 'po']
        assert previous['ln']
        assert 'pkd' not in note
    assert notes[-1]['t']+notes[-1]['sus'] == .5
    assert not notes[-1].get('ln')


def test_tie_across_bars_and_outgoing_legato_use_final_pitch():
    doc=raw_score([measure(beat(fret=3,duration=(3,4)),trill(9,7,duration=(1,4))),
                   measure(beat(fret=9,duration=(1,4),tie=True,hp=True),beat(fret=8,duration=(1,4)))])
    doc['parts'][0]['measures'][0]['voices'][0]['beats'][0].update(type=2,dots=1)
    perf=compare(doc);record=perf['trillEvidence'][0];notes=perf['tracks'][0]['notes']
    assert len(record['sourceIds'])==2 and len(record['events'])==16
    assert notes[-1]['f']==8 and notes[-1]['ho']  # final trill pitch is 7, not 9
    assert not any(n.get('po') for n in notes[-1:])
    assert len(perf['tracks'][0]['notation']['measures']) == 2


def test_initial_legato_repeats_and_final_remainder():
    doc=raw_score([measure(beat(fret=5,duration=(1,4),hp=True),trill(speed=79),repeatStart=True,repeat=2)])
    perf=compare(doc)
    a,b=perf['trillEvidence']
    assert a['occurrence']==1 and b['occurrence']==2 and a['id'] != b['id']
    assert a['events'][0]['articulation']=='ho'
    assert a['events'][-1]['fret']==9
    assert F(a['events'][-1]['endQuarter'])==2
    assert F(a['events'][-1]['endQuarter'])-F(a['events'][-1]['startQuarter']) > F(a['ticks'],a['tpqn'])


def test_simultaneous_trills_keep_chord_ownership():
    first = trill()
    second = deepcopy(first['notes'][0]); second['string'] = 1
    first['notes'].append(second)
    perf = compare(raw_score([measure(first)]))
    assert len(perf['tracks'][0]['chords']) == 8
    assert perf['tracks'][0]['notes'] == []
    written = perf['tracks'][0]['notation']['measures'][0]['staves']['staff']['voices'][0]['beats']
    assert len(written) == 1 and len(written[0]['notes']) == 2


def test_untied_rearticulations_restart_and_rest_is_kept():
    doc = raw_score([measure(trill(), {'duration':[1,4], 'rest':True, 'notes':[]}, trill())])
    perf = compare(doc)
    assert len(perf['trillEvidence']) == 2
    notes = perf['tracks'][0]['notes']
    assert notes[8]['t'] == 1 and notes[8]['f'] == 7
    assert not notes[8].get('ho') and not notes[8].get('po')


def test_swing_changes_duration_not_written_trill_speed():
    doc = raw_score([measure(trill(duration=(1,8)), trill(duration=(1,8)), tripletFeel='8th')])
    perf = compare(doc)
    assert len(perf['trillEvidence']) == 2


def test_expansion_limit_fails_without_allocating_excessive_events(monkeypatch):
    from feedback_converter.song_import.songsterr_trills import expand
    note = {'t':0, 's':0, 'f':7, 'sus':1, 'source_ids':['source']}
    state = {'start':F(0), 'end':F(1000), 'occurrence':1,
             'trill':{'auxiliaryFret':9,'speed':'1','tpqn':16384,'ticks':1}}
    with pytest.raises(ScoreImportError,match='limit'):
        expand([note],{id(note):state},[],'0',float,[],{})


@pytest.mark.parametrize('duration,speed,count', [((1,64),960,1),((1,16),120,1),((3,32),120,2),((1,4),60,8)])
def test_short_note_threshold_and_written_duration_cap(duration,speed,count):
    perf=compare(raw_score([measure(trill(duration=duration,speed=speed))]))
    assert len(perf['trillEvidence'][0]['events'])==count


def test_staccato_end_and_tempo_change_map_every_alternation():
    doc=raw_score([measure(trill(duration=(1,2),staccato=True))])
    doc['parts'][0]['automations']['tempo'].append({'measure':0,'position':480,'bpm':60,'type':4})
    perf=compare(doc);notes=perf['tracks'][0]['notes']
    assert len(notes)==8
    assert notes[-1]['t']+notes[-1]['sus']==.75
    assert notes[4]['sus']==2*notes[0]['sus']


@pytest.mark.parametrize('value',[{}, {'fret':9}, {'auxiliaryFret':9}, {'auxiliaryFret':9,'speed':0},
    {'auxiliaryFret':9,'speed':-1}, {'auxiliaryFret':9,'speed':True}, {'auxiliaryFret':9,'speed':1000},
    {'auxiliaryFret':9.5,'speed':60}, {'auxiliaryFret':25,'speed':60}, {'auxiliaryFret':7,'speed':60},
    {'auxiliaryFret':9,'speed':.00001}, {'auxiliaryFret':9,'speed':60,'unknown':True}])
def test_invalid_description_has_source_location(value):
    doc=raw_score([measure(trill())]);doc['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['trill']=value
    with pytest.raises(ScoreImportError) as err:render(parse(doc))
    assert err.value.source_location['note']==1
    with pytest.raises((ValueError,UnverifiedFeature)):expected(songsterr(doc),{'offset':0,'scale':1})


@pytest.mark.parametrize('extra',[{'harmonic':'pinch'}, {'slide':'downwards'}, {'tap':True}])
def test_unverified_combinations_are_located_not_dropped(extra):
    doc=raw_score([measure(trill(**extra))])
    with pytest.raises(ScoreImportError,match='combination'):render(parse(doc))
    with pytest.raises(UnverifiedFeature):expected(songsterr(doc),{'offset':0,'scale':1})


@pytest.mark.parametrize('fault',[None,'pitch','time','sustain','technique','link','missing','extra',
                                 'evidence','source','rate','identity','order','boolean','numeric_type'])
def test_final_archive_nonlinear_mapping_and_mutation(tmp_path,fault):
    from test_song_import_builder import inputs
    from feedback_converter.song_import import load_performance
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.verification import verify_import
    _,audio,_,job=inputs(tmp_path)
    source=tmp_path/'source.json';source.write_text(json.dumps(raw_score([measure(trill())])),encoding='utf-8')
    perf=load_performance(source)
    alignment={'status':'validated','mapping':'piecewise-linear','anchors':[
        {'score':0,'audio':.25},{'score':.25,'audio':.75},{'score':2,'audio':6}],
        'tempos':[{'time':.25,'bpm':60},{'time':.75,'bpm':40}]}
    built=build_feedpak(perf,audio,alignment,job,output_dir=tmp_path/'out',source_path=source,
        compatibility=perf['compatibilityReport'],recipe={'preservationContract':23})
    original=Path(built['stagingPath'])
    checked=verify_import(source,original,alignment)
    assert checked['status']=='passed',checked
    with ZipFile(original) as z: files={n:z.read(n) for n in z.namelist()}
    manifest=yaml.safe_load(files['manifest.yaml']);name=manifest['arrangements'][0]['file']
    chart=json.loads(files[name]);notes=chart['notes'];evidence=json.loads(files['import/trills.json'])
    assert len(notes)==8 and notes[4]['t']==.75
    assert files['import/source.json']==source.read_bytes()
    if fault=='pitch':notes[1]['f']+=1
    if fault=='time':notes[1]['t']+=.01
    if fault=='sustain':notes[1]['sus']+=.01
    if fault=='technique':notes[1].pop('ho')
    if fault=='link':notes[1].pop('ln')
    if fault=='missing':notes.pop(1)
    if fault=='extra':notes.insert(1,deepcopy(notes[1]))
    if fault=='evidence':evidence['trills'][0]['events'].pop()
    if fault=='source':evidence['sourceSha256']='0'*64
    if fault=='rate':evidence['trills'][0]['speed']='120'
    if fault=='identity':evidence['trills'][0]['sourceIds'][0]='invented'
    if fault=='order':evidence['trills'][0]['events'].reverse()
    if fault=='boolean':evidence['trills'][0]['events'][1]['ordinal']=True
    if fault=='numeric_type':evidence['trills'][0]['events'][1]['ordinal']=1.0
    files[name]=json.dumps(chart).encode();files['import/trills.json']=json.dumps(evidence).encode()
    mutated=tmp_path/'mutated.feedpak'
    with ZipFile(mutated,'w') as z:
        for name,data in files.items():z.writestr(name,data)
    checked=verify_import(source,mutated,alignment)
    assert checked['status']==('passed' if fault is None else 'failed'),checked
    if fault in {'boolean','numeric_type'}:
        assert len(json.dumps(checked)) < 10000
