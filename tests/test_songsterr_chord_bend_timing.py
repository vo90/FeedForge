"""Authored chord clocks are independent of automatic synth strumming."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, measure, raw_score, import_json
from test_songsterr_bend_timing import checked, RISE, FALL
from test_songsterr_overlapping_bends import sample
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.verification import verify_import
from feedback_converter.song_import.hybrid_lead import normalize_options, choose_main
from feedback_converter.song_import.score import load_performance

REFERENCE = json.loads((Path(__file__).parent/'fixtures/songsterr_chord_bend_reference.json').read_text())


def source(direction='downwards', bend=None, both=False):
    a=beat(fret=9,string=1,duration=(1,4),bend=deepcopy(bend or RISE))
    a['notes'].append({'fret':8,'string':2,**({'bend':deepcopy(bend or RISE)} if both else {})})
    b=beat(fret=9,string=1,duration=(3,4),tie=True,slide=direction,leftHandVibrato='slight')
    if both:b['notes'].append({'fret':8,'string':2,'tie':True,'slide':direction})
    return raw_score([measure(a,b)])


def notes(performance):
    return [n for t in performance['tracks'] for n in
            [*t['notes'],*[dict(n,t=c['t']) for c in t['chords'] for n in c['notes']]]]


@pytest.mark.parametrize('case',REFERENCE['cases'],ids=lambda c:c['id'])
def test_chord_clock_matches_qualified_source_bend_reference(case):
    assert REFERENCE['referenceSha256']=='4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    p=checked(case['source'])
    for target in case['targets']:
        e=next(e for e in p['fingerBendTimingEvidence'] if e['sourceId']==target['sourceId'] and e['occurrence']==target['occurrence'])
        assert e['status']=='resolved'
        assert e['terminalSlideOut']['attackTiming']=='authored-chord'
        n=next(n for n in notes(p) if e['sourceId'] in n['source_ids'] and abs(n['t']-e['start'])<1e-7)
        for profile in target['profiles'].values():
            assert profile['pairedSlideRemovalMatched']
            for point in profile['samples']:
                lo=sample(n['bnv'],max(0,point['t']-profile['positionStepSeconds']-profile['tickSeconds']))
                hi=sample(n['bnv'],point['t'])
                quantum=profile['pitchQuantum']
                assert min(lo,hi)-quantum <= point['v'] <= max(lo,hi)+quantum


@pytest.mark.parametrize('direction',['downwards','upwards'])
@pytest.mark.parametrize('bend',[RISE,FALL])
@pytest.mark.parametrize('both',[False,True])
def test_each_chord_member_keeps_its_full_bend_clock(direction,bend,both):
    doc=source(direction,bend,both);p=checked(doc)
    assert len(p['tracks'][0]['chords'])==1 and len(p['tracks'][0]['chords'][0]['notes'])==2
    assert len(notes(p))==2
    for e in p['fingerBendTimingEvidence']:
        assert e['rule']=='bend-with-slide-out' and e['terminalSlideOut']['attackTiming']=='authored-chord'
    for n in notes(p):
        if not n.get('bnv'):continue
        assert n['bnv']==[{'t':0.,'v':0. if bend==RISE else 2.},{'t':2.,'v':2. if bend==RISE else 0.}]
        assert n['sus']==2 and n['t']==0
        assert n['slide_out_marks']==[{'direction':'down' if direction=='downwards' else 'up','start':.5,'end':2.}]
    # Removing only the direction cue must not change any authored timing.
    plain=deepcopy(doc)
    for n in plain['parts'][0]['measures'][0]['voices'][0]['beats'][1]['notes']:n.pop('slide')
    q=checked(plain)
    kept=lambda p:[{k:n.get(k) for k in ('t','s','f','sus','source_ids','bnv','vibrato_marks')} for n in notes(p)]
    assert kept(p)==kept(q)


def test_precise_controls_tempo_repeats_and_separate_voice():
    doc=source(bend={'points':[{'position':0,'precisePosition':0,'tone':0},
        {'position':30,'precisePosition':50,'tone':100},{'position':60,'precisePosition':100,'tone':0}]})
    doc['parts'][0]['measures'][0].update(repeatStart=True,repeat=2)
    doc['parts'][0]['automations']['tempo'].append({'measure':0,'position':960,'bpm':60,'type':4})
    p=checked(doc)
    assert len(p['fingerBendTimingEvidence'])==2
    assert all(e['terminalSlideOut']['attackTiming']=='authored-chord' for e in p['fingerBendTimingEvidence'])
    # A separate voice is not automatically a chord member of this voice.
    doc=source();bar=doc['parts'][0]['measures'][0]
    bar['voices'][0]['beats'][0]['notes'].pop()
    bar['voices'].append({'beats':[beat(fret=8,string=2)]})
    e=checked(doc)['fingerBendTimingEvidence'][0]
    assert e['status']=='resolved' and 'attackTiming' not in e['terminalSlideOut']


@pytest.mark.parametrize('context',['overlapping-bend','beat-vibrato','actual-strum'])
def test_compound_context_keeps_each_independent_warning(tmp_path,context):
    doc=source();bs=doc['parts'][0]['measures'][0]['voices'][0]['beats']
    if context=='overlapping-bend':
        bs[1]['duration']=[1,4]
        bs.insert(1,beat(fret=9,string=1,duration=(1,4),tie=True))
        bs.insert(2,beat(fret=9,string=1,duration=(1,4),tie=True,bend=deepcopy(FALL)))
    elif context=='beat-vibrato':bs[0]['wideVibrato']=True
    else:
        bs[0]['brushStroke']={'direction':'down','duration':120,'shift':100}
        bs[1]['notes'][0]['bend']=bs[0]['notes'][0].pop('bend')
        bs[1]['notes'][0].pop('slide')
    p=checked(doc)
    resolved=context=='beat-vibrato'
    assert all(e['status']==('resolved' if resolved else 'deferred') for e in p['fingerBendTimingEvidence'])
    if resolved:
        assert all('beatVibrato' not in e['terminalSlideOut'] for e in p['fingerBendTimingEvidence'])
        assert notes(p) == notes(checked(source()))  # Keep explicit tied note-wide vibrato.
    loaded=import_json(tmp_path,doc)
    findings=loaded['compatibilityReport']['findings']
    assert any(f['feature']=='note.bend_timing' for f in findings)==(not resolved)
    if resolved:assert any(f['feature']=='beat.wideVibrato' for f in findings)


@pytest.mark.parametrize('piecewise',[False,True])
@pytest.mark.parametrize('hybrid',[False,True])
def test_package_verifier_reconstructs_chord_bends_and_rejects_mutations(tmp_path,piecewise,hybrid):
    from test_song_import_builder import inputs
    _,audio,_,job=inputs(tmp_path)
    p=import_json(tmp_path,source(both=True));path=tmp_path/'score.json'
    if hybrid:p=load_performance(path,composition_context=True)
    alignment={'status':'validated','offset':.25,'scale':1.}
    if piecewise:alignment.update(mapping='piecewise-linear',anchors=[{'score':0,'audio':.25},{'score':1,'audio':1.25},{'score':4,'audio':4.85}],tempos=[{'time':.25,'bpm':120},{'time':1.25,'bpm':100}])
    options=normalize_options({'enabled':hybrid})
    if hybrid:
        source_hash=hashlib.sha256(path.read_bytes()).hexdigest()
        options.update(mainTrackId=choose_main(p,options,source_hash),sourceSha256=source_hash)
    recipe={'preservationContract':64,'scoreHash':hashlib.sha256(path.read_bytes()).hexdigest(),
            'audioHash':audio['hash'],**({'hybridLead':options} if hybrid else {})}
    old=tmp_path/'old';old.mkdir()
    with pytest.raises(ImportFailure,match='contract 64'):
        build_feedpak(p,audio,alignment,old,output_dir=tmp_path/'old-out',source_path=path,
            compatibility=p['compatibilityReport'],recipe={'preservationContract':63})
    result=build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=path,
        compatibility=p['compatibilityReport'],recipe=recipe,
        hybrid_lead={'enabled':hybrid,'mainTrackId':options.get('mainTrackId'),'options':options})
    archive=Path(result['stagingPath'])
    checked_result=verify_import(path,archive,alignment,hybrid_options=options if hybrid else None)
    assert checked_result['status']=='passed',checked_result['errors']
    with ZipFile(archive) as z:original={name:z.read(name) for name in z.namelist()}
    m=yaml.safe_load(original['manifest.yaml']);ep=m['song_import']['fingerBendTimingFile']
    assert json.loads(original[ep])['version']==5
    assert original[m['song_import']['sourceFile']]==path.read_bytes()
    if hybrid:assert any(a['name']=='Hybrid Lead' for a in m['arrangements'])
    for fault in ('compressed','staggered','fret','sustain','new-attack','missing-slide','direction','vibrato','evidence','version','contract'):
        files=dict(original);manifest=deepcopy(m);chartfile=manifest['arrangements'][0]['file']
        chart=json.loads(files[chartfile]);chord=chart['chords'][0];n=chord['notes'][0];e=json.loads(files[ep])
        if fault=='compressed':n['bnv'][-1]['t']/=4
        elif fault=='staggered':chord['t']+=.0046
        elif fault=='fret':n['f']+=1
        elif fault=='sustain':n['sus']-=.1
        elif fault=='new-attack':chart['chords'].append(deepcopy(chord))
        elif fault=='missing-slide':del n['slide_out_marks']
        elif fault=='direction':n['slide_out_marks'][0]['direction']='up'
        elif fault=='vibrato':n['vibrato_marks']=[{'start':0,'end':n['sus'],'intensity':'wide'}]
        elif fault=='evidence':del e['gestures'][0]['terminalSlideOut']['attackTiming']
        elif fault=='version':e.update(version=4,policy='songsterr-finger-bend-timing-v4')
        elif fault=='contract':manifest['song_import']['preservationContract']=63
        files[chartfile]=json.dumps(chart).encode();files[ep]=json.dumps(e).encode();files['manifest.yaml']=yaml.safe_dump(manifest).encode()
        dest=tmp_path/f'{fault}.feedpak'
        with ZipFile(dest,'w') as z:
            for name,data in files.items():z.writestr(name,data)
        assert verify_import(path,dest,alignment,hybrid_options=options if hybrid else None)['status']=='failed',fault
