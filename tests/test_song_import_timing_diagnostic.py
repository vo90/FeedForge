from copy import deepcopy

import numpy as np
import pytest
import soundfile as sf

from feedback_converter.song_import import local_sync as ls, recording_sync as rs, timing_diagnostic as td


def fixture(delay=.3, mode='attacks', count=18):
    times=np.cumsum(np.random.default_rng(482).uniform(.4,.9,count))+.5
    grid=np.arange(round(16/rs.DT)+1)*rs.DT
    pitch=np.zeros((len(grid),len(rs.MIDIS)))
    flux=np.ones((len(grid),4))*(1 if mode=='pitch_only' else .05)
    events=[]
    for index,t in enumerate(times):
        midi=45+index%14
        events.append({'t':float(t),'end':float(t+.2),'midi':midi,'effects':{}})
        offsets=[delay]
        if mode=='conflicting_halves':offsets=[delay if index<count//2 else -.4]
        if mode=='repeated':offsets=[delay,delay+1.5]
        for offset in offsets:
            pitch[(grid>=t+offset+.015)&(grid<t+offset+.20),midi-28]=1
            if mode!='pitch_only':flux+=np.exp(-.5*((grid-t-offset)/.025)**2)[:,None]*4
    return [{'id':'control','instrument':'guitar','events':events}],pitch,flux


@pytest.mark.parametrize('delay',[-.3,.3,.7])
def test_clear_pitch_and_attack_delays_remain_suspected(delay):
    tracks,pitch,flux=fixture(delay)
    before=deepcopy(tracks)
    r=ls.assess_features(tracks,pitch,flux,16,corroborate_mismatch=True)
    assert r['suspectedMismatchWindows']==1,r
    e=r['windows'][0]['parts'][0]['mismatchEvidence']
    assert e['status']=='corroborated' and all(h['accepted'] for h in e['halves'])
    assert e['candidateOffset']==pytest.approx(delay,abs=.04)
    assert tracks==before


@pytest.mark.parametrize('mode,count',[('pitch_only',18),('conflicting_halves',18),('repeated',18),('attacks',6)])
def test_weak_ambiguous_and_sparse_observations_never_become_a_pass(mode,count):
    tracks,pitch,flux=fixture(mode=mode,count=count)
    old=ls.assess_features(tracks,pitch,flux,16)
    new=ls.assess_features(tracks,pitch,flux,16,corroborate_mismatch=True)
    assert new['supportedWindows']==old['supportedWindows']==0
    assert new['suspectedMismatchWindows']==0
    assert new['status']=='inconclusive'
    if mode=='pitch_only':
        assert old['suspectedMismatchWindows']==1
        assert 'weak_attack_evidence' in new['windows'][0]['parts'][0]['mismatchEvidence']['reasons']


def test_aligned_pass_is_unchanged_and_disagreed_tracks_do_not_accuse_whole_window():
    ts,p,f=fixture(delay=0)
    assert ls.assess_features(ts,p,f,16)==ls.assess_features(ts,p,f,16,corroborate_mismatch=True)
    assert ls.assess_features(ts,p,f,16)['supportedWindows']==1
    ts,p,f=fixture()
    other=deepcopy(ts[0]);other['id']='other'
    for e in other['events']:e['t']+=.6;e['end']+=.6
    r=ls.assess_features(ts+[other],p,f,16,corroborate_mismatch=True)
    assert r['suspectedMismatchWindows']==0


def test_versions_and_warning_do_not_claim_source_was_untouched_after_repair(tmp_path):
    path=tmp_path/'silence.wav';sf.write(path,np.zeros(11025*4),11025)
    for version in ('local-recording-clock-v1','local-recording-clock-v2',td.VERSION):
        report=ls.assess([],path,4,'map',version=version)
        assert report['version']==version
        assert ('suspectedMismatchRanges' in report)==(version==td.VERSION)
    report={'status':'inconclusive','supportedWindows':1,'windowCount':27}
    assert 'does not necessarily mean they are wrong' in td.warning(report)
    assert "Songsterr's timing was preserved" in td.warning(report)
    assert 'opening adjustment' in td.warning(report,opening_repaired=True)
    assert td.warning({'status':'supported'}) is None


def test_receipt_decisions_ranges_and_offsets_cannot_be_tampered_with():
    from feedback_converter.song_import.verification import Check
    ts,p,f=fixture();r=ls.assess_features(ts,p,f,16,corroborate_mismatch=True)
    r.update(td.summary(r))
    for key in ('status','candidateOffset','reasons'):
        tampered=deepcopy(r)
        tampered['windows'][0]['parts'][0]['mismatchEvidence'][key]='tampered'
        check=Check();ls.compare_assessment(r,tampered,check)
        assert check.errors
    tampered=deepcopy(r);tampered['suspectedMismatchRanges']=[]
    check=Check();ls.compare_assessment(r,tampered,check);assert check.errors


@pytest.mark.parametrize('delay',[0,.3])
def test_encoded_audio_preserves_passes_and_detects_clear_delay(tmp_path,delay):
    rate=11025;duration=48;rng=np.random.default_rng(19)
    signal=np.zeros(rate*duration);events=[]
    times=np.cumsum(rng.uniform(.45,.85,90));times=times[times<46]
    for time,midi in zip(times,rng.integers(43,66,len(times))):
        x=np.arange(round(.22*rate))/rate;hz=440*2**((int(midi)-69)/12)
        wave=sum(np.sin(2*np.pi*hz*h*x)/h for h in range(1,5))
        wave*=.12*np.minimum(1,x/.004)*np.exp(-x*16)
        start=round((time+delay)*rate);signal[start:start+len(x)]+=wave
        events.append({'t':float(time),'end':float(time+.22),'midi':int(midi),'effects':{}})
    path=tmp_path/'attacks.wav';sf.write(path,signal,rate,subtype='FLOAT')
    report=ls.assess([{'id':'g','instrument':'guitar','events':events}],path,duration,'map')
    assert report['version']==td.VERSION
    if delay:
        assert report['suspectedMismatchWindows']>=1,[(h['reasons']) for w in report['windows'] for p in w['parts'] for h in p.get('mismatchEvidence',{}).get('halves',[])]
        assert report['status']=='inconclusive'
    else:
        assert report['supportedWindows']==3 and report['suspectedMismatchWindows']==0
