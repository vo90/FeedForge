from copy import deepcopy
from fractions import Fraction

import pytest

from test_song_import_score import beat, measure, raw_score
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.songsterr_whammy import source_whammy
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.verification import Check, _notes
from feedback_converter.song_import.builder import _retime_note
from feedback_converter.whammy import valid_whammy, trim_whammy


def bar(points, **extra):
    return {'tremoloBar':{'points':[{'position':p,'tone':v} for p,v in points]}, **extra}


def compare(source, alignment=None):
    alignment = alignment or {'offset':0,'scale':1}
    performance = render(parse(source))
    got = performance['tracks'][0]
    flat = list(got['notes']) + [{**n,'t':n.get('t',c['t'])} for c in got['chords'] for n in c['notes']]
    mapped = [_retime_note(n,alignment,100) for n in flat]
    want = expected(songsterr(source),alignment)['parts'][0]
    check = Check()
    _notes(want['notes'], [(n,'note') for n in mapped], check, want['source'],100)
    assert not check.errors, check.errors
    assert all(valid_whammy(n['whammy'],n['sus']) for n in mapped if 'whammy' in n)
    return mapped, got


@pytest.mark.parametrize('points,values', [
    ([(0,0),(0,0),(60,-400)],[0,-8]),
    ([(0,0),(60,-100)],[-2,-2]),
    ([(0,-25)],[-.5,-.5]),
    ([(0,0),(30,100),(30,-100),(60,0)],[0,-2,0]),
    ([(0,400),(30,-800),(60,0)],[8,-16,0])])
def test_source_curve_forms(points,values):
    assert [v for _,v in source_whammy(bar(points))['curve']] == values
    source=raw_score([measure({**beat(),**bar(points)})])
    original=deepcopy(source)
    notes,track=compare(source)
    assert [p['v'] for p in notes[0]['whammy']['segments'][0]['curve']] == values
    assert notes[0]['f']==3 and 'bnv' not in notes[0]
    assert 'bar' in track['notation']['measures'][0]['staves']['staff']['voices'][0]['beats'][0]
    assert source==original


@pytest.mark.parametrize('vibrato',['slight','wide'])
def test_qualitative_vibrato_does_not_invent_curve(vibrato):
    source=raw_score([measure({**beat(), 'vibratoWithTremoloBar':vibrato})])
    notes,_=compare(source)
    segment=notes[0]['whammy']['segments'][0]
    assert segment['curve']==[] and segment['vibrato']==vibrato
    assert not notes[0].get('vb')


def test_ties_keep_segments_and_repeats_get_distinct_groups():
    source=raw_score([measure(
        {**beat(duration=(1,2)),**bar([(0,0),(0,0),(60,-100)])},
        {**beat(duration=(1,2),tie=True),**bar([(0,-100),(30,0)])},
        repeatStart=True,repeat=2)])
    notes,_=compare(source)
    assert len(notes)==2
    assert all(len(n['whammy']['segments'])==2 for n in notes)
    assert notes[0]['whammy']['segments'][0]['group'] != notes[1]['whammy']['segments'][0]['group']


def test_chord_uses_shared_group_and_preserves_harmonic():
    b={**beat(fret=7,harmonic='artificial',harmonicFret=12),**bar([(0,-25)])}
    b['notes'].append({'string':1,'fret':0,'ghost':True})
    notes,_=compare(raw_score([measure(b)]))
    assert len(notes)==2
    assert len({n['whammy']['segments'][0]['group'] for n in notes})==1
    assert any(n.get('harmonic_target') for n in notes)
    assert any(n.get('ghost') for n in notes)


@pytest.mark.parametrize('vibrato',[None,'slight'])
def test_unmarked_ties_retain_source_performer_reset_without_new_attack(vibrato):
    continuation=beat(duration=(1,4),tie=True)
    if vibrato: continuation['vibratoWithTremoloBar']=vibrato
    source=raw_score([measure(
        {**beat(duration=(1,2)),**bar([(0,-50),(30,-100),(60,0)])},
        continuation, beat(duration=(1,4),tie=True))])
    notes,_=compare(source)
    assert len(notes)==1
    segments=notes[0]['whammy']['segments']
    assert len(segments)==3
    assert segments[0]['curve'][-1]['v']==0
    assert [p['v'] for s in segments[1:] for p in s['curve']]==[-1,-1,-1,-1]
    assert segments[1].get('vibrato')==vibrato and 'vibrato' not in segments[2]


