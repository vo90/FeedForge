from copy import deepcopy
import json
import pytest
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.builder import _retime_note
from feedback_converter.song_import.verify_source import read_source
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.verify_let_ring import attach
from feedback_converter.song_import.verification import Check, _notes


def source(beats=None):
    beats = beats or [{'duration':[1,4], 'letRing':True, 'notes':[{'string':s,'fret':0}]} for s in range(4)]
    return {'format':'songsterr','songId':1,'revisionId':2,'title':'Fixture','artist':'Artist',
            'tracks':[{'id':'g','name':'Guitar','instrumentId':25,'tuning':[64,59,55,50,45,40]}],
            'parts':[{'instrumentId':25,'tuning':[64,59,55,50,45,40],
                      'automations':{'tempo':[{'measure':0,'position':[0,1],'bpm':120,'type':4}]},
                      'measures':[{'signature':[4,4],'voices':[{'beats':beats}]}]}]}


def compare(tmp_path, doc, mapping=None, duration=100):
    mapping=mapping or {'scale':1,'offset':0}
    path=tmp_path/'source.json';path.write_text(json.dumps(doc))
    perf=render(parse(doc)); src=read_source(path)
    want=expected(src,mapping);attach(src,want,mapping,duration)
    aa=[]
    for t,p in zip(perf['tracks'],want['parts']):
        ns=t['notes']+[{**n,'t':c['t']} for c in t['chords'] for n in c['notes']]
        actual=sorted([_retime_note(n,mapping,duration) for n in ns],key=lambda n:(n['t'],n['s']))
        check=Check();_notes(p['notes'],[(n,str(i)) for i,n in enumerate(actual)],check,p['source'],duration)
        assert not check.errors,check.errors
        aa.extend(actual)
    return aa,want


def test_open_arpeggio_changes_only_visual_span(tmp_path):
    notes,_=compare(tmp_path,source())
    assert [n['sus'] for n in notes]==[.5]*4
    assert [n.get('let_ring_sus') for n in notes]==[2,1.5,1,None]
    assert [n['t'] for n in notes]==[0,.5,1,1.5]
    assert all(not n.get('ln') for n in notes)


@pytest.mark.parametrize('variant,span',[('reattack',None),('rest',None),('unmarked',1),('dead',None),('staccato',1)])
def test_qualified_boundaries(tmp_path,variant,span):
    doc=source();b=doc['parts'][0]['measures'][0]['voices'][0]['beats']
    if variant=='reattack':b[1]['notes'][0]['string']=0
    if variant=='rest':b[1].update(rest=True,notes=[])
    if variant=='unmarked':b[1].pop('letRing')
    if variant in ('dead','staccato'):b[0]['notes'][0][variant]=True
    ns,_=compare(tmp_path,doc)
    assert ns[0].get('let_ring_sus')==span


def test_cross_bar_and_tempo_change_maps_both_ends(tmp_path):
    doc=source();part=doc['parts'][0]
    part['measures'].append(deepcopy(part['measures'][0]))
    for b in part['measures'][1]['voices'][0]['beats']:b['notes'][0]['string']=4
    part['automations']['tempo'].append({'measure':1,'position':[0,1],'bpm':60,'type':4})
    ns,_=compare(tmp_path,doc,{'offset':2,'scale':1.1})
    assert ns[0]['sus']==.55
    assert ns[0]['let_ring_sus']==6.6


def test_tied_segments_do_not_create_attack(tmp_path):
    doc=source();b=doc['parts'][0]['measures'][0]['voices'][0]['beats']
    b[1]['notes']=[{'string':0,'fret':0,'tie':True}]
    ns,_=compare(tmp_path,doc)
    assert len(ns)==3 and ns[0]['sus']==1
    assert ns[0]['let_ring_sus']==2


@pytest.mark.parametrize('fault',['missing','long','short','attack','score'])
def test_independent_check_rejects_corruption(tmp_path,fault):
    actual,want=compare(tmp_path,source())
    if fault=='missing':actual[0].pop('let_ring_sus')
    elif fault=='long':actual[0]['let_ring_sus']+=.1
    elif fault=='short':actual[0]['let_ring_sus']-=.1
    elif fault=='attack':actual[0]['t']+=.1
    else:actual[0]['sus']+=.1
    check=Check();p=want['parts'][0];_notes(p['notes'],[(n,str(i)) for i,n in enumerate(actual)],check,p['source'],100)
    assert check.errors


def test_optional_ring_stops_at_audio_boundary_without_extending_audio():
    n={'t':1,'s':0,'f':0,'sus':.5,'lr':True,'let_ring_sus':8}
    result=_retime_note(n,{'scale':1,'offset':0},4)
    assert result['let_ring_sus']==3 and result['sus']==.5


def test_hybrid_limits_ring_at_handover_and_physical_reattack():
    from feedback_converter.song_import.let_ring import clip_chart,clip_event
    first={'t':0,'s':0,'f':3,'sus':.5,'let_ring_sus':5}
    clip_event(first,3)
    assert first['let_ring_sus']==3 and first['sus']==.5
    second={'t':2,'s':0,'f':5,'sus':.5}
    chart={'notes':[first,second],'chords':[]}
    clip_chart(chart)
    assert first['let_ring_sus']==2
    clip_event(first,.5)
    assert 'let_ring_sus' not in first and first['sus']==.5


