"""Resolved outgoing legato must not compress an otherwise qualified bend."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, measure, import_json
from test_songsterr_bend_timing import checked
from test_songsterr_overlapping_bends import sample
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.hybrid_lead import normalize_options, choose_main
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.verification import verify_import

REFERENCE=json.loads((Path(__file__).parent/'fixtures/songsterr_outgoing_hopo_bend_reference.json').read_text())


def document():
    return deepcopy(REFERENCE['cases'][0]['source'])


@pytest.mark.parametrize('case',REFERENCE['cases'],ids=lambda c:c['id'])
def test_native_clock_and_legato_preservation(case):
    source=deepcopy(case['source']);p=checked(source)
    assert source==case['source']
    notes=p['tracks'][0]['notes'];evidence=p['fingerBendTimingEvidence']
    assert evidence and all(e['status']=='resolved' and e['overlap']['outgoingLegato'] for e in evidence)
    for e in evidence:
        link=e['overlap']['outgoingLegato']
        origin=next(n for n in notes if e['sourceId'] in n['source_ids'] and n['t']==e['start'])
        dest=next(n for n in notes if link['destinationSourceId'] in n['source_ids'] and n['t']==link['start'])
        assert origin['ln'] is True and dest[link['technique']] is True
        assert origin['t']+origin['sus']==pytest.approx(dest['t'])
        assert link['fret']==dest['f'] and len(origin['source_ids']) in (3,4)
    plain=deepcopy(source)
    for bar in plain['parts'][0]['measures']:
        for b in bar['voices'][0]['beats']:
            for n in b.get('notes',[]):n.pop('hp',None)
    plain_notes=checked(plain)['tracks'][0]['notes']
    assert [{k:v for k,v in n.items() if k not in ('ln','ho','po')} for n in notes]==plain_notes
    assert REFERENCE['referenceSha256']=='4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    for profile in case['profiles']:
        for native in profile['gestures']:
            e=next(e for e in evidence if e['sourceId']==native['sourceId'] and e['occurrence']==native['occurrence'])
            assert native['samples']
            for point in native['samples']:assert sample(e['curve'],point['t'])==pytest.approx(point['v'],abs=.023)


@pytest.mark.parametrize('fault',['changing','nearly-settled','intermediate-origin','incoming','tie-gap','destination-gap',
                                 'same-fret','slide-in','slide-out','whammy','palm-mute','let-ring','note-vibrato','beat-vibrato',
                                 'pinch','destination-bend','destination-slide','destination-mute','destination-staccato'])
def test_unqualified_interactions_remain_guarded(fault,tmp_path):
    d=document();part=d['parts'][0];bs=part['measures'][0]['voices'][0]['beats']
    if fault in ('changing','nearly-settled'):
        bs[0]['notes'][0]['bend']={'points':[{'position':0,'tone':0},{'position':60,'precisePosition':100 if fault=='changing' else 66.667,'tone':100}]}
    elif fault=='intermediate-origin':bs[2]['notes'][0].pop('hp');bs[1]['notes'][0]['hp']=True
    elif fault=='incoming':part['measures'].insert(0,measure(beat(fret=5,hp=True)))
    elif fault=='tie-gap':part['measures']=[measure(bs[0]),measure(*bs[1:])]
    elif fault=='destination-gap':part['measures']=[measure(*bs[:-1]),measure(bs[-1])]
    elif fault=='same-fret':bs[-1]['notes'][0]['fret']=7
    elif fault=='slide-in':bs[0]['notes'][0]['slide']='above'
    elif fault=='slide-out':bs[2]['notes'][0]['slide']='downwards'
    elif fault=='whammy':bs[0]['tremoloBar']={'points':[{'position':0,'tone':0},{'position':60,'tone':-50}]}
    elif fault=='palm-mute':bs[0]['palmMute']=True
    elif fault=='let-ring':bs[0]['letRing']=True
    elif fault=='note-vibrato':bs[1]['notes'][0]['leftHandVibrato']='wide'
    elif fault=='beat-vibrato':bs[1]['vibrato']=True
    elif fault=='pinch':bs[0]['notes'][0].update(harmonic='pinch',harmonicFret=12)
    elif fault=='destination-bend':bs[-1]['notes'][0]['bend']={'points':[{'position':0,'tone':0},{'position':60,'tone':100}]}
    elif fault=='destination-slide':bs[-1]['notes'][0]['slide']='above'
    elif fault=='destination-mute':bs[-1]['palmMute']=True
    elif fault=='destination-staccato':bs[-1]['notes'][0]['staccato']=True
    p=checked(d);e=p['fingerBendTimingEvidence'][0]
    assert e['status']=='deferred' and 'outgoingLegato' not in e['overlap']
    if fault in ('changing','nearly-settled'):
        assert e['reason']=='overlap-with-other-expression'
        assert e['overlap']['classification']=='other-expression'
    assert any(f['feature']=='note.bend_timing' for f in import_json(tmp_path,d)['compatibilityReport']['findings'])


def test_plain_bend_does_not_claim_legato_and_renaming_is_irrelevant():
    d=document();before=checked(d);d.update(title='Different title',songId=99999,revisionId=999)
    assert checked(d)['fingerBendTimingEvidence']==before['fingerBendTimingEvidence']
    d['parts'][0]['measures'][0]['voices'][0]['beats'][2]['notes'][0].pop('hp')
    e=checked(d)['fingerBendTimingEvidence'][0]
    assert e['status']=='resolved' and 'outgoingLegato' not in e['overlap']


@pytest.mark.parametrize('piecewise',[False,True])
@pytest.mark.parametrize('hybrid',[False,True])
def test_archive_contract_and_independent_mutation_rejection(tmp_path,piecewise,hybrid):
    from test_song_import_builder import inputs
    _,audio,_,job=inputs(tmp_path);p=import_json(tmp_path,document());path=tmp_path/'score.json'
    if hybrid:p=load_performance(path,composition_context=True)
    alignment={'status':'validated','offset':.25,'scale':1.}
    if piecewise:alignment.update(mapping='piecewise-linear',anchors=[{'score':0,'audio':.25},{'score':.5,'audio':.75},{'score':4,'audio':4.95}],tempos=[{'time':.25,'bpm':120},{'time':.75,'bpm':100}])
    options=normalize_options({'enabled':hybrid});sha=hashlib.sha256(path.read_bytes()).hexdigest()
    if hybrid:options.update(mainTrackId=choose_main(p,options,sha),sourceSha256=sha)
    recipe={'preservationContract':74,'scoreHash':sha,'audioHash':audio['hash'],**({'hybridLead':options} if hybrid else {})}
    old=tmp_path/'old';old.mkdir()
    with pytest.raises(ImportFailure,match='contract 74'):
        build_feedpak(p,audio,alignment,old,output_dir=tmp_path/'old-out',source_path=path,compatibility=p['compatibilityReport'],recipe={'preservationContract':73})
    built=build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=path,compatibility=p['compatibilityReport'],recipe=recipe,
        hybrid_lead={'enabled':hybrid,'mainTrackId':options.get('mainTrackId'),'options':options})
    archive=Path(built['stagingPath']);verify=lambda f:verify_import(path,f,alignment,hybrid_options=options if hybrid else None)
    assert verify(archive)['status']=='passed'
    with ZipFile(archive) as z:original={name:z.read(name) for name in z.namelist()}
    manifest=yaml.safe_load(original['manifest.yaml']);ep=manifest['song_import']['fingerBendTimingFile']
    assert json.loads(original[ep])['version']==14 and original[manifest['song_import']['sourceFile']]==path.read_bytes()
    if hybrid:assert any(a['name']=='Hybrid Lead' for a in manifest['arrangements'])
    for fault in ('bend','legato-origin','legato-destination','technique','attack','fret','duration','extra-attack',
                  'evidence-destination','evidence-time','evidence-policy','evidence-absent','version','contract'):
        files=dict(original);m=deepcopy(manifest);cp=m['arrangements'][0]['file'];chart=json.loads(files[cp]);n=chart['notes'][0];dest=chart['notes'][1];ev=json.loads(files[ep])
        if fault=='bend':n['bnv'][1]['t']/=3
        elif fault=='legato-origin':del n['ln']
        elif fault=='legato-destination':del dest['po']
        elif fault=='technique':dest.pop('po');dest['ho']=True
        elif fault=='attack':dest['t']+=.1
        elif fault=='fret':dest['f']+=1
        elif fault=='duration':n['sus']-=.1
        elif fault=='extra-attack':chart['notes'].append(deepcopy(n))
        elif fault=='evidence-destination':ev['gestures'][0]['overlap']['outgoingLegato']['destinationSourceId']='songsterr:0:0:0:0:0'
        elif fault=='evidence-time':ev['gestures'][0]['overlap']['outgoingLegato']['start']+=.1
        elif fault=='evidence-policy':ev['gestures'][0]['overlap']['outgoingLegato']['policy']='guessed-link'
        elif fault=='evidence-absent':del ev['gestures'][0]['overlap']['outgoingLegato']
        elif fault=='version':ev.update(version=13,policy='songsterr-finger-bend-timing-v13')
        else:m['song_import']['preservationContract']=73
        files[cp]=json.dumps(chart).encode();files[ep]=json.dumps(ev).encode();files['manifest.yaml']=yaml.safe_dump(m).encode()
        target=tmp_path/f'{fault}.feedpak'
        with ZipFile(target,'w') as z:
            for name,data in files.items():z.writestr(name,data)
        assert verify(target)['status']=='failed',fault
