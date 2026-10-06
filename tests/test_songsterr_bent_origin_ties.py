"""Ordinary bent attacks retain identity across plain legacy tie frets."""
from copy import deepcopy
from fractions import Fraction

import pytest

from feedback_converter.song_import.model import ScoreImportError
from feedback_converter.song_import.plain_ties import BEND_RULE
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from test_song_import_score import beat, measure, raw_score


RISE = {'points': [{'position': 0, 'tone': 0}, {'position': 60, 'tone': 100}]}
RELEASE = {'points': [{'position': 0, 'tone': 100}, {'position': 20, 'tone': 100},
                      {'position': 40, 'tone': 0}, {'position': 60, 'tone': 0}], 'tone': 100}
HOLD = {'points': [{'position': 0, 'tone': 100}, {'position': 60, 'tone': 100}]}
SETTLED = {'points': [{'position': 0, 'tone': 0}, {'position': 20, 'tone': 100},
                      {'position': 60, 'tone': 100}]}


def source(origin=2, stored=0, *, curve=RELEASE, durations=((1, 2), (1, 2))):
    document = raw_score([measure(beat(origin, duration=durations[0], bend=deepcopy(curve)),
                                 beat(stored, duration=durations[1], tie=True))])
    for b, duration in zip(document['parts'][0]['measures'][0]['voices'][0]['beats'], durations):
        if duration == (3,4):
            b.update(type=2,dots=1)
        elif duration == (1,24):
            b['type'] = 16
    return document


def produced(document):
    before = deepcopy(document)
    score = parse(document)
    parsed = deepcopy(score)
    performance = render(score)
    assert document == before and score == parsed
    return performance


def flat(track):
    return sorted([*track['notes'], *[{**n, 't':c['t']} for c in track['chords'] for n in c['notes']]],
                  key=lambda n:(n['t'], n['s']))


def written(track):
    return [n for m in track['notation']['measures'] for v in m['staves']['staff']['voices']
            for b in v['beats'] for n in b.get('notes', [])]


def equal_control(document):
    control = deepcopy(document)
    for bar in control['parts'][0]['measures']:
        for voice in bar['voices']:
            origins = {}
            for b in voice['beats']:
                for n in b.get('notes', []):
                    if n.get('rest'):
                        continue
                    if n.get('tie'):
                        n['fret'] = origins[n['string']]
                    else:
                        origins[n['string']] = n['fret']
    return control


@pytest.mark.parametrize('origin,stored', [(2,0), (7,9), (0,8), (12,4), (48,0)])
@pytest.mark.parametrize('curve', [RISE, RELEASE, HOLD, SETTLED], ids=['rise','release','hold','settled'])
@pytest.mark.parametrize('durations', [((1,2),(1,2)), ((1,4),(3,4)), ((1,24),(1,24))])
@pytest.mark.parametrize('bass', [False,True])
def test_ordinary_bent_tie_matches_supported_same_fret_clock(origin, stored, curve, durations, bass):
    document = source(origin, stored, curve=curve, durations=durations)
    document.update(title='Independent ordinary bend', songId=987654, revisionId=123456)
    if bass:
        document['tracks'][0].update(instrumentId=33, tuning=[43,38,33,28])
    performance = produced(document)
    control = produced(equal_control(document))
    track, = performance['tracks']
    note, = flat(track)
    assert flat(track) == flat(control['tracks'][0])
    assert track['notation'] == control['tracks'][0]['notation']
    assert performance['fingerBendTimingEvidence'] == control['fingerBendTimingEvidence']
    assert len(note['source_ids']) == 2 and note['f'] == origin
    row, = performance['plainTieIdentityEvidence']
    assert row['rule'] == BEND_RULE
    assert row['authored'] == {'fret':stored} and row['used'] == {'fret':origin}
    assert row['originSourceId'] == note['source_ids'][0]
    assert row['sourceId'] == note['source_ids'][1]
    assert row['attack'] == note['t'] == 0 and row['end'] == pytest.approx(note['sus'])
    assert all(n['fret'] == origin for n in written(track))
    assert all(n['midi'] == document['tracks'][0]['tuning'][0]+origin for n in written(track))
    assert performance['fingerBendTimingEvidence'][0]['status'] == 'resolved'
    assert not control.get('plainTieIdentityEvidence')


