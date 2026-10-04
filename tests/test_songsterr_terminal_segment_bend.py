"""A tied terminal bend retains its source clock alongside a slide-out cue."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, measure, import_json
from test_songsterr_bend_timing import checked, source, RISE, FALL
from test_songsterr_chord_bend_timing import notes
from test_songsterr_overlapping_bends import sample
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.hybrid_lead import choose_main, normalize_options
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.verification import verify_import

REFERENCE=json.loads((Path(__file__).parent/'fixtures/songsterr_terminal_segment_bend_reference.json').read_text())
HOLD={'points':[{'position':0,'tone':100},{'position':60,'tone':100}]}


def document(out='downwards', bend=FALL):
    doc=source(1,3,later=bend)
    doc['parts'][0]['measures'][0]['voices'][0]['beats'][1]['notes'][0].update(slide=out,leftHandVibrato='wide')
    return doc


@pytest.mark.parametrize('case',REFERENCE['cases'],ids=lambda c:c['id'])
def test_native_bend_controls_on_authored_musical_clock(case):
    assert REFERENCE['referenceSha256']=='4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    p=checked(case['source']);tempos=p['scoreTimeline']['tempoPoints']
    def quarter(t):
        a=next(a for a in reversed(tempos) if a['time']<=t+1e-8)
        return a['quarter']+(t-a['time'])*a['bpm']/60
    def seconds(q):
        a=next((a for a in reversed(tempos) if a['quarter']<=q+1e-8),tempos[0])
        return a['time']+(q-a['quarter'])*60/a['bpm']
    for target in case['targets']:
        e=next(e for e in p['fingerBendTimingEvidence'] if (e['sourceId'],e['occurrence'])==(target['sourceId'],target['occurrence']))
        assert e['status']=='resolved' and e['terminalSlideOut']['bendTiming']=='authored-segment'
        n=next(n for n in notes(p) if e['sourceId'] in n['source_ids'] and n['t']==e['start'])
        for segment in target['segments']:
            s=next(s for s in e['segments'] if (s['sourceId'],s['occurrence'])==(segment['sourceId'],segment['occurrence']))
            left=quarter(s['start']);span=quarter(s['gestureEnd'])-left
            for profile in segment['profiles']:
                step=profile['positionTolerance']
                for position,value in profile['samples']:
                    lo=seconds(left+max(0,position-step)*span)-e['start']
                    hi=seconds(left+(position+step)*span)-e['start']
                    values=[sample(n['bnv'],lo),sample(n['bnv'],hi),*[p['v'] for p in n['bnv'] if lo<=p['t']<=hi]]
                    assert min(values)-.023<=value<=max(values)+.023


@pytest.mark.parametrize('out',['downwards','upwards'])
@pytest.mark.parametrize('bend',[RISE,FALL,HOLD])
@pytest.mark.parametrize('program',[25,29,34])
def test_preserves_both_gestures_without_a_new_attack(out,bend,program):
    doc=document(out,bend)
    for item in (doc['tracks'][0],doc['parts'][0]):
        item['instrumentId']=program
        if program==34:item['tuning']=[43,38,33,28]
    p=checked(doc);n=notes(p)[0];e=p['fingerBendTimingEvidence'][0]
    assert len(notes(p))==1 and (n['t'],n['f'],n['sus'])==(0,7,2)
    assert n['slide_out_marks']==[{'start':.5,'end':2.,'direction':'down' if out=='downwards' else 'up'}]
    assert n['vibrato_marks']==[{'start':.5,'end':2.,'intensity':'wide'}]
    assert e['terminalSlideOut']['bendTiming']=='authored-segment'
    assert e['rule']==('bend-hold-slide-out' if bend==HOLD else 'bend-with-slide-out')
    control=deepcopy(doc);del control['parts'][0]['measures'][0]['voices'][0]['beats'][1]['notes'][0]['slide']
    assert {k:v for k,v in n.items() if k not in ('slide_out','slide_out_marks')}==notes(checked(control))[0]


@pytest.mark.parametrize('out',['downwards','upwards'])
def test_tempo_change_inside_terminal_bend_uses_quarters_not_linear_seconds(out):
    doc=document(out);doc['parts'][0]['automations']['tempo'].append({'measure':0,'position':1920,'bpm':60,'type':4})
    p=checked(doc);n=notes(p)[0]
    assert n['bnv']==[{'t':0.,'v':0.},{'t':.5,'v':2.},{'t':1.,'v':pytest.approx(4/3)},{'t':3.,'v':0.}]
    assert n['sus']==3 and n['slide_out_marks'][0]['end']==3


@pytest.mark.parametrize('extra',['earlier-out','targeted','harmonic','beat-vibrato','whammy','hopo','palm-mute','let-ring','changing-overlap'])
def test_compositions_keep_their_independent_findings(extra,tmp_path):
    doc=document();bs=doc['parts'][0]['measures'][0]['voices'][0]['beats']
    if extra=='earlier-out':bs[0]['notes'][0]['slide']='downwards'
    elif extra=='targeted':bs[1]['notes'][0]['slide']='shift';doc['parts'][0]['measures'].append(measure(beat(fret=5)))
    elif extra=='harmonic':bs[1]['notes'][0].update(harmonic='artificial',harmonicFret=12)
    elif extra=='beat-vibrato':bs[0]['wideVibrato']=True
    elif extra=='whammy':bs[1]['tremoloBar']=deepcopy(FALL)
    elif extra=='hopo':bs[1]['notes'][0]['hp']=True;doc['parts'][0]['measures'].append(measure(beat(fret=5)))
    elif extra=='palm-mute':bs[0]['palmMute']=True
    elif extra=='let-ring':bs[0]['letRing']=True
    else:
        bs[1]['duration']=[1,2];bs.insert(1,beat(fret=7,tie=True,duration=(1,4)))
    p=checked(doc);e=p['fingerBendTimingEvidence'][0]
    resolved=extra in ('beat-vibrato','targeted')
    assert e['status']==('resolved' if resolved else 'deferred')
    if extra=='beat-vibrato':assert e['terminalSlideOut']['beatVibrato']
    else:assert 'terminalSlideOut' not in e
    loaded=import_json(tmp_path,doc)
    findings=loaded['compatibilityReport']['findings']
    assert any(f['feature']=='note.bend_timing' for f in findings)==(not resolved)
    if extra=='beat-vibrato':assert any(f['feature']=='beat.wideVibrato' for f in findings)
    if extra=='targeted':assert e['targetedSlide']


@pytest.mark.parametrize('piecewise',[False,True])
@pytest.mark.parametrize('hybrid',[False,True])
@pytest.mark.parametrize('bend',[FALL,HOLD])
def test_package_contract_and_mutations(tmp_path,piecewise,hybrid,bend):
    from test_song_import_builder import inputs
    _,audio,_,job=inputs(tmp_path)
    p=import_json(tmp_path,document(bend=bend));path=tmp_path/'score.json'
    if hybrid:p=load_performance(path,composition_context=True)
    alignment={'status':'validated','offset':.25,'scale':1.}
    if piecewise:alignment.update(mapping='piecewise-linear',anchors=[{'score':0,'audio':.25},{'score':.5,'audio':.75},{'score':4,'audio':4.95}],tempos=[{'time':.25,'bpm':120},{'time':.75,'bpm':100}])
    options=normalize_options({'enabled':hybrid});sha=hashlib.sha256(path.read_bytes()).hexdigest()
    if hybrid:options.update(mainTrackId=choose_main(p,options,sha),sourceSha256=sha)
    recipe={'preservationContract':68,'scoreHash':sha,'audioHash':audio['hash'],**({'hybridLead':options} if hybrid else {})}
    old=tmp_path/'old';old.mkdir()
    with pytest.raises(ImportFailure,match='contract 68'):
        build_feedpak(p,audio,alignment,old,output_dir=tmp_path/'old-out',source_path=path,compatibility=p['compatibilityReport'],recipe={'preservationContract':67})
    result=build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=path,
        compatibility=p['compatibilityReport'],recipe=recipe,
        hybrid_lead={'enabled':hybrid,'mainTrackId':options.get('mainTrackId'),'options':options})
    archive=Path(result['stagingPath']);verify=lambda file:verify_import(path,file,alignment,hybrid_options=options if hybrid else None)
    assert verify(archive)['status']=='passed'
    with ZipFile(archive) as z:original={n:z.read(n) for n in z.namelist()}
    m=yaml.safe_load(original['manifest.yaml']);ep=m['song_import']['fingerBendTimingFile']
    assert json.loads(original[ep])['version']==9 and original[m['song_import']['sourceFile']]==path.read_bytes()
    if hybrid:assert any(a['name']=='Hybrid Lead' for a in m['arrangements'])
    for fault in ('curve','cue-absent','cue-direction','cue-start','cue-end','attack','duration','fret','vibrato','evidence','version','contract'):
        files=dict(original);manifest=deepcopy(m);cp=manifest['arrangements'][0]['file']
        chart=json.loads(files[cp]);n=chart['notes'][0];ev=json.loads(files[ep])
        if fault=='curve':n['bnv'][-1]['v']+=.1
        elif fault=='cue-absent':del n['slide_out_marks']
        elif fault=='cue-direction':n['slide_out_marks'][0]['direction']='up'
        elif fault=='cue-start':n['slide_out_marks'][0]['start']+=.1
        elif fault=='cue-end':n['slide_out_marks'][0]['end']-=.1
        elif fault=='attack':n['t']+=.1
        elif fault=='duration':n['sus']-=.1
        elif fault=='fret':n['f']+=1
        elif fault=='vibrato':del n['vibrato_marks']
        elif fault=='evidence':del ev['gestures'][0]['terminalSlideOut']['bendTiming']
        elif fault=='version':ev.update(version=8,policy='songsterr-finger-bend-timing-v8')
        else:manifest['song_import']['preservationContract']=67
        files[cp]=json.dumps(chart).encode();files[ep]=json.dumps(ev).encode();files['manifest.yaml']=yaml.safe_dump(manifest).encode()
        dest=tmp_path/f'{fault}.feedpak'
        with ZipFile(dest,'w') as z:
            for name,data in files.items():z.writestr(name,data)
        assert verify(dest)['status']=='failed',fault
