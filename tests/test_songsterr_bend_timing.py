"""Source-player timing answers, independent verification and package mutations."""
from copy import deepcopy
from fractions import Fraction as F
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, measure, raw_score, import_json
from test_songsterr_staccato_bends import check, stable
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.verification import verify_import

RISE = {'points': [{'position': 0, 'tone': 0}, {'position': 60, 'tone': 100}]}
FALL = {'points': [{'position': 0, 'tone': 100}, {'position': 60, 'tone': 0}]}
REFERENCE = json.loads((Path(__file__).parent/'fixtures/songsterr_finger_bend_reference.json').read_text())


@pytest.mark.parametrize('case',REFERENCE['cases'],ids=lambda row:row['id'])
def test_curve_matches_captured_native_pitch_events(case):
    p=checked(case['source'])
    curve=p['tracks'][0]['notes'][0].get('bnv',[])
    assert REFERENCE['referenceSha256']=='4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    for sample in case['samples']:
        t=sample['t']
        before=[point for point in curve if point['t']<=t]
        after=[point for point in curve if point['t']>t]
        if not curve: value=0.
        elif not before: value=curve[0]['v']
        elif not after: value=curve[-1]['v']
        else:
            a,b=before[-1],after[0]
            value=a['v']+(b['v']-a['v'])*(t-a['t'])/(b['t']-a['t'])
        assert value==pytest.approx(sample['v'],abs=.04),(case['id'],sample,value)


def source(a=1, b=1, initial=True, later=None):
    def duration(q):
        value=F(q,4)
        return value.numerator,value.denominator
    beats = [beat(fret=7, duration=duration(a), **({'bend':deepcopy(RISE)} if initial else {})),
             beat(fret=7, duration=duration(b), tie=True, **({'bend':deepcopy(later)} if later else {}))]
    if a+b<4:
        beats.append({'duration':list(duration(4-a-b)), 'notes':[{'rest':True}]})
    return raw_score([measure(*beats)])


def checked(doc):
    p=check(doc)
    v=expected(songsterr(doc), {'offset':0,'scale':1})
    assert stable(p.get('fingerBendTimingEvidence',[])) == stable(v['finger_bends'])
    return p


@pytest.mark.parametrize('fault',[None,'time','pitch','bool','identity','point_count','extra'])
def test_evidence_uses_numeric_tolerance_but_exact_structure(fault):
    from feedback_converter.song_import.verify_bend_timing import check_evidence
    from feedback_converter.song_import.verification import Check
    wanted={'sourceId':'songsterr:0:0:0:0:0','occurrence':1,'curve':[{'t':.4265625,'v':2.}]}
    actual=deepcopy(wanted)
    actual['curve'][0]['t']=294.684375-294.2578125
    if fault=='time': actual['curve'][0]['t']+=.00001
    if fault=='pitch': actual['curve'][0]['v']+=.00001
    if fault=='bool': actual['curve'][0]['t']=False
    if fault=='identity': actual['occurrence']=1.
    if fault=='point_count': actual['curve'].append(deepcopy(actual['curve'][0]))
    if fault=='extra': actual['other']=True
    check=Check();check_evidence(wanted,actual,check)
    assert bool(check.errors)==bool(fault)


@pytest.mark.parametrize('a,b',[(1,1),(1,3),(3,1)])
@pytest.mark.parametrize('program,tuning',[(29,[64,59,55,50,45,40]),(33,[43,38,33,28])])
def test_bend_uses_complete_tie_unless_next_segment_has_own_bend(a,b,program,tuning):
    doc=source(a,b)
    doc['tracks'][0].update(instrumentId=program,tuning=tuning)
    p=checked(doc)
    n=p['tracks'][0]['notes'][0]
    assert n['bnv']==[{'t':0.,'v':0.},{'t':(a+b)/2,'v':2.}]
    assert n['sus']==(a+b)/2 and len(n['source_ids'])==2
    n=checked(source(a,b,later=FALL))['tracks'][0]['notes'][0]
    assert n['bnv']==[{'t':0.,'v':0.},{'t':a/2,'v':2.},{'t':(a+b)/2,'v':0.}]


def test_later_prebend_keeps_original_pitch_until_authored_boundary():
    n=checked(source(initial=False,later=FALL))['tracks'][0]['notes'][0]
    assert n['bnv']==[{'t':0.,'v':0.},{'t':.5,'v':0.},{'t':.5,'v':2.},{'t':1.,'v':0.}]


