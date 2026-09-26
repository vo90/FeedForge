"""Known-answer musical timing and independent package/evidence checks."""
from copy import deepcopy
from fractions import Fraction as F
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, measure, raw_score, import_json
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.verification import Check, _notes, _chords, _flatten, verify_import

RISE = {'points': [{'position': 0, 'tone': 0}, {'position': 60, 'tone': 50}]}
HOLD = {'points': [{'position': 0, 'tone': 50}, {'position': 60, 'tone': 50}]}


def source(first=1, second=1, origin=True, continuation=False, bend=True, next_bend=True):
    def duration(q):
        f = F(q, 4)
        return f.numerator, f.denominator
    beats = [beat(fret=5, duration=duration(first), staccato=origin, **({'bend':deepcopy(RISE)} if bend else {})),
             beat(fret=5, duration=duration(second), tie=True, staccato=continuation,
                  **({'bend':deepcopy(HOLD)} if next_bend else {}))]
    if first + second < 4:
        beats.append({'duration': list(duration(4-first-second)), 'notes':[{'rest':True}]})
    return raw_score([measure(*beats)])


def stable(v):
    if type(v) is float: return round(v,6)
    if isinstance(v,list): return [stable(x) for x in v]
    if isinstance(v,dict): return {k:stable(x) for k,x in v.items()}
    return v


def check(doc):
    original = deepcopy(doc)
    p = render(parse(doc))
    v = expected(songsterr(doc), {'offset':0,'scale':1})
    assert stable(p.get('staccatoBendEvidence',[])) == stable(v['staccato_bends'])
    checks = Check()
    for t,ref in zip(p['tracks'],v['parts']):
        _notes(ref['notes'],_flatten(t),checks,ref['source'],p['duration'])
        _chords(ref['notes'],t,checks,ref['source'])
    assert not checks.errors, checks.errors
    assert doc == original
    return p


@pytest.mark.parametrize('program,tuning', [(29,[64,59,55,50,45,40]),(33,[43,38,33,28])])
@pytest.mark.parametrize('fret,string,prefix', [(5,0,0),(12,2,3)])
def test_general_tied_staccato_bend_not_a_song_specific_rule(program,tuning,fret,string,prefix):
    doc = source()
    doc.update(songId=8123,title='Arbitrary source',artist='Unrelated artist')
    doc['tracks'][0].update(instrumentId=program,tuning=tuning)
    for b in doc['parts'][0]['measures'][0]['voices'][0]['beats'][:2]:
        b['notes'][0].update(fret=fret,string=string,harmonic='pinch',harmonicFret=24)
    doc['parts'][0]['measures'][:0] = [measure(beat(fret=7)) for _ in range(prefix)]
    p = check(doc)
    n = p['tracks'][0]['notes'][-1]
    assert n['t'] == prefix*2 and n['sus'] == .5
    assert n['f'] == fret and n['s'] == len(tuning)-1-string and n['hp']
    assert n['bnv'] == [{'t':0.,'v':0.},{'t':.5,'v':1.}]
    assert len(n['source_ids']) == 2
    evidence = p['staccatoBendEvidence'][0]
    assert evidence['end'] - evidence['start'] == .5
    assert evidence['writtenEnd'] - evidence['start'] == 1
    assert evidence['segments'][1]['audible'] is False
    written = p['tracks'][0]['notation']['measures'][prefix]['staves']['staff']['voices'][0]['beats']
    assert written[1]['notes'][0]['tied']


@pytest.mark.parametrize('a,b,origin,continuation,next_bend,sustain,end_value', [
    (1,3,True,False,False,1.,1.),
    (3,1,True,False,True,1.,2/3),
    (1,1,False,True,True,1.,1.),
    (1,1,True,True,True,.5,1.),
])
def test_source_timing_cases(a,b,origin,continuation,next_bend,sustain,end_value):
    n = check(source(a,b,origin,continuation,next_bend=next_bend))['tracks'][0]['notes'][0]
    assert n['sus'] == sustain
    assert n['bnv'][-1]['v'] == pytest.approx(end_value)
    assert n['bn'] == pytest.approx(end_value)
    assert max(p['t'] for p in n['bnv']) <= sustain
    if origin: assert n['bnv'][-1]['t'] == sustain


