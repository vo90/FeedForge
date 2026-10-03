"""General overlap policy, native known answers and independent package checks."""
from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, measure, raw_score, import_json
from test_songsterr_bend_timing import checked, RISE, FALL
from feedback_converter.song_import.verification import verify_import

SETTLED = {'points': [{'position': 0, 'tone': 0}, {'position': 15, 'tone': 100},
                      {'position': 60, 'tone': 100}]}


def source(first=None, last=None, tail=False):
    beats = [beat(fret=7, duration=(1,4), bend=deepcopy(first or SETTLED)),
             beat(fret=7, duration=(1,4), tie=True),
             beat(fret=7, duration=(1,4) if tail else (1,2), tie=True, bend=deepcopy(last or FALL))]
    if tail:
        beats.append(beat(fret=7, duration=(1,4), tie=True))
    return raw_score([measure(*beats)])


def sample(curve, time):
    before = [p for p in curve if p['t'] <= time]
    after = [p for p in curve if p['t'] > time]
    if not before:
        return curve[0]['v']
    if not after:
        return before[-1]['v']
    a,b = before[-1],after[0]
    return a['v']+(b['v']-a['v'])*(time-a['t'])/(b['t']-a['t'])


@pytest.mark.parametrize('case',json.loads((Path(__file__).parent/'fixtures/songsterr_overlap_reference.json').read_text())['cases'],ids=lambda r:r['id'])
def test_clear_handoffs_match_native_samples_conflicts_keep_written_curves(case):
    p=checked(case['source'])
    row=p['fingerBendTimingEvidence'][0]
    note=p['tracks'][0]['notes'][0]
    assert row['overlap']['classification']==case['classification']
    if case['classification']=='clear-handoff':
        assert row['status']=='resolved'
        for point in case['samples']:
            assert sample(note['bnv'],point['t'])==pytest.approx(point['v'],abs=case['nativeQuantizationBound'])
    else:
        assert row['status']=='deferred'
        assert row['curve']==case['writtenCurve']
    assert len(p['tracks'][0]['notes'])==1
    assert note['f']==7 and note['sus']==2


@pytest.mark.parametrize('bass',[False,True])
@pytest.mark.parametrize('tail',[False,True])
def test_settled_handoff_keeps_authored_release_and_latest_hold(bass,tail):
    doc=source(tail=tail)
    if bass:doc['tracks'][0].update(instrumentId=33,tuning=[43,38,33,28])
    doc.update(title='Any song',artist='Any artist',songId='1')
    p=checked(doc)
    row=p['fingerBendTimingEvidence'][0]
    assert row['rule']=='settled-bend-handoff'
    curve=p['tracks'][0]['notes'][0]['bnv']
    assert curve==[{'t':0.,'v':0.},{'t':.5,'v':2.},{'t':1.,'v':2.},
                   {'t':1.5 if tail else 2.,'v':0.}]
    assert sample(curve,1.9)==pytest.approx(0 if tail else .2)
    assert row['segments'][0]['gestureEnd']==2  # Retain original controller extent.
    assert row['overlap']['handoffs'][0]['start']==1


def test_change_one_rational_step_after_handoff_is_not_silently_accepted():
    near={'points':[{'position':0,'precisePosition':0,'tone':0},
                    {'position':30,'precisePosition':50.001,'tone':100},
                    {'position':60,'precisePosition':100,'tone':100}]}
    row=checked(source(first=near))['fingerBendTimingEvidence'][0]
    assert row['status']=='deferred'
    assert row['overlap']['classification']=='conflicting-controls'


