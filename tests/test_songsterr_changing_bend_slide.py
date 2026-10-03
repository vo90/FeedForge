"""Changing bends and terminal direction cues keep independent source clocks."""
from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, measure, raw_score, import_json
from test_songsterr_bend_timing import checked, RISE, FALL
from test_songsterr_overlapping_bends import sample
from feedback_converter.song_import.builder import build_feedpak, _retime_note
from feedback_converter.song_import.verification import verify_import
from feedback_converter.song_import.terminal_sustains import trim_held_note
from feedback_converter.song_import.audio import ImportFailure

REFERENCE = json.loads((Path(__file__).parent/'fixtures/songsterr_changing_bend_slide_reference.json').read_text())


def source(direction='downwards', bend=None):
    return raw_score([measure(
        beat(fret=15, string=1, duration=(1,4), bend=deepcopy(bend or RISE)),
        beat(fret=15, string=1, duration=(3,4), tie=True, slide=direction))])


@pytest.mark.parametrize('case', REFERENCE['cases'], ids=lambda c:c['id'])
def test_independent_bend_matches_qualified_native_controls(case):
    assert REFERENCE['referenceSha256']=='4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    p=checked(case['source']);n=p['tracks'][0]['notes'][0]
    assert p['fingerBendTimingEvidence'][0]['status']=='resolved'
    for profile in case['profiles'].values():
        assert profile['unchangedWhenSlideRemoved'] and profile['sourceAttacks']==1
        for point in profile['samples']:
            assert sample(n['bnv'],point['t'])==pytest.approx(point['v'],abs=.04)
    control=deepcopy(case['source'])
    control['parts'][0]['measures'][0]['voices'][0]['beats'][-1]['notes'][0].pop('slide')
    assert n['bnv']==checked(control)['tracks'][0]['notes'][0]['bnv']
    assert len(p['tracks'][0]['notes'])==1 and 'sl' not in n and 'slu' not in n


@pytest.mark.parametrize('direction',['downwards','upwards'])
@pytest.mark.parametrize('bend',[RISE,FALL])
@pytest.mark.parametrize('bass',[False,True])
def test_changed_pitch_retains_fret_attack_duration_and_direction(direction,bend,bass):
    doc=source(direction,bend)
    if bass:doc['tracks'][0].update(instrumentId=33,tuning=[43,38,33,28])
    p=checked(doc);n=p['tracks'][0]['notes'][0];e=p['fingerBendTimingEvidence'][0]
    assert e['rule']=='bend-with-slide-out'
    assert e['terminalSlideOut']['pitchPolicy']=='independent-source-bend'
    assert n['f']==15 and n['t']==0 and n['sus']==2
    assert n['bnv']==[{'t':0.,'v':0. if bend==RISE else 2.},{'t':2.,'v':2. if bend==RISE else 0.}]
    assert n['slide_out_marks']==[{'direction':direction[:-5],'start':.5,'end':2.}]


def test_precise_point_just_after_segment_start_is_not_snapped_or_sped_up():
    doc=source(bend={'points':[{'position':0,'tone':0},
        {'position':15,'precisePosition':25.0001,'tone':100},{'position':60,'tone':100}]})
    n=checked(doc)['tracks'][0]['notes'][0]
    assert n['bnv'][1]['t']==pytest.approx(.500002,abs=1e-12)
    assert n['slide_out_marks'][0]['start']==.5


def test_repeated_tempo_changes_do_not_change_the_musical_bend_shape():
    doc=source();doc['parts'][0]['measures'][0].update(repeatStart=True,repeat=2)
    doc['parts'][0]['automations']['tempo'].append({'measure':0,'position':960,'bpm':60,'type':4})
    p=checked(doc)
    assert len(p['tracks'][0]['notes'])==2
    for n in p['tracks'][0]['notes']:
        assert n['bnv']==[{'t':0.,'v':0.},{'t':.5,'v':.5},{'t':3.5,'v':2.}]
        assert n['slide_out_marks']==[{'direction':'down','start':.5,'end':3.5}]


@pytest.mark.parametrize('context',['following_slide_in','competing_bend'])
def test_unqualified_surroundings_retain_existing_curve_and_warning(tmp_path,context):
    doc=source();bs=doc['parts'][0]['measures'][0]['voices'][0]['beats']
    if context=='following_slide_in':doc['parts'][0]['measures'].append(measure(beat(fret=17,string=1,slide='below')))
    else:bs[-1]['notes'][0]['bend']=deepcopy(FALL)
    p=checked(doc);e=p['fingerBendTimingEvidence'][0]
    assert e['status']=='deferred' and e['rule']=='retained-segment-timing'
    loaded=import_json(tmp_path,doc)
    assert any(f['feature']=='note.bend_timing' for f in loaded['compatibilityReport']['findings'])