def test_bend_starting_after_sound_ends_is_retained_only_as_source_evidence():
    p = check(source(bend=False))
    n = p['tracks'][0]['notes'][0]
    assert n['sus'] == .5 and 'bnv' not in n and 'bn' not in n
    assert p['staccatoBendEvidence'][0]['segments'][1]['bend']


def test_audible_later_bend_does_not_move_its_initial_pitch_to_the_attack():
    n = check(source(origin=False,continuation=True,bend=False))['tracks'][0]['notes'][0]
    assert n['bnv'] == [{'t':0.,'v':0.},{'t':.5,'v':0.},{'t':.5,'v':1.},{'t':.75,'v':1.}]


def test_separated_bend_controls_hold_pitch_between_them():
    doc = raw_score([measure(
        beat(fret=5,duration=(1,4),bend=deepcopy(RISE)),
        beat(fret=5,duration=(1,4),tie=True,staccato=True,bend=deepcopy(HOLD)),
        beat(fret=5,duration=(1,4),tie=True),
        beat(fret=5,duration=(1,4),tie=True,bend={'points':[{'position':0,'tone':100},{'position':60,'tone':100}]}))])
    n = check(doc)['tracks'][0]['notes'][0]
    assert n['sus'] == 2
    assert n['bnv'] == [{'t':0.,'v':0.},{'t':.5,'v':1.},{'t':.75,'v':1.},
                        {'t':1.5,'v':1.},{'t':1.5,'v':2.},{'t':2.,'v':2.}]


def test_cross_bar_repeat_and_chord_keep_one_attack_per_string_per_pass():
    doc = raw_score([measure(beat(fret=5,staccato=True,bend=deepcopy(RISE)),repeatStart=True),
                     measure(beat(fret=5,tie=True,bend=deepcopy(HOLD)),repeat=2)])
    for m in doc['parts'][0]['measures']:
        b = m['voices'][0]['beats'][0]
        b['notes'].append({'fret':9,'string':1,**({'tie':True} if b['notes'][0].get('tie') else {})})
    p = check(doc)
    assert len(p['tracks'][0]['chords']) == 2
    assert [r['occurrence'] for r in p['staccatoBendEvidence']] == [1,3]
    for c in p['tracks'][0]['chords']:
        assert sorted(n['sus'] for n in c['notes']) == [2,4]


def test_separate_voices_keep_evidence_attached_to_its_arrangement():
    doc = source()
    doc['parts'][0]['measures'][0]['voices'].append({'beats':[beat(fret=9)]})
    p = check(doc)
    assert len(p['tracks']) == 2
    assert p['staccatoBendEvidence'][0]['trackId'] == p['tracks'][0]['id']
    assert 'bnv' not in p['tracks'][1]['notes'][0]


