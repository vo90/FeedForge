"""Low-bass opening repair must retain bounded, independently replayable evidence."""
from copy import deepcopy
import json
from zipfile import ZipFile

import numpy as np
import pytest
import soundfile as sf
import yaml

from feedback_converter.song_import import local_sync as ls, low_bass_opening as low, recording_sync as rs
from feedback_converter.song_import import worker
from feedback_converter.song_import.verification import verify_import
from test_song_import_opening_confirmation import recording_fixture


def features(monkeypatch, lowest=23):
    times=[0,.5,1,1.5,2,2.5,3,3.5]
    midis=[lowest+i for i in (0,2,4,1,4,2,1,0)]
    tracks=[{'id':'bass','instrument':'bass','events':[
        {'t':t,'end':t+.25,'midi':m,'effects':{}} for t,m in zip(times,midis)]}]
    bounds=[0,2,4,6]; points=[-.2,2,4,6]
    mapped=np.interp(times,bounds,[.2,2,4,6])
    x=np.arange(round(7/rs.DT))*rs.DT
    pitch=np.zeros((len(x),76)); flux=np.full((len(x),4),.15)
    for t,m in zip(mapped,midis):
        pitch[:,m-21]+=np.exp(-.5*((x-t-.085)/.045)**2)
        flux[:,0]+=5*np.exp(-.5*((x-t)/.018)**2)
    calls=[]
    def extract(path,**kwargs):
        calls.append(kwargs)
        assert kwargs=={'reference_hz':442.0,'midi_min':21}
        return pitch,flux,None
    monkeypatch.setattr(rs,'features',extract)
    return tracks,bounds,points,pitch,flux,calls


def test_profile_counts_and_exact_candidate(monkeypatch):
    tracks,bounds,points,p,f,calls=features(monkeypatch)
    result=low.propose(tracks,bounds,points,'recording',442.0)
    assert result['status']=='supported'
    confirmed=low.confirm(tracks,bounds,points,'recording',442.0,result['replacementBoundaries'])
    assert confirmed['status']=='supported'
    assert confirmed['version']==low.VERSION
    assert confirmed['replacementBoundaries']==result['replacementBoundaries']
    assert confirmed['candidateToBestRatio']>=.95
    assert confirmed['analysisProfile']=={'midiMin':21,'midiMax':96,'instrument':'bass',
        'fitGroups':4,'fitLowGroups':4,'heldOutGroups':4,'heldOutLowGroups':4}
    assert len(calls)==2


@pytest.mark.parametrize('fault',['guitar','below_range','sparse_fit','sparse_held','mixed_high',
    'bend','legato','mute','too_dense','positive_opening','far_negative','legacy_available'])
def test_ineligible_source_does_not_extract_low_features(monkeypatch,fault):
    tracks,bounds,points,p,f,calls=features(monkeypatch)
    events=tracks[0]['events']
    if fault=='guitar': tracks[0]['instrument']='guitar'
    elif fault=='below_range': events[0]['midi']=20
    elif fault=='sparse_fit': tracks[0]['events']=[e for e in events if e['t']>=2 or e['t']<1]
    elif fault=='sparse_held': tracks[0]['events']=[e for e in events if e['t']<2.5]
    elif fault=='mixed_high':
        for e in events[1:]: e['midi']+=12
    elif fault in ('bend','legato','mute'): events[0]['effects'][{'bend':'bn','legato':'ho','mute':'mt'}[fault]]=1
    elif fault=='too_dense':
        tracks[0]['events']=[{**deepcopy(events[0]),'t':i/20} for i in range(80)]
    elif fault=='positive_opening': points[0]=0
    elif fault=='far_negative': points[0]=-1
    else:
        other=deepcopy(tracks[0]); other['id']='normal'
        for e in other['events']: e['midi']+=12
        tracks.append(other)
    result=low.propose(tracks,bounds,points,'recording',442.0)
    assert result['status']=='inconclusive',result
    assert not calls


@pytest.mark.parametrize('fault',['wrong_pitch','noise','no_attacks','missing_fit','missing_held','competing'])
def test_acoustic_failures_cannot_gain_support(monkeypatch,fault):
    tracks,bounds,points,p,f,calls=features(monkeypatch)
    candidate=low.propose(tracks,bounds,points,'recording',442.0)['replacementBoundaries']
    if fault=='wrong_pitch': p[:]=np.roll(p,1,axis=1)
    elif fault=='noise': p[:]=.01
    elif fault=='no_attacks': f[:]=.1
    elif fault in ('missing_fit','missing_held'):
        t=.2 if fault=='missing_fit' else 2.5
        f[round((t-.15)/rs.DT):round((t+.15)/rs.DT)]=.1
    else:
        delay=round(.2/rs.DT)
        p[delay:]=np.maximum(p[delay:],p[:-delay].copy())
        f[delay:]=np.maximum(f[delay:],f[:-delay].copy())
    result=low.confirm(tracks,bounds,points,'recording',442.0,candidate)
    assert result['status']=='inconclusive',result


@pytest.mark.parametrize('candidate',[None,[True,2],[float('nan'),2],[.201,2],[.3,2.1],[.5,2.3]])
def test_invalid_or_shifted_candidate_never_refits(monkeypatch,candidate):
    tracks,bounds,points,*_=features(monkeypatch)
    assert low.confirm(tracks,bounds,points,'recording',442.0,candidate)['status']=='inconclusive'


