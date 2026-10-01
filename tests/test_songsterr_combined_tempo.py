"""Known-answer clocks from the public performer; no downloaded song data."""
from copy import deepcopy
from fractions import Fraction as F
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from test_song_import_score import beat, measure, raw_score


CASES = json.loads((Path(__file__).parent / 'fixtures/songsterr_combined_tempo.json').read_text())['cases']


def source(case=0):
    fixture = deepcopy(CASES[case]['input'])
    doc = raw_score([measure(*(beat(fret=3+i, duration=(1, 4)) for i in range(4))) for _ in fixture['measures']])
    doc['parts'][0]['automations'] = fixture['automations']
    return doc


def rates(events):
    return {(t['measure'], F(str(t.get('position', 0))) / 960):
            F(str(t['bpm'])) * F(4, t.get('type', 4)) * (F(3, 2) if t.get('dotted') else 1)
            for t in events}


def clocks(doc):
    score, ref = parse(doc), songsterr(doc)
    return ({(i, q): bpm for i, bar in enumerate(score.measures) for q, bpm in bar.tempos},
            {(i, q): bpm for i, bar in enumerate(ref.bars) for q, bpm in bar.tempos.items()})


@pytest.mark.parametrize('case', range(len(CASES)), ids=[c['input']['name'] for c in CASES])
def test_combined_clock_matches_unchanged_public_performer(case):
    doc = source(case); original = deepcopy(doc)
    want = rates(CASES[case]['result'])
    assert clocks(doc) == (want, want)
    assert doc == original


def test_hold_after_final_ramp_has_half_beat_duration_and_restores_next_bar():
    doc = source()
    doc['parts'][0]['automations'] = {'gradualTempo': True, 'tempo': [
        {'measure': 0, 'bpm': 58}, {'measure': 1, 'bpm': 52, 'linear': True}],
        'fermata': [{'measure': 1, 'position': 3360, 'type': 'medium', 'length': .6}]}
    want = {(0,F(0)):58, (0,F(1)):57, (0,F(2)):55, (0,F(3)):54,
            (1,F(0)):52, (1,F(7,2)):27, (2,F(0)):52}
    assert clocks(doc) == (want, want)
    result = render(parse(doc)); ref = expected(songsterr(doc), {'offset':0,'scale':1})
    assert result['duration'] == pytest.approx(ref['score_duration'])
    assert result['tracks'][0]['notes'][7]['sus'] == pytest.approx(30/52 + 30/27)
    assert len(result['tracks'][0]['notes']) == 16


@pytest.mark.parametrize('order', [False, True])
def test_adjacent_holds_override_restoration_independently_of_input_order(order):
    doc = source()
    holds = [{'measure':0,'position':960,'type':'medium','length':.6},
             {'measure':0,'position':1920,'type':'short','length':0}]
    doc['parts'][0]['automations']['fermata'] = list(reversed(holds)) if order else holds
    want = {(0,F(0)):60,(0,F(1)):31,(0,F(2)):48,(0,F(3)):60,
            (1,F(0)):72,(1,F(1)):84,(1,F(2)):96,(1,F(3)):108,(2,F(0)):120}
    assert clocks(doc) == (want,want)


@pytest.mark.parametrize('unit,expected_hold,restored', [(2,62,120),(8,F(31,2),30)])
def test_authored_tempo_unit_is_used_before_following_ramp(unit,expected_hold,restored):
    doc=source(); doc['parts'][0]['automations']['tempo'][0]['type']=unit
    a,b=clocks(doc)
    assert a==b
    assert a[0,F(2)]==expected_hold and a[0,F(3)]==restored