def test_actual_triplet_release_shape_uses_completed_held_clock():
    document = source(durations=((1,24),(1,24)))
    document['parts'][0]['automations']['tempo'][0]['bpm'] = 72
    p = produced(document)
    note, = flat(p['tracks'][0])
    assert note['f'] == 2 and note['sus'] == pytest.approx(5/18)
    assert [k['t'] for k in note['bnv']] == pytest.approx([0,5/54,5/27,5/18])
    assert [k['v'] for k in note['bnv']] == [2,2,0,0]
    evidence, = p['fingerBendTimingEvidence']
    assert evidence['segments'][0]['end'] == pytest.approx(5/36)
    assert evidence['segments'][0]['gestureEnd'] == pytest.approx(5/18)
    assert p['plainTieIdentityEvidence'][0]['start'] == pytest.approx(5/36)


def test_following_fresh_open_hopo_attack_does_not_reclassify_held_bend():
    document = source(durations=((1,24),(1,24)))
    bs = document['parts'][0]['measures'][0]['voices'][0]['beats']
    bs.extend([beat(0,duration=(1,24),hp=True), beat(2,duration=(1,8))])
    bs[2]['type'] = 16
    p = produced(document)
    held, open_note, destination = flat(p['tracks'][0])
    assert held['source_ids'] == ['songsterr:0:0:0:0:0','songsterr:0:0:0:1:0']
    assert held['f'] == 2 and held['sus'] == pytest.approx(1/6)
    assert not held.get('ln') and not held.get('ho') and not held.get('po')
    assert open_note['f'] == 0 and open_note['ln'] and destination['ho']
    assert p['fingerBendTimingEvidence'][0]['status'] == 'resolved'


def test_tempo_boundary_adds_knot_in_quarter_time_without_resetting_target():
    document = source(curve=RISE, durations=((1,4),(3,4)))
    document['parts'][0]['automations']['tempo'].append({'measure':0,'position':960,'bpm':60,'type':4})
    performance = produced(document)
    control = produced(equal_control(document))
    note, = flat(performance['tracks'][0])
    assert note['sus'] == 3.5
    assert note['bnv'] == [{'t':0.,'v':0.},{'t':.5,'v':.5},{'t':3.5,'v':2.}]
    assert flat(performance['tracks'][0]) == flat(control['tracks'][0])
    assert performance['tracks'][0]['notation'] == control['tracks'][0]['notation']
    assert performance['plainTieIdentityEvidence'][0]['start'] == .5


def test_history_of_plain_ties_keeps_original_attack_and_onset_instructions():
    document = raw_score([measure(beat(7,duration=(1,4),bend=deepcopy(RISE),leftFingering='2'),
                                 beat(0,duration=(1,4),tie=True,leftFingering='3'),
                                 beat(9,duration=(1,4),tie=True), beat(7,duration=(1,4),tie=True))])
    bs = document['parts'][0]['measures'][0]['voices'][0]['beats']
    bs[0]['pickStroke'], bs[1]['pickStroke'] = 'down','up'
    bs[0]['notes'][0].update(ghost=True,accentuated=True)
    p = produced(document)
    control = produced(equal_control(document))
    note, = flat(p['tracks'][0])
    assert (note['f'],note['sus'],note['fg'],note['pkd']) == (7,2,2,0)
    assert flat(p['tracks'][0]) == flat(control['tracks'][0])
    assert [r['rule'] for r in p['plainTieIdentityEvidence']] == [BEND_RULE,BEND_RULE]
    assert [r['originSourceId'] for r in p['plainTieIdentityEvidence']] == [note['source_ids'][0]]*2
    assert len(note['source_ids']) == 4 and [n['fret'] for n in written(p['tracks'][0])] == [7]*4


def test_repeat_entrance_uses_each_performed_bent_origin():
    document = raw_score([measure(beat(2,bend=deepcopy(RISE))),
                          measure(beat(0,tie=True),repeatStart=True),
                          measure(beat(5,bend=deepcopy(RELEASE)),repeat=2),
                          measure(beat(0,tie=True))])
    p = produced(document)
    notes = flat(p['tracks'][0])
    assert [(n['t'],n['f'],n['sus']) for n in notes] == [(0,2,4),(4,5,4),(8,5,4)]
    assert [(r['occurrence'],r['used']['fret'],r['rule']) for r in p['plainTieIdentityEvidence']] == [(2,2,BEND_RULE),(4,5,BEND_RULE),(6,5,BEND_RULE)]
    assert [n['fret'] for n in written(p['tracks'][0])] == [2,2,5,5,5,5]
    assert all(e['status'] == 'resolved' for e in p['fingerBendTimingEvidence'])


