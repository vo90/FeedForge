from copy import deepcopy
import pytest
from test_song_import_score import beat, measure, raw_score
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.builder import _retime_note
from feedback_converter.song_import.verification import Check, _scrape_marks
from feedback_converter.song_import.terminal_sustains import trim_held_note


def scrape(direction='down', **kw):
    return beat(dead=True, pickScrape=direction, **kw)


@pytest.mark.parametrize('direction', ['up', 'down'])
@pytest.mark.parametrize('fret', [None, 0, 19, 34])
def test_preserves_unpitched_source_and_independent_intervals(direction, fret):
    b = scrape(direction)
    if fret is None: b['notes'][0].pop('fret')
    else: b['notes'][0]['fret'] = fret
    src = raw_score([measure(b)])
    original = deepcopy(src)
    actual = render(parse(src))['tracks'][0]['notes'][0]
    checked = expected(songsterr(src), {'offset':0,'scale':1})['parts'][0]['notes'][0]['note']
    assert actual['pick_scrape_marks'] == [{'direction':direction,'start':0.,'end':2.}]
    assert actual['pick_scrape_marks'] == checked['pick_scrape_marks']
    assert actual['f'] == (127 if fret is None else fret) and actual['mt']
    assert src == original
    assert inspect_songsterr(src)['status'] != 'blocked'


def test_tied_continuation_and_direction_change_preserve_one_attack():
    src=raw_score([measure(scrape(duration=(1,4)),beat(duration=(1,4),dead=True,tie=True),
                          scrape('up',duration=(1,2),tie=True))])
    actual=render(parse(src))['tracks'][0]['notes']
    assert len(actual)==1
    marks=[{'direction':'down','start':0.,'end':1.},{'direction':'up','start':1.,'end':2.}]
    assert actual[0]['pick_scrape_marks']==marks
    assert expected(songsterr(src),{'offset':0,'scale':1})['parts'][0]['notes'][0]['note']['pick_scrape_marks']==marks


def test_mixed_chord_repeats_leave_pitched_member_unchanged():
    b=scrape(duration=(1,1)); b['notes'].append({'fret':7,'string':1})
    src=raw_score([measure(b,repeatStart=True,repeat=2)])
    chords=render(parse(src))['tracks'][0]['chords']
    assert [c['t'] for c in chords]==[0.,2.]
    for c in chords:
        assert sum('pick_scrape_marks' in n for n in c['notes'])==1
        n=next(n for n in c['notes'] if n['f']==7)
        assert not n.get('mt') and n['sus']==2


def test_piecewise_retime_and_verified_audio_cut():
    n={'t':0.,'s':1,'f':127,'mt':True,'sus':3.,'pick_scrape_marks':[
        {'direction':'down','start':0.,'end':1.}, {'direction':'up','start':1.,'end':3.}]}
    alignment={'mapping':'piecewise-linear','anchors':[{'score':0,'audio':1},{'score':1,'audio':2},{'score':3,'audio':6}]}
    mapped=_retime_note(n,alignment,10)
    assert mapped['pick_scrape_marks']==[{'direction':'down','start':0.,'end':1.},{'direction':'up','start':1.,'end':5.}]
    cut,detail=trim_held_note(mapped,4)
    assert detail and cut['sus']==3. and cut['pick_scrape_marks'][-1]['end']==3.
    assert mapped['pick_scrape_marks'][-1]['end']==5.
    late={'t':0.,'s':1,'f':127,'mt':True,'sus':3.,'pick_scrape_marks':[
        {'direction':'down','start':2.,'end':3.}]}
    cut,_=trim_held_note(late,1.)
    assert 'pick_scrape_marks' not in cut and cut['mt'] and cut['sus']==1.


@pytest.mark.parametrize('change', ['missing','reverse','time','extra','pitched'])
def test_checker_detects_lost_or_changed_scrape(change):
    wanted=[{'direction':'up','start':0.,'end':1.}]
    actual={'mt':True,'sus':1.,'pick_scrape_marks':deepcopy(wanted)}
    if change=='missing': actual.pop('pick_scrape_marks')
    if change=='reverse': actual['pick_scrape_marks'][0]['direction']='down'
    if change=='time': actual['pick_scrape_marks'][0]['end']=.5
    if change=='extra': actual['pick_scrape_marks']*=2
    if change=='pitched': actual['mt']=False
    check=Check();_scrape_marks(wanted,actual,check,'test')
    assert check.errors


@pytest.mark.parametrize('direction', [True, 1, 'sideways', {}])
def test_malformed_scrapes_stay_blocked(direction):
    with pytest.raises(ValueError): parse(raw_score([measure(scrape(direction))]))


