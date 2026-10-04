"""Sequential bends keep their source pitch while the existing flourish ends them."""
from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, measure, raw_score, import_json
from test_songsterr_bend_timing import checked, RISE
from feedback_converter.song_import.verification import verify_import
from test_songsterr_overlapping_bends import sample

HOLD = {'points': [{'position': 0, 'tone': 100}, {'position': 60, 'tone': 100}]}
REFERENCE = json.loads((Path(__file__).parent/'fixtures/songsterr_bend_slide_out_reference.json').read_text())


@pytest.mark.parametrize('case',REFERENCE['cases'],ids=lambda c:c['id'])
def test_prefix_matches_qualified_native_samples_with_independent_slide_composition(case):
    assert REFERENCE['referenceSha256']=='4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    p=checked(case['source']);n=p['tracks'][0]['notes'][0];e=p['fingerBendTimingEvidence'][0]
    assert e['status']=='resolved'
    if e['rule']=='bend-hold-slide-out':
        for profile in case['profiles']:
            for point in profile['samples']:
                assert point['t'] < e['terminalSlideOut']['start']
                assert sample(n['bnv'],point['t'])==pytest.approx(point['v'],abs=case['nativeQuantizationBound'])
        assert sample(n['bnv'],1.99)==e['terminalSlideOut']['value']
    assert len(p['tracks'][0]['notes'])==1 and n['sus']==2


def source(direction='downwards'):
    return raw_score([measure(
        beat(fret=13, duration=(1,4), bend=deepcopy(RISE)),
        beat(fret=13, duration=(1,4), tie=True, bend=deepcopy(HOLD)),
        beat(fret=13, duration=(1,2), tie=True, slide=direction))])


@pytest.mark.parametrize('direction', ['downwards', 'upwards'])
@pytest.mark.parametrize('bass', [False, True])
def test_bend_hold_slide_out_retains_single_attack_pitch_and_source_interval(tmp_path,direction,bass):
    doc=source(direction)
    if bass:doc['tracks'][0].update(instrumentId=33,tuning=[43,38,33,28])
    p=checked(doc);n=p['tracks'][0]['notes'][0];e=p['fingerBendTimingEvidence'][0]
    assert e['status']=='resolved' and e['rule']=='bend-hold-slide-out'
    assert e['terminalSlideOut']=={'sourceId':'songsterr:0:0:0:2:0','direction':direction[:-5],
                                  'start':1.,'end':2.,'value':2.}
    assert n['bnv']==[{'t':0.,'v':0.},{'t':.5,'v':2.},{'t':1.,'v':2.}]
    assert n['slide_out_marks']==[{'direction':direction[:-5],'start':1.,'end':2.}]
    assert len(p['tracks'][0]['notes'])==1 and n['sus']==2 and n['f']==13
    assert 'sl' not in n and 'slu' not in n  # No invented target fret.
    loaded=import_json(tmp_path,doc)
    assert not any(f['feature']=='note.bend_timing' for f in loaded['compatibilityReport']['findings'])


@pytest.mark.parametrize('fault', ['overlapping_bend','early_slide','later_slide_in','whammy',
                                  'beat_vibrato','harmonic','mute','palm_mute','let_ring','hopo','open','strum'])
def test_unqualified_combinations_stay_explicitly_deferred(fault):
    doc=source();beats=doc['parts'][0]['measures'][0]['voices'][0]['beats']
    if fault=='overlapping_bend':
        del beats[1]['notes'][0]['bend']
        beats[2]['notes'][0]['bend']=deepcopy(RISE)
    elif fault=='early_slide':
        beats[1]['notes'][0]['slide']=beats[2]['notes'][0].pop('slide')
    elif fault=='later_slide_in':beats[1]['notes'][0]['slide']='below'
    elif fault=='whammy':beats[1]['tremoloBar']=deepcopy(RISE)
    elif fault=='beat_vibrato':beats[1]['vibrato']=True
    elif fault=='harmonic':beats[0]['notes'][0]['harmonic']='pinch'
    elif fault=='mute':beats[0]['notes'][0]['dead']=True
    elif fault=='palm_mute':beats[0]['palmMute']=True
    elif fault=='let_ring':beats[0]['letRing']=True
    elif fault=='hopo':
        beats[2]['notes'][0]['hp']=True
        doc['parts'][0]['measures'].append(measure(beat(fret=15)))
    elif fault=='open':
        for b in beats:b['notes'][0]['fret']=0
    elif fault=='strum':
        del beats[0]['notes'][0]['bend']
        beats[1]['notes'][0]['bend']=deepcopy(RISE)
        beats[0]['upStroke']=1
        beats[0]['notes'].append({'string':1,'fret':9})
    e=checked(doc)['fingerBendTimingEvidence'][0]
    assert e['status']=='deferred' and e['reason']=='mixed-pitch-or-displaced-attack'
    assert 'terminalSlideOut' not in e


