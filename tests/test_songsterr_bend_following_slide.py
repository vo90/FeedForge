"""Following slide-in synthesis must not compress the previous source bend."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, measure, raw_score, import_json
from test_songsterr_bend_timing import checked, RISE, FALL
from test_songsterr_chord_bend_timing import notes
from test_songsterr_overlapping_bends import sample
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.hybrid_lead import normalize_options, choose_main
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.verification import verify_import

REFERENCE = json.loads((Path(__file__).parent/'fixtures/songsterr_bend_following_slide_reference.json').read_text())


def source(out='downwards', incoming='below', bend=None):
    return raw_score([measure(
        beat(fret=14, string=2, duration=(1,8), bend=deepcopy(bend or RISE)),
        beat(fret=14, string=2, duration=(3,8), tie=True, slide=out, leftHandVibrato='wide'),
        beat(fret=15, string=2, duration=(1,16), slide=incoming))])


@pytest.mark.parametrize('case', REFERENCE['cases'], ids=lambda c:c['id'])
def test_source_clock_matches_normalized_native_controls(case):
    assert REFERENCE['referenceSha256']=='4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    p=checked(case['source'])
    for target in case['targets']:
        e=next(e for e in p['fingerBendTimingEvidence'] if e['sourceId']==target['sourceId'] and e['occurrence']==target['occurrence'])
        assert e['status']=='resolved' and e['terminalSlideOut']['endTiming']=='authored-tie'
        n=next(n for n in notes(p) if e['sourceId'] in n['source_ids'] and abs(n['t']-e['start'])<1e-7)
        for profile in target['profiles'].values():
            assert profile['nextAttackUnchanged'] and profile['terminalCueIndependent']
            for variant in profile['curves'].values():
                unit=profile['tickSeconds']*variant['scale']
                for t,v in variant['samples']:
                    t*=variant['scale']
                    a=sample(n['bnv'],max(0,t-profile['positionStepSeconds']-unit))
                    b=sample(n['bnv'],t+unit)
                    assert min(a,b)-profile['pitchQuantum'] <= v <= max(a,b)+profile['pitchQuantum']


@pytest.mark.parametrize('out',['downwards','upwards'])
@pytest.mark.parametrize('incoming',['below','above'])
@pytest.mark.parametrize('bend',[RISE,FALL])
@pytest.mark.parametrize('program',[25,30,34])
def test_bend_and_both_cues_keep_authored_attacks(out,incoming,bend,program):
    doc=source(out,incoming,bend)
    for item in (doc['tracks'][0],doc['parts'][0]):
        item['instrumentId']=program
        if program==34:item['tuning']=[43,38,33,28]
    p=checked(doc);ns=notes(p)
    assert len(ns)==2
    a,b=ns
    assert (a['t'],a['f'],a['sus'],b['t'],b['f'])==(0,14,1,1,15)
    assert a['bnv']==[{'t':0.,'v':0. if bend==RISE else 2.},{'t':1.,'v':2. if bend==RISE else 0.}]
    assert a['slide_out_marks']==[{'direction':'down' if out=='downwards' else 'up','start':.25,'end':1.}]
    assert a['vibrato_marks']==[{'start':.25,'end':1.,'intensity':'wide'}]
    assert b['slide_in_marks']==[{'direction':'up' if incoming=='below' else 'down','time':0.}]
    # Removing only the following cue cannot move or compress this bend.
    control=deepcopy(doc);control['parts'][0]['measures'][0]['voices'][0]['beats'][2]['notes'][0].pop('slide')
    assert notes(checked(control))[0]==a


def test_precise_curve_spans_the_tie_instead_of_the_first_segment():
    doc=source(bend={'points':[{'position':9,'precisePosition':15,'tone':0},
                             {'position':25,'precisePosition':42,'tone':100}]})
    a=notes(checked(doc))[0]
    assert a['bnv']==[{'t':0.,'v':0.},{'t':.15,'v':0.},{'t':.42,'v':2.},{'t':1.,'v':2.}]


@pytest.mark.parametrize('context',['gap','other-string','other-voice','chord','repeat-tempo'])
def test_context_and_tempo_map_do_not_change_the_source_rule(context):
    doc=source();bar=doc['parts'][0]['measures'][0];bs=bar['voices'][0]['beats']
    if context=='gap':bs.insert(2,{'duration':[1,4],'rest':True,'notes':[{'rest':True}]})
    elif context=='other-string':bs[2]['notes'][0]['string']=1
    elif context=='other-voice':bar['voices'].append({'beats':[{'duration':[1,2],'rest':True,'notes':[{'rest':True}]},bs.pop()]})
    elif context=='chord':bs[0]['notes'].append({'fret':12,'string':1})
    else:
        bar.update(repeatStart=True,repeat=2)
        doc['parts'][0]['automations']['tempo'].append({'measure':0,'position':480,'bpm':60,'type':4})
    p=checked(doc)
    for e in p['fingerBendTimingEvidence']:
        assert e['status']=='resolved'
        assert ('endTiming' in e['terminalSlideOut'])==(context!='other-string')
        assert ('attackTiming' in e['terminalSlideOut'])==(context=='chord')
        if context=='repeat-tempo':
            a=next(n for n in notes(p) if e['sourceId'] in n['source_ids'] and n['t']==e['start'])
            assert a['bnv']==[{'t':0.,'v':0.},{'t':.25,'v':.5},{'t':1.75,'v':2.}]
            assert a['sus']==1.75
    if context=='repeat-tempo':assert len(p['fingerBendTimingEvidence'])==2


@pytest.mark.parametrize('extra',['incoming-on-later-tie','targeted-slide','competing-bend','whammy','beat-vibrato','authored-strum'])
def test_other_expressions_and_independent_beat_vibrato_are_distinguished(tmp_path,extra):
    doc=source();bs=doc['parts'][0]['measures'][0]['voices'][0]['beats']
    if extra=='incoming-on-later-tie':
        bs.insert(1,beat(fret=14,string=2,duration=(1,8),tie=True,slide='above'))
    elif extra=='targeted-slide':bs[1]['notes'][0]['slide']='shift'
    elif extra=='competing-bend':
        bs[1]['duration']=[1,4];bs[1]['notes'][0]['bend']=deepcopy(FALL)
        bs.insert(1,beat(fret=14,string=2,duration=(1,8),tie=True))
    elif extra=='whammy':bs[0]['tremoloBar']=deepcopy(RISE)
    elif extra=='beat-vibrato':bs[0]['wideVibrato']=True
    else:
        bs[0]['notes'].append({'fret':12,'string':3})
        bs[0]['brushStroke']={'direction':'down','duration':120,'shift':100}
        # A bend on the strummed beat disables spreading in the source player.
        # Put it on a later tie to exercise an actual displaced initial attack.
        bend=bs[0]['notes'][0].pop('bend')
        bs[1]['duration']=[1,4]
        bs.insert(1,beat(fret=14,string=2,duration=(1,8),tie=True,bend=bend))
    p=checked(doc)
    resolved=extra in ('beat-vibrato','targeted-slide')
    assert all(e['status']==('resolved' if resolved else 'deferred') for e in p['fingerBendTimingEvidence'])
    if extra=='beat-vibrato':assert all(e['terminalSlideOut']['beatVibrato'] for e in p['fingerBendTimingEvidence'])
    if extra=='targeted-slide':assert all(e['targetedSlide'] for e in p['fingerBendTimingEvidence'])
    loaded=import_json(tmp_path,doc)
    findings=loaded['compatibilityReport']['findings']
    assert any(f['feature']=='note.bend_timing' for f in findings)==(not resolved)
    if extra=='beat-vibrato':assert any(f['feature']=='beat.wideVibrato' for f in findings)


@pytest.mark.parametrize('piecewise',[False,True])
@pytest.mark.parametrize('hybrid',[False,True])
def test_packaged_source_reconstruction_and_mutations(tmp_path,piecewise,hybrid):
    from test_song_import_builder import inputs
    _,audio,_,job=inputs(tmp_path)
    p=import_json(tmp_path,source());path=tmp_path/'score.json'
    if hybrid:p=load_performance(path,composition_context=True)
    alignment={'status':'validated','offset':.25,'scale':1.}
    if piecewise:alignment.update(mapping='piecewise-linear',anchors=[{'score':0,'audio':.25},{'score':.5,'audio':.75},{'score':4,'audio':4.95}],tempos=[{'time':.25,'bpm':120},{'time':.75,'bpm':100}])
    options=normalize_options({'enabled':hybrid});sha=hashlib.sha256(path.read_bytes()).hexdigest()
    if hybrid:options.update(mainTrackId=choose_main(p,options,sha),sourceSha256=sha)
    recipe={'preservationContract':65,'scoreHash':sha,'audioHash':audio['hash'],**({'hybridLead':options} if hybrid else {})}
    old=tmp_path/'old';old.mkdir()
    with pytest.raises(ImportFailure,match='contract 65'):
        build_feedpak(p,audio,alignment,old,output_dir=tmp_path/'old-out',source_path=path,
                      compatibility=p['compatibilityReport'],recipe={'preservationContract':64})
    result=build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=path,
        compatibility=p['compatibilityReport'],recipe=recipe,
        hybrid_lead={'enabled':hybrid,'mainTrackId':options.get('mainTrackId'),'options':options})
    archive=Path(result['stagingPath'])
    verify=lambda file:verify_import(path,file,alignment,hybrid_options=options if hybrid else None)
    report=verify(archive);assert report['status']=='passed',report['errors']
    with ZipFile(archive) as z:original={name:z.read(name) for name in z.namelist()}
    m=yaml.safe_load(original['manifest.yaml']);ep=m['song_import']['fingerBendTimingFile']
    assert json.loads(original[ep])['version']==6
    assert original[m['song_import']['sourceFile']]==path.read_bytes()
    if hybrid:assert any(a['name']=='Hybrid Lead' for a in m['arrangements'])
    for fault in ('compressed','synth-truncated','next-attack','fret','extra-attack','incoming','outgoing','vibrato','evidence','version','contract'):
        files=dict(original);manifest=deepcopy(m);cp=manifest['arrangements'][0]['file']
        chart=json.loads(files[cp]);a,b=chart['notes'][:2];e=json.loads(files[ep])
        if fault=='compressed':a['bnv'][-1]['t']/=4
        elif fault=='synth-truncated':a['sus']-=.05
        elif fault=='next-attack':b['t']-=.05
        elif fault=='fret':b['f']-=1
        elif fault=='extra-attack':chart['notes'].append(deepcopy(b))
        elif fault=='incoming':del b['slide_in_marks']
        elif fault=='outgoing':del a['slide_out_marks']
        elif fault=='vibrato':del a['vibrato_marks']
        elif fault=='evidence':del e['gestures'][0]['terminalSlideOut']['endTiming']
        elif fault=='version':e.update(version=5,policy='songsterr-finger-bend-timing-v5')
        elif fault=='contract':manifest['song_import']['preservationContract']=64
        files[cp]=json.dumps(chart).encode();files[ep]=json.dumps(e).encode();files['manifest.yaml']=yaml.safe_dump(manifest).encode()
        dest=tmp_path/f'{fault}.feedpak'
        with ZipFile(dest,'w') as z:
            for name,data in files.items():z.writestr(name,data)
        assert verify(dest)['status']=='failed',fault
