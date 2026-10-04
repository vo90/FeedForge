"""Initial approach cues and finger bends keep independent source timing."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, measure, import_json
from test_songsterr_bend_timing import checked, source, FALL
from test_songsterr_chord_bend_timing import notes
from test_songsterr_overlapping_bends import sample
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.hybrid_lead import choose_main, normalize_options
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.verification import verify_import

REFERENCE=json.loads((Path(__file__).parent/'fixtures/songsterr_incoming_slide_bend_reference.json').read_text())


def document(incoming='below', later=None):
    doc=source(1,3,later=later)
    bs=doc['parts'][0]['measures'][0]['voices'][0]['beats']
    bs[0]['notes'][0]['slide']=incoming
    bs[1]['notes'][0]['leftHandVibrato']='wide'
    return doc


def artificial_document(incoming='below', node=12):
    doc=document(incoming)
    for b in doc['parts'][0]['measures'][0]['voices'][0]['beats']:
        b['notes'][0].update(harmonic='artificial',harmonicFret=node)
    return doc


@pytest.mark.parametrize('incoming',['below','above'])
@pytest.mark.parametrize('node',[5,7,12])
def test_constant_artificial_harmonic_keeps_its_target_and_bend(incoming,node):
    doc=artificial_document(incoming,node);p=checked(doc)
    assert p['fingerBendTimingEvidence'][0]['status']=='resolved'
    n=notes(p)[0]
    assert n['harmonic_target']['kind']=='artificial'
    assert n['bnv']==[{'t':0.,'v':0.},{'t':2.,'v':2.}]
    del doc['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['slide']
    assert {k:v for k,v in n.items() if k!='slide_in_marks'}==notes(checked(doc))[0]


@pytest.mark.parametrize('case',REFERENCE['cases'],ids=lambda c:c['id'])
def test_normalized_native_controls(case):
    assert REFERENCE['referenceSha256']=='4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    p=checked(case['source'])
    for target in case['targets']:
        e=next(e for e in p['fingerBendTimingEvidence'] if e['sourceId']==target['sourceId'] and e['occurrence']==target['occurrence'])
        assert e['status']=='resolved' and e['initialSlideIn']['bendTiming']=='authored-tie'
        n=next(n for n in notes(p) if e['sourceId'] in n['source_ids'] and n['t']==e['start'])
        for profile in target['profiles']:
            for t,value,step in profile['samples']:
                lo,hi=max(0,t-step),t+step
                values=[sample(n['bnv'],lo),sample(n['bnv'],hi),*[p['v'] for p in n['bnv'] if lo<=p['t']<=hi]]
                assert min(values)-.023 <= value <= max(values)+.023


@pytest.mark.parametrize('incoming',['below','above'])
@pytest.mark.parametrize('program',[25,29,34])
@pytest.mark.parametrize('later',[None,FALL])
def test_full_tie_clock_preserves_attack_direction_and_vibrato(incoming,program,later):
    doc=document(incoming,later)
    for part in (doc['tracks'][0],doc['parts'][0]):
        part['instrumentId']=program
        if program==34:part['tuning']=[43,38,33,28]
    p=checked(doc);n=notes(p)[0]
    assert len(notes(p))==1 and (n['t'],n['f'],n['sus'])==(0,7,2)
    assert n['slide_in_marks']==[{'time':0.,'direction':'up' if incoming=='below' else 'down'}]
    assert n['vibrato_marks']==[{'start':.5,'end':2.,'intensity':'wide'}]
    assert n['bnv']==([{'t':0.,'v':0.},{'t':2.,'v':2.}] if later is None else
                      [{'t':0.,'v':0.},{'t':.5,'v':2.},{'t':2.,'v':0.}])
    control=deepcopy(doc);del control['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['slide']
    a={k:v for k,v in n.items() if k!='slide_in_marks'}
    assert a==notes(checked(control))[0]


@pytest.mark.parametrize('variant',['precise','late-bend','repeat-tempo','chord','other-voice','single-tempo'])
def test_general_source_contexts(variant):
    doc=document();bar=doc['parts'][0]['measures'][0];bs=bar['voices'][0]['beats']
    if variant=='precise':bs[0]['notes'][0]['bend']={'points':[{'position':9,'precisePosition':15,'tone':0},{'position':25,'precisePosition':42,'tone':50}]}
    elif variant=='late-bend':bs[1]['notes'][0]['bend']=bs[0]['notes'][0].pop('bend')
    elif variant=='chord':bs[0]['notes'].append({'fret':5,'string':1})
    elif variant=='other-voice':bar['voices'].append({'beats':[beat(fret=12)]})
    elif variant=='single-tempo':bs.pop();bs[0]['duration']=[1,1]
    if variant in ('repeat-tempo','single-tempo'):
        if variant=='repeat-tempo':bar.update(repeatStart=True,repeat=2)
        doc['parts'][0]['automations']['tempo'].append({'measure':0,'position':960,'bpm':60,'type':4})
    p=checked(doc)
    assert all(e['status']=='resolved' and e['initialSlideIn']['attackTiming']=='authored-note' for e in p['fingerBendTimingEvidence'])
    n=notes(p)[0]
    if variant=='precise':assert n['bnv']==[{'t':0.,'v':0.},{'t':.3,'v':0.},{'t':.84,'v':1.},{'t':2.,'v':1.}]
    if variant in ('repeat-tempo','single-tempo'):assert n['bnv']==[{'t':0.,'v':0.},{'t':.5,'v':.5},{'t':3.5,'v':2.}]


@pytest.mark.parametrize('extra',['later-cue','targeted','whammy','beat-vibrato','hopo','harmonic','changing-harmonic','late-harmonic','palm-mute','let-ring','overlap'])
def test_other_compositions_remain_guarded(extra,tmp_path):
    doc=document();bar=doc['parts'][0]['measures'][0];bs=bar['voices'][0]['beats']
    if extra=='later-cue':bs[1]['notes'][0]['slide']='above'
    elif extra=='targeted':bs[1]['notes'][0]['slide']='shift';doc['parts'][0]['measures'].append(measure(beat(fret=5)))
    elif extra=='whammy':bs[0]['tremoloBar']=deepcopy(FALL)
    elif extra=='beat-vibrato':bs[0]['wideVibrato']=True
    elif extra=='hopo':bs[1]['notes'][0]['hp']=True;doc['parts'][0]['measures'].append(measure(beat(fret=5)))
    elif extra=='harmonic':bs[0]['notes'][0].update(harmonic='pinch',harmonicFret=12)
    elif extra in ('changing-harmonic','late-harmonic'):
        if extra=='changing-harmonic':bs[0]['notes'][0].update(harmonic='artificial',harmonicFret=12)
        bs[1]['notes'][0].update(harmonic='artificial',harmonicFret=7)
    elif extra=='palm-mute':bs[0]['palmMute']=True
    elif extra=='let-ring':bs[0]['letRing']=True
    elif extra=='overlap':
        bs[1]['duration']=[1,4];bs.append(beat(fret=7,tie=True,duration=(1,2),bend=deepcopy(FALL)))
    p=checked(doc);e=p['fingerBendTimingEvidence'][0]
    assert e['status']==('resolved' if extra=='targeted' else 'deferred') and 'initialSlideIn' not in e
    if extra=='targeted':assert e['targetedSlide']
    loaded=import_json(tmp_path,doc)
    assert any(f['feature']=='note.bend_timing' for f in loaded['compatibilityReport']['findings'])==(extra!='targeted')


def test_source_slide_disables_strum_spreading_but_explicit_offsets_remain_guarded(monkeypatch):
    from fractions import Fraction
    from feedback_converter.song_import import bend_timing, verify_bend_timing
    doc=document();bs=doc['parts'][0]['measures'][0]['voices'][0]['beats']
    bs[1]['notes'][0]['bend']=bs[0]['notes'][0].pop('bend')
    bs[0]['notes'].append({'fret':5,'string':1});bs[0]['downStroke']=1
    assert checked(doc)['fingerBendTimingEvidence'][0]['status']=='resolved'
    # The Songsterr parser suppresses strum offsets on a slide. Exercise the
    # resolver boundary too: an explicit offset from any future source reader
    # must not silently qualify as an ordinary incoming cue.
    finish=bend_timing.finish;reconstruct=verify_bend_timing.reconstruct
    def displaced_output(output, articulation, *args, **kwargs):
        articulation['bend_segments'][0][0].attack_offset=Fraction(1,8)
        return finish(output, articulation, *args, **kwargs)
    def displaced_source(event, *args, **kwargs):
        event['bend_atoms'][0][0].attack_offset=Fraction(1,8)
        return reconstruct(event, *args, **kwargs)
    monkeypatch.setattr(bend_timing,'finish',displaced_output)
    monkeypatch.setattr(verify_bend_timing,'reconstruct',displaced_source)
    e=checked(document())['fingerBendTimingEvidence'][0]
    assert e['status']=='deferred' and 'initialSlideIn' not in e


@pytest.mark.parametrize('piecewise',[False,True])
@pytest.mark.parametrize('hybrid',[False,True])
@pytest.mark.parametrize('artificial',[False,True])
def test_package_and_source_mutations(tmp_path,piecewise,hybrid,artificial):
    from test_song_import_builder import inputs
    _,audio,_,job=inputs(tmp_path)
    p=import_json(tmp_path,artificial_document() if artificial else document());path=tmp_path/'score.json'
    if hybrid:p=load_performance(path,composition_context=True)
    alignment={'status':'validated','offset':.25,'scale':1.}
    if piecewise:alignment.update(mapping='piecewise-linear',anchors=[{'score':0,'audio':.25},{'score':.5,'audio':.75},{'score':4,'audio':4.95}],tempos=[{'time':.25,'bpm':120},{'time':.75,'bpm':100}])
    options=normalize_options({'enabled':hybrid});sha=hashlib.sha256(path.read_bytes()).hexdigest()
    if hybrid:options.update(mainTrackId=choose_main(p,options,sha),sourceSha256=sha)
    recipe={'preservationContract':67,'scoreHash':sha,'audioHash':audio['hash'],**({'hybridLead':options} if hybrid else {})}
    old=tmp_path/'old';old.mkdir()
    with pytest.raises(ImportFailure,match='contract 67'):
        build_feedpak(p,audio,alignment,old,output_dir=tmp_path/'old-out',source_path=path,compatibility=p['compatibilityReport'],recipe={'preservationContract':66})
    result=build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=path,
        compatibility=p['compatibilityReport'],recipe=recipe,
        hybrid_lead={'enabled':hybrid,'mainTrackId':options.get('mainTrackId'),'options':options})
    archive=Path(result['stagingPath']);verify=lambda file:verify_import(path,file,alignment,hybrid_options=options if hybrid else None)
    assert verify(archive)['status']=='passed'
    with ZipFile(archive) as z:original={n:z.read(n) for n in z.namelist()}
    m=yaml.safe_load(original['manifest.yaml']);ep=m['song_import']['fingerBendTimingFile']
    assert json.loads(original[ep])['version']==8 and original[m['song_import']['sourceFile']]==path.read_bytes()
    if hybrid:assert any(a['name']=='Hybrid Lead' for a in m['arrangements'])
    for fault in ('compressed','cue-absent','cue-direction','cue-time','attack','duration','fret','vibrato','evidence','version','contract'):
        files=dict(original);manifest=deepcopy(m);cp=manifest['arrangements'][0]['file']
        chart=json.loads(files[cp]);n=chart['notes'][0];ev=json.loads(files[ep])
        if fault=='compressed':n['bnv'][-1]['t']/=4
        elif fault=='cue-absent':del n['slide_in_marks']
        elif fault=='cue-direction':n['slide_in_marks'][0]['direction']='down'
        elif fault=='cue-time':n['slide_in_marks'][0]['time']+=.1
        elif fault=='attack':n['t']+=.1
        elif fault=='duration':n['sus']-=.1
        elif fault=='fret':n['f']+=1
        elif fault=='vibrato':del n['vibrato_marks']
        elif fault=='evidence':del ev['gestures'][0]['initialSlideIn']
        elif fault=='version':ev.update(version=7,policy='songsterr-finger-bend-timing-v7')
        else:manifest['song_import']['preservationContract']=66
        files[cp]=json.dumps(chart).encode();files[ep]=json.dumps(ev).encode();files['manifest.yaml']=yaml.safe_dump(manifest).encode()
        dest=tmp_path/f'{fault}.feedpak'
        with ZipFile(dest,'w') as z:
            for name,data in files.items():z.writestr(name,data)
        assert verify(dest)['status']=='failed',fault
