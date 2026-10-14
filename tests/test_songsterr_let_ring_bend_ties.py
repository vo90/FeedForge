"""Let-ring adds no pitch controller to a qualified bent continuation."""
from copy import deepcopy

import pytest

from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from test_song_import_score import beat, measure, raw_score
from test_songsterr_bent_origin_ties import flat, produced, source, equal_control, RISE, RELEASE


@pytest.mark.parametrize('owner', ['origin', 'continuation', 'both', 'later-equal'])
@pytest.mark.parametrize('curve', [RISE, RELEASE])
@pytest.mark.parametrize('bass', [False, True])
def test_ringing_bent_tie_preserves_same_fret_music_and_independent_evidence(owner, curve, bass):
    raw = source(9, 0, curve=curve, durations=((1,4),(1,4)))
    bs = raw['parts'][0]['measures'][0]['voices'][0]['beats']
    bs.append(beat(9, duration=(1,2), tie=True))
    for i in {'origin':[0], 'continuation':[1], 'both':[0,1], 'later-equal':[2]}[owner]:
        bs[i]['letRing'] = True
    if bass:
        raw['tracks'][0].update(instrumentId=33, tuning=[43,38,33,28])
    before = deepcopy(raw)
    p, control = produced(raw), produced(equal_control(raw))
    v = expected(songsterr(raw), {'offset':0, 'scale':1})
    note, = flat(p['tracks'][0])
    independent, = v['parts'][0]['notes']
    assert note['f'] == 9 and note['lr'] is True and len(note['source_ids']) == 3
    assert flat(p['tracks'][0]) == flat(control['tracks'][0])
    assert p['tracks'][0]['notation'] == control['tracks'][0]['notation']
    assert p['plainTieIdentityEvidence'] == v['plain_tie_identities']
    assert note['sus'] == pytest.approx(independent['note']['sus'], abs=1e-6)
    assert independent['note']['lr'] is True
    for actual, wanted in zip(note['bnv'], independent['note']['bnv'], strict=True):
        assert actual['t'] == pytest.approx(wanted['t'], abs=1e-6)
        assert actual['v'] == wanted['v']
    assert raw == before


def test_ringing_tie_across_bar_and_repeat_keeps_each_attack():
    first = beat(9, bend=deepcopy(RISE)); first['letRing'] = True
    raw = raw_score([measure(first, repeatStart=True), measure(beat(0,tie=True),repeat=2)])
    p = produced(raw); v = expected(songsterr(raw), {'offset':0, 'scale':1})
    assert [(n['t'],n['f'],n['sus'],n['lr']) for n in flat(p['tracks'][0])] == [(0,9,4,True),(4,9,4,True)]
    assert p['plainTieIdentityEvidence'] == v['plain_tie_identities']


@pytest.mark.parametrize('fault', ['gap','rest','duplicate-origin','duplicate-tie','string','voice',
                                  'slide','harmonic','vibrato','new-bend','not-boolean','palm-mute'])
def test_let_ring_does_not_bypass_continuity_or_controller_guards(fault):
    raw = source(9,0,durations=((1,4),(1,4)))
    bs = raw['parts'][0]['measures'][0]['voices'][0]['beats']
    bs[0]['letRing'] = bs[1]['letRing'] = True
    if fault == 'gap': bs.insert(1,beat(7,string=1,duration=(1,4)))
    elif fault == 'rest': bs.insert(1,{'duration':[1,4],'notes':[{'rest':True}]})
    elif fault.startswith('duplicate-'):
        target = bs[0 if fault.endswith('origin') else 1]
        target['notes'].append(deepcopy(target['notes'][0]))
    elif fault == 'string': bs[1]['notes'][0]['string'] = 1
    elif fault == 'voice': raw['parts'][0]['measures'][0]['voices'].append({'beats':[bs.pop(1)]})
    elif fault == 'palm-mute': bs[0]['palmMute'] = True
    else:
        fields = {'slide':('slide','shift'), 'harmonic':('harmonic','pinch'),
                  'vibrato':('vibrato',True), 'new-bend':('bend',deepcopy(RISE)), 'not-boolean':('tie',1)}
        key,value = fields[fault]; bs[1]['notes'][0][key] = value
    with pytest.raises(ValueError): produced(raw)
    with pytest.raises(ValueError): expected(songsterr(raw), {'offset':0, 'scale':1})
