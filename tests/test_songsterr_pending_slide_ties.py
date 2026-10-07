"""A hidden tied placeholder cannot consume or retarget a pending slide."""
from copy import deepcopy
from fractions import Fraction as F

import pytest

from test_song_import_score import beat, measure, raw_score
from test_songsterr_plain_tie_identity import flat
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected


def source(stored=0, kind='shift', vibrato=False):
    raw = raw_score([measure(beat(2, string=2, duration=(1, 16), slide=kind),
                             beat(stored, string=2, duration=(1, 8), tie=True),
                             beat(0, string=2, duration=(1, 8)))])
    raw['parts'][0].update(version=5, capo=4)
    raw['tracks'][0]['instrumentId'] = 24
    if vibrato:
        raw['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['vibrato'] = True
    return raw


def checked(raw):
    before = deepcopy(raw)
    model, independent = parse(raw), songsterr(raw)
    model_before, independent_before = deepcopy(model), deepcopy(independent)
    p = render(model)
    v = expected(independent, {'offset': 0, 'scale': 1})
    assert p.get('plainTieIdentityEvidence', []) == v['plain_tie_identities']
    for track, part in zip(p['tracks'], v['parts']):
        produced = sorted(flat(track), key=lambda n: (n['t'], n['s']))
        verified = sorted([entry['note'] for entry in part['notes']], key=lambda n: (n['t'], n['s']))
        def wire_equal(left, right):
            if isinstance(left, dict):
                assert left.keys() == right.keys()
                for key in left: wire_equal(left[key], right[key])
            elif isinstance(left, list):
                assert len(left) == len(right)
                for a, b in zip(left, right): wire_equal(a, b)
            elif isinstance(left, float):
                # Independent wire times round absolute endpoints to microseconds.
                assert left == pytest.approx(right, abs=1e-6, rel=0)
            else:
                assert left == right
        for left, right in zip(produced, verified):
            wire_equal({k: val for k, val in left.items() if k != 'source_ids'}, right)
        assert len(produced) == len(verified)
    assert raw == before and model == model_before and independent == independent_before
    return p, v


@pytest.mark.parametrize('stored', [0, 2, 9])
@pytest.mark.parametrize('kind', ['shift', 'legato'])
@pytest.mark.parametrize('program,tuning', [(24, [64, 59, 55, 50, 45, 40]),
                                           (29, [64, 59, 55, 50, 45, 40]), (33, [43, 38, 33, 28])])
@pytest.mark.parametrize('vibrato', [False, True])
def test_hidden_tie_preserves_slide_to_real_fresh_attack(stored, kind, program, tuning, vibrato):
    raw = source(stored, kind, vibrato)
    raw['tracks'][0].update(instrumentId=program, tuning=tuning)
    p, v = checked(raw)
    held, fresh = flat(p['tracks'][0])
    assert (held['t'], held['f'], held['sus'], held['sl']) == (0., 2, .375, 0)
    assert bool(held.get('ln')) is (kind == 'legato')
    assert (fresh['t'], fresh['f'], fresh['sus']) == (.375, 0, .25)
    assert not any(fresh.get(k) for k in ('ho', 'po', 'ln'))
    assert len(held['source_ids']) == 2
    assert bool(held.get('vb')) is vibrato
    if vibrato:
        assert held['vibrato_marks'] == [{'start': 0., 'end': .375, 'intensity': 'slight'}]
    assert v['parts'][0]['notation_beats'][1]['notes'][0]['fret'] == 2
    assert bool(p.get('plainTieIdentityEvidence')) is (stored != 2)
    if stored != 2:
        row = p['plainTieIdentityEvidence'][0]
        assert row['markerSourceId'] == row['originSourceId'] == held['source_ids'][0]
        assert row['authored'] == {'fret': stored, 'tie': True, 'slide': kind}
        assert row['used'] == {'fret': 2, 'pendingSlide': kind}


@pytest.mark.parametrize('kind', ['shift', 'legato'])
@pytest.mark.parametrize('version', [None, 5, 8])
@pytest.mark.parametrize('sequence', [[0, 9], [2, 0], [0, 2]])
def test_chains_and_real_post_release_gap(kind, version, sequence):
    raw = source(kind=kind, vibrato=True)
    if version is None: raw['parts'][0].pop('version')
    else: raw['parts'][0]['version'] = version
    beats = raw['parts'][0]['measures'][0]['voices'][0]['beats']
    beats[0]['vibrato'] = True  # duplicate beat marker is already note-owned
    beats[1:2] = [beat(f, string=2, duration=(1, 16), tie=True) for f in sequence]
    beats.insert(-1, {'duration': [1, 16], 'rest': True, 'notes': []})
    p, _ = checked(raw)
    held, target = flat(p['tracks'][0])
    assert held['sus'] == .375 and target['t'] == .5
    assert held['sl'] == 0 and len(held['source_ids']) == 3


def test_beat_only_vibrato_keeps_existing_note_ownership_policy():
    raw = source()
    raw['parts'][0]['measures'][0]['voices'][0]['beats'][0]['vibrato'] = True
    p, _ = checked(raw)
    assert not flat(p['tracks'][0])[0].get('vb')


@pytest.mark.parametrize('kind', ['shift', 'legato'])
def test_cross_bar_tempo_repeat_occurrences(kind):
    raw = raw_score([measure(beat(2, string=2, duration=(1, 1), slide=kind), repeatStart=True),
                     measure(beat(0, string=2, duration=(1, 4), tie=True),
                             beat(0, string=2, duration=(3, 4)), repeat=2)])
    raw['parts'][0]['automations']['tempo'].append({'measure':1, 'position':0, 'bpm':60})
    p, v = checked(raw)
    rows = p['plainTieIdentityEvidence']
    assert len(rows) == 2 and [r['occurrence'] for r in rows] == [2, 4]
    assert [r['markerOccurrence'] for r in rows] == [1, 3]
    assert rows[1]['start'] > rows[0]['end']


@pytest.mark.parametrize('fault', ['missing_target', 'wrong_string', 'origin_bend', 'tie_bend',
    'origin_harmonic', 'tie_harmonic', 'origin_hopo', 'tie_hopo', 'tie_slide', 'tie_vibrato',
    'origin_wide', 'staccato', 'muted', 'tie_not_literal', 'rest',
    'equal_tie_controller_after_normalization', 'marker_on_tie', 'incoming_legato', 'incoming_hopo'])
def test_unsupported_compounds_are_not_silently_normalized(fault):
    raw = source()
    beats = raw['parts'][0]['measures'][0]['voices'][0]['beats']
    a, b = beats[0]['notes'][0], beats[1]['notes'][0]
    if fault == 'missing_target': beats.pop()
    if fault == 'wrong_string': b['string'] = 1
    if fault in ('origin_bend', 'tie_bend'):
        (a if fault.startswith('origin') else b)['bend'] = {'points': [{'position': 0, 'tone': 0}, {'position': 60, 'tone': 100}]}
    if fault in ('origin_harmonic', 'tie_harmonic'): (a if fault.startswith('origin') else b)['harmonic'] = 'natural'
    if fault in ('origin_hopo', 'tie_hopo'): (a if fault.startswith('origin') else b)['hp'] = True
    if fault == 'tie_slide': b['slide'] = 'shift'
    if fault == 'tie_vibrato': b['vibrato'] = True
    if fault == 'origin_wide': a['wideVibrato'] = True
    if fault == 'staccato': b['staccato'] = True
    if fault == 'muted': a['dead'] = True
    if fault == 'tie_not_literal': b['tie'] = 1
    if fault == 'rest': beats.insert(1, {'duration': [1, 16], 'rest': True, 'notes': []})
    if fault == 'equal_tie_controller_after_normalization': beats.insert(2, beat(2, string=2, duration=(1, 16), tie=True, vibrato=True))
    if fault == 'marker_on_tie':
        a.pop('slide');b['fret'] = 2;b['slide'] = 'shift'
        beats.insert(2, beat(0, string=2, duration=(1, 16), tie=True))
    if fault.startswith('incoming_'):
        beats.insert(0, beat(1, string=2, duration=(1, 16), **({'slide':'legato'} if fault.endswith('legato') else {'hp':True})))
    with pytest.raises(ValueError): expected(songsterr(raw), {'offset': 0, 'scale': 1})
    with pytest.raises(ValueError): render(parse(raw))


@pytest.mark.parametrize('fault', ['gap', 'overlap', 'duplicate_origin', 'duplicate_tie', 'source_marker', 'source_tie'])
def test_model_and_raw_identity_guards(fault):
    raw = source()
    model, independent = parse(raw), songsterr(raw)
    if fault in ('gap', 'overlap'):
        offset = F(1, 100) if fault == 'gap' else -F(1, 100)
        model.tracks[0].bars[0][1].position += offset
        independent.parts[0].bars[0][1].q += offset
    if fault.startswith('duplicate'):
        i = 0 if fault.endswith('origin') else 1
        model.tracks[0].bars[0].append(deepcopy(model.tracks[0].bars[0][i]))
        independent.parts[0].bars[0].append(deepcopy(independent.parts[0].bars[0][i]))
    if fault == 'source_marker':
        model.source_document['document']['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['slide'] = 'above'
        independent.parts[0].bars[0][0].explicit_slide = 'above'
    if fault == 'source_tie':
        model.source_document['document']['parts'][0]['measures'][0]['voices'][0]['beats'][1]['notes'][0]['tie'] = False
        independent.parts[0].bars[0][1].explicit_tie = False
    with pytest.raises(ValueError): render(model)
    with pytest.raises(ValueError): expected(independent, {'offset': 0, 'scale': 1})


@pytest.mark.parametrize('stored', [0, 2])
def test_historical_semantics_and_local_sync_reconstruction(stored):
    from feedback_converter.song_import.local_sync import independent_tracks
    s = songsterr(source(stored))
    if stored != 2:
        with pytest.raises(ValueError): expected(s, {'offset':0, 'scale':1}, pending_slide_ties=False)
        with pytest.raises(ValueError): independent_tracks(s, pending_slide_ties=False)
    else:
        assert expected(s, {'offset':0, 'scale':1}, pending_slide_ties=False) == expected(s, {'offset':0, 'scale':1})
    assert len(independent_tracks(s)[0][0]['events']) == 2
