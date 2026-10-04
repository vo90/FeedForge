"""A terminal slide cue composes only with independently settled bend handoffs."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_songsterr_bend_timing import checked
from test_songsterr_chord_bend_timing import notes
from test_songsterr_overlapping_bends import source, sample
from test_song_import_score import import_json
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.hybrid_lead import choose_main, normalize_options
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.verification import verify_import

REFERENCE=json.loads((Path(__file__).parent/'fixtures/songsterr_overlap_slide_out_reference.json').read_text())


def document(direction='downwards', vibrato=True):
    d=source();bs=d['parts'][0]['measures'][0]['voices'][0]['beats']
    bs[-1]['notes'][0]['slide']=direction
    if vibrato:
        bs[1]['notes'][0]['leftHandVibrato']='slight'
        bs[-1]['notes'][0]['leftHandVibrato']='wide'
    return d


@pytest.mark.parametrize('case',REFERENCE['cases'],ids=lambda c:c['id'])
def test_native_reference_and_independent_verifier(case):
    assert REFERENCE['referenceSha256']=='4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    doc=deepcopy(case['source']);p=checked(doc);assert doc==case['source']
    e=next(e for e in p['fingerBendTimingEvidence'] if all(e[k]==v for k,v in case['target'].items()))
    if not case['candidate']:
        assert e==case['previousEvidence']
        assert e['status']=='deferred' and 'slideOutTiming' not in e['overlap']
        return
    assert e['status']=='resolved' and e['overlap']['classification']=='clear-handoff'
    assert e['overlap']['slideOutTiming']=='independent-terminal-cue'
    # Removing just the visual cue must preserve all other note data. This
    # diagnostic control is never used as the user's source or output.
    control=deepcopy(doc)
    for part in control['parts']:
        for bar in part['measures']:
            for voice in bar['voices']:
                for beat in voice['beats']:
                    for n in beat['notes']:
                        if n.get('slide') in ('upwards','downwards'):del n['slide']
    assert [{k:v for k,v in n.items() if k not in ('slide_out','slide_out_marks')} for n in notes(p)]==notes(checked(control))
    tempos=p['scoreTimeline']['tempoPoints']
    def quarter(t):
        a=next(a for a in reversed(tempos) if a['time']<=t+1e-8)
        return a['quarter']+(t-a['time'])*a['bpm']/60
    def seconds(q):
        a=next((a for a in reversed(tempos) if a['quarter']<=q+1e-8),tempos[0])
        return a['time']+(q-a['quarter'])*60/a['bpm']
    count=0
    for captured in case['segments']:
        index,s=next((i,s) for i,s in enumerate(e['segments']) if (s['sourceId'],s['occurrence'])==(captured['sourceId'],captured['occurrence']))
        left=quarter(s['start']);span=quarter(s['gestureEnd'])-left
        following=next((s for s in e['segments'][index+1:] if s['bend']),None)
        cutoff=following['start'] if following else e['end']
        for profile in captured['profiles']:
            for pos,value in profile['samples']:
                time=seconds(left+pos*span)
                if time<cutoff-1e-8:
                    step=profile['positionTolerance']
                    lo=seconds(left+max(0,pos-step)*span)-e['start'];hi=seconds(left+(pos+step)*span)-e['start']
                    values=[sample(e['curve'],lo),sample(e['curve'],hi),*[k['v'] for k in e['curve'] if lo<=k['t']<=hi]]
                    assert min(values)-.023<=value<=max(values)+.023
                    count+=1
                if following:
                    margin=abs(seconds(quarter(cutoff)+profile['tickTolerance']*span)-cutoff)
                    assert not cutoff+margin<time<e['end']-1e-7
    assert count>0


@pytest.mark.parametrize('direction',['upwards','downwards'])
@pytest.mark.parametrize('vibrato',[False,True])
def test_one_attack_with_independently_timed_controls(direction,vibrato):
    p=checked(document(direction,vibrato));n=notes(p)[0];e=p['fingerBendTimingEvidence'][0]
    assert len(notes(p))==1 and (n['t'],n['f'],n['sus'])==(0,7,2)
    assert n['bnv']==[{'t':0.,'v':0.},{'t':.5,'v':2.},{'t':1.,'v':2.},{'t':2.,'v':0.}]
    assert n['slide_out_marks']==[{'start':1.,'end':2.,'direction':'up' if direction=='upwards' else 'down'}]
    if vibrato:
        assert n['vibrato_marks']==[{'start':.5,'end':1.,'intensity':'slight'},{'start':1.,'end':2.,'intensity':'wide'}]
        assert e['overlap']['vibratoTiming']=='independent-note-controls'
    else:assert 'vibrato_marks' not in n


@pytest.mark.parametrize('piecewise',[False,True])
@pytest.mark.parametrize('hybrid',[False,True])
@pytest.mark.parametrize('direction',['upwards','downwards'])
def test_package_contract_and_mutations(tmp_path,piecewise,hybrid,direction):
    from test_song_import_builder import inputs
    _,audio,_,job=inputs(tmp_path)
    p=import_json(tmp_path,document(direction));path=tmp_path/'score.json'
    if hybrid:p=load_performance(path,composition_context=True)
    alignment={'status':'validated','offset':.25,'scale':1.}
    if piecewise:alignment.update(mapping='piecewise-linear',anchors=[{'score':0,'audio':.25},{'score':.5,'audio':.75},{'score':4,'audio':4.95}],tempos=[{'time':.25,'bpm':120},{'time':.75,'bpm':100}])
    options=normalize_options({'enabled':hybrid});sha=hashlib.sha256(path.read_bytes()).hexdigest()
    if hybrid:options.update(mainTrackId=choose_main(p,options,sha),sourceSha256=sha)
    recipe={'preservationContract':69,'scoreHash':sha,'audioHash':audio['hash'],**({'hybridLead':options} if hybrid else {})}
    old=tmp_path/'old';old.mkdir()
    with pytest.raises(ImportFailure,match='contract 69'):
        build_feedpak(p,audio,alignment,old,output_dir=tmp_path/'old-out',source_path=path,compatibility=p['compatibilityReport'],recipe={'preservationContract':68})
    result=build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=path,
        compatibility=p['compatibilityReport'],recipe=recipe,
        hybrid_lead={'enabled':hybrid,'mainTrackId':options.get('mainTrackId'),'options':options})
    archive=Path(result['stagingPath']);verify=lambda file:verify_import(path,file,alignment,hybrid_options=options if hybrid else None)
    assert verify(archive)['status']=='passed'
    with ZipFile(archive) as z:original={n:z.read(n) for n in z.namelist()}
    m=yaml.safe_load(original['manifest.yaml']);ep=m['song_import']['fingerBendTimingFile']
    assert json.loads(original[ep])['version']==10 and original[m['song_import']['sourceFile']]==path.read_bytes()
    if hybrid:assert any(a['name']=='Hybrid Lead' for a in m['arrangements'])
    for fault in ('curve','cue-absent','cue-direction','cue-start','cue-end','attack','duration','fret','vibrato','overlap','handoff','marker','version','contract'):
        files=dict(original);manifest=deepcopy(m);cp=manifest['arrangements'][0]['file']
        chart=json.loads(files[cp]);n=chart['notes'][0];ev=json.loads(files[ep])
        if fault=='curve':n['bnv'][1]['t']-=.1
        elif fault=='cue-absent':del n['slide_out_marks']
        elif fault=='cue-direction':n['slide_out_marks'][0]['direction']='down' if direction=='upwards' else 'up'
        elif fault=='cue-start':n['slide_out_marks'][0]['start']+=.1
        elif fault=='cue-end':n['slide_out_marks'][0]['end']-=.1
        elif fault=='attack':n['t']+=.1
        elif fault=='duration':n['sus']-=.1
        elif fault=='fret':n['f']+=1
        elif fault=='vibrato':del n['vibrato_marks']
        elif fault=='overlap':ev['gestures'][0]['overlap']['classification']='conflicting-controls'
        elif fault=='handoff':ev['gestures'][0]['overlap']['handoffs'][0]['classification']='changing-tail'
        elif fault=='marker':del ev['gestures'][0]['overlap']['slideOutTiming']
        elif fault=='version':ev.update(version=9,policy='songsterr-finger-bend-timing-v9')
        else:manifest['song_import']['preservationContract']=68
        files[cp]=json.dumps(chart).encode();files[ep]=json.dumps(ev).encode();files['manifest.yaml']=yaml.safe_dump(manifest).encode()
        dest=tmp_path/f'{fault}.feedpak'
        with ZipFile(dest,'w') as z:
            for name,data in files.items():z.writestr(name,data)
        assert verify(dest)['status']=='failed',fault