@pytest.mark.parametrize('fault', ['gap','rest','orphan','voice','string','overlap','duplicate-origin','duplicate-tie','repeat-gap'])
def test_missing_or_ambiguous_bent_continuity_stays_guarded(fault):
    document = source(durations=((1,4),(1,4)))
    bs = document['parts'][0]['measures'][0]['voices'][0]['beats']
    if fault == 'gap':
        bs.insert(1,beat(8,string=1,duration=(1,4)))
    elif fault == 'rest':
        bs.insert(1,{'duration':[1,4],'notes':[{'rest':True}]})
    elif fault == 'orphan':
        bs.pop(0)
    elif fault == 'voice':
        document['parts'][0]['measures'][0]['voices'].append({'beats':[bs.pop(1)]})
    elif fault == 'string':
        bs[1]['notes'][0]['string'] = 1
    elif fault == 'duplicate-origin':
        bs[0]['notes'].append(deepcopy(bs[0]['notes'][0]))
    elif fault == 'duplicate-tie':
        bs[1]['notes'].append(deepcopy(bs[1]['notes'][0]))
    elif fault == 'repeat-gap':
        document = raw_score([measure(beat(2,duration=(1,4),bend=deepcopy(RISE))),
                              measure(beat(0,tie=True),repeatStart=True),
                              measure(beat(5,duration=(1,4),bend=deepcopy(RISE)),repeat=2)])
    score = parse(document)
    if fault == 'overlap':
        score.tracks[0].bars[0][1].position = Fraction(1,2)
    with pytest.raises(ScoreImportError):
        render(score)


@pytest.mark.parametrize('tie', [1,'true',[True],{'active':True}])
def test_malformed_tie_cannot_admit_bent_identity(tie):
    document = source()
    document['parts'][0]['measures'][0]['voices'][0]['beats'][1]['notes'][0]['tie'] = tie
    with pytest.raises(ScoreImportError):
        produced(document)


@pytest.mark.parametrize('tie', [1,'true',[True],{'active':True}])
def test_coerced_same_fret_history_does_not_qualify_later_bent_identity(tie):
    document = raw_score([measure(beat(7,duration=(1,4),bend=deepcopy(RISE)),
                                 beat(7,duration=(1,4),tie=tie),beat(0,duration=(1,2),tie=True))])
    with pytest.raises(ScoreImportError):
        produced(document)


COMPOUNDS = [{'vibrato':True},{'wideVibrato':True},{'harmonic':'pinch'}, {'dead':True},
             {'slide':'below'}, {'slide':'upwards'}, {'slide':'shift'}, {'hp':True},
             {'trill':{'auxiliaryFret':9,'speed':60}}, {'staccato':True}]


@pytest.mark.parametrize('fields', COMPOUNDS)
@pytest.mark.parametrize('role', ['origin','continuation','later-equal'])
def test_pitch_and_articulation_compounds_stay_guarded(fields,role):
    document = source(7,0,durations=((1,4),(1,4)))
    bs = document['parts'][0]['measures'][0]['voices'][0]['beats']
    if role == 'later-equal':
        bs.append(beat(7,duration=(1,2),tie=True,**fields))
    else:
        bs[0 if role == 'origin' else 1]['notes'][0].update(fields)
    with pytest.raises(ScoreImportError):
        produced(document)


@pytest.mark.parametrize('owner', ['current','previous','later-equal'])
def test_continuation_owned_bend_is_not_initial_bend_identity(owner):
    document = source(7,0,durations=((1,4),(1,4)))
    bs = document['parts'][0]['measures'][0]['voices'][0]['beats']
    if owner == 'current':
        bs[1]['notes'][0]['bend'] = deepcopy(HOLD)
    elif owner == 'previous':
        bs.insert(1,beat(7,duration=(1,4),tie=True,bend=deepcopy(HOLD)))
    else:
        bs.append(beat(7,duration=(1,2),tie=True,bend=deepcopy(HOLD)))
    with pytest.raises(ScoreImportError):
        produced(document)