@pytest.mark.parametrize('tied',[False,True])
def test_curve_progress_follows_musical_time_across_tempo_change(tied):
    doc=source(1,3) if tied else raw_score([measure(beat(fret=7,bend=deepcopy(RISE)))])
    doc['parts'][0]['automations']['tempo'].append({'measure':0,'position':960,'bpm':60,'type':4})
    n=checked(doc)['tracks'][0]['notes'][0]
    assert n['sus']==3.5
    assert n['bnv']==[{'t':0.,'v':0.},{'t':.5,'v':.5},{'t':3.5,'v':2.}]


def test_later_zero_hold_survives_existing_duplicate_source_point_normalization():
    later={'points':[{'position':0,'tone':100},{'position':30,'tone':0},
                     {'position':30,'tone':50},{'position':60,'tone':50}]}
    n=checked(source(initial=False,later=later))['tracks'][0]['notes'][0]
    assert n['bnv']==[{'t':0.,'v':0.},{'t':.5,'v':0.},{'t':.5,'v':2.},
                      {'t':.75,'v':1.},{'t':1.,'v':1.}]


def test_cross_bar_repeat_chord_and_separate_voices_keep_attacks_and_notation():
    doc=raw_score([measure(beat(fret=7,bend=deepcopy(RISE)),repeatStart=True),
                   measure(beat(fret=7,tie=True)),measure(beat(fret=9),repeat=2)])
    for m in doc['parts'][0]['measures']:
        n=m['voices'][0]['beats'][0]['notes'][0]
        m['voices'][0]['beats'][0]['notes'].append({'string':1,'fret':5,**({'tie':True} if n.get('tie') else {})})
        m['voices'].append({'beats':[beat(fret=12)]})
    p=checked(doc)
    assert len(p['tracks'])==2
    assert len(p['tracks'][0]['chords'])==4
    rows=p['fingerBendTimingEvidence']
    assert [r['occurrence'] for r in rows]==[1,4]
    assert all(r['curve']==[{'t':0.,'v':0.},{'t':4.,'v':2.}] for r in rows)
    assert all(r['trackId']==p['tracks'][0]['id'] for r in rows)


@pytest.mark.parametrize('kind',['overlap','slide','whammy','strum'])
def test_unqualified_combinations_are_explicitly_reported_without_new_guess(tmp_path,kind):
    doc=source(initial=False,later=FALL) if kind=='strum' else source()
    beats=doc['parts'][0]['measures'][0]['voices'][0]['beats']
    if kind=='overlap': beats[-1]=beat(fret=7,duration=(1,2),tie=True,bend=deepcopy(FALL))
    if kind=='slide': beats[0]['notes'][0]['slide']='upwards'
    if kind=='whammy': beats[0]['tremoloBar']=deepcopy(FALL)
    if kind=='strum':
        beats[0]['upStroke']=1
        beats[0]['notes'].append({'fret':9,'string':1})
    p=checked(doc)
    rows=p['fingerBendTimingEvidence']
    assert rows[0]['status']=='deferred'
    if kind in {'slide','whammy'}:
        assert rows[0]['curve']==[{'t':0.,'v':0.},{'t':.5,'v':2.}]
    loaded=import_json(tmp_path,doc)
    assert any(f['feature']=='note.bend_timing' for f in loaded['compatibilityReport']['findings'])
    from test_song_import_builder import inputs
    from feedback_converter.song_import.builder import build_feedpak
    _,audio,_,job=inputs(tmp_path)
    alignment={'status':'validated','offset':1,'scale':1}
    path=tmp_path/'score.json'
    result=build_feedpak(loaded,audio,alignment,job,output_dir=tmp_path/'out',source_path=path,
                        compatibility=loaded['compatibilityReport'],recipe={'preservationContract':60})
    archive=Path(result['stagingPath'])
    verified=verify_import(path,archive,alignment)
    assert verified['status']=='passed',verified
    with ZipFile(archive) as z: original={name:z.read(name) for name in z.namelist()}
    manifest=yaml.safe_load(original['manifest.yaml'])
    report_path=manifest['song_import']['compatibilityFile']
    for fault in ('missing','reason','category'):
        files=dict(original); report=json.loads(files[report_path])
        finding=next(r for r in report['findings'] if r['feature']=='note.bend_timing')
        if fault=='missing':
            report['findings'].remove(finding)
            report['findingCount']-=1
        elif fault=='reason': finding['value']['reason']='invented'
        else: finding['category']='game_limitation'
        files[report_path]=json.dumps(report).encode()
        altered=tmp_path/f'limitation-{fault}.feedpak'
        with ZipFile(altered,'w') as z:
            for name,data in files.items(): z.writestr(name,data)
        failed=verify_import(path,altered,alignment)
        assert failed['status']=='failed',fault
        assert any(e['code'].startswith('compatibility_') for e in failed['errors']),failed