def test_tempos_repeats_chords_and_voices_preserve_written_grouping():
    doc=source();bar=doc['parts'][0]['measures'][0];bar.update(repeatStart=True,repeat=2)
    doc['parts'][0]['automations']['tempo'].append({'measure':0,'position':960,'bpm':60,'type':4})
    for b in bar['voices'][0]['beats']:
        other=deepcopy(b['notes'][0]);other.update(string=1,fret=9);b['notes'].append(other)
    bar['voices'].append({'beats':[beat(fret=5)]})
    p=checked(doc)
    assert len(p['tracks'])==2 and len(p['tracks'][0]['chords'])==2
    rows=p['fingerBendTimingEvidence']
    assert len(rows)==4 and all(e['rule']=='bend-hold-slide-out' for e in rows)
    assert rows[0]['terminalSlideOut']['start']==1.5 and rows[0]['terminalSlideOut']['end']==3.5
    assert rows[0]['curve']==[{'t':0.,'v':0.},{'t':.5,'v':2.},{'t':1.5,'v':2.}]


@pytest.mark.parametrize('piecewise',[False,True])
def test_archive_retains_source_and_rejects_pitch_slide_or_evidence_mutation(tmp_path,piecewise):
    from test_song_import_builder import inputs
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.audio import ImportFailure
    _,audio,_,job=inputs(tmp_path)
    p=import_json(tmp_path,source());path=tmp_path/'score.json'
    alignment={'status':'validated','offset':.25,'scale':1.}
    if piecewise:
        alignment.update(mapping='piecewise-linear',anchors=[{'score':0,'audio':.25},{'score':.75,'audio':1.},{'score':4,'audio':4.9}],
                         tempos=[{'time':.25,'bpm':120},{'time':1.,'bpm':100}])
    with pytest.raises(ImportFailure,match='contract 61'):
        (tmp_path/'old-job').mkdir()
        build_feedpak(p,audio,alignment,tmp_path/'old-job',output_dir=tmp_path/'old',source_path=path,
                      compatibility=p['compatibilityReport'],recipe={'preservationContract':60})
    result=build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'new',source_path=path,
                        compatibility=p['compatibilityReport'],recipe={'preservationContract':61})
    archive=Path(result['stagingPath'])
    assert verify_import(path,archive,alignment)['status']=='passed'
    with ZipFile(archive) as z:original={n:z.read(n) for n in z.namelist()}
    manifest=yaml.safe_load(original['manifest.yaml'])
    assert original[manifest['song_import']['sourceFile']]==path.read_bytes()
    for fault in ('missing','direction','start','held_pitch','identity','bend','slide','attack','contract','version'):
        files=dict(original);m=deepcopy(manifest);ep=m['song_import']['fingerBendTimingFile']
        evidence=json.loads(files[ep]);e=evidence['gestures'][0]
        if fault=='missing':del e['terminalSlideOut']
        elif fault=='direction':e['terminalSlideOut']['direction']='up'
        elif fault=='start':e['terminalSlideOut']['start']-=.1
        elif fault=='held_pitch':e['terminalSlideOut']['value']=0.
        elif fault=='identity':e['terminalSlideOut']['sourceId']='songsterr:0:0:0:0:0'
        elif fault=='contract':m['song_import']['preservationContract']=60
        elif fault=='version':evidence.update(version=2,policy='songsterr-finger-bend-timing-v2')
        else:
            cp=m['arrangements'][0]['file'];chart=json.loads(files[cp]);n=chart['notes'][0]
            if fault=='bend':n['bnv'][-1]['v']=0.
            elif fault=='slide':n['slide_out_marks'][0]['direction']='up'
            elif fault=='attack':chart['notes'].append(deepcopy(n))
            files[cp]=json.dumps(chart).encode()
        files[ep]=json.dumps(evidence).encode();files['manifest.yaml']=yaml.safe_dump(m).encode()
        altered=tmp_path/f'{fault}.feedpak'
        with ZipFile(altered,'w') as z:
            for name,data in files.items():z.writestr(name,data)
        assert verify_import(path,altered,alignment)['status']=='failed',fault
