from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, measure, raw_score
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected


def source(first=None, second=None):
    return raw_score([measure(beat(fret=12, duration=(1,2), **(first or {})),
                              beat(fret=12, duration=(1,2), tie=True, dead=True, **(second or {})))])


def compare(document):
    original = deepcopy(document)
    actual = render(parse(document))
    checked = expected(songsterr(document), {'offset':0, 'scale':1})
    assert actual.get('tiedMuteEvidence', []) == checked['tied_mutes']
    assert original == document
    return actual, checked


@pytest.mark.parametrize('harmonic', [None, 'artificial', 'natural', 'pinch'])
def test_pitched_attack_keeps_target_duration_and_curves(harmonic):
    src = source({'harmonic':harmonic, **({'harmonicFret':5} if harmonic != 'natural' else {})} if harmonic else {},
                 {'bend':{'points':[{'position':0,'tone':0},{'position':60,'tone':100}]}})
    src['parts'][0]['measures'][0]['voices'][0]['beats'][1]['tremoloBar'] = {
        'points':[{'position':0,'tone':0},{'position':60,'tone':-100}]}
    p, v = compare(src)
    notes=p['tracks'][0]['notes']; note=notes[0]; checked=v['parts'][0]['notes'][0]['note']
    assert len(notes)==1 and note['sus']==2 and not note.get('mt')
    for key in ('f','s','t','sus','harmonic_target','hm','hp','hn','hps','bnv','whammy'):
        assert note.get(key)==checked.get(key)
    assert note['bnv']==[{'t':1,'v':0},{'t':2,'v':2}]
    assert len(note['source_ids'])==2
    row=p['tiedMuteEvidence'][0]
    assert (row['attack'],row['start'],row['end'])==(0,1,2)
    assert row['authored']=={'dead':True} and row['used']=={'dead':False}


@pytest.mark.parametrize('first', [{'dead':True}, {'dead':True,'pickScrape':'up'}])
def test_initial_dead_and_scrape_ties_keep_existing_behavior(first):
    p,v=compare(source(first))
    assert 'tiedMuteEvidence' not in p
    assert p['tracks'][0]['notes'][0]['mt']
    assert v['parts'][0]['notes'][0]['note']['mt']


def test_unpitched_ties_and_untied_dead_attacks_stay_dead():
    src=source({'dead':True})
    for b in src['parts'][0]['measures'][0]['voices'][0]['beats']: b['notes'][0].pop('fret')
    p,_=compare(src)
    assert p['tracks'][0]['notes'][0]['f']==127 and 'tiedMuteEvidence' not in p
    src=source();src['parts'][0]['measures'][0]['voices'][0]['beats'][1]['notes'][0]['tie']=False
    p,_=compare(src)
    assert len(p['tracks'][0]['notes'])==2 and p['tracks'][0]['notes'][1]['mt']
    assert 'tiedMuteEvidence' not in p


def test_cross_bar_repeats_mixed_chords_keep_each_occurrence_and_source_notation():
    a=beat(fret=12); b=beat(fret=12,tie=True,dead=True)
    a['notes'].append({'fret':7,'string':1})
    b['notes'].append({'fret':7,'string':1,'tie':True})
    src=raw_score([measure(a,repeatStart=True),measure(b,repeat=2)])
    p,v=compare(src)
    assert [r['occurrence'] for r in p['tiedMuteEvidence']]==[2,4]
    assert len(p['tracks'][0]['chords'])==2
    for chord in p['tracks'][0]['chords']:
        assert len(chord['notes'])==2
        assert all(not n.get('mt') and n['sus']==4 for n in chord['notes'])


