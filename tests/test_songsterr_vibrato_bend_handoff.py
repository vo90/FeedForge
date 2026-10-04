"""Qualified bend/vibrato composition; independent source and native checks."""
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

REFERENCE = json.loads((Path(__file__).parent/'fixtures/songsterr_vibrato_handoff_reference.json').read_text())


def document(kind='slight', index=2):
    doc=source()
    doc['parts'][0]['measures'][0]['voices'][0]['beats'][index]['notes'][0]['leftHandVibrato']=kind
    return doc


@pytest.mark.parametrize('case',REFERENCE['cases'],ids=lambda c:c['id'])
def test_native_curves_and_separate_vibrato(case):
    assert REFERENCE['referenceSha256']=='4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    p=checked(case['source']);e=p['fingerBendTimingEvidence'][0];n=p['tracks'][0]['notes'][0]
    assert e['overlap']['vibratoTiming']=='independent-note-controls'
    for profile in case['profiles']:
        assert profile['pitchUnchangedByVibrato'] and profile['timingUnchangedByVibrato']
        for t,v,step in profile['samples']:
            a,b=sample(n['bnv'],max(0,t-step)),sample(n['bnv'],t+step)
            assert min(a,b)-.023 <= v <= max(a,b)+.023
    assert n['vibrato_marks']==case['vibratoMarks']


@pytest.mark.parametrize('kind',['slight','wide','legacy','legacy-wide'])
@pytest.mark.parametrize('index',[0,1,2])
@pytest.mark.parametrize('bass',[False,True])
def test_general_note_vibrato_keeps_attacks_and_full_bend_clock(kind,index,bass):
    doc=document(kind if kind in ('slight','wide') else 'slight',index)
    b=doc['parts'][0]['measures'][0]['voices'][0]['beats'][index]
    if kind.startswith('legacy'):
        del b['notes'][0]['leftHandVibrato'];b['notes'][0]['wideVibrato' if kind=='legacy-wide' else 'vibrato']=True
    if bass:doc['tracks'][0].update(instrumentId=33,tuning=[43,38,33,28])
    p=checked(doc);n=p['tracks'][0]['notes'][0];e=p['fingerBendTimingEvidence'][0]
    assert len(p['tracks'][0]['notes'])==1 and n['f']==7 and n['t']==0 and n['sus']==2
    assert e['status']=='resolved' and e['rule']=='settled-bend-handoff'
    assert n['bnv']==[{'t':0.,'v':0.},{'t':.5,'v':2.},{'t':1.,'v':2.},{'t':2.,'v':0.}]
    bounds=[(0,2),(.5,1),(1,2)][index]
    assert n['vibrato_marks']==[{'start':bounds[0],'end':bounds[1],'intensity':'wide' if kind in ('wide','legacy-wide') else 'slight'}]


@pytest.mark.parametrize('variant',['repeat-tempo','multiple-controls','intensity-change','different-title'])
def test_context_does_not_change_eligibility(variant):
    doc=document();bar=doc['parts'][0]['measures'][0];bs=bar['voices'][0]['beats']
    if variant=='repeat-tempo':
        bar.update(repeatStart=True,repeat=2)
        doc['parts'][0]['automations']['tempo'].append({'measure':0,'position':480,'bpm':60,'type':4})
    elif variant=='multiple-controls':
        bs[2]['duration']=[1,4];bs.append(beat(fret=7,tie=True,duration=(1,4),bend={'points':[{'position':0,'tone':0},{'position':60,'tone':50}]},leftHandVibrato='wide'))
    elif variant=='intensity-change':bs[0]['notes'][0]['leftHandVibrato']='wide'
    else:doc.update(title='No song-specific handling',songId='different',revisionId=999)
    p=checked(doc)
    assert all(e['status']=='resolved' for e in p['fingerBendTimingEvidence'])
    if variant=='repeat-tempo':
        assert len(p['tracks'][0]['notes'])==2
        for n in p['tracks'][0]['notes']:
            assert n['bnv']==[{'t':0.,'v':0.},{'t':.25,'v':1.},{'t':.75,'v':2.},{'t':1.75,'v':2.},{'t':3.75,'v':0.}]
            assert n['vibrato_marks']==[{'start':1.75,'end':3.75,'intensity':'slight'}]