def test_scrape_requires_unpitched_note():
    src=raw_score([measure(beat(pickScrape='down'))])
    assert inspect_songsterr(src)['status']=='blocked'
    with pytest.raises(ValueError): parse(src)


def test_staccato_ties_clip_once_after_tie_expansion():
    src=raw_score([measure(scrape(duration=(1,4),staccato=True),
        beat(duration=(1,4),dead=True,tie=True),scrape('up',duration=(1,2),tie=True))])
    actual=render(parse(src))['tracks'][0]['notes'][0]
    checked=expected(songsterr(src),{'offset':0,'scale':1})['parts'][0]['notes'][0]['note']
    assert actual['sus']==checked['sus']==1
    assert actual['pick_scrape_marks']==checked['pick_scrape_marks']==[{'direction':'down','start':0.,'end':1.}]


def test_scrapes_are_not_used_as_pitched_audio_alignment_evidence():
    import numpy as np
    from feedback_converter.song_import.alignment import _score_samples
    ordinary = [{'t':i, 's':0, 'f':i % 12, 'sus':.5} for i in range(36)]
    score = {'tracks':[{'tuning':[40], 'notes':ordinary}]}
    before = _score_samples(score)
    score['tracks'][0]['notes'] = ordinary + [{'t':i+.25, 's':0, 'f':34, 'mt':True, 'sus':.3,
        'pick_scrape_marks':[{'direction':'down','start':0,'end':.3}]} for i in range(36)]
    after = _score_samples(score)
    for a,b in zip(before,after): np.testing.assert_array_equal(a,b)


@pytest.mark.parametrize('fret', [3, 34])
def test_finished_archive_is_verified_against_source_and_lost_marks_fail(tmp_path, fret):
    import json,zipfile
    from pathlib import Path
    from test_song_import_builder import inputs
    from feedback_converter.song_import import load_performance
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.verification import verify_import
    _,audio,_,job=inputs(tmp_path)
    source=raw_score([measure(scrape('up',duration=(1,2),fret=fret),scrape('down',duration=(1,2),fret=fret))])
    source_path=tmp_path/'scrape-source.json';source_path.write_text(json.dumps(source),encoding='utf-8')
    performance=load_performance(source_path)
    alignment={'status':'validated','offset':0,'scale':1}
    built=build_feedpak(performance,audio,alignment,job,output_dir=tmp_path/'out',source_path=source_path,
        compatibility=performance['compatibilityReport'],recipe={'preservationContract':16})
    archive=Path(built['stagingPath'])
    assert verify_import(source_path,archive,alignment)['status']=='passed'
    with zipfile.ZipFile(archive) as z: files={name:z.read(name) for name in z.namelist()}
    chart_name=next(name for name in files if name.startswith('arrangements/') and name.endswith('.json') and 'notation' not in name)
    chart=json.loads(files[chart_name]);chart['notes'][0].pop('pick_scrape_marks');files[chart_name]=json.dumps(chart).encode()
    corrupt=tmp_path/'lost-scrape.feedpak'
    with zipfile.ZipFile(corrupt,'w') as z:
        for name,data in files.items():z.writestr(name,data)
    assert verify_import(source_path,corrupt,alignment)['status']=='failed'


def test_hidden_scrape_fret_is_only_valid_with_typed_dead_child_on_every_template_use():
    from feedback_converter.feedpak_semantics import validate_arrangement_semantics
    n={'s':0,'f':34,'mt':True,'sus':1,'pick_scrape_marks':[{'direction':'down','start':0,'end':1}]}
    chart={'templates':[{'frets':[34,-1,-1,-1,-1,-1]}],'chords':[{'t':0,'id':0,'notes':[n]}]}
    errors=[];validate_arrangement_semantics(chart,{},'test',errors.append)
    assert not errors
    for change in ('not_dead','missing','empty','reversed','outside','unordered','pitch','template_only'):
        changed=deepcopy(chart);child=changed['chords'][0]['notes'][0]
        if change=='not_dead':child['mt']=False
        if change=='missing':child.pop('pick_scrape_marks')
        if change=='empty':child['pick_scrape_marks']=[]
        if change=='reversed':child['pick_scrape_marks'][0]['end']=-1
        if change=='outside':child['pick_scrape_marks'][0]['end']=2
        if change=='unordered':child['pick_scrape_marks']*=2
        if change=='pitch':child['bn']=1
        if change=='template_only':changed['chords'].append({'t':1,'id':0})
        errors=[];validate_arrangement_semantics(changed,{},'test',errors.append)
        assert errors,change