@pytest.mark.parametrize('fault', ['fret','rest','missing'])
def test_invalid_ties_still_fail(fault):
    src=source();beats=src['parts'][0]['measures'][0]['voices'][0]['beats']
    if fault=='fret': beats[1]['notes'][0]['fret']=11
    if fault=='rest': beats[0]={'duration':[1,2],'notes':[{'rest':True}]}
    if fault=='missing': beats.pop(0)
    with pytest.raises(ValueError): render(parse(src))
    with pytest.raises(ValueError): expected(songsterr(src),{'offset':0,'scale':1})


@pytest.mark.parametrize('fault', [None,'missing','source','rule','time','shape','extra','count','report','attack','mute','shorten','contract','boolean'])
def test_archive_requires_independent_evidence_and_unchanged_gameplay(tmp_path,fault):
    from test_song_import_builder import inputs
    from feedback_converter.song_import import load_performance
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.verification import verify_import
    from feedback_converter.feedpak_validator import validate_feedpak
    _,audio,_,job=inputs(tmp_path)
    path=tmp_path/'source.json';path.write_text(json.dumps(source({'harmonic':'artificial','harmonicFret':5})),encoding='utf-8')
    performance=load_performance(path)
    alignment={'status':'validated','offset':.25,'scale':1.25}
    built=build_feedpak(performance,audio,alignment,job,output_dir=tmp_path/'out',source_path=path,
        compatibility=performance['compatibilityReport'],recipe={'preservationContract':25})
    archive=Path(built['stagingPath'])
    assert validate_feedpak(archive).ok
    assert verify_import(path,archive,alignment)['status']=='passed'
    with ZipFile(archive) as z: files={n:z.read(n) for n in z.namelist()}
    evidence=json.loads(files['import/tied-mutes.json'])
    if fault=='missing': files.pop('import/tied-mutes.json')
    if fault=='source': evidence['sourceSha256']='0'*64
    if fault=='rule': evidence['continuations'][0]['rule']='invented-cutoff'
    if fault=='time': evidence['continuations'][0]['start']+=.1
    if fault=='shape': evidence['continuations'][0]=None
    if fault=='extra': evidence['continuations'][0]['extra']=True
    if fault=='count': evidence['continuations']=[]
    if fault=='boolean': evidence['continuations'][0]['authored']['dead']=1
    if fault in ('source','rule','time','shape','extra','count','boolean'): files['import/tied-mutes.json']=json.dumps(evidence).encode()
    if fault=='report':
        report=json.loads(files['import/compatibility.json']);report['findings']=[];report['findingCount']=0;report['status']='compatible'
        files['import/compatibility.json']=json.dumps(report).encode()
    manifest=yaml.safe_load(files['manifest.yaml'])
    if fault=='contract':
        manifest['song_import']['preservationContract']=24;files['manifest.yaml']=yaml.safe_dump(manifest).encode()
    if fault in ('attack','mute','shorten'):
        name=manifest['arrangements'][0]['file'];chart=json.loads(files[name])
        if fault=='attack': chart['notes'].append(deepcopy(chart['notes'][0]))
        if fault=='mute': chart['notes'][0]['mt']=True
        if fault=='shorten': chart['notes'][0]['sus']/=2
        files[name]=json.dumps(chart).encode()
    mutated=tmp_path/'checked.feedpak'
    with ZipFile(mutated,'w') as z:
        for name,data in files.items():z.writestr(name,data)
    result=verify_import(path,mutated,alignment)
    assert result['status']==('passed' if fault is None else 'failed'),result


def test_old_contract_cannot_publish_interpretation(tmp_path):
    from test_song_import_builder import inputs
    from feedback_converter.song_import import load_performance
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.audio import ImportFailure
    _,audio,_,job=inputs(tmp_path)
    path=tmp_path/'source.json';path.write_text(json.dumps(source()),encoding='utf-8')
    performance=load_performance(path)
    with pytest.raises(ImportFailure,match='contract 25'):
        build_feedpak(performance,audio,{'status':'validated','offset':0,'scale':1},job,
            output_dir=tmp_path/'out',source_path=path,compatibility=performance['compatibilityReport'],recipe={'preservationContract':24})