@pytest.mark.parametrize('piecewise',[False,True])
@pytest.mark.parametrize('kind',['late','tie','tempo'])
def test_package_verifier_rejects_wrong_timing_and_missing_evidence(tmp_path,piecewise,kind):
    from test_song_import_builder import inputs
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.feedpak_validator import validate_feedpak
    _,audio,_,job=inputs(tmp_path)
    doc=source(initial=False,later=FALL) if kind=='late' else source(1,3)
    if kind=='tempo':
        doc['parts'][0]['automations']['tempo'].append({'measure':0,'position':960,'bpm':60,'type':4})
    p=import_json(tmp_path,doc)
    alignment={'status':'validated','offset':.25,'scale':1.25}
    if piecewise:
        alignment.update(mapping='piecewise-linear',anchors=[{'score':0,'audio':.25},{'score':.75,'audio':1.},{'score':4,'audio':4.9}],
                         tempos=[{'time':.25,'bpm':120},{'time':1.,'bpm':100}])
        if kind=='tempo':
            alignment['tempos']=[{'time':.25,'bpm':120},{'time':.75,'bpm':60},{'time':1.,'bpm':50}]
    path=tmp_path/'score.json'
    result=build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=path,
                        compatibility=p['compatibilityReport'],recipe={'preservationContract':60})
    archive=Path(result['stagingPath'])
    assert validate_feedpak(archive).ok
    verified=verify_import(path,archive,alignment)
    assert verified['status']=='passed',verified
    with ZipFile(archive) as z: original={n:z.read(n) for n in z.namelist()}
    manifest=yaml.safe_load(original['manifest.yaml'])
    assert original[manifest['song_import']['sourceFile']]==path.read_bytes()
    for fault in ['hold','double_speed','tempo','attack','fret','evidence','missing','source','contract','deferred']:
        files=dict(original)
        m=deepcopy(manifest)
        cpath=m['arrangements'][0]['file']
        chart=json.loads(files[cpath]); n=chart['notes'][0]
        if fault=='hold':
            if kind=='late': n['bnv']=n['bnv'][2:]
            elif kind=='tempo': del n['bnv'][1]
            else: n['bnv'][-1]['t']/=2
        if fault=='double_speed':
            for pt in n['bnv']: pt['t']/=2
        if fault=='tempo': n['bnv'][-1]['v']+=.25
        if fault=='attack': chart['notes'].append(deepcopy(n))
        if fault=='fret': n['f']+=1
        files[cpath]=json.dumps(chart).encode()
        epath=m['song_import']['fingerBendTimingFile']; evidence=json.loads(files[epath])
        if fault=='evidence': next(r for r in evidence['gestures'][0]['segments'] if 'gestureEnd' in r)['gestureEnd']+=1
        if fault=='source': evidence['sourceSha256']='0'*64
        if fault=='deferred': evidence['gestures'][0]['status']='deferred'
        files[epath]=json.dumps(evidence).encode()
        if fault=='missing': files.pop(epath)
        if fault=='contract': m['song_import']['preservationContract']=59
        files['manifest.yaml']=yaml.safe_dump(m).encode()
        changed=tmp_path/f'{fault}.feedpak'
        with ZipFile(changed,'w') as z:
            for name,data in files.items(): z.writestr(name,data)
        checked_package=verify_import(path,changed,alignment)
        assert checked_package['status']=='failed',(fault,checked_package)


def test_old_contract_cannot_publish_corrected_timing(tmp_path):
    from test_song_import_builder import inputs
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.audio import ImportFailure
    _,audio,_,job=inputs(tmp_path)
    p=import_json(tmp_path,source())
    with pytest.raises(ImportFailure,match='contract 60'):
        build_feedpak(p,audio,{'status':'validated','offset':0,'scale':1},job,output_dir=tmp_path/'out',
                      source_path=tmp_path/'score.json',recipe={'preservationContract':59},compatibility=p['compatibilityReport'])
