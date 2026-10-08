from copy import deepcopy

import numpy as np
import pytest
import soundfile as sf

from feedback_converter.song_import import local_sync as ls, pitch_clock as pc, recording_sync as rs


def report(strength=.5, supported=True, count=9):
    return {'version': ls.VERSION, 'status': 'supported' if supported else 'inconclusive',
            'everyNoteVerified': False, 'windowCount': count, 'supportedWindows': count if supported else 0,
            'suspectedMismatchWindows': 0,
            'windows': [{'start': i*16, 'end': (i+1)*16,
                         'status': 'supported' if supported else 'inconclusive',
                         'parts': [{'jointAtMap': strength}]} for i in range(count)]}


def candidates():
    return [{'cents': -33., 'referenceHz': 431.7, 'report': report(.02, False)},
            {'cents': 67., 'referenceHz': 457.4, 'report': report()}]


def test_global_reference_with_independent_partitions_and_explicit_warning():
    cs=candidates(); original=deepcopy(cs)
    result,evidence=pc.select(cs,cs[0]['report'])
    assert result['status']=='supported'
    assert evidence['pitchDifference'] and evidence['cents']==67
    assert len(evidence['heldOutPartitions'])==2
    message=pc.warning({**result,'pitchReference':evidence})
    assert 'Timing is supported' in message and '67 cents higher' in message and 'out of tune' in message
    assert cs==original


@pytest.mark.parametrize('fault',['held_out','competing','sparse','mismatch','strength_lost'])
def test_ambiguous_or_local_matches_never_select_reference(fault):
    cs=candidates()
    if fault=='held_out':
        for w in cs[1]['report']['windows'][6:]:w['status']='inconclusive'
    if fault=='competing':cs.append({'cents':0,'referenceHz':440,'report':report(.49)})
    if fault=='sparse':
        for c in cs:c['report']=report(.5,c['cents']>0,5)
    if fault=='mismatch':cs[1]['report']['windows'][4]['status']='suspected_mismatch'
    if fault=='strength_lost':
        for w in cs[1]['report']['windows'][6:]:w['parts'][0]['jointAtMap']=.01
    result,evidence=pc.select(cs,cs[0]['report'])
    assert evidence['status']=='inconclusive' and result is cs[0]['report']


def test_pitch_warning_can_never_claim_timing_when_some_passages_are_uncertain():
    cs=candidates();cs[1]['report']['status']='inconclusive'
    result,evidence=pc.select(cs,cs[0]['report'])
    assert pc.warning({**result,'pitchReference':evidence}).startswith('Timing remains uncertain')


@pytest.mark.parametrize('cents,offset',[(67,0),(-67,0),(0,0),(67,.26),(67,1)])
def test_real_audio_reference_and_wrong_clock(tmp_path,cents,offset):
    rate=11025;duration=96;signal=np.zeros(rate*duration)
    rng=np.random.default_rng(18)
    times=np.cumsum(rng.uniform(.35,.85,170));times=times[times<94]
    midis=rng.integers(43,65,len(times));events=[]
    for time,midi in zip(times,midis):
        x=np.arange(round(.25*rate))/rate
        hz=440*2**((int(midi)-69)/12+cents/1200)
        wave=sum(np.sin(2*np.pi*hz*h*x)/h for h in range(1,5))
        wave*=.12*np.minimum(1,x/.004)*np.exp(-x*12)
        start=round(time*rate);signal[start:start+len(x)]+=wave
        events.append({'t':float(time+offset),'end':float(time+offset+.25),'midi':int(midi),'effects':{}})
    path=tmp_path/'audio.wav';sf.write(path,signal,rate,subtype='FLOAT')
    tracks=[{'id':'guitar','instrument':'guitar','events':events}];before=deepcopy(tracks)
    result=ls.assess(tracks,path,duration,'test-map')
    assert tracks==before and result['version']==pc.VERSION and result['everyNoteVerified'] is False
    if offset:
        assert result['status']=='inconclusive'
        assert result['pitchReference']['status']!='corroborated'
    else:
        assert result['status']=='supported',result
        if cents:
            assert result['pitchReference']['cents']==pytest.approx(cents,abs=3)
            assert pc.warning(result)
        else:assert pc.warning(result) is None


def test_legacy_replay_and_invalid_version(tmp_path):
    p=tmp_path/'silent.wav';sf.write(p,np.zeros(11025*4),11025)
    legacy=ls.assess([],p,4,'map',version=ls.VERSION)
    assert legacy['version']==ls.VERSION and 'pitchReference' not in legacy
    with pytest.raises(ValueError):ls.assess([],p,1,'map',version='invented')


def test_reference_receipt_tampering_rejected():
    from feedback_converter.song_import.verification import Check
    cs=candidates();r,e=pc.select(cs,cs[0]['report']);fresh={**r,'pitchReference':e}
    for field,value in [('cents',0),('pitchDifference',False),('status','inconclusive'),('referenceHz',440)]:
        tampered=deepcopy(fresh);tampered['pitchReference'][field]=value
        check=Check();ls.compare_assessment(fresh,tampered,check)
        assert check.errors
