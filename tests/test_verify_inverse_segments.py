"""Numerical oracle for independently inverted timing segments."""
from fractions import Fraction as F
from types import SimpleNamespace as NS
import random
import pytest
from feedback_converter.song_import.verify_timeline import Clock, RecordingMap
from feedback_converter.song_import.verify_inverse import inverse_clock


def search(clock, recording, first, seconds):
    lo, hi = first, float(clock.quarters)
    for _ in range(45):
        mid = (lo + hi) / 2
        if float(recording.raw(clock.at(F(str(mid))))) < seconds:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


@pytest.mark.parametrize('kind', ['affine', 'piecewise', 'opening', 'collapsed', 'steep'])
def test_matches_previous_search_at_joins_tempos_repeats_and_bounds(kind):
    source = NS(bars=[NS(length=F(4), tempos={F(0):F(120), F(3,2):F(87)}),
                      NS(length=F(3), tempos={F(1):F(190)})])
    clock = Clock(source, [0,1,0,1])
    end = float(clock.at(clock.quarters))
    mapping = {'mapping':'piecewise-linear', 'anchors':[
        {'score':s,'audio':a} for s,a in [(0,2),(2,2.12),(4,9),(end,end+9)]]}
    first = 0
    if kind == 'affine':
        mapping = {'offset':2,'scale':.06}
        first = -.1
    elif kind == 'opening':
        first = -.1
        mapping.update(method='songsterr-video-points-v1', provenance={'openingStrum':{
            'version':1,'rule':'authored-opening-strum-first-interval','scoreStart':-.1}})
    elif kind == 'collapsed':
        first = 4
        mapping.update(method='songsterr-video-points-v1', anchors=[
            {'score':0,'audio':0}, {'score':float(clock.at(F(4))),'audio':0},
            {'score':end,'audio':9}], collapsedOpening={
            'version':1,'policy':'songsterr-collapsed-opening-v1','measureCount':1})
    elif kind == 'steep':
        mapping['anchors'] = [{'score':s,'audio':a} for s,a in
                              [(0,0),(2,10000),(4,10000.001),(end,10001)]]
    recording = RecordingMap(mapping)
    inverse = inverse_clock(clock, recording, first)
    lo = float(recording.raw(clock.at(F(str(first)))))
    hi = float(recording.raw(clock.at(clock.quarters)))
    rng = random.Random(102026)
    values = [lo-1, hi+1, *[rng.uniform(lo,hi) for _ in range(100)]]
    boundaries = [a for _,a in recording.anchors] if recording.piecewise else [F(lo),F(hi)]
    boundaries.extend(recording.raw(s) for s in clock.seconds if s >= clock.at(F(str(first))))
    for a in boundaries:
        values.extend(float(a)+d for d in [-1e-5,-1e-6,-1e-9,-1e-11,0,1e-11,1e-9,1e-6,1e-5])
    for t in values:
        assert inverse(t) == pytest.approx(search(clock,recording,first,t), abs=1e-8)
    assert inverse.cache_info().maxsize == 100000


@pytest.mark.parametrize('mapping', [
    {'offset':0,'scale':0},
    {'mapping':'piecewise-linear','anchors':[{'score':0,'audio':0},{'score':1,'audio':0}]},
    {'mapping':'piecewise-linear','anchors':[{'score':0,'audio':1},{'score':1,'audio':0}]},
])
def test_malformed_maps_still_rejected(mapping):
    with pytest.raises(ValueError):
        RecordingMap(mapping)