def test_changing_meter_and_repeated_passages_share_combined_clock():
    doc=source()
    part=doc['parts'][0]
    part['measures'][0]=measure(beat(duration=(3,4)), signature=[6,8], repeatStart=True)
    part['measures'][1]['repeat']=2
    part['automations']['fermata']=[{'measure':0,'position':2400,'type':'medium','length':.6}]
    # 6/8's last eighth is held, and restoration at bar two starts the ramp.
    a,b=clocks(doc); assert a==b
    assert a[0,F(5,2)]==31 and a[1,F(0)]==60
    assert [a[1,F(i)] for i in range(4)]==[60,75,90,105]
    result=render(parse(doc)); ref=expected(songsterr(doc),{'offset':0,'scale':1})
    assert result['duration']==pytest.approx(ref['score_duration'])
    notes=result['tracks'][0]['notes']
    assert len(notes)==18
    assert notes[5]['t']==pytest.approx(notes[10]['t']/2)


def test_disabled_gradual_tempo_still_performs_hold_without_interpolation():
    doc=source(); doc['parts'][0]['automations']['gradualTempo']=False
    want={(0,F(0)):60,(0,F(2)):31,(0,F(3)):60,(2,F(0)):120}
    assert clocks(doc)==(want,want)


@pytest.mark.parametrize('fault', ['overlap','midbar','missing_length','invalid_length','unknown_type'])
def test_uninterpreted_or_invalid_holds_are_still_rejected(fault):
    doc=source(); auto=doc['parts'][0]['automations']; hold=auto['fermata'][0]
    if fault=='overlap': auto['fermata'].append({**hold,'position':2400})
    elif fault=='midbar': auto['tempo'].append({'measure':0,'position':960,'bpm':90})
    elif fault=='missing_length': del hold['length']
    elif fault=='invalid_length': hold['length']=2
    else: hold['type']='unknown'
    for reader in (parse,songsterr):
        with pytest.raises((ValueError,KeyError)): reader(doc)


def test_conflicting_part_timing_still_blocks_the_import():
    doc=source(); doc['tracks'].append({**deepcopy(doc['tracks'][0]),'id':1})
    doc['parts'].append(deepcopy(doc['parts'][0]))
    doc['parts'][1]['automations']['fermata'][0]['length']=.1
    for reader in (parse,songsterr):
        with pytest.raises(ValueError,match='disagree|different'): reader(doc)


@pytest.mark.parametrize('tamper', ['tempo','attack','sustain'])
def test_built_package_retains_source_and_rejects_timing_corruption(tmp_path,tamper):
    from test_song_import_builder import inputs
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.compatibility import inspect_songsterr
    from feedback_converter.song_import.verification import verify_import
    doc=source(); performance=render(parse(doc)); _,audio,_,job=inputs(tmp_path)
    score_path=tmp_path/'score.json'; score_path.write_text(json.dumps(doc),encoding='utf-8')
    alignment={'status':'validated','offset':0,'scale':.5}
    built=build_feedpak(performance,audio,alignment,job,output_dir=tmp_path/'out',source_path=score_path,
                        compatibility=inspect_songsterr(doc),recipe={'preservationContract':18})
    assert verify_import(score_path,built['stagingPath'],alignment)['status']=='passed'
    with ZipFile(built['stagingPath']) as z: entries={n:z.read(n) for n in z.namelist()}
    assert entries['import/source.json']==score_path.read_bytes()
    target=next(n for n in entries if n.endswith('timeline.json')) if tamper=='tempo' else next(
        n for n in entries if n.startswith('arrangements/') and n.endswith('.json') and 'notation' not in n)
    data=json.loads(entries[target])
    if tamper=='tempo': data['tempos'][1]['bpm']+=1
    elif tamper=='attack': data['notes'][4]['t']+=.1
    else: data['notes'][2]['sus']-=.1
    entries[target]=json.dumps(data).encode()
    changed=tmp_path/'changed.feedpak'
    with ZipFile(changed,'w') as z:
        for n,v in entries.items(): z.writestr(n,v)
    report=verify_import(score_path,changed,alignment)
    assert report['status']=='failed'
    assert any(e['code']=={'tempo':'tempo_value','attack':'note_time','sustain':'note_sustain'}[tamper] for e in report['errors'])
