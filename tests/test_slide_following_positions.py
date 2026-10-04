"""Timed guidance follows real slide geometry without rewriting music."""
from bisect import bisect_right
from copy import deepcopy
import math

import pytest

from feedback_converter.chart_guidance import finalize, music_digest, digest
from feedback_converter.generated_hand_positions import generate_positions
from feedback_converter.verify_chart_guidance import validate


def chart(notes):
    return dict(tuning=[0] * 6, capo=0, notes=notes, chords=[], templates=[], anchors=[], handshapes=[])


def at(rows, time):
    return rows[bisect_right([a['time'] for a in rows], time) - 1]


def wire(f, logarithmic):
    raw = 330 * (1 - 2 ** (-f / 12))
    return (raw if f <= 12 else 165 + (raw - 165) * 1.1) if logarithmic else f * 255.75 / 24


def position(note, time, logarithmic):
    mid = lambda f: -2 if f == 0 else (wire(f - 1, logarithmic) + wire(f, logarithmic)) / 2
    key = 'sl' if note.get('sl', -1) >= 0 else 'slu'
    p = max(0, min(1, (time - note['t']) / note['sus']))
    w = math.sin(p * math.pi / 2) ** 3 if key == 'sl' else 1 - math.cos(p * math.pi / 2)
    return mid(note['f']) * (1 - w) + mid(note[key]) * w


@pytest.mark.parametrize('key', ['sl', 'slu'])
@pytest.mark.parametrize('start,target', [(13, 0), (24, 1), (1, 24), (20, 3), (3, 8)])
@pytest.mark.parametrize('duration', [.05, 5.34, 120])
def test_dense_geometry_coverage_in_both_fret_layouts(key, start, target, duration):
    n = dict(t=.1234567, s=3, f=start, sus=duration, **{key: target})
    data = chart([n])
    before = deepcopy(data)
    finalize(data)
    assert music_digest(data) == music_digest(before)
    assert data['anchors'][0]['width'] == 4
    assert len(data['anchors']) <= 49  # Cost depends on neck size, not duration/FPS.
    for i in range(1001):
        t = n['t'] + duration * i / 1001
        a = at(data['anchors'], t)
        for logarithmic in (False, True):
            low = -2 if a['fret'] == 1 else wire(a['fret'] - 1, logarithmic)
            high = wire(a['fret'] + a['width'] - 1, logarithmic)
            assert low - .02 <= position(n, t, logarithmic) <= high + .02
    assert validate(data) == []
    end = data['anchors'][-1]
    assert end['fret'] <= max(1, target) < end['fret'] + end['width']
    assert end['width'] == 4


def test_cirice_descends_from_existing_area_and_keeps_low_position_through_rest():
    data = chart([dict(t=266.7175, s=5, f=12, sus=.165625),
                  dict(t=267.38, s=3, f=13, sus=5.34, sl=0, bn=1, vb=True),
                  dict(t=272.72, s=3, f=0, sus=2.69, mt=True, ghost=True),
                  dict(t=295.74125, s=3, f=5, sus=1)])
    finalize(data)
    rows = [a for a in data['anchors'] if a['time'] < 290]
    assert rows[0]['fret'] == 12
    assert rows[-1]['fret'] == 1
    assert all(a['fret'] >= b['fret'] for a, b in zip(rows, rows[1:]))
    assert all(a['width'] == 4 for a in rows)
    assert at(data['anchors'], 290)['fret'] == 1
    assert validate(data) == []


def test_other_string_hold_stays_covered_then_releases():
    data = chart([dict(t=0, s=3, f=13, sus=5.34, sl=0), dict(t=0, s=2, f=15, sus=3)])
    finalize(data)
    for t in [0, 1, 2, 2.999]:
        a = at(data['anchors'], t)
        assert a['fret'] <= 15 < a['fret'] + a['width']
    assert at(data['anchors'], 3)['width'] == 4
    assert data['anchors'][-1]['fret'] == 1
    assert validate(data) == []


def test_next_pick_cuts_off_guidance_without_accelerating_or_rewriting_slide():
    source = dict(t=0, s=3, f=13, sus=10, sl=0)
    data = chart([source, dict(t=1, s=3, f=18, sus=1)])
    before = deepcopy(data)
    finalize(data)
    assert at(data['anchors'], .9)['fret'] == 13
    assert at(data['anchors'], 1)['fret'] <= 18 < at(data['anchors'], 1)['fret'] + 4
    assert data['anchors'][-1]['time'] == 1
    assert data['notes'] == before['notes']
    assert validate(data) == []


@pytest.mark.parametrize('gesture', [{'slide_out': 'down'}, {'slide_out_marks': [{'t': 1, 'd': 'down'}]},
                                    {'slide_in_marks': [{'t': 0, 'd': 'up'}]}])
def test_directional_cues_never_move_the_position(gesture):
    data = chart([dict(t=0, s=3, f=13, sus=5, **gesture)])
    assert generate_positions(data) == [dict(time=0., fret=13, width=4)]


def test_slide_precedence_matches_renderer():
    data = chart([dict(t=0, s=3, f=13, sus=5, sl=0, slu=24)])
    finalize(data)
    assert data['anchors'][-1]['fret'] == 1
    assert validate(data) == []


@pytest.mark.parametrize('extra', [{'f':3.2}, {'hm':True, 'hn':3.2}])
def test_fractional_or_harmonic_contacts_keep_conservative_coverage(extra):
    data = chart([dict(t=0, s=3, f=3, sus=5, sl=12) | extra])
    finalize(data)
    assert len(data['anchors']) == 1
    assert data['anchors'][0]['fret'] <= 3
    assert data['anchors'][0]['fret'] + data['anchors'][0]['width'] > 12
    assert validate(data) == []


def test_independent_verifier_rejects_stale_lane_even_with_fresh_hash():
    data = chart([dict(t=0, s=3, f=13, sus=5.34, sl=0)])
    finalize(data)
    data['anchors'] = [dict(time=0., fret=12, width=4)]
    proof = data['ext']['chartGuidance']
    proof['guidanceSha256'] = digest({k: data[k] for k in proof['fields']})
    proof['wideAnchorCount'] = 0
    assert any('active slide' in error for error in validate(data))


@pytest.mark.parametrize('policy', [None, 'chord-local-v1', 'open-preparation-v1', 'open-preparation-v2'])
def test_old_generated_slide_positions_regenerate_explicitly(policy):
    data = chart([dict(t=0, s=3, f=13, sus=5.34, sl=0)])
    finalize(data)
    data['anchors'] = [dict(time=0., fret=12, width=4)]
    proof = data['ext']['chartGuidance']
    proof.update(positionPolicy=policy, slidePolicy='known-corridor', wideAnchorCount=0)
    proof['guidanceSha256'] = digest({k: data[k] for k in proof['fields']})
    old = deepcopy(data)
    finalize(data)
    assert data == old
    finalize(data, regenerate=True)
    assert data['anchors'][-1]['fret'] == 1
    assert music_digest(data) == music_digest(old)
    assert validate(data) == []