def test_nonlinear_map_inserts_pitch_knot():
    source=raw_score([measure({**beat(),**bar([(0,0),(0,0),(60,-100)])})])
    alignment={'mapping':'piecewise-linear','anchors':[{'score':0,'audio':.3},{'score':1,'audio':1.3},{'score':2,'audio':3.3}]}
    notes,_=compare(source,alignment)
    points=notes[0]['whammy']['segments'][0]['curve']
    assert points==[{'t':0,'v':0},{'t':1,'v':-1},{'t':3,'v':-2}]


@pytest.mark.parametrize('corrupt', ['pitch','time','group','policy','missing','unknown'])
def test_independent_checker_detects_mutations(corrupt):
    source=raw_score([measure({**beat(),**bar([(0,0),(0,0),(60,-100)])})])
    notes,_=compare(source)
    wb=notes[0]['whammy']
    if corrupt=='pitch': wb['segments'][0]['curve'][-1]['v']=-3
    if corrupt=='time': wb['segments'][0]['end']-=.1
    if corrupt=='group': wb['segments'][0]['group']='wrong'
    if corrupt=='policy': wb['policy']='required'
    if corrupt=='missing': notes[0].pop('whammy')
    if corrupt=='unknown': wb['segments'][0]['made_up']=True
    want=expected(songsterr(source),{'offset':0,'scale':1})['parts'][0]
    check=Check(); _notes(want['notes'],[(n,'note') for n in notes],check,want['source'],100)
    assert check.errors


@pytest.mark.parametrize('change', [
    {'points':[]}, {'points':[{'position':0,'tone':False}]},
    {'points':[{'position':0,'tone':-801}]},
    {'points':[{'position':30,'tone':0},{'position':20,'tone':0}]},
    {'points':[{'position':0,'tone':0,'precisePosition':0},{'position':60,'tone':0}]},
    {'points':[{'position':0,'tone':0}],'extend':'yes'}])
def test_invalid_active_fields_still_fail(change):
    with pytest.raises(ValueError): source_whammy({'tremoloBar':change})
    with pytest.raises(ValueError): songsterr(raw_score([measure({**beat(),'tremoloBar':change})]))


def test_trim_only_constant_tail():
    moving,_=compare(raw_score([measure({**beat(),**bar([(0,0),(0,0),(60,-100)])})]))
    with pytest.raises(ValueError): trim_whammy(moving[0]['whammy'],1)
    held,_=compare(raw_score([measure({**beat(),**bar([(0,0),(20,-100),(60,-100)])})]))
    clipped=trim_whammy(held[0]['whammy'],1)
    assert clipped['segments'][0]['end']==1
    assert clipped['segments'][0]['curve'][-1]=={'t':1,'v':-2}


@pytest.mark.parametrize('kind',['curve','vibrato','harmonic','chord'])
def test_completed_archive_verifies(tmp_path,kind):
    import json
    from pathlib import Path
    from test_song_import_builder import inputs
    from feedback_converter.song_import import load_performance
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.verification import verify_import
    _,audio,_,job=inputs(tmp_path)
    b={**beat(),**bar([(0,0),(0,0),(60,-100)])}
    if kind=='vibrato': b={**beat(),'vibratoWithTremoloBar':'wide'}
    if kind=='harmonic': b['notes'][0].update(fret=7,harmonic='artificial',harmonicFret=12)
    if kind=='chord': b['notes'].append({'string':1,'fret':3})
    source=raw_score([measure(b)])
    path=tmp_path/'source.json';path.write_text(json.dumps(source),encoding='utf-8')
    performance=load_performance(path)
    alignment={'status':'validated','offset':0,'scale':1}
    built=build_feedpak(performance,audio,alignment,job,output_dir=tmp_path/'out',source_path=path,
        compatibility=performance['compatibilityReport'],recipe={'preservationContract':17})
    result=verify_import(path,Path(built['stagingPath']),alignment)
    assert result['status']=='passed',result