def test_separate_voices_and_other_strings_do_not_create_false_context():
    doc=source();doc['parts'][0]['measures'][0]['voices'].append({'beats':[beat(fret=4,string=2)]})
    doc['parts'][0]['measures'].append(measure(beat(fret=17,string=2,slide='below')))
    assert checked(doc)['fingerBendTimingEvidence'][0]['rule']=='bend-with-slide-out'


def test_mapping_and_audio_end_guard_preserve_changing_pitch():
    n=checked(source())['tracks'][0]['notes'][0]
    mapping={'offset':1.,'scale':1.,'mapping':'piecewise-linear',
             'anchors':[{'score':0,'audio':1},{'score':1,'audio':2},{'score':2,'audio':4}]}
    mapped=_retime_note(n,mapping,5)
    assert mapped['bnv']==[{'t':0.,'v':0.},{'t':1.,'v':1.},{'t':3.,'v':2.}]
    assert mapped['slide_out_marks']==[{'direction':'down','start':.5,'end':3.}]
    with pytest.raises(ImportFailure,match='during a bend'):
        trim_held_note(mapped,3,allow_directional_slides=True)
    hold=checked(source(bend={'points':[{'position':0,'tone':0},{'position':30,'tone':100},{'position':60,'tone':100}]}))['tracks'][0]['notes'][0]
    cut,_=trim_held_note(hold,1.7,allow_directional_slides=True)
    assert cut['bnv'][-1]=={'t':1.7,'v':2.}
    assert cut['slide_out_marks'][0]['end']==1.7


@pytest.mark.parametrize('piecewise',[False,True])
def test_archive_verifies_source_clock_and_rejects_mutations(tmp_path,piecewise):
    from test_song_import_builder import inputs
    _,audio,_,job=inputs(tmp_path)
    p=import_json(tmp_path,source());path=tmp_path/'score.json'
    alignment={'status':'validated','offset':.25,'scale':1.}
    if piecewise:alignment.update(mapping='piecewise-linear',anchors=[{'score':0,'audio':.25},{'score':1,'audio':1.25},{'score':4,'audio':4.85}],tempos=[{'time':.25,'bpm':120},{'time':1.25,'bpm':100}])
    (tmp_path/'old-job').mkdir()
    with pytest.raises(ImportFailure,match='contract 63'):
        build_feedpak(p,audio,alignment,tmp_path/'old-job',output_dir=tmp_path/'old',source_path=path,
                      compatibility=p['compatibilityReport'],recipe={'preservationContract':62})
    out=build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=path,
                     compatibility=p['compatibilityReport'],recipe={'preservationContract':63})
    archive=Path(out['stagingPath']);assert verify_import(path,archive,alignment)['status']=='passed'
    with ZipFile(archive) as z:original={n:z.read(n) for n in z.namelist()}
    manifest=yaml.safe_load(original['manifest.yaml'])
    assert original[manifest['song_import']['sourceFile']]==path.read_bytes()
    for fault in ('faster','frozen','direction','attack','phase','policy','contract','version'):
        files=dict(original);m=deepcopy(manifest);ep=m['song_import']['fingerBendTimingFile'];e=json.loads(files[ep]);cn=m['arrangements'][0]['file'];chart=json.loads(files[cn]);n=chart['notes'][0]
        if fault=='faster':n['bnv'][-1]['t']/=4
        elif fault=='frozen':n['bnv'][-1]['v']=0
        elif fault=='direction':n['slide_out_marks'][0]['direction']='up'
        elif fault=='attack':chart['notes'].append(deepcopy(n))
        elif fault=='phase':e['gestures'][0]['terminalSlideOut']['bendPhase']='held'
        elif fault=='policy':del e['gestures'][0]['terminalSlideOut']['pitchPolicy']
        elif fault=='contract':m['song_import']['preservationContract']=62
        elif fault=='version':e.update(version=3,policy='songsterr-finger-bend-timing-v3')
        files[ep]=json.dumps(e).encode();files[cn]=json.dumps(chart).encode();files['manifest.yaml']=yaml.safe_dump(m).encode()
        mutated=tmp_path/f'{fault}.feedpak'
        with ZipFile(mutated,'w') as z:
            for name,data in files.items():z.writestr(name,data)
        assert verify_import(path,mutated,alignment)['status']=='failed',fault