def test_same_written_note_on_repeat_is_not_an_extra_tied_segment(tmp_path):
    doc=source();bar=doc['parts'][0]['measures'][0]
    bar.update(repeatStart=True,repeat=2)
    ns,_=compare(tmp_path,doc)
    assert len(ns)==8
    assert ns[0]['let_ring_sus']==2
    assert ns[4]['let_ring_sus']==2


@pytest.mark.parametrize('kind,span', [('tie',2),('staccato-tie',1),('tie-staccato',2),
                                     ('bend-tie',2),('slide',None),('hopo',None),('vibrato',2)])
def test_native_compound_known_answers(tmp_path,kind,span):
    # Qualified against reviewed Songsterr worker 4219f9db; endpoints omit
    # its one-tick synthesizer release gap. No scored duration is extended.
    doc=source();b=doc['parts'][0]['measures'][0]['voices'][0]['beats']
    for beat in b:beat['notes'][0]['fret']=5
    if 'tie' in kind:b[1]['notes']=[{'string':0,'fret':5,'tie':True}]
    if kind=='staccato-tie':b[0]['notes'][0]['staccato']=True
    if kind=='tie-staccato':b[1]['notes'][0]['staccato']=True
    if kind=='bend-tie':b[0]['notes'][0]['bend']={'points':[{'position':0,'tone':0},{'position':60,'tone':100}]}
    if kind in ('slide','hopo'):
        b[0]['notes'][0]['slide' if kind=='slide' else 'hp']='shift' if kind=='slide' else True
        b[1]['notes']=[{'string':0,'fret':7}]
    if kind=='vibrato':b[0]['notes'][0]['vibrato']=True
    ns,_=compare(tmp_path,doc)
    assert ns[0].get('let_ring_sus')==span


def test_nonlinear_recording_clock_preserves_ring_endpoint(tmp_path):
    mapping={'mapping':'piecewise-linear','anchors':[{'score':0,'audio':2},
             {'score':1,'audio':3},{'score':2,'audio':5}]}
    ns,_=compare(tmp_path,source(),mapping)
    assert ns[0]['let_ring_sus']==3 and ns[0]['sus']==.5


def test_simultaneous_string_duplicates_do_not_hide_next_attack():
    from feedback_converter.song_import.let_ring import clip_chart
    first={'t':0,'s':0,'f':3,'sus':.5,'let_ring_sus':5}
    chart={'notes':[first,deepcopy(first),{'t':2,'s':0,'f':5,'sus':.5}], 'chords':[]}
    clip_chart(chart)
    assert [n['let_ring_sus'] for n in chart['notes'][:2]]==[2,2]


@pytest.mark.parametrize('direction',['up','down'])
def test_strummed_members_keep_separate_attacks_and_common_ring_end(tmp_path,direction):
    doc=source();b=doc['parts'][0]['measures'][0]['voices'][0]['beats'][0]
    b['notes'].append({'string':4,'fret':5})
    b['brushStroke']={'direction':direction,'duration':30,'shift':100}
    ns,_=compare(tmp_path,doc)
    first=sorted(ns,key=lambda n:n['t'])[:2]
    assert first[0]['t'] < first[1]['t']
    assert all(n['t']+n['let_ring_sus']==pytest.approx(2) for n in first)


def test_full_package_primary_selection_bounds_ringing_without_mutating_original(tmp_path):
    from test_hybrid_lead_priority import solo_song
    from test_songsterr_hybrid_lead import build
    from zipfile import ZipFile
    import yaml
    doc=solo_song()
    bars=doc['parts'][0]['measures']
    bars[0]['voices'][0]['beats']=source()['parts'][0]['measures'][0]['voices'][0]['beats']
    for i in range(1,4):
        bars[i]['voices'][0]['beats']=[{'duration':[1,1],'letRing':True,'notes':[{'string':4,'fret':3+i}]}]
    *_,archive,report=build(tmp_path,doc,overrides={'mainTrackId':'0'},contract=110)
    assert report['status']=='passed', [{k:v for k,v in e.items() if k not in ('actual','expected')} for e in report['errors']]
    with ZipFile(archive) as z:
        m=yaml.safe_load(z.read('manifest.yaml'))
        receipt=json.loads(z.read('import/hybrid-lead.json'))
        assert receipt['passages'] and receipt['removedMain']
        main=json.loads(z.read('arrangements/0.json'))
        hybrid=json.loads(z.read(m['arrangements'][-1]['file']))
        a=main['notes'][0];b=next(n for n in hybrid['notes'] if n['t']==a['t'] and n['s']==a['s'])
        assert a['let_ring_sus'] > b['let_ring_sus']
        assert b['t']+b['let_ring_sus']==receipt['passages'][0]['recordingStart']
        assert a['sus']==b['sus']
