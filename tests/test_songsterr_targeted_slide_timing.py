"""Targeted slide segments survive ties, mapping, archive checks and Hybrid Lead."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile
import pytest
import yaml
from test_song_import_score import raw_score, measure, beat, import_json
from test_songsterr_bend_timing import checked, RISE, FALL
from feedback_converter.song_import.builder import _retime_note, build_feedpak
from feedback_converter.song_import.verification import verify_import
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.hybrid_lead import normalize_options, choose_main
from feedback_converter.song_import.score import load_performance

REFERENCE=json.loads((Path(__file__).parent/'fixtures/songsterr_targeted_slide_reference.json').read_text())

@pytest.mark.parametrize('case',REFERENCE['cases'],ids=lambda c:c['id'])
def test_captured_native_finger_shape_on_authored_clock(case):
    p=checked(case['source']);e=p['fingerBendTimingEvidence'][0]
    assert (e['status']=='resolved')==case['eligible']
    curve=p['tracks'][0]['notes'][0]['bnv']
    for sample in case['samples']:
        before=[x for x in curve if x['t']<=sample['t']];after=[x for x in curve if x['t']>sample['t']]
        a=before[-1] if before else curve[0];b=after[0] if after else a
        value=a['v'] if a['t']==b['t'] else a['v']+(b['v']-a['v'])*(sample['t']-a['t'])/(b['t']-a['t'])
        assert value==pytest.approx(sample['v'],abs=.06)


def document(target=9, kind='legato', tied=True, bend=True):
    notes = [beat(fret=7, duration=(1,4), **({'bend': deepcopy(RISE)} if bend else {})),
             beat(fret=7, duration=(1,4), tie=True),
             beat(fret=7, duration=(1,4), tie=True, slide=kind),
             beat(fret=target, duration=(1,4))]
    if not tied:
        notes = [beat(fret=7, duration=(3,4), slide=kind, **({'bend':deepcopy(RISE)} if bend else {})), notes[-1]]
    return raw_score([measure(*notes)])


@pytest.mark.parametrize('target', [0,5,9])
@pytest.mark.parametrize('kind', ['shift','legato'])
@pytest.mark.parametrize('tied', [False,True])
@pytest.mark.parametrize('bend', [False,True])
def test_authored_segment_and_independent_bend_clock(target,kind,tied,bend):
    d=document(target,kind,tied,bend); p=checked(d); n=p['tracks'][0]['notes'][0]
    assert n['slide_interval']=={'start':1. if tied else 0.,'end':1.5}
    assert n['sl']==target and bool(n.get('ln'))==(kind=='legato')
    assert len(p['tracks'][0]['notes'])==2 and n['sus']==1.5
    if bend and tied:
        e=p['fingerBendTimingEvidence'][0]
        assert e['status']=='resolved' and e['targetedSlide']['kind']==kind
        assert n['bnv']==[{'t':0.,'v':0.},{'t':1.5,'v':2.}]


def test_tempo_repeat_initial_cue_and_source_identity():
    d=document(); bs=d['parts'][0]['measures'][0]['voices'][0]['beats']
    bs[0]['notes'][0]['slide']='below'
    d['parts'][0]['automations']['tempo'].append({'measure':0,'position':960,'bpm':60,'type':4})
    p=checked(d); n=p['tracks'][0]['notes'][0]
    assert n['slide_interval']=={'start':1.5,'end':2.5}
    assert n['slide_in_marks']==[{'direction':'up','time':0.}]
    d.update(songId=88772,title='Unrelated title')
    assert checked(d)['tracks'][0]['notes']==p['tracks'][0]['notes']


@pytest.mark.parametrize('fault', ['overlap','bar','harmonic','staccato','early-slide','gap'])
def test_ambiguous_combinations_keep_warning_or_legacy_interval(fault):
    d=document(); bs=d['parts'][0]['measures'][0]['voices'][0]['beats']
    if fault=='overlap': bs[2]['notes'][0]['bend']=deepcopy(FALL)
    elif fault=='bar': bs[0]['tremoloBar']={'points':[{'position':0,'tone':0},{'position':60,'tone':100}]}
    elif fault=='harmonic': bs[0]['notes'][0]['harmonic']='pinch'
    elif fault=='staccato': bs[0]['notes'][0]['staccato']=True
    elif fault=='early-slide': bs[1]['notes'][0]['slide']='legato'
    else:
        bs[2]['duration']=[1,8]; bs.insert(3,{'duration':[1,8],'notes':[{'rest':True}]})
    if fault=='staccato':
        from feedback_converter.song_import.model import ScoreImportError
        with pytest.raises(ScoreImportError,match='Staccato ties'): checked(d)
        return
    p=checked(d); e=p['fingerBendTimingEvidence'][0]
    assert e['status']=='deferred' and 'targetedSlide' not in e
    if fault in ('staccato','early-slide','gap'):
        assert 'slide_interval' not in p['tracks'][0]['notes'][0]


@pytest.mark.parametrize('piecewise', [False,True])
@pytest.mark.parametrize('hybrid', [False,True])
def test_archive_mapping_hybrid_and_mutations(tmp_path,piecewise,hybrid):
    from test_song_import_builder import inputs
    _,audio,_,job=inputs(tmp_path)
    p=import_json(tmp_path,document()); path=tmp_path/'score.json'
    if hybrid: p=load_performance(path,composition_context=True)
    alignment={'status':'validated','offset':2.,'scale':1.}
    if piecewise:
        alignment.update(mapping='piecewise-linear', anchors=[{'score':0,'audio':2},{'score':1,'audio':3},{'score':4,'audio':6.6}],tempos=[{'time':2,'bpm':120},{'time':3,'bpm':100}])
    options=normalize_options({'enabled':hybrid}); sha=hashlib.sha256(path.read_bytes()).hexdigest()
    if hybrid: options.update(mainTrackId=choose_main(p,options,sha),sourceSha256=sha)
    recipe={'preservationContract':80,'scoreHash':sha,'audioHash':audio['hash'],**({'hybridLead':options} if hybrid else {})}
    built=build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=path,
          compatibility=p['compatibilityReport'],recipe=recipe,
          hybrid_lead={'enabled':hybrid,'mainTrackId':options.get('mainTrackId'),'options':options})
    archive=Path(built['stagingPath'])
    verify=lambda f:verify_import(path,f,alignment,hybrid_options=options if hybrid else None)
    assert verify(archive)['status']=='passed',verify(archive)
    with ZipFile(archive) as z: original={name:z.read(name) for name in z.namelist()}
    m=yaml.safe_load(original['manifest.yaml']); ep=m['song_import']['fingerBendTimingFile']
    assert json.loads(original[ep])['version']==19
    chart=json.loads(original[m['arrangements'][0]['file']]); n=chart['notes'][0]
    assert n['t']==2. and n['slide_interval']=={'start':1.,'end':1.6 if piecewise else 1.5}
    for fault in ('absent','start','end','bool','extra','bend','evidence','contract'):
        files=dict(original); manifest=deepcopy(m); cp=m['arrangements'][0]['file']
        chart=json.loads(files[cp]); n=chart['notes'][0]; ev=json.loads(files[ep])
        if fault=='absent': del n['slide_interval']
        elif fault=='start': n['slide_interval']['start']=0.
        elif fault=='end': n['slide_interval']['end']+=.2
        elif fault=='bool': n['slide_interval']['start']=True
        elif fault=='extra': n['slide_interval']['other']=1
        elif fault=='bend': n['bnv'][-1]['t']/=2
        elif fault=='evidence': ev['gestures'][0]['targetedSlide']['start']+=.1
        else: manifest['song_import']['preservationContract']=79
        files[cp]=json.dumps(chart).encode();files[ep]=json.dumps(ev).encode();files['manifest.yaml']=yaml.safe_dump(manifest).encode()
        target=tmp_path/'mutated.feedpak'
        with ZipFile(target,'w') as z:
            for name,data in files.items():z.writestr(name,data)
        assert verify(target)['status']=='failed',fault


def test_slide_cutoff_is_not_stretched_or_shortened():
    n=checked(document())['tracks'][0]['notes'][0]
    with pytest.raises(ImportFailure):
        _retime_note(n,{'offset':0,'scale':1,'terminalSustains':{'audioDuration':1.2}},1.2)


def test_generated_lane_waits_with_held_fret():
    from feedback_converter.generated_hand_positions import _position_spans
    from feedback_converter.verify_chart_guidance import _slide_fits
    n={'t':0,'f':7,'s':0,'sl':19,'sus':3,'slide_interval':{'start':2,'end':3}}
    spans=_position_spans(0,3,n)
    assert all(cells==(7,) for left,right,cells in spans if left<2)
    assert _slide_fits(n,0,1.9,{'fret':7,'width':4})
    assert not _slide_fits(n,0,1.9,{'fret':15,'width':4})
    for left,right,cells in spans:
        anchor={'fret':min(cells),'width':max(cells)-min(cells)+1}
        assert _slide_fits(n,0,(left+right)/2,anchor)
