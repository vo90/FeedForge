from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile
import pytest
import yaml
from test_song_import_score import beat,measure,raw_score,import_json
from feedback_converter.song_import import ScoreImportError
from feedback_converter.song_import.voices import flat
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.verification import Check,_notes,_chords,_flatten,verify_import
from feedback_converter.song_import.builder import build_feedpak


def document(kind):
    a=beat(3);b=deepcopy(a)
    if kind=='pitch':b['notes'][0]['fret']=5
    if kind=='palm':a['palmMute']=True
    if kind in ('let-ring','short','intervening'):
        a['duration']=[1,8];b['duration']=[1,2]
        if kind!='short':a['letRing']=True
    m=measure(a);m['voices'].append({'beats':[b]})
    if kind=='intervening':m['voices'][0]['beats'].append(beat(4,duration=(1,8)))
    if kind=='mixed':a['notes'].append({'string':1,'fret':5});b['notes'].append({'string':2,'fret':7})
    return raw_score([m])


@pytest.mark.parametrize('kind,count',[('same',1),('pitch',2),('palm',2),('let-ring',1),('short',2),('intervening',2),('mixed',1)])
def test_projection_preserves_instructions_and_matches_independent_oracle(tmp_path,kind,count):
    raw=document(kind);before=deepcopy(raw);p=import_json(tmp_path,raw);ref=expected(songsterr(raw),{'offset':0,'scale':1})
    assert raw==before and len(p['tracks'])==count
    check=Check()
    for t,r in zip(p['tracks'],ref['parts']):
        assert t['id']==r['source'].id and t['name']==r['source'].name
        _notes(r['notes'],_flatten(t),check,r['source'],p['duration']);_chords(r['notes'],t,check,r['source'])
    assert not check.errors
    assert p['voiceProjection']==ref['voice_projection']
    if kind=='let-ring':assert flat(p['tracks'][0])[0]['sus']==1 and flat(p['tracks'][0])[0]['lr']
    if count==2:assert [t['name'] for t in p['tracks']]==['Lead Guitar — Voice 1','Lead Guitar — Voice 2']


def test_no_projection_for_ordinary_polyphonic_guitar_or_single_voice_duplicates(tmp_path):
    raw=document('same');raw['parts'][0]['measures'][0]['voices'][1]['beats'][0]['notes'][0]['string']=1
    assert 'voiceProjection' not in import_json(tmp_path,raw)
    raw['parts'][0]['measures'][0]['voices']=[{'beats':[beat()]}]
    raw['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'].append({'string':0,'fret':4})
    with pytest.raises(ScoreImportError):import_json(tmp_path,raw)


def test_ties_repeats_and_derived_strum_identity(tmp_path):
    raw=document('pitch');m=raw['parts'][0]['measures'][0];m['repeatStart']=True
    for v in m['voices']:
        v['beats'][0]['duration']=[1,2]
        v['beats'].append(beat(v['beats'][0]['notes'][0]['fret'],duration=(1,2),tie=True))
    m['repeat']=2
    p=import_json(tmp_path,raw)
    assert all(len(flat(t))==2 and all(n['sus']==2 for n in flat(t)) for t in p['tracks'])
    raw=document('pitch');m=raw['parts'][0]['measures'][0]
    for v in m['voices']:
        b=v['beats'][0];b['brushStroke']={'direction':'down','duration':30,'shift':100};b['notes'].append({'string':1,'fret':7})
    p=import_json(tmp_path,raw);ref=expected(songsterr(raw),{'offset':0,'scale':1})
    assert {s['trackId'] for s in p['strumEvidence']}=={t['id'] for t in p['tracks']}
    assert len(p['strumEvidence'])==2 and p['strumEvidence']==ref['strums']


@pytest.mark.parametrize('kind',['same','let-ring','pitch','palm','mixed'])
@pytest.mark.parametrize('mapped',[False,True])
def test_real_package_and_projection_tamper_rejection(tmp_path,kind,mapped):
    from test_song_import_builder import inputs
    _,audio,_,job=inputs(tmp_path);raw=document(kind);p=import_json(tmp_path,raw);source=tmp_path/'score.json'
    alignment={'status':'validated','offset':0,'scale':1}
    if mapped:
        alignment={'status':'validated','mapping':'piecewise-linear','anchors':[
            {'score':0,'audio':.2},{'score':.5,'audio':.8},{'score':2,'audio':3}]}
        alignment['tempos']=[{'time':.2,'bpm':120/1.2},{'time':.8,'bpm':120/(2.2/1.5)}]
    built=build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=source,compatibility=p['compatibilityReport'],recipe={'preservationContract':27})
    archive=Path(built['stagingPath']);report=verify_import(source,archive,alignment)
    assert report['status']=='passed',report
    with ZipFile(archive) as z:files={n:z.read(n) for n in z.namelist()}
    for fault in ('source','voice','missing','note'):
        changed=deepcopy(files);r=json.loads(changed['import/voices.json'])
        if fault=='source':r['sourceSha256']='0'*64
        if fault=='voice':r['tracks'][0]['arrangements'][0]['voices']=[99]
        changed['import/voices.json']=json.dumps(r).encode()
        if fault=='missing':
            m=yaml.safe_load(changed['manifest.yaml']);m['song_import'].pop('voicesFile');changed['manifest.yaml']=yaml.safe_dump(m).encode()
        if fault=='note':
            name=next(n for n in changed if n.startswith('arrangements/'));chart=json.loads(changed[name]);notes=chart['notes'] or chart['chords'][0]['notes'];notes[0]['f']+=1;changed[name]=json.dumps(chart).encode()
        target=tmp_path/(fault+'.feedpak')
        with ZipFile(target,'w') as z:
            for n,data in changed.items():z.writestr(n,data)
        assert verify_import(source,target,alignment)['status']=='failed'


def test_voice_specific_notation_limitation_and_high_fret_projection(tmp_path):
    from feedback_converter.song_import.high_frets import project
    from feedback_converter.song_import.verify_high_frets import reconstruct,check_receipt
    raw=document('pitch');voices=raw['parts'][0]['measures'][0]['voices']
    voices[0]['beats'][0]['duration']=[1,64]
    voices[1]['beats'][0]['notes'][0]['fret']=26
    full=import_json(tmp_path,raw)
    assert 'notation' not in full['tracks'][0] and 'notation' in full['tracks'][1]
    findings=[r for r in full['compatibilityReport']['findings'] if r['feature']=='notation.written_rhythm']
    assert len(findings)==1 and findings[0]['arrangement'].endswith('Voice 1')
    projected,receipt=project(full)
    ref=expected(songsterr(raw),{'offset':0,'scale':1});wanted=deepcopy(ref);check=Check()
    check_receipt(reconstruct(ref,wanted),receipt,check)
    for track,part in zip(projected['tracks'],wanted['parts']):
        _notes(part['notes'],_flatten(track),check,part['source'],projected['duration'])
    assert not check.errors and len(receipt['notes'])==1
