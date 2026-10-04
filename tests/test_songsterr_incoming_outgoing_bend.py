"""Boundary slide cues compose without inventing attacks or shortening bends."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_songsterr_bend_timing import checked, source
from test_songsterr_chord_bend_timing import notes
from test_songsterr_overlapping_bends import sample
from test_song_import_score import import_json, beat, measure
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.hybrid_lead import choose_main, normalize_options
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.verification import verify_import

REFERENCE=json.loads((Path(__file__).parent/'fixtures/songsterr_incoming_outgoing_bend_reference.json').read_text())


def document(incoming='below', outgoing='downwards', vibrato=True):
    d=source(1,3);bs=d['parts'][0]['measures'][0]['voices'][0]['beats']
    bs[0]['notes'][0]['slide']=incoming;bs[1]['notes'][0]['slide']=outgoing
    if vibrato:
        bs[0]['notes'][0]['leftHandVibrato']='slight'
        bs[1]['notes'][0]['leftHandVibrato']='wide'
    return d


@pytest.mark.parametrize('case',REFERENCE['cases'],ids=lambda c:c['id'])
def test_native_reference_and_independent_verifier(case):
    assert REFERENCE['referenceSha256']=='4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    doc=deepcopy(case['source']);p=checked(doc);assert doc==case['source']
    e=next(e for e in p['fingerBendTimingEvidence'] if all(e[k]==v for k,v in case['target'].items()))
    if not case['candidate']:
        assert e==case['previousEvidence'] and e['status']=='deferred'
        assert not (e.get('initialSlideIn') and e.get('terminalSlideOut'))
        return
    assert e['status']=='resolved' and e['initialSlideIn'] and e['terminalSlideOut']
    # Removing only visual cues in diagnostic copies cannot change the bend,
    # attacks, durations or other techniques; the package keeps both cues.
    for remove in ('incoming','outgoing','both'):
        control=deepcopy(doc)
        directions={'above','below'} if remove=='incoming' else {'upwards','downwards'} if remove=='outgoing' else {'above','below','upwards','downwards'}
        for part in control['parts']:
            for bar in part['measures']:
                for voice in bar['voices']:
                    for beat in voice['beats']:
                        for n in beat['notes']:
                            if n.get('slide') in directions:del n['slide']
        ignored={'slide_in_marks'} if remove=='incoming' else {'slide_out','slide_out_marks'} if remove=='outgoing' else {'slide_in_marks','slide_out','slide_out_marks'}
        assert [{k:v for k,v in n.items() if k not in ignored} for n in notes(p)]==notes(checked(control))
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
    assert count>0


@pytest.mark.parametrize('incoming',['above','below'])
@pytest.mark.parametrize('outgoing',['upwards','downwards'])
@pytest.mark.parametrize('vibrato',[False,True])
def test_one_attack_and_original_boundaries(incoming,outgoing,vibrato):
    p=checked(document(incoming,outgoing,vibrato));n=notes(p)[0];e=p['fingerBendTimingEvidence'][0]
    assert len(notes(p))==1 and (n['t'],n['f'],n['sus'])==(0,7,2)
    assert n['bnv']==[{'t':0.,'v':0.},{'t':2.,'v':2.}]
    assert n['slide_in_marks']==[{'time':0.,'direction':'up' if incoming=='below' else 'down'}]
    assert n['slide_out_marks']==[{'start':.5,'end':2.,'direction':'up' if outgoing=='upwards' else 'down'}]
    assert e['initialSlideIn']['attackTiming']=='authored-note'
    assert e['terminalSlideOut']['pitchPolicy']=='independent-source-bend'
    if vibrato:assert n['vibrato_marks']==[{'start':0.,'end':.5,'intensity':'slight'},{'start':.5,'end':2.,'intensity':'wide'}]
    else:assert 'vibrato_marks' not in n


@pytest.mark.parametrize('extra',['targeted','fixed-artificial','settled-overlap'])
def test_individually_supported_combinations_do_not_broaden_this_rule(extra):
    d=document();bs=d['parts'][0]['measures'][0]['voices'][0]['beats']
    if extra=='targeted':
        bs[-1]['notes'][0]['slide']='shift'
        d['parts'][0]['measures'].append(measure(beat(fret=9)))
    elif extra=='fixed-artificial':
        for b in bs:b['notes'][0].update(harmonic='artificial',harmonicFret=12)
    else:
        from test_songsterr_overlapping_bends import source as overlapping
        d=overlapping();bs=d['parts'][0]['measures'][0]['voices'][0]['beats']
        bs[0]['notes'][0]['slide']='below';bs[-1]['notes'][0]['slide']='downwards'
    e=checked(d)['fingerBendTimingEvidence'][0]
    assert e['status']=='deferred'
    assert 'initialSlideIn' not in e and 'terminalSlideOut' not in e


def test_displaced_attack_stays_guarded(monkeypatch):
    from fractions import Fraction
    from feedback_converter.song_import import bend_timing, verify_bend_timing
    finish=bend_timing.finish;reconstruct=verify_bend_timing.reconstruct
    def output(output, articulation, *args, **kwargs):
        articulation['bend_segments'][0][0].attack_offset=Fraction(1,8)
        return finish(output,articulation,*args,**kwargs)
    def source_atoms(event,*args,**kwargs):
        event['bend_atoms'][0][0].attack_offset=Fraction(1,8)
        return reconstruct(event,*args,**kwargs)
    monkeypatch.setattr(bend_timing,'finish',output)
    monkeypatch.setattr(verify_bend_timing,'reconstruct',source_atoms)
    e=checked(document())['fingerBendTimingEvidence'][0]
    assert e['status']=='deferred' and 'initialSlideIn' not in e and 'terminalSlideOut' not in e


@pytest.mark.parametrize('piecewise',[False,True])
@pytest.mark.parametrize('hybrid',[False,True])
@pytest.mark.parametrize('direction',['upwards','downwards'])
def test_package_contract_and_mutations(tmp_path,piecewise,hybrid,direction):
    from test_song_import_builder import inputs
    _,audio,_,job=inputs(tmp_path)
    p=import_json(tmp_path,document(outgoing=direction));path=tmp_path/'score.json'
    if hybrid:p=load_performance(path,composition_context=True)
    alignment={'status':'validated','offset':.25,'scale':1.}
    if piecewise:alignment.update(mapping='piecewise-linear',anchors=[{'score':0,'audio':.25},{'score':.5,'audio':.75},{'score':4,'audio':4.95}],tempos=[{'time':.25,'bpm':120},{'time':.75,'bpm':100}])
    options=normalize_options({'enabled':hybrid});sha=hashlib.sha256(path.read_bytes()).hexdigest()
    if hybrid:options.update(mainTrackId=choose_main(p,options,sha),sourceSha256=sha)
    recipe={'preservationContract':70,'scoreHash':sha,'audioHash':audio['hash'],**({'hybridLead':options} if hybrid else {})}
    old=tmp_path/'old';old.mkdir()
    with pytest.raises(ImportFailure,match='contract 70'):
        build_feedpak(p,audio,alignment,old,output_dir=tmp_path/'old-out',source_path=path,compatibility=p['compatibilityReport'],recipe={'preservationContract':69})
    result=build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=path,
        compatibility=p['compatibilityReport'],recipe=recipe,
        hybrid_lead={'enabled':hybrid,'mainTrackId':options.get('mainTrackId'),'options':options})
    archive=Path(result['stagingPath']);verify=lambda file:verify_import(path,file,alignment,hybrid_options=options if hybrid else None)
    assert verify(archive)['status']=='passed'
    with ZipFile(archive) as z:original={n:z.read(n) for n in z.namelist()}
    m=yaml.safe_load(original['manifest.yaml']);ep=m['song_import']['fingerBendTimingFile']
    assert json.loads(original[ep])['version']==11 and original[m['song_import']['sourceFile']]==path.read_bytes()
    if hybrid:assert any(a['name']=='Hybrid Lead' for a in m['arrangements'])
    for fault in ('curve','incoming-absent','incoming-direction','incoming-time','outgoing-absent','outgoing-direction','outgoing-start','outgoing-end','attack','duration','fret','vibrato','initial-evidence','terminal-evidence','version','contract'):
        files=dict(original);manifest=deepcopy(m);cp=manifest['arrangements'][0]['file']
        chart=json.loads(files[cp]);n=chart['notes'][0];ev=json.loads(files[ep])
        if fault=='curve':n['bnv'][-1]['t']-=.1
        elif fault=='incoming-absent':del n['slide_in_marks']
        elif fault=='incoming-direction':n['slide_in_marks'][0]['direction']='down'
        elif fault=='incoming-time':n['slide_in_marks'][0]['time']+=.1
        elif fault=='outgoing-absent':del n['slide_out_marks']
        elif fault=='outgoing-direction':n['slide_out_marks'][0]['direction']='down' if direction=='upwards' else 'up'
        elif fault=='outgoing-start':n['slide_out_marks'][0]['start']+=.1
        elif fault=='outgoing-end':n['slide_out_marks'][0]['end']-=.1
        elif fault=='attack':n['t']+=.1
        elif fault=='duration':n['sus']-=.1
        elif fault=='fret':n['f']+=1
        elif fault=='vibrato':del n['vibrato_marks']
        elif fault=='initial-evidence':del ev['gestures'][0]['initialSlideIn']
        elif fault=='terminal-evidence':del ev['gestures'][0]['terminalSlideOut']
        elif fault=='version':ev.update(version=10,policy='songsterr-finger-bend-timing-v10')
        else:manifest['song_import']['preservationContract']=69
        files[cp]=json.dumps(chart).encode();files[ep]=json.dumps(ev).encode();files['manifest.yaml']=yaml.safe_dump(manifest).encode()
        dest=tmp_path/f'{fault}.feedpak'
        with ZipFile(dest,'w') as z:
            for name,data in files.items():z.writestr(name,data)
        assert verify(dest)['status']=='failed',fault