@pytest.mark.parametrize('effect',[{'vibrato':True},{'harmonic':'pinch'}, {'letRing':True}, {'palmMute':True}])
def test_compound_expressions_are_not_promoted_by_flat_finger_bend_alone(effect):
    doc=source()
    if 'letRing' in effect or 'palmMute' in effect:
        doc['parts'][0]['measures'][0]['voices'][0]['beats'][0].update(effect)
    else:
        doc['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0].update(effect)
    row=checked(doc)['fingerBendTimingEvidence'][0]
    assert row['status']=='deferred'
    assert row['overlap']['classification']=='other-expression'


def test_tempo_knots_and_multiple_later_controls_keep_pitch_and_no_new_attacks():
    doc=source(tail=True)
    doc['parts'][0]['measures'][0]['voices'][0]['beats'][-1]['notes'][0]['bend']=deepcopy(RISE)
    doc['parts'][0]['automations']['tempo'].append({'measure':0,'position':480,'bpm':60,'type':4})
    p=checked(doc); n=p['tracks'][0]['notes'][0]
    assert len(p['tracks'][0]['notes'])==1 and n['sus']==3.75
    assert n['bnv']==[{'t':0.,'v':0.},{'t':.25,'v':1.},{'t':.75,'v':2.},
                      {'t':1.75,'v':2.},{'t':2.75,'v':0.},{'t':3.75,'v':2.}]


def test_repeat_chords_and_voices_are_independent():
    doc=source()
    bar=doc['parts'][0]['measures'][0];bar.update(repeatStart=True,repeat=2)
    for b in bar['voices'][0]['beats']:
        twin=deepcopy(b['notes'][0]);twin.update(string=1,fret=5);b['notes'].append(twin)
    bar['voices'].append({'beats':[beat(fret=10)]})
    p=checked(doc)
    assert len(p['tracks'])==2
    assert len(p['tracks'][0]['chords'])==2
    rows=p['fingerBendTimingEvidence']
    assert len(rows)==4 and all(r['rule']=='settled-bend-handoff' for r in rows)


def test_hopo_origin_on_hidden_continuation_keeps_written_fallback():
    doc=source()
    doc['parts'][0]['measures'][0]['voices'][0]['beats'][-1]['notes'][0]['hp']=True
    doc['parts'][0]['measures'].append(measure(beat(fret=9)))
    row=checked(doc)['fingerBendTimingEvidence'][0]
    assert row['overlap']['classification']=='other-expression'
    assert row['reason']=='overlap-with-other-expression'


@pytest.mark.parametrize('conflict',[False,True])
@pytest.mark.parametrize('piecewise',[False,True])
def test_packaged_curve_policy_and_source_cannot_be_falsified(tmp_path,conflict,piecewise):
    from test_song_import_builder import inputs
    from feedback_converter.song_import.builder import build_feedpak
    _,audio,_,job=inputs(tmp_path)
    doc=source(first=RISE if conflict else SETTLED)
    p=import_json(tmp_path,doc)
    alignment={'status':'validated','offset':.25,'scale':1.}
    if piecewise:
        alignment.update(mapping='piecewise-linear',anchors=[{'score':0,'audio':.25},{'score':.75,'audio':1.},{'score':4,'audio':4.9}],
                         tempos=[{'time':.25,'bpm':120},{'time':1.,'bpm':100}])
    result=build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=tmp_path/'score.json',
                        compatibility=p['compatibilityReport'],recipe={'preservationContract':61})
    archive=Path(result['stagingPath'])
    verified=verify_import(tmp_path/'score.json',archive,alignment)
    assert verified['status']=='passed',verified
    with ZipFile(archive) as z: original={n:z.read(n) for n in z.namelist()}
    manifest=yaml.safe_load(original['manifest.yaml'])
    assert original[manifest['song_import']['sourceFile']]==(tmp_path/'score.json').read_bytes()
    for fault in ('classification','handoff','identity','curve','policy','contract','warning'):
        if fault=='warning' and not conflict:continue
        files=dict(original);m=deepcopy(manifest)
        ep=m['song_import']['fingerBendTimingFile'];e=json.loads(files[ep]);r=e['gestures'][0]
        if fault=='classification':r['overlap']['classification']='clear-handoff' if conflict else 'conflicting-controls'
        elif fault=='handoff':r['overlap']['handoffs'][0]['start']+=.1
        elif fault=='identity':r['overlap']['handoffs'][0]['nextSourceId']='songsterr:0:0:0:0:0'
        elif fault=='policy':e['version']=1;e['policy']='songsterr-finger-bend-timing-v1'
        elif fault=='contract':m['song_import']['preservationContract']=59
        elif fault=='curve':
            cp=m['arrangements'][0]['file'];chart=json.loads(files[cp]);chart['notes'][0]['bnv'][-1]['v']+=.25;files[cp]=json.dumps(chart).encode()
        elif fault=='warning':
            rp=m['song_import']['compatibilityFile'];report=json.loads(files[rp]);report['findings']=[x for x in report['findings'] if x['feature']!='note.bend_timing'];report['findingCount']=len(report['findings']);files[rp]=json.dumps(report).encode()
        files[ep]=json.dumps(e).encode();files['manifest.yaml']=yaml.safe_dump(m).encode()
        changed=tmp_path/f'{fault}.feedpak'
        with ZipFile(changed,'w') as z:
            for name,data in files.items():z.writestr(name,data)
        assert verify_import(tmp_path/'score.json',changed,alignment)['status']=='failed',fault