@pytest.mark.parametrize('precise', [False,True])
def test_bend_release_shape_and_tempo_boundary(precise):
    doc = source()
    points = [{'position':0,'tone':50},{'position':30,'tone':100},{'position':60,'tone':0}]
    if precise:
        for p in points: p['precisePosition'] = p['position']*100/60
    doc['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['bend'] = {'points':points}
    doc['parts'][0]['automations']['tempo'].append({'measure':0,'position':240,'bpm':60,'type':4})
    n = check(doc)['tracks'][0]['notes'][0]
    assert n['sus'] == .875
    assert n['bnv'] == [{'t':0.,'v':1.},{'t':.125,'v':1.5},{'t':.375,'v':2.},{'t':.875,'v':0.}]
    assert n['bn'] == 2.


@pytest.mark.parametrize('fault', ['missing','fret','voice','string','rest','slide','whammy','overlap'])
def test_invalid_or_unverified_combinations_remain_rejected(fault):
    doc = source()
    beats = doc['parts'][0]['measures'][0]['voices'][0]['beats']
    n = beats[1]['notes'][0]
    if fault == 'missing': beats[0]['notes'][0]['tie'] = True
    if fault == 'fret': n['fret'] = 9
    if fault == 'voice': doc['parts'][0]['measures'][0]['voices'].append({'beats':[beats.pop(1)]})
    if fault == 'string': n['string'] = 1
    if fault == 'rest': beats.insert(1,{'duration':[1,8],'notes':[{'rest':True}]})
    if fault == 'slide': n['slide'] = 'upwards'
    if fault == 'whammy': beats[1]['tremoloBar'] = deepcopy(RISE)
    if fault == 'overlap':
        doc = source(1,1,next_bend=False)
        bs = doc['parts'][0]['measures'][0]['voices'][0]['beats']
        bs[-1] = beat(fret=5,duration=(1,1),tie=True,bend=deepcopy(HOLD))
        doc['parts'][0]['measures'][0]['signature'] = [6,4]
    with pytest.raises(ValueError): render(parse(doc))
    with pytest.raises(ValueError): expected(songsterr(doc),{'offset':0,'scale':1})


@pytest.mark.parametrize('piecewise', [False,True])
def test_package_source_evidence_alignment_and_tampering(tmp_path,piecewise):
    from test_song_import_builder import inputs
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.feedpak_validator import validate_feedpak
    _,audio,_,job = inputs(tmp_path)
    p = import_json(tmp_path,source())
    source_path = tmp_path/'score.json'
    alignment = {'status':'validated','offset':.25,'scale':1.25}
    if piecewise:
        alignment.update(mapping='piecewise-linear',anchors=[{'score':0,'audio':.25},{'score':.25,'audio':.75},{'score':2,'audio':2.5}],
                         tempos=[{'time':.25,'bpm':60},{'time':.75,'bpm':120}])
    result = build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=source_path,
                          compatibility=p['compatibilityReport'],recipe={'preservationContract':31})
    archive = Path(result['stagingPath'])
    assert validate_feedpak(archive).ok
    verified = verify_import(source_path,archive,alignment)
    assert verified['status']=='passed',verified
    assert 'staccato_bend_timing' in verified['scope']
    with ZipFile(archive) as z: original = {n:z.read(n) for n in z.namelist()}
    manifest = yaml.safe_load(original['manifest.yaml'])
    assert original[manifest['song_import']['sourceFile']] == source_path.read_bytes()
    for fault in ['missing','reference','source','id','time','boolean','curve','count','extra','contract',
                  'double_speed','sustain','peak','attack','fret','harmonic']:
        files = dict(original)
        evidence = json.loads(files['import/staccato-bends.json'])
        if fault=='source': evidence['sourceSha256']='0'*64
        if fault=='id': evidence['chains'][0]['segments'][1]['sourceId']='fabricated'
        if fault=='time': evidence['chains'][0]['end']+=.1
        if fault=='boolean': evidence['chains'][0]['segments'][0]['staccato']=1
        if fault=='curve': evidence['chains'][0]['curve'][-1]['v']+=1
        if fault=='count': evidence['chains']=[]
        if fault=='extra': evidence['extra']=True
        files['import/staccato-bends.json']=json.dumps(evidence).encode()
        if fault=='missing': files.pop('import/staccato-bends.json')
        m=deepcopy(manifest)
        if fault=='reference': m['song_import'].pop('staccatoBendsFile')
        if fault=='contract': m['song_import']['preservationContract']=30
        files['manifest.yaml']=yaml.safe_dump(m).encode()
        chart_path=m['arrangements'][0]['file']; chart=json.loads(files[chart_path]); n=chart['notes'][0]
        if fault=='double_speed':
            for point in n['bnv']: point['t']/=2
        if fault=='sustain': n['sus']*=2
        if fault=='peak': n['bn']=2
        if fault=='attack': chart['notes'].append(deepcopy(n))
        if fault=='fret': n['f']=9
        if fault=='harmonic': n['hp']=True
        files[chart_path]=json.dumps(chart).encode()
        changed=tmp_path/(fault+'.feedpak')
        with ZipFile(changed,'w') as z:
            for name,data in files.items(): z.writestr(name,data)
        check_result=verify_import(source_path,changed,alignment)
        assert check_result['status']=='failed',(fault,check_result)


def test_old_contract_cannot_publish_new_interpretation(tmp_path):
    from test_song_import_builder import inputs
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.audio import ImportFailure
    _,audio,_,job=inputs(tmp_path)
    p=import_json(tmp_path,source())
    with pytest.raises(ImportFailure,match='contract 31'):
        build_feedpak(p,audio,{'status':'validated','offset':0,'scale':1},job,
                      output_dir=tmp_path/'out',source_path=tmp_path/'score.json',
                      compatibility=p['compatibilityReport'],recipe={'preservationContract':30})
