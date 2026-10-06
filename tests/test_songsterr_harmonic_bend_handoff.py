"""Fixed harmonic identity and settled bend timing compose independently."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import import_json, beat, measure
from test_songsterr_bend_timing import checked
from test_songsterr_overlapping_bends import source, sample
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.hybrid_lead import normalize_options, choose_main
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.verification import verify_import

REFERENCE=json.loads((Path(__file__).parent/'fixtures/songsterr_harmonic_handoff_reference.json').read_text())


@pytest.mark.parametrize('case',REFERENCE['cases'],ids=lambda c:c['id'])
def test_native_reference(case):
    assert REFERENCE['referenceSha256']=='4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    p=checked(case['source']);n=p['tracks'][0]['notes'][0]
    assert p['fingerBendTimingEvidence'][0]['overlap']['fixedHarmonic']==n['harmonic_target']
    for profile in case['profiles']:
        assert profile['bendAndTimingIndependent'] and profile['samples']
        for point in profile['samples']:
            assert sample(n['bnv'],point['t'])==pytest.approx(point['v'],abs=.023)


def document(node=12, bass=False, vibrato=False):
    doc=source()
    for b in doc['parts'][0]['measures'][0]['voices'][0]['beats']:
        b['notes'][0].update(harmonic='artificial',harmonicFret=node)
    if bass:doc['tracks'][0].update(instrumentId=33,tuning=[43,38,33,28])
    if vibrato:doc['parts'][0]['measures'][0]['voices'][0]['beats'][-1]['notes'][0]['leftHandVibrato']='wide'
    return doc


@pytest.mark.parametrize('node',[5,7,12])
@pytest.mark.parametrize('bass',[False,True])
@pytest.mark.parametrize('vibrato',[False,True])
def test_fixed_target_retains_one_attack_and_full_tie_clock(node,bass,vibrato):
    d=document(node,bass,vibrato);original=deepcopy(d);p=checked(d)
    assert d==original
    n=p['tracks'][0]['notes'][0];e=p['fingerBendTimingEvidence'][0]
    assert len(p['tracks'][0]['notes'])==1 and (n['t'],n['sus'],n['f'])==(0,2,7)
    assert n['bnv']==[{'t':0.,'v':0.},{'t':.5,'v':2.},{'t':1.,'v':2.},{'t':2.,'v':0.}]
    assert e['status']=='resolved' and e['overlap']['fixedHarmonic']==n['harmonic_target']
    assert n['harmonic_target']['node']==node and n['harmonic_target']['kind']=='artificial'
    control=deepcopy(d)
    for b in control['parts'][0]['measures'][0]['voices'][0]['beats']:
        del b['notes'][0]['harmonic'];del b['notes'][0]['harmonicFret']
    assert {k:v for k,v in n.items() if k!='harmonic_target'}==checked(control)['tracks'][0]['notes'][0]
    if vibrato:assert n['vibrato_marks']==[{'start':1.,'end':2.,'intensity':'wide'}]


@pytest.mark.parametrize('variant',['repeat-tempo','following-slide','held-prebend','different-title'])
def test_authored_clock_is_general(variant):
    d=document(vibrato=True);bar=d['parts'][0]['measures'][0];bs=bar['voices'][0]['beats']
    if variant=='repeat-tempo':
        bar.update(repeatStart=True,repeat=2)
        d['parts'][0]['automations']['tempo'].append({'measure':0,'position':480,'bpm':60,'type':4})
    elif variant=='following-slide':d['parts'][0]['measures'].append(measure(beat(fret=9,slide='below')))
    elif variant=='held-prebend':
        for b in (bs[0],bs[-1]):b['notes'][0]['bend']={'points':[{'position':0,'tone':100},{'position':60,'tone':100}]}
    else:d.update(title='Any title',songId=998,revisionId=12)
    p=checked(d)
    assert all(e['status']=='resolved' and e['overlap']['fixedHarmonic'] for e in p['fingerBendTimingEvidence'])
    if variant=='repeat-tempo':
        assert len(p['tracks'][0]['notes'])==2
        assert p['tracks'][0]['notes'][0]['bnv']==[{'t':0.,'v':0.},{'t':.25,'v':1.},{'t':.75,'v':2.},{'t':1.75,'v':2.},{'t':3.75,'v':0.}]
    elif variant=='held-prebend':assert all(p['v']==2 for p in p['tracks'][0]['notes'][0]['bnv'])
    else:assert p['tracks'][0]['notes'][0]['sus']==2


@pytest.mark.parametrize('fault',['changing','missing','mixed-pinch','semi','conflicting','nearly-settled','beat-only','palm-mute','let-ring','hopo','slide-in','whammy'])
def test_unqualified_combinations_keep_warning(fault,tmp_path):
    d=document(vibrato=True);bs=d['parts'][0]['measures'][0]['voices'][0]['beats']
    if fault=='changing':bs[-1]['notes'][0]['harmonicFret']=7
    elif fault=='missing':del bs[1]['notes'][0]['harmonic'];del bs[1]['notes'][0]['harmonicFret']
    elif fault=='mixed-pinch':bs[-1]['notes'][0]['harmonic']='pinch'
    elif fault=='semi':
        for b in bs:b['notes'][0]['harmonic']=fault
    elif fault in ('conflicting','nearly-settled'):
        bs[0]['notes'][0]['bend']={'points':[{'position':0,'precisePosition':0,'tone':0},{'position':60,'precisePosition':100 if fault=='conflicting' else 50.001,'tone':100}]}
    elif fault=='beat-only':bs[1]['vibrato']=True
    elif fault=='palm-mute':bs[0]['palmMute']=True
    elif fault=='let-ring':bs[0]['letRing']=True
    elif fault=='hopo':bs[-1]['notes'][0]['hp']=True;d['parts'][0]['measures'].append(measure(beat(fret=5)))
    elif fault=='slide-in':bs[0]['notes'][0]['slide']='above'
    elif fault=='whammy':bs[0]['tremoloBar']={'points':[{'position':0,'tone':0},{'position':60,'tone':-50}]}
    p=checked(d);e=p['fingerBendTimingEvidence'][0]
    findings=import_json(tmp_path,d)['compatibilityReport']['findings']
    if fault=='beat-only':
        control=deepcopy(d);del control['parts'][0]['measures'][0]['voices'][0]['beats'][1]['vibrato']
        baseline=checked(control)
        assert p['tracks'][0]['notes']==baseline['tracks'][0]['notes']
        assert e==baseline['fingerBendTimingEvidence'][0]
        assert e['status']=='resolved' and e['overlap']['fixedHarmonic']
        assert p['tracks'][0]['notes'][0]['vibrato_marks']==[{'start':1.,'end':2.,'intensity':'wide'}]
        assert any(f['feature']=='beat.vibrato' for f in findings)
        assert not any(f['feature']=='note.bend_timing' for f in findings)
    else:
        assert e['status']=='deferred' and 'fixedHarmonic' not in e['overlap']
        assert any(f['feature']=='note.bend_timing' for f in findings)


@pytest.mark.parametrize('piecewise',[False,True])
@pytest.mark.parametrize('hybrid',[False,True])
def test_package_contract_and_mutations(tmp_path,piecewise,hybrid):
    from test_song_import_builder import inputs
    _,audio,_,job=inputs(tmp_path)
    p=import_json(tmp_path,document(vibrato=True));path=tmp_path/'score.json'
    if hybrid:p=load_performance(path,composition_context=True)
    alignment={'status':'validated','offset':.25,'scale':1.}
    if piecewise:alignment.update(mapping='piecewise-linear',anchors=[{'score':0,'audio':.25},{'score':.5,'audio':.75},{'score':4,'audio':4.95}],tempos=[{'time':.25,'bpm':120},{'time':.75,'bpm':100}])
    options=normalize_options({'enabled':hybrid});sha=hashlib.sha256(path.read_bytes()).hexdigest()
    if hybrid:options.update(mainTrackId=choose_main(p,options,sha),sourceSha256=sha)
    recipe={'preservationContract':71,'scoreHash':sha,'audioHash':audio['hash'],**({'hybridLead':options} if hybrid else {})}
    old=tmp_path/'old';old.mkdir()
    with pytest.raises(ImportFailure,match='contract 71'):
        build_feedpak(p,audio,alignment,old,output_dir=tmp_path/'old-out',source_path=path,compatibility=p['compatibilityReport'],recipe={'preservationContract':70})
    result=build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=path,
        compatibility=p['compatibilityReport'],recipe=recipe,
        hybrid_lead={'enabled':hybrid,'mainTrackId':options.get('mainTrackId'),'options':options})
    archive=Path(result['stagingPath']);verify=lambda file:verify_import(path,file,alignment,hybrid_options=options if hybrid else None)
    assert verify(archive)['status']=='passed'
    with ZipFile(archive) as z:original={name:z.read(name) for name in z.namelist()}
    m=yaml.safe_load(original['manifest.yaml']);ep=m['song_import']['fingerBendTimingFile']
    assert json.loads(original[ep])['version']==12 and original[m['song_import']['sourceFile']]==path.read_bytes()
    if hybrid:assert any(a['name']=='Hybrid Lead' for a in m['arrangements'])
    for fault in ('bend','harmonic-target','harmonic-missing','vibrato','attack','fret','duration','evidence-target','evidence-absent','version','contract'):
        files=dict(original);manifest=deepcopy(m);cp=manifest['arrangements'][0]['file']
        chart=json.loads(files[cp]);n=chart['notes'][0];ev=json.loads(files[ep])
        if fault=='bend':n['bnv'][1]['t']/=4
        elif fault=='harmonic-target':n['harmonic_target']['interval']+=12
        elif fault=='harmonic-missing':del n['harmonic_target']
        elif fault=='vibrato':del n['vibrato_marks']
        elif fault=='attack':n['t']+=.1
        elif fault=='fret':n['f']+=1
        elif fault=='duration':n['sus']-=.1
        elif fault=='evidence-target':ev['gestures'][0]['overlap']['fixedHarmonic']['node']=7
        elif fault=='evidence-absent':del ev['gestures'][0]['overlap']['fixedHarmonic']
        elif fault=='version':ev.update(version=11,policy='songsterr-finger-bend-timing-v11')
        else:manifest['song_import']['preservationContract']=70
        files[cp]=json.dumps(chart).encode();files[ep]=json.dumps(ev).encode();files['manifest.yaml']=yaml.safe_dump(manifest).encode()
        dest=tmp_path/f'{fault}.feedpak'
        with ZipFile(dest,'w') as z:
            for name,data in files.items():z.writestr(name,data)
        assert verify(dest)['status']=='failed',fault
