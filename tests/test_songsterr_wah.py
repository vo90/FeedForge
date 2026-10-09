from copy import deepcopy
import json
from zipfile import ZipFile
import pytest
import yaml
from test_song_import_score import raw_score, measure, beat
from test_songsterr_hybrid_lead import build, song, rest
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import read_source
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.verification import Check
from feedback_converter.song_import.verify_wah import compare
from feedback_converter.song_import.wah import export


def marked(b,state): return {**b,'wahwah':state}


@pytest.mark.parametrize('mapping',[{'scale':1,'offset':2},
    {'mapping':'piecewise-linear','anchors':[{'score':0,'audio':2},{'score':1,'audio':3},{'score':4,'audio':9}]}])
def test_notes_ties_rests_and_repeats_are_independently_checked(tmp_path,mapping):
    doc=raw_score([measure(marked(beat(duration=(1,4)),'open'),marked(beat(duration=(1,4),tie=True),'closed'),
                            marked(rest((1,4)),'open'),beat(duration=(1,4)),repeatStart=True,repeat=2)])
    path=tmp_path/'score.json';path.write_text(json.dumps(doc))
    actual=render(parse(doc))['tracks'][0]
    reference=expected(read_source(path),mapping)['parts'][0]['wah']
    rows=export(actual['wah'],mapping,100)
    assert len(rows)==6 and len(actual['notes'])==4
    check=Check();compare(reference,rows,check,'test');assert not check.errors,check.errors
    assert rows[2]['strings']==[] and rows[2]['source_ids']==[]
    for key,value in [('t',.3),('state','closed'),('strings',[1]),('source_id','invented')]:
        broken=deepcopy(rows);broken[0][key]=value
        check=Check();compare(reference,broken,check,'test');assert check.errors


def test_pedal_is_not_part_of_played_notes():
    doc=raw_score([measure(marked(beat(),'open'))]); a=render(parse(doc))['tracks'][0]
    doc['parts'][0]['measures'][0]['voices'][0]['beats'][0].pop('wahwah')
    b=render(parse(doc))['tracks'][0]
    assert {k:v for k,v in a.items() if k!='wah'}==b


@pytest.mark.parametrize('contract',[111,112])
def test_package_and_hybrid_source_selection(tmp_path,contract):
    doc=song()
    for part in doc['parts']:
        for m in part['measures']:
            for b in m['voices'][0]['beats']: b['wahwah']='open' if b.get('rest') else 'closed'
    *_,archive,report=build(tmp_path,doc,mapped=True,difficulty=True,contract=contract)
    assert report['status']=='passed',report
    with ZipFile(archive) as z:
        manifest=yaml.safe_load(z.read('manifest.yaml'))
        charts=[json.loads(z.read(a['file'])) for a in manifest['arrangements']]
        if contract==112:
            assert all(c['wah'] for c in charts)
            assert any(e['source_id'].startswith('songsterr:1:') for e in charts[-1]['wah'])
            assert any(e['source_id'].startswith('songsterr:2:') for e in charts[-1]['wah'])
        else: assert all('wah' not in c for c in charts)


def test_cutoff_does_not_add_pedal_changes_to_silent_tail():
    events=[dict(t=t,state='open') for t in (0,1,2)]
    assert [e['t'] for e in export(events,{'scale':1,'offset':2},4)]==[2,3]
    assert [e['t'] for e in export(events,{'scale':1,'offset':-1},4)]==[0,1]