def test_source_selected_part_cannot_be_replaced_by_better_sounding_part(monkeypatch):
    tracks,bounds,points,*_=features(monkeypatch)
    other=deepcopy(tracks[0]); other['id']='z'
    tracks.append(other)
    tracks[0]['events'][0]['midi']+=1
    result=low.propose(tracks,bounds,points,'recording',442.0)
    assert result['trackId']=='bass'
    assert result['status']=='inconclusive'


def run_low_worker(tmp_path,monkeypatch,strings=4,lowest=23,damage=None):
    source,request=recording_fixture(tmp_path,monkeypatch,lowest)
    data=json.loads(source.read_text())
    data['tracks'][0]['tuning']=list(reversed([lowest+5*i for i in range(strings)]))
    data['parts'][0]['strings']=strings
    for measure in data['parts'][0]['measures']:
        for beat in measure['voices'][0]['beats']:
            beat['notes'][0]['string']=strings-1
            if damage=='wrong_pitch': beat['notes'][0]['fret']+=1
    source.write_text(json.dumps(data))
    if damage in ('delayed','missing_held'):
        recording=tmp_path/'recording.wav'
        samples,rate=sf.read(recording)
        if damage=='delayed':
            shift=round(.3*rate); samples=np.concatenate((np.zeros(shift),samples[:-shift]))
        else: samples[round(2.4*rate):round(2.9*rate)]=0
        sf.write(recording,samples,rate,subtype='FLOAT')
    result=worker.run_import(request)
    (tmp_path/'worker-result.json').write_text(json.dumps(result,indent=2))
    return source,result


@pytest.mark.parametrize('strings,lowest',[(4,21),(5,23),(6,23)])
def test_real_worker_encoding_and_independent_archive_verification(tmp_path,monkeypatch,strings,lowest):
    initial=[]; propose=low.propose
    def capture(*a,**kw):
        result=propose(*a,**kw); initial.append(deepcopy(result)); return result
    monkeypatch.setattr(low,'propose',capture)
    source,result=run_low_worker(tmp_path,monkeypatch,strings,lowest)
    assert result['ok'],result
    assert result['verification']['status']=='passed'
    assert result['recipe']['preservationContract']>=107
    receipt=result['alignment']['openingRepair']
    assert receipt['version']==low.VERSION
    assert receipt['replacementBoundaries']==initial[0]['replacementBoundaries']
    assert receipt['candidateToBestRatio']>=.95
    evidence=tmp_path/'song-import-evidence'
    record=json.loads((evidence/'records'/f"{result['evidence']['id']}.json").read_text())
    alignment=json.loads((evidence/'objects'/record['objects']['appliedAlignment']).read_text())
    assert alignment['sourceTiming']['points']==[-.2,2,4,6,8]
    offset=result['verification']['preparationSeconds']
    assert [a['audio']-offset for a in alignment['anchors'][2:]]==pytest.approx([4,6,8])
    assert verify_import(source,result['stagingPath'],alignment)['status']=='passed'
    if strings!=5: return
    with ZipFile(result['stagingPath']) as archive:
        original={name:archive.read(name) for name in archive.namelist()}
    for fault in ('profile','count','part','candidate','reference','hash','anchor','contract','legacy_version'):
        files=dict(original); changed=deepcopy(alignment)
        manifest=yaml.safe_load(files['manifest.yaml']); recipe=manifest['song_import']
        stored=changed['openingRepair']
        if fault=='profile': stored['analysisProfile']['midiMin']=20
        elif fault=='count': stored['analysisProfile']['fitLowGroups']+=1
        elif fault=='part': stored['trackId']='invented'
        elif fault=='candidate': stored['replacementBoundaries'][0]+=.1
        elif fault=='reference': stored['tuning']['referenceHz']+=1
        elif fault=='hash': stored['audioSha256']='changed'
        elif fault=='anchor': changed['anchors'][2]['audio']+=.1
        elif fault=='contract': recipe['preservationContract']=106
        else: stored['version']=ls.CONFIRMED_REPAIR
        files[recipe['openingRepairFile']]=json.dumps(stored).encode()
        files['manifest.yaml']=yaml.safe_dump(manifest).encode()
        archive=tmp_path/(fault+'.feedpak')
        with ZipFile(archive,'w') as z:
            for name,data in files.items(): z.writestr(name,data)
        assert verify_import(source,archive,changed)['status']=='failed',fault


def test_builder_rejects_old_contract_for_new_profile(tmp_path,monkeypatch):
    monkeypatch.setattr(worker,'CONTRACT_VERSION',106)
    _,result=run_low_worker(tmp_path,monkeypatch)
    assert not result['ok']
    assert 'contract 107' in result['error']


@pytest.mark.parametrize('damage',['delayed','missing_held','wrong_pitch'])
def test_real_worker_rejects_unsupported_low_bass_recording(tmp_path,monkeypatch,damage):
    _,result=run_low_worker(tmp_path,monkeypatch,damage=damage)
    assert not result['ok'],result
    assert result['code']=='alignment_failed'


@pytest.mark.parametrize('damaged',[False,True])
def test_legacy_acoustic_choice_never_tries_low_fallback(tmp_path,monkeypatch,damaged):
    def forbidden(*a,**kw): pytest.fail('Eligible legacy opening must not try another profile.')
    monkeypatch.setattr(low,'propose',forbidden)
    _,result=run_low_worker(tmp_path,monkeypatch,lowest=40,damage='wrong_pitch' if damaged else None)
    assert result['ok'] is not damaged
    if not damaged: assert result['alignment']['openingRepair']['version']==ls.CONFIRMED_REPAIR
