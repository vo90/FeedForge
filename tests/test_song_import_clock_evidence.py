"""Known audio truth, withheld tuning support and adversarial ending clocks."""
from copy import deepcopy

import numpy as np
import pytest
import soundfile as sf

from feedback_converter.song_import import clock_evidence as ce, recording_sync as rs


@pytest.fixture(scope='module')
def sparse_audio(tmp_path_factory):
    root=tmp_path_factory.mktemp('clock-evidence')
    rate=22050; duration=132
    times=list(np.arange(.25,104,.5))+[104.5,110,116,122,130]
    rng=np.random.default_rng(1007)
    midis=rng.integers(40,65,len(times))
    events=[]; audio=np.zeros(rate*duration)
    for time,midi in zip(times,midis):
        size=round(.3*rate); t=np.arange(size)/rate
        hz=450*2**((int(midi)-69)/12)
        audio[round(time*rate):round(time*rate)+size] += .16*np.minimum(t/.002,1)*np.exp(-t*12)*sum(
            np.sin(2*np.pi*hz*h*t)/h for h in range(1,6))
        events.append({'t':float(time),'end':float(time+.3),'midi':int(midi),'effects':{}})
    path=root/'truth.wav'; sf.write(path,audio,rate,subtype='FLOAT')
    pitch,flux,_=rs.features(path,reference_hz=450)
    return [{'id':'guitar','instrument':'guitar','events':events}],path,pitch,flux,duration


def test_real_sparse_ending_has_independent_cues(sparse_audio):
    tracks,_,pitch,flux,duration=sparse_audio
    result=ce.sparse_ending(tracks,pitch,flux,duration,104)
    assert result['status']=='supported', result
    assert result['supportedGroups']==5
    assert result['everyNoteVerified'] is False


@pytest.mark.parametrize('fault',['shift','drift','wrong_pitch','only_final','silent','final_fixed'])
def test_wrong_ending_cannot_borrow_body_or_final_chord(sparse_audio,fault):
    tracks,_,pitch,flux,duration=sparse_audio
    tracks=deepcopy(tracks)
    for n in tracks[0]['events']:
        if n['t']<104: continue
        if fault=='wrong_pitch': n['midi']+=1
        if fault=='shift' or fault=='final_fixed' and n['t']<130: n['t']+=.5; n['end']+=.5
        if fault=='drift':
            delta=(n['t']-104)*.04; n['t']+=delta; n['end']+=delta
    if fault=='only_final': tracks[0]['events']=[tracks[0]['events'][-1]]
    if fault=='silent': pitch=np.zeros_like(pitch); flux=np.zeros_like(flux)
    assert ce.sparse_ending(tracks,pitch,flux,duration,104)['status']!='supported'


def test_reference_selection_and_holdout_do_not_share_audio():
    windows=[{'start':i,'end':i+16,'status':'supported'} for i in range(0,104,8)]
    report=ce._reference_support({'windows':windows},136)
    assert report['status']=='supported'
    assert sum(p['windows'] for p in report['partitions'])==6
    for w in windows:
        if w['start']%32==16: w['status']='inconclusive'
    assert ce._reference_support({'windows':windows},136)['status']=='inconclusive'


def test_recording_clock_supports_detuned_audio_and_sparse_ending(sparse_audio):
    tracks,path,_,_,duration=sparse_audio
    report=rs.assess(tracks,path,duration,'test-map')
    assert report['status']=='supported', report
    assert report['endingEvidence']['status']=='supported'
    # Default pitch bands can already support this recording. Do not adopt a
    # hypothesis merely because it is closer to the known synthetic reference.
    assert report['referenceEvidence']['status']=='default'
    assert report['referenceEvidence']['hypothesis']['referenceHz']==pytest.approx(450,abs=2)


@pytest.mark.parametrize('fault', ['shift', 'drift', 'wrong_semitone'])
def test_wrong_recording_cannot_use_pitch_reference_to_authorize(sparse_audio, fault):
    tracks,path,_,_,duration=sparse_audio
    tracks=deepcopy(tracks)
    for note in tracks[0]['events']:
        delta = .25 if fault=='shift' else note['t']/duration*.5 if fault=='drift' else 0
        note['t']+=delta; note['end']+=delta
        if fault=='wrong_semitone': note['midi']+=1
    assert rs.assess(tracks,path,duration,'test-map')['status']!='supported'


def test_octave_ambiguity_does_not_rewrite_source_or_claim_note_verification(sparse_audio):
    tracks,path,_,_,duration=sparse_audio
    tracks=deepcopy(tracks)
    for note in tracks[0]['events']: note['midi']+=12
    before=deepcopy(tracks)
    report=rs.assess(tracks,path,duration,'test-map')
    # A recording's harmonics can support the same clock one octave higher.
    # This check must not infer or repair the authored notes' octave.
    assert report['everyNoteVerified'] is False
    assert report['scope']=='shared_recording_timing'
    assert abs(1200*np.log2(report['referenceEvidence']['referenceHz']/440))<=50
    assert tracks==before


def test_repeated_indistinguishable_attacks_are_ambiguous(sparse_audio):
    tracks,_,pitch,flux,duration=sparse_audio
    pitch=pitch.copy(); flux=flux.copy()
    for n in tracks[0]['events'][-5:]:
        left=round((n['t']-.08)/rs.DT); right=round((n['t']+.4)/rs.DT); offset=round(.75/rs.DT)
        pitch[left+offset:right+offset]=pitch[left:right]
        flux[left+offset:right+offset]=flux[left:right]
    assert ce.sparse_ending(tracks,pitch,flux,duration,104)['status']!='supported'


def test_weak_reference_without_held_out_support_cannot_authorize(sparse_audio,monkeypatch):
    tracks,path,pitch,flux,duration=sparse_audio
    monkeypatch.setattr(ce.ls,'tuning',lambda _: {'referenceHz':450,'cents':38.9,'status':'inconclusive'})
    monkeypatch.setattr(ce,'_reference_support',lambda *_: {'status':'inconclusive','partitions':[{'supported':0}]})
    original=rs.assess_features(tracks,pitch,flux,duration)
    report=ce.assess(tracks,path,duration,pitch,flux,original)
    assert report['referenceEvidence']['status']=='default'
    assert report['referenceEvidence']['referenceHz']==440


def test_dense_recording_clock_rejects_competing_repeat(sparse_audio):
    tracks,_,pitch,flux,duration=sparse_audio
    # Distinct pitches support the original clock despite regularly spaced
    # attacks. Duplicating the features half-way between attacks introduces
    # a competing clock without changing the source or its correct near-match.
    original={'windows':[{'start':0,'end':16}]}
    assert ce._joint(tracks,pitch,flux,duration,original)['windows'][0]['status']=='supported'
    offset=round(.25/rs.DT)
    repeated_pitch=np.maximum(pitch,np.roll(pitch,offset,axis=0))
    repeated_flux=np.maximum(flux,np.roll(flux,offset,axis=0))
    diagnostic=ce.ls.assess_features(tracks,repeated_pitch,repeated_flux,duration,windows=[(0,16)])
    assert diagnostic['windows'][0]['status']=='supported'
    strict=ce._joint(tracks,repeated_pitch,repeated_flux,duration,original)
    assert strict['windows'][0]['status']=='inconclusive'
    assert strict['windows'][0]['parts'][0]['distantBest']>=.9*strict['windows'][0]['parts'][0]['jointAtMap']
