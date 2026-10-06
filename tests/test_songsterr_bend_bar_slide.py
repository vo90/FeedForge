"""Independent held-bend, explicit bar and terminal slide composition."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile
import pytest
import yaml
from test_songsterr_bend_timing import checked, FALL
from test_song_import_score import import_json
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.hybrid_lead import choose_main, normalize_options
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.verification import verify_import

REFERENCE=json.loads((Path(__file__).parent/'fixtures/songsterr_bend_bar_slide_reference.json').read_text())

def document():
    return deepcopy(REFERENCE['cases'][0]['source'])

@pytest.mark.parametrize('case',REFERENCE['cases'],ids=lambda c:c['id'])
def test_independent_captured_constituents(case):
    d=deepcopy(case['source']);p=checked(d);e=p['fingerBendTimingEvidence'][0]
    assert d==case['source']
    assert REFERENCE['referenceSha256']=='4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    assert e['status']=='resolved' and e['barCurve']['policy']=='independent-explicit-control'
    assert e['terminalSlideOut']['direction'] in ('up','down')
    assert p['tracks'][0]['notes']==[case['note']]
    for layer in ('slide','bar'):
        plain=deepcopy(d)
        for b in plain['parts'][0]['measures'][0]['voices'][0]['beats']:
            if layer=='slide':
                for n in b['notes']:n.pop('slide',None)
            else:b.pop('tremoloBar',None)
        stripped=checked(plain)['tracks'][0]['notes'][0]
        assert stripped['bnv']==case['note']['bnv']
        field='whammy' if layer=='slide' else 'slide_out_marks'
        assert stripped[field]==case['note'][field]

def test_identity_tempo_and_repeat_do_not_change_eligibility():
    d=document();part=d['parts'][0]
    part['automations']['tempo'].append({'measure':0,'position':960,'bpm':60,'type':4})
    p=checked(d);n=p['tracks'][0]['notes'][0]
    assert n['sus']==3.5 and n['slide_out_marks']==[{'direction':'up','start':1.5,'end':3.5}]
    assert n['bnv'][1]['t']==.5
    d.update(title='Unrelated title',songId=987654,revisionId=321)
    assert checked(d)['tracks'][0]['notes']==p['tracks'][0]['notes']
    part['measures'][0].update(repeatStart=True,repeat=2)
    repeated=checked(d)
    assert len(repeated['fingerBendTimingEvidence'])==2
    assert all(e['status']=='resolved' for e in repeated['fingerBendTimingEvidence'])

@pytest.mark.parametrize('fault',['overlap','settled-overlap','targeted','incoming','intermediate','harmonic','mute','palm','beat-vibrato','hopo','qualitative-bar','strum'])
def test_unqualified_mixtures_stay_guarded(fault):
    d=document();bs=d['parts'][0]['measures'][0]['voices'][0]['beats']
    if fault in ('overlap','settled-overlap'):
        if fault=='overlap':bs[0]['notes'][0]['bend']['points']=[{'position':0,'tone':0},{'position':60,'tone':100}]
        bs[2]['notes'][0]['bend']=deepcopy(FALL)
    elif fault=='targeted':
        bs[2]['notes'][0]['slide']='shift';bs[2]['duration']=[1,4]
        bs.append({'duration':[1,4],'notes':[{'string':0,'fret':9}]})
    elif fault=='incoming':bs[0]['notes'][0]['slide']='below'
    elif fault=='intermediate':bs[1]['notes'][0]['slide']='downwards'
    elif fault=='harmonic':bs[0]['notes'][0]['harmonic']='pinch'
    elif fault=='mute':
        for b in bs:b['notes'][0]['dead']=True
    elif fault=='palm':bs[0]['palmMute']=True
    elif fault=='beat-vibrato':bs[0]['vibrato']='wide'
    elif fault=='hopo':
        bs[2]['notes'][0]['hp']=True;bs[2]['duration']=[1,4]
        bs.append({'duration':[1,4],'notes':[{'string':0,'fret':9}]})
    elif fault=='qualitative-bar':
        for b in bs:b.pop('tremoloBar');b['vibratoWithTremoloBar']='wide'
    else:
        bs[2]['notes'][0]['bend']=bs[0]['notes'][0].pop('bend')
        bs[0]['brushStroke']={'direction':'down','duration':30,'shift':100}
        bs[0]['notes'].append({'string':1,'fret':5})
    e=checked(d)['fingerBendTimingEvidence'][0]
    if fault == 'beat-vibrato':
        control = deepcopy(d); control['parts'][0]['measures'][0]['voices'][0]['beats'][0].pop('vibrato')
        assert e == checked(control)['fingerBendTimingEvidence'][0]
        assert e['status'] == 'resolved' and e['barCurve'] and e['terminalSlideOut']
    else:
        assert e['status']=='deferred'
        assert 'barCurve' not in e and 'terminalSlideOut' not in e

@pytest.mark.parametrize('piecewise',[False,True])
@pytest.mark.parametrize('hybrid',[False,True])
def test_archive_contract_and_mutation_rejection(tmp_path,piecewise,hybrid):
    from test_song_import_builder import inputs
    _,audio,_,job=inputs(tmp_path)
    p=import_json(tmp_path,document());source=tmp_path/'score.json'
    if hybrid:p=load_performance(source,composition_context=True)
    alignment={'status':'validated','offset':.25,'scale':1.}
    if piecewise:alignment.update(mapping='piecewise-linear',anchors=[{'score':0,'audio':.25},{'score':.75,'audio':1.},{'score':4,'audio':4.9}],tempos=[{'time':.25,'bpm':120},{'time':1.,'bpm':100}])
    sha=hashlib.sha256(source.read_bytes()).hexdigest();options=normalize_options({'enabled':hybrid})
    if hybrid:options.update(mainTrackId=choose_main(p,options,sha),sourceSha256=sha)
    recipe={'preservationContract':81,'scoreHash':sha,'audioHash':audio['hash'],**({'hybridLead':options} if hybrid else {})}
    old=tmp_path/'old';old.mkdir()
    with pytest.raises(ImportFailure,match='contract 81'):
        build_feedpak(p,audio,alignment,old,output_dir=tmp_path/'old-out',source_path=source,compatibility=p['compatibilityReport'],recipe={'preservationContract':80})
    built=build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=source,compatibility=p['compatibilityReport'],recipe=recipe,
                       hybrid_lead={'enabled':hybrid,'mainTrackId':options.get('mainTrackId'),'options':options})
    verify=lambda path:verify_import(source,path,alignment,hybrid_options=options if hybrid else None)
    assert verify(built['stagingPath'])['status']=='passed'
    with ZipFile(built['stagingPath']) as z:original={k:z.read(k) for k in z.namelist()}
    manifest=yaml.safe_load(original['manifest.yaml']);ep=manifest['song_import']['fingerBendTimingFile']
    assert json.loads(original[ep])['version']==20
    assert original[manifest['song_import']['sourceFile']]==source.read_bytes()
    if hybrid:assert any(a['name']=='Hybrid Lead' for a in manifest['arrangements'])
    for fault in ('bend','bar-time','bar-pitch','bar-policy','bar-absent','slide-start','slide-end','slide-direction','slide-absent','attack','fret','duration','extra-attack',
                  'evidence-source','evidence-time','evidence-pitch','evidence-policy','evidence-bar-absent','evidence-slide-absent','version','contract'):
        files=dict(original);m=deepcopy(manifest);cp=m['arrangements'][0]['file'];chart=json.loads(files[cp]);n=chart['notes'][0];ev=json.loads(files[ep])
        if fault=='bend':n['bnv'][1]['t']/=3
        elif fault=='bar-time':n['whammy']['segments'][0]['end']+=.1
        elif fault=='bar-pitch':n['whammy']['segments'][0]['curve'][1]['v']+=1
        elif fault=='bar-policy':n['whammy']['policy']='required'
        elif fault=='bar-absent':del n['whammy']
        elif fault.startswith('slide-'):
            field=fault.split('-')[1]
            if field=='absent':del n['slide_out_marks']
            elif field=='direction':n['slide_out_marks'][0][field]='down'
            else:n['slide_out_marks'][0][field]-=.1
        elif fault=='attack':n['t']+=.1
        elif fault=='fret':n['f']+=1
        elif fault=='duration':n['sus']-=.1
        elif fault=='extra-attack':chart['notes'].append(deepcopy(n))
        elif fault=='evidence-source':ev['gestures'][0]['barCurve']['segments'][0]['sourceId']='songsterr:0:0:0:2:0'
        elif fault=='evidence-time':ev['gestures'][0]['terminalSlideOut']['start']+=.1
        elif fault=='evidence-pitch':ev['gestures'][0]['barCurve']['segments'][0]['curve'][0]['value']+=.1
        elif fault=='evidence-policy':ev['gestures'][0]['barCurve']['policy']='summed'
        elif fault=='evidence-bar-absent':del ev['gestures'][0]['barCurve']
        elif fault=='evidence-slide-absent':del ev['gestures'][0]['terminalSlideOut']
        elif fault=='version':ev.update(version=19,policy='songsterr-finger-bend-timing-v19')
        else:m['song_import']['preservationContract']=80
        files[cp]=json.dumps(chart).encode();files[ep]=json.dumps(ev).encode();files['manifest.yaml']=yaml.safe_dump(m).encode()
        target=tmp_path/'mutated.feedpak'
        with ZipFile(target,'w') as z:
            for name,data in files.items():z.writestr(name,data)
        assert verify(target)['status']=='failed',fault