@pytest.mark.parametrize('field,value', [('tremoloBar',{'points':[{'position':0,'tone':0},{'position':60,'tone':100}]}),
                                        ('vibratoWithTremoloBar','slight'),('palmMute',True),('letRing',True),('tremolo',16),('tap',True)])
@pytest.mark.parametrize('origin', [False,True])
def test_inherited_expression_compounds_stay_guarded(field,value,origin):
    document = source()
    document['parts'][0]['measures'][0]['voices'][0]['beats'][0 if origin else 1][field] = deepcopy(value)
    with pytest.raises(ScoreImportError):
        produced(document)


def test_prior_rest_interrupting_held_origin_cannot_be_recovered():
    document = raw_score([measure(beat(7,duration=(1,4),bend=deepcopy(RISE)),
                                 {'duration':[1,4],'notes':[{'rest':True}]},
                                 beat(0,duration=(1,2),tie=True))])
    score = parse(document)
    score.tracks[0].bars[0][0].duration = Fraction(2)
    with pytest.raises(ScoreImportError,match='Unresolved tie'):
        render(score)


def test_origin_hopo_link_is_distinct_from_bend_admission():
    document = raw_score([measure(beat(2,duration=(1,4),hp=True),beat(0,duration=(3,4),tie=True))])
    with pytest.raises(ScoreImportError,match='Unresolved tie'):
        produced(document)


def test_resolved_incoming_hopo_does_not_make_an_ordinary_bend_origin():
    document = raw_score([measure(beat(2,duration=(1,4),hp=True),
                                 beat(7,duration=(1,4),bend=deepcopy(RISE)),
                                 beat(0,duration=(1,2),tie=True))])
    with pytest.raises(ScoreImportError,match='Unresolved tie'):
        produced(document)


@pytest.mark.parametrize('same_string', [False,True])
def test_bent_identity_evidence_survives_combined_and_split_voice_projection(same_string):
    document = source(7,0)
    other = [beat(12,string=0 if same_string else 1,duration=(1,2),bend=deepcopy(RELEASE)),
             beat(4,string=0 if same_string else 1,duration=(1,2),tie=True)]
    document['parts'][0]['measures'][0]['voices'].append({'beats':other})
    p = produced(document)
    assert len(p['tracks']) == (2 if same_string else 1)
    assert sorted(n['f'] for t in p['tracks'] for n in flat(t)) == [7,12]
    assert sorted((r['voice'],r['used']['fret'],r['rule']) for r in p['plainTieIdentityEvidence']) == [(0,7,BEND_RULE),(1,12,BEND_RULE)]
    assert all(r['trackId'] in {t['id'] for t in p['tracks']} for r in p['plainTieIdentityEvidence'])
    assert all(n['fret'] in {7,12} for t in p['tracks'] for n in written(t))


@pytest.mark.parametrize('status', [None,'deferred'])
def test_new_bent_identity_requires_resolved_final_curve(monkeypatch,status):
    from feedback_converter.song_import import bend_timing
    original = bend_timing.finish

    def unresolved(*args,**kwargs):
        result = original(*args,**kwargs)
        if status is None:
            return None
        return {**result,'status':status,'rule':'retained-segment-timing'}

    monkeypatch.setattr(bend_timing,'finish',unresolved)
    with pytest.raises(ScoreImportError,match='resolved finger-bend timing'):
        produced(source())
    # This new admission guard does not change historical same-fret rendering.
    assert len(flat(produced(source(2,2))['tracks'][0])) == 1


def test_existing_nonbent_gap_and_same_fret_bend_compositions_keep_their_rules():
    ordinary = raw_score([measure(beat(7,duration=(1,4))),measure(beat(0,tie=True))])
    p = produced(ordinary)
    assert p['plainTieIdentityEvidence'][0]['rule'] == 'plain-tie-keeps-attack-target'
    equal = source(7,7)
    equal['parts'][0]['measures'][0]['voices'][0]['beats'][1]['notes'][0]['bend'] = deepcopy(HOLD)
    same = produced(equal)
    assert not same.get('plainTieIdentityEvidence')
    assert len(flat(same['tracks'][0])) == 1