@pytest.mark.parametrize('extra',['conflicting','almost-settled','beat-only','mixed-beat-only','unknown-harmonic','whammy','slide','incoming','hopo','palm-mute','let-ring'])
def test_unqualified_combinations_keep_existing_warning_and_curve(extra,tmp_path):
    doc=document();bs=doc['parts'][0]['measures'][0]['voices'][0]['beats']
    if extra in ('conflicting','almost-settled'):
        bs[0]['notes'][0]['bend']={'points':[{'position':0,'precisePosition':0,'tone':0},{'position':60,'precisePosition':100 if extra=='conflicting' else 50.001,'tone':100}]}
    elif extra=='beat-only':del bs[2]['notes'][0]['leftHandVibrato'];bs[2]['vibrato']=True
    elif extra=='mixed-beat-only':bs[1]['wideVibrato']=True
    # Validated pinch continuation now composes; an unknown target still cannot.
    elif extra=='unknown-harmonic':bs[0]['notes'][0]['harmonic']='pinch'
    elif extra=='whammy':bs[0]['tremoloBar']={'points':[{'position':0,'tone':0},{'position':60,'tone':-50}]}
    elif extra=='slide':bs[0]['notes'][0]['slide']='downwards'  # Nonterminal cue remains unqualified.
    elif extra=='incoming':bs[0]['notes'][0]['slide']='above'
    elif extra=='hopo':bs[-1]['notes'][0]['hp']=True;doc['parts'][0]['measures'].append(measure(beat(fret=5)))
    elif extra=='palm-mute':bs[0]['palmMute']=True
    elif extra=='let-ring':bs[0]['letRing']=True
    p=checked(doc);e=p['fingerBendTimingEvidence'][0]
    assert e['status']=='deferred' and 'vibratoTiming' not in e['overlap']
    loaded=import_json(tmp_path,doc)
    assert any(f['feature']=='note.bend_timing' for f in loaded['compatibilityReport']['findings'])


@pytest.mark.parametrize('piecewise',[False,True])
@pytest.mark.parametrize('hybrid',[False,True])
def test_package_source_evidence_and_mutation_detection(tmp_path,piecewise,hybrid):
    from test_song_import_builder import inputs
    _,audio,_,job=inputs(tmp_path)
    p=import_json(tmp_path,document());path=tmp_path/'score.json'
    if hybrid:p=load_performance(path,composition_context=True)
    alignment={'status':'validated','offset':.25,'scale':1.}
    if piecewise:alignment.update(mapping='piecewise-linear',anchors=[{'score':0,'audio':.25},{'score':.5,'audio':.75},{'score':4,'audio':4.95}],tempos=[{'time':.25,'bpm':120},{'time':.75,'bpm':100}])
    options=normalize_options({'enabled':hybrid});sha=hashlib.sha256(path.read_bytes()).hexdigest()
    if hybrid:options.update(mainTrackId=choose_main(p,options,sha),sourceSha256=sha)
    recipe={'preservationContract':66,'scoreHash':sha,'audioHash':audio['hash'],**({'hybridLead':options} if hybrid else {})}
    old=tmp_path/'old';old.mkdir()
    with pytest.raises(ImportFailure,match='contract 66'):
        build_feedpak(p,audio,alignment,old,output_dir=tmp_path/'old-out',source_path=path,
                      compatibility=p['compatibilityReport'],recipe={'preservationContract':65})
    result=build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=path,
        compatibility=p['compatibilityReport'],recipe=recipe,
        hybrid_lead={'enabled':hybrid,'mainTrackId':options.get('mainTrackId'),'options':options})
    archive=Path(result['stagingPath']);verify=lambda file:verify_import(path,file,alignment,hybrid_options=options if hybrid else None)
    assert verify(archive)['status']=='passed'
    with ZipFile(archive) as z:original={name:z.read(name) for name in z.namelist()}
    m=yaml.safe_load(original['manifest.yaml']);ep=m['song_import']['fingerBendTimingFile']
    assert json.loads(original[ep])['version']==7
    if hybrid:assert any(a['name']=='Hybrid Lead' for a in m['arrangements'])
    for fault in ('bend','vibrato-onset','vibrato-duration','vibrato-strength','vibrato-absent','attack','fret','duration','evidence','version','contract'):
        files=dict(original);manifest=deepcopy(m);cp=manifest['arrangements'][0]['file']
        chart=json.loads(files[cp]);n=chart['notes'][0];ev=json.loads(files[ep])
        if fault=='bend':n['bnv'][1]['t']/=4
        elif fault=='vibrato-onset':n['vibrato_marks'][0]['start']=0
        elif fault=='vibrato-duration':n['vibrato_marks'][0]['end']-=.2
        elif fault=='vibrato-strength':n['vibrato_marks'][0]['intensity']='wide'
        elif fault=='vibrato-absent':del n['vibrato_marks']
        elif fault=='attack':n['t']+=.1
        elif fault=='fret':n['f']+=1
        elif fault=='duration':n['sus']-=.1
        elif fault=='evidence':del ev['gestures'][0]['overlap']['vibratoTiming']
        elif fault=='version':ev.update(version=6,policy='songsterr-finger-bend-timing-v6')
        else:manifest['song_import']['preservationContract']=65
        files[cp]=json.dumps(chart).encode();files[ep]=json.dumps(ev).encode();files['manifest.yaml']=yaml.safe_dump(manifest).encode()
        dest=tmp_path/f'{fault}.feedpak'
        with ZipFile(dest,'w') as z:
            for name,data in files.items():z.writestr(name,data)
        assert verify(dest)['status']=='failed',fault
