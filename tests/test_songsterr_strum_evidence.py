from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile
import pytest
import yaml
from test_song_import_score import beat,measure,raw_score
from feedback_converter.song_import import load_performance
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.verification import verify_import
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected


def document(direction='down',shift=100):
    b=beat(fret=3);b['notes'] += [{'string':1,'fret':5},{'string':2,'fret':0}]
    b['brushStroke']={'direction':direction,'duration':30,'shift':shift}
    return raw_score([measure(beat()),measure(b,repeatStart=True,repeat=2)])


@pytest.mark.parametrize('direction',['up','down'])
@pytest.mark.parametrize('shift',[0,50,100])
def test_repeated_shifted_strums_match_independent_source_clock(tmp_path,direction,shift):
    raw=document(direction,shift);path=tmp_path/'source.json';path.write_text(json.dumps(raw),encoding='utf-8')
    p=load_performance(path);v=expected(songsterr(raw),{'offset':0,'scale':1})
    assert p['strumEvidence']==v['strums']
    assert len(v['strums'])==2 and [r['occurrence'] for r in v['strums']]==[2,3]
    assert all(r['direction']==direction and len(r['notes'])==3 for r in v['strums'])


@pytest.mark.parametrize('fault',[None,'time','direction','missing','member','count','source'])
def test_piecewise_archive_requires_source_bound_exact_members(tmp_path,fault):
    from test_song_import_builder import inputs
    _,audio,_,job=inputs(tmp_path)
    raw=document();path=tmp_path/'source.json';path.write_text(json.dumps(raw),encoding='utf-8')
    p=load_performance(path)
    alignment={'status':'validated','mapping':'piecewise-linear','anchors':[{'score':0,'audio':0},{'score':2,'audio':2.1},{'score':6,'audio':7}]}
    alignment['tempos']=[{'time':0,'bpm':120/1.05},{'time':2.1,'bpm':120/1.225}]
    built=build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=path,compatibility=p['compatibilityReport'],recipe={'preservationContract':26})
    archive=Path(built['stagingPath']);assert verify_import(path,archive,alignment)['status']=='passed'
    with ZipFile(archive) as z:files={n:z.read(n) for n in z.namelist()}
    r=json.loads(files['import/strums.json'])
    if fault=='time':r['groups'][0]['notes'][1]['t']+=.01
    if fault=='direction':r['groups'][0]['direction']='up'
    if fault=='member':r['groups'][0]['notes'][1]['f']=99
    if fault=='count':r['groups'].pop()
    if fault=='source':r['sourceSha256']='0'*64
    files['import/strums.json']=json.dumps(r).encode()
    if fault=='missing':
        m=yaml.safe_load(files['manifest.yaml']);m['song_import'].pop('strumsFile');files['manifest.yaml']=yaml.safe_dump(m).encode()
    changed=tmp_path/'changed.feedpak'
    with ZipFile(changed,'w') as z:
        for name,data in files.items():z.writestr(name,data)
    assert verify_import(path,changed,alignment)['status']==('passed' if fault is None else 'failed')
